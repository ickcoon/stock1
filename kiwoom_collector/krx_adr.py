"""AQ: offline KRX breadth, explicit session gaps and ratio-of-sums ADR20.

AP source databases are opened read-only. Only new artifacts beneath krx-aq
are created. No transport, credentials, scheduler or operational store imports.
"""
import argparse
from collections import Counter, deque
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

from . import krx_daily as daily
from .krx_adr_prepare import rolling

ROOT = daily.master.PROJECT / 'diagnostics' / 'krx-aq'
SOURCE = daily.ROOT / 'ap2-20251001-20261030.db'
CHECKPOINT = daily.master.PROJECT / 'diagnostics/krx-ap2/20251001-20261030/progress.db'
MARKETS = ('KOSPI', 'KOSDAQ', 'COMBINED')
UNIVERSES = ('common', 'common_unknown_reference')
POLICY = 'aq-common-v1-official-change'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def readonly(path):
    path = Path(path).absolute()
    if not path.is_file() or path.resolve() != path:
        raise ValueError('existing_regular_source_required')
    db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def windowed(expected_days, counts):
    """Keep missing sessions in the window. Never backfill it with older days."""
    if expected_days != sorted(set(expected_days)):
        raise ValueError('sorted_unique_expected_sessions_required')
    window = deque(maxlen=20)
    for day in expected_days:
        g = counts.get(day)
        window.append((day, g))
        missing = [d for d, item in window if item is None or not item['complete']]
        state = ('insufficient_history' if len(window)<20 else
                 'missing_session' if missing else 'ready')
        up = sum(item['up'] for _, item in window) if not missing else None
        down = sum(item['down'] for _, item in window) if not missing else None
        value = None
        if state == 'ready':
            if down == 0:
                state = 'zero_declines'
            else:
                # Reuse AP-2's exact Decimal formula after the completeness gate.
                values = [(d, item['up'], item['down'], item['flat']) for d,item in window]
                value = list(rolling(values))[-1][-1]
        yield dict(day=day, window_start=window[0][0], window_end=day,
                   window_dates=[d for d,_ in window], window_sessions=len(window),
                   missing_dates=missing, up_sum=up, down_sum=down, value=value,
                   status='provisional' if value is not None else state)


def eligibility(row, include_unknown=False):
    product = row['product_type']
    if product == 'UNKNOWN':
        if not include_unknown:
            return 'unknown_product'
        if '스팩' in (row['name'] or '') or '기업인수목적' in (row['name'] or ''):
            return 'spac_hint'
    elif product != 'common_stock':
        return 'excluded_product'
    if not row['stock_code'] or not row['standard_code'] or row['master_basis_date'] != row['day'] or not row['master_body_hash']:
        return 'unverified_identity'
    if row['volume'] is None:
        return 'missing_volume'
    if Decimal(row['volume']) == 0:
        return 'zero_volume_unclassified'
    if Decimal(row['volume']) < 0:
        return 'invalid_volume'
    if any(row[k] is None or Decimal(row[k]) <= 0 for k in ('open','high','low','close')):
        return 'price_unformed'
    if row['is_new_listing']:
        return 'new_listing'
    if row['change_price'] is None:
        return 'missing_change'
    if json.loads(row['issues_json']):
        return 'source_issues'
    return None


def blank(day, market, universe):
    return dict(day=day, market=market, universe=universe, total=0, up=0, down=0,
                flat=0, eligible=0, excluded=0, unknown=0, unknown_eligible=0,
                zero_volume=0, official_change_mismatch=0, mismatch_used=0,
                excluded_reasons={}, complete=False, snapshot_ids=[])


