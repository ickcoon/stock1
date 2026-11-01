"""Synthetic weekly/daily separation and completion/basis boundaries; no HTTP."""
from copy import deepcopy
from dataclasses import replace
from datetime import date,timedelta
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from kiwoom_collector import weekly_chart as w,daily_chart as d
from kiwoom_collector.analyst.hybrid import analyze_hybrid,WeeklyEvidence
from kiwoom_collector.analyst.context import analyze_context
from kiwoom_collector.daily_supply_cli import ReplayTransport,identity
from test_analyst_chart import fixture,ASOF


def weekly_fixture(count=52,*,daily_count=253,current=True):
    doc=fixture(daily_count)
    start=date(2026,10,26)
    periods=[dict(start=(start-timedelta(weeks=n)).isoformat(),end=(start-timedelta(weeks=n)+timedelta(days=4)).isoformat(),
                  bar_date=(start-timedelta(weeks=n)).isoformat()) for n in range(count,0,-1)]
    rows=[dict(dt=p['bar_date'].replace('-',''),open_pric=str(100+i),high_pric=str(102+i),
        low_pric=str(99+i),cur_prc=str(101+i),trde_qty='1000',trde_prica='100') for i,p in enumerate(periods)]
    if current:rows.append(dict(rows[-1],dt='20261026',high_pric='999999'))
    body=dict(stk_cd='005930',return_code=0,stk_stk_pole_chart_qry=list(reversed(rows)))
    request=w.WeeklyRequest('ka10082','005930','mock','20261027','1')
    page=w.parse_response(body,request=request,response_environment='mock',
        request_started_at='2026-10-27T16:30:00+09:00',response_received_at='2026-10-27T16:30:01+09:00',
        source='SYNTHETIC weekly',cont_yn='N',response_api_id='ka10082')
    reference=dict(start='2026-10-26',end='2026-10-30')
    calendar=dict(verified=True,source='SYNTHETIC week calendar',available_at=ASOF,as_of=ASOF,
                  reference_week=reference,completed_weeks=periods)
    basis=dict(doc['price_basis'],session_basis=doc['session_basis'])
    proofs=[]
    indexed={p['bar_date']:p for p in periods}
    for row in page.rows:
        p=indexed.get(row.trade_date.isoformat(),reference)
        proofs.append(WeeklyEvidence(page.fingerprint,row.fingerprint,p['start'],p['end'],
            '2026-10-27T17:00:00+09:00','2026-10-27T17:00:00+09:00','SYNTHETIC weekly review',
            basis['id'],doc['session_basis'],status='incomplete' if row.trade_date>=start else 'final',price_unit='KRW'))
    args=dict(weekly_data_origin='synthetic_fixture',weekly_price_basis=basis,weekly_calendar=calendar,evidence=proofs)
    return doc,page,args


