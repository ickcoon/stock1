"""AP-2 resumable, paced date-by-date collection using AP-1's owned store."""
import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import time

from . import krx_daily as daily, krx_master as ao, krx_openapi as krx

ROOT = ao.master.PROJECT / 'diagnostics' / 'krx-ap2'
SERVICES = ('stk_bydd_trd', 'ksq_bydd_trd', 'stk_isu_base_info', 'ksq_isu_base_info')


def response_rows(receipt):
    body = receipt.get('raw_response')
    rows = body.get('OutBlock_1') if isinstance(body, dict) else None
    return rows if receipt.get('http_status') == 200 and isinstance(rows, list) else None


class Backfill:
    def __init__(self, store, root, fetch, *, spacing=1.1, max_calls=1800,
                 clock=time.monotonic, sleep=time.sleep, caches=()):
        root = Path(root).absolute()
        if (root.resolve() != root or not root.is_relative_to(ROOT.absolute())
                or ROOT.resolve() != ROOT.absolute() or not 0.5 <= spacing <= 30
                or not 1 <= max_calls <= 5000):
            raise ValueError('bounded_development_backfill_required')
        self.store, self.root, self.fetch = store, root, fetch
        self.spacing, self.max_calls = spacing, max_calls
        self.clock, self.sleep, self.caches = clock, sleep, tuple(map(Path, caches))
        self.last_start = None
        root.mkdir(parents=True, exist_ok=True)
        (root/'receipts').mkdir(exist_ok=True)
        self.path = root/'progress.db'
        # Refuse arbitrary pre-existing databases even in the development directory.
        if self.path.exists():
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True)) as db:
                if db.execute('SELECT version FROM backfill_meta').fetchone() != (1,):
                    raise ValueError('backfill_schema_mismatch')
        else:
            with self.path.open('xb'):
                pass
            with closing(sqlite3.connect(self.path)) as db, db:
                db.executescript('''CREATE TABLE backfill_meta(version INTEGER PRIMARY KEY);
                INSERT INTO backfill_meta VALUES(1);
                CREATE TABLE requests(id INTEGER PRIMARY KEY,day TEXT NOT NULL,service TEXT NOT NULL,
                 started_at TEXT NOT NULL,status TEXT NOT NULL,path TEXT NOT NULL,http_status INTEGER,rows INTEGER);
                CREATE INDEX request_scope ON requests(day,service,id);
                CREATE TABLE dates(day TEXT PRIMARY KEY,state TEXT NOT NULL,details_json TEXT NOT NULL);
                ''')

    def connection(self):
        return sqlite3.connect(self.path.as_uri()+'?mode=rw', uri=True)

    def receipt(self, day, service):
        compact = day.replace('-', '')
        with closing(self.connection()) as db:
            rows = db.execute('SELECT path,status FROM requests WHERE day=? AND service=? ORDER BY id DESC', (day,service)).fetchall()
            calls = db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]
        paths = [Path(r[0]) for r in rows if r[1] == 'response_received']
        paths += [p/(service+'_'+compact+'.json') for p in self.caches]
        for path in paths:
            if not path.is_file():
                continue
            r = json.loads(path.read_text(encoding='utf8'))
            if r.get('service') != service or r.get('basis_date') != day or r.get('source_kind') != 'krx_live':
                raise ValueError('cached_receipt_scope_mismatch')
            if response_rows(r) is not None:
                return r, path
        for attempt in range(3):
            if calls >= self.max_calls:
                raise ValueError('backfill_request_budget_exhausted')
            if self.last_start is not None:
                self.sleep(max(0, self.spacing-(self.clock()-self.last_start)))
            with closing(self.connection()) as db, db:
                seq = db.execute('SELECT COALESCE(MAX(id),0)+1 FROM requests').fetchone()[0]
                path = self.root/'receipts'/f'{service}_{compact}_{seq:05d}.json'
                db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,NULL,NULL)',
                    (seq,day,service,datetime.now(timezone.utc).isoformat(),'started',str(path)))
            self.last_start = self.clock()
            r = self.fetch(service, compact)
            calls += 1
            if r.get('service') != service or r.get('basis_date') != day or r.get('source_kind') != 'krx_live':
                raise ValueError('transport_receipt_scope_mismatch')
            with path.open('x', encoding='utf8') as stream:
                json.dump(r, stream, ensure_ascii=False, separators=(',', ':'))
            count = len(response_rows(r)) if response_rows(r) is not None else None
            with closing(self.connection()) as db, db:
                db.execute('UPDATE requests SET status=?,http_status=?,rows=? WHERE id=?',
                    ('response_received' if count is not None else 'failed',r.get('http_status'),count,seq))
            if count is not None:
                return r, path
            if r.get('http_status') in (401,403,429) or r.get('status') in ('tls_verification_failed', 'quota_limited'):
                raise ValueError('authentication_or_tls_requires_review')
            if attempt < 2:
                self.sleep(min(30, 2 ** (attempt+1)))
        return r, path

    def save_day(self, day, state, details):
        with closing(self.connection()) as db, db:
            db.execute('INSERT INTO dates VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET '
                'state=excluded.state,details_json=excluded.details_json', (day,state,daily.encode(details)))

    def collect_day(self, day):
        with ao.read_db(self.store.path) as db:
            ready = db.execute('SELECT COUNT(*) FROM market_days WHERE day=? AND current_snapshot IS NOT NULL', (day,)).fetchone()[0]
        if ready == 2:
            self.save_day(day, 'complete_reused', {'new_requests':0})
            return 'complete_reused'
        details, empties, failed = [], 0, False
        for prefix, market in (('stk','KOSPI'), ('ksq','KOSDAQ')):
            r, path = self.receipt(day, prefix+'_bydd_trd')
            rows = response_rows(r)
            if not rows:
                outcome = self.store.ingest(r)
                empties += rows == []
                failed |= rows is None
            else:
                base, source = self.receipt(day, prefix+'_isu_base_info')
                if not response_rows(base):
                    failed = True
                    details.append(dict(market=market,state='dated_master_unavailable',daily_receipt=str(path)))
                    continue
                masters, evidence = ao.load_snapshot(source, service=prefix+'_isu_base_info',basis_date=day)
                outcome = self.store.ingest(r, masters=masters, master_body_hash=evidence['body_sha256'])
                failed |= outcome['state'] not in ('ready','already_imported')
            details.append({**outcome, 'market':market, 'receipt':str(path)})
        state = 'failed_or_quarantined' if failed else 'empty_unclassified' if empties else 'complete'
        if empties == 1:
            state = 'partial_market_availability'
        self.save_day(day, state, details)
        return state

    def run(self, start, end, *, target_new_sessions=None):
        completed = 0
        for day in reversed(daily.candidate_sessions(start, end)):
            try:
                state = self.collect_day(day)
            except Exception as exc:
                self.save_day(day, 'interrupted_or_failed', {'error_type':type(exc).__name__, 'reason':str(exc)})
                raise
            if state == 'complete':
                completed += 1
            print(json.dumps(dict(day=day,state=state,new_complete_sessions=completed)), flush=True)
            if state in ('failed_or_quarantined','partial_market_availability'):
                raise ValueError('data_quality_gate_failed')
            if target_new_sessions is not None and completed >= target_new_sessions:
                break
        return self.summary(start,end)

    def summary(self, start, end):
        with closing(self.connection()) as db:
            counts = dict(db.execute('SELECT state,COUNT(*) FROM dates GROUP BY state'))
            calls = db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]
            failures = db.execute("SELECT COUNT(*) FROM requests WHERE status!='response_received'").fetchone()[0]
            states = dict(db.execute('SELECT day,state FROM dates'))
        pending = [d for d in daily.candidate_sessions(start,end) if d not in states]
        result = dict(start=start,end=end,date_states=counts,new_requests=calls,failed_attempts=failures,
                      pending_weekdays=pending,unresolved_empty_dates=[d for d,s in sorted(states.items()) if s=='empty_unclassified'])
        (self.root/'progress.json').write_text(json.dumps(result,indent=2),encoding='utf8')
        return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--start',default='2025-10-01');p.add_argument('--end',default='2026-10-30')
    p.add_argument('--pilot',type=int);p.add_argument('--spacing',type=float,default=1.1)
    p.add_argument('--execute',action='store_true')
    a = p.parse_args(argv)
    if not a.execute:
        raise ValueError('explicit_execute_required')
    root = ROOT/'20251001-20261030'
    destination = daily.ROOT/'ap2-20251001-20261030.db'
    original = daily.ROOT/'ap1-20261026-30.db'
    if not destination.exists():
        # A new isolated copy; the AP-1 database remains byte-for-byte unchanged.
        with destination.open('xb') as stream, original.open('rb') as source:
            shutil.copyfileobj(source,stream)
    store = daily.DailyStore(destination)
    import requests
    from unittest.mock import patch
    from .weekly_tls import WindowsTrustAdapter
    with patch.object(krx,'PROJECT',Path('C:/Users/dada1/Downloads/stock1')):
        key = krx.credential(env_file=Path('C:/Users/dada1/Downloads/stock1/.env.krx.local'))
    if key is None:
        raise ValueError('krx_key_missing')
    review = json.loads((ao.master.PROJECT/'diagnostics/krx-an/an3-20261103/review.json').read_text(encoding='utf8'))
    session = requests.Session();session.trust_env=False;session.verify=True
    session.mount('https://',WindowsTrustAdapter());session.headers['User-Agent']='Mozilla/5.0'
    def fetch(service, compact):
        client = krx.ProbeClient(key,review,session=session)
        return client.probe(service,compact)
    runner = Backfill(store,root,fetch,spacing=a.spacing,
                      caches=[ao.master.PROJECT/'diagnostics/krx-ap1/receipts'])
    try:
        print(json.dumps(runner.run(a.start,a.end,target_new_sessions=a.pilot)),flush=True)
    finally:
        runner.summary(a.start,a.end)
        session.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