def aggregate(rows, expected_days, available):
    """Stream records in date order, retaining only the prior session's identities."""
    result = {}
    previous = {}
    prior_day = {d:expected_days[i-1] if i else None for i,d in enumerate(expected_days)}
    anomalies = []
    for r in rows:
        old = previous.get((r['market'],r['stock_code'])) if r['stock_code'] else None
        mismatch = bool(old and old['day'] == prior_day.get(r['day']) and old['standard_code'] == r['standard_code']
                        and all(x is not None for x in (r['close'],r['change_price'],old['close']))
                        and Decimal(r['close'])-Decimal(old['close']) != Decimal(r['change_price']))
        change = Decimal(r['change_price']) if r['change_price'] is not None else None
        direction = None if change is None else 'up' if change>0 else 'down' if change<0 else 'flat'
        if direction is not None and direction != r['direction']:
            raise ValueError('stored_direction_disagrees_with_official_change')
        if mismatch:
            anomalies.append(dict(day=r['day'],market=r['market'],stock_code=r['stock_code'],
                previous_close=old['close'],close=r['close'],official_change=r['change_price'],
                direction=direction,validation='reference_difference_official_change_used_when_eligible'))
        for universe in UNIVERSES:
            key = (r['day'],r['market'],universe)
            g = result.setdefault(key,blank(*key))
            g['total'] += 1
            g['unknown'] += r['product_type']=='UNKNOWN'
            g['zero_volume'] += r['volume'] is not None and Decimal(r['volume'])==0
            g['official_change_mismatch'] += mismatch
            if r['snapshot_id'] not in g['snapshot_ids']:
                g['snapshot_ids'].append(r['snapshot_id'])
            reason = eligibility(r,universe==UNIVERSES[1])
            if reason:
                g['excluded'] += 1
                g['excluded_reasons'][reason] = g['excluded_reasons'].get(reason,0)+1
            else:
                g['eligible'] += 1
                g[direction] += 1
                g['unknown_eligible'] += r['product_type']=='UNKNOWN'
                g['mismatch_used'] += mismatch
        if r['stock_code']:
            previous[(r['market'],r['stock_code'])] = dict(r)
    for day in expected_days:
        for universe in UNIVERSES:
            for market in MARKETS[:2]:
                key = day,market,universe
                g = result.setdefault(key,blank(*key))
                g['complete'] = bool(g['total'] and (day,market) in available)
            combined = blank(day,'COMBINED',universe)
            parts = [result[(day,m,universe)] for m in MARKETS[:2]]
            for field in ('total','up','down','flat','eligible','excluded','unknown','unknown_eligible',
                          'zero_volume','official_change_mismatch','mismatch_used'):
                combined[field] = sum(g[field] for g in parts)
            reasons = Counter()
            for g in parts:
                reasons.update(g['excluded_reasons'])
            combined['excluded_reasons'] = dict(reasons)
            combined['snapshot_ids'] = [s for g in parts for s in g['snapshot_ids']]
            combined['complete'] = all(g['complete'] for g in parts)
            result[(day,'COMBINED',universe)] = combined
    return result, anomalies


def calendar(source_db, checkpoint, start, end):
    """Checkpoint is independent of current rows, so deleted sessions stay expected.

    Both-market empty responses are excluded only as observed unavailable dates;
    this calendar remains explicitly provisional, not an official holiday list.
    """
    with closing(readonly(checkpoint)) as db:
        if tuple(db.execute('SELECT version FROM backfill_meta').fetchone()) != (1,):
            raise ValueError('checkpoint_schema_mismatch')
        states = dict(db.execute('SELECT day,state FROM dates'))
    market_states = {(r['day'],r['market']):r['last_status'] for r in source_db.execute('SELECT * FROM market_days')}
    expected, empty = [], []
    for d in daily.candidate_sessions(start,end):
        if states.get(d)=='empty_unclassified' and all(market_states.get((d,m))=='empty_unclassified' for m in MARKETS[:2]):
            empty.append(d)
        else:
            expected.append(d)
    return expected, empty


