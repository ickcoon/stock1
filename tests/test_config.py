import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector.config import Credentials, load_credentials


class ConfigTests(unittest.TestCase):
    def test_reads_env_and_hides_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("KIWOOM_APP_KEY=test-key\nKIWOOM_SECRET_KEY=test-secret\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                credentials = load_credentials(path)
            self.assertEqual(credentials.app_key, "test-key")
            self.assertEqual(credentials.secret_key, "test-secret")
            self.assertNotIn("test-key", repr(credentials))
            self.assertNotIn("test-secret", repr(credentials))

    def test_missing_names_without_values(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "KIWOOM_APP_KEY, KIWOOM_SECRET_KEY"):
                    load_credentials(Path(directory) / "absent")

    def test_preserves_existing_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("KIWOOM_APP_KEY=file-key\nKIWOOM_SECRET_KEY=file-secret\n", encoding="utf-8")
            with patch.dict(os.environ, {"KIWOOM_APP_KEY": "existing-key"}, clear=True):
                self.assertEqual(load_credentials(path).app_key, "existing-key")

    def test_environment_from_dotenv_and_safe_default(self):
        for setting, expected in (("", "mock"), ("KIWOOM_ENV=mock\n", "mock"), ("KIWOOM_ENV=real\n", "real")):
            with self.subTest(setting=setting), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / ".env"
                path.write_text(setting + "KIWOOM_APP_KEY=test-key\nKIWOOM_SECRET_KEY=test-secret\n", encoding="utf-8-sig")
                with patch.dict(os.environ, {}, clear=True):
                    credentials = load_credentials(path)
                self.assertEqual(credentials.environment, expected)
                self.assertEqual(credentials.base_url, "https://mockapi.kiwoom.com" if expected == "mock" else "https://api.kiwoom.com")

    def test_invalid_environment_never_echoes_value(self):
        for value in ("", "prod", "MOCK", "https://example.com", "test-secret"):
            with patch.dict(os.environ, {"KIWOOM_ENV": value}, clear=True):
                with self.assertRaises(ValueError) as caught:
                    load_credentials(Path("/nonexistent/test.env"))
                self.assertIn("KIWOOM_ENV", str(caught.exception))
                self.assertNotIn("test-secret", str(caught.exception))

    def test_environment_binding_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("KIWOOM_ENV=real\nKIWOOM_APP_KEY=x\nKIWOOM_SECRET_KEY=y\n", encoding="utf-8")
            with patch.dict(os.environ, {"KIWOOM_ENV": "mock"}, clear=True):
                self.assertEqual(load_credentials(path).environment, "mock")

    def test_direct_client_settings_reject_arbitrary_host(self):
        with self.assertRaisesRegex(ValueError, "KIWOOM_ENV"):
            Credentials("test-key", "test-secret", environment="https://example.com")
