"""Bounded, opt-in follow-up of a newly completed scheduled ranking only."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from uuid import uuid4

from .api_resource import exclusive_quotes
from .client import KST
from .quotes import (SPEC_VERSION, VALUE_FIELDS, QuoteDeadlineReached,
                     QuoteInterrupted, normalize_quote, stamp)
from .top20_quotes import connection, make_plan, migrate_top20, stop_reason


@dataclass(frozen=True)
class QuotePolicy:
    max_age_seconds: int = 180
    max_duration_seconds: int = 120
    next_slot_reserve_seconds: int = 60
    request_budget_seconds: int = 60
    spacing_seconds: float = 1.1

    def __post_init__(self):
        for value in (self.max_age_seconds, self.max_duration_seconds,
                      self.next_slot_reserve_seconds, self.request_budget_seconds):
            if not math.isfinite(value) or value < 60:
                raise ValueError('시세 안전 시간 설정은 유한한 60초 이상이어야 합니다.')
        if not math.isfinite(self.spacing_seconds) or self.spacing_seconds < 1.1:
            raise ValueError('시세 요청 간격은 1.1초 이상이어야 합니다.')


def migrate_scheduled_quotes(db):
    """Caller owns transaction. Rebuild known tables, preserving every old row.

    No renaming of existing tables: request/lock foreign keys retain their names.
    Deferred FK validation permits replacing parents within this transaction.
    """
    migrate_top20(db)
    db.execute('PRAGMA defer_foreign_keys=ON')
    definitions = {}
    for name in ('quote_batches', 'quote_observations'):
        sql = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
        if 'CHECK(test_mode=1)' in sql:
            definitions[name] = sql.replace('CHECK(test_mode=1)', 'CHECK(test_mode IN (0,1))')
        elif 'CHECK(test_mode IN (0,1))' not in sql:
            raise ValueError('알 수 없는 시세 실행 구분 스키마입니다. 자동 변경하지 않습니다.')
    if definitions:
        # Preserve all explicit indexes/triggers, including locally added ones.
        extras = [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name IN ('quote_batches','quote_observations') AND type IN ('index','trigger') AND sql IS NOT NULL")]
        data = {name: list(db.execute('SELECT * FROM '+name)) for name in definitions}
        for name in ('quote_observations', 'quote_batches'):
            if name in definitions:
                db.execute('DROP TABLE '+name)
        for name in ('quote_batches', 'quote_observations'):
            if name in definitions:
                db.execute(definitions[name])
                for row in data[name]:
                    db.execute('INSERT INTO '+name+' VALUES('+','.join('?' for _ in row)+')', tuple(row))
        for sql in extras:
            db.execute(sql)
    db.execute('''CREATE TABLE IF NOT EXISTS scheduled_quote_runs (
        collection_id TEXT PRIMARY KEY REFERENCES collections(id),
        batch_id TEXT NOT NULL UNIQUE REFERENCES quote_batches(id),
        status TEXT NOT NULL CHECK(status IN ('running','complete','partial','error','stopped','uncertain')),
        reason TEXT, deadline_at TEXT NOT NULL)''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS scheduled_quote_parent BEFORE INSERT ON quote_batches
        WHEN NEW.test_mode=0 AND NOT EXISTS (SELECT 1 FROM collections
          WHERE id=NEW.collection_id AND test_mode=0 AND scheduled_at IS NOT NULL)
        BEGIN SELECT RAISE(ABORT,'scheduled quote parent mismatch'); END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS quote_observation_mode BEFORE INSERT ON quote_observations
        WHEN NOT EXISTS (SELECT 1 FROM quote_batches WHERE id=NEW.batch_id
            AND collection_id=NEW.collection_id AND api_id=NEW.api_id AND test_mode=NEW.test_mode)
        BEGIN SELECT RAISE(ABORT,'quote observation mode mismatch'); END''')
    if db.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('시세 스키마 확장 후 외래키 검증 실패')


@exclusive_quotes
def run_scheduled_quotes(path, client, collection_id, *, environment, created_at,
                         next_scheduled_at, now=lambda: datetime.now(KST), policy=QuotePolicy()):
    if client.credentials.environment != environment:
        raise ValueError('순위/시세 클라이언트 환경 불일치')
    current = now()
    age = (current-created_at).total_seconds()
    if age < 0 or age > policy.max_age_seconds or current.astimezone(KST).date() != created_at.astimezone(KST).date():
        return {'status': 'stopped', 'reason': 'stale_ranking', 'collection_id': collection_id}
    deadline = min(created_at+timedelta(seconds=policy.max_age_seconds),
                   current+timedelta(seconds=policy.max_duration_seconds),
                   next_scheduled_at-timedelta(seconds=policy.next_slot_reserve_seconds))
    with closing(connection(path, 'rw')) as db:
        batch_id = str(uuid4())
        try:
            db.execute('BEGIN IMMEDIATE')
            parent = db.execute('SELECT * FROM collections WHERE id=?', (collection_id,)).fetchone()
            if (parent is None or parent['status'] != 'success' or parent['test_mode'] != 0
                    or not parent['scheduled_at'] or parent['environment'] != environment
                    or parent['collected_at'] != created_at.astimezone(KST).isoformat(timespec='seconds')):
                raise ValueError('동일 흐름에서 생성된 신규 운영 순위 회차가 필요합니다.')
            migrate_scheduled_quotes(db)
            previous = db.execute('SELECT * FROM scheduled_quote_runs WHERE collection_id=?', (collection_id,)).fetchone()
            if previous:
                db.rollback()
                return {'status': 'skipped', 'reason': 'already_claimed', 'collection_id': collection_id}
            plan = make_plan(db, collection_id, environment, policy.spacing_seconds, 'never', test_mode=0)
            if plan['blocked_by_batch_id']:
                db.rollback()
                return {'status': 'stopped', 'reason': 'unresolved_lock', 'collection_id': collection_id}
            plan['temporal_relation'] = 'same_scheduled_collection_flow'
            db.execute('''INSERT INTO quote_batches(id,collection_id,environment,test_mode,api_id,spec_version,started_at,status,plan_json)
                VALUES(?,?,?,0,'ka10095',?,?,'running',?)''',
                (batch_id,collection_id,environment,SPEC_VERSION,stamp(),json.dumps(plan,ensure_ascii=False)))
            db.execute('INSERT INTO scheduled_quote_runs VALUES(?,?,\'running\',NULL,?)',
                       (collection_id,batch_id,deadline.isoformat()))
            db.execute('INSERT INTO quote_top20_locks VALUES(?,?)', (environment,batch_id))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        states = []
        reason = None
        uncertain = False
        old_deadline = client.request_deadline
        old_interval = client.request_interval
        client.request_deadline = deadline
        client.request_interval = policy.spacing_seconds
        try:
            for chunk in plan['requests']:
                if now()+timedelta(seconds=policy.request_budget_seconds) >= deadline:
                    reason = 'time_budget'
                    break
                request_id = str(uuid4())
                with db:
                    db.execute("INSERT INTO quote_requests(id,batch_id,requested_codes_json,status) VALUES(?,?,?,'running')",
                               (request_id,batch_id,json.dumps(chunk['codes'])))
                    for rank, code in zip(chunk['ranks'],chunk['codes']):
                        db.execute('''INSERT INTO quote_observations(batch_id,collection_id,rank,requested_code,api_id,test_mode,status,attempt,request_id)
                            VALUES(?,?,?,?,'ka10095',0,'running',0,?)''', (batch_id,collection_id,rank,code,request_id))
                try:
                    quotes = client.fetch_quotes(chunk['codes'])
                    if set(quotes) != set(chunk['codes']):
                        raise ValueError('시세 반환 대상 불일치')
                except QuoteDeadlineReached:
                    reason = 'deadline_before_send'
                    with db:
                        db.execute("UPDATE quote_requests SET status='skipped',issues_json=? WHERE id=?", (json.dumps([reason]),request_id))
                        db.execute("UPDATE quote_observations SET status='interrupted',issues_json=? WHERE request_id=?", (json.dumps([reason]),request_id))
                    break
                sample = quotes[chunk['codes'][0]]
                chunk_states = [q.status for q in quotes.values()]
                with db:
                    db.execute('''UPDATE quote_requests SET status=?,started_at=?,finished_at=?,http_status=?,return_code=?,response_json=?,issues_json=? WHERE id=?''',
                               ('success' if all(s=='success' for s in chunk_states) else 'partial' if any(s in ('success','partial') for s in chunk_states) else 'error',
                                sample.request_started_at,sample.request_finished_at,sample.http_status,sample.return_code,
                                json.dumps({c:q.raw for c,q in quotes.items()},ensure_ascii=False),json.dumps({c:q.issues for c,q in quotes.items()}),request_id))
                    for rank, code in zip(chunk['ranks'],chunk['codes']):
                        q = quotes[code]
                        db.execute('''UPDATE quote_observations SET status=?,request_started_at=?,request_finished_at=?,http_status=?,return_code=?,raw_json=?,issues_json=?,normalized_json=?,'''
                                   + ','.join(k+'=?' for k in VALUE_FIELDS) + ' WHERE request_id=? AND rank=?',
                                   (q.status,q.request_started_at,q.request_finished_at,q.http_status,q.return_code,json.dumps(q.raw,ensure_ascii=False),json.dumps(q.issues),
                                    json.dumps(normalize_quote(q.raw,'ka10095')),*(q.raw.get(k) for k in VALUE_FIELDS),request_id,rank))
                states.extend(chunk_states)
                if any('transport_failed' in q.issues for q in quotes.values()):
                    uncertain = True
                    reason = 'transmission_unknown'
                    with db:
                        db.execute("UPDATE quote_requests SET status='uncertain' WHERE id=?", (request_id,))
                        for rank, code in zip(chunk['ranks'],chunk['codes']):
                            q = quotes[code]
                            db.execute("UPDATE quote_observations SET status='interrupted',issues_json=? WHERE request_id=? AND rank=?",
                                       (json.dumps(q.issues+[reason]),request_id,rank))
                    break
                reason = stop_reason(quotes)
                if reason:
                    break
        except BaseException as exc:
            uncertain = True
            reason = 'transmission_unknown'
            with db:
                if isinstance(exc, QuoteInterrupted):
                    for table, start, finish in (('quote_requests','started_at','finished_at'), ('quote_observations','request_started_at','request_finished_at')):
                        db.execute(f'UPDATE {table} SET {start}=?,{finish}=? WHERE batch_id=? AND status=\'running\'',
                                   (exc.quote.request_started_at,exc.quote.request_finished_at,batch_id))
                db.execute("UPDATE quote_requests SET status='uncertain',issues_json=? WHERE batch_id=? AND status='running'", (json.dumps([reason]),batch_id))
                db.execute("UPDATE quote_observations SET status='interrupted',issues_json=? WHERE batch_id=? AND status='running'", (json.dumps([reason]),batch_id))
            raise
        finally:
            client.request_deadline = old_deadline
            client.request_interval = old_interval
            status = ('uncertain' if uncertain else 'stopped' if reason else
                      'complete' if states and all(s=='success' for s in states) else
                      'partial' if any(s in ('success','partial') for s in states) else 'error')
            batch_status = ('interrupted' if uncertain else 'success' if status=='complete' else
                            'error' if states and all(s not in ('success','partial') for s in states) else
                            'partial' if states else 'skipped')
            with db:
                db.execute('UPDATE scheduled_quote_runs SET status=?,reason=? WHERE collection_id=?', (status,reason,collection_id))
                db.execute('UPDATE quote_batches SET status=?,finished_at=? WHERE id=?',
                           (batch_status,stamp(),batch_id))
                if not uncertain:
                    db.execute('DELETE FROM quote_top20_locks WHERE batch_id=?', (batch_id,))
        return {'collection_id':collection_id,'batch_id':batch_id,'status':status,'reason':reason,
                'observed':len(states),'unsent':len(plan['targets'])-len(states)}
