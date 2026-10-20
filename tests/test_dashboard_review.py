from datetime import date
import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from kiwoom_collector.timeline import _change, compare, sector_timeline, _groups
from kiwoom_collector.classification import classify
from kiwoom_collector.dashboard import export_dashboard
from kiwoom_collector.client import Snapshot
from kiwoom_collector.schedule import slot_at
from kiwoom_collector.storage import Store
from kiwoom_collector.sectors import SectorManager


def stock(code, rank):
    return {'stock_code':code, 'stock_name':code+' 종목', 'rank':rank}


def summary(groups, unclassified=()):
    return {'sectors':[{'sector_id':i,'sector_name':str(i),'stocks':members,
                       'stock_count':len(members),'best_rank':min(s['rank'] for s in members)}
                      for i,members in groups.items()], 'unclassified_stocks':list(unclassified)}


class TurnoverReviewTests(TestCase):
    def test_global_and_sector_turnover_multitag_entries_exits_and_rank_changes(self):
        previous=summary({1:[stock('A',1),stock('B',2)],2:[stock('A',1),stock('C',8)]},[stock('D',15)])
        current=summary({1:[stock('A',6),stock('E',1)],2:[stock('A',6),stock('C',3)]},[stock('F',9)])
        result=compare(current,previous)
        self.assertEqual((result['previous_unique_count'],result['current_unique_count']),(4,4))
        self.assertEqual((result['entered_count'],result['exited_count'],result['retained_count']),(2,2,2))
        self.assertEqual([s['stock_code'] for s in result['entered']],['E','F'])
        self.assertEqual([s['stock_code'] for s in result['exited']],['B','D'])
        self.assertEqual(result['replacement_percent'],50)
        self.assertEqual(result['retention_percent'],50)
        sectors={s['sector_id']:s for s in result['sectors']}
        self.assertEqual(sectors[1]['replacement_percent'],50)
        self.assertEqual((sectors[1]['entered_count'],sectors[1]['exited_count'],sectors[1]['retained_count']),(1,1,1))
        self.assertEqual(sectors[2]['replacement_percent'],0)
        self.assertEqual(sectors[2]['retained_count'],2)
        changes={s['stock_code']:s['improvement'] for s in result['rank_changes']}
        self.assertEqual(changes,{'A':-5,'C':5})

    def test_unequal_sizes_growth_contraction_and_empty_denominators(self):
        a,b,c=stock('A',1),stock('B',2),stock('C',3)
        prior={'A':a,'B':b}
        growth=_change({'A':a,'B':b,'C':c},prior)
        self.assertEqual(growth['replacement_percent'],0)
        self.assertAlmostEqual(growth['current_entry_percent'],100/3)
        self.assertEqual(_change({'A':a},prior)['replacement_percent'],50)
        self.assertEqual(_change({},prior)['replacement_percent'],100)
        self.assertIsNone(_change({'C':c},{})['replacement_percent'])
        self.assertIsNone(_change({}, {})['retention_percent'])
        missing=compare(None,summary({1:[a,b]}))
        self.assertFalse(missing['available'])
        self.assertIsNone(missing['replacement_percent'])

    def test_equal_count_quality_strength_weakness_mixed_and_composition(self):
        cases=[([(6,11),(3,7)],'오전 대비 강화'), ([(3,7),(6,11)],'약화'),
               ([(2,8),(5,6)],'약화'), ([(4,11),(6,7)],'혼조')]
        for (oldranks,newranks),expected in cases:
            with self.subTest(expected=expected,old=oldranks,new=newranks):
                old=list(_groups(summary({1:[stock('A',oldranks[0]),stock('B',oldranks[1])]})).values())
                new=list(_groups(summary({1:[stock('A',newranks[0]),stock('B',newranks[1])]})).values())
                result=classify(new,old,{'available':False})['sectors'][0]
                self.assertEqual(result['label'],expected)
                self.assertEqual(result['metric_changes']['count_delta'],0)
                self.assertEqual(result['replacement_percent'],0)
        old=list(_groups(summary({1:[stock('A',2),stock('B',8)]})).values())
        new=list(_groups(summary({1:[stock('A',2),stock('C',8)]})).values())
        changed=classify(new,old,{})['sectors'][0]
        self.assertEqual(changed['label'],'구성 교체(지표 유지)')
        self.assertEqual(changed['replacement_percent'],50)
        self.assertEqual(changed['composition_label'],'일부 교체')
        self.assertEqual(changed['entered_codes'],['C'])
        self.assertEqual(changed['exited_codes'],['B'])
        stronger=list(_groups(summary({1:[stock('C',1),stock('D',4)]})).values())
        full=classify(stronger,old,{})['sectors'][0]
        self.assertEqual(full['label'],'오전 대비 강화')
        self.assertEqual(full['replacement_percent'],100)
        self.assertEqual(full['composition_label'],'전면 교체')


class DashboardReadOnlyReviewTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'stock1.db'
        self.store=Store(self.path)
        self.addCleanup(self.store.close)
        self.day=date(2026,10,20)
        for slot,codes in (('09:40',['005930','000660']),('13:00',['005930','035420','035720'])):
            at=slot_at(self.day,slot)
            identifier=self.store.begin(at,'mock','1',scheduled_at=at)
            self.store.finish(identifier,Snapshot(at,[{'bigd_rank':str(i),'stk_cd':c,'stk_nm':c+' 종목'} for i,c in enumerate(codes,1)]))
        manager=SectorManager(self.store)
        manager.add('반도체')
        manager.tag('005930','반도체')

    def test_unclassified_ratio_missing_and_entire_database_unchanged(self):
        before=list(self.store.db.iterdump())
        with patch('kiwoom_collector.client.requests.Session',side_effect=AssertionError('no API')):
            result=sector_timeline(self.path,self.day.isoformat())
            path=Path(self.temp.name)/'review.html'
            export_dashboard(self.path,self.day.isoformat(),'mock','1',path)
        slots={s['slot']:s for s in result['slots']}
        self.assertEqual(slots['13:00']['summary']['unclassified_stock_count'],2)
        self.assertAlmostEqual(slots['13:00']['summary']['unclassified_ratio_percent'],200/3)
        self.assertEqual(slots['14:00']['status'],'missing')
        self.assertIsNone(slots['14:00']['summary'])
        self.assertFalse(slots['15:00']['versus_previous']['available'])
        text=path.read_text(encoding='utf-8')
        self.assertIn('id="coverage"',text)
        self.assertIn('전체 고유 종목 교체율',text)
        self.assertIn('섹터 내 교체율(직전 대비)',text)
        self.assertIn('섹터 내 교체율(09:40 대비)',text)
        self.assertEqual(list(self.store.db.iterdump()),before)
