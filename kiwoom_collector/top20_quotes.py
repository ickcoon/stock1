"""Independent, explicit TOP20 quote orchestration. No scheduler integration."""
from contextlib import closing, contextmanager
import json
import math
from pathlib import Path
import sqlite3
import tempfile
from uuid import uuid4

from .quotes import (CODE, SPEC_VERSION, VALUE_FIELDS, QuoteInterrupted,
                     migrate_quotes, normalize_quote, stamp)


def validate_options(environment, spacing, recollect):
    if environment not in ('mock', 'real'):
        raise ValueError('환경은 mock 또는 real이어야 합니다.')
    if not math.isfinite(spacing) or spacing < 1.1:
        raise ValueError('시세 요청 간격은 유한한 1.1초 이상이어야 합니다.')
    if recollect not in ('never', 'failed', 'all'):
        raise ValueError('재수집 정책은 never/failed/all 중 하나여야 합니다.')


def connection(path, mode):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=' + mode, uri=True, timeout=10)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        if mode == 'ro':
            db.execute('PRAGMA query_only=ON')
        return db
    except BaseException:
        db.close()
        raise


@contextmanager
def read_snapshot(path):
    """Read source bytes only; SQLite may recover WAL exclusively on a temp copy.

    Even mode=ro can create source WAL/SHM files. Avoid opening the source with
    SQLite at all. Fail closed if source main/WAL bytes change during copying.
    """
    source = Path(path).resolve()
    wal = Path(str(source)+'-wal')
    if not source.is_file():
        raise sqlite3.OperationalError('기존 DB 파일이 필요합니다.')
    def contents():
        try:
            return source.read_bytes(), wal.read_bytes() if wal.exists() else None
        except FileNotFoundError:
            raise ValueError('DB/WAL이 변경 중입니다. 잠시 후 dry-run을 다시 실행하세요.') from None
    main_bytes, wal_bytes = contents()
    if (main_bytes,wal_bytes) != contents():
        raise ValueError('DB/WAL이 변경 중입니다. 잠시 후 dry-run을 다시 실행하세요.')
    with tempfile.TemporaryDirectory(prefix='stock1-quote-plan-') as folder:
        copied = Path(folder)/'snapshot.db'
        copied.write_bytes(main_bytes)
        if wal_bytes is not None:
            Path(str(copied)+'-wal').write_bytes(wal_bytes)
        with closing(connection(copied,'rw')) as db:
            db.execute('PRAGMA query_only=ON')
            yield db


def eligible(previous, recollect):
    if previous is None:
        return True
    # A running request is ambiguous. Never replace/replay it, even with 'all'.
    if previous['status'] == 'running':
        return False
    return recollect == 'all' or (recollect == 'failed' and not previous.get('has_success', previous['status']=='success'))


def latest_observations(db, collection_id):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quote_observations'").fetchone()
    if not exists:
        return {}
    columns = {r[1] for r in db.execute('PRAGMA table_info(quote_observations)')}
    order = 'attempt' if 'attempt' in columns else 'rowid'
    rows = db.execute('''SELECT * FROM quote_observations WHERE collection_id=?
        AND api_id='ka10095' AND test_mode=1 ORDER BY ''' + order, (collection_id,)).fetchall()
    previous = {}
    for row in rows:
        old_success = previous.get(row['rank'],{}).get('has_success',False)
        previous[row['rank']] = dict(row,has_success=old_success or row['status']=='success')
    return previous


def make_plan(db, collection_id, environment, spacing, recollect):
    """Called once per execution inside a read/write snapshot transaction."""
    parent = db.execute('SELECT * FROM collections WHERE id=?', (collection_id,)).fetchone()
    if parent is None or parent['api_id'] != 'ka00198' or parent['status'] != 'success':
        raise ValueError('성공한 ka00198 회차 UUID가 필요합니다.')
    if parent['environment'] != environment:
        raise ValueError('순위 회차와 요청 환경이 다릅니다.')
    rows = [dict(r) for r in db.execute('''SELECT rank,stk_cd,stk_nm FROM ranking_records
        WHERE collection_id=? AND rank BETWEEN 1 AND 20 ORDER BY rank LIMIT 20''', (collection_id,))]
    if not rows or any(not isinstance(r['stk_cd'], str) or not CODE.fullmatch(r['stk_cd']) for r in rows):
        raise ValueError('TOP20에 유효한 종목코드가 필요합니다. 누락 코드를 추측하지 않습니다.')
    if len({r['stk_cd'] for r in rows}) != len(rows):
        raise ValueError('TOP20 종목코드가 중복되었습니다.')
    previous = latest_observations(db, collection_id)
    selected = []
    skipped = []
    for row in rows:
        prior = previous.get(row['rank'])
        if prior and prior['requested_code'] != row['stk_cd']:
            raise ValueError('기존 시세 대상 코드와 현재 순위 코드가 다릅니다.')
        if eligible(prior, recollect):
            selected.append(row)
        else:
            skipped.append(dict(row, previous_status=prior['status']))
    chunks = []
    for offset in range(0, len(selected), 2):
        chunk = selected[offset:offset+2]
        payload = '|'.join(r['stk_cd'] for r in chunk)
        if len(payload) > 20:
            raise ValueError('종목 묶음의 요청 필드 길이 20을 초과했습니다.')
        chunks.append({'number': len(chunks)+1, 'ranks': [r['rank'] for r in chunk],
                       'codes': [r['stk_cd'] for r in chunk], 'stk_cd': payload})
    locks = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quote_top20_locks'").fetchone()
    blocked = db.execute('SELECT batch_id FROM quote_top20_locks WHERE environment=?', (environment,)).fetchone() if locks else None
    return {'collection_id': collection_id, 'environment': environment, 'api_id': 'ka10095',
            'test_mode': True, 'ranking_collected_at': parent['collected_at'],
            'ranking_scheduled_at': parent['scheduled_at'] if 'scheduled_at' in parent.keys() else None,
            'temporal_relation': 'current_quote_for_historical_ranking',
            'request_spacing_seconds': spacing, 'recollect': recollect,
            'targets': rows, 'selected': selected, 'skipped': skipped, 'requests': chunks,
            'blocked_by_batch_id': blocked[0] if blocked else None,
            'expected_quote_requests': len(chunks), 'auth_requests_if_needed': 1 if chunks else 0,
            'minimum_start_span_seconds': max(0, len(chunks)-1) * spacing}


