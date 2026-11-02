"""Only existing synthetic fixtures/temporary SQLite; external calls forbidden."""
from copy import deepcopy
from decimal import Decimal
from contextlib import closing
import io
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import c12_replay as replay, am_briefing
from test_c12ac_morning import document
import test_am_input_adapter as adapter_fixtures


class ReplayTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))
        for target in ('requests.Session.request','kiwoom_collector.telegram_briefing.deliver',
                       'kiwoom_collector.client.KiwoomClient.fetch_once',
                       'kiwoom_collector.client.KiwoomClient.ensure_token'):
            mock=self.enterContext(patch(target,side_effect=AssertionError('external call forbidden')))
            self.addCleanup(mock.assert_not_called)
        self.docs=[document(s) for s in replay.SLOTS]

    def run_replay(self,docs=None):
        return replay.replay(docs or self.docs,trade_date='2026-10-29')

    def test_normal_engine_consistency_and_determinism(self):
        before=deepcopy(self.docs)
        first=self.run_replay();second=self.run_replay()
        self.assertEqual(first,second);self.assertEqual(self.docs,before)
        self.assertEqual([p['status'] for p in first['phases']],['잠정']*3)
        for phase in first['phases']:
            self.assertGreater(len(phase['sectors']),0)
            self.assertEqual(phase['session']['phases'],list(replay.SLOTS[:replay.SLOTS.index(phase['slot'])+1]))
            am_briefing.validate(phase['analysis_report'])
        self.assertEqual(len(first['phases'][-1]['sectors']),3)
        self.assertTrue(any(c['before']=='보류' and c['after']=='잠정'
                            for c in first['phases'][-1]['changes']['status_changes']))
        self.assertEqual(first['external_calls'],0)
        self.assertFalse(first['delivery_attempted'])

    def test_future_observation_and_late_receipt_excluded(self):
        for doc in self.docs:
            extra=deepcopy(doc['securities']['000001']['market_cap'][0])
            extra['received_at']='2026-10-29T09:51:00+09:00'
            extra['value']='99999999999999999'
            doc['securities']['000001']['market_cap'].append(extra)
            extra=deepcopy(doc['rankings'][0]);extra['observed_at']='2026-10-29T09:51:00+09:00'
            extra['received_at']=extra['observed_at'];doc['rankings'].append(extra)
        result=self.run_replay()
        for phase in result['phases']:
            self.assertGreaterEqual(phase['excluded_future_records'],2)
            serialized=json.dumps(phase['analysis_report'])
            self.assertNotIn('99999999999999999',serialized)
            self.assertNotIn('09:51:00',serialized)

    def test_missing_and_delayed_final_observation(self):
        self.docs[-1]['rankings'][-1]['received_at']='2026-10-29T09:50:01+09:00'
        for sec in self.docs[-1]['securities'].values():sec['turnover']=[]
        final=self.run_replay()['phases'][-1]
        self.assertEqual(final['status'],'보류')
        self.assertIn('09:50',final['completeness']['missing_slots'])
        self.assertTrue(all(c['turnover']=='검증 불가' for c in final['candidates']))
        self.assertTrue(final['changes']['exited'])

    def test_duplicate_collection_not_silently_merged(self):
        self.docs[1]['rankings'].append(deepcopy(self.docs[1]['rankings'][0]))
        result=self.run_replay()
        self.assertEqual(result['phases'][1]['error'],'duplicate_slot')
        self.assertEqual(result['phases'][2]['changes']['from_slot'],'09:40')

    def test_leader_and_status_change(self):
        for doc in self.docs[1:]:
            for metric in doc['securities']['000002']['change_pct']:metric['value']='-1'
        result=self.run_replay()
        change=result['phases'][1]['changes']
        self.assertIn('000002',change['exited'])
        self.assertTrue(any(c['sector']=='반도체' for c in change['leader_changes']))
        self.assertIn(dict(stock_code='000002',before='잠정',after='보류'),change['status_changes'])
        self.assertTrue(all(isinstance(c['stock_code'],str) and len(c['stock_code'])==6
                            for p in result['phases'] for c in p['candidates']))

    def test_master_from_future_not_backdated(self):
        result=self.run_replay([self.docs[-1]])
        self.assertEqual(result['phases'][0]['status'],'보류')
        self.assertEqual(result['phases'][-1]['status'],'잠정')
        self.assertTrue(all(c['eligibility']['state']=='unmatched' for c in result['phases'][0]['candidates']))

    def test_saved_report_and_cli_byte_identical(self):
        source=self.root/'input.json'
        source.write_text(json.dumps(dict(schema_version='c12-replay-input-v1',documents=self.docs)),encoding='utf-8')
        for index in (1,2):
            with patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(replay.main(['--input',str(source),'--date','2026-10-29','--output',str(self.root/f'{index}.json')]),0)
        self.assertEqual((self.root/'1.json').read_bytes(),(self.root/'2.json').read_bytes())
        saved=self.root/'saved.json'
        saved.write_text(json.dumps(self.run_replay()['phases'][-1]['analysis_report']),encoding='utf-8')
        docs,_=replay.load(saved)
        self.assertEqual(self.run_replay(docs)['phases'][-1]['sectors'],self.run_replay()['phases'][-1]['sectors'])

    def test_synthetic_db_readonly_and_unit_conversion(self):
        fixture=adapter_fixtures.AMInputAdapterTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        before=fixture.db.read_bytes();mtime=fixture.db.stat().st_mtime_ns
        doc=fixture.adapt()
        result=replay.replay([doc],trade_date='2026-10-29',slots=('09:50',))
        candidate=next(c for c in result['phases'][0]['candidates'] if c['stock_code']=='000001')
        self.assertEqual(Decimal(candidate['turnover']['value']),Decimal(adapter_fixtures.FIXTURE['securities']['000001']['turnover'][-1]['value']))
        self.assertEqual(fixture.db.read_bytes(),before)
        self.assertEqual(fixture.db.stat().st_mtime_ns,mtime)
        self.assertFalse(Path(str(fixture.db)+'-wal').exists())

    def test_db_cli_requires_explicit_synthetic_scope(self):
        with patch.object(replay.am_input_adapter,'adapt') as adapt,patch('sys.stdout',new_callable=io.StringIO):
            code=replay.main(['--ranking-db',str(self.root/'not-opened.db'),'--date','2026-10-29','--output',str(self.root/'out.json')])
        self.assertEqual(code,1);adapt.assert_not_called()

    def test_0186_million_krw_and_db_cli_preserve_sources(self):
        from kiwoom_collector.turnover_rank import SCHEMA
        from kiwoom_collector import am_input_adapter as adapter
        fixture=adapter_fixtures.AMInputAdapterTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        turnover=fixture.root/'turnover.db'
        at=adapter_fixtures.CUTOFF
        with closing(sqlite3.connect(turnover)) as db,db:
            db.executescript(SCHEMA)
            db.execute('''INSERT INTO turnover_runs(id,api_id,environment,source_kind,test_mode,spec_version,request_json,profile_hash,created_at,status,issues_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',('turn','ka10032','mock','fixture',1,'synthetic',json.dumps(dict(stex_tp='1',mrkt_tp='000',mang_stk_incls='0')),'synthetic',at,'success','[]'))
            db.execute('''INSERT INTO turnover_pages(run_id,page_no,started_at,finished_at,http_status,return_code,status,row_count,issues_json,response_json)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',('turn',1,at,at,200,0,'success',1,'[]','{}'))
            raw=dict(stk_cd='000001',trde_prica='180000',flu_rt='7',cur_prc='10000')
            db.execute('INSERT INTO turnover_rows VALUES(?,?,?,?,?,?,?,?,?)',('turn',1,1,'000001',1,'syntax_valid',json.dumps(raw),'{}','[]'))
        with closing(sqlite3.connect(fixture.db)) as db,db:
            db.execute('DELETE FROM quote_observations WHERE request_finished_at=?',(at,))
        manifest=fixture.make_manifest()
        source=adapter.read_sources(turnover,role='turnover')
        template=deepcopy(next(iter(manifest['records'].values())))
        for record in source['records']:
            manifest['records'][adapter.fingerprint(record)]=dict(template,source_sha256=source['sha256'],observed_at=at,received_at=at)
        evidence=self.root/'proof.json';evidence.write_text(json.dumps(manifest),encoding='utf-8')
        before={p:p.read_bytes() for p in (fixture.db,turnover,evidence,fixture.capital)}
        output=self.root/'db-result.json'
        with patch('sys.stdout',new_callable=io.StringIO):
            code=replay.main(['--ranking-db',str(fixture.db),'--synthetic-db','--turnover-db',str(turnover),
                '--capital-json',str(fixture.capital),'--evidence',str(evidence),'--date','2026-10-29',
                '--slots','09:50','--output',str(output)])
        self.assertEqual(code,0)
        result=json.loads(output.read_text(encoding='utf-8'))
        candidate=next(c for c in result['phases'][0]['candidates'] if c['stock_code']=='000001')
        self.assertEqual(Decimal(candidate['turnover']['value']),Decimal('180000000000'))
        for path,raw in before.items():self.assertEqual(path.read_bytes(),raw)

    def test_input_output_not_overwritten_and_invalid_phase_rejected(self):
        source=self.root/'input.json';source.write_text(json.dumps(self.docs[-1]),encoding='utf-8')
        before=source.read_bytes()
        with patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(replay.main(['--input',str(source),'--date','2026-10-29','--output',str(source)]),1)
        self.assertEqual(source.read_bytes(),before)
        with self.assertRaises(ValueError):replay.replay(self.docs,trade_date='2026-10-29',slots=('15:00',))


if __name__=='__main__':unittest.main()
