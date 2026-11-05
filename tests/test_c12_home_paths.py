"""Regression checks for a standalone home-PC checkout."""
from pathlib import Path
import unittest

from kiwoom_collector import krx_openapi, theme_research


class HomePathTests(unittest.TestCase):
    def test_outputs_stay_in_this_checkout(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(krx_openapi.PROJECT, root)
        self.assertEqual(krx_openapi.OUTPUT_ROOT, root / 'diagnostics' / 'krx-an')
        self.assertEqual(theme_research.ROOT, root / 'diagnostics' / 'theme_review')

    def test_local_credential_path_accepted_without_key(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / '.env.krx.local'
            path.write_text('KRX_AUTH_KEY=\n', encoding='utf8')
            with patch.object(krx_openapi, 'PROJECT', root):
                self.assertIsNone(krx_openapi.credential(env_file=path))


if __name__ == '__main__':
    unittest.main()
