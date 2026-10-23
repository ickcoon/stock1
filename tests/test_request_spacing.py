"""Check HTTP call starts with fake transport, including early wakeups and auth."""
from datetime import datetime, timedelta
import math
import time
from unittest import TestCase
from unittest.mock import patch

import requests

from clock_helpers import request_clock
from test_client import response, rank_response, token_response
from test_quotes import body
from test_batch_quotes import row, reply
from kiwoom_collector.client import DEFAULT_REQUEST_SPACING, KST, KiwoomClient, Token
from kiwoom_collector.config import Credentials
from kiwoom_collector.quotes import BatchQuoteClient, QuoteClient, QuoteDeadlineReached
from kiwoom_collector.scheduled_quotes import QuotePolicy


class RequestSpacingTests(TestCase):
    def setUp(self):
        self.clock = self.enterContext(request_clock())
        self.session = self.enterContext(patch('kiwoom_collector.client.requests.Session')).return_value
        self.starts = []

        def post(url, **kwargs):
            api = kwargs['headers'].get('api-id', 'auth')
            self.starts.append((self.clock.now(), api))
            if api == 'auth':
                return token_response()
            if api == 'ka00198':
                return rank_response()
            if api == 'ka10007':
                return response(body())
            return reply([row(code) for code in kwargs['json']['stk_cd'].split('|')])

        self.session.post.side_effect = post

    def client(self, environment='mock'):
        client = BatchQuoteClient(Credentials('fake-key', 'fake-secret', environment))
        self.addCleanup(client.close)
        return client

    def assert_gaps(self, minimum):
        gaps = [b[0] - a[0] for a, b in zip(self.starts, self.starts[1:])]
        self.assertTrue(gaps)
        self.assertTrue(all(gap >= minimum - 1e-9 for gap in gaps), gaps)

    def test_default_1_2_shared_by_clients_and_scheduled_policy(self):
        self.assertEqual(DEFAULT_REQUEST_SPACING, 1.2)
        for cls in (KiwoomClient, QuoteClient, BatchQuoteClient):
            self.assertEqual(cls.request_interval, 1.2)
        self.assertEqual(QuotePolicy().spacing_seconds, 1.2)

    def test_auth_ranking_single_and_batch_share_start_clock_in_both_environments(self):
        for environment in ('mock', 'real'):
            with self.subTest(environment=environment):
                self.starts.clear()
                client = self.client(environment)
                client.fetch_once(single_request=True)
                client.fetch_quote('005930')
                client.fetch_quotes(['005930', '000660'])
                client.fetch_once(single_request=True)
                self.assertEqual([api for _, api in self.starts],
                                 ['auth', 'ka00198', 'ka10007', 'ka10095', 'ka00198'])
                self.assert_gaps(1.2)

    def test_short_sleep_rechecks_actual_clock_at_explicit_1_1(self):
        client = self.client()
        client.request_interval = 1.1
        client.fetch_once(single_request=True)
        original_sleep = self.clock.sleep
        wakes = []

        def early_sleep(seconds):
            wakes.append(seconds)
            original_sleep(max(0, seconds - 0.015) if len(wakes) == 1 else seconds)

        with patch('kiwoom_collector.client.time.sleep', side_effect=early_sleep):
            client.fetch_quotes(['005930'])
        self.assertGreaterEqual(len(wakes), 2)
        self.assert_gaps(1.1)

    def test_coarse_monotonic_clock_does_not_control_request_spacing(self):
        with patch('kiwoom_collector.client.time.monotonic', return_value=0) as coarse:
            client = self.client()
            client.fetch_once(single_request=True)
            client.fetch_quotes(['005930'])
        coarse.assert_not_called()
        self.assert_gaps(1.2)

    def test_oversleep_sets_next_deadline_from_actual_previous_start(self):
        client = self.client()

        def oversleep(seconds):
            self.clock.seconds += seconds + 0.025

        with patch('kiwoom_collector.client.time.sleep', side_effect=oversleep):
            client.fetch_once(single_request=True)
            client.fetch_quotes(['005930'])
        self.assert_gaps(1.2)

    def test_slow_response_does_not_add_unnecessary_full_sleep(self):
        client = self.client()
        client.fetch_once(single_request=True)
        self.clock.seconds += 2
        old_sleeps = len(self.clock.sleeps)
        client.fetch_quotes(['005930'])
        self.assertEqual(len(self.clock.sleeps), old_sleeps)
        self.assert_gaps(1.2)

    def test_existing_bounded_retry_still_respects_start_interval(self):
        client = self.client()
        calls = []

        def post(*args, **kwargs):
            calls.append(self.clock.now())
            if len(calls) == 1:
                raise requests.Timeout()
            return token_response()

        self.session.post.side_effect = post
        client.authenticate(attempts=2)
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(calls[1] - calls[0], 1.2 - 1e-9)

    def test_deadline_after_pacing_sends_no_http_and_keeps_previous_start(self):
        client = self.client()
        client.token = Token('fake-token', datetime.now(KST) + timedelta(hours=1))
        client.fetch_quotes(['005930'])
        old_start = client._last_request

        def check(budget):
            if budget == 30 and self.clock.now() > old_start:
                self.assertGreaterEqual(self.clock.now() - old_start, 1.2 - 1e-9)
                raise QuoteDeadlineReached('fixture deadline after pacing')

        with patch.object(client, '_check_deadline', side_effect=check):
            with self.assertRaises(QuoteDeadlineReached):
                client.fetch_quotes(['000660'])
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(client._last_request, old_start)

    def test_invalid_spacing_never_sends_http(self):
        for value in (0, 1, math.nan, math.inf):
            client = self.client()
            client.request_interval = value
            with self.assertRaises(ValueError):
                client.fetch_once(single_request=True)
        self.session.post.assert_not_called()


class RealClockSpacingTests(TestCase):
    def test_real_perf_counter_at_fake_transport_boundary(self):
        # Real Windows timer and sleep; fake HTTP only. Verify the original 1.1s floor.
        starts = []
        with patch('kiwoom_collector.client.requests.Session') as factory:
            def post(*args, **kwargs):
                starts.append(time.perf_counter())
                return token_response() if 'api-id' not in kwargs['headers'] else rank_response()
            factory.return_value.post.side_effect = post
            client = KiwoomClient(Credentials('fake-key', 'fake-secret'))
            try:
                client.fetch_once(single_request=True)
                client.fetch_once(single_request=True)
            finally:
                client.close()
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(b - a >= 1.1 for a, b in zip(starts, starts[1:])))
