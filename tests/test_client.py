from contextlib import nullcontext
from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import Mock, patch
import requests
from clock_helpers import request_clock

from kiwoom_collector.client import KiwoomClient, KiwoomError, KST
from kiwoom_collector.config import Credentials
from kiwoom_collector.__main__ import main


def response(data=None, status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = data
    return result


def token_response():
    return response({"return_code": 0, "token": "test-only-token", "token_type": "bearer",
                     "expires_dt": (datetime.now(KST) + timedelta(hours=1)).strftime("%Y%m%d%H%M%S")})


def rank_response(rows=None, headers=None):
    return response({"return_code": 0, "item_inq_rank": [] if rows is None else rows}, headers=headers)


class ClientTests(TestCase):
    def setUp(self):
        # Network is replaced before any client is constructed.
        self.session_patch = patch("kiwoom_collector.client.requests.Session")
        self.session = self.session_patch.start().return_value
        self.addCleanup(self.session_patch.stop)
        self.request_clock = self.enterContext(request_clock())
        self.client = KiwoomClient(Credentials("test-key", "test-secret"))
        self.addCleanup(self.client.close)

    def test_auth_and_single_query_contract(self):
        row = {"bigd_rank": "1", "stk_cd": "039490", "stk_nm": "테스트", "base_comp_chgr": "+0.57"}
        self.session.post.side_effect = [token_response(), rank_response([row])]
        result = self.client.fetch_once()
        self.assertEqual(result.records, [row])
        self.assertEqual(result.collected_at.utcoffset(), timedelta(hours=9))
        auth, query = self.session.post.call_args_list
        self.assertEqual(auth.args[0], "https://mockapi.kiwoom.com/oauth2/token")
        self.assertEqual(auth.kwargs['json'], {"grant_type": "client_credentials", "appkey": "test-key", "secretkey": "test-secret"})
        self.assertNotIn('authorization', auth.kwargs['headers'])
        self.assertFalse(auth.kwargs['allow_redirects'])
        self.assertEqual(query.args[0], "https://mockapi.kiwoom.com/api/dostk/stkinfo")
        self.assertEqual(query.kwargs['json'], {"qry_tp": "1"})
        self.assertEqual(query.kwargs['headers']['api-id'], 'ka00198')
        self.assertEqual(query.kwargs['headers']['authorization'], 'Bearer test-only-token')
        self.assertNotIn('test-only-token', repr(self.client.token))

    def test_top_twenty_uses_actual_rank_not_position(self):
        rows = [{"bigd_rank": str(i), "stk_cd": str(i).zfill(6)} for i in range(25, 0, -1)]
        self.session.post.side_effect = [token_response(), rank_response(rows)]
        result = self.client.fetch_once()
        self.assertEqual([r['bigd_rank'] for r in result.records], [str(i) for i in range(1, 21)])
        self.assertNotIn('stk_nm', result.records[0])

    def test_continuation(self):
        self.session.post.side_effect = [token_response(), rank_response([{'bigd_rank': '1'}], {'cont-yn': 'Y', 'next-key': 'page2'}), rank_response([{'bigd_rank': '2'}])]
        self.assertEqual(len(self.client.fetch_once().records), 2)
        headers = self.session.post.call_args_list[-1].kwargs['headers']
        self.assertEqual(headers['next-key'], 'page2')
        self.assertEqual(headers['cont-yn'], 'Y')

    def test_auth_recovery_once(self):
        self.session.post.side_effect = [token_response(), response({'return_code': 3, 'return_msg': '[8005:expired]'}), token_response(), rank_response()]
        self.assertEqual(self.client.fetch_once().records, [])
        self.assertEqual(self.session.post.call_count, 4)

    def test_repeated_auth_failure_stops(self):
        self.session.post.side_effect = [token_response(), response({}, 401), token_response(), response({}, 401)]
        with self.assertRaisesRegex(KiwoomError, '재발급 후에도'):
            self.client.fetch_once()
        self.assertEqual(self.session.post.call_count, 4)

    def test_network_retry_bounded(self):
        self.session.post.side_effect = requests.Timeout()
        with self.assertRaisesRegex(KiwoomError, '최대 3회'):
            self.client.authenticate()
        self.assertEqual(self.session.post.call_count, 3)

    def test_rate_limit_retry(self):
        self.session.post.side_effect = [response({}, 429, {'Retry-After': '2'}), token_response()]
        self.client.authenticate()
        self.assertEqual(self.session.post.call_count, 2)

    def test_business_rate_limit_bounded(self):
        self.session.post.return_value = response({'return_code': 1700})
        with self.assertRaisesRegex(KiwoomError, '최대 3회'):
            self.client.authenticate()
        self.assertEqual(self.session.post.call_count, 3)

    def test_tls_verification_not_bypassed(self):
        self.session.post.side_effect = requests.exceptions.SSLError('sensitive details')
        with self.assertRaisesRegex(KiwoomError, 'TLS') as caught:
            self.client.authenticate()
        self.assertNotIn('sensitive', str(caught.exception))
        self.assertEqual(self.session.post.call_count, 1)

    def test_malformed_json(self):
        result = response()
        result.json.side_effect = ValueError()
        self.session.post.return_value = result
        with self.assertRaisesRegex(KiwoomError, 'JSON'):
            self.client.authenticate()

    def test_unknown_rank_does_not_invent_values(self):
        self.session.post.side_effect = [token_response(), rank_response([{'stk_cd': '039490'}])]
        with self.assertRaisesRegex(KiwoomError, 'bigd_rank'):
            self.client.fetch_once()

    def test_zero_rank_requires_confirmation(self):
        self.session.post.side_effect = [token_response(), rank_response([{'bigd_rank': '0'}])]
        with self.assertRaisesRegex(KiwoomError, '의미 확인'):
            self.client.fetch_once()

    def test_repeated_cursor_fails(self):
        page = rank_response([], {'cont-yn': 'Y', 'next-key': 'same'})
        self.session.post.side_effect = [token_response(), page, page]
        with self.assertRaisesRegex(KiwoomError, '반복'):
            self.client.fetch_once()

    def test_business_error_hides_raw_message(self):
        self.session.post.return_value = response({'return_code': 999, 'return_msg': 'test-secret'})
        with self.assertRaises(KiwoomError) as caught:
            self.client.authenticate()
        self.assertIn('999', str(caught.exception))
        self.assertNotIn('test-secret', str(caught.exception))

    def test_default_command_never_calls_network(self):
        with patch('kiwoom_collector.__main__.load_credentials', return_value=Credentials('x', 'y')), patch('builtins.print'):
            self.assertEqual(main([]), 0)
        self.session.post.assert_not_called()

    def test_invalid_query_never_calls_network(self):
        with self.assertRaises(ValueError):
            self.client.fetch_once('6')
        self.session.post.assert_not_called()

    def test_real_auth_and_query_use_same_host(self):
        self.client = KiwoomClient(Credentials("test-key", "test-secret", environment="real"))
        self.session.post.side_effect = [token_response(), rank_response()]
        self.client.fetch_once()
        self.assertEqual([call.args[0] for call in self.session.post.call_args_list], [
            "https://api.kiwoom.com/oauth2/token",
            "https://api.kiwoom.com/api/dostk/stkinfo",
        ])

    def test_cli_commands_preserve_environment_and_hide_secrets(self):
        for environment in ("mock", "real"):
            for command in ("check-config", "auth", "once"):
                with self.subTest(environment=environment, command=command):
                    self.session.post.reset_mock()
                    self.session.post.side_effect = [token_response(), rank_response()]
                    log_capture = self.assertLogs("kiwoom_collector.client", level="INFO") if command != "check-config" else nullcontext()
                    with patch("kiwoom_collector.__main__.load_credentials", return_value=Credentials("test-key", "test-secret", environment)), patch("builtins.print") as output, log_capture as logs:
                        self.assertEqual(main([command]), 0)
                        text = str(output.call_args_list) + (str(logs.output) if command != "check-config" else "")
                        self.assertIn(environment, text)
                        for secret in ("test-key", "test-secret", "test-only-token"):
                            self.assertNotIn(secret, text)
                    if command == "check-config":
                        self.session.post.assert_not_called()
                    else:
                        host = "mockapi" if environment == "mock" else "api"
                        self.assertTrue(all(call.args[0].startswith(f"https://{host}.kiwoom.com/") for call in self.session.post.call_args_list))
