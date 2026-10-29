"""Offline AM model tests; synthetic fixtures and temporary report roots only."""
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import am_momentum as am

FIXTURE = json.loads((Path(__file__).parent/'fixtures/am_momentum_synthetic.json').read_text(encoding='utf-8'))
NOW = '2026-10-29T10:00:00+09:00'


class AMMomentumTests(unittest.TestCase):
    def setUp(self):
        self.doc = deepcopy(FIXTURE)

    def analyze(self):
        return am.analyze(self.doc, generated_at=NOW)

    def row(self, code='005930'):
        return next(r for r in self.analyze()['rows'] if r['stock_code'] == code)

    def test_three_states_and_unconfirmed(self):
        self.assertEqual(self.row()['candidate_status'], 'momentum_candidate')
        self.assertEqual(self.row('000001')['candidate_status'], 'attention_leader')
        self.assertEqual(self.row('000018')['candidate_status'], 'insufficient_evidence')
        for row in self.analyze()['rows']:
            self.assertIs(row['candidate_confirmed'], False)
            self.assertIs(row['d0_storage_allowed'], False)

    def test_top5_counts_and_rank_change(self):
        r = self.row()
        self.assertEqual(r['top5_appearances'], 4)
        self.assertEqual(r['top20_appearances'], 4)
        self.assertEqual(r['rank_change'], 1)
        self.assertEqual(self.row('000660')['rank_change'], -1)

    def test_sector_deduplication(self):
        r = self.row()
        self.assertEqual(len(r['sectors']), 1)
        self.assertEqual(r['sector_companions'], [dict(sector='Semiconductor', stock_codes=['000660'], count=1)])
        self.assertEqual(r['top5_appearances'], 4)

    def test_future_rankings_do_not_change_candidates(self):
        before = self.analyze()['rows']
        future = deepcopy(self.doc['rankings'][-1])
        future.update(slot='10:00', observed_at='2026-10-29T10:00:00+09:00', received_at='2026-10-29T10:00:01+09:00')
        self.doc['rankings'].append(future)
        self.assertEqual(before, self.analyze()['rows'])

    def test_received_after_cutoff_is_not_known(self):
        self.doc['rankings'][-1]['received_at'] = '2026-10-29T09:50:01+09:00'
        report = self.analyze()
        self.assertEqual(report['completeness']['missing_slots'], ['09:50'])
        self.assertEqual(self.row()['top5_appearances'], 3)
        self.assertEqual(self.row()['candidate_status'], 'attention_leader')

    def test_future_metrics_and_sector_excluded(self):
        before = self.row()
        for kind in ('price','turnover','market_cap','liquidity','sectors'):
            item = deepcopy(self.doc['securities']['005930'][kind][-1])
            item['received_at'] = '2026-10-29T10:00:00+09:00'
            self.doc['securities']['005930'][kind].append(item)
        self.assertEqual(before, self.row())

    def test_missing_price_and_turnover(self):
        self.doc['securities']['005930'].pop('price')
        self.doc['securities']['005930'].pop('turnover')
        r=self.row()
        self.assertEqual(r['candidate_status'], 'attention_leader')
        self.assertIn('price_missing_or_unverified', r['missing_evidence'])
        self.assertIsNone(r['evidence']['turnover']['change'])

    def test_unverified_cap_and_liquidity(self):
        for kind in ('market_cap','liquidity'):
            self.doc['securities']['005930'][kind][0]['verified'] = False
        r=self.row()
        self.assertEqual(r['candidate_status'], 'attention_leader')
        self.assertIn('market_cap_3000_eok_not_verified', r['missing_evidence'])

    def test_missing_observation_time(self):
        self.doc['rankings'][0].pop('observed_at')
        self.doc['securities']['005930']['price'][0].pop('received_at')
        self.assertEqual(self.row()['top5_appearances'], 3)
        self.assertIsNone(self.row()['evidence']['price']['change'])

    def test_mixed_scope_rejected(self):
        for field,value in [('environment','real'),('market','NXT'),('source_kind','live_test')]:
            with self.subTest(field=field):
                self.doc=deepcopy(FIXTURE)
                self.doc['rankings'][0][field]=value
                with self.assertRaises(ValueError):self.analyze()
                self.doc=deepcopy(FIXTURE)
                self.doc['securities']['005930']['price'][0][field]=value
                with self.assertRaises(ValueError):self.analyze()

    def test_duplicate_slot_or_rows_rejected(self):
        self.doc['rankings'].append(deepcopy(self.doc['rankings'][0]))
        with self.assertRaises(ValueError):self.analyze()
        self.doc=deepcopy(FIXTURE)
        self.doc['rankings'][0]['stocks'].append(deepcopy(self.doc['rankings'][0]['stocks'][0]))
        with self.assertRaises(ValueError):self.analyze()

    def test_incomplete_top20_not_silently_complete(self):
        self.doc['rankings'][0]['stocks'].pop()
        self.assertFalse(self.analyze()['completeness']['top20_complete'])
        self.assertEqual(self.row()['candidate_status'], 'attention_leader')

    def test_missing_all_rankings(self):
        self.doc['rankings']=[]
        self.assertEqual(self.analyze()['rows'], [])
        self.assertEqual(len(self.analyze()['completeness']['missing_slots']),4)

    def test_observed_absence_is_not_rank_21(self):
        self.doc['rankings'][-1]['stocks']=[r for r in self.doc['rankings'][-1]['stocks'] if r['stk_cd']!='005930']
        r=self.row()
        self.assertIsNone(r['rank_history'][-1]['rank'])
        self.assertEqual(r['candidate_status'],'insufficient_evidence')

    def test_alphanumeric_codes_preserved(self):
        for s in self.doc['rankings']:s['stocks'][0]['stk_cd']='0053F0'
        self.assertEqual(self.row('0053F0')['top5_appearances'],4)

    def test_sensitive_fields_and_paths_rejected(self):
        for key,value in [('api_key','x'),('metadata',{'account_number':'123'}),('metadata',r'C:\Users\hospital\stock1'),('metadata','Bearer test'),('metadata','https://private.example')]:
            with self.subTest(key=key,value=value):
                self.doc=deepcopy(FIXTURE);self.doc[key]=value
                with self.assertRaises(ValueError):self.analyze()

    def test_html_label_injection_rejected(self):
        self.doc['securities']['005930']['sectors'][0]['name']='</script><script>alert(1)</script>'
        with self.assertRaises(ValueError):self.analyze()

    def test_trends_units_and_no_closing_1000_eok_gate(self):
        r=self.row()
        self.assertEqual(r['evidence']['price']['change'],'500')
        self.assertEqual(r['evidence']['turnover']['change'],'500000000')
        self.assertEqual(r['evidence']['turnover']['observations'][0]['unit'],'KRW')
        self.assertFalse(self.analyze()['policy']['closing_turnover_requirement_applied'])
        self.assertEqual(r['candidate_status'],'momentum_candidate')
        self.doc['securities']['005930']['turnover'][1]['unit']='million_KRW'
        self.assertIsNone(self.row()['evidence']['turnover']['change'])

    def test_html_json_exact_same_result_and_portability(self):
        report=self.analyze()
        with tempfile.TemporaryDirectory() as folder, patch.object(am,'PROJECT',Path(folder)), patch.object(am,'REPORTS',Path(folder)/'reports'):
            paths=am.write_reports(report,'review')
            saved=json.loads(paths[0].read_text(encoding='utf-8'))
            html=paths[1].read_text(encoding='utf-8')
            embedded=json.loads(re.search(r'id="analysis-data">(.*?)</script>',html,re.S).group(1))
            self.assertEqual(report,saved)
            self.assertEqual(saved,embedded)
            self.assertNotIn('C:\\',paths[0].read_text(encoding='utf-8'))
            self.assertEqual(saved['rows'][0]['rank_history'],report['rows'][0]['rank_history'])
            self.assertIn('missing_evidence',html)
            with self.assertRaises(ValueError):am.write_reports(report,'review')
            self.assertEqual(json.loads(paths[0].read_text(encoding='utf-8')),report)

    def test_output_traversal_and_symlink_rejected(self):
        for name in ('../escape','a/b','C:\\report','/tmp/report'):
            with self.assertRaises(ValueError):am.write_reports(self.analyze(),name)
        with tempfile.TemporaryDirectory() as folder, patch.object(am,'PROJECT',Path(folder)), patch.object(am,'REPORTS',Path(folder)/'elsewhere'):
            with self.assertRaises(ValueError):am.write_reports(self.analyze(),'review')

    def test_pure_evaluation_does_not_touch_db_network_or_d0(self):
        from kiwoom_collector import d0_store
        with patch('sqlite3.connect',side_effect=AssertionError('DB forbidden')), patch('socket.socket.connect',side_effect=AssertionError('API forbidden')), patch.object(d0_store,'save_record',side_effect=AssertionError('D0 forbidden')):
            self.assertFalse(self.analyze()['candidate_confirmed'])

    def test_fixture_only_and_time_contract(self):
        self.doc['source_kind']='live_test'
        with self.assertRaises(ValueError):self.analyze()
        self.doc=deepcopy(FIXTURE);self.doc['selection_cutoff_at']='2026-10-29T09:50:00'
        with self.assertRaises(ValueError):self.analyze()
        with self.assertRaises(ValueError):am.analyze(FIXTURE,generated_at='2026-10-29T09:00:00+09:00')

    def test_cli_and_generic_error_does_not_leak_input_path(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(am,'PROJECT',Path(folder)), patch.object(am,'REPORTS',Path(folder)/'reports'), patch('sys.stdout',new_callable=io.StringIO) as output:
            fixture=Path(folder)/'fixture.json';fixture.write_text(json.dumps(FIXTURE),encoding='utf-8')
            self.assertEqual(am.main(['--fixture',str(fixture),'--name','sample']),0)
            self.assertEqual(am.main(['--fixture',str(fixture),'--name','sample']),1)
            self.assertNotIn(folder,output.getvalue())

    def test_input_immutable_and_reproducible(self):
        before=deepcopy(self.doc)
        self.assertEqual(self.analyze(),self.analyze())
        self.assertEqual(before,self.doc)