class HybridTests(unittest.TestCase):
    def setUp(self):self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('no HTTP')))
    def run_hybrid(self,doc,pages,args):
        before=deepcopy(doc);r=analyze_hybrid(doc,pages,**args)
        self.assertEqual(doc,before);json.dumps(r,allow_nan=False);self.assertFalse(r['order_signal'])
        return r

    def test_complete_52_weeks_exclude_ongoing_and_preserve_daily_exactly(self):
        doc,page,args=weekly_fixture();r=self.run_hybrid(doc,[page],args)
        self.assertEqual(r['daily'],analyze_context(doc))
        self.assertEqual(r['primary_high52']['prior_high'],'153')
        self.assertTrue(r['primary_high52']['close_breakout_confirmed'])
        self.assertEqual(r['high52_source'],'weekly')
        self.assertNotIn('999999',json.dumps(r))
        self.assertIn('incomplete_week',r['weekly']['quarantine'][0]['reasons'])
        self.assertEqual(len(r['weekly']['selected_periods']),52)

    def test_short_daily_history_can_use_weekly_high_without_faking_ma200(self):
        doc,page,args=weekly_fixture(daily_count=60);r=self.run_hybrid(doc,[page],args)
        self.assertEqual(r['primary_high52']['status'],'available')
        self.assertIsNone(r['daily']['moving_averages']['MA200']['value'])
        self.assertIsNotNone(r['daily']['moving_averages']['MA60']['value'])
        self.assertEqual(r['daily']['high52']['status'],'unavailable')
        self.assertEqual(r['daily']['supply_proxy'],analyze_context(doc)['supply_proxy'])

    def test_more_than_52_uses_only_fixed_latest_calendar_window(self):
        doc,page,args=weekly_fixture(54,current=False)
        body=page.raw;body[w.LIST_KEY][-1]['high_pric']='999999'
        page=w.parse_response(body,request=page.request,response_environment='mock',request_started_at=page.request_started_at,
            response_received_at=page.response_received_at,source=page.source,cont_yn='N',response_api_id='ka10082')
        # Rebind synthetic proofs after the deliberately modified raw response.
        args['evidence']=[replace(p,page_fingerprint=page.fingerprint,row_fingerprint=row.fingerprint)
                          for p,row in zip(args['evidence'],page.rows)]
        r=self.run_hybrid(doc,[page],args)
        self.assertEqual(r['primary_high52']['prior_high'],'155')
        self.assertEqual(len(r['weekly']['selected_periods']),52)

    def test_missing_week_is_not_replaced_by_older_raw_bar(self):
        doc,page,args=weekly_fixture(53,current=False)
        args['evidence'].pop(10)
        r=self.run_hybrid(doc,[page],args)
        self.assertEqual(r['primary_high52']['reason'],'missing_or_invalid_completed_week')
        self.assertIsNone(r['primary_high52']['prior_high'])

    def test_51_week_calendar_and_calendar_gap_are_unavailable(self):
        doc,page,args=weekly_fixture(51);r=self.run_hybrid(doc,[page],args)
        self.assertEqual(r['primary_high52']['reason'],'insufficient_completed_weeks')
        doc,page,args=weekly_fixture(53)
        args['weekly_calendar']['completed_weeks'].pop(-2)
        r=self.run_hybrid(doc,[page],args)
        self.assertEqual(r['primary_high52']['reason'],'missing_completed_calendar_week')

    def test_missing_basis_mismatch_adjustment_session_and_origin_keep_daily(self):
        for change in ({'id':'wrong'},{'mode':'original'},{'verified':False},
                       {'effective_at':'2026-10-26T16:00:00+09:00'},{'session_basis':'different'}):
            doc,page,args=weekly_fixture();args['weekly_price_basis'].update(change)
            with self.subTest(change=change):
                r=self.run_hybrid(doc,[page],args)
                self.assertEqual(r['primary_high52']['status'],'unavailable')
                self.assertEqual(r['daily'],analyze_context(doc))
        doc,page,args=weekly_fixture();args['weekly_data_origin']='api_response'
        self.assertEqual(self.run_hybrid(doc,[page],args)['primary_high52']['reason'],'weekly_daily_scope_mismatch')

    def test_future_receipt_or_review_never_enters_weekly_high(self):
        for kind in ('review','receipt'):
            doc,page,args=weekly_fixture()
            if kind=='review':args['evidence'][5]=replace(args['evidence'][5],available_at='2099-01-01T00:00:00+09:00')
            else:
                page=w.parse_response(page.raw,request=page.request,response_environment='mock',
                    request_started_at='2099-01-01T00:00:00+09:00',response_received_at='2099-01-01T00:00:01+09:00',
                    source=page.source,cont_yn='N',response_api_id='ka10082')
            self.assertEqual(self.run_hybrid(doc,[page],args)['primary_high52']['status'],'unavailable')

    def test_duplicate_pages_proofs_and_tampered_model_are_rejected(self):
        doc,page,args=weekly_fixture()
        self.assertEqual(self.run_hybrid(doc,[page,page],args)['primary_high52']['status'],'unavailable')
        args['evidence'].append(args['evidence'][5])
        self.assertEqual(self.run_hybrid(doc,[page],args)['primary_high52']['status'],'unavailable')
        bad=replace(page,rows=(replace(page.rows[1],high=Decimal(10000)),*page.rows[1:]))
        with self.assertRaisesRegex(ValueError,'raw_model'):analyze_hybrid(doc,[bad],**args)

    def test_equality_is_retest_not_breakout(self):
        doc,page,args=weekly_fixture()
        doc['bars'][-1].update(open='153',high='154',low='152',close='153')
        r=self.run_hybrid(doc,[page],args)
        self.assertTrue(r['primary_high52']['high_retest'])
        self.assertFalse(r['primary_high52']['close_breakout_confirmed'])
        self.assertEqual(Decimal(r['primary_high52']['distance_below_high_percent']),0)

    def test_units_array_code_environment_api_checks(self):
        doc,page,args=weekly_fixture()
        self.assertEqual(page.units['turnover'],'KRW')
        self.assertEqual(page.rows[0].turnover_raw,page.rows[0].turnover_won)
        self.assertEqual(page.request.api_id,'ka10082')
        with self.assertRaises(ValueError):w.WeeklyRequest('ka10081','005930','mock','20261027','1')
        for body,env,api in ((dict(page.raw,stk_cd='000660'),'mock','ka10082'),
                             (page.raw,'real','ka10082'),(page.raw,'mock','ka10081')):
            with self.subTest(env=env,api=api),self.assertRaises(ValueError):
                w.parse_response(body,request=page.request,response_environment=env,
                    request_started_at=page.request_started_at,response_received_at=page.response_received_at,
                    source=page.source,response_api_id=api)
        self.assertNotIn('ka10082',d.LIST_KEYS)

    def test_missing_evidence_and_completed_calendar_do_not_become_weekly_high(self):
        doc,page,args=weekly_fixture();args['evidence']=[]
        self.assertEqual(self.run_hybrid(doc,[page],args)['primary_high52']['status'],'unavailable')
        args['weekly_calendar']['verified']=False
        self.assertEqual(self.run_hybrid(doc,[page],args)['primary_high52']['reason'],'weekly_calendar_unverified')

    def test_bad_weekly_price_or_zero_volume_keeps_primary_high_unavailable(self):
        for change in (dict(high_pric='0'),dict(trde_qty='0')):
            doc,page,args=weekly_fixture();body=page.raw;body[w.LIST_KEY][5].update(change)
            page=w.parse_response(body,request=page.request,response_environment='mock',
                request_started_at=page.request_started_at,response_received_at=page.response_received_at,
                source=page.source,cont_yn='N',response_api_id='ka10082')
            args['evidence']=[replace(p,page_fingerprint=page.fingerprint,row_fingerprint=row.fingerprint)
                              for p,row in zip(args['evidence'],page.rows)]
            with self.subTest(change=change):
                r=self.run_hybrid(doc,[page],args)
                self.assertEqual(r['primary_high52']['status'],'unavailable')
                self.assertEqual(r['daily']['high52']['status'],'available')

    def test_finality_proof_price_unit_session_and_period_are_not_assumed(self):
        for change in (dict(price_unit=None),dict(session_basis='different'),dict(status='unverified'),
                       dict(period_end='2020-01-01'),dict(source='')):
            doc,page,args=weekly_fixture();args['evidence'][5]=replace(args['evidence'][5],**change)
            with self.subTest(change=change):
                self.assertEqual(self.run_hybrid(doc,[page],args)['primary_high52']['status'],'unavailable')