def build(source, checkpoint, output, *, incremental=False, lightweight=False):
    source, checkpoint, output = map(lambda p:Path(p).absolute(),(source,checkpoint,output))
    expected_source = daily.ROOT/'as-incremental.db' if incremental else SOURCE
    if lightweight:
        if (not source.parent.name.startswith('lightweight-') or source.name!='daily-120.db'
                or source.parent.parent != (daily.master.PROJECT/'diagnostics').absolute()):
            raise ValueError('owned_lightweight_daily_source_required')
        expected_source = source
    expected_checkpoint = daily.master.PROJECT/'diagnostics/krx-as/progress.db' if incremental else CHECKPOINT
    if (source != expected_source.absolute() or checkpoint != expected_checkpoint.absolute() or
        source.resolve()!=source or checkpoint.resolve()!=checkpoint or
        output.resolve()!=output or ROOT.resolve()!=ROOT.absolute() or
        not output.is_relative_to(ROOT.absolute())):
        raise ValueError('isolated_aq_artifacts_required')
    if output.exists():
        raise ValueError('new_output_directory_required')
    before = sha(source)
    with closing(readonly(source)) as db:
        db.execute('BEGIN')
        if tuple(db.execute('SELECT version,synthetic FROM meta').fetchone()) != (1,0):
            raise ValueError('live_daily_source_required')
        integrity = [r[0] for r in db.execute('PRAGMA integrity_check')]
        foreign = [tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
        duplicates = db.execute('SELECT COUNT(*) FROM (SELECT day,market,official_code_raw FROM daily_latest GROUP BY 1,2,3 HAVING COUNT(*)>1)').fetchone()[0]
        if integrity!=['ok'] or foreign or duplicates:
            raise ValueError('source_integrity_gate_failed')
        view = 'adr_source_latest' if lightweight else 'daily_latest'
        bounds = db.execute(f'SELECT min(day),max(day) FROM {view}').fetchone()
        if incremental and not bounds[0]:
            raise ValueError('nonempty_incremental_source_required')
        start, end = tuple(bounds) if incremental or lightweight else ('2025-10-01','2026-10-30')
        days, empties = calendar(db,checkpoint,start,end)
        coverage = [dict(r) for r in db.execute('''SELECT d.day,d.market,
            (SELECT COUNT(*) FROM raw_rows WHERE snapshot_id=d.current_snapshot) AS expected_rows,
            (SELECT COUNT(*) FROM bars WHERE snapshot_id=d.current_snapshot) AS actual_rows,
            (SELECT COUNT(*) FROM raw_rows WHERE snapshot_id=d.current_snapshot AND error IS NOT NULL) AS invalid_rows
            FROM market_days d WHERE d.current_snapshot IS NOT NULL AND d.day BETWEEN ? AND ?''',(start,end))]
        available = {(r['day'],r['market']) for r in coverage
                     if r['expected_rows']>0 and r['expected_rows']==r['actual_rows'] and not r['invalid_rows']}
        groups, anomalies = aggregate(db.execute(f'SELECT * FROM {view} ORDER BY day,market,official_code_raw'),days,available)
    series = []
    for universe in UNIVERSES:
        for market in MARKETS:
            counts = {d:groups[(d,market,universe)] for d in days}
            for row in windowed(days,counts):
                series.append(dict(**row,market=market,universe=universe,**{k:v for k,v in counts[row['day']].items() if k not in ('day','market','universe')}))
    after = sha(source)
    if before != after:
        raise ValueError('source_changed_during_calculation')
    valid = [r['day'] for r in series if r['market']=='COMBINED' and r['universe']=='common' and r['value'] is not None]
    report = dict(policy=POLICY,source=str(source),source_sha256=before,source_unchanged=before==after,
        checkpoint_sha256=sha(checkpoint),source_integrity=integrity,foreign_key_violations=foreign,
        duplicate_groups=duplicates,created_at=datetime.now(timezone.utc).isoformat(),
        expected_sessions=len(days),complete_sessions=sum(groups[(d,'COMBINED','common')]['complete'] for d in days),
        records=sum(groups[(d,'COMBINED','common')]['total'] for d in days),
        calendar_status='provisional_observed_both_market_availability',empty_dates=empties,
        valid_range=[valid[0],valid[-1]] if valid else [],valid_sessions=len(valid),
        missing_dates=[d for d in days if not groups[(d,'COMBINED','common')]['complete']],
        coverage_mismatches=[r for r in coverage if (r['day'],r['market']) not in available],
        latest={m:{u:next((r for r in reversed(series) if r['market']==m and r['universe']==u),None) for u in UNIVERSES} for m in MARKETS},
        reference_mismatches=len(anomalies),reference_mismatch_records=anomalies,
        policy_notes=['official CMPPREVDD_PRC determines direction; reference differences retained',
            'common: officially classified common_stock, dated verified identity, formed OHLC, positive volume',
            'exclude preferred/REIT/ETF/ETN/SPAC, UNKNOWN, new listing day, source issues',
            'reference: add eligible UNKNOWN except explicit SPAC name hints; not a confirmed ordinary-stock universe',
            'zero-volume is untraded/unclassified, not confirmed suspension; flats excluded from A/D',
            'all numeric values provisional: observed-empty calendar and product coverage need review',
            'source amounts remain unconverted; no KRX API calls; retrospective revisions, not point-in-time backtest data'])
    output.mkdir(parents=True)
    with closing(sqlite3.connect(output/'adr20.db')) as db, db:
        db.executescript('''CREATE TABLE aq_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE breadth_daily(day TEXT,market TEXT,universe TEXT,total INTEGER,eligible INTEGER,
        advances INTEGER,declines INTEGER,unchanged INTEGER,excluded INTEGER,unknown INTEGER,zero_volume INTEGER,
        complete INTEGER,details_json TEXT,PRIMARY KEY(day,market,universe));
        CREATE TABLE adr20(day TEXT,market TEXT,universe TEXT,window_start TEXT,window_end TEXT,
        window_sessions INTEGER,up_sum INTEGER,down_sum INTEGER,value TEXT,status TEXT,window_dates_json TEXT,
        missing_dates_json TEXT,PRIMARY KEY(day,market,universe));''')
        db.execute('INSERT INTO aq_meta VALUES(?,?)',('report',json.dumps(report,ensure_ascii=False)))
        for r in series:
            db.execute('INSERT INTO breadth_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                tuple(r[k] for k in ('day','market','universe','total','eligible','up','down','flat','excluded','unknown','zero_volume','complete'))+(json.dumps(r,ensure_ascii=False),))
            db.execute('INSERT INTO adr20 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                tuple(r[k] for k in ('day','market','universe','window_start','window_end','window_sessions','up_sum','down_sum','value','status'))+
                (json.dumps(r['window_dates']),json.dumps(r['missing_dates'])))
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('output_integrity_failed')
    payload = dict(report=report,series=series)
    (output/'adr20.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf8')
    from .krx_adr_chart import render
    (output/'adr20.html').write_text(render(payload),encoding='utf8')
    return payload


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source',type=Path)
    p.add_argument('--incremental',action='store_true')
    p.add_argument('--lightweight',action='store_true')
    args=p.parse_args()
    incremental=args.incremental or args.lightweight
    checkpoint=daily.master.PROJECT/'diagnostics/krx-as/progress.db' if incremental else CHECKPOINT
    source=args.source or (daily.ROOT/'as-incremental.db' if incremental else SOURCE)
    result=build(source,checkpoint,args.output,incremental=incremental,lightweight=args.lightweight)
    print(json.dumps({k:result['report'][k] for k in ('expected_sessions','complete_sessions','records','valid_range','valid_sessions','missing_dates','reference_mismatches','source_unchanged')}))


if __name__=='__main__':
    main()
