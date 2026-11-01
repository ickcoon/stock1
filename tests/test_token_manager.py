"""Synthetic clocks/HTTP only. DPAPI tests encrypt synthetic values in temp dirs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import multiprocessing
import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import requests

from kiwoom_collector.client import KiwoomClient, Token, KST
from kiwoom_collector.config import Credentials
from kiwoom_collector.errors import TokenManagementError, TransportError, KiwoomError
from kiwoom_collector.token_manager import TokenManager
from kiwoom_collector.token_cache import ProtectedTokenCache
from kiwoom_collector.catalog_auth import OAuthProvider
from kiwoom_collector.managed_info import ManagedInfoTransport
from kiwoom_collector.turnover_rank import LiveTransport, Profile
from clock_helpers import request_clock

FAKE = 'SYNTHETIC_PRIVATE_TOKEN'


def credentials(environment='mock', secret='SYNTHETIC_SECRET'):
    return Credentials('SYNTHETIC_APP_KEY', secret, environment)


def process_get(root, barrier, count, queue):
    """Spawned independent manager: persisted token/lock must prevent duplicate OAuth."""
    cache = ProtectedTokenCache(credentials(), root=root)
    def issue():
        with count.get_lock():count.value += 1
        time.sleep(0.15)
        return Token(FAKE, datetime.now(KST)+timedelta(hours=1))
    manager = TokenManager(issue, credentials=credentials(), cache=cache)
    try:
        barrier.wait(timeout=5)
        queue.put(manager.get_token().value == FAKE)
    except Exception:
        queue.put(False)


class TokenManagerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 31, 9, tzinfo=KST)
        self.issuer = Mock(side_effect=lambda: Token(FAKE, self.now+timedelta(hours=1)))
        self.sleeps = []
        self.manager = TokenManager(self.issuer, credentials=credentials(), clock=lambda:self.now, sleep=self.sleeps.append)

    def test_first_issue_and_valid_reuse(self):
        first = self.manager.get_token()
        self.now += timedelta(minutes=49)
        self.assertEqual(self.manager.get_token().expires_at, first.expires_at)
        self.assertEqual(self.manager.get_token().value, first.value)
        self.issuer.assert_called_once()
        self.assertNotIn(FAKE, repr(first))

    def test_exact_ten_minute_boundary_and_long_running_refresh(self):
        self.manager.get_token()
        self.now += timedelta(minutes=50)
        self.manager.get_token()
        self.assertEqual(self.issuer.call_count, 2)

    def test_expired_token_refresh(self):
        self.manager.get_token()
        self.now += timedelta(hours=2)
        self.assertGreater(self.manager.get_token().expires_at, self.now)
        self.assertEqual(self.issuer.call_count, 2)

    def test_network_failure_bounded_and_old_token_never_reused(self):
        self.manager.get_token()
        self.now += timedelta(minutes=50)
        self.issuer.side_effect = TransportError('safe',category='network',retryable=True)
        with self.assertRaisesRegex(TokenManagementError,'network'):self.manager.get_token()
        self.assertEqual(self.issuer.call_count,3)
        self.assertEqual(self.sleeps,[1])
        with self.assertRaisesRegex(TokenManagementError,'cooldown'):self.manager.get_token()
        self.assertEqual(self.issuer.call_count,3)
        self.now += timedelta(seconds=60)
        self.issuer.side_effect = lambda:Token(FAKE,self.now+timedelta(hours=1))
        self.manager.get_token()
        self.assertEqual(self.issuer.call_count,4)

    def test_auth_failure_no_retry_and_no_secret_in_logs(self):
        self.issuer.side_effect = KiwoomError(FAKE)
        with self.assertLogs('kiwoom_collector.token_manager',level='WARNING') as logs:
            with self.assertRaises(TokenManagementError) as caught:self.manager.get_token()
        self.assertNotIn(FAKE,str(caught.exception)+repr(logs.output))
        self.issuer.assert_called_once()
        with self.assertRaisesRegex(TokenManagementError,'cooldown'):self.manager.get_token()

    def test_rate_limit_retry_after_is_respected(self):
        self.issuer.side_effect=[TransportError('safe',category='server_or_rate_limit',retryable=True,retry_after=3),
                                Token(FAKE,self.now+timedelta(hours=1))]
        self.manager.get_token()
        self.assertEqual(self.sleeps,[3])
        self.assertEqual(self.issuer.call_count,2)

    def test_short_lifetime_not_replaced_with_invented_24_hours(self):
        self.issuer.side_effect = lambda:Token(FAKE,self.now+timedelta(minutes=5))
        with self.assertRaisesRegex(TokenManagementError,'lifetime'):self.manager.get_token()
        with self.assertRaisesRegex(TokenManagementError,'cooldown'):self.manager.get_token()
        self.issuer.assert_called_once()

    def test_crash_after_issue_marker_prevents_immediate_reissue(self):
        self.issuer.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):self.manager.get_token()
        with self.assertRaisesRegex(TokenManagementError,'cooldown'):self.manager.get_token()
        self.issuer.assert_called_once()

    def test_shared_manager_scope_enforced_and_close_not_owned(self):
        with self.assertRaisesRegex(TokenManagementError,'scope'):
            KiwoomClient(credentials('real'),token_manager=self.manager)
        client = KiwoomClient(credentials(),token_manager=self.manager)
        client.close()
        self.assertEqual(self.manager.get_token().value,FAKE)

    def test_thread_refresh_single_flight(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            values=list(pool.map(lambda _:self.manager.get_token().value,range(4)))
        self.assertEqual(values,[FAKE]*4)
        self.issuer.assert_called_once()


class CacheSecurityContractTests(unittest.TestCase):
    """Failure contracts use no Windows profile or working encryption provider."""
    def test_failed_encryption_stops_before_oauth(self):
        with tempfile.TemporaryDirectory() as folder:
            protector=Mock()
            protector.protect.side_effect=RuntimeError(FAKE)
            issuer=Mock(side_effect=AssertionError('issuer must not run'))
            with patch('kiwoom_collector.token_cache.WindowsDPAPI',
                       side_effect=AssertionError('DPAPI must not be constructed')) as dpapi:
                cache=ProtectedTokenCache(credentials(),root=Path(folder).resolve(),protector=protector)
                manager=TokenManager(issuer,credentials=credentials(),cache=cache)
                with self.assertRaisesRegex(TokenManagementError,'cache_write_failed'):manager.get_token()
            dpapi.assert_not_called()
            protector.protect.assert_called_once()
            issuer.assert_not_called()
            self.assertFalse(cache.path.exists())


@unittest.skipUnless(os.name=='nt','Windows user protection required')
class ProtectedCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.now=datetime.now(KST)

    def manager(self, environment='mock', secret='SYNTHETIC_SECRET'):
        creds=credentials(environment,secret)
        issuer=Mock(return_value=Token(FAKE,self.now+timedelta(hours=1)))
        cache=ProtectedTokenCache(creds,root=self.root)
        return TokenManager(issuer,credentials=creds,cache=cache),issuer,cache

    def test_restart_restores_dpapi_cache_without_oauth(self):
        first,issue,cache=self.manager();first.get_token();first.close()
        second,issue2,_=self.manager()
        self.assertEqual(second.get_token().value,FAKE)
        issue.assert_called_once();issue2.assert_not_called()
        raw=cache.path.read_bytes()
        for value in (FAKE,'SYNTHETIC_SECRET','SYNTHETIC_APP_KEY'):
            self.assertNotIn(value.encode(),raw)

    def test_mock_real_and_secret_rotation_separated(self):
        mock,im,cm=self.manager();real,ir,cr=self.manager('real')
        mock.get_token();real.get_token()
        self.assertNotEqual(cm.path,cr.path)
        im.assert_called_once();ir.assert_called_once()
        rotated,issuer,_=self.manager(secret='ROTATED_SYNTHETIC')
        with self.assertRaisesRegex(TokenManagementError,'scope'):rotated.get_token()
        issuer.assert_not_called()

    def test_different_app_keys_have_independent_cache_and_lock(self):
        first,issuer1,cache1=self.manager();first.get_token()
        other=Credentials('SYNTHETIC_OTHER_APP_KEY','SYNTHETIC_SECRET','mock')
        cache2=ProtectedTokenCache(other,root=self.root)
        issuer2=Mock(return_value=Token('SYNTHETIC_OTHER_TOKEN',self.now+timedelta(hours=1)))
        second=TokenManager(issuer2,credentials=other,cache=cache2)
        self.assertNotEqual(cache1.path,cache2.path)
        self.assertNotEqual(cache1.lock_path,cache2.lock_path)
        second.get_token()
        restored=TokenManager(Mock(side_effect=AssertionError('must reuse')),credentials=other,
                              cache=ProtectedTokenCache(other,root=self.root))
        self.assertEqual(restored.get_token().value,'SYNTHETIC_OTHER_TOKEN')
        issuer1.assert_called_once();issuer2.assert_called_once()

    def test_corrupt_cache_does_not_trigger_unbounded_reissue(self):
        manager,issuer,cache=self.manager();manager.get_token()
        cache.path.write_bytes(b'SYNTHETIC_CORRUPTION')
        for _ in range(2):
            with self.assertRaisesRegex(TokenManagementError,'unreadable'):manager.get_token()
        self.assertEqual(issuer.call_count,1)

    def test_cache_failure_cooldown_survives_restart(self):
        first,issuer,_=self.manager()
        issuer.side_effect=KiwoomError(FAKE)
        with self.assertRaises(TokenManagementError):first.get_token()
        second,issuer2,_=self.manager()
        with self.assertRaisesRegex(TokenManagementError,'cooldown'):second.get_token()
        issuer2.assert_not_called()

    def test_invalidation_compare_and_swap_preserves_newer_token(self):
        manager,issuer,cache=self.manager();manager.get_token()
        with cache.locked():cache.save(dict(token='SYNTHETIC_NEW_TOKEN',expires_at=(self.now+timedelta(hours=2)).isoformat()))
        manager.invalidate(FAKE)
        self.assertEqual(manager.get_token().value,'SYNTHETIC_NEW_TOKEN')
        issuer.assert_called_once()

    def test_two_processes_issue_once(self):
        context=multiprocessing.get_context('spawn')
        count=context.Value('i',0);barrier=context.Barrier(2);queue=context.Queue()
        processes=[context.Process(target=process_get,args=(str(self.root),barrier,count,queue)) for _ in range(2)]
        for p in processes:p.start()
        try:
            for p in processes:p.join(10)
            self.assertEqual([p.exitcode for p in processes],[0,0])
            self.assertTrue(queue.get(timeout=2));self.assertTrue(queue.get(timeout=2))
            self.assertEqual(count.value,1)
        finally:
            for p in processes:
                if p.is_alive():p.terminate();p.join(2)
            queue.close()

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.guard=patch('socket.socket.connect',side_effect=AssertionError('network forbidden'))
        self.guard.start();self.addCleanup(self.guard.stop)
        self.enterContext(request_clock())

    def response(self,data,status=200):
        value=Mock(status_code=status,headers={});value.json.return_value=data
        return value

    def client(self):
        client=KiwoomClient(credentials());client.session.close();client.session=Mock()
        self.addCleanup(client.close)
        return client

    def auth(self):
        return self.response(dict(token=FAKE,token_type='bearer',expires_dt='20990101000000',return_code=0))

    def test_ranking_reuses_token_and_single_auth_recovery_then_stops(self):
        client=self.client()
        client.session.post.side_effect=[self.auth(),self.response(dict(return_code=0,item_inq_rank=[])),
            self.response({},401),self.auth(),self.response({},401)]
        client.fetch_once()
        with self.assertRaisesRegex(KiwoomError,'재발급 후에도'):client.fetch_once()
        self.assertEqual(client.session.post.call_count,5)
        with self.assertRaisesRegex(TokenManagementError,'cooldown'):client.fetch_once()
        self.assertEqual(client.session.post.call_count,5)

    def test_transport_vs_auth_failure_categories(self):
        client=self.client();client.session.post.side_effect=requests.Timeout(FAKE)
        with self.assertRaisesRegex(TokenManagementError,'network'):client.ensure_token()
        self.assertEqual(client.session.post.call_count,2)
        client2=self.client();client2.session.post.return_value=self.response(dict(return_code=8005,return_msg=FAKE),401)
        with self.assertRaisesRegex(TokenManagementError,'authentication_failed'):client2.ensure_token()
        self.assertEqual(client2.session.post.call_count,1)

    def test_actual_oauth_expires_dt_is_preserved_without_24_hour_assumption(self):
        client=self.client()
        expiry=(datetime.now(KST)+timedelta(minutes=37)).replace(microsecond=0)
        client.session.post.return_value=self.response(dict(token=FAKE,token_type='bearer',
            expires_dt=expiry.strftime('%Y%m%d%H%M%S'),return_code=0))
        self.assertEqual(client.ensure_token().expires_at,expiry)
        self.assertEqual(client.ensure_token().expires_at,expiry)
        self.assertEqual(client.session.post.call_count,1)

    def test_managed_oauth_provider_reuses_existing_issuer(self):
        fake=Mock();fake.authenticate.return_value=Token(FAKE,datetime.now(KST)+timedelta(hours=1))
        provider=OAuthProvider(credentials(),approved=True,token_impact_reviewed=True,
                               auto_refresh=True,client_factory=lambda _:fake)
        self.addCleanup(provider.close)
        self.assertEqual(provider.get_token(),FAKE);provider.get_token()
        fake.authenticate.assert_called_once_with(attempts=1)
        provider._manager._state['expires_at']=(datetime.now(KST)+timedelta(minutes=9)).isoformat()
        self.assertEqual(provider.get_token(),FAKE)
        self.assertEqual(provider.oauth_calls,2)

    def test_0186_can_share_manager_without_extra_oauth(self):
        issuer=Mock(return_value=Token(FAKE,datetime.now(KST)+timedelta(hours=1)))
        manager=TokenManager(issuer,credentials=credentials())
        client=LiveTransport(credentials(),token_manager=manager);client.session.close();client.session=Mock()
        self.addCleanup(client.close)
        client.session.post.return_value=self.response(dict(return_code=0,trde_prica_upper=[]))
        for _ in range(2):self.assertEqual(client.fetch(Profile(),{}).http_status,200)
        issuer.assert_called_once()

    def test_ka10001_shared_provider_and_order_endpoint_blocked(self):
        fake=Mock();fake.authenticate.return_value=Token(FAKE,datetime.now(KST)+timedelta(hours=1))
        provider=OAuthProvider(credentials(),approved=True,token_impact_reviewed=True,auto_refresh=True,client_factory=lambda _:fake)
        self.addCleanup(provider.close)
        client=ManagedInfoTransport(credentials(),approved=True,token_provider=provider)
        self.addCleanup(client.close);client.session.close();client.session=Mock()
        client.session.post.return_value=self.response(dict(return_code=0,stk_cd='005930',mac='100'))
        args=dict(api_id='ka10001',path='/api/dostk/stkinfo',body={'stk_cd':'005930'},environment='mock')
        result=client(**args)
        self.assertEqual(result['body']['mac'],'100')
        client(**args);fake.authenticate.assert_called_once()
        with self.assertRaises(ValueError):client(**dict(args,path='/api/dostk/ordr'))
        self.assertEqual(client.session.post.call_count,2)

    def test_auth_failure_records_error_without_changing_previous_success(self):
        from kiwoom_collector.collector import _attempt
        client=self.client();store=Mock()
        client.session.post.side_effect=[self.auth(),self.response(dict(return_code=0,item_inq_rank=[]))]
        self.assertEqual(_attempt(client,store,'previous','1'),0)
        previous=store.finish.call_args
        client._token_manager.invalidate(FAKE,cooldown=True)
        self.assertEqual(_attempt(client,store,'failed','1'),1)
        self.assertEqual(store.finish.call_count,1)
        self.assertEqual(store.finish.call_args,previous)
        self.assertEqual(store.fail.call_args.args[0],'failed')

    def test_cache_cli_option_rejected_before_auth_in_one_shot_mode(self):
        from kiwoom_collector.__main__ import main
        with patch('kiwoom_collector.__main__.load_credentials') as load:
            with self.assertRaises(SystemExit):main(['collect','--test-now','--protected-token-cache'])
        load.assert_not_called()


if __name__=='__main__':unittest.main()
