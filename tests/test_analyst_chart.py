"""Synthetic completed trading sessions only. No market/API/DB/HTML execution."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from kiwoom_collector.analyst import analyze_stock, from_daily_pages
from kiwoom_collector.daily_tracking_adapter import ClosingEvidence
from kiwoom_collector import daily_chart as daily

ASOF = '2026-10-28T10:00:00+09:00'


def fixture(count=200, *, falling=False, price=None):
    dates=[];day=date(2026,10,27)
    while len(dates)<count:
        if day.weekday()<5:dates.append(day.isoformat())
        day-=timedelta(days=1)
    dates.reverse()
    bars=[]
    for i, day in enumerate(dates):
        close=Decimal(300-i if falling else 100+i)
        bars.append(dict(trade_date=day,open=str(close-1),high=str(close+2),low=str(close-2),close=str(close),
            volume=str(1000+i),status='final',market_at=day+'T15:30:00+09:00',
            finalized_at=day+'T16:00:00+09:00',available_at=day+'T16:01:00+09:00',source='SYNTHETIC',
            basis_id='synthetic-adjusted',price_unit='KRW',volume_unit='share',session_basis='regular_session'))
    return dict(schema_version='analyst-input-v1',stock_code='005930',stock_name='SYNTHETIC',as_of=ASOF,
        environment='mock',market='KRX',data_origin='synthetic_fixture',session_basis='regular_session',
        price_basis=dict(id='synthetic-adjusted',mode='adjusted',verified=True,corporate_actions_checked=True,
            comparable=True,volume_comparable=True,
            effective_at='2026-10-27T16:00:00+09:00',available_at='2026-10-27T17:00:00+09:00',source='SYNTHETIC review'),
        calendar=dict(completed_sessions=dates,as_of=ASOF,verified=True,available_at=ASOF,source='SYNTHETIC calendar'),
        price=dict(value=str(price if price is not None else 305 if not falling else 95),unit='KRW',
            basis_id='synthetic-adjusted',observed_at=ASOF,available_at=ASOF,source='SYNTHETIC price'),
        selection=dict(stock_code='005930',source='SYNTHETIC AM_MOMENTUM',available_at=ASOF,
            reference='synthetic-selection-id',reasons=['top5_repeated']),bars=bars)


class ChartTests(unittest.TestCase):
    def analyze(self, doc):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            result=analyze_stock(doc)
        json.dumps(result,allow_nan=False)
        return result

    def risks(self, report):return {r['code'] for r in report['risks']}

    def test_upward_alignment_and_all_exact_averages(self):
        doc=fixture();before=deepcopy(doc);r=self.analyze(doc)
        self.assertEqual(doc,before)
        for n in (3,5,20,60,200):
            expected=sum(Decimal(b['close']) for b in doc['bars'][-n:])/n
            self.assertEqual(Decimal(r['moving_averages']['MA'+str(n)]['value']),expected)
        self.assertEqual(r['trend_state'],'above')
        self.assertEqual(r['price_comparisons'],dict(MA5='above',MA20='above',MA200='above'))
        self.assertEqual(r['review_status'],'aggressive_review_possible')
        self.assertFalse(r['order_signal']);self.assertIsNone(r['position_size'])
        self.assertEqual(r['extensions']['news_analysis']['status'],'not_analyzed')

    def test_downward_alignment_and_below_ma20_precedence(self):
        r=self.analyze(fixture(falling=True,price=105))
        self.assertEqual(r['trend_state'],'below')
        self.assertEqual(r['price_comparisons']['MA5'],'above')
        self.assertEqual(r['price_zone'],'below_ma20')
        self.assertEqual(r['review_status'],'avoidance_priority')

    def test_ma5_support_equal_boundary(self):
        r=self.analyze(fixture(price=297))
        self.assertEqual(r['price_comparisons']['MA5'],'equal')
        self.assertEqual(r['price_zone'],'above_or_at_ma5')

    def test_break_ma5_while_holding_ma20_and_equal_boundary(self):
        for value in (294,'289.5'):
            r=self.analyze(fixture(price=value))
            self.assertEqual(r['review_status'],'conservative_review')
            self.assertEqual(r['price_zone'],'below_ma5_at_or_above_ma20')

    def test_break_ma20(self):
        r=self.analyze(fixture(price=280))
        self.assertEqual(r['review_status'],'avoidance_priority')

    def test_disparity_is_numeric_without_invented_overheat_threshold(self):
        r=self.analyze(fixture(price=900))
        self.assertEqual(r['overheat_assessment'],'not_rated_no_threshold')
        self.assertGreater(Decimal(r['short_disparity']['MA3']['gap_percent']),100)
        self.assertNotIn('overheated',self.risks(r))
        self.assertEqual(Decimal(r['short_disparity']['MA5']['price_to_ma_percent'])-
                         Decimal(r['short_disparity']['MA5']['gap_percent']),Decimal(100))

    def test_twenty_bar_high_volume_overhead_is_estimate(self):
        doc=fixture(price=305)
        row=doc['bars'][-12]
        row.update(open='330',high='345',low='325',close='338',volume='900000')
        r=self.analyze(doc)
        self.assertTrue(r['supply_proxy']['overhead_resistance_possible'])
        self.assertIn('estimated_overhead_supply',self.risks(r))
        self.assertEqual(r['supply_proxy']['areas'][0]['volume'],'900000')
        self.assertIn('추정치',r['supply_proxy']['notice'])

    def test_short_history_ma200_unavailable_without_padding(self):
        r=self.analyze(fixture(20,price=125))
        self.assertIsNone(r['moving_averages']['MA200']['value'])
        self.assertEqual(r['moving_averages']['MA200']['usable_bars'],20)
        self.assertIsNone(r['price_comparisons']['MA200'])
        self.assertIsNotNone(r['moving_averages']['MA20']['value'])
        r=self.analyze(fixture(2))
        self.assertEqual(r['review_status'],'data_review_required')

    def test_adjustment_and_corporate_action_review_required(self):
        for change in (dict(verified=False),dict(corporate_actions_checked=False),dict(comparable=False),dict(mode='unknown'),
                       dict(effective_at='2026-10-29T00:00:00+09:00')):
            doc=fixture();doc['price_basis'].update(change);r=self.analyze(doc)
            self.assertIsNone(r['moving_averages']['MA20']['value'])
            self.assertEqual(r['review_status'],'data_review_required')
        doc=fixture();doc['price']['basis_id']='unadjusted'
        self.assertIsNone(self.analyze(doc)['price'])

    def test_incomplete_today_is_excluded_from_averages(self):
        doc=fixture();baseline=self.analyze(doc)
        today=dict(doc['bars'][-1],trade_date='2026-10-28',status='incomplete',close='999999')
        doc['bars'].append(today);r=self.analyze(doc)
        self.assertEqual(r['moving_averages'],baseline['moving_averages'])
        self.assertEqual(len(r['evidence']['quarantine']),1)
        self.assertNotIn('999999',json.dumps(r))

    def test_future_bar_receipt_price_and_selection_are_not_exposed(self):
        doc=fixture();doc['bars'][-1]['available_at']='2026-10-29T00:00:00+09:00'
        doc['bars'][-1]['close']='777777'
        doc['price'].update(value='888888',available_at='2026-10-29T00:00:00+09:00')
        doc['selection'].update(available_at='2026-10-29T00:00:00+09:00',reasons=['FUTURE_SECRET_FACT'])
        r=self.analyze(doc)
        self.assertIsNone(r['moving_averages']['MA5']['value'])
        self.assertIsNone(r['selection']);self.assertIsNone(r['price'])
        for value in ('777777','888888','FUTURE_SECRET_FACT'):self.assertNotIn(value,json.dumps(r))

    def test_missing_latest_bar_cannot_be_replaced_with_older_bar(self):
        doc=fixture();doc['bars'].pop(-3);r=self.analyze(doc)
        self.assertIsNone(r['moving_averages']['MA5']['value'])
        self.assertEqual(r['moving_averages']['MA5']['reason'],'missing_or_invalid_bar_in_window')

    def test_duplicate_dates_both_quarantined(self):
        doc=fixture();doc['bars'].append(deepcopy(doc['bars'][-1]));r=self.analyze(doc)
        self.assertEqual(len(r['evidence']['quarantine']),2)
        self.assertIsNone(r['moving_averages']['MA5']['value'])

    def test_sort_by_trading_day_preserving_original_indices(self):
        doc=fixture();baseline=self.analyze(doc)
        doc['bars'].reverse();r=self.analyze(doc)
        self.assertEqual(r['moving_averages'],baseline['moving_averages'])
        self.assertEqual(r['evidence']['bars'][0]['original_index'],199)

    def test_invalid_volume_is_not_reconstructed_and_does_not_erase_close(self):
        for value in ('-1','1.5','4294967295','NaN',None,'0'):
            doc=fixture();doc['bars'][-1]['volume']=value;r=self.analyze(doc)
            self.assertIsNotNone(r['moving_averages']['MA20']['value'])
            self.assertEqual(r['supply_proxy']['status'],'unavailable')
            self.assertIn('volume_quality_incomplete',self.risks(r))

    def test_bad_ohlc_and_calendar_or_symbol_mismatch_held(self):
        doc=fixture();doc['bars'][-1]['high']='1'
        self.assertIsNone(self.analyze(doc)['moving_averages']['MA5']['value'])
        doc=fixture();doc['bars'][-1]['stock_code']='000660'
        self.assertIsNone(self.analyze(doc)['moving_averages']['MA5']['value'])
        doc=fixture();doc['calendar']['verified']=False
        self.assertEqual(self.analyze(doc)['review_status'],'data_review_required')
        for value in (None,[],123):
            doc=fixture();doc['bars'][-1]['trade_date']=value
            self.assertIsNone(self.analyze(doc)['moving_averages']['MA5']['value'])

    def test_future_trade_date_cannot_enter_even_with_old_receipt_time(self):
        doc=fixture();baseline=self.analyze(doc)
        doc['bars'].append(dict(doc['bars'][-1],trade_date='2026-10-29',close='666666'))
        r=self.analyze(doc)
        self.assertEqual(r['moving_averages'],baseline['moving_averages'])
        self.assertNotIn('666666',json.dumps(r))

    def test_volume_adjustment_not_inferred_from_adjusted_price_flag(self):
        doc=fixture();doc['price_basis']['volume_comparable']=False;r=self.analyze(doc)
        self.assertIsNotNone(r['moving_averages']['MA20']['value'])
        self.assertEqual(r['supply_proxy']['status'],'unavailable')
        self.assertIn('volume_adjustment_basis_unverified',self.risks(r))

    def test_missing_price_and_calendar_do_not_produce_trade_guide(self):
        doc=fixture();doc['price']=None
        self.assertEqual(self.analyze(doc)['review_status'],'data_review_required')
        doc=fixture();doc['calendar']=None
        self.assertEqual(self.analyze(doc)['review_status'],'data_review_required')


class SavedPageTests(unittest.TestCase):
    def input(self, adjustment='1'):
        doc=fixture(20,price=125)
        request=daily.DailyRequest('ka11081','005930','mock','20261027',adjustment)
        body=dict(return_code=0,stk_cd='005930',result_list=[dict(dt=b['trade_date'].replace('-',''),
            open_pric='-'+b['open'],high_pric=b['high'],low_pric=b['low'],cur_prc=b['close'],trde_qty=b['volume'])
            for b in reversed(doc['bars'])])
        page=daily.parse_response(body,request=request,response_environment='mock',
            request_started_at='2026-10-27T16:30:00+09:00',response_received_at='2026-10-27T16:30:01+09:00',
            source='SYNTHETIC saved page',cont_yn='N',response_api_id='ka11081')
        proofs=[ClosingEvidence(page.fingerprint,r.fingerprint,'synthetic-context',
            r.trade_date.isoformat()+'T15:30:00+09:00','2026-10-27T17:00:00+09:00',
            'SYNTHETIC finality','regular_session','KRW','share',None,status='final') for r in page.rows]
        args=dict(stock_name=doc['stock_name'],as_of=ASOF,price=doc['price'],selection=doc['selection'],
            calendar=doc['calendar'],price_basis=doc['price_basis'],evidence=proofs,context_id='synthetic-context',
            expected_session_basis='regular_session',data_origin='synthetic_fixture')
        return page,args

    def test_existing_parser_and_bound_evidence_reused_without_tracking_changes(self):
        page,args=self.input();raw=page.raw
        result=analyze_stock(from_daily_pages([page],**args))
        self.assertIsNotNone(result['moving_averages']['MA20']['value'])
        self.assertEqual(page.raw,raw)
        self.assertEqual(result['evidence']['bars'][0]['source_reference']['page_fingerprint'],page.fingerprint)
        self.assertTrue(result['evidence']['bars'][0]['api_raw_values']['open_pric'].startswith('-'))

    def test_tampered_page_rejected_and_missing_proof_held(self):
        page,args=self.input()
        tampered=replace(page,rows=(replace(page.rows[0],close=Decimal(1)),*page.rows[1:]))
        with self.assertRaisesRegex(ValueError,'raw_model'):from_daily_pages([tampered],**args)
        args['evidence']=[]
        r=analyze_stock(from_daily_pages([page],**args))
        self.assertIsNone(r['moving_averages']['MA20']['value'])

    def test_adjustment_request_mismatch_and_future_page_blocked(self):
        page,args=self.input('0');r=analyze_stock(from_daily_pages([page],**args))
        self.assertIsNone(r['moving_averages']['MA20']['value'])
        page,args=self.input();args['as_of']='2026-10-27T16:00:00+09:00'
        r=analyze_stock(from_daily_pages([page],**args))
        self.assertEqual(r['review_status'],'data_review_required')


if __name__=='__main__':unittest.main()
