"""Pure synthetic comparison; no operational DB or API access."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from kiwoom_collector.cross_analysis import analyze, main, render_table

FIXTURE = Path(__file__).parent/'fixtures/cross_analysis_synthetic.json'


class CrossAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(FIXTURE.read_text(encoding='utf8'))

    def verified(self):
        a,b = self.doc['ranking'],self.doc['turnover']
        self.doc['verification'] = dict(ranking_id=a['id'],turnover_id=b['id'],environment='mock',
            time=dict(verified=True,evidence='SYNTHETIC alignment evidence', ranking_collected_at=a['collected_at'],turnover_collected_at=b['collected_at']),
            unit=dict(verified=True,evidence='SYNTHETIC unit evidence',unit='million_KRW'),
            coverage=dict(verified=True,evidence='SYNTHETIC sample coverage evidence',scope='matched_sample_only'),
            quotes=dict(verified=True,evidence='SYNTHETIC quote evidence'))

    def test_intersection_leading_zero_and_repetition(self):
        data=analyze(self.doc)
        first,second=data['rows'][:2]
        self.assertEqual((first['stock_code'],first['top5_appearances'],second['top5_appearances']),('005930',2,1))
        self.assertEqual(first['turnover_raw'],'1234.56')
        self.assertEqual(first['turnover_won_diagnostic'],'1234560000.00')
        self.assertEqual(first['price_raw'],'+070000')
        self.assertEqual(data['rows'][1]['price_raw'],'-180000')

    def test_verified_evidence_only_prioritizes_review_never_confirms(self):
        self.verified(); data=analyze(self.doc)
        self.assertEqual([r['category'] for r in data['rows'][:2]],['후보 검토','관찰'])
        self.assertTrue(all(not r['candidate_confirmed'] for r in data['rows']))
        self.assertNotIn('score',data)

    def test_same_timestamp_is_not_proof(self):
        self.doc['turnover']['collected_at']=self.doc['ranking']['collected_at']
        row=analyze(self.doc)['rows'][0]
        self.assertEqual(row['time_delta_seconds'],0)
        self.assertIn('time_alignment_unverified',row['reasons'])

    def test_async_time_and_stale_bound_evidence(self):
        self.verified();self.doc['turnover']['collected_at']='2026-10-27T15:00:00+09:00'
        row=analyze(self.doc)['rows'][0]
        self.assertEqual(row['time_delta_seconds'],3600)
        self.assertEqual(row['category'],'검증 보류')

    def test_unit_and_coverage_gates(self):
        for key in ('unit','coverage','quotes'):
            with self.subTest(key=key):
                self.verified();self.doc['verification'][key]['verified']=False
                self.assertEqual(analyze(self.doc)['rows'][0]['category'],'검증 보류')

    def test_wrong_pair_or_environment_evidence_rejected(self):
        for field in ('ranking_id','turnover_id','environment'):
            self.verified();self.doc['verification'][field]='wrong'
            self.assertEqual(analyze(self.doc)['rows'][0]['category'],'검증 보류')

    def test_market_and_filter_mismatch(self):
        for field,value in (('mrkt_tp','001'),('mang_stk_incls','1'),('stex_tp','2')):
            doc=deepcopy(self.doc);doc['turnover']['market'][field]=value
            self.assertIn('market_or_conditions_unverified',analyze(doc)['rows'][0]['reasons'])

    def test_environment_and_test_mode_mixing_blocked(self):
        for field,value in (('environment','real'),('test_mode',False)):
            doc=deepcopy(self.doc);doc['turnover'][field]=value
            with self.assertRaises(ValueError):analyze(doc)

    def test_invalid_code_is_not_padded_or_name_matched(self):
        for code in ('5930',5930,'005930_NX'):
            doc=deepcopy(self.doc);doc['turnover']['rows'][0]['stk_cd']=code
            data=analyze(doc)
            self.assertIsNone(data['rows'][0]['turnover_raw'])
            self.assertTrue(data['diagnostics']['turnover']['invalid_rows'])

    def test_duplicates_never_arbitrarily_select_or_inflate_counts(self):
        for source in ('ranking','turnover'):
            doc=deepcopy(self.doc);doc[source]['rows'].append(deepcopy(doc[source]['rows'][0]))
            data=analyze(doc)
            self.assertEqual(data['diagnostics'][source]['duplicate_codes'],['005930'])
            self.assertNotIn('005930',[r['stock_code'] for r in data['rows'] if r['turnover_raw'] is not None])

    def test_duplicate_ranks_are_ambiguous(self):
        self.doc['turnover']['rows'][1]['now_rank']='1'
        self.assertTrue(analyze(self.doc)['diagnostics']['turnover']['invalid_rows'])

    def test_empty_and_partial_responses(self):
        self.verified();self.doc['turnover']['rows']=[];self.doc['turnover']['status']='empty'
        data=analyze(self.doc)
        self.assertIn('turnover',data['diagnostics']['empty_sources'])
        self.assertTrue(all(r['category']=='검증 보류' for r in data['rows']))
        self.doc['ranking']['rows']=[]
        self.assertEqual(analyze(self.doc)['rows'],[])

    def test_unclassified_etf_and_turnover_only(self):
        self.verified();data=analyze(self.doc)
        self.assertEqual(data['rows'][2]['security_type'],'ETF')
        self.assertEqual(data['rows'][2]['category'],'검증 보류')
        self.assertEqual(data['diagnostics']['unclassified_codes'],['000001'])
        self.doc['ranking']['rows']=self.doc['ranking']['rows'][1:]
        self.assertIn('005930',analyze(self.doc)['diagnostics']['turnover_only_codes'])

    def test_future_other_day_and_duplicate_history_not_counted(self):
        history=self.doc['history']
        history.append(deepcopy(history[0]))
        for i,t in enumerate(('2026-10-27T15:00:00+09:00','2026-10-26T13:00:00+09:00')):
            item=deepcopy(history[0]);item.update(id='bad'+str(i),collected_at=t);history.append(item)
        result=analyze(self.doc)
        self.assertEqual(result['rows'][0]['top5_appearances'],2)
        self.assertEqual(len(result['diagnostics']['history_rejected']),3)

    def test_malformed_numeric_values_not_zero_filled(self):
        for key in ('cur_prc','trde_prica','flu_rt','now_trde_qty'):
            self.verified();self.doc['turnover']['rows'][0][key]='invalid'
            self.assertIn('invalid_market_values',analyze(self.doc)['rows'][0]['reasons'])

    def test_no_mutation_io_or_network_and_safe_table(self):
        before=deepcopy(self.doc)
        with patch('builtins.open',side_effect=AssertionError('I/O forbidden')),patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            result=analyze(self.doc)
        self.assertEqual(self.doc,before)
        self.doc['ranking']['rows'][0]['stk_nm']='a|b\nnext'
        self.assertIn('a\\|b next',render_table(analyze(self.doc)))
        self.assertIn('005930',render_table(result))

    def test_cli_rejects_db_before_open(self):
        with patch.object(Path,'read_text',side_effect=AssertionError('File read forbidden')):
            with self.assertRaises(SystemExit):main(['--fixture','data/stock1.db'])


if __name__=='__main__':unittest.main()
