"""C15-13 no-send defaults, configurable slots and restart expiry."""
from datetime import timedelta
import unittest
from kiwoom_collector.c15_delivery import send_policy
from kiwoom_collector.am_time_policy import instant


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.at=instant('2026-11-05T09:40:00+09:00')
        self.on=dict(c15_send_enabled=True,telegram_send_enabled=True)

    def test_default_off(self):self.assertEqual(send_policy({},self.at,self.at),'disabled')
    def test_existing_send_switch_insufficient(self):self.assertEqual(send_policy(dict(telegram_send_enabled=True),self.at,self.at),'disabled')
    def test_0940_ready(self):self.assertEqual(send_policy(self.on,self.at,self.at+timedelta(seconds=30)),'ready')
    def test_0950_ready(self):
        at=self.at+timedelta(minutes=10)
        self.assertEqual(send_policy(self.on,at,at),'ready')
    def test_0945_record_only(self):
        at=self.at+timedelta(minutes=5)
        self.assertEqual(send_policy(self.on,at,at),'record_only')
    def test_slots_configurable(self):
        at=self.at+timedelta(minutes=5)
        self.assertEqual(send_policy(dict(self.on,c15_send_slots=['09:45']),at,at),'ready')
    def test_boundary(self):self.assertEqual(send_policy(self.on,self.at,self.at+timedelta(seconds=120)),'ready')
    def test_expired(self):self.assertEqual(send_policy(self.on,self.at,self.at+timedelta(seconds=121)),'expired_no_catchup')
    def test_other_day(self):self.assertEqual(send_policy(self.on,self.at,self.at+timedelta(days=1)),'expired_no_catchup')
    def test_future(self):self.assertEqual(send_policy(self.on,self.at,self.at-timedelta(seconds=1)),'expired_no_catchup')
    def test_frozen_restart_never_sends(self):self.assertEqual(send_policy(self.on,self.at,self.at,replay=True),'frozen_replay_no_send')
    def test_bad_configuration(self):
        for values in (dict(c15_send_slots=['09:40','09:40']),dict(c15_send_slots=['09:41']),dict(c15_send_max_lag_seconds=301),dict(c15_send_max_lag_seconds=True)):
            with self.assertRaises(ValueError):send_policy(dict(self.on,**values),self.at,self.at)
