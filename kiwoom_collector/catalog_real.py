"""Interactive real-server ka10099 CLI. No credentials in shell arguments."""
import argparse
from datetime import datetime
import getpass
import json
from pathlib import Path
import sqlite3
import warnings

from dotenv import dotenv_values

from .catalog_auth import AuthBridgeError, OAuthProvider
from .config import Credentials
from . import master_data, stock_catalog

OPERATING_ENV = master_data.PROJECT.parent / 'stock1' / '.env'
DEFAULT_DB = master_data.PROJECT / 'diagnostics' / 'master' / 'c10-master.db'


def prepare_database(*, create=False):
    """Offline only. Never repair, reset or replace an existing database."""
    path=master_data.writable_path(DEFAULT_DB)
    created=False
    if not path.exists():
        if not create: raise FileNotFoundError()
        if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm','-journal')):
            raise ValueError('orphan_database_sidecars')
        # create_master uses exclusive file creation; a concurrent existing file wins.
        master_data.create_master(path.stem,synthetic=False)
        created=True
    with master_data.readonly_database(path) as (db,_):
        # Only fixed internal read-only PRAGMAs; the connection remains query_only.
        db.set_authorizer(None)
        if [row[0] for row in db.execute('PRAGMA integrity_check')]!=['ok']:
            raise ValueError('master_integrity_failed')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('master_foreign_keys_failed')
        expected=sqlite3.connect(':memory:')
        try:
            expected.executescript(master_data.SCHEMA)
            tables=expected.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            for (table,) in tables:
                actual=[tuple(row) for row in db.execute('PRAGMA table_info('+table+')')]
                if actual!=expected.execute('PRAGMA table_info('+table+')').fetchall():
                    raise ValueError('master_schema_mismatch')
        finally:
            expected.close()
        meta=[tuple(row) for row in db.execute('SELECT version,synthetic FROM master_meta')]
        if meta!=[(1,0)]: raise ValueError('master_source_scope_mismatch')
    return dict(status='local_database_ready',created=created,schema_version=1,synthetic=False,
                integrity='ok',actual_api_calls=0,oauth_calls=0,database_updated=False)


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
    parser.add_argument('--prepare-db',action='store_true',
                        help='Offline: create missing development master or validate existing DB; then exit')
    parser.add_argument('--time-policy',type=Path,help='Local catalog time policy JSON; no clock override')
    parser.add_argument('--name', default='c11-real-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    args = parser.parse_args(argv)
    if args.prepare_db and (args.diagnose_once or args.execute_approved or args.oauth_approved or args.quota_reviewed):
        parser.error('--prepare-db must be used alone without live approval flags')
    if args.prepare_db:
        try:
            result=prepare_database(create=True)
        except (ValueError,OSError,sqlite3.Error) as error:
            result=dict(status='failed',reason='local_database_preflight_failed',
                        failure_stage='database_prepare',exception_type=type(error).__name__,
                        actual_api_calls=0,oauth_calls=0,database_updated=False)
        print(json.dumps(result))
        return 0 if result['status']=='local_database_ready' else 2
    if not (args.execute_approved and args.oauth_approved and args.quota_reviewed):
        parser.error('requires --execute-approved --oauth-approved --quota-reviewed')
    try:
        prepare_database()
    except (ValueError,OSError,sqlite3.Error) as error:
        print(json.dumps(dict(status='failed',reason='local_database_preflight_failed',
            failure_stage='database_preflight',exception_type=type(error).__name__,
            actual_api_calls=0,oauth_calls=0,database_updated=False)))
        return 2
    return stock_catalog.main([
        'probe' if args.diagnose_once else 'collect', '--db', str(DEFAULT_DB), '--environment', 'real',
        '--name', args.name, '--execute-approved', '--quota-reviewed',
        '--max-pages', '50', '--seconds', '300',
    ]+(['--time-policy',str(args.time_policy)] if args.time_policy else []), token_provider_factory=prompted_provider)


if __name__ == '__main__':
    raise SystemExit(main())
