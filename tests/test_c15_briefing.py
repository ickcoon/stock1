"""Synthetic display/product/capital evidence checks, with no transmission."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from kiwoom_collector import c15_briefing as briefing, c15_briefing_data as data
from test_c15_flow import saved
from test_c15_turnover_audit import CONTRACT as TURNOVER

ROOT=Path(__file__).resolve().parents[1]
DISPLAY=json.loads((ROOT/'config/c15-display-contract.json').read_text(encoding='utf8'))
AT='2026-11-05T09:40:00+09:00'


def reference(code='000001',kind='common_stock',amount='500000000000'):
    return dict(stock_code=code,product_type=kind,market='KOSPI',basis_date='2026-11-03',
        official_name='Fixture',available_at='2026-11-04T14:59:12+09:00',master_body_hash='synthetic',
        name_basis='checked_KRX_daily_name_with_dated_official_master_join',
        daily_capital=dict(basis_date='2026-11-03',snapshot_id='synthetic',collected_at='2026-11-04T14:59:08+09:00',
            raw_market_cap=amount,raw_close='10000',raw_listed_shares=str(int(amount)//10000),
            raw_fields_match=True,stored_market_cap_won=None))


def source(definitions=None):
    definitions=definitions or [('000001','2',1,'A'),('000002','3',2,'A')]
    r=saved('09:40',definitions)
    r['paths']=dict(turnover='synthetic-not-read.db',eligibility_master=None)
    r['auxiliary']=dict(analysis_adr_db=dict(rows=[['2026-11-04','COMBINED','64.24','provisional']]))
    for s in r['morning_leadership']['stocks']:s['product_reference']=reference(s['stock_code'])
    return r


class EvidenceTests(unittest.TestCase):
    def cap(self,r,**updates):
        return data.capital_reference(r,stock_code='000001',as_of=AT,contract=DISPLAY,
            minimum_won='300000000000',**updates)

    def test_cap_exact_raw_arithmetic_and_explicit_prior_date(self):
        cap=self.cap(reference())
        self.assertEqual(cap['won'],'500000000000');self.assertEqual(cap['basis_date'],'2026-11-03')
        self.assertFalse(cap['api_unit_explicitly_documented']);self.assertFalse(cap['intraday_cap_verified'])
        self.assertEqual(data.format_capital(cap['won']),'시총 5,000억')
        self.assertEqual(data.format_capital('5290939283000'),'시총 5.29조')

    def test_future_receipt_or_same_day_close_not_used(self):
        for key in ('available_at','daily_receipt','same_day'):
            r=reference()
            if key=='available_at':r[key]='2026-11-05T12:00:00+09:00'
            elif key=='daily_receipt':r['daily_capital']['collected_at']='2026-11-05T12:00:00+09:00'
            else:r['basis_date']=r['daily_capital']['basis_date']='2026-11-05'
            self.assertIsNone(self.cap(r)['won'])

    def test_cap_wrong_code_raw_mismatch_or_missing_units_stay_unknown(self):
        for key in ('code','raw_match','arithmetic','missing'):
            r=reference()
            if key=='code':r['stock_code']='000002'
            elif key=='raw_match':r['daily_capital']['raw_fields_match']=False
            elif key=='arithmetic':r['daily_capital']['raw_listed_shares']='1'
            else:r['daily_capital']['raw_close']=None
            self.assertIsNone(self.cap(r)['won'])
        self.assertIsNone(data.capital_reference(reference(),stock_code='000001',as_of=AT,
            contract=dict(DISPLAY,capital_currency='USD'),minimum_won='300000000000')['won'])

    def test_cap_floor_boundary_and_existing_bands_are_not_new_weights(self):
        for value,state in [('299999990000','below_or_equal_minimum_reference'),('300000000000','below_or_equal_minimum_reference'),('300000010000','above_minimum_reference')]:
            self.assertEqual(self.cap(reference(amount=value))['eligibility_status'],state)
        for value,band in [('5000000000000','primary_exploration'),('10000000000000','extended_exploration'),('10000000010000','mega_cap_leader')]:
            r=self.cap(reference(amount=value));self.assertEqual(r['capital_band']['band'],band)
            self.assertFalse(r['capital_band']['upper_cap_exclusion'])

    def test_verified_code_types_can_exclude_all_four_products(self):
        stock=dict(stock_code='000001',name='Ordinary name')
        for kind in ('ETF','ETN','REIT','SPAC'):
            result=data.product_evidence(stock,reference(kind=kind),as_of=AT)
            self.assertTrue(result['excluded']);self.assertTrue(result['verified'])

    def test_name_patterns_never_exclude_unknown_types(self):
        for name in ('KODEX 200','TIGER ETF','Some ETN','회사스팩','회사리츠'):
            r=data.product_evidence(dict(stock_code='000001',name=name),{},as_of=AT)
            self.assertTrue(r['name_review_only']);self.assertFalse(r['excluded']);self.assertFalse(r['verified'])

    def test_future_or_conflicting_code_product_evidence_not_promoted(self):
        for r in (reference('000002',kind='ETF'),dict(reference(kind='ETF'),available_at='2026-11-05T10:00:00+09:00')):
            self.assertFalse(data.product_evidence(dict(stock_code='000001',name='KODEX'),r,as_of=AT)['excluded'])

    def test_unknown_is_not_verified_merely_because_identity_is_known(self):
        r=data.product_evidence(dict(stock_code='000001',name='Fixture'),reference(kind='UNKNOWN'),as_of=AT)
        self.assertTrue(r['identity_verified']);self.assertFalse(r['verified']);self.assertEqual(r['status'],'pending')

    def test_existing_verified_kiwoom_decision_reused_but_provisional_is_not(self):
        from kiwoom_collector.equity_eligibility import CANDIDATE_VERSION
        from kiwoom_collector.master_data import digest
        raw=dict(code='000001',name='Fixture')
        prior=dict(state='confirmed_excluded',confidence='verified',rule_version=CANDIDATE_VERSION,
            raw_sha256=digest(raw),reasons=['excluded_type:ETF'],evidence=dict(source='ka10099',run_id='synthetic',
                updated_at='2026-11-03T10:00:00+09:00',applied_at='2026-11-03T10:00:01+09:00',raw=raw))
        s=dict(stock_code='000001',name='Fixture')
        self.assertTrue(data.product_evidence(s,{},as_of=AT,existing_decision=prior)['excluded'])
        self.assertFalse(data.product_evidence(s,{},as_of=AT,existing_decision=dict(prior,state='provisional_excluded'))['excluded'])
        self.assertEqual(data.product_evidence(s,reference(),as_of=AT,existing_decision=prior)['status'],'conflict_pending')


class BriefingTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))
        self.enterContext(patch.object(briefing.c15_turnover_audit,'audit',return_value=dict(status='missing',rows=[],storage_consistent=False)))

    def build(self,r=None,**kwargs):return briefing.prepare(r or source(),DISPLAY,TURNOVER,**kwargs)

    def test_compact_text_and_diagnostics_split_with_input_immutability(self):
        r=source();before=deepcopy(r);result=self.build(r)
        self.assertEqual(r,before)
        self.assertIn('시총 5,000억',result['text']);self.assertIn('거래대금 미확인',result['text'])
        self.assertIn('잠정 관측 후보',result['text']);self.assertIn('합성 테스트',result['text'])
        self.assertNotIn('synthetic-not-read.db',result['text']);self.assertNotIn('점수 구성',result['text'])
        self.assertIn('stock_measurements',result['diagnostics']);self.assertFalse(result['telegram_sent'])

    def test_unknown_capital_stays_pending_and_is_printed_missing(self):
        r=source()
        for s in r['morning_leadership']['stocks']:s['product_reference'].pop('daily_capital')
        result=self.build(r)
        self.assertIn('시총 미확인',result['text'])
        self.assertTrue(all(c['eligibility_status']=='pending' for t in result['analysis']['sectors'] for c in t['leaders']))

    def test_low_cap_is_filtered_by_existing_engine_not_just_hidden_in_text(self):
        r=source();r['morning_leadership']['stocks'][0]['product_reference']=reference(amount='200000000000')
        result=self.build(r)
        self.assertEqual(result['analysis']['sectors'],[])
        self.assertEqual(result['diagnostics']['comparison']['after_capital']['below_capital_minimum_held'],1)

    def test_product_filter_comparison_and_no_fuzzy_type_guess(self):
        r=source([(f'{i:06}','3',i,'A') for i in range(1,7)])
        kinds=['ETF','ETN','REIT','SPAC','common_stock','common_stock']
        records=[reference(f'{i:06}',kind) for i,kind in enumerate(kinds,1)]
        result=self.build(r,official_products=records)
        self.assertEqual(result['diagnostics']['comparison']['after_product']['excluded_products'],4)
        self.assertEqual(result['analysis']['sectors'][0]['rising_count'],2)
        self.assertEqual(len(result['analysis']['sectors'][0]['leaders']),2)
        self.assertEqual(result['analysis']['policy'],r['morning_leadership']['analysis']['policy'])

    def test_unknown_type_held_instead_of_name_based_exclusion_or_promotion(self):
        r=source();s=r['morning_leadership']['stocks'][0];s['name']='KODEX Example';s['product_reference']={};s['product_status']='UNKNOWN'
        result=self.build(r)
        self.assertEqual(result['diagnostics']['comparison']['after_product']['excluded_products'],0)
        self.assertEqual(result['diagnostics']['stock_measurements'][s['stock_code']]['product']['status'],'pending')
        self.assertEqual(result['analysis']['sectors'],[])

    def test_amount_format_uses_existing_documented_conversion_and_actual_clock(self):
        fake=dict(storage_consistent=True,rows=[dict(stock_code='000001',raw_value='306023',documented_eok='3060.23',documented_won='306023000000')])
        with patch.object(briefing.c15_turnover_audit,'audit',return_value=fake):result=self.build()
        self.assertIn('대금 3,060.23억',result['text'])
        measure=result['diagnostics']['stock_measurements']['000001']['turnover']
        self.assertEqual(measure['request_started_at'][11:16],'09:40');self.assertFalse(measure['cumulative_period_verified'])
        self.assertIn('누적 기준 미검증',result['text']);self.assertFalse(measure['amount_score_enabled'])

    def test_existing_exclusion_conflict_remains_held_not_automatically_reincluded(self):
        r=source();r['morning_leadership']['stocks'][0]['explicitly_excluded']=True
        result=self.build(r)
        self.assertEqual(result['analysis']['sectors'],[])
        self.assertEqual(result['diagnostics']['stock_measurements']['000001']['product']['status'],'conflict_pending')

    def test_output_path_rejected_before_any_source_reads(self):
        with patch.object(briefing.preview,'generate',side_effect=AssertionError('must not read')):
            with self.assertRaises(ValueError):briefing.main(['--date','2026-11-05','--output-dir',str(ROOT/'outside-diagnostics')])


if __name__=='__main__':unittest.main()
