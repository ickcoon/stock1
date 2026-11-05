"""AP-1 offline market-wide KRX daily staging with immutable response revisions.

The C14 supply store is chart-request scoped and assumes Kiwoom units. This
independent store reuses its canonical encoding, KRX parsers and AO identifiers
without asserting chart adjustment/amount units or publishing into C14.
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from . import krx_openapi as krx, krx_master as ao, master_data as master
from .daily_supply import encode
from .schedule import load_holidays

ROOT = master.PROJECT / 'diagnostics' / 'krx-daily'
NUMBERS = {'open': 'TDD_OPNPRC', 'high': 'TDD_HGPRC', 'low': 'TDD_LWPRC',
           'close': 'TDD_CLSPRC', 'change_price': 'CMPPREVDD_PRC', 'change_pct': 'FLUC_RT',
           'volume': 'ACC_TRDVOL', 'turnover_raw': 'ACC_TRDVAL', 'market_cap_raw': 'MKTCAP',
           'listed_shares': 'LIST_SHRS'}
SCHEMA = '''
CREATE TABLE meta(version INTEGER PRIMARY KEY CHECK(version=1),synthetic INTEGER NOT NULL);
CREATE TABLE snapshots(id TEXT PRIMARY KEY,day TEXT NOT NULL,market TEXT NOT NULL,service TEXT NOT NULL,
 source_kind TEXT NOT NULL,received_at TEXT NOT NULL,body_json TEXT,receipt_json TEXT NOT NULL);
CREATE TABLE attempts(receipt_hash TEXT PRIMARY KEY,snapshot_id TEXT REFERENCES snapshots(id),
 day TEXT NOT NULL,market TEXT NOT NULL,http_status INTEGER,status TEXT NOT NULL);
CREATE TABLE raw_rows(snapshot_id TEXT NOT NULL REFERENCES snapshots(id),n INTEGER NOT NULL,
 raw_json TEXT NOT NULL,error TEXT,PRIMARY KEY(snapshot_id,n));
CREATE TABLE bars(snapshot_id TEXT NOT NULL REFERENCES snapshots(id),day TEXT NOT NULL,market TEXT NOT NULL,
 official_code_raw TEXT NOT NULL,stock_code TEXT,standard_code TEXT,name TEXT,
 open TEXT,high TEXT,low TEXT,close TEXT,change_price TEXT,change_pct TEXT,volume TEXT,
 turnover_raw TEXT,market_cap_raw TEXT,listed_shares TEXT,turnover_won TEXT,market_cap_won TEXT,
 product_type TEXT NOT NULL,master_basis_date TEXT,master_body_hash TEXT,
 is_new_listing INTEGER,halt_status INTEGER,trading_state TEXT NOT NULL,direction TEXT NOT NULL,
 collected_at TEXT NOT NULL,source_kind TEXT NOT NULL,validation_status TEXT NOT NULL,issues_json TEXT NOT NULL,
 PRIMARY KEY(snapshot_id,day,market,official_code_raw));
CREATE TABLE market_days(day TEXT NOT NULL,market TEXT NOT NULL,current_snapshot TEXT REFERENCES snapshots(id),
 last_status TEXT NOT NULL,PRIMARY KEY(day,market));
CREATE TABLE missing_sessions(day TEXT NOT NULL,market TEXT NOT NULL,reason TEXT NOT NULL,
 PRIMARY KEY(day,market));
CREATE VIEW daily_latest AS SELECT b.* FROM bars b JOIN market_days d
 ON d.day=b.day AND d.market=b.market AND d.current_snapshot=b.snapshot_id;
'''


def candidate_sessions(start, end, *, holidays=None):
    """Weekday candidates only. Nonempty dated KRX responses confirm availability."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    excluded = load_holidays(holidays)
    if first > last:
        raise ValueError('invalid_date_range')
    return [(first + timedelta(days=i)).isoformat() for i in range((last-first).days+1)
            if (first+timedelta(days=i)).weekday() < 5 and first+timedelta(days=i) not in excluded]


