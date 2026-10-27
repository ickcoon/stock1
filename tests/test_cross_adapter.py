"""Step 5-E fixture tests. All databases are temporary; no live/API access."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from kiwoom_collector import cross_adapter as ca
from kiwoom_collector import turnover_rank as tr


def document():
    def row(c,r,amount='50000'):
        return dict(stk_cd=c,stk_nm='fixture '+c,now_rank=str(r),trde_prica=amount,cur_prc='+070000',flu_rt='+1.25',now_trde_qty='100')
    ranking=dict(id='a',environment='mock',test_mode=False,collected_at='2026-10-27T15:00:00+09:00',qry_tp='1',status='success',market=None,
        rows=[dict(stk_cd='005930',stk_nm='fixture',rank=1),dict(stk_cd='000660',stk_nm='fixture',rank=6)])
    turnover=dict(id='b',environment='mock',test_mode=True,request_started_at='2026-10-27T15:03:28+09:00',market={'mrkt_tp':'000','mang_stk_incls':'0','stex_tp':'1'},status='partial',pagination_complete=0,
        rows=[row('005930',40),row('000660',41),row('123456',2,'49999.999')])
    return dict(ranking=ranking,turnover=turnover,history=[],code_registry={},sectors={})


class CrossAdapterAnalysisTests(unittest.TestCase):
    def setUp(self):self.doc=document()

    def test_500_eok_exact_boundary_and_rank_40_41(self):
        result=ca.analyze_pair(self.doc)
        self.assertEqual(result['counts']['turnover_meeting_numeric_filters'],1)
        self.assertTrue(result['rows'][0]['filter_selected'])
        self.assertFalse(result['rows'][1]['filter_selected'])
        self.assertEqual(result['rows'][0]['turnover_won_diagnostic'],'50000000000')
        self.assertEqual(result['rows'][1]['filter_exclusion_reasons'],['rank_over_40'])

    def test_1000_eok_option(self):
        self.doc['turnover']['rows'][0]['trde_prica']='100000'
        self.assertEqual(ca.analyze_pair(self.doc,min_turnover_eok=1000)['counts']['common_after_filter'],1)
        self.doc['turnover']['rows'][0]['trde_prica']='99999.99'
        self.assertEqual(ca.analyze_pair(self.doc,min_turnover_eok=1000)['counts']['common_after_filter'],0)
        for value in (0,500.0,True,501):
            with self.assertRaises(ValueError):ca.analyze_pair(self.doc,min_turnover_eok=value)

    def test_provisional_300_seconds_inclusive_and_301(self):
        for at,expected in (('15:05:00',True),('15:05:01',False),('14:55:00',True)):
            self.doc['turnover']['request_started_at']='2026-10-27T'+at+'+09:00'
            r=ca.analyze_pair(self.doc)
            self.assertIs(r['timing']['collection_window_provisional'],expected)
            self.assertFalse(r['candidate_confirmed'])
            self.assertFalse(r['timing']['market_time_verified'])

    def test_actual_async_delta_and_operational_test_provenance(self):
        r=ca.analyze_pair(self.doc)
        self.assertEqual(r['timing']['delta_seconds'],208)
        self.assertFalse(r['sources']['ranking']['test_mode'])
        self.assertTrue(r['sources']['turnover']['test_mode'])
        self.assertIn('operational_test_diagnostic_only',r['rows'][0]['reasons'])

    def test_mixed_environment_blocked(self):
        self.doc['turnover']['environment']='real'
        with self.assertRaises(ValueError):ca.analyze_pair(self.doc)

    def test_market_conditions_never_inferred_from_same_environment(self):
        self.assertEqual(ca.analyze_pair(self.doc)['market_match'],'unverified')
        self.doc['ranking']['market']=dict(self.doc['turnover']['market'])
        self.assertEqual(ca.analyze_pair(self.doc)['market_match'],'declared_match_unverified')
        self.doc['ranking']['market']['stex_tp']='2'
        self.assertEqual(ca.analyze_pair(self.doc)['market_match'],'mismatch')

    def test_zero_preserved_and_no_padding_case_or_suffix_rewrite(self):
        self.assertEqual(ca.analyze_pair(self.doc)['rows'][0]['stock_code'],'005930')
        for code in (5930,'5930','005930_NX','0158s0',' 005930','ABCDEF'):
            self.assertNotIn(ca.code_status(code,{}),('legacy_numeric_format','official_evidence'))

    def test_alpha_requires_specific_official_evidence(self):
        self.doc['ranking']['rows'][0]['stk_cd']='0193T0'
        self.doc['turnover']['rows'][0]['stk_cd']='0193T0'
        r=ca.analyze_pair(self.doc)
        self.assertEqual(len(r['review']['ranking']),1)
        self.doc['code_registry']={'0193T0':{'verified':True,'source':'https://example.test/synthetic-authority','security_type':'ETF'}}
        r=ca.analyze_pair(self.doc)
        self.assertEqual(r['rows'][0]['stock_code'],'0193T0')
        self.assertEqual(r['rows'][0]['security_type'],'ETF')
        self.assertFalse(r['rows'][0]['candidate_confirmed'])

    def test_duplicate_code_or_rank_does_not_pick_first(self):
        self.doc['turnover']['rows'].append(deepcopy(self.doc['turnover']['rows'][0]))
        r=ca.analyze_pair(self.doc)
        self.assertFalse(r['rows'][0]['filter_selected'])
        self.assertEqual(len(r['review']['turnover']),2)

    def test_invalid_amount_and_empty_source(self):
        self.doc['turnover']['rows'][0]['trde_prica']='not numeric'
        self.assertIn('invalid_turnover',ca.analyze_pair(self.doc)['excluded_turnover'][0]['reasons'])
        self.doc['turnover']['rows']=[]
        r=ca.analyze_pair(self.doc)
        self.assertEqual(r['counts']['common_before_filter'],0)
        self.assertEqual(r['missing_turnover_codes'],['000660','005930'])

    def test_history_counts_only_same_scope_success_not_future_duplicate(self):
        h=deepcopy(self.doc['ranking']);h.update(id='prior',collected_at='2026-10-27T14:00:00+09:00')
        self.doc['history']=[h,deepcopy(h)]
        future=deepcopy(h);future.update(id='future',collected_at='2026-10-27T16:00:00+09:00')
        self.doc['history'].append(future)
        r=ca.analyze_pair(self.doc)
        self.assertEqual(r['rows'][0]['top5_appearances'],2)
        self.assertEqual(r['history']['success_observations'],2)

    def test_partial_raw_rows_and_unclassified_not_changed(self):
        before=deepcopy(self.doc)
        with patch('socket.socket.connect',side_effect=AssertionError('Network forbidden')):
            r=ca.analyze_pair(self.doc)
        self.assertEqual(self.doc,before)
        self.assertEqual(r['sources']['turnover']['status'],'partial')
        self.assertIn('unclassified_sector',r['rows'][0]['reasons'])
        self.assertFalse(r['unit_runtime_verified'])
        self.assertEqual(r['rows'][1]['review_priority'],'보조 관찰')


class CrossAdapterDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.a=Path(self.temp.name)/'ranking.db';self.b=Path(self.temp.name)/'turnover.db'
        with closing(sqlite3.connect(self.a)) as db:
            db.executescript('''CREATE TABLE collections(id TEXT,collected_at TEXT,scheduled_at TEXT,environment TEXT,api_id TEXT,qry_tp TEXT,test_mode INTEGER,status TEXT,record_count INTEGER);
CREATE TABLE ranking_records(collection_id TEXT,rank INTEGER,stk_cd TEXT,stk_nm TEXT,raw_json TEXT);
CREATE TABLE stock_sectors(stock_code TEXT,sector_id INTEGER,source TEXT,updated_at TEXT);
CREATE TABLE sectors(sector_id INTEGER,sector_name TEXT);''')
            db.execute("INSERT INTO collections VALUES('r','2026-10-27T15:00:00+09:00','2026-10-27T15:00:00+09:00','mock','ka00198','1',0,'success',1)")
            db.execute("INSERT INTO ranking_records VALUES('r',1,'005930','synthetic','{}')")
            db.commit()
        fixture=json.loads((Path(__file__).parent/'fixtures/ka10032_synthetic.json').read_text(encoding='utf8'))
        fixture['pages'][0].update(request_started_at='2026-10-27T15:03:28+09:00',request_finished_at='2026-10-27T15:03:29+09:00')
        tr.collect(self.b,tr.FixtureTransport(fixture,tr.Profile(),'mock'))

    def test_adapt_readonly_original_hashes_and_no_sidecar_creation(self):
        before=[p.read_bytes() for p in (self.a,self.b)]
        d=ca.adapt(self.a,self.b,ranking_at='2026-10-27T15:00:00+09:00')
        self.assertEqual([p.read_bytes() for p in (self.a,self.b)],before)
        self.assertTrue(all(p['hash_unchanged'] for p in d['provenance'].values()))
        self.assertEqual(d['ranking']['market'],None)
        self.assertTrue(d['turnover']['test_mode'])
        self.assertEqual(len(d['turnover']['rows']),2)
        self.assertFalse(any(Path(str(p)+'-wal').exists() for p in (self.a,self.b)))

    def test_writes_and_ddl_rejected(self):
        with ca.readonly_database(self.a) as (db,_):
            with self.assertRaises(sqlite3.DatabaseError):db.execute('DELETE FROM collections')
            with self.assertRaises(sqlite3.DatabaseError):db.execute('CREATE TABLE forbidden(x)')

    def test_sidecars_refuse_without_checkpoint_or_copy(self):
        side=Path(str(self.a)+'-wal');side.write_bytes(b'synthetic-active-wal')
        with self.assertRaises(ValueError):
            with ca.readonly_database(self.a):pass
        self.assertEqual(side.read_bytes(),b'synthetic-active-wal')

    def test_missing_observation_and_mixed_db_environment_rejected(self):
        with self.assertRaises(ValueError):ca.adapt(self.a,self.b,ranking_at='2026-10-27T14:00:00+09:00')
        with closing(sqlite3.connect(self.b)) as db:
            db.execute("UPDATE turnover_runs SET environment='real'")
            db.commit()
        with self.assertRaises(ValueError):ca.adapt(self.a,self.b,ranking_at='2026-10-27T15:00:00+09:00')

    def test_response_evidence_binding(self):
        evidence=dict(environment='mock',host='https://mockapi.kiwoom.com',requests=[dict(api='ka10032',request_started_at='2026-10-27T15:03:28+09:00',response_received_at='2026-10-27T15:03:29+09:00')])
        d=ca.adapt(self.a,self.b,ranking_at='2026-10-27T15:00:00+09:00',http_evidence=evidence)
        self.assertEqual(d['turnover']['response_received_at'],'2026-10-27T15:03:29+09:00')
        evidence['requests'][0]['request_started_at']='wrong'
        with self.assertRaises(ValueError):ca.adapt(self.a,self.b,ranking_at='2026-10-27T15:00:00+09:00',http_evidence=evidence)


if __name__=='__main__':unittest.main()