def dry_run(path, collection_id, *, environment='mock', spacing=1.1, recollect='never'):
    validate_options(environment, spacing, recollect)
    with read_snapshot(path) as db:
        db.execute('BEGIN')
        try:
            plan = make_plan(db, collection_id, environment, spacing, recollect)
        finally:
            db.rollback()
    return dict(plan, dry_run=True, actual_api_calls=0)


def migrate_top20(db):
    """Version observations for explicit recollection; preserve every existing row."""
    migrate_quotes(db)
    columns = [r[1] for r in db.execute('PRAGMA table_info(quote_observations)')]
    if 'attempt' not in columns:
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='quote_observations'").fetchone()[0]
        # Known stage-two schema only. Fail closed instead of guessing a different schema.
        old_key = 'PRIMARY KEY(collection_id,rank,api_id,test_mode)'
        if old_key not in sql:
            raise ValueError('알 수 없는 시세 스키마입니다. 자동 재구성하지 않습니다.')
        db.execute('ALTER TABLE quote_observations RENAME TO quote_observations_pre_top20')
        sql = sql.replace(old_key, "attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0), "
                          "request_id TEXT REFERENCES quote_requests(id), "
                          "PRIMARY KEY(collection_id,rank,api_id,test_mode,attempt)")
        db.execute(sql)
        names = ','.join(columns)
        db.execute(f'INSERT INTO quote_observations({names}) SELECT {names} FROM quote_observations_pre_top20')
        db.execute('DROP TABLE quote_observations_pre_top20')
    batch_columns = {r[1] for r in db.execute('PRAGMA table_info(quote_batches)')}
    if 'plan_json' not in batch_columns:
        db.execute('ALTER TABLE quote_batches ADD COLUMN plan_json TEXT')
    db.execute('''CREATE TABLE IF NOT EXISTS quote_top20_locks (
        environment TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES quote_batches(id))''')


def stop_reason(quotes):
    for q in quotes.values():
        if q.http_status in (401,403,429):
            return 'http_limit_or_auth'
        if q.return_code in (1700,1701,1702,8001,8002,8003,8005,8006,8009,8010,8011,
                             8012,8015,8016,8030,8031,8040,8050,8103,8104):
            return 'api_limit_or_auth'
        if set(q.issues) & {'authentication_failed', 'rate_limited', 'mock_unsupported'}:
            return 'api_limit_or_auth'
    return None