class WeeklyPagingTests(unittest.TestCase):
    def test_reuses_supply_replay_continuation_and_stops_with_53_unverified_bars(self):
        _,page,_=weekly_fixture();rows=page.raw[w.LIST_KEY];responses=[]
        for i,chunk in enumerate((rows[:20],rows[20:])):
            responses.append(dict(request=identity(page.request),continuation={} if i==0 else {'cont-yn':'Y','next-key':'next'},
                reply=dict(body=dict(stk_cd='005930',return_code=0,stk_stk_pole_chart_qry=chunk),http_status=200,
                    headers={'api-id':'ka10082','cont-yn':'Y' if i==0 else 'N',**({'next-key':'next'} if i==0 else {})},
                    request_started_at=page.request_started_at.isoformat(),response_received_at=page.response_received_at.isoformat())))
        transport=ReplayTransport(dict(schema_version='daily-supply-replay-v1',responses=responses))
        result=w.fetch_pages(page.request,transport)
        self.assertEqual(result['status'],'collected_unverified')
        self.assertEqual(result['attempts'],2);self.assertEqual(result['api_calls'],0)
        self.assertEqual(sum(len(p.rows) for p in result['pages']),53)
        self.assertEqual(result['verified_completed_weeks'],0)
        transport=ReplayTransport(dict(schema_version='daily-supply-replay-v1',responses=responses))
        self.assertEqual(w.fetch_pages(page.request,transport,max_pages=1)['reason'],'weekly_page_budget')
        transport.origin='api_response'
        with self.assertRaisesRegex(ValueError,'not_authorized'):w.fetch_pages(page.request,transport)


if __name__=='__main__':unittest.main()
