"""Saved synthetic projections only; network forbidden and no operational DB."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector.analyst.saved import analyze_saved, diagnostic_pages
from kiwoom_collector.analyst.context import analyze_context
from kiwoom_collector.analyst.__main__ import main
from test_analyst_chart import fixture, ASOF


def archive(count=253):
    doc=fixture(count)
    request=dict(api_id='ka11081',body=dict(stk_cd='005930',base_dt='20261027',upd_stkpc_tp='1'))
    projection=dict(return_code=0,stk_cd='005930',result_list=[dict(dt=b['trade_date'].replace('-',''),
        open_pric='-'+b['open'],high_pric=b['high'],low_pric=b['low'],cur_prc=b['close'],
        trde_qty=b['volume']) for b in reversed(doc['bars'])])
    item=dict(request=request,environment='mock',status='parsed',http_status=200,
        response_projection=projection,response_headers={'api-id':'ka11081','cont-yn':'N'},
        request_started_at='2026-10-27T16:30:00+09:00',response_received_at='2026-10-27T16:30:01+09:00')
    saved=dict(version='step7-b2-a-v1',mode='synthetic_fixture',data_origin='synthetic_fixture',
        environment='mock',results=[item])
    return saved,doc


def review_for(saved,doc):
    pages,origin=diagnostic_pages(saved)
    p=pages[0];r=p.request
    return dict(scope=dict(api_id=r.api_id,stock_code=r.stock_code,environment=r.environment,
        base_dt=r.base_dt,adjustment=r.adjustment,data_origin=origin),
        page_fingerprints=[p.fingerprint],context_id='synthetic-closing-review',stock_name=doc['stock_name'],
        price_basis=doc['price_basis'],calendar=doc['calendar'],price=doc['price'],selection=doc['selection'],
        evidence=[dict(page_fingerprint=p.fingerprint,row_fingerprint=row.fingerprint,
            episode_id='synthetic-closing-review',market_at=row.trade_date.isoformat()+'T15:30:00+09:00',
            verified_at='2026-10-27T17:00:00+09:00',source='SYNTHETIC explicit review',
            session_basis='regular_session',price_unit='KRW',volume_unit='share',status='final') for row in p.rows])


class SavedChartTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def save(self,value,name='archive.json'):
        path=self.root/name;path.write_text(json.dumps(value),encoding='utf-8');return path

    def test_no_review_never_fabricates_calendar_finality_or_metrics(self):
        saved,_=archive();path=self.save(saved);before=path.read_bytes()
        result=analyze_saved([path],as_of=ASOF);r=result['reports'][0]['report']
        self.assertEqual(result['summary']['parsed_rows'],253)
        self.assertEqual(result['summary']['fully_analyzable_count'],0)
        self.assertTrue(all(v['value'] is None for v in r['moving_averages'].values()))
        self.assertIsNone(r['high52']['prior_high']);self.assertEqual(r['supply_proxy']['status'],'unavailable')
        self.assertEqual(r['data_origin'],'synthetic_fixture')
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(result['sources'][0]['sha256'],hashlib.sha256(before).hexdigest())
        self.assertEqual(result['api_calls'],0);self.assertEqual(result['database_writes'],0)

    def test_bound_review_computes_all_metrics_from_reparsed_raw_rows(self):
        saved,doc=archive();review=review_for(saved,doc)
        saved['results'][0]['rows']=[{'close':'777777'}]
        result=analyze_saved([self.save(saved)],as_of=ASOF,reviews=[review]);r=result['reports'][0]['report']
        self.assertEqual(result['summary']['fully_analyzable_count'],1)
        for n in (3,5,20,60,200):
            expected=sum(Decimal(b['close']) for b in doc['bars'][-n:])/n
            self.assertEqual(Decimal(r['moving_averages'][f'MA{n}']['value']),expected)
        self.assertEqual(r['high52']['prior_high'],'353')
        self.assertEqual(Decimal(r['high52']['distance_below_high_percent']).quantize(Decimal('.000001')),Decimal('.283286'))
        self.assertEqual(r['supply_proxy']['status'],'available')
        self.assertEqual(r['supply_proxy']['areas'][0]['volume'],'1252')
        self.assertEqual(Decimal(r['supply_proxy']['areas'][0]['typical_price']),352)
        self.assertFalse(r['supply_proxy']['overhead_resistance_possible'])
        self.assertNotIn('777777',json.dumps(r))
        self.assertTrue(r['analysis']['evidence']['bars'][0]['api_raw_values']['open_pric'].startswith('-'))

    def test_short_validated_history_keeps_ma20_but_not_ma200_or_high52(self):
        saved,doc=archive(20)
        r=analyze_saved([self.save(saved)],as_of=ASOF,reviews=[review_for(saved,doc)])['reports'][0]['report']
        self.assertIsNotNone(r['moving_averages']['MA20']['value'])
        self.assertIsNone(r['moving_averages']['MA200']['value'])
        self.assertIsNone(r['high52']['prior_high'])

    def test_missing_one_closing_proof_does_not_fill_that_bar(self):
        saved,doc=archive();review=review_for(saved,doc);review['evidence'].pop(8)
        r=analyze_saved([self.save(saved)],as_of=ASOF,reviews=[review])['reports'][0]['report']
        self.assertEqual(r['high52']['status'],'unavailable')
        self.assertEqual(len(r['analysis']['evidence']['bars']),252)

    def test_review_origin_page_row_and_scope_binding_fail_closed(self):
        saved,doc=archive();base=review_for(saved,doc);path=self.save(saved)
        for kind in ('origin','page','row','scope'):
            review=deepcopy(base)
            if kind=='origin':review['scope']['data_origin']='api_response'
            if kind=='page':review['page_fingerprints']=['wrong']
            if kind=='row':review['evidence'][0]['row_fingerprint']='wrong'
            if kind=='scope':review['scope']['stock_code']='000660'
            with self.subTest(kind=kind),self.assertRaises(ValueError):analyze_saved([path],as_of=ASOF,reviews=[review])

    def test_future_receipt_and_future_review_do_not_become_final_bars(self):
        saved,doc=archive();review=review_for(saved,doc)
        for proof in review['evidence']:proof['verified_at']='2099-01-01T00:00:00+09:00'
        r=analyze_saved([self.save(saved)],as_of=ASOF,reviews=[review])['reports'][0]['report']
        self.assertIsNone(r['reference_close'])
        r=analyze_saved([self.root/'archive.json'],as_of='2026-10-27T16:00:00+09:00')['reports'][0]['report']
        self.assertIsNone(r['reference_close'])

    def test_live_archive_metadata_remains_api_response_even_for_mock(self):
        saved,_=archive();saved.update(mode='live_http_diagnostic',data_origin='mock_server_unverified_source')
        result=analyze_saved([self.save(saved)],as_of=ASOF)
        r=result['reports'][0]['report']
        self.assertEqual((r['environment'],r['data_origin']),('mock','api_response'))
        self.assertEqual(result['summary']['synthetic_reports'],0)
        self.assertIsNone(r['reference_close'])
        saved['data_origin']='real_server_unverified_daily_bars'
        with self.assertRaises(ValueError):diagnostic_pages(saved)

    def test_raw_response_environment_code_api_and_http_errors_not_trusted(self):
        for key,value in (('environment','real'),('http_status',403)):
            saved,_=archive();saved['results'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):diagnostic_pages(saved)
        saved,_=archive();saved['results'][0]['response_projection']['stk_cd']='000660'
        with self.assertRaises(ValueError):diagnostic_pages(saved)
        saved,_=archive();saved['results'][0]['response_headers']['api-id']='ka10081'
        with self.assertRaises(ValueError):diagnostic_pages(saved)

    def test_duplicate_pages_and_mixed_fixture_server_sources_rejected(self):
        saved,_=archive();path=self.save(saved)
        with self.assertRaises(ValueError):analyze_saved([path,path],as_of=ASOF)
        other=deepcopy(saved);other.update(mode='live_http_diagnostic',data_origin='mock_server_unverified_source')
        with self.assertRaises(ValueError):analyze_saved([path,self.save(other,'other.json')],as_of=ASOF)

    def test_c12_normalized_codes_preserve_decisions_and_block_scope_mismatch(self):
        saved,_=archive();path=self.save(saved)
        c12=dict(environment='mock',market='KRX',source_kind='fixture',
            candidate_filter=dict(version='c12-am-candidate-join-v1',at=ASOF,
                decisions={'5930':dict(state='pending',reasons=['type_unverified'])}))
        r=analyze_saved([path],as_of=ASOF,candidate_document=c12)['reports'][0]['report']
        self.assertEqual(r['candidate_context']['stock_code'],'005930')
        self.assertEqual(r['candidate_context']['decision']['state'],'pending')
        self.assertFalse(r['candidate_eligibility_changed'])
        for change in (dict(environment='real'),dict(source_kind='readonly_adapter'),dict(market='NXT')):
            with self.subTest(change=change),self.assertRaises(ValueError):
                analyze_saved([path],as_of=ASOF,candidate_document=dict(c12,**change))
        c12['candidate_filter']['decisions']['005930']=dict(state='analysis_candidate')
        with self.assertRaises(ValueError):analyze_saved([path],as_of=ASOF,candidate_document=c12)

    def test_readonly_c12_metadata_is_not_promoted_to_verified_origin(self):
        saved,_=archive();saved.update(mode='live_http_diagnostic',data_origin='mock_server_unverified_source')
        c12=dict(environment='mock',market='KRX',source_kind='readonly_adapter',data_origin='unverified',
            candidate_filter=dict(version='c12-am-candidate-join-v1',at=ASOF,decisions={}))
        r=analyze_saved([self.save(saved)],as_of=ASOF,candidate_document=c12)['reports'][0]['report']
        self.assertEqual(r['candidate_context']['decision']['state'],'unmatched')
        self.assertEqual(r['candidate_context']['c12_data_origin'],'unverified')

    def test_cli_writes_individual_json_and_refuses_overwrite(self):
        saved,_=archive();path=self.save(saved);output=self.root/'new-output'
        args=['--diagnostic-json',str(path),'--as-of',ASOF,'--output-dir',str(output)]
        with patch('sys.stdout',new_callable=io.StringIO):self.assertEqual(main(args),0)
        report=json.loads((output/'mock-005930-ka11081-20261027-1.json').read_text(encoding='utf-8'))
        self.assertEqual(report['schema_version'],'arvis-chart-context-v1')
        self.assertEqual(report['high52']['status'],'unavailable')
        with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):main(args)

    def test_changed_source_even_truncated_to_empty_is_detected_before_export(self):
        saved,_=archive();path=self.save(saved)
        def change(document,**kwargs):
            result=analyze_context(document,**kwargs)
            path.write_bytes(b'')
            return result
        with patch('kiwoom_collector.analyst.saved.analyze_context',side_effect=change):
            with self.assertRaisesRegex(ValueError,'saved_source_changed_during_analysis'):
                analyze_saved([path],as_of=ASOF)


if __name__=='__main__':unittest.main()