def execute(path, client, collection_id, *, environment='mock', spacing=1.1, recollect='never'):
    validate_options(environment, spacing, recollect)
    if client.credentials.environment != environment:
        raise ValueError('KIWOOM_ENV와 요청 환경이 다릅니다. 자동 전환하지 않습니다.')
    client.request_interval = spacing
    with closing(connection(path, 'rw')) as db:
        batch_id = str(uuid4())
        try:
            db.execute('BEGIN IMMEDIATE')
            plan = make_plan(db, collection_id, environment, spacing, recollect)
            if any(r['previous_status']=='running' for r in plan['skipped']):
                raise ValueError('대상에 진행/결과 불명 요청이 있습니다. 자동 재전송하지 않습니다.')
            migrate_top20(db)
            if db.execute('SELECT 1 FROM quote_top20_locks WHERE environment=?', (environment,)).fetchone():
                raise ValueError('같은 환경의 TOP20 시세 실행이 진행/미완료 상태입니다. 기존 실행을 확인하세요.')
            db.execute('''INSERT INTO quote_batches(id,collection_id,environment,api_id,spec_version,
                started_at,status,plan_json) VALUES(?,?,?,'ka10095',?,?,'running',?)''',
                (batch_id, collection_id, environment, SPEC_VERSION, stamp(), json.dumps(plan,ensure_ascii=False)))
            db.execute('INSERT INTO quote_top20_locks VALUES(?,?)', (environment, batch_id))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        records = []
        completed_requests = 0
        actual_quote_requests = 0
        stopped = None
        try:
            for chunk in plan['requests']:
                request_id = str(uuid4())
                # Claim only the next request. Unsent later targets remain resumable.
                with db:
                    db.execute('''INSERT INTO quote_requests(id,batch_id,requested_codes_json,status)
                        VALUES(?,?,?,'running')''', (request_id,batch_id,json.dumps(chunk['codes'])))
                    for rank, code in zip(chunk['ranks'],chunk['codes']):
                        attempt = db.execute('''SELECT COALESCE(MAX(attempt),-1)+1 FROM quote_observations
                            WHERE collection_id=? AND rank=? AND api_id='ka10095' AND test_mode=1''',
                            (collection_id,rank)).fetchone()[0]
                        db.execute('''INSERT INTO quote_observations(batch_id,collection_id,rank,requested_code,
                            api_id,status,attempt,request_id) VALUES(?,?,?,?,'ka10095','running',?,?)''',
                            (batch_id,collection_id,rank,code,attempt,request_id))
                quotes = client.fetch_quotes(chunk['codes'])
                if set(quotes) != set(chunk['codes']):
                    raise ValueError('시세 클라이언트 반환 대상이 요청 대상과 다릅니다.')
                sample = quotes[chunk['codes'][0]]
                actual_quote_requests += int(sample.request_started_at is not None)
                states = [q.status for q in quotes.values()]
                request_status = ('success' if all(s=='success' for s in states) else 'partial'
                                  if any(s in ('success','partial') for s in states) else 'error')
                with db:
                    db.execute('''UPDATE quote_requests SET status=?,started_at=?,finished_at=?,http_status=?,
                        return_code=?,response_json=?,issues_json=? WHERE id=?''',
                        (request_status,sample.request_started_at,sample.request_finished_at,
                         sample.http_status,sample.return_code,
                         json.dumps({c:q.raw for c,q in quotes.items()},ensure_ascii=False),
                         json.dumps({c:q.issues for c,q in quotes.items()}),request_id))
                    for rank, code in zip(chunk['ranks'],chunk['codes']):
                        q = quotes[code]
                        db.execute('''UPDATE quote_observations SET status=?,request_started_at=?,
                            request_finished_at=?,http_status=?,return_code=?,raw_json=?,issues_json=?,
                            normalized_json=?,''' + ','.join(k+'=?' for k in VALUE_FIELDS) +
                            ' WHERE request_id=? AND rank=?',
                            (q.status,q.request_started_at,q.request_finished_at,q.http_status,q.return_code,
                             json.dumps(q.raw,ensure_ascii=False),json.dumps(q.issues),
                             json.dumps(normalize_quote(q.raw,'ka10095')),
                             *(q.raw.get(k) for k in VALUE_FIELDS),request_id,rank))
                        records.append({'rank':rank,'code':code,'status':q.status,'issues':q.issues})
                completed_requests += 1
                stopped = stop_reason(quotes)
                if stopped:
                    break
        except BaseException as exc:
            with db:
                if isinstance(exc, QuoteInterrupted):
                    q = exc.quote
                    db.execute('''UPDATE quote_requests SET started_at=?,finished_at=?
                        WHERE batch_id=? AND status='running' ''',
                        (q.request_started_at,q.request_finished_at,batch_id))
                    db.execute('''UPDATE quote_observations SET request_started_at=?,request_finished_at=?
                        WHERE batch_id=? AND status='running' ''',
                        (q.request_started_at,q.request_finished_at,batch_id))
                db.execute("UPDATE quote_requests SET status='interrupted',issues_json=? WHERE batch_id=? AND status='running'",
                           (json.dumps(['execution_interrupted']),batch_id))
                db.execute("UPDATE quote_observations SET status='interrupted',issues_json=? WHERE batch_id=? AND status='running'",
                           (json.dumps(['execution_interrupted']),batch_id))
                db.execute("UPDATE quote_batches SET status='interrupted',finished_at=? WHERE id=?",(stamp(),batch_id))
            raise
        finally:
            with db:
                db.execute('DELETE FROM quote_top20_locks WHERE batch_id=?',(batch_id,))
        states = [r['status'] for r in records]
        status = ('skipped' if not states else 'success' if all(s=='success' for s in states) and not stopped
                  and all(r['previous_status']=='success' for r in plan['skipped'])
                  else 'partial' if any(s in ('success','partial') for s in states) else 'error')
        with db:
            db.execute('UPDATE quote_batches SET status=?,finished_at=? WHERE id=?',(status,stamp(),batch_id))
        return dict(plan,dry_run=False,batch_id=batch_id,status=status,stop_reason=stopped,
                    completed_requests=completed_requests,observations=records,
                    actual_quote_requests=actual_quote_requests,
                    unsent_requests=len(plan['requests'])-completed_requests), 0 if status in ('success','skipped') else 1
