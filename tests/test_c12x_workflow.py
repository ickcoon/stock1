"""Accelerated schedules and session state using existing engines; no network."""
from copy import deepcopy
from contextlib import closing
from datetime import date,timedelta
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from kiwoom_collector import schedule,session_review,am_briefing,am_momentum as am
from kiwoom_collector.am_candidate_adapter import join
from kiwoom_collector.client import KiwoomClient,Snapshot
from kiwoom_collector.config import Credentials
from kiwoom_collector.storage import Store
from kiwoom_collector.operating_workflow import Workflow
from test_telegram_observation import observation,rehash
from kiwoom_collector import telegram_briefing as tg

DAY=date(2026,11,2)

def empty_morning(slot):
    cutoff=f'2026-11-02T{slot}:00+09:00'
    doc=dict(schema_version=am.ADAPTER_INPUT_SCHEMA,source_kind='readonly_adapter',trade_date='2026-11-02',
        environment='real',market='KRX',selection_cutoff_at=cutoff,rankings=[],securities={})
    doc=join(doc,dict(at=cutoff,synthetic=False,records=[],provenance={}))
    return am_briefing.analyze(doc,generated_at='2026-11-02T12:00:00+09:00')

class C12XTests(unittest.TestCase):
    def setUp(self):self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def test_exact_23_and_8_slots_no_observation_after_1600(self):
        self.assertEqual(len(schedule.C12X_SLOTS),23);self.assertEqual(len(schedule.TURNOVER_SLOTS),9)
        self.assertEqual(schedule.C12X_SLOTS[:10],tuple(f'09:{m:02}' for m in range(10,60,5)))
        self.assertEqual(schedule.C12X_SLOTS[-1],'16:00')
        self.assertTrue(set(schedule.TURNOVER_SLOTS)<=set(schedule.C12X_SLOTS))
        after=schedule.slot_at(DAY,'16:00')+timedelta(seconds=1)
        self.assertEqual(schedule.next_slot(after,slots=schedule.C12X_SLOTS),schedule.slot_at(date(2026,11,3),'09:10'))
        self.assertEqual(schedule.next_slot(schedule.slot_at(date(2026,11,6),'16:00')+timedelta(seconds=1),slots=schedule.C12X_SLOTS),schedule.slot_at(date(2026,11,9),'09:10'))
        with self.assertRaises(ValueError):schedule.checked_slots(('16:30',))

    def test_existing_loop_one_day_23_rankings_8_turnover_and_restart_no_duplicate(self):
        with tempfile.TemporaryDirectory() as folder, closing(Store(Path(folder)/'data/stock1-real.db')) as store:
            root=Path(folder)
            client=KiwoomClient(Credentials('SYNTHETIC_APP','SYNTHETIC_SECRET','real'));self.addCleanup(client.close)
            clock=[schedule.slot_at(DAY,'09:10')]
            client.fetch_once=Mock(side_effect=lambda _:Snapshot(clock[0],[dict(bigd_rank='1',stk_cd='005930',stk_nm='SYNTHETIC')]))
            workflow=Workflow(root,{'turnover_max_pages':3},clock=lambda:clock[0]);workflow.review=Mock(return_value={'status':'held'})
            workflow.close_day=Mock(return_value={'automatic_deletion':False})
            def sleep(seconds):
                clock[0]+=timedelta(seconds=seconds)
                if clock[0]>schedule.slot_at(DAY,'16:00'):raise KeyboardInterrupt()
            with patch('kiwoom_collector.operating_workflow.collect_turnover',return_value={'status':'partial'}) as turnover:
                with self.assertRaises(KeyboardInterrupt):
                    schedule.collect_schedule(client,store,'real',now=lambda:clock[0],sleep=sleep,
                        slots=schedule.C12X_SLOTS,after_slot=workflow)
                self.assertEqual(client.fetch_once.call_count,23);self.assertEqual(turnover.call_count,9)
                self.assertTrue(all(call.args[-1]==3 for call in turnover.call_args_list))
                self.assertEqual(workflow.review.call_count,5);workflow.close_day.assert_called_once()
                clock[0]=schedule.slot_at(DAY,'09:40')
                with self.assertRaises(KeyboardInterrupt):
                    schedule.collect_schedule(client,store,'real',now=lambda:clock[0],sleep=Mock(side_effect=KeyboardInterrupt()),
                        slots=schedule.C12X_SLOTS,after_slot=workflow)
                self.assertEqual(turnover.call_count,9);self.assertEqual(client.fetch_once.call_count,23)

    def test_hook_failure_preserves_ranking_and_skips_late_backfill(self):
        with tempfile.TemporaryDirectory() as folder, closing(Store(Path(folder)/'real.db')) as store:
            client=Mock();client.fetch_once.return_value=Snapshot(schedule.slot_at(DAY,'09:10'),[dict(bigd_rank='1',stk_cd='005930',stk_nm='SYNTHETIC')])
            with self.assertRaises(KeyboardInterrupt):
                schedule.collect_schedule(client,store,'real',now=lambda:schedule.slot_at(DAY,'09:10'),
                    sleep=Mock(side_effect=KeyboardInterrupt()),slots=schedule.C12X_SLOTS,after_slot=Mock(side_effect=ValueError('synthetic')))
            self.assertEqual(store.status()['successes'],1)

    def test_morning_is_one_session_and_missing_evidence_never_replaces_selection(self):
        state=None
        for slot in schedule.AM_DECISION_SLOTS:
            state,event=session_review.morning_transition(empty_morning(slot),slot,state)
            self.assertEqual(event['send'],slot=='09:50');self.assertEqual(state['revision'],0)
        self.assertEqual(state['phases'],list(schedule.AM_DECISION_SLOTS));self.assertTrue(state['closed'])
        same,event=session_review.morning_transition(empty_morning('09:50'),'09:50',state)
        self.assertEqual(event['action'],'duplicate_phase');self.assertEqual(same,state)

    def test_0945_snapshot_does_not_invent_an_extra_c12_repeat(self):
        from kiwoom_collector.am_time_policy import ObservationPolicy
        path=Path(__file__).parent/'fixtures/am_sector_briefing_synthetic.json'
        doc=json.loads(path.read_text(encoding='utf-8-sig'))
        doc['selection_cutoff_at']='2026-10-29T09:45:00+09:00'
        doc['observation_policy']=ObservationPolicy().document()
        before=am_briefing.analyze(doc,generated_at='2026-10-29T09:46:00+09:00')
        extra=deepcopy(doc['rankings'][2]);extra.update(slot='09:45',observed_at='2026-10-29T09:45:00+09:00',received_at='2026-10-29T09:45:00+09:00')
        doc['rankings'].append(extra)
        after=am_briefing.analyze(doc,generated_at='2026-10-29T09:46:00+09:00')
        self.assertEqual(before['completeness'],after['completeness'])
        self.assertEqual(before['analysis']['rows'],after['analysis']['rows'])

    def test_first_selection_change_and_price_only_no_spam(self):
        state=None
        for slot,codes,send in [('09:40',['005930'],True),('09:45',['005930'],False),('09:50',['000660'],True)]:
            report=empty_morning(slot)
            with patch('kiwoom_collector.session_review.c12_chart_bridge.published_top',return_value=[{'stock_code':c} for c in codes]):
                state,event=session_review.morning_transition(report,slot,state)
            self.assertEqual(event['send'],send)
        self.assertEqual(state['revision'],2);self.assertEqual(state['selected'],['000660'])

    def test_pm_reuses_final_review_gate_and_only_1500_reports(self):
        report={'rows':[{'stock_code':'005930','candidate_confirmed':False,'qualification':{'final_review_allowed':False,'review_status':'검증 보류'}}]}
        self.assertFalse(session_review.afternoon_review(report,'14:30')['send'])
        result=session_review.afternoon_review(report,'15:00')
        self.assertTrue(result['send']);self.assertEqual(result['candidates'],[])
        with self.assertRaises(ValueError):session_review.afternoon_review(report,'16:00')

    def test_pm_partial_message_has_observations_not_fabricated_candidates(self):
        report=observation();report.update(selection_type='PM_CLOSING_REVIEW',pm_candidates=[],pm_reviewable=[])
        text=tg.summary(rehash(report));self.assertIn('사전 검토',text);self.assertIn('보류',text)
        report['pm_candidates']=[{'stock_code':'005930','qualification':{'final_review_allowed':True,'review_status':'검토 가능','candidate_confirmed':False,'checks':{'market_cap':None}}}]
        with self.assertRaises(ValueError):tg.validate(rehash(report))

    def test_empty_data_is_reported_missing_not_zero_price(self):
        report=observation();report['observation'].update(ranking_at=None,turnover_received_at=None,turnover_rows=0,stocks=[])
        report['step6d']=dict(verified=0,held=0);report['charts']=dict(available=0,unavailable=0)
        text=tg.summary(rehash(report));self.assertIn('미확보',text);self.assertIn('자료 없음',text)

    def test_compressed_summary_preserves_source_and_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'day.json.gz';source=Path(folder)/'raw.db';source.write_bytes(b'SYNTHETIC_RAW')
            result=session_review.write_daily_summary(path,{'finality':'unverified_1600_observation'})
            self.assertFalse(result['automatic_deletion']);self.assertEqual(source.read_bytes(),b'SYNTHETIC_RAW')
            with gzip.open(path,'rt') as stream:self.assertEqual(json.load(stream)['finality'],'unverified_1600_observation')
            with self.assertRaises(ValueError):session_review.write_daily_summary(path,{})

    def test_step7_adapter_retains_identity_and_delegates_without_fabricated_performance(self):
        from kiwoom_collector import leader_tracking as tracking
        at=schedule.slot_at(DAY,'15:00')
        snapshot=tracking.Snapshot('005930','real','KRX',DAY,at,at,tracking.canonical({'candidate_confirmed':False}))
        with patch.object(tracking,'snapshot_from_review',return_value=snapshot):
            records=session_review.tracking_records({'rows':[{'stock_code':'005930'}]},['005930'],captured_at=at)
        self.assertEqual(records[0]['role'],'selected');self.assertFalse(records[0]['is_execution_record'])
        calendar=Mock();calendar.offset.return_value=None
        result=session_review.review_performance(records[0],[],calendar=calendar,as_of=at)
        self.assertEqual(result['episode_id'],snapshot.episode_id)
        self.assertEqual(calendar.offset.call_count,2)

    def test_saved_real_scope_pm_pipeline_produces_partial_text_without_network(self):
        from kiwoom_collector.turnover_rank import Capture,Profile,collect
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);at=schedule.slot_at(DAY,'15:00')
            snapshot=root/'snapshot.db'
            with closing(Store(snapshot)) as store:
                identifier=store.begin(at,'real','1',scheduled_at=at)
                store.finish(identifier,Snapshot(at,[dict(bigd_rank=str(i),stk_cd=f'{i:06}',stk_nm='SYNTHETIC') for i in range(1,21)]))
            # Synthetic HTTP capture in a temp DB models the existing real/live_test contract.
            fixture=json.loads((Path(__file__).parent/'fixtures/ka10032_synthetic.json').read_text(encoding='utf-8-sig'))
            body=fixture['pages'][0]['body']
            transport=Mock(source_kind='live_test',credentials=Credentials('SYNTHETIC','SYNTHETIC','real'))
            transport.fetch.return_value=Capture(body,{'cont-yn':'N'},200,(at+timedelta(seconds=1)).isoformat(),(at+timedelta(seconds=2)).isoformat())
            turnover=root/'turnover.db';collect(turnover,transport,Profile(),'real',1)
            workflow=Workflow(root,{'telegram_send_enabled':False},clock=lambda:at+timedelta(seconds=50))
            with patch('kiwoom_collector.operating_workflow.online_snapshot.capture_snapshot',return_value={'status':'success','snapshot_path':str(snapshot)}):
                result=workflow.review(Mock(path=root/'data/stock1-real.db'),at,turnover)
            self.assertEqual(result['delivery']['status'],'disabled')
            self.assertEqual(result['decision']['candidates'],[])
            saved=json.loads((root/'reports/c12x-20261102-1500/telegram.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['selection_type'],'PM_CLOSING_REVIEW')
            self.assertEqual(len(saved['observation']['stocks']),20)

if __name__=='__main__':unittest.main()
