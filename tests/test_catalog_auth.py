"""No network: existing authenticator reuse, secret isolation and provenance."""
from contextlib import redirect_stdout,closing
from datetime import datetime,timedelta,timezone
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from kiwoom_collector import catalog_auth as auth,stock_catalog as catalog,data_origin
from kiwoom_collector.config import Credentials
from kiwoom_collector.client import KiwoomClient,Token,KST
import test_stock_catalog as fixtures


class CatalogAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'.env'
        self.path.write_text('KIWOOM_ENV=real\nKIWOOM_APP_KEY=SYNTHETIC_APP_PRIVATE\nKIWOOM_SECRET_KEY=SYNTHETIC_SECRET_PRIVATE\n',encoding='utf-8')

    def credentials(self):return auth.credentials(self.path,environment='real')

    def fake_client(self,seconds=3600):
        client=Mock()
        client.authenticate.return_value=Token('SYNTHETIC_TOKEN_PRIVATE',datetime.now(KST)+timedelta(seconds=seconds))
        return client

    def test_check_reads_only_no_network_or_environment_mutation(self):
        before=self.path.read_bytes();environment=dict(os.environ)
        with patch('requests.Session.post',side_effect=AssertionError('network forbidden')):
            result=auth.check(self.path,environment='real')
        self.assertTrue(result['ready']);self.assertEqual(result['oauth_calls'],0)
        self.assertEqual(dict(os.environ),environment);self.assertEqual(self.path.read_bytes(),before)
        self.assertNotIn('SYNTHETIC_APP_PRIVATE',json.dumps(result));self.assertNotIn('SYNTHETIC_SECRET_PRIVATE',repr(self.credentials()))

    def test_mock_file_not_used_for_real_and_no_implicit_process_override(self):
        self.path.write_text(self.path.read_text().replace('=real','=mock'),encoding='utf-8')
        with patch.dict(os.environ,{'KIWOOM_ENV':'real','KIWOOM_APP_KEY':'OTHER_PRIVATE','KIWOOM_SECRET_KEY':'OTHER_PRIVATE'}):
            result=auth.check(self.path,environment='real')
            self.assertFalse(result['ready']);self.assertEqual(result['reason'],'environment_mismatch')
            with self.assertRaisesRegex(auth.AuthBridgeError,'environment_mismatch'):self.credentials()
            self.assertEqual(auth.credentials(self.path,environment='mock').environment,'mock')

    def test_missing_environment_keys_and_interpolation_are_not_guessed(self):
        self.path.write_text('KIWOOM_APP_KEY=SYNTHETIC\nKIWOOM_SECRET_KEY=SYNTHETIC',encoding='utf-8')
        self.assertFalse(auth.check(self.path,environment='real')['ready'])
        self.path.write_text('KIWOOM_ENV=real\nKIWOOM_APP_KEY=${OTHER_PRIVATE}\nKIWOOM_SECRET_KEY=SYNTHETIC',encoding='utf-8')
        with self.assertRaisesRegex(auth.AuthBridgeError,'interpolation'):self.credentials()

    def test_provider_requires_separate_approval_and_impact_review_before_factory(self):
        factory=Mock()
        for approved,reviewed in ((False,False),(True,False),(False,True)):
            with self.assertRaises(auth.AuthBridgeError):
                auth.OAuthProvider(self.credentials(),approved=approved,token_impact_reviewed=reviewed,client_factory=factory)
        factory.assert_not_called()

    def test_existing_authenticate_called_once_in_memory_no_files_or_env_export(self):
        client=self.fake_client();collector_token=Token('UNCHANGED_COLLECTOR_TOKEN',datetime.now(KST)+timedelta(hours=1))
        unrelated_collector=Mock(token=collector_token)
        before=dict(os.environ);files=list(self.path.parent.iterdir())
        provider=auth.OAuthProvider(self.credentials(),approved=True,token_impact_reviewed=True,client_factory=lambda _:client)
        self.assertEqual(provider.get_token(),'SYNTHETIC_TOKEN_PRIVATE');provider.get_token()
        client.authenticate.assert_called_once_with(attempts=1)
        self.assertEqual(provider.oauth_calls,1);provider.close()
        self.assertEqual(unrelated_collector.token,collector_token)
        self.assertEqual(dict(os.environ),before);self.assertEqual(list(self.path.parent.iterdir()),files)
        client.close.assert_called_once();self.assertIsNone(client.token)

    def test_failure_and_short_lifetime_never_retry_or_leak_secret(self):
        for failure in (True,False):
            client=self.fake_client(seconds=10)
            if failure:client.authenticate.side_effect=RuntimeError('SYNTHETIC_SECRET_PRIVATE')
            provider=auth.OAuthProvider(self.credentials(),approved=True,token_impact_reviewed=True,client_factory=lambda _:client)
            for _ in range(2):
                with self.assertRaises(auth.AuthBridgeError) as error:provider.get_token()
                self.assertNotIn('SYNTHETIC_SECRET_PRIVATE',str(error.exception))
            self.assertEqual(client.authenticate.call_count,1);provider.close()

    def test_reuses_actual_project_authenticate_with_fake_http_one_oauth_only(self):
        created=[]
        def factory(credentials):
            client=KiwoomClient(credentials);client.session.close()
            response=Mock(status_code=200)
            response.json.return_value={'return_code':0,'token_type':'bearer','token':'SYNTHETIC_TOKEN_PRIVATE',
                'expires_dt':(datetime.now(KST)+timedelta(hours=1)).strftime('%Y%m%d%H%M%S')}
            client.session=Mock();client.session.post.return_value=response;created.append(client)
            return client
        provider=auth.OAuthProvider(self.credentials(),approved=True,token_impact_reviewed=True,client_factory=factory)
        with self.assertLogs('kiwoom_collector.client',level='INFO') as logs:provider.get_token()
        args,kwargs=created[0].session.post.call_args
        self.assertEqual(args[0],'https://api.kiwoom.com/oauth2/token')
        self.assertEqual(created[0].session.post.call_count,1)
        self.assertNotIn('SYNTHETIC_TOKEN_PRIVATE',' '.join(logs.output));provider.close()

    def test_auth_check_cli_does_not_construct_authenticator_or_touch_db(self):
        with patch('kiwoom_collector.client.KiwoomClient',side_effect=AssertionError('must not instantiate')), \
                redirect_stdout(io.StringIO()) as output:
            result=catalog.main(['auth-check','--credentials-env-file',str(self.path),'--environment','real'])
        self.assertEqual(result,0);self.assertTrue(json.loads(output.getvalue())['ready'])
        self.assertNotIn('SYNTHETIC_APP_PRIVATE',output.getvalue())


