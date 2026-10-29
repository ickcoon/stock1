"""Synthetic SQLite/export integration only. Manifest review is simulated here."""
from copy import deepcopy
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import am_input_adapter as a, am_briefing as b, am_momentum as am, telegram_briefing as tg

FIXTURE = json.loads((Path(__file__).parent/'fixtures/am_sector_briefing_synthetic.json').read_text(encoding='utf-8'))
CUTOFF = '2026-10-29T09:50:00+09:00'


class AMInputAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.db = self.root/'source.db'; self.capital = self.root/'capital.json'
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript('''
                CREATE TABLE collections(id,collected_at,scheduled_at,environment,api_id,qry_tp,test_mode,status,record_count);
                CREATE TABLE ranking_records(collection_id,rank,stk_cd,stk_nm);
                CREATE TABLE sectors(sector_id,sector_name);
                CREATE TABLE stock_sectors(stock_code,sector_id,updated_at);
                CREATE TABLE quote_batches(id,environment,collection_id,api_id,status,test_mode);
                CREATE TABLE quote_observations(batch_id,collection_id,rank,requested_code,api_id,test_mode,status,
                    request_started_at,request_finished_at,http_status,return_code,cur_prc,flu_rt,trde_prica,date,tm,request_id);
            ''')
            for index, snapshot in enumerate(FIXTURE['rankings']):
                identifier = f'run{index}'; at = snapshot['observed_at']
                db.execute('INSERT INTO collections VALUES(?,?,?,?,?,?,?,?,?)', (identifier,at,at,'mock','ka00198','1',0,'success',20))
                for stock in snapshot['stocks']:
                    code = stock['stk_cd']; sec = FIXTURE['securities'].get(code)
                    name = sec['names'][0]['value'] if sec else '가상종목'+code
                    db.execute('INSERT INTO ranking_records VALUES(?,?,?,?)',(identifier,stock['rank'],code,name))
                if index not in (0,3): continue
                db.execute('INSERT INTO quote_batches VALUES(?,?,?,?,?,?)', (identifier,'mock',identifier,'ka10007','success',1))
                for code, sec in FIXTURE['securities'].items():
                    rank = next(s['rank'] for s in snapshot['stocks'] if s['stk_cd']==code)
                    turnover = sec['turnover'][0 if index==0 else -1]['value']
                    price = sec['price'][0 if index==0 else -1]['value']
                    db.execute('INSERT INTO quote_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (identifier,identifier,rank,code,'ka10007',1,'success',at,at,200,0,
                                price,sec['change_pct'][0]['value'],str(am.Decimal(turnover)/1000000),
                                '20261029',snapshot['slot'].replace(':','')+'00',identifier+code))
            for index,(code,sec) in enumerate(FIXTURE['securities'].items()):
                db.execute('INSERT INTO sectors VALUES(?,?)',(index,sec['sectors'][0]['name']))
                db.execute('INSERT INTO stock_sectors VALUES(?,?,?)',(code,index,'2026-10-29T09:00:00+09:00'))
        self.write_capital()
        self.manifest = self.make_manifest()

    def write_capital(self, source='fixture'):
        records = [dict(api_id='ka10001',environment='mock',requested_code=code,request_started_at=CUTOFF,response_received_at=CUTOFF,
                        http_status=200,return_code=0,issues=[],raw=dict(stk_cd=code,mac='10000')) for code in FIXTURE['securities']]
        self.capital.write_text(json.dumps(dict(source_kind=source,records=records)),encoding='utf-8')

    def make_manifest(self, synthetic=True):
        manifest = dict(schema_version='am-verification-manifest-v1',synthetic=synthetic,records={})
        for source in [a.read_sources(self.db),a.read_capital(self.capital)]:
            for record in source['records']:
                observed = record.get('collected_at') or record.get('request_finished_at') or record.get('response_received_at') or '2026-10-29T09:20:00+09:00'
                proof = dict(source_sha256=source['sha256'],verified=True,environment='mock',market='KRX',trade_date='2026-10-29',
                             market_verified=True,market_time_verified=True,observed_at=observed,received_at=observed,
                             evidence_reference='synthetic_review_only',unit_runtime_verified=True,
                             units=dict(trde_prica='million_KRW',flu_rt='percent',cur_prc='KRW',mac='hundred_million_KRW'),
                             turnover_period='intraday_cumulative',price_sign_verified=True,market_cap_basis_verified=True)
                manifest['records'][a.fingerprint(record)] = proof
        return manifest

    def adapt(self, **kwargs):
        args=dict(trade_date='2026-10-29',cutoff_at=CUTOFF,environment='mock',evidence=self.manifest,
                  capital_json=self.capital,synthetic=True)
        args.update(kwargs)
        return a.adapt(self.db,**args)

    def proofs(self, kind):
        sources=[a.read_sources(self.db),a.read_capital(self.capital)]
        return [(r,self.manifest['records'][a.fingerprint(r)]) for source in sources for r in source['records'] if r['kind']==kind]

    def report(self, **kwargs):
        return b.analyze(self.adapt(**kwargs),generated_at='2026-10-29T10:00:00+09:00')

    def test_full_synthetic_sqlite_to_three_sector_briefing_and_growth(self):
        report = self.report()
        self.assertEqual([len(s['leaders']) for s in report['sectors']],[3,3,3])
        self.assertIn('합성 테스트 데이터',b.format_telegram(report))
        candidate=next(c for c in report['candidates'] if c['stock_code']=='000001')
        self.assertEqual(candidate['turnover_comparison']['increase_per_second'],'50000000')
        self.assertEqual(candidate['metrics']['market_cap']['value'],'1000000000000')

    def test_no_manifest_never_promotes_start_time_or_numeric_units(self):
        report=self.report(evidence=None)
        self.assertEqual(report['sectors'],[])
        self.assertEqual(report['completeness']['accepted_slots'],[])
        self.assertIn('bound_verification_missing',[d['reason'] for d in report['input_document']['adapter_diagnostics']['rejected']])

    def test_real_adapter_source_is_distinct_and_cannot_send(self):
        self.write_capital('collected_export'); self.manifest=self.make_manifest(synthetic=False)
        report=self.report(synthetic=False)
        self.assertEqual(report['source_kind'],'readonly_adapter')
        self.assertEqual(len(report['sectors']),3)
        text=b.format_telegram(report)
        self.assertNotIn('합성',text); self.assertIn('읽기 전용 수집 자료',text)
        with patch.object(tg.requests,'Session',side_effect=AssertionError('HTTP')):
            with self.assertRaisesRegex(ValueError,'readonly_report_not_deliverable'):
                tg.deliver(report,text,None,None)

    def test_source_bytes_unchanged_no_sidecars_and_write_authorizer_denies(self):
        before=self.db.read_bytes(); cap_before=self.capital.read_bytes(); self.report()
        self.assertEqual(self.db.read_bytes(),before); self.assertEqual(self.capital.read_bytes(),cap_before)
        self.assertFalse(Path(str(self.db)+'-wal').exists())
        with a.readonly_database(self.db) as (db,_):
            with self.assertRaises(sqlite3.DatabaseError): db.execute('DELETE FROM collections')
        self.assertEqual(self.db.read_bytes(),before)

    def test_missing_database_is_not_created(self):
        missing=self.root/'missing.db'
        with self.assertRaises(FileNotFoundError):
            a.adapt(missing,trade_date='2026-10-29',cutoff_at=CUTOFF,environment='mock')
        self.assertFalse(missing.exists())

    def test_active_wal_or_journal_refused_without_cleanup(self):
        for suffix in ['-wal','-shm','-journal']:
            path=Path(str(self.db)+suffix); path.write_bytes(b'synthetic-sidecar')
            try:
                with self.assertRaises(ValueError): self.adapt()
                self.assertEqual(path.read_bytes(),b'synthetic-sidecar')
            finally: path.unlink()

    def test_manifest_binding_breaks_after_source_mutation(self):
        with closing(sqlite3.connect(self.db)) as db, db: db.execute("UPDATE ranking_records SET stk_nm='changed' WHERE stk_cd='000001'")
        self.assertEqual(self.report()['sectors'],[])

    def test_timing_start_is_not_receipt_and_cutoff_is_strict(self):
        for _,p in self.proofs('ranking'):
            if p['observed_at']==CUTOFF: p['received_at']='2026-10-29T09:50:01+09:00'
        report=self.report()
        self.assertIn('09:50',report['completeness']['missing_slots'])
        self.assertEqual(report['sectors'],[])

    def test_mixed_day_environment_and_test_mode_do_not_join(self):
        self.assertEqual(self.report(environment='real')['sectors'],[])
        self.assertEqual(self.report(test_mode=True)['sectors'],[])
        self.assertEqual(self.report(trade_date='2026-10-28',cutoff_at='2026-10-28T09:50:00+09:00')['sectors'],[])

    def test_quote_wrong_code_parent_is_rejected(self):
        with closing(sqlite3.connect(self.db)) as db, db: db.execute("UPDATE quote_observations SET requested_code='000002' WHERE requested_code='000001'")
        self.manifest=self.make_manifest()
        report=self.report()
        candidate=next(c for c in report['candidates'] if c['stock_code']=='000001')
        self.assertEqual(candidate['status'],'held')
        self.assertIn('quote_parent_mismatch',[d['reason'] for d in report['input_document']['adapter_diagnostics']['rejected']])

    def test_stored_quote_market_date_and_time_must_match_proof(self):
        for date_value,time_value in [('20261028','095000'),('20261029','094900')]:
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute("UPDATE quote_observations SET date=?,tm=? WHERE request_finished_at=?",(date_value,time_value,CUTOFF))
            self.manifest=self.make_manifest()
            self.assertEqual(self.report()['sectors'],[])

    def test_units_and_period_never_assumed_from_spec(self):
        for _,p in self.proofs('quote'): p['units']['trde_prica']='KRW'
        self.assertEqual(self.report()['sectors'],[])
        self.manifest=self.make_manifest()
        for _,p in self.proofs('quote'): p['turnover_period']='daily_close'
        self.assertEqual(self.report()['sectors'],[])
        self.manifest=self.make_manifest()
        for _,p in self.proofs('quote'): p['unit_runtime_verified']=False
        self.assertEqual(self.report()['sectors'],[])

    def test_missing_capital_remains_missing(self):
        report=self.report(capital_json=None)
        self.assertEqual(report['sectors'],[])
        self.assertIn('capital',report['input_document']['adapter_diagnostics']['unavailable_sources'])
        self.assertTrue(all(c['metrics']['market_cap'] is None for c in report['candidates']))

    def test_independent_0186_database_maps_only_verified_same_scope_rows(self):
        from kiwoom_collector.turnover_rank import SCHEMA
        turnover=self.root/'turnover.db'
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('DELETE FROM quote_observations WHERE request_finished_at=?',(CUTOFF,))
        self.manifest=self.make_manifest()
        with closing(sqlite3.connect(turnover)) as db, db:
            db.executescript(SCHEMA)
            db.execute('''INSERT INTO turnover_runs(id,api_id,environment,source_kind,test_mode,spec_version,
                request_json,profile_hash,created_at,status,issues_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                ('turn','ka10032','mock','fixture',1,'synthetic',json.dumps(dict(stex_tp='1',mrkt_tp='000',mang_stk_incls='0')),
                 'synthetic',CUTOFF,'success','[]'))
            db.execute('''INSERT INTO turnover_pages(run_id,page_no,started_at,finished_at,http_status,return_code,
                status,row_count,issues_json,response_json) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                ('turn',1,CUTOFF,CUTOFF,200,0,'success',9,'[]','{}'))
            for index,(code,sec) in enumerate(FIXTURE['securities'].items(),1):
                raw=dict(stk_cd=code,trde_prica=str(am.Decimal(sec['turnover'][-1]['value'])/1000000),
                         flu_rt=sec['change_pct'][0]['value'],cur_prc=sec['price'][-1]['value'])
                db.execute('INSERT INTO turnover_rows VALUES(?,?,?,?,?,?,?,?,?)',
                           ('turn',1,index,code,index,'syntax_valid',json.dumps(raw),'{}','[]'))
        source=a.read_sources(turnover,role='turnover')
        template=deepcopy(self.proofs('quote')[0][1])
        for record in source['records']:
            self.manifest['records'][a.fingerprint(record)]=dict(template,source_sha256=source['sha256'],
                                                               observed_at=CUTOFF,received_at=CUTOFF)
        before=turnover.read_bytes()
        report=self.report(turnover_db=turnover)
        self.assertEqual(len(report['sectors']),3)
        self.assertEqual(turnover.read_bytes(),before)
        for record in source['records']:
            self.manifest['records'][a.fingerprint(record)]['turnover_period']='daily_close'
        self.assertEqual(self.report(turnover_db=turnover)['sectors'],[])

    def test_unverified_capital_basis_not_promoted(self):
        for _,p in self.proofs('capital'): p['market_cap_basis_verified']=False
        self.assertEqual(self.report()['sectors'],[])

    def test_missing_previous_turnover_does_not_invent_growth(self):
        for record,p in self.proofs('quote'):
            if record['request_finished_at']!=CUTOFF: p['verified']=False
        report=self.report()
        self.assertEqual(len(report['sectors']),3)
        candidate=next(c for c in report['candidates'] if c['stock_code']=='000001')
        self.assertEqual(candidate['turnover_comparison']['growth_status'],'미확인')

    def test_duplicate_metrics_and_rank_rows_rejected(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('INSERT INTO quote_observations SELECT * FROM quote_observations LIMIT 1')
        self.manifest=self.make_manifest()
        with self.assertRaisesRegex(ValueError,'duplicate_adapter_metric'): self.adapt()

    def test_ambiguous_scheduled_slot_and_old_sector_never_assumed(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE collections SET scheduled_at='2026-10-29T09:30:00+09:00' WHERE id='run0'")
        self.manifest=self.make_manifest()
        self.assertEqual(self.report()['sectors'],[])

    def test_missing_schema_and_manifest_scope_rejected(self):
        self.manifest['synthetic']=False
        with self.assertRaisesRegex(ValueError,'verification_manifest_scope_invalid'): self.adapt()
        wrong=self.root/'wrong.db'
        with closing(sqlite3.connect(wrong)) as db, db: db.execute('CREATE TABLE unrelated(id)')
        with self.assertRaisesRegex(ValueError,'ranking_schema_missing'): a.read_sources(wrong)

    def test_adapter_cli_reports_and_telegram_dry_run_without_network(self):
        manifest_path=self.root/'proof.json'; manifest_path.write_text(json.dumps(self.manifest),encoding='utf-8')
        from kiwoom_collector import d0_store
        with patch.object(am,'PROJECT',self.root),patch.object(am,'REPORTS',self.root/'reports'), \
             patch.object(tg,'PROJECT',self.root),patch.object(tg,'REPORTS',self.root/'reports'), \
             patch('socket.socket.connect',side_effect=AssertionError('network')), \
             patch.object(d0_store,'save_record',side_effect=AssertionError('D0')), \
             patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(a.main(['--ranking-db',str(self.db),'--capital-json',str(self.capital),'--evidence',str(manifest_path),
                                    '--date','2026-10-29','--cutoff',CUTOFF,'--environment','mock','--synthetic','--name','adapter']),0)
            self.assertEqual(json.loads(output.getvalue())['status'],'dry_run')
            _,text,html=tg.prepare(self.root/'reports/adapter.json',self.root/'reports/adapter.html')
            self.assertIn('1위 반도체',text); self.assertIn('analysis-data',html)
