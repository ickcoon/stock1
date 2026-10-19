"""Transactional local history. Only documented market fields enter persisted JSON."""
from contextlib import closing
import json
import logging
from pathlib import Path
import sqlite3
from uuid import uuid4

from .config import PROJECT_ROOT
from .sectors import migrate, upsert_stock
from .diagnostics import MARKET_FIELDS, type_name
from .errors import ResponseValidationError

DEFAULT_DB = PROJECT_ROOT / "data" / "stock1.db"
FIELDS = MARKET_FIELDS


def market_record(row, location="record"):
    if not isinstance(row, dict):
        raise ResponseValidationError(f"{location}: expected=dict actual={type_name(row)}")
    result = {key: row[key] for key in FIELDS if key in row}
    for key, value in result.items():
        if not isinstance(value, str):
            raise ResponseValidationError(f"{location}.{key}: expected=str actual={type_name(value)} (값 생략)")
    return result


def market_page(body, location="page"):
    # Authentication responses, headers, arbitrary server messages and unknown fields are never stored.
    if not isinstance(body, dict):
        raise ResponseValidationError(f"{location}: expected=dict actual={type_name(body)}")
    result = {}
    if "return_code" in body:
        code = body["return_code"]
        if isinstance(code, bool) or not isinstance(code, (int, str)):
            raise ResponseValidationError(f"{location}.return_code: expected=int/str actual={type_name(code)}")
        try:
            int(code)
        except ValueError:
            raise ResponseValidationError(f"{location}.return_code: 숫자 형식 불일치 (값 생략)") from None
        result["return_code"] = code
    if "item_inq_rank" in body:
        rows = body["item_inq_rank"]
        if not isinstance(rows, list):
            raise ResponseValidationError(f"{location}.item_inq_rank: expected=list actual={type_name(rows)}")
        result["item_inq_rank"] = [market_record(row, f"{location}.item_inq_rank[{index}]") for index, row in enumerate(rows)]
    return result


class Store:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS collections (
                id TEXT PRIMARY KEY,
                collected_at TEXT NOT NULL,
                environment TEXT NOT NULL CHECK(environment IN ('mock','real')),
                api_id TEXT NOT NULL DEFAULT 'ka00198',
                qry_tp TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('running','success','empty','error','interrupted')),
                record_count INTEGER NOT NULL DEFAULT 0 CHECK(record_count >= 0),
                error TEXT,
                response_json TEXT,
                UNIQUE(collected_at, environment, api_id, qry_tp)
            );
            CREATE TABLE IF NOT EXISTS ranking_records (
                collection_id TEXT NOT NULL REFERENCES collections(id),
                rank INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 20),
                stk_cd TEXT,
                stk_nm TEXT,
                raw_json TEXT NOT NULL,
                PRIMARY KEY(collection_id, rank),
                UNIQUE(collection_id, stk_cd)
            );
            CREATE INDEX IF NOT EXISTS idx_stock_history ON ranking_records(stk_cd, collection_id);
            CREATE INDEX IF NOT EXISTS idx_collection_time ON collections(collected_at);
            ''')

            # Additive migration: historical rows are production observations by default.
            with self.db:
                columns = {row['name'] for row in self.db.execute("PRAGMA table_info(collections)")}
                if 'test_mode' not in columns:
                    self.db.execute("ALTER TABLE collections ADD COLUMN test_mode INTEGER NOT NULL DEFAULT 0 CHECK(test_mode IN (0,1))")
                for name in ("error_stage", "diagnostic_json", "scheduled_at"):
                    if name not in columns:
                        self.db.execute(f"ALTER TABLE collections ADD COLUMN {name} TEXT")

                self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_scheduled_collection ON collections(scheduled_at,environment,api_id,qry_tp) WHERE scheduled_at IS NOT NULL")

            migrate(self.db)
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def begin(self, at, environment, qry_tp, *, test_mode=False, scheduled_at=None):
        from .client import KST
        timestamp = at.astimezone(KST).isoformat(timespec="seconds")
        planned = scheduled_at.astimezone(KST).isoformat(timespec="seconds") if scheduled_at is not None else None
        identifier = str(uuid4())
        with self.db:
            with closing(self.db.execute(
                "INSERT INTO collections(id,collected_at,environment,qry_tp,test_mode,scheduled_at,status) VALUES(?,?,?,?,?,?, 'running') "
                "ON CONFLICT DO NOTHING",
                (identifier, timestamp, environment, qry_tp, int(test_mode), planned))) as cursor:
                inserted = bool(cursor.rowcount)
        return identifier if inserted else None

    def finish(self, identifier, snapshot):
        records = [market_record(row, f"selected[{index}]") for index, row in enumerate(snapshot.records)]
        pages = [market_page(page, f"pages[{index}]") for index, page in enumerate(snapshot.pages)]
        with self.db:
            state = self.db.execute("SELECT status FROM collections WHERE id=?", (identifier,)).fetchone()
            if state is None or state['status'] != 'running':
                raise ValueError("진행 중인 수집에만 결과를 저장할 수 있습니다.")
            for row in records:
                self.db.execute("INSERT INTO ranking_records VALUES(?,?,?,?,?)",
                                (identifier, int(row['bigd_rank']), row.get('stk_cd'), row.get('stk_nm'),
                                 json.dumps(row, ensure_ascii=False)))
            for row in records:
                if not upsert_stock(self.db, row.get('stk_cd'), row.get('stk_nm')):
                    logging.getLogger(__name__).warning('종목 마스터 갱신 제외: 코드/이름 미제공 또는 지원 코드 형식 아님 (관측 원본은 보존)')
            self.db.execute("UPDATE collections SET status=?,record_count=?,response_json=? WHERE id=? AND status='running'",
                            ('success' if records else 'empty', len(records),
                             json.dumps(pages, ensure_ascii=False), identifier))

    def fail(self, identifier, message, interrupted=False, *, stage=None, diagnostic=None):
        with self.db:
            self.db.execute("UPDATE collections SET status=?,error=?,error_stage=?,diagnostic_json=? WHERE id=? AND status='running'",
                            ('interrupted' if interrupted else 'error', message, stage,
                             json.dumps(diagnostic, ensure_ascii=False) if diagnostic else None, identifier))

    def status(self):
        row = self.db.execute('''SELECT COUNT(*) AS attempts, MAX(collected_at) AS last_attempt,
            MAX(CASE WHEN status='success' THEN collected_at END) AS last_success,
            SUM(status='success') AS successes, SUM(status='error') AS errors,
            SUM(status='running') AS running, SUM(status='empty') AS empty,
            SUM(status='interrupted') AS interrupted, SUM(test_mode) AS tests FROM collections''').fetchone()
        return dict(row) | {'records': self.db.execute('SELECT COUNT(*) FROM ranking_records').fetchone()[0]}

    def last_failure(self):
        row = self.db.execute("SELECT id,collected_at,error,error_stage,diagnostic_json FROM collections WHERE status='error' ORDER BY collected_at DESC,rowid DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def latest(self):
        run = self.db.execute("SELECT * FROM collections WHERE status IN ('success','empty') ORDER BY collected_at DESC, rowid DESC LIMIT 1").fetchone()
        if run is None:
            return None, []
        rows = self.db.execute("SELECT rank,stk_cd,stk_nm FROM ranking_records WHERE collection_id=? ORDER BY rank", (run['id'],)).fetchall()
        return dict(run), [dict(row) for row in rows]
