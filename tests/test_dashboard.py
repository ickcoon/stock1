from datetime import date
import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from kiwoom_collector.classification import classify
from kiwoom_collector.dashboard import export_dashboard
from kiwoom_collector.schedule import slot_at
from kiwoom_collector.client import Snapshot
from kiwoom_collector.storage import Store
from kiwoom_collector.sectors import SectorManager
from kiwoom_collector.__main__ import main


def group(count=2, top5=2, top10=2, mean=2, key=1):
    return {'sector_id':key,'sector_name':str(key),'stock_count':count,'top5_count':top5,
            'top10_count':top10,'mean_rank':mean,'stocks':[{'stock_code':str(i)} for i in range(count)]}


class ClassificationTests(TestCase):
    def test_all_rule_branches_without_inventing_price_data(self):
        old=[group()]
        examples=[(group(),'오전 강세 유지(관심도 기준)'),(group(count=3),'오전 대비 강화'),
                  (group(count=1),'약화'),(None,'소멸'),(group(top5=1,mean=4),'약화'),
                  (group(count=3,top5=1),'혼조')]
        for current,label in examples:
            with self.subTest(label=label):
                report=classify([current] if current else [],old,{'available':True,'sectors':[]})
                self.assertEqual(report['sectors'][0]['label'],label)
                self.assertFalse(report['sectors'][0]['price_change_verified'])
                self.assertFalse(report['sectors'][0]['trading_value_verified'])

    def test_missing_not_zero_and_afternoon_onset_needs_previous_observation(self):
        self.assertFalse(classify(None,[group()],{})['available'])
        self.assertFalse(classify([group()],None,{})['available'])
        no_previous=classify([group()],[],{'available':False})
        self.assertIn('발생 시점 미확인',no_previous['sectors'][0]['label'])
        previous={'available':True,'sectors':[{'sector_id':1,'new_cluster':True}]}
        self.assertEqual(classify([group()],[],previous)['sectors'][0]['label'],'오후 신규 부상(직전 대비)')

    def test_composition_and_attention_rotation_remain_separate(self):
        result=classify([group(key=2)],[group(key=1)],{'available':False})
        self.assertTrue(result['rotation_candidate'])
        self.assertEqual(result['weakening_sectors'],['1'])
        self.assertEqual(result['rising_sectors'],['2'])
        changed=group()
        changed['stocks']=[{'stock_code':'different'}]
        self.assertTrue(classify([changed],[group()],{})['sectors'][0]['composition_changed'])


class DashboardTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'stock1.db'
        self.store=Store(self.path)
        self.addCleanup(self.store.close)
        self.output=Path(self.temp.name)/'dashboard.html'
        self.day=date(2026,10,20)
        for slot in ('09:40','13:00','15:00'):
            at=slot_at(self.day,slot)
            identifier=self.store.begin(at,'mock','1',scheduled_at=at)
            self.store.finish(identifier,Snapshot(at,[{'bigd_rank':'1','stk_cd':'005930','stk_nm':'삼성전자'},
                                                     {'bigd_rank':'2','stk_cd':'000660','stk_nm':'SK하이닉스'}]))
        manager=SectorManager(self.store)
        manager.add('반도체</script><script>alert(1)</script>')
        for code in ('005930','000660'):
            manager.tag(code,'반도체</script><script>alert(1)</script>')

    def data(self,path):
        text=path.read_text(encoding='utf-8')
        data=text.split('<script type="application/json" id="data">',1)[1].split('</script>',1)[0]
        return text,json.loads(data)

    def test_html_read_only_environment_missing_and_script_escaping(self):
        before=list(self.store.db.iterdump())
        with patch('kiwoom_collector.client.requests.Session',side_effect=AssertionError('no API')):
            export_dashboard(self.path,self.day.isoformat(),'mock','1',self.output)
        text,data=self.data(self.output)
        self.assertNotIn('반도체</script><script>alert(1)</script>',text)
        self.assertNotIn('src="http',text)
        self.assertEqual(data['environment'],'mock')
        self.assertEqual(next(s for s in data['slots'] if s['slot']=='14:00')['summary'],None)
        self.assertTrue(next(s for s in data['slots'] if s['slot']=='13:00')['classification']['available'])
        self.assertEqual(list(self.store.db.iterdump()),before)
        self.assertEqual(len(next(s for s in data['slots'] if s['slot']=='15:00')['review_stocks']),2)

    def test_real_report_does_not_include_mock_observations(self):
        export_dashboard(self.path,self.day.isoformat(),'real','1',self.output)
        _,data=self.data(self.output)
        self.assertEqual(data['environment'],'real')
        self.assertTrue(all(slot['summary'] is None for slot in data['slots']))

    def test_no_overwrite_and_no_database_creation(self):
        self.output.write_text('preserve',encoding='utf-8')
        with self.assertRaises(ValueError):
            export_dashboard(self.path,self.day.isoformat(),'mock','1',self.output)
        self.assertEqual(self.output.read_text(encoding='utf-8'),'preserve')
        with self.assertRaises(ValueError):
            export_dashboard(self.path,self.day.isoformat(),'mock','1',self.path)
        missing=Path(self.temp.name)/'missing.db'
        with self.assertRaises(ValueError):
            export_dashboard(missing,self.day.isoformat(),'mock','1',self.output.with_name('missing.html'))
        self.assertFalse(missing.exists())

    def test_cli_routes_before_store_and_credentials(self):
        with patch('kiwoom_collector.__main__.Store',side_effect=AssertionError('no migration')),patch('kiwoom_collector.__main__.load_credentials',side_effect=AssertionError('no keys')),patch('builtins.print'):
            self.assertEqual(main(['sector','dashboard','--date',self.day.isoformat(),'--environment','mock','--db',str(self.path),'--output',str(self.output)]),0)
        self.assertTrue(self.output.is_file())
