"""C15-12 saved Workflow integration; transport boundary always blocked."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import c15_delivery as adapter, telegram_briefing as tg
from kiwoom_collector import c15_briefing_data as data
from kiwoom_collector.operating_workflow import Workflow
from kiwoom_collector.am_time_policy import instant

ROOT=Path(__file__).resolve().parents[1]


class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(dir=ROOT/'diagnostics')
        cls.root=Path(cls.temp.name)
        cls.settings=dict(c15_briefing_enabled=True,c15_preview_config=str(ROOT/'config/c15-preview.json'))
        cls.reports={}
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),patch.object(tg.Configuration,'from_environment',side_effect=AssertionError('credentials forbidden')):
            w=Workflow(cls.root,cls.settings)
            for slot in adapter.SLOTS:
                due=instant('2026-11-05T'+slot+':00+09:00')
                result=w.morning_review(None,due,None)
                cls.reports[slot]=json.loads((Path(result['report'])/'telegram.json').read_text(encoding='utf8'))

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_contract_three_slots(self):
        for r in self.reports.values():
            self.assertEqual(tg.validate(r),r)
            self.assertEqual(tg.summary(r),r['briefing']['text'])

    def test_fallback(self):
        self.assertIn('09:40 (5분 전 대체)',tg.summary(self.reports['09:45']))

    def test_tampering(self):
        r=deepcopy(self.reports['09:40']);r['briefing']['text']+='tampered'
        with self.assertRaises(ValueError):tg.validate(r)

    def test_frozen_replay(self):
        w=Workflow(self.root,self.settings)
        with patch.object(adapter.c15_preview,'generate',side_effect=AssertionError('must reuse frozen')):
            result=w.morning_review(None,instant(self.reports['09:40']['selection_cutoff_at']),None)
        self.assertEqual(result['delivery']['status'],'frozen_replay_no_send')

    def test_transport_boundary_and_duplicate_each_slot(self):
        state=self.root/'boundary'/'notification_state';state.mkdir(parents=True,exist_ok=True)
        config=tg.Configuration('123456:'+('a'*24),'1234567',('1234567',))
        with patch.object(tg,'api_call',side_effect=RuntimeError('simulated stop before HTTP')) as call,patch('socket.socket.connect',side_effect=AssertionError('no network')):
            for r in self.reports.values():
                args=(r,tg.summary(r),None,config)
                first=tg.deliver(*args,allow_readonly_report=True,state_root=state)
                self.assertEqual(first['status'],'failed_or_unknown')
                self.assertEqual(tg.deliver(*args,allow_readonly_report=True,state_root=state)['status'],'duplicate_blocked')
            self.assertEqual(call.call_count,3)

    def test_recipient_and_readonly_gates(self):
        r=self.reports['09:40'];bad=tg.Configuration('123456:'+('a'*24),'1234567',())
        with self.assertRaises(ValueError):tg.deliver(r,tg.summary(r),None,bad,allow_readonly_report=True)
        with self.assertRaises(ValueError):tg.deliver(r,tg.summary(r),None,bad)

    def test_legacy_reservation_blocks_all_c15_slots(self):
        import hashlib
        state=self.root/'legacy'/'notification_state';state.mkdir(parents=True,exist_ok=True)
        config=tg.Configuration('123456:'+('a'*24),'1234567',('1234567',))
        r=self.reports['09:40']
        scope=[r['trade_date'],r['selection_type'],tg.delivery_schema(r),r['environment'],r['market'],hashlib.sha256(config.chat_id.encode()).hexdigest()]
        (state/(tg.digest(scope)+'.json')).write_text('{}',encoding='utf8')
        with patch.object(tg,'api_call',side_effect=AssertionError('must not reach transport')):
            for r in self.reports.values():
                self.assertEqual(tg.deliver(r,tg.summary(r),None,config,allow_readonly_report=True,state_root=state)['status'],'duplicate_blocked')

    def test_preflight_uses_same_slot_reservation(self):
        import hashlib
        state=self.root/'preflight'/'notification_state';state.mkdir(parents=True,exist_ok=True)
        config=tg.Configuration('123456:'+('a'*24),'1234567',('1234567',))
        r=self.reports['09:40'];path=self.root/'reports/c15-20261105-0940/telegram.json'
        scope=[r['trade_date'],r['selection_type'],tg.delivery_schema(r),r['environment'],r['market'],hashlib.sha256(config.chat_id.encode()).hexdigest(),'c15-slot','09:40']
        (state/(tg.digest(scope)+'.json')).write_text('{}',encoding='utf8')
        with patch.object(tg.Configuration,'from_environment',return_value=config),patch.object(tg,'prepare',return_value=(r,tg.summary(r),None)):
            self.assertEqual(tg.preflight(path,state_root=state)['status'],'duplicate_blocked')

    def test_practical_names_not_official_claim(self):
        for name in ('KODEX 200','TIGER 반도체','삼성 ETN','회사리츠','회사스팩'):
            d=data.practical_product(dict(name=name),dict(verified=False,excluded=False))
            self.assertTrue(d['excluded']);self.assertFalse(d['verified'])
            self.assertIn('exclusion_basis',d)
        self.assertFalse(data.practical_product(dict(name='일반회사'),dict(verified=False,excluded=False))['excluded'])

    def test_recent_capital(self):
        for day,expected in [('2026-11-03','100'),('2026-11-04','100'),('2026-10-30',None)]:
            self.assertEqual(data.recent_capital(dict(won='100',basis_date=day),'2026-11-05T09:40:00+09:00')['won'],expected)
