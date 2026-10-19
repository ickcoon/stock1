import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector.config import load_credentials


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
