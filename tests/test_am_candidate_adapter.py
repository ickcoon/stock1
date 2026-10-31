"""C12-I synthetic 0198/0186 joins and AM selection integration."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import unittest
import sqlite3
from unittest.mock import patch

from kiwoom_collector import am_candidate_adapter as a, am_briefing as b
from kiwoom_collector import am_input_adapter as input_adapter
from test_equity_candidates import sample
import test_am_input_adapter as adapter_fixtures

AT = '2026-10-29T09:50:00+09:00'
NOW = '2026-10-29T10:00:00+09:00'
FIXTURE = Path(__file__).parent/'fixtures/am_sector_briefing_synthetic.json'


def master():
    rows = []
    for n in range(1, 21):
        row = sample(f'{n:06}')
        row['product_source'].update(updated_at='2026-10-28T16:00:00+09:00', applied_at='2026-10-28T16:01:00+09:00')
        rows.append(row)
    for n in (0, 1):
        rows[n].update(market='UNKNOWN', market_code='8')
        rows[n]['product_source']['raw'].update(marketCode='8', marketName='ETF', orderWarning='1' if n == 0 else '0')
    rows[2]['product_source']['raw']['companyClassName'] = '스팩'
    return dict(at=AT, synthetic=True, provenance=dict(sha256='synthetic-hash', hash_unchanged=True),
                records=[r for r in rows if r['stock_code'] != '000004'])


class AMCandidateTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.document['selection_cutoff_at'] = AT

    def test_normalization_preserves_krx_scope(self):
        for value in (1, '1', ' 000001 '):
            self.assertEqual(a.normalize_code(value), '000001')
        self.assertEqual(a.normalize_code('0035s0'), '0035S0')
        for value in (True, 1.0, None, '000001_NX', 'A000001', '1234567'):
            self.assertIsNone(a.normalize_code(value))

    def test_partition_diagnostics_original_values_and_missing_turnover_only(self):
        original = deepcopy(self.document)
        source = [dict(kind='turnover', stock_code='21', now_rank='1', trde_prica='0.001',
                       market_cap='300000000001'), dict(kind='turnover', stock_code=None, trde_prica='7')]
        joined = a.join(self.document, master(), source_records=source)
        f = joined['candidate_filter']
        self.assertEqual(f['counts'], dict(analysis_candidate=16, confirmed_excluded=1,
            provisional_excluded=1, pending=1, unmatched=2))
        self.assertEqual(f['by_source']['ranking'], dict(before_count=20, after_count=16))
        self.assertEqual(f['by_source']['turnover'], dict(before_count=1, after_count=0))
        self.assertEqual(f['invalid_code_rows'], 1)
        self.assertEqual(f['observations'][-2]['original'], source[0])
        self.assertEqual(joined['securities'], original['securities'])
        self.assertEqual(joined['rankings'], original['rankings'])
        self.assertEqual(self.document, original)

    def test_am_excludes_products_from_leaders_and_companions_without_rank_rewrite(self):
        joined = a.join(self.document, master())
        report = b.analyze(joined, generated_at=NOW)
        self.assertEqual(len(report['candidates']), 16)
        self.assertTrue(report['completeness']['top20_complete'])
        blocked = {'000001', '000002', '000003', '000004'}
        self.assertFalse(blocked & {r['stock_code'] for r in report['candidates']})
        for row in report['analysis']['rows']:
            if row['stock_code'] in blocked:
                self.assertEqual(row['candidate_status'], 'insufficient_evidence')
                self.assertIn('product_candidate_filter_blocked', row['missing_evidence'])
            for peer in row['sector_companions']:
                self.assertFalse(blocked & set(peer['stock_codes']))
        for sector in report['sectors']:
            self.assertFalse(blocked & {x['stock_code'] for x in sector['leaders']})
        self.assertEqual(len(report['analysis']['rows']), 20)
        self.assertEqual(report['input_document']['securities'], self.document['securities'])
        b.validate(report)

    def test_no_absolute_am_turnover_floor(self):
        for sec in self.document['securities'].values():
            for metric in sec.get('turnover', []):
                metric['value'] = '1'
        report = b.analyze(a.join(self.document, master()), generated_at=NOW)
        self.assertIsNone(report['policy']['absolute_am_turnover_min_won'])
        self.assertTrue(any(r['status'] == 'priority' for r in report['candidates']))

    def test_scope_and_normalization_collision_reject(self):
        m = master()
        m['at'] = NOW
        with self.assertRaisesRegex(ValueError, 'cutoff_mismatch'):
            a.join(self.document, m)
        m = master(); m['synthetic'] = False
        with self.assertRaisesRegex(ValueError, 'source_scope_mismatch'):
            a.join(self.document, m)
        self.document['rankings'][0]['stocks'][1]['stk_cd'] = 1
        with self.assertRaisesRegex(ValueError, 'ambiguous_ranking_code'):
            a.join(self.document, master())

    def test_readonly_input_adapter_connects_to_master_preview(self):
        fixture = adapter_fixtures.AMInputAdapterTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        before = fixture.db.read_bytes()
        with patch.object(a, 'preview', return_value=master()) as preview:
            with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
                document = fixture.adapt(master_db='synthetic-master.db', candidate_filter=True)
        preview.assert_called_once_with('synthetic-master.db', at=AT, synthetic=True)
        self.assertEqual(fixture.db.read_bytes(), before)
        self.assertEqual(document['candidate_filter']['by_source']['ranking']['after_count'], 16)
        self.assertEqual(len(b.analyze(document, generated_at=NOW)['candidates']), 16)

    def test_0186_metrics_and_stored_rank_survive_candidate_filter(self):
        from kiwoom_collector.turnover_rank import SCHEMA
        fixture = adapter_fixtures.AMInputAdapterTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        path = fixture.root/'turnover.db'
        with closing(sqlite3.connect(fixture.db)) as db, db:
            db.execute('DELETE FROM quote_observations WHERE request_finished_at=?', (AT,))
        fixture.manifest = fixture.make_manifest()
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(SCHEMA)
            db.execute('''INSERT INTO turnover_runs(id,api_id,environment,source_kind,test_mode,spec_version,
                request_json,profile_hash,created_at,status,issues_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                ('turn','ka10032','mock','fixture',1,'synthetic',json.dumps(dict(stex_tp='1')),'synthetic',AT,'success','[]'))
            db.execute('''INSERT INTO turnover_pages(run_id,page_no,started_at,finished_at,http_status,return_code,
                status,row_count,issues_json,response_json) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                ('turn',1,AT,AT,200,0,'success',9,'[]','{}'))
            for index, (code, sec) in enumerate(self.document['securities'].items(), 1):
                raw = dict(stk_cd=code, trde_prica='0.001', flu_rt='1', cur_prc='100')
                db.execute('INSERT INTO turnover_rows VALUES(?,?,?,?,?,?,?,?,?)',
                    ('turn',1,index,code,index,'syntax_valid',json.dumps(raw),'{}','[]'))
        source = input_adapter.read_sources(path, role='turnover')
        template = deepcopy(fixture.proofs('quote')[0][1])
        for row in source['records']:
            fixture.manifest['records'][input_adapter.fingerprint(row)] = dict(template,
                source_sha256=source['sha256'], observed_at=AT, received_at=AT)
        before = path.read_bytes()
        with patch.object(a, 'preview', return_value=master()):
            doc = fixture.adapt(master_db='synthetic.db', candidate_filter=True, turnover_db=path)
        self.assertEqual(path.read_bytes(), before)
        report = b.analyze(doc, generated_at=NOW)
        f = report['candidate_filter']
        self.assertEqual(f['by_source']['turnover'], dict(before_count=9, after_count=5))
        rows = [r for r in f['observations'] if r['kind'] == 'turnover']
        self.assertEqual(rows[0]['original']['stored_turnover_rank'], 1)
        self.assertEqual(rows[0]['original']['trde_prica'], '0.001')
        self.assertEqual(next(r for r in report['candidates'] if r['stock_code']=='000005')['metrics']['turnover']['value'], '1000.000')

    def test_adapter_normalizes_ranking_codes_after_manifest_binding(self):
        fixture = adapter_fixtures.AMInputAdapterTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with closing(sqlite3.connect(fixture.db)) as db, db:
            db.execute("UPDATE ranking_records SET stk_cd='5' WHERE stk_cd='000005'")
        fixture.manifest = fixture.make_manifest()
        with patch.object(a, 'preview', return_value=master()):
            doc = fixture.adapt(master_db='synthetic.db', candidate_filter=True)
        self.assertTrue(any(s['stk_cd']=='000005' for s in doc['rankings'][0]['stocks']))
        self.assertEqual(doc['candidate_filter']['original_ranking_sources'][0]['stocks'][4]['stk_cd'], '5')


if __name__ == '__main__':
    unittest.main()