def normalized(raw, *, service, day, origin, masters):
    """Fill absent numeric fields only in parser input; raw archive stays unchanged."""
    shadow = dict(raw)
    for field in NUMBERS.values():
        if shadow.get(field) is None:
            shadow[field] = '-'
    parsed = krx.parse({'OutBlock_1': [shadow]}, service=service, bas_dd=day.replace('-', ''),
                       source_kind=origin, base_records=masters)['records'][0]
    numbers = {key: krx.decimal_value(shadow[field]) for key, field in NUMBERS.items()}
    issues = list(parsed['issues'])
    matched = next((r for r in masters if r['stock_code'] == parsed['stock_code']), None)
    verified = parsed['identity_master_join_matches']
    classification = ao.classify(matched) if matched and verified else {'product_type': 'UNKNOWN'}
    volume, change = numbers['volume'], numbers['change_price']
    state = 'missing_volume' if volume is None else 'zero_volume_unclassified' if volume == 0 else 'traded'
    direction = 'missing' if change is None else 'up' if change > 0 else 'down' if change < 0 else 'flat'
    if change is not None and numbers['close'] is not None and numbers['change_pct'] is not None:
        base = numbers['close'] - change
        if base > 0 and abs(100 * change / base - numbers['change_pct']) > Decimal('0.011'):
            issues.append('change_rate_reference_mismatch')
    cap, close, shares = (numbers[k] for k in ('market_cap_raw', 'close', 'listed_shares'))
    if None not in (cap, close, shares) and cap != close * shares:
        issues.append('capitalization_price_shares_mismatch')
    if any(numbers[k] is None for k in ('open', 'high', 'low', 'close', 'volume')):
        issues.append('ohlcv_missing')
    return dict(official_code_raw=raw['ISU_CD'], stock_code=parsed['stock_code'] if verified else None,
                standard_code=parsed['standard_code'] if verified else None, name=raw.get('ISU_NM'),
                **{k: str(v) if v is not None else None for k, v in numbers.items()},
                turnover_won=None, market_cap_won=None, product_type=classification['product_type'],
                master_basis_date=matched['basis_date'] if verified else None,
                is_new_listing=int(parsed['is_new_listing']) if verified else None,
                halt_status=None, trading_state=state, direction=direction,
                validation_status='parsed_with_issues' if issues else 'schema_and_dated_identity_checked',
                issues_json=encode(issues))


