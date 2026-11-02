"""Live-report contracts with synthetic strings/fake HTTP, no real credentials."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from kiwoom_collector import am_briefing, am_momentum as am, telegram_briefing as tg

def observation():
    analysis=am_briefing.analyze(dict(schema_version=am.ADAPTER_INPUT_SCHEMA,
        source_kind='readonly_adapter',trade_date='2026-11-02',environment='real',market='KRX',
        selection_cutoff_at='2026-11-02T09:50:00+09:00',rankings=[],securities={}),
        generated_at='2026-11-02T12:35:00+09:00')
    report=dict(schema_version=tg.OBSERVATION_SCHEMA,selection_type='OBSERVATION_REVIEW',
        source_kind='readonly_adapter',trade_date='2026-11-02',selection_cutoff_at='2026-11-02T12:32:36+09:00',
        generated_at='2026-11-02T12:35:00+09:00',environment='real',market='KRX',candidate_confirmed=False,
        d0_storage_allowed=False,analysis=analysis,
        observation=dict(ranking_at='2026-11-02T12:32:36+09:00',turnover_received_at='2026-11-02T12:32:37+09:00',
            turnover_rows=100,pagination_complete=False,stocks=[dict(stock_code='005930',stock_name='SYNTHETIC_STOCK',
                rank=1,change_percent='1.25',turnover_million_krw='12345')]),
        step6d=dict(verified=0,held=1),charts=dict(available=0,unavailable=1))
    return rehash(report)

def rehash(report):
    report['evaluation_reference']=tg.digest({k:v for k,v in report.items() if k!='evaluation_reference'})
    return report

class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        for k,v in [('PROJECT',root),('REPORTS',root/'reports'),('STATE',root/'notification_state')]:
            self.enterContext(patch.object(tg,k,v))
        self.report=observation()
        self.config=tg.Configuration('123456:'+'x'*30,'987654321',('987654321',))
        self.session=MagicMock();self.session.__enter__.return_value=self.session
        self.session.post.return_value=Mock(status_code=200,json=Mock(return_value=dict(ok=True,
            result=dict(message_id=1,chat=dict(id=987654321,type='private')))))
        self.factory=Mock(return_value=self.session)

    def send(self,report=None,**kwargs):
        report=report or self.report
        return tg.deliver(report,tg.summary(report),None,self.config,session_factory=self.factory,**kwargs)

    def test_preview_preserves_observation_and_missing_selection(self):
        text=tg.summary(self.report)
        for fragment in ('provisional','12:32:36','partial','1.25%','12345','Top5: 보류','09:50','정기 자동 보고 미활성'):
            self.assertIn(fragment,text)
        self.assertIn('미확보',text)
        self.assertLess(len(text.encode('utf-16-le'))//2,4096)

    def test_default_live_send_still_rejected_before_reservation(self):
        with self.assertRaisesRegex(ValueError,'readonly_report_not_deliverable'):self.send()
        self.factory.assert_not_called();self.assertFalse(tg.STATE.exists())

    def test_explicit_live_option_uses_existing_single_send_and_duplicate_guard(self):
        self.assertEqual(self.send(allow_readonly_report=True)['status'],'sent')
        self.assertEqual(self.send(allow_readonly_report=True)['status'],'duplicate_blocked')
        self.assertEqual(self.session.post.call_count,1)

    def test_unknown_delivery_keeps_reservation_no_retry(self):
        import requests
        self.session.post.side_effect=requests.Timeout('SYNTHETIC_PRIVATE_ERROR')
        first=self.send(allow_readonly_report=True)
        self.assertEqual(first['status'],'failed_or_unknown')
        self.assertNotIn('SYNTHETIC_PRIVATE_ERROR',str(first))
        self.assertEqual(self.send(allow_readonly_report=True)['status'],'duplicate_blocked')
        self.assertEqual(self.session.post.call_count,1)

    def test_optional_native_tls_is_session_scoped(self):
        with patch('kiwoom_collector.weekly_tls.configure_windows_trust') as configure:
            self.assertEqual(self.send(allow_readonly_report=True,windows_trust_store=True)['status'],'sent')
        configure.assert_called_once_with(self.session)

    def test_unverified_metrics_may_be_missing_but_not_fabricated(self):
        self.report['observation']['stocks'][0].update(change_percent=None,turnover_million_krw=None)
        text=tg.summary(rehash(self.report))
        self.assertIn('등락률 미확인',text);self.assertIn('거래대금 미확인',text)

    def test_rejects_scope_duplicates_promotion_and_tampering(self):
        variants=[]
        a=deepcopy(self.report);a['environment']='mock';variants.append(a)
        a=deepcopy(self.report);a['observation']['stocks']*=2;variants.append(a)
        a=deepcopy(self.report);a['step6d']['verified']=1;variants.append(a)
        a=deepcopy(self.report);a['charts']['available']=1;variants.append(a)
        a=deepcopy(self.report);a['candidate_confirmed']=True;variants.append(a)
        a=deepcopy(self.report);a['analysis']['sectors']=[{}];variants.append(a)
        for value in variants:
            with self.assertRaises((ValueError,KeyError,TypeError)):tg.validate(rehash(value))
        self.report['observation']['stocks'][0]['change_percent']='3.25'
        with self.assertRaisesRegex(ValueError,'report_fingerprint_mismatch'):tg.validate(self.report)

    def test_secret_and_path_content_rejected(self):
        self.report['observation']['stocks'][0]['stock_name']='C:\\private\\secret.txt'
        with self.assertRaises(ValueError):tg.validate(rehash(self.report))

if __name__=='__main__':unittest.main()
