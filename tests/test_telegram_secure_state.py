"""Configuration/protected storage/shared reservations; synthetic data only."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,Mock,patch
from copy import deepcopy

from kiwoom_collector import telegram_briefing as tg
from test_telegram_observation import observation
from test_c12x_workflow import empty_morning

TOKEN='123456:'+'x'*30
CHAT='987654321'

class SecureStateTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.enterContext(patch.dict(os.environ,{'LOCALAPPDATA':str(self.root),
            'ARVIS_TELEGRAM_BOT_TOKEN':'','ARVIS_TELEGRAM_CHAT_ID':'','ARVIS_TELEGRAM_ALLOWED_CHAT_IDS':''}))
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))
        for k,v in [('PROJECT',self.root),('REPORTS',self.root/'reports'),('STATE',self.root/'notification_state')]:
            self.enterContext(patch.object(tg,k,v))
        tg.Configuration.protected_path().parent.mkdir(parents=True)
        self.config=tg.Configuration(TOKEN,CHAT,(CHAT,))
        self.session=MagicMock();self.session.__enter__.return_value=self.session
        self.session.post.return_value=Mock(status_code=200,json=Mock(return_value=dict(ok=True,
            result=dict(message_id=1,chat=dict(id=int(CHAT),type='private')))))

    def test_protected_store_roundtrip_never_plaintext_and_preserves_existing(self):
        payload=[]
        protector=Mock()
        protector.protect.side_effect=lambda value:payload.append(value) or b'OPAQUE_TEST_CIPHERTEXT'
        protector.unprotect.side_effect=lambda value:payload[0] if value==b'OPAQUE_TEST_CIPHERTEXT' else None
        with patch('kiwoom_collector.token_cache.WindowsDPAPI',return_value=protector):
            self.config.save_protected()
            raw=tg.Configuration.protected_path().read_bytes();self.assertNotIn(TOKEN.encode(),raw)
            restored=tg.Configuration.from_environment();self.assertEqual(restored,self.config)
            with self.assertRaises(ValueError):self.config.save_protected()
            self.assertEqual(tg.Configuration.protected_path().read_bytes(),raw)

    def test_partial_environment_does_not_mix_with_protected_settings(self):
        tg.Configuration.protected_path().write_bytes(b'OPAQUE')
        with patch.dict(os.environ,{'ARVIS_TELEGRAM_CHAT_ID':CHAT}):
            with self.assertRaisesRegex(ValueError,'configuration_required'):tg.Configuration.from_environment()

    def test_corrupt_protected_file_fails_without_plaintext_fallback(self):
        tg.Configuration.protected_path().write_bytes(b'OPAQUE')
        with patch('kiwoom_collector.token_cache.WindowsDPAPI',side_effect=RuntimeError('SYNTHETIC_PRIVATE')):
            with self.assertRaisesRegex(ValueError,'secure_configuration_unreadable'):tg.Configuration.from_environment()

    def test_shared_unknown_reservation_blocks_across_deployments(self):
        shared=self.root/'shared/notification_state';shared.mkdir(parents=True)
        report=observation()
        key=tg.digest([report['trade_date'],report['selection_type'],tg.delivery_schema(report),'real','KRX',tg.hashlib.sha256(CHAT.encode()).hexdigest()])
        path=shared/(key+'.json');raw=b'{"version":"telegram-delivery-v1","status":"reserved","message_sent":false}'
        path.write_bytes(raw)
        result=tg.deliver(report,tg.summary(report),None,self.config,session_factory=Mock(),
            allow_readonly_report=True,state_root=shared)
        self.assertEqual(result['status'],'duplicate_blocked');self.assertEqual(path.read_bytes(),raw)
        self.assertFalse(tg.STATE.exists())

    def test_revision_requires_confirmed_previous_send_and_actual_selection_change(self):
        first=empty_morning('09:40')
        kwargs=dict(allow_readonly_report=True,session_factory=lambda:self.session)
        self.assertEqual(tg.deliver(first,tg.summary(first),None,self.config,decision_revision=1,**kwargs)['status'],'sent')
        with self.assertRaisesRegex(ValueError,'important_selection_change_required'):
            tg.deliver(empty_morning('09:45'),tg.summary(empty_morning('09:45')),None,self.config,decision_revision=2,**kwargs)
        # An unknown first result must never be bypassed via a revision key.
        path=next(tg.STATE.glob('*.json'));state=json.loads(path.read_text());state['status']='failed_or_unknown'
        path.write_text(json.dumps(state))
        second=empty_morning('09:45')
        with self.assertRaisesRegex(ValueError,'previous_delivery_unconfirmed'):
            tg.deliver(second,tg.summary(second),None,self.config,decision_revision=2,**kwargs)
        self.assertEqual(self.session.post.call_count,1)

    def test_confirmed_important_change_uses_one_new_reservation_only(self):
        kwargs=dict(allow_readonly_report=True,session_factory=lambda:self.session)
        first=empty_morning('09:40');second=empty_morning('09:45')
        with patch('kiwoom_collector.c12_chart_bridge.published_top',return_value=[{'stock_code':'005930'}]):
            self.assertEqual(tg.deliver(first,tg.summary(first),None,self.config,decision_revision=1,**kwargs)['status'],'sent')
        with patch('kiwoom_collector.c12_chart_bridge.published_top',return_value=[{'stock_code':'000660'}]):
            self.assertEqual(tg.deliver(second,tg.summary(second),None,self.config,decision_revision=2,**kwargs)['status'],'sent')
            self.assertEqual(tg.deliver(second,tg.summary(second),None,self.config,decision_revision=2,**kwargs)['status'],'duplicate_blocked')
        self.assertEqual(self.session.post.call_count,2)

if __name__=='__main__':unittest.main()