class DataOriginTests(unittest.TestCase):
    def test_both_server_api_responses_are_not_synthetic(self):
        for environment in ('mock','real'):
            result=data_origin.metadata(environment,'api_response')
            self.assertEqual(result['data_origin'],'api_response')
            self.assertNotIn('합성',result['data_origin_label'])
            self.assertEqual(data_origin.validate_record(result|{'synthetic':False}),'api_response')

    def test_fixture_is_independent_of_server_and_old_data_stays_unverified(self):
        self.assertEqual(data_origin.metadata('mock','synthetic_fixture')['data_origin'],'synthetic_fixture')
        self.assertEqual(data_origin.validate_record({'environment':'real','synthetic':False}),'unverified')
        self.assertEqual(data_origin.validate_record({'environment':'mock','synthetic':False}),'unverified')
        with self.assertRaises(ValueError):data_origin.validate_record({'environment':'mock','synthetic':True,'data_origin':'api_response'})


class CatalogOriginPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.StockCatalogTests('test_complete_two_market_collection_and_original_metadata')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_new_fixture_has_independent_environment_and_origin_in_db(self):
        report=self.fixture.publish()
        self.assertEqual(report['environment'],'mock')
        self.assertEqual(report['data_origin'],'synthetic_fixture')
        with self.fixture.query() as (reader,_):
            self.assertEqual(reader.db.execute('SELECT data_origin FROM catalog_runs').fetchone()[0],'synthetic_fixture')
            info=reader.catalog_info('000001',at='2026-10-30T16:02:00+09:00')
            self.assertEqual(info['data_origin'],'synthetic_fixture')

    def test_old_catalogue_not_reclassified_by_server_during_schema_extension(self):
        from kiwoom_collector import master_data
        first=self.fixture.publish()
        with master_data.MasterStore(self.fixture.path) as store:
            report=json.loads(store.db.execute('SELECT report_json FROM catalog_runs').fetchone()[0])
            report.pop('data_origin');report.pop('data_origin_label')
            store.db.execute('UPDATE catalog_runs SET report_json=?',(json.dumps(report),))
            store.db.execute('ALTER TABLE catalog_runs DROP COLUMN data_origin')
        with self.fixture.query() as (reader,_):
            self.assertEqual(reader.stock('000001',at='2026-10-30T16:02:00+09:00')['state'],'catalog_origin_unverified')
        self.fixture.publish(self.fixture.dataset(self.fixture.shifted()),at='2026-10-31T16:01:00+09:00')
        with self.fixture.query() as (reader,_):
            self.assertEqual(reader.db.execute('SELECT data_origin FROM catalog_runs WHERE run_id=?',(first['run_id'],)).fetchone()[0],'unverified')

    def test_collect_file_oauth_missing_approval_does_not_issue_or_use_env_token(self):
        from kiwoom_collector import master_data
        path=master_data.create_master('approval_only',synthetic=False,at='2026-10-29T08:00:00+09:00')
        credential=self.fixture.root/'mock.env'
        credential.write_text('KIWOOM_ENV=mock\nKIWOOM_APP_KEY=SYNTHETIC\nKIWOOM_SECRET_KEY=SYNTHETIC',encoding='utf-8')
        with patch('kiwoom_collector.stock_catalog_live.after_close'), \
                patch('kiwoom_collector.catalog_auth.OAuthProvider') as provider, \
                patch('kiwoom_collector.stock_catalog_live.ListTransport') as transport,redirect_stdout(io.StringIO()):
            code=catalog.main(['collect','--db',str(path),'--environment','mock','--rules',str(fixtures.RULES),
                '--name','unapproved_oauth','--execute-approved','--quota-reviewed','--credentials-env-file',str(credential)])
        self.assertEqual(code,2);provider.assert_not_called();transport.assert_not_called()


def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(CatalogAuthTests))
    suite.addTests(loader.loadTestsFromTestCase(DataOriginTests))
    suite.addTests(loader.loadTestsFromTestCase(CatalogOriginPersistenceTests))
    return suite