class DailyStore:
    def __init__(self, path, *, synthetic=False):
        path = Path(path).absolute()
        allowed = ROOT.absolute()
        if (path.resolve() != path or allowed.resolve() != allowed
                or not path.is_relative_to(allowed) or path.suffix != '.db'):
            raise ValueError('independent_krx_daily_directory_required')
        self.path, self.synthetic = path, synthetic
        if path.exists():
            with ao.read_db(path) as db:
                if tuple(db.execute('SELECT version,synthetic FROM meta').fetchone()) != (1, int(synthetic)):
                    raise ValueError('daily_schema_or_origin_mismatch')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb'):
                pass
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(SCHEMA)
                db.execute('INSERT INTO meta VALUES(1,?)', (int(synthetic),))

    def ingest(self, receipt, *, masters=(), master_body_hash=None):
        day, service = receipt['basis_date'], receipt['service']
        if service not in ('stk_bydd_trd', 'ksq_bydd_trd'):
            raise ValueError('daily_service_required')
        market = krx.SERVICES[service][0]
        date.fromisoformat(day)
        origin = 'synthetic_fixture' if self.synthetic else 'krx_live'
        if receipt.get('source_kind') != origin:
            raise ValueError('daily_origin_mismatch')
        if any(m['source_kind'] != origin or m['basis_date'] != day or m['market'] != market for m in masters):
            raise ValueError('same_date_market_master_required')
        if masters and master_body_hash != master.digest({'OutBlock_1': [r['raw'] for r in masters]}):
            raise ValueError('dated_master_hash_mismatch')
        by_short = {r['stock_code']: r for r in masters}
        by_standard = {r['standard_code']: r for r in masters}
        if len(by_short) != len(masters) or len(by_standard) != len(masters):
            raise ValueError('ambiguous_dated_master')
        received = receipt.get('response_received_at') or receipt.get('attempt_finished_at')
        master.instant(received)
        body = receipt.get('raw_response')
        identity = master.digest([day, service, origin, body, master_body_hash])
        attempt = master.digest([receipt, master_body_hash])
        rows = body.get('OutBlock_1') if isinstance(body, dict) else None
        if isinstance(rows, list) and len(rows) > 10000:
            raise ValueError('response_row_limit')
        good = receipt.get('http_status') == 200 and isinstance(rows, list) and bool(rows)
        status = 'ready' if good else 'empty_unclassified' if rows == [] and receipt.get('http_status') == 200 else 'request_or_contract_failed'
        staged, raw_rows, seen = [], [], set()
        if good:
            for n, row in enumerate(rows):
                error = None
                try:
                    supplied = row.get('ISU_CD') if isinstance(row, dict) else None
                    matched = by_short.get(supplied) or by_standard.get(supplied)
                    result = normalized(row, service=service, day=day, origin=origin,
                                        masters=[matched] if matched else [])
                    if result['official_code_raw'] in seen:
                        raise ValueError('duplicate_daily_identifier')
                    seen.add(result['official_code_raw'])
                    staged.append(result)
                except (ValueError, TypeError, KeyError) as exc:
                    error = str(exc) if isinstance(exc, ValueError) else 'invalid_row_structure'
                raw_rows.append((identity, n, encode(row), error))
            if any(r[3] for r in raw_rows):
                status = 'quarantined_invalid_rows'
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=rw', uri=True)) as db, db:
            db.execute('PRAGMA foreign_keys=ON')
            if db.execute('SELECT 1 FROM attempts WHERE receipt_hash=?', (attempt,)).fetchone():
                return dict(state='already_imported', day=day, market=market)
            existing = db.execute('SELECT 1 FROM snapshots WHERE id=?', (identity,)).fetchone()
            current = db.execute('SELECT d.current_snapshot,s.received_at FROM market_days d '
                'LEFT JOIN snapshots s ON d.current_snapshot=s.id WHERE d.day=? AND d.market=?', (day, market)).fetchone()
            publishable = status == 'ready'
            stale = bool(publishable and current and current[1]
                         and master.instant(received) < master.instant(current[1]))
            if stale:
                status = 'stale_receipt_preserved_history'
            db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?,?,?)',
                       (identity, day, market, service, origin, received, encode(body), encode(receipt)))
            db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',
                       (attempt, identity, day, market, receipt.get('http_status'), status))
            if not existing:
                db.executemany('INSERT INTO raw_rows VALUES(?,?,?,?)', raw_rows)
                if publishable:
                    for r in staged:
                        values = dict(snapshot_id=identity, day=day, market=market, **r,
                                      master_body_hash=master_body_hash, collected_at=received, source_kind=origin)
                        columns = ','.join('"'+c+'"' for c in values)
                        db.execute('INSERT INTO bars('+columns+') VALUES('+','.join('?' for _ in values)+')', tuple(values.values()))
            db.execute('INSERT INTO market_days VALUES(?,?,?,?) ON CONFLICT(day,market) DO UPDATE SET '
                       'current_snapshot=CASE WHEN excluded.current_snapshot IS NOT NULL THEN excluded.current_snapshot '
                       'ELSE market_days.current_snapshot END,last_status=excluded.last_status',
                       (day, market, identity if publishable and not stale else None, status))
            if publishable:
                db.execute('DELETE FROM missing_sessions WHERE day=? AND market=?', (day, market))
            elif not current or current[0] is None:
                db.execute('INSERT INTO missing_sessions VALUES(?,?,?) ON CONFLICT(day,market) DO UPDATE SET reason=excluded.reason',
                           (day, market, status))
        return dict(state=status, day=day, market=market, rows=len(staged), invalid_rows=sum(bool(r[3]) for r in raw_rows))

    def record_unattempted(self, days):
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=rw', uri=True)) as db, db:
            for day in days:
                date.fromisoformat(day)
                for market in ('KOSPI', 'KOSDAQ'):
                    if not db.execute('SELECT 1 FROM market_days WHERE day=? AND market=?', (day, market)).fetchone():
                        db.execute('INSERT OR IGNORE INTO missing_sessions VALUES(?,?,?)', (day, market, 'not_attempted'))

    def report(self):
        with ao.read_db(self.path) as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM daily_latest ORDER BY day,market,official_code_raw')]
            missing = [dict(r) for r in db.execute('SELECT * FROM missing_sessions ORDER BY day,market')]
            errors = [dict(r) for r in db.execute('SELECT snapshot_id,n,error FROM raw_rows WHERE error IS NOT NULL')]
            integrity = [r[0] for r in db.execute('PRAGMA integrity_check')]
            fk = [tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
            expected_days = [tuple(r) for r in db.execute('SELECT day,market FROM market_days UNION SELECT day,market FROM missing_sessions')]
        groups, prior, anomalies = {}, {}, []
        previous_day = {}
        for market in ('KOSPI', 'KOSDAQ'):
            days = sorted({d for d, m in expected_days if m == market})
            previous_day.update({(d, market): days[i-1] if i else None for i, d in enumerate(days)})
        for r in rows:
            key = (r['day'], r['market'])
            g = groups.setdefault(key, dict(day=r['day'], market=r['market'], rows=0, identity_verified=0,
                ohlcv_complete=0, missing_turnover=0, missing_cap=0, price_unformed=0,
                zero_volume=0, missing_volume=0, up=0, down=0, flat=0, missing_change=0,
                change_rate_mismatch=0, capitalization_mismatch=0, previous_close_compared=0,
                previous_close_difference_mismatch=0, new_listings=0, adr_common_candidate=0,
                product_types={}, duplicate_identifiers=0))
            g['rows'] += 1
            g['identity_verified'] += r['stock_code'] is not None
            g['ohlcv_complete'] += all(r[k] is not None for k in ('open','high','low','close','volume'))
            g['missing_turnover'] += r['turnover_raw'] is None
            g['missing_cap'] += r['market_cap_raw'] is None
            formed = all(r[k] is not None and Decimal(r[k]) > 0 for k in ('open','high','low','close'))
            g['price_unformed'] += not formed
            g['zero_volume'] += r['trading_state'] == 'zero_volume_unclassified'
            g['missing_volume'] += r['trading_state'] == 'missing_volume'
            g[r['direction'] if r['direction'] != 'missing' else 'missing_change'] += 1
            issues = json.loads(r['issues_json'])
            g['change_rate_mismatch'] += 'change_rate_reference_mismatch' in issues
            g['capitalization_mismatch'] += 'capitalization_price_shares_mismatch' in issues
            g['new_listings'] += bool(r['is_new_listing'])
            cross = False
            old = prior.get((r['market'], r['stock_code'])) if r['stock_code'] else None
            if (old and old['day'] == previous_day[key] and old['standard_code'] == r['standard_code']
                    and r['change_price'] is not None and r['close'] is not None and old['close'] is not None):
                g['previous_close_compared'] += 1
                cross = Decimal(r['close'])-Decimal(old['close']) != Decimal(r['change_price'])
                g['previous_close_difference_mismatch'] += cross
                if cross:
                    anomalies.append(dict(day=r['day'], market=r['market'], stock_code=r['stock_code'],
                        prior_day=old['day'], prior_close=old['close'], close=r['close'],
                        reported_change=r['change_price'], computed_change=str(Decimal(r['close'])-Decimal(old['close'])),
                        current_snapshot=r['snapshot_id'], prior_snapshot=old['snapshot_id'],
                        reason='corporate_action_or_reference_price_review_required'))
            eligible = (r['product_type'] == 'common_stock' and r['stock_code'] is not None
                        and formed and r['trading_state'] == 'traded' and not r['is_new_listing']
                        and r['direction'] != 'missing' and not issues and not cross)
            g['adr_common_candidate'] += bool(eligible)
            g['product_types'][r['product_type']] = g['product_types'].get(r['product_type'], 0)+1
            if r['stock_code']:
                prior[(r['market'], r['stock_code'])] = r
        return dict(records=len(rows), market_days=list(groups.values()), missing_sessions=missing,
                    previous_close_anomalies=anomalies,
                    invalid_raw_rows=errors, integrity_check=integrity, foreign_key_violations=fk,
                    amount_units='unverified_no_conversion', adr20_calculated=False,
                    adjustment_basis='unverified', automatic_trading=False)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--receipts', type=Path, required=True)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True)
    p.add_argument('--synthetic', action='store_true')
    a = p.parse_args(argv)
    days = candidate_sessions(a.start, a.end)
    if len(days) > 6:
        raise ValueError('AP1_maximum_six_session_candidates')
    store = DailyStore(a.db, synthetic=a.synthetic)
    for day in days:
        for prefix, market in (('stk', 'KOSPI'), ('ksq', 'KOSDAQ')):
            compact = day.replace('-', '')
            daily = a.receipts / (prefix+'_bydd_trd_'+compact+'.json')
            base = a.receipts / (prefix+'_isu_base_info_'+compact+'.json')
            if not daily.exists():
                continue
            records, evidence = ao.load_snapshot(base, service=prefix+'_isu_base_info', basis_date=day,
                                                 synthetic=a.synthetic) if base.exists() else ([], {})
            result = store.ingest(json.loads(daily.read_text(encoding='utf8')), masters=records,
                                  master_body_hash=evidence.get('body_sha256'))
            print(json.dumps(result))
    store.record_unattempted(days)
    report = store.report()
    target = store.path.with_suffix('.report.json')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
