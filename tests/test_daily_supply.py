"""C14-D synthetic paging/incremental/store/analysis/CLI, never real HTTP."""
from copy import deepcopy
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

from kiwoom_collector import daily_chart as daily
from kiwoom_collector.daily_supply import SupplyStore,collect,scope_key,analyze_store,candidates
from kiwoom_collector.daily_supply_cli import ReplayTransport,HTTPTransport,identity,main
from kiwoom_collector.analyst.saved import diagnostic_pages
from kiwoom_collector.config import Credentials
from test_analyst_saved import archive
from test_analyst_chart import ASOF

BASIS='synthetic-adjusted'


def plan(base='20261027',adjustment='1'):
    return daily.DailyRequest('ka10081','005930','mock',base,adjustment)


def source(count=253):
    saved,doc=archive(count)
    item=saved['results'][0]
    rows=item['response_projection']['result_list']
    return rows,doc


def replay(request,chunks,*,cursor='next-page',terminal='N',received='2026-10-27T16:30:01+09:00'):
    responses=[]
    for i,rows in enumerate(chunks):
        more=i<len(chunks)-1
        responses.append(dict(request=identity(request),continuation={} if i==0 else {'cont-yn':'Y','next-key':cursor},
            reply=dict(body=dict(return_code=0,stk_cd=request.stock_code,stk_dt_pole_chart_qry=deepcopy(rows)),
                http_status=200,headers={'api-id':'ka10081','cont-yn':'Y' if more else terminal,
                                        **({'next-key':cursor} if more or terminal=='Y' else {})},
                request_started_at=received,response_received_at=received)))
    return dict(schema_version='daily-supply-replay-v1',responses=responses)


def continuity(request,latest='2026-10-27',at=ASOF):
    return dict(source='SYNTHETIC corporate action review',available_at=at,basis_id=BASIS,
        stock_code=request.stock_code,environment=request.environment,base_dt=request.base_dt,
        data_origin='synthetic_fixture',api_id=request.api_id,adjustment=request.adjustment,
        previous_latest_day=latest,corporate_actions_unchanged=True)


class SupplyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.store=SupplyStore(self.root/'owned')
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def collect(self,request=None,rows=None,chunks=None,**kwargs):
        request=request or plan()
        rows=source()[0] if rows is None else rows
        transport=ReplayTransport(replay(request,chunks or [rows],received=kwargs.pop('received','2026-10-27T16:30:01+09:00')))
        args=dict(origin='synthetic_fixture',basis_id=BASIS,as_of=ASOF,completed_through='2026-10-27')
        args.update(kwargs)
        return collect(self.store,request,transport,**args)

    def records(self,request=None,origin='synthetic_fixture',basis=BASIS):
        return self.store.records(scope_key(request or plan(),origin,basis))

    def test_bootstrap_pagination_253_unique_and_unreviewed_analysis_is_unavailable(self):
        rows,_=source();run=self.collect(chunks=[rows[:120],rows[120:]])
        self.assertEqual((run['status'],run['mode'],run['replay_calls']),('success','initial',2))
        self.assertEqual(len(self.records()),253);self.assertEqual(run['api_calls'],0)
        self.assertEqual(run['verified_bars'],0)
        r=analyze_store(self.store,plan(),origin='synthetic_fixture',basis_id=BASIS,as_of=ASOF)
        self.assertIsNone(r['reference_close']);self.assertIsNone(r['high52']['prior_high'])

    def test_long_cursor_is_used_unredacted_for_continuation_but_not_logged(self):
        rows,_=source();transport=ReplayTransport(replay(plan(),[rows[:100],rows[100:]],cursor='X'*45))
        run=collect(self.store,plan(),transport,origin='synthetic_fixture',basis_id=BASIS,
                    as_of=ASOF,completed_through='2026-10-27')
        self.assertEqual(run['status'],'success');self.assertNotIn('X'*45,json.dumps(run))

    def test_incremental_overlap_new_day_and_repeat_are_idempotent(self):
        self.collect();rows,_=source()
        new=dict(rows[0],dt='20261028',open_pric='353',high_pric='355',low_pric='351',cur_prc='353',trde_qty='1300')
        request=plan('20261028');later='2026-10-29T10:00:00+09:00'
        args=dict(as_of=later,completed_through='2026-10-28',received='2026-10-28T16:30:01+09:00',
                  continuity_review=continuity(request,at=later))
        run=self.collect(request,rows=[new,*rows[:5]],**args)
        self.assertEqual((run['mode'],run['status']),('incremental','success'))
        self.assertEqual(len(self.records()),254)
        args['continuity_review']=continuity(request,latest='2026-10-28',at=later)
        self.assertEqual(self.collect(request,rows=[new,*rows[:5]],**args)['status'],'success')
        self.assertEqual(len(self.records()),254)

    def test_adjusted_incremental_needs_bound_corporate_action_continuity(self):
        self.collect();before=self.records();run=self.collect()
        self.assertEqual(run['reason'],'adjusted_incremental_requires_basis_review')
        self.assertEqual(run['attempts'],0);self.assertEqual(self.records(),before)
        bad=continuity(plan());bad['basis_id']='different'
        self.assertEqual(self.collect(continuity_review=bad)['attempts'],0)
        for change in (dict(available_at='2099-01-01T00:00:00+09:00'),dict(environment='real'),
                       dict(previous_latest_day='2026-10-20'),dict(corporate_actions_unchanged=False),
                       dict(data_origin='api_response'),dict(api_id='ka11081')):
            with self.subTest(change=change):
                self.assertEqual(self.collect(continuity_review=dict(continuity(plan()),**change))['attempts'],0)

    def test_original_price_incremental_does_not_need_adjustment_continuity(self):
        request=plan(adjustment='0');self.collect(request)
        run=self.collect(request,rows=source()[0][:5])
        self.assertEqual((run['mode'],run['status']),('incremental','success'))

    def test_historical_revision_stops_atomically_and_full_refresh_is_explicit(self):
        self.collect();before=self.records();rows,_=source()
        rows[2].update(open_pric='500',high_pric='505',low_pric='490',cur_prc='501')
        run=self.collect(rows=rows[:5],continuity_review=continuity(plan()))
        self.assertEqual(run['reason'],'historical_revision_requires_full_refresh')
        self.assertEqual(before,self.records())
        self.assertEqual(self.collect(rows=rows,full_refresh=True)['status'],'success')
        self.assertNotEqual(before,self.records())
        with closing(sqlite3.connect(self.store.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0],3)

    def test_corporate_action_metadata_revision_is_not_ignored_when_prices_match(self):
        self.collect();before=self.records();rows,_=source();rows[0]['upd_rt']='0.5'
        run=self.collect(rows=rows[:5],continuity_review=continuity(plan()))
        self.assertEqual(run['reason'],'historical_revision_requires_full_refresh')
        self.assertEqual(self.records(),before)

    def test_page_call_and_time_budgets_preserve_existing_view(self):
        rows,_=source()
        for kwargs,reason in ((dict(max_pages=1),'page_budget_exhausted'),(dict(max_calls=1),'call_or_time_budget')):
            with self.subTest(kwargs=kwargs):
                run=self.collect(chunks=[rows[:100],rows[100:]],**kwargs)
                self.assertEqual(run['reason'],reason);self.assertEqual(self.records(),{})
        transport=ReplayTransport(replay(plan(),[rows]))
        original=transport.fetch
        def delayed(*args):time.sleep(.02);return original(*args)
        transport.fetch=delayed
        run=collect(self.store,plan(),transport,origin='synthetic_fixture',basis_id=BASIS,
                    as_of=ASOF,completed_through='2026-10-27',max_seconds=.001)
        self.assertEqual(run['reason'],'time_budget');self.assertFalse(run['published'])

    def test_repeated_cursor_missing_overlap_and_no_progress_stop(self):
        rows,_=source()
        run=self.collect(chunks=[rows[:60],rows[60:120],rows[120:180],rows[180:]])
        self.assertEqual(run['reason'],'repeated_cursor')
        run=self.collect(chunks=[rows[:60],rows[:60],rows[60:]])
        self.assertEqual(run['reason'],'pagination_no_progress')
        self.collect()
        run=self.collect(rows=rows[:2],continuity_review=continuity(plan()))
        self.assertEqual(run['reason'],'incremental_overlap_missing')

    def test_identical_boundary_overlap_deduplicates_conflicting_overlap_fails(self):
        rows,_=source()
        run=self.collect(chunks=[rows[:130],rows[129:]])
        self.assertEqual(run['status'],'success');self.assertEqual(len(self.records()),253)
        other=deepcopy(rows[129:]);other[0]['cur_prc']='1'
        before=self.records();run=self.collect(chunks=[rows[:130],other],full_refresh=True)
        self.assertEqual(run['reason'],'conflicting_duplicate_day');self.assertEqual(self.records(),before)

    def test_duplicate_date_unordered_days_rejected_ascending_accepted(self):
        rows,_=source()
        for bad in ([*rows,rows[0]],[rows[1],rows[0],*rows[2:]]):
            self.assertEqual(self.collect(rows=bad)['reason'],'duplicate_or_unordered_dates')
        self.assertEqual(self.collect(rows=list(reversed(rows)))['status'],'success')

    def test_zero_volume_invalid_ohlc_missing_date_are_held_not_filled(self):
        rows,_=source();rows[0]['trde_qty']='0';rows[1]['high_pric']='1';rows[2]['dt']='bad'
        run=self.collect(rows=rows)
        self.assertEqual(run['status'],'insufficient_history')
        self.assertIn('zero_volume_or_halt_review',run['quality_counts'])
        self.assertEqual(len(self.records()),252)
        self.assertEqual(sum(r['quality']=='candidate' for r in self.records().values()),250)

    def test_current_session_separate_from_253_completed_bars(self):
        rows,_=source();current=dict(rows[0],dt='20261028')
        run=self.collect(plan('20261028'),rows=[current,*rows],received='2026-10-28T09:30:00+09:00')
        self.assertEqual(run['status'],'success')
        self.assertEqual(self.records(plan('20261028'))['2026-10-28']['quality'],'held')
        self.assertEqual(run['quality_counts']['open_or_unreviewed_session'],1)

    def test_transport_failure_and_http_auth_failure_never_retry_or_leak(self):
        transport=ReplayTransport(replay(plan(),[source()[0]]))
        transport.fetch=Mock(side_effect=RuntimeError('SYNTHETIC_SECRET_TOKEN'))
        run=collect(self.store,plan(),transport,origin='synthetic_fixture',basis_id=BASIS,as_of=ASOF,completed_through='2026-10-27')
        transport.fetch.assert_called_once();self.assertEqual(run['reason'],'transport_or_parse_failure')
        self.assertNotIn('SYNTHETIC_SECRET_TOKEN',json.dumps(run))
        fixture=replay(plan(),[source()[0]]);fixture['responses'][0]['reply']['http_status']=401
        transport=ReplayTransport(fixture)
        run=collect(self.store,plan(),transport,origin='synthetic_fixture',basis_id=BASIS,as_of=ASOF,completed_through='2026-10-27')
        self.assertEqual(run['reason'],'daily_response_rejected');self.assertEqual(transport.calls,1)

    def test_environment_origin_and_basis_storage_scopes_do_not_mix(self):
        self.collect()
        self.assertEqual(self.records(origin='api_response'),{})
        self.assertEqual(self.records(basis='rotated-basis'),{})
        real=daily.DailyRequest('ka10081','005930','real','20261027','1')
        self.assertEqual(self.records(real),{})

    def test_bound_review_feeds_c14_and_missing_review_stays_unavailable(self):
        self.collect();records=self.records();archives=self.store.archives(records);rows,doc=source()
        evidence=[]
        for saved in archives.values():
            p=diagnostic_pages(saved)[0][0]
            evidence.extend(dict(page_fingerprint=p.fingerprint,row_fingerprint=r.fingerprint,
                episode_id='SYNTHETIC',market_at=r.trade_date.isoformat()+'T15:30:00+09:00',
                verified_at='2026-10-27T17:00:00+09:00',source='SYNTHETIC reviewer',
                session_basis='chart_session',price_unit='KRW',volume_unit='share',status='final') for r in p.rows)
        review=dict(scope=dict(environment='mock',data_origin='synthetic_fixture',api_id='ka10081',
            stock_code='005930',adjustment='1',basis_id=BASIS),price_basis=doc['price_basis'],
            calendar=doc['calendar'],selection=doc['selection'],price=doc['price'],evidence=evidence,context_id='SYNTHETIC')
        r=analyze_store(self.store,plan(),origin='synthetic_fixture',basis_id=BASIS,as_of=ASOF,review=review)
        self.assertEqual(r['reference_close']['value'],'352');self.assertEqual(r['high52']['prior_high'],'353')
        self.assertEqual(r['moving_averages']['MA20']['value'],'342.5')
        review['price_basis']=dict(review['price_basis'],id='wrong')
        with self.assertRaises(ValueError):analyze_store(self.store,plan(),origin='synthetic_fixture',basis_id=BASIS,as_of=ASOF,review=review)

    def test_existing_nonowned_directory_and_operational_data_root_rejected(self):
        root=self.root/'existing';root.mkdir();file=root/'operations.db';file.write_bytes(b'preserve')
        with self.assertRaises(ValueError):SupplyStore(root)
        self.assertEqual(file.read_bytes(),b'preserve')
        with self.assertRaises(ValueError):SupplyStore(self.root/'data'/'new')
        self.assertIsInstance(SupplyStore(self.store.root),SupplyStore)

    def test_incremental_pages_merge_into_one_reviewed_254_bar_c14_input(self):
        self.collect();rows,doc=source();request=plan('20261028');later='2026-10-29T10:00:00+09:00'
        new=dict(rows[0],dt='20261028',open_pric='353',high_pric='355',low_pric='351',cur_prc='353',trde_qty='1300')
        self.collect(request,rows=[new,*rows[:5]],as_of=later,completed_through='2026-10-28',
            received='2026-10-28T16:30:01+09:00',continuity_review=continuity(request,at=later))
        evidence=[]
        for saved in self.store.archives(self.records()).values():
            page=diagnostic_pages(saved)[0][0]
            evidence.extend(dict(page_fingerprint=page.fingerprint,row_fingerprint=r.fingerprint,
                episode_id='SYNTHETIC',market_at=r.trade_date.isoformat()+'T15:30:00+09:00',
                verified_at='2026-10-28T17:00:00+09:00',source='SYNTHETIC updated review',
                session_basis='chart_session',price_unit='KRW',volume_unit='share',status='final') for r in page.rows)
        review=dict(scope=dict(environment='mock',data_origin='synthetic_fixture',api_id='ka10081',
            stock_code='005930',adjustment='1',basis_id=BASIS),price_basis=doc['price_basis'],
            calendar=dict(doc['calendar'],as_of=later,available_at=later,
                completed_sessions=[*doc['calendar']['completed_sessions'],'2026-10-28']),
            selection=doc['selection'],price={},evidence=evidence,context_id='SYNTHETIC')
        r=analyze_store(self.store,request,origin='synthetic_fixture',basis_id=BASIS,as_of=later,review=review)
        self.assertEqual(len(r['analysis']['evidence']['bars']),254)
        self.assertEqual(r['reference_close']['value'],'353')
        self.assertEqual(r['moving_averages']['MA3']['value'],'352')
        self.assertEqual(r['moving_averages']['MA20']['value'],'343.5')
        self.assertEqual(r['high52']['prior_high'],'354')

    def test_server_secrets_and_authorization_headers_are_not_persisted_or_logged(self):
        fixture=replay(plan(),[source()[0]])
        secret='SYNTHETIC_CREDENTIAL_NOT_FOR_STORAGE'
        fixture['responses'][0]['reply']['body'].update(token=secret,return_msg=secret)
        fixture['responses'][0]['reply']['headers']['authorization']='Bearer '+secret
        transport=ReplayTransport(fixture)
        run=collect(self.store,plan(),transport,origin='synthetic_fixture',basis_id=BASIS,as_of=ASOF,completed_through='2026-10-27')
        self.assertEqual(run['status'],'success')
        self.assertNotIn(secret,json.dumps(run))
        self.assertNotIn(secret.encode(),self.store.path.read_bytes())

    def test_candidate_code_normalization_exclusion_duplicate_and_future_cutoff(self):
        doc=c12();doc['candidate_filter']['decisions']['660']={'state':'pending'}
        self.assertEqual(candidates(doc,environment='mock',origin='synthetic_fixture',as_of=ASOF),['005930'])
        doc['candidate_filter']['decisions']['005930']={'state':'analysis_candidate'}
        with self.assertRaises(ValueError):candidates(doc,environment='mock',origin='synthetic_fixture',as_of=ASOF)


