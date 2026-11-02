"""Synthetic C12-AC integration; no credentials, live DB or external requests."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from kiwoom_collector import morning_briefing as mb, am_briefing as ab, telegram_briefing as tg
from kiwoom_collector.am_candidate_adapter import join
from kiwoom_collector.operating_workflow import Workflow
from test_equity_candidates import sample

FIXTURE = Path(__file__).parent/'fixtures/am_sector_briefing_synthetic.json'


def document(slot='09:50'):
    from kiwoom_collector.am_time_policy import ObservationPolicy
    doc = json.loads(FIXTURE.read_text(encoding='utf-8'))
    doc['observation_policy']=ObservationPolicy().document()
    doc['selection_cutoff_at'] = f'2026-10-29T{slot}:00+09:00'
    for sec in doc['securities'].values():
        for values in sec.values():
            if isinstance(values,list):
                for value in values:
                    for key in ('observed_at','received_at'):
                        if value.get(key,'') > doc['selection_cutoff_at']:
                            value[key]=doc['selection_cutoff_at']
    rows = [sample(f'{n:06}') for n in range(1,21)]
    for row in rows:
        row['product_source'].update(updated_at='2026-10-28T16:00:00+09:00',applied_at='2026-10-28T16:01:00+09:00')
    return join(doc,dict(at=doc['selection_cutoff_at'],synthetic=True,records=rows,provenance={}))


class MorningTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workflow = Workflow(self.root,{},clock=lambda:datetime.fromisoformat('2026-10-29T09:51:00+09:00'))
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def process(self,doc):
        return mb.process(self.workflow,datetime.fromisoformat(doc['selection_cutoff_at']),doc)

    def report(self,result):
        return json.loads((Path(result['report'])/'telegram.json').read_text(encoding='utf-8'))

    def test_top3_mobile_and_engine_identity(self):
        doc=document();result=self.process(doc);report=self.report(result)
        self.assertEqual(len(report['sectors']),3)
        self.assertEqual([len(s['leaders']) for s in report['sectors']],[3,3,3])
        self.assertEqual(report['sectors'],ab.analyze(doc,generated_at=self.workflow.clock().isoformat())['sectors'])
        text=tg.summary(report)
        for fragment in ['[ARVIS 오전 주도주 | 09:50]','1,800억','[판정] 잠정','합성 테스트']:
            self.assertIn(fragment,text)
        self.assertFalse(report['candidate_confirmed'])

    def test_maintained_candidates_single_final_send(self):
        self.workflow.settings['telegram_send_enabled']=True
        self.workflow.settings['notification_state']=self.root/'notification_state'
        with patch.object(tg.Configuration,'from_environment',return_value=Mock()),patch.object(tg,'deliver',return_value={'status':'sent'}) as send:
            with patch('kiwoom_collector.operating_workflow.online_snapshot.capture_snapshot',return_value={'status':'success','snapshot_path':'synthetic.db'}),patch('kiwoom_collector.operating_workflow.adapter.adapt') as adapt,patch.object(self.workflow,'review_artifacts',return_value=(None,None,None,[])) as artifacts:
                for slot in ('09:40','09:45','09:50'):
                    doc=document(slot);adapt.return_value=doc
                    self.workflow.review(Mock(),datetime.fromisoformat(doc['selection_cutoff_at']),None)
                    self.assertEqual(send.call_count,int(slot=='09:50'))
                self.assertEqual(artifacts.call_count,3)
            self.assertNotIn('decision_revision',send.call_args.kwargs)
        states=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((self.root/'reports/decisions/2026-10-29').glob('*.json'))]
        self.assertEqual(states[-1]['phases'],['09:40','09:45','09:50'])
        self.assertTrue(states[-1]['closed'])
        self.assertTrue(states[0]['selected'])
        self.assertEqual(states[0]['selected'],states[-1]['selected'])

    def test_candidate_change_reuses_state_without_early_delivery(self):
        from kiwoom_collector import session_review
        state=None
        for slot,codes in [('09:40',['000001']),('09:45',['000002']),('09:50',['000002'])]:
            with patch('kiwoom_collector.c12_chart_bridge.published_top',return_value=[{'stock_code':c} for c in codes]):
                report=ab.analyze(mb.freeze(document(slot)),generated_at=self.workflow.clock().isoformat())
                state,event=session_review.morning_transition(report,slot,state)
        self.assertEqual(state['selected'],['000002']);self.assertEqual(state['revision'],2)

    def test_actual_0945_evidence_change_removes_leader_without_early_send(self):
        self.process(document('09:40'))
        before=json.loads((self.root/'reports/decisions/2026-10-29/0940.json').read_text(encoding='utf-8'))
        self.assertIn('000001',before['selected'])
        doc=document('09:45')
        for metric in doc['securities']['000001']['change_pct']:metric['value']='-1'
        result=self.process(doc)
        after=json.loads((self.root/'reports/decisions/2026-10-29/0945.json').read_text(encoding='utf-8'))
        self.assertNotIn('000001',after['selected'])
        self.assertEqual(result['delivery']['status'],'preview_only')

    def test_missing_turnover_holds_without_fabrication(self):
        doc=document()
        for sec in doc['securities'].values():sec['turnover']=[]
        report=self.report(self.process(doc))
        self.assertEqual(report['sectors'],[])
        self.assertIn('0186 거래대금 미검증/누락',tg.summary(report))
        self.assertIn('관측 종목',tg.summary(report))

    def test_insufficient_companions_hold(self):
        doc=document()
        for code,sec in doc['securities'].items():
            for tag in sec.get('sectors',[]):tag['name']='합성'+code
        report=self.report(self.process(doc))
        self.assertEqual(report['sectors'],[])
        self.assertIn('동반상승 근거 부족',tg.summary(report))

    def test_delayed_final_data_excluded_even_inside_old_grace(self):
        from kiwoom_collector.am_time_policy import ObservationPolicy
        doc=document();doc['observation_policy']=ObservationPolicy().document()
        doc['rankings'][-1]['received_at']='2026-10-29T09:50:01+09:00'
        for sec in doc['securities'].values():
            for metric in sec.get('turnover',[]):
                metric['received_at']='2026-10-29T09:50:01+09:00'
        report=self.report(self.process(doc))
        self.assertEqual(report['sectors'],[])
        self.assertIn('09:50',report['completeness']['missing_slots'])
        self.assertTrue(all(not sec['turnover'] for sec in report['input_document']['securities'].values()))

    def delivery_setup(self):
        self.workflow.settings['telegram_send_enabled']=True
        shared=self.root/'notification_state';shared.mkdir()
        self.workflow.settings['notification_state']=shared
        config=tg.Configuration('123456:'+'x'*30,'987654321',('987654321',))
        self.enterContext(patch.object(tg.Configuration,'from_environment',return_value=config))
        session=MagicMock();session.__enter__.return_value=session
        session.post.return_value=Mock(status_code=200,json=Mock(return_value=dict(ok=True,result=dict(message_id=1,chat=dict(id=987654321,type='private')))))
        self.enterContext(patch.object(tg.requests,'Session',return_value=session))
        self.enterContext(patch('kiwoom_collector.weekly_tls.configure_windows_trust'))
        return session,shared

    def test_duplicate_replay_one_send_and_frozen_report(self):
        session,shared=self.delivery_setup();doc=document()
        first=self.process(doc)
        doc['rankings']=[]
        second=self.process(doc)
        self.assertEqual(first['delivery']['status'],'sent')
        self.assertEqual(second['delivery']['status'],'duplicate_blocked')
        self.assertEqual(session.post.call_count,1)
        self.assertEqual(len(self.report(second)['sectors']),3)
        self.assertEqual(json.loads(next(shared.glob('*.json')).read_text())['status'],'sent')

    def test_failure_retry_not_marked_sent_or_repeated(self):
        session,shared=self.delivery_setup()
        session.post.side_effect=RuntimeError('SYNTHETIC_PRIVATE')
        first=self.process(document());second=self.process(document())
        self.assertEqual(first['delivery']['status'],'failed_or_unknown')
        self.assertEqual(second['delivery']['status'],'duplicate_blocked')
        self.assertEqual(session.post.call_count,1)
        raw=next(shared.glob('*.json')).read_text()
        self.assertNotEqual(json.loads(raw)['status'],'sent')
        self.assertNotIn('SYNTHETIC_PRIVATE',raw)

    def test_disabled_never_reads_credentials_or_sends(self):
        with patch.object(tg.Configuration,'from_environment',side_effect=AssertionError('credentials forbidden')),patch.object(tg,'deliver',side_effect=AssertionError('delivery forbidden')):
            result=self.process(document())
        self.assertEqual(result['delivery']['status'],'disabled')
        self.assertTrue((Path(result['report'])/'telegram.txt').is_file())

    def test_snapshot_failure_creates_held_final(self):
        due=datetime.fromisoformat('2026-10-29T09:50:00+09:00')
        with patch('kiwoom_collector.operating_workflow.online_snapshot.capture_snapshot',return_value={'status':'failed'}):
            result=self.workflow.review(Mock(),due,None)
        self.assertEqual(self.report(result)['sectors'],[])
        self.assertIn('수집 실패',tg.summary(self.report(result)))

    def test_duplicate_source_creates_held_final(self):
        doc=document();doc['rankings'].append(deepcopy(doc['rankings'][0]))
        due=datetime.fromisoformat(doc['selection_cutoff_at'])
        with patch('kiwoom_collector.operating_workflow.online_snapshot.capture_snapshot',return_value={'status':'success','snapshot_path':'synthetic.db'}),patch('kiwoom_collector.operating_workflow.adapter.adapt',return_value=doc):
            result=self.workflow.review(Mock(),due,None)
        self.assertEqual(self.report(result)['sectors'],[])


if __name__=='__main__':unittest.main()
