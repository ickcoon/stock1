"""Interactive real-server ka10099 CLI. No credentials in shell arguments."""
import argparse
from datetime import datetime
import getpass
from pathlib import Path
import warnings

from dotenv import dotenv_values

from .catalog_auth import AuthBridgeError, OAuthProvider
from .config import Credentials
from . import master_data, stock_catalog

OPERATING_ENV = master_data.PROJECT.parent / 'stock1' / '.env'
DEFAULT_DB = master_data.PROJECT / 'diagnostics' / 'master' / 'c10-master.db'


def prompted_provider():
    # Read only the explicitly named operating file. Never load it into os.environ.
    source = OPERATING_ENV.resolve(strict=True)
    values = dotenv_values(source, encoding='utf-8-sig', interpolate=False)
    operating_key = (values.get('KIWOOM_APP_KEY') or '').strip()
    if values.get('KIWOOM_ENV') != 'mock' or not operating_key:
        raise AuthBridgeError('operating_mock_key_cannot_be_verified')
    del values
    # Abort if a terminal cannot hide input; do not accept getpass's echo fallback.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            app = getpass.getpass('Real App Key (hidden): ').strip()
            secret = getpass.getpass('Real Secret Key (hidden): ').strip()
    except (getpass.GetPassWarning, EOFError):
        raise AuthBridgeError('secure_console_input_required') from None
    if not app or not secret or '${' in app or '${' in secret:
        raise AuthBridgeError('explicit_real_credentials_required')
    if app == operating_key:
        raise AuthBridgeError('same_operating_app_key_rejected')
    credentials = Credentials(app, secret, environment='real')
    # Separate real App Key, separate client memory; no operating token access.
    return OAuthProvider(credentials, approved=True, token_impact_reviewed=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-approved', action='store_true')
    parser.add_argument('--oauth-approved', action='store_true')
    parser.add_argument('--quota-reviewed', action='store_true')
    parser.add_argument('--diagnose-once', action='store_true',
                        help='One KOSPI page only; no continuation or database publication')
    parser.add_argument('--name', default='c11-real-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    args = parser.parse_args(argv)
    if not (args.execute_approved and args.oauth_approved and args.quota_reviewed):
        parser.error('requires --execute-approved --oauth-approved --quota-reviewed')
    return stock_catalog.main([
        'probe' if args.diagnose_once else 'collect', '--db', str(DEFAULT_DB), '--environment', 'real',
        '--name', args.name, '--execute-approved', '--quota-reviewed',
        '--max-pages', '50', '--seconds', '300',
    ], token_provider_factory=prompted_provider)


if __name__ == '__main__':
    raise SystemExit(main())