def c12():
    return dict(environment='mock',market='KRX',source_kind='fixture',candidate_filter=dict(
        version='c12-am-candidate-join-v1',at=ASOF,decisions={'5930':dict(state='analysis_candidate')}))


class SupplyCLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.doc=self.root/'c12.json';self.doc.write_text(json.dumps(c12()),encoding='utf-8')
        self.args=['--c12-json',str(self.doc),'--base-date','20261027','--completed-through','2026-10-27',
                   '--environment','mock','--basis-id',BASIS,'--as-of',ASOF]
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def test_plan_never_reads_credentials_or_opens_store(self):
        with patch('kiwoom_collector.daily_supply_cli.credentials',side_effect=AssertionError('credentials')),\
             patch('kiwoom_collector.daily_supply_cli.SupplyStore',side_effect=AssertionError('store')),\
             patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(main(self.args),0)
        self.assertEqual(json.loads(out.getvalue())['api_calls'],0)

    def test_replay_cli_executes_store_and_exports_unavailable_chart(self):
        path=self.root/'replay.json';path.write_text(json.dumps(replay(plan(),[source()[0]])),encoding='utf-8')
        root=self.root/'supply'
        with patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(main(self.args+['--replay-json',str(path),'--store-root',str(root)]),0)
        self.assertEqual(json.loads(out.getvalue())['api_calls'],0)
        report=json.loads(next(root.glob('*.chart.json')).read_text(encoding='utf-8'))
        self.assertIsNone(report['reference_close']);self.assertEqual(report['data_origin'],'synthetic_fixture')

    def test_live_requires_approval_and_existing_token_and_never_issues_oauth(self):
        with patch('kiwoom_collector.daily_supply_cli.HTTPTransport',side_effect=AssertionError('no HTTP')),\
             patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):
            main(self.args+['--live'])
        creds=Credentials('SYNTHETIC_APP','SYNTHETIC_SECRET','mock')
        with self.assertRaises(ValueError):HTTPTransport(creds,[plan()],'token',approved=False)
        transport=HTTPTransport(creds,[plan()],'SYNTHETIC_ACCESS_TOKEN',approved=True)
        self.addCleanup(transport.close)
        self.assertTrue(transport.client.session.verify)
        self.assertFalse(transport.client.session.trust_env)
        response=Mock(status_code=200,headers={'cont-yn':'N'});response.json.return_value={'return_code':0}
        def fake_request(path,body,headers,*,before_send):
            self.assertEqual(path,daily.API_PATH);before_send();return response
        with patch.object(transport.client,'_request',side_effect=fake_request),\
             patch.object(transport.client,'authenticate',side_effect=AssertionError('OAuth forbidden')):
            transport.fetch(plan(),{},time.perf_counter()+2)
        self.assertEqual(transport.calls,1)


if __name__=='__main__':unittest.main()
