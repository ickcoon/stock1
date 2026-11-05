"""C12-AS independent, append-only daily updater. No Collector or scheduler wiring."""
import argparse
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import uuid

from . import krx_daily as daily, krx_master as master, krx_openapi as api
from . import krx_backfill as backfill, krx_adr as adr

PROJECT = api.PROJECT
ROOT = PROJECT / 'diagnostics/krx-as'
DATABASE = daily.ROOT / 'as-incremental.db'
SEED = daily.ROOT / 'ap2-20251001-20261030.db'
LEGACY_PROGRESS = backfill.ROOT / '20251001-20261030/progress.db'
KST = timezone(timedelta(hours=9))
MARKETS = ('KOSPI', 'KOSDAQ')
SAFE_REASONS = frozenset(('receipt_scope_or_origin_mismatch','new_daily_stage_required',
    'daily_stage_validation_failed','dated_master_coverage_incomplete','incomplete_daily_numeric_fields',
    'entire_market_unformed','daily_identity_or_price_quality_failed','market_row_count_drop_requires_review',
    'cross_market_master_duplicate','authentication_or_tls_requires_review','backfill_request_budget_exhausted'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with temp.open('x', encoding='utf8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
    os.replace(temp, path)


@contextmanager
def run_lock(root=ROOT):
    """OS-released lock; a crashed process does not leave a stale lock to delete."""
    root.mkdir(parents=True, exist_ok=True)
    with (root/'updater.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError('another_incremental_process_is_running') from None
            try:
                yield
            finally:
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def integrity(path):
    with closing(adr.readonly(path)) as db:
        checks = [r[0] for r in db.execute('PRAGMA integrity_check')]
        foreign = [tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
    if checks != ['ok'] or foreign:
        raise ValueError('sqlite_integrity_gate_failed')
    return dict(integrity_check=checks, foreign_key_violations=foreign)


def backup_new(source, destination):
    """SQLite online backup, then atomic publish. Never overwrite a database."""
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + '.backup-' + uuid.uuid4().hex)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(temp)) as dst:
            src.backup(dst, pages=4096)
    integrity(temp)
    temp.rename(destination)


def completed_days(path):
    with closing(adr.readonly(path)) as db:
        # Do not trust only a checkpoint label or the presence of two pointers.
        rows = db.execute('''SELECT d.day,d.market FROM market_days d
          JOIN snapshots s ON s.id=d.current_snapshot
          WHERE s.day=d.day AND s.market=d.market AND s.source_kind IN ('krx_live','synthetic_fixture')
          AND (SELECT count(*) FROM bars WHERE snapshot_id=s.id)>0
          AND (SELECT count(*) FROM bars WHERE snapshot_id=s.id)=
              (SELECT count(*) FROM raw_rows WHERE snapshot_id=s.id)
          AND NOT EXISTS(SELECT 1 FROM raw_rows WHERE snapshot_id=s.id AND error IS NOT NULL)''').fetchall()
    groups = {}
    for day, market in rows:
        groups.setdefault(day, set()).add(market)
    return {day for day, markets in groups.items() if markets == set(MARKETS)}


def bootstrap():
    # Strict fixed destinations keep production, master and theme stores out of reach.
    daily.DailyStore(SEED)
    backup_new(SEED, DATABASE)
    daily.DailyStore(DATABASE)
    checkpoint = ROOT/'progress.db'
    backup_new(LEGACY_PROGRESS, checkpoint)
    with closing(sqlite3.connect(checkpoint)) as db, db:
        if db.execute('SELECT version FROM backfill_meta').fetchone() != (1,):
            raise ValueError('incremental_checkpoint_schema_mismatch')
        db.execute('CREATE TABLE IF NOT EXISTS as_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO as_meta VALUES(?,?)',
                   ('bootstrap_last_day', max(completed_days(DATABASE))))
    return checkpoint


def revision(path):
    with closing(adr.readonly(path)) as db:
        return daily.master.digest([tuple(r) for r in db.execute(
            'SELECT day,market,current_snapshot FROM market_days ORDER BY day,market')])


def plan(path, checkpoint, end, *, start=None, retry_history=False):
    date.fromisoformat(end)
    complete = completed_days(path)
    last = max(complete) if complete else None
    if not last and not start:
        raise ValueError('initial_start_date_required')
    first = start or (date.fromisoformat(last)+timedelta(days=1)).isoformat()
    pending = set()
    with master.read_db(checkpoint) as db:
        bootstrap_day = db.execute("SELECT value FROM as_meta WHERE key='bootstrap_last_day'").fetchone()[0]
        pending.update(r[0] for r in db.execute('SELECT day FROM dates WHERE day>?', (bootstrap_day,)))
    with closing(adr.readonly(path)) as db:
        pending.update(r[0] for r in db.execute(
            "SELECT day FROM missing_sessions WHERE reason!='empty_unclassified' OR ?", (int(retry_history),)))
    candidates = set(daily.candidate_sessions(first, end)) if first <= end else set()
    candidates.update(d for d in pending if d <= end and (not start or d >= start))
    return last, sorted(candidates - complete)


class Updater:
    def __init__(self, store, checkpoint, run_dir, receipt, *, now=None):
        self.store, self.checkpoint, self.run_dir = store, Path(checkpoint), Path(run_dir)
        self.receipt = receipt
        self.now = now or datetime.now(KST)
        if self.now.tzinfo is None:
            raise ValueError('timezone_aware_clock_required')
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.results = []

    def save(self, day, state, details):
        result = dict(day=day, state=state, details=details)
        with closing(sqlite3.connect(self.checkpoint)) as db, db:
            db.execute('INSERT INTO dates VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET '
                       'state=excluded.state,details_json=excluded.details_json',
                       (day,state,daily.encode(details)))
        self.results.append(result)
        write_json(self.run_dir/'dates.json', self.results)
        print(json.dumps(dict(day=day,state=state),ensure_ascii=True),flush=True)
        return result

    def get(self, day, service):
        receipt, path = self.receipt(day, service)
        expected = 'synthetic_fixture' if self.store.synthetic else 'krx_live'
        if (receipt.get('basis_date') != day or receipt.get('service') != service
                or receipt.get('source_kind') != expected):
            raise ValueError('receipt_scope_or_origin_mismatch')
        return receipt, path

    def masters(self, day):
        result = {}
        for prefix, market in zip(('stk','ksq'), MARKETS):
            receipt, path = self.get(day, prefix+'_isu_base_info')
            records, evidence = master.load_snapshot(path,service=prefix+'_isu_base_info',
                basis_date=day,synthetic=self.store.synthetic)
            result[market] = (records,evidence)
        codes = [r['stock_code'] for records, _ in result.values() for r in records]
        standards = [r['standard_code'] for records, _ in result.values() for r in records]
        if len(codes)!=len(set(codes)) or len(standards)!=len(set(standards)):
            raise ValueError('cross_market_master_duplicate')
        write_json(self.run_dir/('master-'+day+'.json'), {
            'basis_date':day, 'markets':{m:dict(records=r,provenance=e) for m,(r,e) in result.items()},
            'theme_database_changed':False})
        return result

    def collect(self, day):
        if day in completed_days(self.store.path):
            return self.save(day,'complete_reused',{'new_requests':0,'new_rows':0})
        if date.fromisoformat(day) >= self.now.astimezone(KST).date():
            return self.save(day,'deferred_current_or_future_day',{'new_requests':0,'new_rows':0})
        if date.fromisoformat(day).weekday() >= 5:
            return self.save(day,'nontrading_weekend',{'new_requests':0,'new_rows':0})
        try:
            receipts = {m:self.get(day,p+'_bydd_trd')[0] for p,m in zip(('stk','ksq'),MARKETS)}
            rows = {m:backfill.response_rows(r) for m,r in receipts.items()}
            if any(v is None for v in rows.values()):
                return self.save(day,'request_failed',{'new_rows':0,'retryable':True})
            if any(not v for v in rows.values()):
                state = 'empty_unclassified' if all(not v for v in rows.values()) else 'partial_market_availability'
                return self.save(day,state,{'new_rows':0,'retryable':True,
                    'market_rows':{m:len(v) for m,v in rows.items()},'holiday_confirmed':False})
            masters = self.masters(day)
            # Stage with existing AP parser/store; the destination is untouched on any failure.
            stage_path = daily.ROOT/('as-stage-'+self.run_dir.name+'-'+day+'.db')
            if stage_path.exists():
                raise ValueError('new_daily_stage_required')
            stage = daily.DailyStore(stage_path,synthetic=self.store.synthetic)
            quality = {}
            for market in MARKETS:
                records, evidence = masters[market]
                outcome = stage.ingest(receipts[market],masters=records,master_body_hash=evidence['body_sha256'])
                if outcome['state'] != 'ready':
                    raise ValueError('daily_stage_validation_failed')
                with master.read_db(stage_path) as db:
                    bars = [dict(r) for r in db.execute('SELECT * FROM daily_latest WHERE market=?',(market,))]
                if ({r['stock_code'] for r in bars} != {r['stock_code'] for r in records}
                        or len(bars) != len(records) or any(r['stock_code'] is None for r in bars)):
                    raise ValueError('dated_master_coverage_incomplete')
                required = ('open','high','low','close','volume','turnover_raw','market_cap_raw','change_price','change_pct','listed_shares')
                if any(any(r[k] is None for k in required) for r in bars):
                    raise ValueError('incomplete_daily_numeric_fields')
                if not any(Decimal(r['volume'])>0 for r in bars):
                    raise ValueError('entire_market_unformed')
                fatal = {'daily_identity_master_join_unverified','change_sign_conflict','ohlcv_missing'}
                if any(fatal.intersection(json.loads(r['issues_json'])) for r in bars):
                    raise ValueError('daily_identity_or_price_quality_failed')
                with master.read_db(self.store.path) as db:
                    prior = db.execute('SELECT count(*) FROM daily_latest WHERE market=? AND day='
                        '(SELECT max(day) FROM daily_latest WHERE market=? AND day<?)',(market,market,day)).fetchone()[0]
                if prior and len(bars)<prior*0.95:
                    raise ValueError('market_row_count_drop_requires_review')
                quality[market] = dict(rows=len(bars),master_rows=len(records),
                    source_service=receipts[market]['service'],master=evidence,
                    issue_rows=sum(bool(json.loads(r['issues_json'])) for r in bars))
            integrity(stage_path)
            self.publish(stage_path,day)
            return self.save(day,'complete',dict(new_rows=sum(v['rows'] for v in quality.values()),
                markets=quality,validation='both_market_dated_master_and_schema_checked',
                source_kind='synthetic_fixture' if self.store.synthetic else 'krx_live'))
        except Exception as exc:
            # Only fixed type codes are logged; never serialize arbitrary exception text or keys.
            safe = str(exc) if str(exc) in SAFE_REASONS else type(exc).__name__
            return self.save(day,'failed_or_quarantined',dict(new_rows=0,reason=safe,retryable=True))

    def publish(self, stage, day):
        with closing(sqlite3.connect(self.store.path.as_uri()+'?mode=rw',uri=True)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('ATTACH DATABASE ? AS staged',(stage.as_uri()+'?mode=ro',))
            with db:
                db.execute('BEGIN IMMEDIATE')
                for table in ('snapshots','attempts','raw_rows','bars'):
                    db.execute('INSERT OR IGNORE INTO '+table+' SELECT * FROM staged.'+table)
                for market in MARKETS:
                    row = db.execute('SELECT * FROM staged.market_days WHERE day=? AND market=?',(day,market)).fetchone()
                    db.execute('INSERT INTO market_days VALUES(?,?,?,?) ON CONFLICT(day,market) DO UPDATE SET '
                        'current_snapshot=excluded.current_snapshot,last_status=excluded.last_status',row)
                db.execute('DELETE FROM missing_sessions WHERE day=?',(day,))
                if db.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('publish_foreign_key_failed')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--end',default=(datetime.now(KST).date()-timedelta(days=1)).isoformat())
    p.add_argument('--start'); p.add_argument('--retry-history',action='store_true')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--master-only',action='store_true'); p.add_argument('--master-date')
    p.add_argument('--rebuild-adr',action='store_true')
    p.add_argument('--max-calls',type=int,default=120)
    p.add_argument('--spacing',type=float,default=1.1)
    p.add_argument('--env-file',type=Path)
    a = p.parse_args(argv)
    date.fromisoformat(a.end)
    if date.fromisoformat(a.end)>datetime.now(KST).date():
        p.error('end must not exceed today')
    if not 1 <= a.max_calls <= 5000 or not 0.5 <= a.spacing <= 30:
        p.error('max-calls must be 1..5000 and spacing 0.5..30')
    if a.start and date.fromisoformat(a.start)>date.fromisoformat(a.end):
        p.error('start must not exceed end')
    if a.master_only and not a.master_date:
        p.error('--master-only requires --master-date')
    if a.master_date:
        if date.fromisoformat(a.master_date)>=datetime.now(KST).date():
            p.error('master date must precede today')
    if not a.execute:
        path = DATABASE if DATABASE.exists() else SEED
        complete = completed_days(path)
        print(json.dumps(dict(execute=False,project=str(PROJECT),database=str(path),
            last_complete_day=max(complete) if complete else None,end=a.end,
            note='Use --execute to initialize isolated incremental store and collect.')))
        return 0
    # Load only the dedicated KRX credential; no generic .env or Collector token imports.
    key_path = a.env_file or PROJECT.parent/(PROJECT.name+'-private')/'.env.krx.local'
    key = api.credential() or api.credential(env_file=key_path)
    if key is None:
        raise ValueError('krx_key_missing')
    review = json.loads((PROJECT/'diagnostics/krx-an/an3-20261103/review.json').read_text(encoding='utf8'))
    import requests
    from .weekly_tls import WindowsTrustAdapter
    for owned in (ROOT,daily.ROOT,backfill.ROOT,adr.ROOT):
        if owned.resolve()!=owned.absolute() or not owned.is_relative_to(PROJECT):
            raise ValueError('regular_owned_development_paths_required')
    with run_lock(ROOT):
        checkpoint = bootstrap()
        run_id = datetime.now(KST).strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        run_dir = ROOT/'runs'/run_id
        session = requests.Session(); session.trust_env=False; session.verify=True
        def fetch(service, compact):
            return api.ProbeClient(key,review,session=session).probe(service,compact)
        # New per-execution receipt cache: an earlier empty response never poisons a retry.
        transport = backfill.Backfill(None,backfill.ROOT/('as-'+run_id),fetch,
                                      spacing=a.spacing,max_calls=a.max_calls)
        updater = Updater(daily.DailyStore(DATABASE),checkpoint,run_dir,transport.receipt)
        last, days = plan(DATABASE,checkpoint,a.end,start=a.start,retry_history=a.retry_history)
        report = dict(stage='C12-AS',run_id=run_id,source_kind='krx_live',project=str(PROJECT),
            database=str(DATABASE),last_complete_before=last,planned_dates=days,
            key_loaded=True,credential_values_exported=False,started_at=datetime.now(KST).isoformat())
        exit_code = 0
        try:
            session.mount('https://',WindowsTrustAdapter()); session.headers['User-Agent']='Mozilla/5.0'
            if a.master_only:
                result = updater.masters(a.master_date)
                report['master_refresh'] = {m:dict(rows=len(r),provenance=e) for m,(r,e) in result.items()}
            else:
                for day in days:
                    result = updater.collect(day)
                    if result['state'] in ('failed_or_quarantined','request_failed'):
                        exit_code=2
                        break
                    if result['state']=='partial_market_availability':
                        exit_code=2
            report.update(integrity(DATABASE))
            complete = completed_days(DATABASE)
            report['last_complete_after'] = max(complete) if complete else None
            report['dates'] = updater.results
            report['new_rows'] = sum(r['details'].get('new_rows',0) for r in updater.results)
            current_revision = revision(DATABASE)
            pointer = ROOT/'latest-adr.json'
            previous = json.loads(pointer.read_text(encoding='utf8')) if pointer.exists() else {}
            needs_adr = previous.get('revision') != current_revision
            if report['new_rows'] or a.rebuild_adr or (needs_adr and not a.master_only):
                output = adr.ROOT/('as-'+run_id)
                payload = adr.build(DATABASE,checkpoint,output,incremental=True)
                report['adr'] = dict(output=str(output),html=str(output/'adr20.html'),
                    latest=payload['report']['latest'],missing_dates=payload['report']['missing_dates'],
                    provisional=True)
                write_json(pointer,dict(output=str(output),run_id=run_id,revision=current_revision))
        except Exception as exc:
            exit_code=2
            report['error_type'] = type(exc).__name__
        finally:
            session.close()
            report.setdefault('dates',updater.results)
            report.setdefault('new_rows',sum(r['details'].get('new_rows',0) for r in updater.results))
            with closing(transport.connection()) as db:
                report['actual_requests'] = db.execute('SELECT count(*) FROM requests').fetchone()[0]
            report.update(exit_code=exit_code,finished_at=datetime.now(KST).isoformat())
            write_json(run_dir/'summary.json',report)
            write_json(ROOT/'latest-run.json',report)
            print(json.dumps(dict(report=str(run_dir/'summary.json'),new_rows=report['new_rows'],
                actual_requests=report['actual_requests'],exit_code=exit_code)),flush=True)
        return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
