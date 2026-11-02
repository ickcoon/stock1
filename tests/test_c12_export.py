"""C12-AE tests exclusively against existing synthetic fixtures and temp DBs."""
from contextlib import closing
from copy import deepcopy
from decimal import Decimal
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from kiwoom_collector import c12_export as exporter, c12_replay, am_input_adapter as adapter
from kiwoom_collector.cross_adapter import readonly_database
from kiwoom_collector.turnover_rank import SCHEMA
import test_am_input_adapter as fixtures
import test_c10_am_integration as master_fixtures


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.AMInputAdapterTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))
        for target in ('requests.Session.request','kiwoom_collector.telegram_briefing.deliver',
                       'kiwoom_collector.client.KiwoomClient.fetch_once','kiwoom_collector.client.KiwoomClient.ensure_token'):
            mocked=self.enterContext(patch(target,side_effect=AssertionError('external call forbidden')))
            self.addCleanup(mocked.assert_not_called)
        self.turnover=self.root/'turnover.db'
        with closing(sqlite3.connect(self.fixture.db)) as db,db:
            db.execute('DELETE FROM quote_observations WHERE request_finished_at=?',(fixtures.CUTOFF,))
        with closing(sqlite3.connect(self.turnover)) as db,db:
            db.executescript(SCHEMA)
            for index,slot in enumerate(('09:40','09:50')):
                at='2026-10-29T'+slot+':00+09:00';run='synthetic'+str(index)
                db.execute('''INSERT INTO turnover_runs(id,api_id,environment,source_kind,test_mode,spec_version,request_json,profile_hash,created_at,status,issues_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(run,'ka10032','mock','fixture',1,'synthetic',json.dumps(dict(stex_tp='1',mrkt_tp='000',mang_stk_incls='0')),'synthetic',at,'success','[]'))
                db.execute('''INSERT INTO turnover_pages(run_id,page_no,started_at,finished_at,http_status,return_code,status,row_count,issues_json,response_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',(run,1,at,at,200,0,'success',1,'[]','{}'))
                raw=dict(stk_cd='000001',now_rank='1',trde_prica=str(90000*(index+1)),flu_rt='7',cur_prc='10000')
                db.execute('INSERT INTO turnover_rows VALUES(?,?,?,?,?,?,?,?,?)',(run,1,1,'000001',1,'syntax_valid',json.dumps(raw),'{}','[]'))
        self.manifest=self.fixture.make_manifest()
        source=adapter.read_sources(self.turnover,role='turnover')
        template=deepcopy(next(iter(self.manifest['records'].values())))
        for record in source['records']:
            self.manifest['records'][adapter.fingerprint(record)]=dict(template,source_sha256=source['sha256'],
                observed_at=record['request_finished_at'],received_at=record['request_finished_at'])

    def export(self,**kwargs):
        args=dict(trade_date='2026-10-29',environment='mock',synthetic=True,
                  turnover_db=self.turnover,evidence=self.manifest)
        args.update(kwargs)
        return exporter.export(self.fixture.db,**args)

    def test_readonly_deterministic_export_and_replay_cli(self):
        sources=(self.fixture.db,self.turnover)
        before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in sources}
        first=self.export();second=self.export()
        self.assertEqual(first,second)
        output=self.root/'export.json';output.write_text(json.dumps(first),encoding='utf-8')
        docs,_=c12_replay.load(output)
        result=c12_replay.replay(docs,trade_date='2026-10-29')
        self.assertEqual(len(result['phases']),3)
        self.assertTrue(all(p['status']=='보류' for p in result['phases']))
        with patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(c12_replay.main(['--input',str(output),'--date','2026-10-29','--output',str(self.root/'replay.json')]),0)
        for path,(raw,mtime) in before.items():
            self.assertEqual(path.read_bytes(),raw);self.assertEqual(path.stat().st_mtime_ns,mtime)
            self.assertFalse(Path(str(path)+'-wal').exists())

    def test_repeated_observations_codes_units_and_times(self):
        result=self.export()
        turns=[r for r in result['export']['observations'] if r['kind']=='turnover']
        self.assertEqual(len(turns),2)
        self.assertEqual([r['stock_code'] for r in turns],['000001','000001'])
        self.assertEqual({r['turnover_unit'] for r in turns},{'million_KRW'})
        self.assertEqual({r['rank'] for r in turns},{'1'})
        self.assertEqual({r['stored_rank'] for r in turns},{1})
        self.assertTrue(all(r['unit_verified'] for r in turns))
        metric=result['documents'][-1]['securities']['000001']['turnover'][-1]
        self.assertEqual(Decimal(metric['value']),Decimal('180000000000'))
        self.assertEqual(metric['unit'],'KRW')

    def test_window_and_date_filter(self):
        result=self.export(from_time='09:30',until='09:45')
        self.assertEqual([d['selection_cutoff_at'][11:16] for d in result['documents']],['09:40','09:45'])
        rows=[r for r in result['export']['observations'] if r['kind']=='turnover']
        self.assertEqual(len(rows),1);self.assertIn('09:40',rows[0]['response_received_at'])
        self.assertTrue(all(r['observed_at'][11:16]>='09:30' for d in result['documents'] for r in d['rankings']))
        other=self.export(trade_date='2026-10-30')
        self.assertFalse(any(r['kind']!='sector' for r in other['export']['observations']))

    def test_missing_proof_does_not_invent_market_time_or_unit(self):
        result=self.export(evidence=None)
        turns=[r for r in result['export']['observations'] if r['kind']=='turnover']
        self.assertTrue(all(r['market_observed_at'] is None and r['turnover_unit'] is None for r in turns))
        self.assertTrue(all(r['response_received_at'] is not None for r in turns))
        self.assertIn('verification_manifest',result['export']['unavailable'])
        self.assertEqual(result['documents'][-1]['rankings'],[])
        self.assertTrue(all('market_observed_at' in r['unavailable'] for r in turns))

    def test_source_receipt_and_market_time_are_distinct(self):
        source=adapter.read_sources(self.turnover,role='turnover')
        for record in source['records']:
            proof=self.manifest['records'][adapter.fingerprint(record)]
            if '09:50' in proof['observed_at']:proof['observed_at']='2026-10-29T09:49:58+09:00'
        result=self.export()
        row=next(r for r in result['export']['observations'] if r['kind']=='turnover' and '09:50' in r['response_received_at'])
        self.assertIn('09:49:58',row['market_observed_at'])
        self.assertIn('09:50:00',row['response_received_at'])

    def test_private_payloads_ids_and_paths_are_not_exported(self):
        private=dict(api_key='PRIVATE_APP',access_token='PRIVATE_ACCESS',chat_id='987654321',
                     person='PRIVATE_PERSON',path='C:/PRIVATE_PATH/db',authorization='Bearer PRIVATE_ACCESS')
        with closing(sqlite3.connect(self.fixture.db)) as db,db:
            db.execute('ALTER TABLE collections ADD COLUMN response_json')
            db.execute('UPDATE collections SET response_json=?',(json.dumps(private),))
            db.execute('UPDATE ranking_records SET stk_nm=?',('987654321',))
        with closing(sqlite3.connect(self.turnover)) as db,db:
            db.execute('UPDATE turnover_pages SET response_json=?',(json.dumps(private),))
        self.manifest=self.fixture.make_manifest()  # New DB hashes deliberately invalidate old 0186 proofs.
        text=json.dumps(self.export(),ensure_ascii=False)
        for value in ('PRIVATE_APP','PRIVATE_ACCESS','987654321','PRIVATE_PERSON','PRIVATE_PATH',str(self.root)):
            self.assertNotIn(value,text)
        self.assertNotIn('response_json',text)

    def test_master_stored_values_only_and_original_metadata_times(self):
        fixture=master_fixtures.MasterAdapterIntegrationTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        before=fixture.master.read_bytes()
        result=exporter.export(fixture.db,trade_date='2026-10-29',environment='mock',synthetic=True,
            master_db=fixture.master,evidence=fixture.fixture.fixture.manifest)
        stored=[r for phase in result['export']['master_snapshots'] for r in phase['records']]
        self.assertTrue(any(r.get('capital') for r in stored))
        self.assertTrue(any(r.get('sector') for r in stored))
        for doc in result['documents']:
            for sec in doc['securities'].values():
                for tag in sec.get('sectors',[]):
                    self.assertNotEqual(tag['received_at'],doc['selection_cutoff_at'])
            self.assertIn('candidate_filter',doc)
        self.assertEqual(fixture.master.read_bytes(),before)
        replay=c12_replay.replay(result['documents'],trade_date='2026-10-29')
        self.assertTrue(all(p['status']!='검증 불가' for p in replay['phases']))

    def test_multiple_per_slot_0186_databases_keep_both_observations(self):
        other=self.root/'0950.db';other.write_bytes(self.turnover.read_bytes())
        for path,remove in ((self.turnover,'synthetic1'),(other,'synthetic0')):
            with closing(sqlite3.connect(path)) as db,db:
                db.execute('DELETE FROM turnover_rows WHERE run_id=?',(remove,))
            source=adapter.read_sources(path,role='turnover')
            for record in source['records']:
                self.manifest['records'][adapter.fingerprint(record)]['source_sha256']=source['sha256']
        result=self.export(turnover_db=[self.turnover,other])
        turns=[r for r in result['export']['observations'] if r['kind']=='turnover']
        self.assertEqual(len(turns),2)
        values=result['documents'][-1]['securities']['000001']['turnover']
        self.assertIn('180000000000',[v['value'] for v in values])
        self.assertIn('90000000000',[v['value'] for v in values])
        with self.assertRaises(ValueError):self.export(turnover_db=[other,other])

    def test_stored_declared_unit_is_not_promoted_to_runtime_verification(self):
        stored=dict(turnover_source_unit='million_KRW',unit_spec_verified=True,unit_runtime_verified=False)
        with closing(sqlite3.connect(self.turnover)) as db,db:
            db.execute('UPDATE turnover_rows SET normalized_json=?',(json.dumps(stored),))
        result=self.export(evidence=None)
        turns=[r for r in result['export']['observations'] if r['kind']=='turnover']
        self.assertTrue(all(r['turnover_unit']=='million_KRW' and not r['unit_verified'] for r in turns))
        self.assertTrue(all(r['stored_api_fields']['unit_runtime_verified'] is False for r in turns))

    def test_raw_numeric_format_and_missing_receipt_are_preserved(self):
        with closing(sqlite3.connect(self.turnover)) as db,db:
            raw=json.loads(db.execute("SELECT raw_json FROM turnover_rows WHERE run_id='synthetic1'").fetchone()[0])
            raw['trde_prica']='180,000'
            db.execute("UPDATE turnover_rows SET raw_json=? WHERE run_id='synthetic1'",(json.dumps(raw),))
            db.execute("UPDATE turnover_pages SET finished_at=NULL WHERE run_id='synthetic1'")
        result=self.export(evidence=None)
        row=next(r for r in result['export']['observations'] if r.get('turnover_raw')=='180,000')
        self.assertIsNone(row['response_received_at'])
        self.assertEqual(row['time_filter_basis'],'stored_request_started_at')
        self.assertIn('response_received_at',row['unavailable'])

    def test_receipt_after_until_is_not_replaced_with_collection_start(self):
        source=adapter.read_sources(self.fixture.db)
        for record in source['records']:
            if record['kind']=='ranking' and record['collected_at']==fixtures.CUTOFF:
                self.manifest['records'][adapter.fingerprint(record)]['received_at']='2026-10-29T09:50:01+09:00'
        result=self.export()
        self.assertFalse(any(r['kind']=='ranking' and r['collection_started_at']==fixtures.CUTOFF
                             for r in result['export']['observations']))
        self.assertFalse(any(r['slot']=='09:50' for r in result['documents'][-1]['rankings']))

    def test_sidecar_refused_and_sql_writes_denied(self):
        with readonly_database(self.fixture.db) as (db,_):
            with self.assertRaises(sqlite3.DatabaseError):db.execute('DELETE FROM collections')
        journal=Path(str(self.fixture.db)+'-wal');journal.write_bytes(b'SYNTHETIC_ONLY')
        before=self.fixture.db.read_bytes()
        with self.assertRaises(ValueError):self.export()
        self.assertEqual(self.fixture.db.read_bytes(),before)

    def test_cli_new_file_only_and_export_replay(self):
        evidence=self.root/'proof.json';evidence.write_text(json.dumps(self.manifest),encoding='utf-8')
        output=self.root/'portable.json'
        args=['--ranking-db',str(self.fixture.db),'--turnover-db',str(self.turnover),
              '--evidence',str(evidence),'--synthetic','--environment','mock','--date','2026-10-29','--output',str(output)]
        with patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(exporter.main(args),0)
            before=output.read_bytes()
            self.assertEqual(exporter.main(args),1)
        self.assertEqual(output.read_bytes(),before)
        with patch.object(exporter,'export',side_effect=RuntimeError('PRIVATE_SOURCE_PATH')),patch('sys.stdout',new_callable=io.StringIO) as console:
            self.assertEqual(exporter.main(args),1)
            self.assertNotIn('PRIVATE_SOURCE_PATH',console.getvalue())
        documents,_=c12_replay.load(output)
        self.assertEqual(c12_replay.replay(documents,trade_date='2026-10-29')['phases'][-1]['status'],'보류')


if __name__=='__main__':unittest.main()
