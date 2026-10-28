"""Independent C2 connection tests. Closing proofs here are SYNTHETIC only."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from kiwoom_collector import tracking_review as review, leader_tracking as tracking
from kiwoom_collector.daily_tracking_adapter import ClosingEvidence

FIXTURE = json.loads((Path(__file__).parent/'fixtures/tracking_review_synthetic.json').read_text(encoding='utf-8'))


def snapshot():
    bar=tracking.Observation('005930','mock','KRX',date(2026,10,23),
        '2026-10-23T15:30:00+09:00','2026-10-23T15:31:00+09:00','post_market','SYNTHETIC',**FIXTURE['d0'])
    return tracking.Snapshot('005930','mock','KRX',date(2026,10,23),
        '2026-10-23T16:00:00+09:00','2026-10-23T16:01:00+09:00','{"candidate_confirmed":false}',bar)


def calendar():
    return tracking.FixtureCalendar(tuple(map(date.fromisoformat,FIXTURE['open_dates'])),
        tuple(map(date.fromisoformat,FIXTURE['closed_dates'])),FIXTURE['source'])


def reports(): return deepcopy(FIXTURE['reports'])


def basis():
    return dict(price='KRW',volume='share',turnover='million_KRW',adjustment='0',
                session_basis='chart_session',d0_compatible=True)


def proofs(documents, snap=None):
    snap=snap or snapshot(); result=[]
    for doc in documents:
        for saved in review.parse_report(doc):
            page=saved.page
            for row in page.rows:
                result.append(ClosingEvidence(page.fingerprint,row.fingerprint,snap.episode_id,
                    row.trade_date.isoformat()+'T15:30:00+09:00',page.response_received_at+timedelta(seconds=1),
                    'SYNTHETIC finality/units/basis proof','chart_session','KRW','share','million_KRW',True,True,'final'))
    return tuple(result)


def run(documents=None, **kwargs):
    snap=kwargs.pop('snapshot',snapshot())
    values=dict(snapshot=snap,episode_id=snap.episode_id if snap else None,calendar=calendar(),
                as_of='2026-10-28T17:00:00+09:00',basis=basis())
    values.update(kwargs)
    return review.review_reports(reports() if documents is None else documents,**values)


class TrackingReviewTests(unittest.TestCase):
    def test_verified_d0_d1_d2_through_existing_adapter_and_engine(self):
        docs=reports(); result=run(docs,evidence=proofs(docs))
        self.assertEqual(result['tracking']['state'],'completed')
        self.assertEqual(result['trade_dates'],{0:'2026-10-23',1:'2026-10-26',2:'2026-10-28'})
        self.assertEqual(len(result['observations']),2)
        self.assertEqual(result['tracking']['days'][0]['metrics']['change_d0_percent'],-2)
        self.assertEqual(result['tracking']['days'][1]['metrics']['change_d0_percent'],6)
        self.assertFalse(result['eligible_for_tracking']);self.assertFalse(result['candidate_confirmed'])

    def test_provisional_change_never_promoted_by_parser_success(self):
        result=run()
        self.assertEqual(result['observations'],())
        self.assertEqual([p['provisional']['change_d0_percent'] for p in result['provisional']],[-2,6])
        self.assertTrue(all(p['value_status']=='provisional' and p['market_at'] is None and p['verified_at'] is None for p in result['provisional']))
        self.assertTrue(all(d['metrics']=={} for d in result['tracking']['days']))
        self.assertTrue(all('closing_evidence_missing' in h['reasons'] for h in result['holds']))

    def test_d0_and_report_inputs_immutable(self):
        docs=reports();snap=snapshot();before=deepcopy((docs,snap))
        result=run(docs,snapshot=snap,evidence=proofs(docs,snap))
        self.assertEqual((docs,snap),before)
        result['tracking']['days'][0]['metrics']['close']=999
        self.assertEqual(snap.d0_bar.close,100)

    def test_missing_d0_not_created_from_old_daily_rows(self):
        result=run(snapshot=None)
        self.assertEqual(result['status'],'d0_snapshot_missing')
        self.assertIsNone(result['episode_id']);self.assertIsNone(result['tracking'])
        self.assertEqual(result['observations'],())
        self.assertTrue(all(p['provisional']['change_d0_percent'] is None for p in result['provisional']))

    def test_episode_id_must_match_real_snapshot(self):
        with self.assertRaises(ValueError):run(episode_id='different_d0_episode')
        with self.assertRaises(ValueError):run(snapshot=None,episode_id='not_a_snapshot')

    def test_other_stock_and_environment_cannot_mix(self):
        for mode in ('stock','environment','market'):
            docs=reports();r=docs[1]['results'][0]
            if mode=='environment':docs[1]['environment']=r['environment']='real'
            else:
                value='000660' if mode=='stock' else '005930_NX'
                r['request']['body']['stk_cd']=r['response_projection']['stk_cd']=value
                if mode=='market':docs[1]['environment']=r['environment']='real'
            with self.subTest(mode=mode),self.assertRaises(ValueError):run(docs)

    def test_single_wrong_stock_rejected_against_snapshot(self):
        docs=reports()[:1];r=docs[0]['results'][0]
        r['request']['body']['stk_cd']=r['response_projection']['stk_cd']='000660'
        with self.assertRaises(ValueError):run(docs)

    def test_friday_weekend_and_synthetic_holiday(self):
        result=run()
        self.assertEqual(result['trade_dates'][1],'2026-10-26')
        self.assertEqual(result['trade_dates'][2],'2026-10-28')
        self.assertIn('SYNTHETIC',result['calendar_source'])

    def test_unknown_calendar_never_guessed(self):
        result=run(calendar=None)
        self.assertEqual(result['status'],'calendar_unknown')
        self.assertTrue(all(day['status']=='calendar_unknown' for day in result['tracking']['days']))
        self.assertTrue(all(p['provisional']['change_d0_percent'] is None for p in result['provisional']))

    def test_holiday_observation_held(self):
        docs=reports()[:1];docs[0]['results'][0]['response_projection']['stk_dt_pole_chart_qry'][0]['dt']='20261024'
        result=run(docs,evidence=proofs(docs))
        self.assertEqual(result['observations'],())
        self.assertTrue(any('trading_day_unverified' in h['reasons'] for h in result['holds']))

    def test_identical_report_import_is_idempotent(self):
        docs=reports()[:1]
        result=run(docs+deepcopy(docs),evidence=proofs(docs))
        self.assertEqual(result['duplicate_imports_ignored'],1)
        self.assertEqual(len(result['observations']),1)

    def test_conflicting_same_date_same_receipt_rejected(self):
        docs=reports()[:1];other=deepcopy(docs[0]);other['results'][0]['response_projection']['stk_dt_pole_chart_qry'][0]['cur_prc']='99'
        with self.assertRaisesRegex(ValueError,'Conflicting daily observations'):run(docs+[other])

    def test_late_revision_is_separate_event_not_backdated(self):
        docs=reports()[:1];late=deepcopy(docs[0]);r=late['results'][0]
        r['request']['body']['base_dt']='20261028'
        r['request_started_at']='2026-10-28T16:00:00+09:00';r['response_received_at']='2026-10-28T16:00:01+09:00'
        r['response_projection']['stk_dt_pole_chart_qry'][0]['cur_prc']='99'
        combined=docs+[late]
        result=run(combined,evidence=proofs(combined))
        self.assertEqual(len(result['observations']),2)
        self.assertEqual({o.available_at.date() for o in result['observations']},{date(2026,10,26),date(2026,10,28)})
        at='2026-10-26T17:00:00+09:00'
        earlier=run(docs,as_of=at,evidence=proofs(docs))
        self.assertEqual(run(combined,as_of=at,evidence=proofs(combined))['tracking'],earlier['tracking'])

    def test_future_page_not_exposed_in_preview_or_tracking(self):
        result=run(as_of='2026-10-26T17:00:00+09:00')
        self.assertEqual(len(result['provisional']),1)
        self.assertEqual(result['provisional'][0]['close_candidate'],98)
        self.assertEqual(len(result['inputs']),1)
        self.assertTrue(any(h['reasons']==('response_not_yet_available',) for h in result['holds']))

    def test_future_verification_cannot_create_observation(self):
        docs=reports()[:1];proof=replace(proofs(docs)[0],verified_at='2026-10-29T16:00:00+09:00')
        result=run(docs,evidence=(proof,))
        self.assertEqual(result['observations'],())
        self.assertTrue(any('closing_chronology_unverified' in h['reasons'] for h in result['holds']))

    def test_different_base_date_queries_can_connect(self):
        result=run(reports(),evidence=proofs(reports()))
        self.assertEqual({i['base_dt'] for i in result['inputs']},{'20261026','20261028'})
        self.assertEqual(result['tracking']['state'],'completed')

    def test_adjusted_and_original_reports_cannot_mix(self):
        docs=reports();docs[1]['results'][0]['request']['body']['upd_stkpc_tp']='1'
        with self.assertRaises(ValueError):run(docs)

    def test_adjusted_only_held_even_with_closing_proof(self):
        docs=reports()[:1];docs[0]['results'][0]['request']['body']['upd_stkpc_tp']='1'
        result=run(docs,evidence=proofs(docs))
        self.assertEqual(result['observations'],())
        self.assertIsNone(result['provisional'][0]['provisional']['change_d0_percent'])

    def test_price_and_volume_basis_errors_block_calculations(self):
        for key,value in [('price','thousand_KRW'),('volume','thousand_shares'),('turnover','KRW'),('adjustment','1'),('session_basis','regular_session'),('d0_compatible',False)]:
            context=basis();context[key]=value
            with self.subTest(key=key):
                result=run(basis=context,evidence=proofs(reports()))
                self.assertEqual(result['observations'],())
                self.assertTrue(all(p['provisional']['change_d0_percent'] is None for p in result['provisional']))

    def test_report_unit_error_does_not_override_proof(self):
        docs=reports()[:1];docs[0]['results'][0]['units']['price']='thousand_KRW'
        result=run(docs,evidence=proofs(docs))
        self.assertEqual(result['observations'],())
        self.assertIn('report_unit_mismatch:price',result['holds'][0]['reasons'])

    def test_missing_baseline_does_not_fill_snapshot(self):
        snap=replace(snapshot(),d0_bar=None);result=run(snapshot=snap)
        self.assertIsNone(snap.d0_bar)
        self.assertTrue(all(p['provisional']['change_d0_percent'] is None for p in result['provisional']))
        self.assertTrue(all('d0_reference_price_missing' in p['reasons'] for p in result['provisional']))

    def test_missing_basis_no_inference_from_request_zero(self):
        result=run(basis=None,evidence=proofs(reports()))
        self.assertEqual(result['observations'],())
        self.assertTrue(all(p['provisional']['change_d0_percent'] is None for p in result['provisional']))

    def test_missing_ohlcv_and_vwap_remain_unknown(self):
        docs=reports()[:1];row=docs[0]['results'][0]['response_projection']['stk_dt_pole_chart_qry'][0]
        row['trde_qty']='';row['cur_prc']=''
        result=run(docs,evidence=proofs(docs))
        self.assertEqual(result['observations'],())
        self.assertIsNone(result['provisional'][0]['close_candidate'])
        self.assertIsNone(result['provisional'][0]['volume_candidate'])
        self.assertIsNone(result['provisional'][0]['vwap'])

    def test_cached_rows_and_fake_approval_flags_ignored(self):
        docs=reports()[:1];r=docs[0]['results'][0]
        r.update(rows=[{'close':'999','eligible_for_tracking':True}],b1_validated=True)
        result=run(docs)
        self.assertEqual(result['provisional'][0]['close_candidate'],98)
        self.assertFalse(result['eligible_for_tracking']);self.assertEqual(result['observations'],())

    def test_projection_identity_does_not_claim_full_response(self):
        result=run()
        self.assertTrue(all(i['source_kind']=='redacted_api_projection' for i in result['inputs']))
        self.assertTrue(all(i['data_origin']=='SYNTHETIC_NOT_LIVE' for i in result['inputs']))
        self.assertTrue(all(i['pagination'] in ('complete','more_available') for i in result['inputs']))

    def test_load_utf8_bom_report_and_reject_non_object(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'saved.json';path.write_text(json.dumps(reports()[0]),encoding='utf-8-sig')
            self.assertEqual(review.load_report(path),reports()[0])
            path.write_text('[]',encoding='utf-8')
            with self.assertRaises(ValueError):review.load_report(path)

    def test_unsafe_or_unidentified_projection_rejected(self):
        for mode in ('policy','body','row','nested'):
            docs=reports()[:1];r=docs[0]['results'][0];raw=r['response_projection']
            if mode=='policy':r.pop('projection_policy')
            if mode=='body':raw['unexpected_private_field']='SYNTHETIC'
            if mode=='row':raw['stk_dt_pole_chart_qry'][0]['unexpected_private_field']='SYNTHETIC'
            if mode=='nested':raw['stk_dt_pole_chart_qry'][0]['cur_prc']={'not':'scalar'}
            with self.subTest(mode=mode),self.assertRaises(ValueError):run(docs)

    def test_as_of_before_selection_rejected(self):
        with self.assertRaises(ValueError):run(as_of='2026-10-23T09:00:00+09:00')

    def test_changed_snapshot_episode_retains_separate_identity(self):
        snap=snapshot();other=replace(snap,d0=date(2026,10,26),captured_at='2026-10-26T16:00:00+09:00',created_at='2026-10-26T16:01:00+09:00',d0_bar=None)
        self.assertNotEqual(run(snapshot=snap)['episode_id'],run(snapshot=other)['episode_id'])

    def test_confirmed_vwap_still_unknown_and_no_trading_signal(self):
        result=run(evidence=proofs(reports()))
        self.assertTrue(all(d['metrics']['vwap'] is None for d in result['tracking']['days']))
        self.assertFalse(result['eligible_for_tracking']);self.assertFalse(result['candidate_confirmed'])

    def test_future_or_after_base_row_not_exposed_as_candidate(self):
        docs=reports()[:1]
        docs[0]['results'][0]['response_projection']['stk_dt_pole_chart_qry'][0]['dt']='20261028'
        result=run(docs)
        self.assertEqual(result['provisional'],());self.assertEqual(result['observations'],())
        self.assertIn('future_or_after_base_date',result['holds'][0]['reasons'])

    def test_missing_receipt_cannot_default_to_trade_date(self):
        docs=reports()[:1];docs[0]['results'][0].pop('response_received_at')
        with self.assertRaises(ValueError):run(docs)

    def test_mixed_regular_and_general_session_rejected(self):
        docs=reports();r=docs[1]['results'][0]
        r['request']['api_id']=r['response_headers']['api-id']='ka11081'
        r['response_projection']['result_list']=r['response_projection'].pop('stk_dt_pole_chart_qry')
        with self.assertRaisesRegex(ValueError,'Different chart API/session'):run(docs)

    def test_late_d0_confirmation_is_event_and_never_snapshot_patch(self):
        docs=reports()[:1];d0=deepcopy(docs[0]);r=d0['results'][0]
        r['request_started_at']='2026-10-26T15:45:00+09:00';r['response_received_at']='2026-10-26T15:45:01+09:00'
        row=r['response_projection']['stk_dt_pole_chart_qry'][0]
        row.update(dt='20261023',open_pric='98',high_pric='105',low_pric='95',cur_prc='100',trde_qty='1000')
        snap=replace(snapshot(),d0_bar=None);documents=[d0]+docs
        result=run(documents,snapshot=snap,evidence=proofs(documents,snap))
        self.assertIsNone(snap.d0_bar)
        self.assertEqual(len(result['observations']),2)
        self.assertEqual(result['tracking']['days'][0]['metrics']['change_d0_percent'],-2)
