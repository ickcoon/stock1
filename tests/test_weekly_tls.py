"""Scoped TLS/C13 tests; synthetic token and no external network."""
import os
import ssl
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
import requests
from requests.adapters import HTTPAdapter
from kiwoom_collector.weekly_tls import WindowsTrustAdapter,configure_windows_trust
from kiwoom_collector.weekly_probe_cli import WeeklyHTTPTransport
from kiwoom_collector.weekly_chart import WeeklyRequest
from kiwoom_collector.client import KST
from kiwoom_collector.config import Credentials


class WeeklyTLSTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def test_unsupported_platform_fails_closed(self):
        with patch('kiwoom_collector.weekly_tls.os.name','posix'),self.assertRaises(ValueError):
            WindowsTrustAdapter()

    @unittest.skipUnless(os.name=='nt','Windows native store required')
    def test_session_scoped_context_required_and_global_ssl_unchanged(self):
        original=ssl.SSLContext
        with requests.Session() as weekly,requests.Session() as other:
            adapter=configure_windows_trust(weekly)
            self.assertIs(ssl.SSLContext,original)
            self.assertIs(type(other.get_adapter('https://api.kiwoom.com/')),HTTPAdapter)
            self.assertTrue(weekly.verify);self.assertFalse(weekly.trust_env)
            self.assertEqual(adapter.context.verify_mode,ssl.CERT_REQUIRED)
            self.assertTrue(adapter.context.check_hostname)
            request=requests.Request('HEAD','https://api.kiwoom.com/').prepare()
            _,options=adapter.build_connection_pool_key_attributes(request,True)
            self.assertIs(options['ssl_context'],adapter.context)
            for invalid in (False,None,'other-ca.pem'):
                with self.assertRaises(ValueError):adapter.build_connection_pool_key_attributes(request,invalid)
            self.assertEqual(adapter.max_retries.total,0)

    @unittest.skipUnless(os.name=='nt','Windows native store required')
    def test_weekly_c13_reuses_synthetic_token_without_oauth(self):
        expiry=(datetime.now(KST)+timedelta(hours=1)).isoformat()
        transport=WeeklyHTTPTransport(Credentials('SYNTHETIC_APP','SYNTHETIC_SECRET','mock'),
            WeeklyRequest('ka10082','005930','mock','20261030','1'),'SYNTHETIC_TOKEN',expiry,
            approved=True,windows_trust_store=True)
        self.addCleanup(transport.close)
        self.assertIsInstance(transport.client.session.get_adapter('https://api.kiwoom.com/'),WindowsTrustAdapter)
        self.assertEqual(transport.manager.get_token(attempts=1).value,'SYNTHETIC_TOKEN')
        self.assertIsNone(transport.manager.cache)
