"""Interactive real CLI tests: all credentials and responses are synthetic."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from kiwoom_collector import catalog_real as cli, stock_catalog as catalog, master_data
from kiwoom_collector.catalog_auth import AuthBridgeError
from kiwoom_collector.stock_catalog_live import after_close

FLAGS = ['--execute-approved', '--oauth-approved', '--quota-reviewed']


class RealCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = self.root / 'operating.env'
        self.env.write_text('KIWOOM_ENV=mock\nKIWOOM_APP_KEY=SYNTHETIC_OPERATING\nKIWOOM_SECRET_KEY=SYNTHETIC_OLD_SECRET\n', encoding='utf-8')
        p = patch.object(cli, 'OPERATING_ENV', self.env)
        p.start(); self.addCleanup(p.stop)

    def test_prompt_reuses_auth_without_environment_or_file_changes(self):
        before = dict(os.environ), self.env.read_bytes()
        with patch.object(cli.getpass, 'getpass', side_effect=['SYNTHETIC_REAL', 'SYNTHETIC_SECRET']), \
                patch.object(cli, 'OAuthProvider') as provider:
            cli.prompted_provider()
        credentials = provider.call_args.args[0]
        self.assertEqual(credentials.environment, 'real')
        self.assertEqual(credentials.base_url, 'https://api.kiwoom.com')
        self.assertEqual(provider.call_args.kwargs, {'approved': True, 'token_impact_reviewed': True})
        self.assertEqual((dict(os.environ), self.env.read_bytes()), before)

    def test_same_operating_key_rejected_before_oauth(self):
        with patch.object(cli.getpass, 'getpass', side_effect=['SYNTHETIC_OPERATING', 'SYNTHETIC_SECRET']), \
                patch.object(cli, 'OAuthProvider') as provider:
            with self.assertRaisesRegex(AuthBridgeError, 'same_operating_app_key_rejected'):
                cli.prompted_provider()
        provider.assert_not_called()

    def test_unverifiable_operating_environment_rejected_before_input(self):
        self.env.write_text('KIWOOM_ENV=real\nKIWOOM_APP_KEY=SYNTHETIC', encoding='utf-8')
        with patch.object(cli.getpass, 'getpass') as prompt:
            with self.assertRaisesRegex(AuthBridgeError, 'operating_mock_key_cannot_be_verified'):
                cli.prompted_provider()
        prompt.assert_not_called()

    def test_echo_fallback_and_empty_credentials_rejected(self):
        for inputs in (['', 'SYNTHETIC_SECRET'], ['SYNTHETIC_REAL', '']):
            with patch.object(cli.getpass, 'getpass', side_effect=inputs), self.assertRaises(AuthBridgeError):
                cli.prompted_provider()
        with patch.object(cli.getpass, 'getpass', side_effect=cli.getpass.GetPassWarning('hidden unavailable')):
            with self.assertRaisesRegex(AuthBridgeError, 'secure_console_input_required'):
                cli.prompted_provider()

    def test_approval_required_before_prompt_or_network(self):
        with patch.object(cli, 'prompted_provider') as provider, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): cli.main([])
        provider.assert_not_called()

    def test_cli_argv_fixed_scope_and_unicode_name_without_shell_code(self):
        with patch.object(catalog, 'main', return_value=0) as run:
            self.assertEqual(cli.main(FLAGS + ['--name', '한글 "quoted"']), 0)
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index('--name') + 1], '한글 "quoted"')
        self.assertEqual(argv[argv.index('--environment') + 1], 'real')
        self.assertEqual(argv[argv.index('--max-pages') + 1], '50')
        self.assertEqual(argv[argv.index('--seconds') + 1], '300')
        self.assertEqual(run.call_args.kwargs['token_provider_factory'], cli.prompted_provider)

    def test_memory_token_handoff_cleanup_and_error_no_leak(self):
        with patch.multiple(master_data, PROJECT=self.root, MASTER_ROOT=self.root/'master'), \
                patch.object(catalog, 'RUN_ROOT', self.root/'runs'):
            db = master_data.create_master('real', synthetic=False, at='2026-10-29T08:00:00+09:00')
            provider = Mock(oauth_calls=1)
            provider.get_token.return_value = 'SYNTHETIC_PRIVATE_TOKEN'
            factory = Mock(return_value=provider)
            transport = Mock(calls=1)
            with patch('kiwoom_collector.stock_catalog_live.after_close'), \
                    patch('kiwoom_collector.stock_catalog_live.ListTransport', return_value=transport) as live, \
                    patch.object(catalog.time, 'sleep'), \
                    patch.object(catalog, 'collect', side_effect=catalog.CatalogError('synthetic_failure')), \
                    redirect_stdout(io.StringIO()) as output:
                result = catalog.main(['collect', '--db', str(db), '--environment', 'real',
                    '--name', 'memory-only', '--execute-approved', '--quota-reviewed',
                    '--max-pages', '50', '--seconds', '300'], token_provider_factory=factory)
            self.assertEqual(result, 2)
            live.assert_called_once_with('SYNTHETIC_PRIVATE_TOKEN', environment='real', approved=True)
            provider.get_token.assert_called_once_with(minimum_valid_seconds=330)
            provider.close.assert_called_once(); transport.close.assert_called_once()
            self.assertNotIn('SYNTHETIC_PRIVATE_TOKEN', output.getvalue())
            for path in (self.root/'runs').rglob('*.json'):
                self.assertNotIn('SYNTHETIC_PRIVATE_TOKEN', path.read_text(encoding='utf-8'))
            self.assertEqual(json.loads(output.getvalue())['oauth_calls'], 1)

    def test_module_subprocess_help_and_no_approval_do_not_prompt(self):
        command = [sys.executable, '-B', '-m', 'kiwoom_collector.catalog_real']
        help_result = subprocess.run(command+['--help'], capture_output=True, timeout=15)
        self.assertEqual(help_result.returncode, 0)
        denied = subprocess.run(command, input=b'', capture_output=True, timeout=15)
        self.assertEqual(denied.returncode, 2)
        self.assertNotIn(b'Real App Key (hidden)', denied.stdout+denied.stderr)

    def test_diagnose_once_routes_to_probe(self):
        with patch.object(catalog,'main',return_value=0) as run:
            self.assertEqual(cli.main(FLAGS+['--diagnose-once']),0)
        self.assertEqual(run.call_args.args[0][0],'probe')

    def test_probe_single_call_never_publishes_even_with_continuation_or_failure(self):
        fixture=Path(__file__).parent/'fixtures'/'ka10099_synthetic.json'
        for mode in ('valid','mismatch','audit_io'):
            with self.subTest(mode=mode), patch.multiple(master_data,PROJECT=self.root,MASTER_ROOT=self.root/'master'), \
                    patch.object(catalog,'RUN_ROOT',self.root/'runs'):
                db=master_data.create_master('probe_'+mode,synthetic=False,at='2026-10-29T08:00:00+09:00')
                before=db.read_bytes()
                envelope=json.loads(fixture.read_text(encoding='utf-8'))['pages'][0]['response']
                if mode=='mismatch':envelope['body']['list'][0]['marketCode']='10'
                transport=Mock(calls=1,return_value=envelope)
                provider=Mock(oauth_calls=1);provider.get_token.return_value='PRIVATE_TOKEN'
                writes=Path.write_text
                def write(path,*args,**kwargs):
                    if mode=='audit_io' and path.name=='manifest.json' and path.exists():
                        raise OSError('PRIVATE_TOKEN')
                    return writes(path,*args,**kwargs)
                with patch('kiwoom_collector.stock_catalog_live.after_close'), \
                        patch('kiwoom_collector.stock_catalog_live.ListTransport',return_value=transport), \
                        patch.object(catalog.time,'sleep'),patch.object(catalog,'publish') as publish, \
                        patch.object(Path,'write_text',new=write),redirect_stdout(io.StringIO()) as output:
                    result=catalog.main(['probe','--db',str(db),'--environment','real',
                        '--name',mode,'--execute-approved','--quota-reviewed'],
                        token_provider_factory=lambda:provider)
                self.assertEqual(result,0 if mode=='valid' else 2)
                transport.assert_called_once_with(market='0',cont_yn='',next_key='',remaining_seconds=300)
                publish.assert_not_called();provider.close.assert_called_once();transport.close.assert_called_once()
                audit=json.loads(output.getvalue())
                self.assertEqual(audit['actual_api_calls'],1);self.assertEqual(audit['oauth_calls'],1)
                self.assertFalse(audit['database_updated']);self.assertEqual(audit['automatic_retries'],0)
                self.assertNotIn('PRIVATE_TOKEN',output.getvalue())
                self.assertEqual(db.read_bytes(),before)
                self.assertEqual([p.name for p in (self.root/'runs'/mode).iterdir()],['manifest.json'])

    def test_after_close_guard_and_provider_not_called_in_daytime(self):
        with self.assertRaises(catalog.CatalogError):
            after_close(datetime.fromisoformat('2026-10-30T10:00:00+09:00'))
