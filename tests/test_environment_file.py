import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from kiwoom_collector import environment_file as env
from kiwoom_collector.config import load_credentials
from kiwoom_collector.krx_openapi import credential
from kiwoom_collector.c16_news import hub_credentials
from kiwoom_collector.telegram_briefing import Configuration


class UnifiedCredentialsTests(unittest.TestCase):
    def test_root_selection(self):
        name='stock1-dev.env' if env.PROJECT.name=='stock1-dev' else '.env'
        self.assertEqual(env.ENV_FILE, env.PROJECT / name)

    def test_all_loaders_use_file_and_ignore_inherited_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stock1-dev.env'
            token = '123456789:' + 'a' * 35
            path.write_text('KIWOOM_ENV=mock\nKIWOOM_APP_KEY=file-app\nKIWOOM_SECRET_KEY=file-secret\n'
                            'KRX_AUTH_KEY=file-krx\nNAVER_API_HUB_KEY_ID=file-id\nNAVER_API_HUB_KEY=file-hub\n'
                            'ARVIS_TELEGRAM_BOT_TOKEN=' + token + '\nARVIS_TELEGRAM_CHAT_ID=123456789\n'
                            'ARVIS_TELEGRAM_ALLOWED_CHAT_IDS=123456789,987654321\n', encoding='utf-8-sig')
            with patch.object(env, 'ENV_FILE', path), patch.dict(os.environ, {'KIWOOM_APP_KEY':'inherited', 'KRX_AUTH_KEY':'inherited'}), patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
                self.assertEqual(load_credentials().app_key, 'file-app')
                self.assertIsNotNone(credential())
                self.assertEqual(hub_credentials(), ('file-id', 'file-hub'))
                config = Configuration.from_environment()
                self.assertEqual(config.chat_id, '123456789')
                self.assertEqual(len(config.allowed), 2)
                self.assertEqual(os.environ['KIWOOM_APP_KEY'], 'inherited')
                path.write_text('ARVIS_TELEGRAM_BOT_TOKEN=' + token + '\nARVIS_TELEGRAM_CHAT_ID=123456789\n', encoding='utf8')
                self.assertEqual(Configuration.from_environment().allowed, ('123456789',))
                path.write_text('KRX_AUTH_KEY=\n', encoding='utf-8')
                self.assertIsNone(credential())
                self.assertEqual(hub_credentials(), (None, None))
                with self.assertRaises(ValueError): load_credentials()
                with self.assertRaises(ValueError): Configuration.from_environment()
                path.unlink()
                self.assertEqual(env.values(), {})

    def test_no_interpolation_and_safe_parse_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.env'
            path.write_text("KEY='${PRIVATE}'\n", encoding='utf-8')
            self.assertEqual(env.values(path)['KEY'], '${PRIVATE}')
            path.write_text("KEY='unterminated\n", encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '^credential_file_invalid$'):
                env.values(path)
