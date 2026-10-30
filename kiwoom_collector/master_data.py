"""Separate local stock/sector master and evidence-gated ka10001 cache. No network."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from . import market_cap as mc
from .am_time_policy import instant
from .cross_adapter import readonly_database

PROJECT = Path(__file__).resolve().parent.parent
MASTER_ROOT = PROJECT/'diagnostics'/'master'
CODE = re.compile(r'[0-9A-Z]{6}', re.ASCII)
BROAD_SECTORS = ('반도체','자동차','로봇','원전','전력','조선','방산','2차전지',
                 '제약·바이오','금융','통신','인터넷·게임','건설','화학','철강','우주항공')
SCHEMA = '''
CREATE TABLE master_meta(version INTEGER NOT NULL CHECK(version=1),synthetic INTEGER NOT NULL CHECK(synthetic IN(0,1)));
CREATE TABLE stock_master(stock_code TEXT PRIMARY KEY,stock_name TEXT NOT NULL,market TEXT NOT NULL CHECK(market IN('KOSPI','KOSDAQ','UNKNOWN')),
 listing_status TEXT NOT NULL CHECK(listing_status IN('listed','delisted','unknown')),verified INTEGER NOT NULL CHECK(verified IN(0,1)),
 source TEXT NOT NULL,source_updated_at TEXT NOT NULL,registered_at TEXT NOT NULL);
CREATE TABLE stock_history(id INTEGER PRIMARY KEY,stock_code TEXT NOT NULL REFERENCES stock_master(stock_code),
 registered_at TEXT NOT NULL,record_json TEXT NOT NULL,record_hash TEXT NOT NULL UNIQUE);
CREATE TABLE sector_master(sector_id TEXT PRIMARY KEY,sector_name TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN('industry','theme')),
 active INTEGER NOT NULL CHECK(active IN(0,1)),source TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(kind,sector_name));
CREATE TABLE stock_sector(id INTEGER PRIMARY KEY,stock_code TEXT NOT NULL REFERENCES stock_master(stock_code),
 sector_id TEXT NOT NULL REFERENCES sector_master(sector_id),is_primary INTEGER NOT NULL CHECK(is_primary IN(0,1)),
 status TEXT NOT NULL CHECK(status IN('proposed','verified','rejected')),evidence TEXT NOT NULL,source TEXT NOT NULL,
 source_updated_at TEXT NOT NULL,valid_from TEXT NOT NULL,valid_to TEXT,registered_at TEXT NOT NULL,
 source_sha256 TEXT,record_hash TEXT NOT NULL UNIQUE);
CREATE INDEX idx_stock_sector ON stock_sector(stock_code,registered_at);
CREATE TABLE market_cap_cache(id INTEGER PRIMARY KEY,stock_code TEXT NOT NULL REFERENCES stock_master(stock_code),
 environment TEXT NOT NULL CHECK(environment IN('mock','real')),api_id TEXT NOT NULL CHECK(api_id='ka10001'),contract_version TEXT NOT NULL,
 raw_mac TEXT,declared_unit TEXT NOT NULL,verified_unit TEXT,normalized_won TEXT,trade_date TEXT,observed_at TEXT,
 request_started_at TEXT NOT NULL,response_received_at TEXT NOT NULL,registered_at TEXT NOT NULL,source TEXT NOT NULL,
 verified INTEGER NOT NULL CHECK(verified IN(0,1)),record_json TEXT NOT NULL,proof_json TEXT NOT NULL,record_hash TEXT NOT NULL UNIQUE);
CREATE INDEX idx_cap_lookup ON market_cap_cache(stock_code,environment,response_received_at);
CREATE TABLE migration_log(source_sha256 TEXT PRIMARY KEY,registered_at TEXT NOT NULL,report_json TEXT NOT NULL);
'''


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError('nonempty_metadata_required')
    return value.strip()


def code(value):
    if not isinstance(value, str) or not CODE.fullmatch(value):
        raise ValueError('exact_six_character_code_required')
    return value


def writable_path(path):
    path = Path(path).absolute()
    root = MASTER_ROOT.absolute()
    if root.resolve() != root or path.resolve() != path or not path.is_relative_to(root) or path.suffix != '.db':
        raise ValueError('development_master_directory_required')
    return path


def create_master(name, *, synthetic=False, at=None):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name) or type(synthetic) is not bool:
        raise ValueError('invalid_master_scope')
    at = instant(at or datetime.now(timezone.utc).isoformat()).isoformat()
    path = writable_path(MASTER_ROOT/(name+'.db'))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb'):
        pass
    try:
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(SCHEMA)
            db.execute('INSERT INTO master_meta VALUES(1,?)',(int(synthetic),))
            db.executemany('INSERT INTO sector_master VALUES(?,?,\'industry\',1,\'project_broad_taxonomy\',?)',
                           [(f'industry_{i:02}', name, at) for i,name in enumerate(BROAD_SECTORS,1)])
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


class Queries:
    def __init__(self, db):
        self.db = db

    def stock(self, stock_code, *, at):
        code(stock_code); at = instant(at).isoformat()
        rows = self.db.execute('SELECT record_json,registered_at FROM stock_history WHERE stock_code=? AND registered_at<=? ORDER BY registered_at DESC,id DESC', (stock_code,at)).fetchall()
        if not rows:
            return dict(state='unknown',stock_code=stock_code)
        latest = [json.loads(r['record_json']) for r in rows if r['registered_at']==rows[0]['registered_at']]
        if len({digest(r) for r in latest}) != 1:
            return dict(state='conflict',stock_code=stock_code)
        value = latest[0]
        return dict(value, state='active' if value['verified'] and value['listing_status']=='listed'
                    and value['market'] in ('KOSPI','KOSDAQ') else 'inactive_or_unverified')

    def classification(self, stock_code, *, at, kind='industry'):
        at = instant(at).isoformat()
        stock = self.stock(stock_code,at=at)
        if stock['state'] != 'active':
            return dict(state=stock['state'],tags=[],primary=None)
        rows = [dict(r) for r in self.db.execute('''SELECT t.*,s.sector_name,s.kind,s.active FROM stock_sector t
            JOIN sector_master s ON s.sector_id=t.sector_id WHERE t.stock_code=? AND t.registered_at<=?
            AND s.updated_at<=? ORDER BY t.registered_at,t.id''', (stock_code,at,at))]
        # A later review of the same edge supersedes its earlier review.
        latest = {}
        for row in rows:
            latest[row['sector_id']] = row
        tags = [r for r in latest.values() if r['status']=='verified' and r['active']
                and r['valid_from']<=at and (r['valid_to'] is None or r['valid_to']>at)]
        candidates = [r for r in tags if r['kind']==kind]
        primary = [r for r in candidates if r['is_primary']]
        selected = primary[0] if len(primary)==1 else candidates[0] if len(candidates)==1 else None
        state = 'classified' if selected else 'conflict' if candidates else 'unclassified'
        return dict(state=state,tags=tags,primary=selected)

    def capital(self, stock_code, *, environment, at, ttl_seconds=3600):
        at = instant(at)
        if environment not in ('mock','real') or type(ttl_seconds) is not int or not 1<=ttl_seconds<=86400:
            raise ValueError('invalid_capital_lookup_scope')
        stock = self.stock(stock_code,at=at)
        if stock['state'] != 'active':
            return dict(state=stock['state'],record=None)
        rows = [dict(r) for r in self.db.execute('''SELECT * FROM market_cap_cache WHERE stock_code=? AND environment=?
            AND response_received_at<=? AND registered_at<=? ORDER BY response_received_at DESC,id DESC''',
            (stock_code,environment,at.isoformat(),at.isoformat()))]
        if not rows:
            return dict(state='missing',record=None)
        row = rows[0]
        if len({r['raw_mac'] for r in rows if r['response_received_at']==row['response_received_at']}) > 1:
            return dict(state='conflict',record=None)
        if (instant(row['response_received_at']).date()!=at.date()
                or (at-instant(row['response_received_at'])).total_seconds()>ttl_seconds
                or (row['observed_at'] and (at-instant(row['observed_at'])).total_seconds()>ttl_seconds)):
            return dict(state='expired',record=None)
        if stock['registered_at'] > row['response_received_at']:
            return dict(state='stock_changed',record=None)
        raw=mc.numeric(row['raw_mac'])
        normalized=mc.numeric(row['normalized_won'])
        if (not row['verified'] or row['verified_unit']!='hundred_million_KRW'
                or raw is None or raw<0 or normalized is None or normalized!=raw*Decimal(100000000)):
            return dict(state='unverified',record=None)
        if row['trade_date']!=at.date().isoformat() or instant(row['observed_at']).date()!=at.date():
            return dict(state='expired',record=None)
        return dict(state='hit',record=row,above_3000_eok=Decimal(row['normalized_won'])>Decimal('300000000000'))

    def refresh_plan(self, codes, *, environment, at, ttl_seconds=3600, limit=10):
        if type(limit) is not int or not 1<=limit<=20:
            raise ValueError('invalid_refresh_limit')
        results = [(code(c),self.capital(c,environment=environment,at=at,ttl_seconds=ttl_seconds)) for c in dict.fromkeys(codes)]
        eligible = [c for c,r in results if r['state'] in ('missing','expired','unverified','stock_changed')]
        return dict(environment=environment,requests=[{'stk_cd':c} for c in eligible[:limit]],
                    deferred=eligible[limit:],states={c:r['state'] for c,r in results},actual_api_calls=0)


class MasterStore(Queries):
    """Explicit development writer; one transaction per context, no migrations."""
    def __init__(self, path):
        path = writable_path(path).resolve(strict=True)
        db = sqlite3.connect(path.as_uri()+'?mode=rw',uri=True)
        db.row_factory = sqlite3.Row
        try:
            if db.execute('SELECT version FROM master_meta').fetchone()[0] != 1:
                raise ValueError('unsupported_master')
            db.execute('PRAGMA foreign_keys=ON')
        except BaseException:
            db.close(); raise
        super().__init__(db)

    def __enter__(self):
        try:
            self.db.execute('BEGIN IMMEDIATE')
        except BaseException:
            self.db.close()
            raise
        return self

    def __exit__(self, kind, *_):
        try:
            self.db.rollback() if kind else self.db.commit()
        finally:
            self.db.close()

    def put_stock(self, stock_code, stock_name, market, listing_status, *, source, updated_at, registered_at, verified=False):
        code(stock_code)
        if market not in ('KOSPI','KOSDAQ','UNKNOWN') or listing_status not in ('listed','delisted','unknown') or type(verified) is not bool:
            raise ValueError('invalid_stock_metadata')
        updated, registered = instant(updated_at), instant(registered_at)
        if updated > registered:
            raise ValueError('future_stock_revision')
        record = dict(stock_code=stock_code,stock_name=text(stock_name),market=market,listing_status=listing_status,
                      verified=verified,source=text(source),source_updated_at=updated.isoformat(),registered_at=registered.isoformat())
        self.db.execute('''INSERT INTO stock_master VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(stock_code) DO UPDATE SET
            stock_name=excluded.stock_name,market=excluded.market,listing_status=excluded.listing_status,verified=excluded.verified,
            source=excluded.source,source_updated_at=excluded.source_updated_at,registered_at=excluded.registered_at
            WHERE excluded.registered_at>=stock_master.registered_at''',tuple(record.values()))
        self.db.execute('INSERT OR IGNORE INTO stock_history(stock_code,registered_at,record_json,record_hash) VALUES(?,?,?,?)',
                        (stock_code,registered.isoformat(),json.dumps(record,ensure_ascii=False),digest(record)))

    def put_sector(self, sector_id, sector_name, *, kind, source, updated_at, active=True):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',sector_id) or kind not in ('industry','theme') or type(active) is not bool:
            raise ValueError('invalid_sector')
        self.db.execute('INSERT INTO sector_master VALUES(?,?,?,?,?,?)',
                        (sector_id,text(sector_name),kind,int(active),text(source),instant(updated_at).isoformat()))

    def put_tag(self, stock_code, sector_id, *, source, evidence, updated_at, registered_at,
                valid_from, valid_to=None, is_primary=False, status='proposed', source_sha256=None):
        code(stock_code)
        updated, registered, start = map(instant,(updated_at,registered_at,valid_from))
        end = instant(valid_to) if valid_to else None
        if updated>registered or start>registered or (end and end<=start) or type(is_primary) is not bool or status not in ('proposed','verified','rejected'):
            raise ValueError('invalid_tag_interval_or_status')
        values=(stock_code,sector_id,int(is_primary),status,text(evidence),text(source),updated.isoformat(),start.isoformat(),
                end.isoformat() if end else None,registered.isoformat(),source_sha256)
        self.db.execute('''INSERT OR IGNORE INTO stock_sector(stock_code,sector_id,is_primary,status,evidence,source,source_updated_at,
            valid_from,valid_to,registered_at,source_sha256,record_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',values+(digest(values),))

    def put_capital(self, body, *, requested_code, environment, request_started_at, response_received_at,
                    source, registered_at, proof=None, http_status=200):
        code(requested_code)
        parsed = mc.parse_response(body,requested_code=requested_code,environment=environment,
                    request_started_at=request_started_at,response_received_at=response_received_at,http_status=http_status)
        start, received, registered = map(instant,(request_started_at,response_received_at,registered_at))
        if not start<=received<=registered:
            raise ValueError('invalid_cache_interval')
        proof = proof or {}
        observed = mc.instant(proof.get('observed_at'))
        verified = bool(not parsed['issues'] and proof.get('for_record')==parsed['fingerprint']
            and proof.get('environment')==environment and proof.get('stock_code')==requested_code
            and proof.get('unit')=='hundred_million_KRW' and proof.get('unit_runtime_verified') is True
            and proof.get('market_cap_basis_verified') is True and proof.get('verified') is True
            and observed and observed.date()==received.date() and observed<=received
            and proof.get('trade_date')==received.date().isoformat() and isinstance(proof.get('evidence'),str) and proof['evidence'].strip())
        values=(requested_code,environment,'ka10001','ka10001-v1',parsed['raw'].get('mac'),'hundred_million_KRW',
                'hundred_million_KRW' if verified else None,parsed['market_cap_won_diagnostic'] if verified else None,
                proof['trade_date'] if verified else None,observed.isoformat() if observed else None,start.isoformat(),received.isoformat(),
                registered.isoformat(),text(source),int(verified),json.dumps(parsed,ensure_ascii=False),
                json.dumps({k:proof[k] for k in ('for_record','environment','stock_code','unit','unit_runtime_verified',
                    'market_cap_basis_verified','verified','observed_at','trade_date','evidence') if k in proof},ensure_ascii=False))
        self.db.execute('''INSERT OR IGNORE INTO market_cap_cache(stock_code,environment,api_id,contract_version,raw_mac,declared_unit,
            verified_unit,normalized_won,trade_date,observed_at,request_started_at,response_received_at,registered_at,source,verified,
            record_json,proof_json,record_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',values+(digest(values),))
        return parsed


@contextmanager
def read_master(path, *, synthetic):
    with readonly_database(path) as (db, provenance):
        row = db.execute('SELECT version,synthetic FROM master_meta').fetchone()
        if row is None or row['version']!=1 or row['synthetic']!=int(synthetic):
            raise ValueError('master_source_scope_mismatch')
        yield Queries(db), provenance


def import_stocks(path, document):
    if document.get('schema_version')!='stock-master-import-v1' or type(document.get('synthetic')) is not bool:
        raise ValueError('invalid_stock_import')
    records = document['records']
    codes = [code(r['stock_code']) for r in records]
    if len(set(codes)) != len(codes):
        raise ValueError('duplicate_import_code')
    with MasterStore(path) as store:
        if store.db.execute('SELECT synthetic FROM master_meta').fetchone()[0]!=int(document['synthetic']):
            raise ValueError('import_source_scope_mismatch')
        for r in records:
            store.put_stock(**r)
    return dict(imported=len(records),actual_api_calls=0)


def migrate_legacy(source, destination, *, registered_at, synthetic=False):
    """Development offline source only; preserve provenance, never approve tags."""
    source = Path(source).resolve(strict=True)
    if not source.is_relative_to(PROJECT.resolve()):
        raise ValueError('development_snapshot_source_required')
    with readonly_database(source) as (db, provenance):
        stocks = [dict(r) for r in db.execute('SELECT stock_code,stock_name,updated_at FROM stocks')]
        tags = [dict(r) for r in db.execute('''SELECT t.stock_code,s.sector_name,t.source,t.updated_at
            FROM stock_sectors t JOIN sectors s ON s.sector_id=t.sector_id''')]
    with MasterStore(destination) as store:
        if type(synthetic) is not bool or store.db.execute('SELECT synthetic FROM master_meta').fetchone()[0]!=int(synthetic):
            raise ValueError('migration_source_scope_mismatch')
        if store.db.execute('SELECT 1 FROM migration_log WHERE source_sha256=?',(provenance['sha256'],)).fetchone():
            return dict(state='already_imported',stocks=0,tags=0)
        conflicts=[]
        for r in stocks:
            # Unknown listing/market remain unknown; name/code are retained.
            existing = store.db.execute('SELECT stock_name FROM stock_master WHERE stock_code=?',(r['stock_code'],)).fetchone()
            if not existing:
                store.put_stock(r['stock_code'],r['stock_name'],'UNKNOWN','unknown',source='legacy_stocks',
                    updated_at=r['updated_at'],registered_at=registered_at,verified=False)
            elif existing['stock_name']!=r['stock_name']:
                conflicts.append(dict(stock_code=r['stock_code'],existing_name=existing['stock_name'],
                    legacy_name=r['stock_name'],legacy_updated_at=r['updated_at'],resolution='preserved_existing_needs_review'))
        for r in tags:
            sector_id = 'legacy_'+digest(r['sector_name'])[:16]
            if not store.db.execute('SELECT 1 FROM sector_master WHERE sector_id=?',(sector_id,)).fetchone():
                store.put_sector(sector_id,r['sector_name'],kind='theme',source='legacy_sector_name',updated_at=registered_at)
            store.put_tag(r['stock_code'],sector_id,source=r['source'],evidence='legacy_unreviewed',
                updated_at=r['updated_at'],registered_at=registered_at,valid_from=r['updated_at'],
                source_sha256=provenance['sha256'])
        report = dict(state='imported_proposed',stocks=len(stocks),tags=len(tags),source_sha256=provenance['sha256'],
                      stock_conflicts=conflicts)
        store.db.execute('INSERT INTO migration_log VALUES(?,?,?)',(provenance['sha256'],instant(registered_at).isoformat(),json.dumps(report)))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--name',required=True);init.add_argument('--synthetic',action='store_true')
    imp=sub.add_parser('import-stocks');imp.add_argument('--db',required=True,type=Path);imp.add_argument('--input',required=True,type=Path)
    mig=sub.add_parser('migrate-legacy');mig.add_argument('--db',required=True,type=Path);mig.add_argument('--source',required=True,type=Path);mig.add_argument('--registered-at',required=True);mig.add_argument('--synthetic',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='init': result={'path':str(create_master(args.name,synthetic=args.synthetic)),'stocks_imported':0}
    elif args.command=='import-stocks': result=import_stocks(args.db,json.loads(args.input.read_text(encoding='utf-8-sig')))
    else: result=migrate_legacy(args.source,args.db,registered_at=args.registered_at,synthetic=args.synthetic)
    print(json.dumps(result,ensure_ascii=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
