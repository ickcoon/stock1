"""Manual sector taxonomy, provenance and atomic UTF-8 CSV import. No inference."""
from contextlib import closing
import csv
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import re

LOG = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


def timestamp():
    return datetime.now(KST).isoformat(timespec='microseconds')


def valid_code(code):
    return isinstance(code, str) and re.fullmatch(r'[0-9]{6}', code) is not None


def label(value, field):
    value = value.strip()
    if not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise ValueError(f'{field}: 비어 있지 않은 1~200자 텍스트를 입력하세요 (제어 문자 제외).')
    return value


def upsert_stock(db, code, name):
    if not valid_code(code) or not isinstance(name, str) or not name.strip():
        return False
    db.execute('''INSERT INTO stocks(stock_code, stock_name, updated_at) VALUES(?,?,?)
        ON CONFLICT(stock_code) DO UPDATE SET stock_name=excluded.stock_name,updated_at=excluded.updated_at
        WHERE stocks.stock_name != excluded.stock_name''', (code, name, timestamp()))
    return True


def migrate(db):
    # All DDL, backfill and migration marker commit together; never rewrite historical rows.
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
        db.execute('''CREATE TABLE IF NOT EXISTS stocks (
            stock_code TEXT PRIMARY KEY CHECK(length(stock_code)=6 AND stock_code NOT GLOB '*[^0-9]*'),
            stock_name TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS sectors (
            sector_id INTEGER PRIMARY KEY, sector_name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS stock_sectors (
            stock_code TEXT NOT NULL REFERENCES stocks(stock_code),
            sector_id INTEGER NOT NULL REFERENCES sectors(sector_id),
            source TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(stock_code,sector_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS stock_sector_events (
            event_id INTEGER PRIMARY KEY, stock_code TEXT NOT NULL REFERENCES stocks(stock_code),
            sector_id INTEGER NOT NULL REFERENCES sectors(sector_id),
            action TEXT NOT NULL CHECK(action IN ('tag','untag')),
            source TEXT NOT NULL, changed_at TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_sector_members ON stock_sectors(sector_id,stock_code)')
        if not db.execute("SELECT 1 FROM schema_migrations WHERE name='stock_master_v1'").fetchone():
            skipped = 0
            with closing(db.execute('''SELECT r.stk_cd,r.stk_nm FROM ranking_records r
                JOIN collections c ON c.id=r.collection_id WHERE c.status='success'
                ORDER BY c.collected_at,c.rowid,r.rank''')) as rows:
                for row in rows:
                    if not upsert_stock(db, row['stk_cd'], row['stk_nm']):
                        skipped += 1
            db.execute("INSERT INTO schema_migrations VALUES('stock_master_v1',?)", (timestamp(),))
            if skipped:
                LOG.warning('종목 마스터 초기화: 코드/이름 미제공 또는 6자리 숫자 코드가 아닌 과거 관측 %d건 제외 (원본 이력 보존)', skipped)


class SectorManager:
    def __init__(self, store):
        self.db = store.db

    def _stock(self, code):
        if not valid_code(code):
            raise ValueError('종목코드는 앞자리 0을 포함한 6자리 숫자 문자열이어야 합니다.')
        row = self.db.execute('SELECT * FROM stocks WHERE stock_code=?', (code,)).fetchone()
        if row is None:
            raise ValueError('종목 마스터에 없는 종목입니다. 조회순위 수집으로 먼저 등록해야 합니다.')
        return row

    def _sector(self, name):
        name = label(name, '섹터명')
        row = self.db.execute('SELECT * FROM sectors WHERE sector_name=?', (name,)).fetchone()
        if row is None:
            raise ValueError('등록되지 않은 섹터입니다. sector add로 먼저 생성하세요.')
        return row

    def add(self, name):
        name = label(name, '섹터명')
        with self.db:
            self.db.execute('INSERT INTO sectors(sector_name,created_at) VALUES(?,?) ON CONFLICT(sector_name) DO NOTHING', (name, timestamp()))
        return dict(self._sector(name))

    def _tag(self, code, sector_id, source):
        previous = self.db.execute('SELECT source FROM stock_sectors WHERE stock_code=? AND sector_id=?', (code, sector_id)).fetchone()
        if previous and previous['source'] == source:
            return 'unchanged'
        at = timestamp()
        self.db.execute('''INSERT INTO stock_sectors VALUES(?,?,?,?,?)
            ON CONFLICT(stock_code,sector_id) DO UPDATE SET source=excluded.source,updated_at=excluded.updated_at''',
                        (code, sector_id, source, at, at))
        self.db.execute("INSERT INTO stock_sector_events(stock_code,sector_id,action,source,changed_at) VALUES(?,?,'tag',?,?)", (code, sector_id, source, at))
        return 'updated' if previous else 'added'

    def tag(self, code, name, source='수동'):
        source = label(source, '출처')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._stock(code)
            sector = self._sector(name)
            return self._tag(code, sector['sector_id'], source)

    def untag(self, code, name, source='수동'):
        source = label(source, '출처')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._stock(code)
            sector = self._sector(name)
            with closing(self.db.execute('DELETE FROM stock_sectors WHERE stock_code=? AND sector_id=?', (code, sector['sector_id']))) as cursor:
                removed = bool(cursor.rowcount)
            if removed:
                self.db.execute("INSERT INTO stock_sector_events(stock_code,sector_id,action,source,changed_at) VALUES(?,?,'untag',?,?)", (code, sector['sector_id'], source, timestamp()))
            return removed

    def show(self, code):
        stock = dict(self._stock(code))
        tags = self.db.execute('''SELECT s.sector_id,s.sector_name,t.source,t.created_at,t.updated_at
            FROM stock_sectors t JOIN sectors s ON s.sector_id=t.sector_id
            WHERE t.stock_code=? ORDER BY s.sector_name''', (code,)).fetchall()
        return stock | {'sectors': [dict(row) for row in tags]}

    def list(self):
        with closing(self.db.execute('''SELECT s.sector_id,s.sector_name,s.created_at,t.stock_code,k.stock_name,t.source,t.updated_at
            FROM sectors s LEFT JOIN stock_sectors t ON t.sector_id=s.sector_id
            LEFT JOIN stocks k ON k.stock_code=t.stock_code ORDER BY s.sector_name,t.stock_code''')) as rows:
            return [dict(row) for row in rows]

    def untagged(self):
        with closing(self.db.execute('''SELECT * FROM stocks k WHERE NOT EXISTS
            (SELECT 1 FROM stock_sectors t WHERE t.stock_code=k.stock_code) ORDER BY stock_code''')) as rows:
            return [dict(row) for row in rows]

    def export_untagged(self, output):
        path = Path(output).resolve()
        if path.suffix.lower() != '.csv':
            raise ValueError('--output은 .csv 파일 경로여야 합니다.')
        rows = self.untagged()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = path.open('x', encoding='utf-8-sig', newline='')
        except FileExistsError:
            raise ValueError('출력 파일이 이미 존재합니다. 편집본 보존을 위해 덮어쓰지 않습니다. 다른 CSV 파일명을 지정하세요.') from None
        try:
            with handle:
                writer = csv.DictWriter(handle, fieldnames=['stock_code', 'stock_name', 'sector_name'])
                writer.writeheader()
                for row in rows:
                    writer.writerow({'stock_code': row['stock_code'], 'stock_name': row['stock_name'], 'sector_name': ''})
        except BaseException:
            # Only remove the new file opened exclusively by this operation.
            path.unlink()
            raise
        return {'output': str(path), 'row_count': len(rows), 'encoding': 'utf-8-sig',
                'columns': ['stock_code', 'stock_name', 'sector_name']}

    def import_csv(self, path, source='CSV', dry_run=False):
        source = label(source, '출처')
        errors, pairs, seen = [], [], set()
        try:
            with Path(path).open(encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle, strict=True)
                headers = reader.fieldnames
                if headers is None or len(headers) != len(set(headers)) or set(headers) not in ({'stock_code', 'sector_name'}, {'stock_code', 'stock_name', 'sector_name'}):
                    raise ValueError('CSV 헤더는 stock_code,sector_name 또는 stock_code,stock_name,sector_name이어야 합니다.')
                # stock_name is for human review only; never overwrite the API-owned master name.
                for row in reader:
                    line = reader.line_num
                    if None in row or any(value is None for value in row.values()):
                        errors.append(f'{line}행: 열 개수 불일치')
                        continue
                    code, name = row['stock_code'].strip(), row['sector_name'].strip()
                    if not valid_code(code):
                        errors.append(f'{line}행: 종목코드 형식 오류 (6자리 숫자 필요)')
                        continue
                    try:
                        name = label(name, '섹터명')
                    except ValueError:
                        errors.append(f'{line}행: 섹터명 형식 오류')
                        continue
                    pair = (code, name)
                    if pair in seen:
                        errors.append(f'{line}행: CSV 내 중복 종목-섹터 행')
                        continue
                    seen.add(pair)
                    pairs.append((line, code, name))
        except (UnicodeError, csv.Error):
            raise ValueError('CSV는 UTF-8(또는 UTF-8 BOM) 형식이어야 하며 올바른 CSV 구문을 사용해야 합니다.') from None
        report = {'dry_run': dry_run, 'valid_rows': 0, 'new_sectors': [], 'added': 0, 'updated': 0, 'unchanged': 0, 'errors': errors, 'applied': False}
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            valid = []
            for line, code, name in pairs:
                if not self.db.execute('SELECT 1 FROM stocks WHERE stock_code=?', (code,)).fetchone():
                    errors.append(f'{line}행: 종목 마스터에 없는 종목')
                else:
                    valid.append((code, name))
            report['valid_rows'] = len(valid)
            report['new_sectors'] = sorted({name for _, name in valid if not self.db.execute('SELECT 1 FROM sectors WHERE sector_name=?', (name,)).fetchone()})
            for code, name in valid:
                row = self.db.execute('''SELECT t.source FROM stock_sectors t JOIN sectors s ON s.sector_id=t.sector_id
                    WHERE t.stock_code=? AND s.sector_name=?''', (code, name)).fetchone()
                key = 'unchanged' if row and row['source'] == source else 'updated' if row else 'added'
                report[key] += 1
            if errors or dry_run:
                return report
            for name in report['new_sectors']:
                self.db.execute('INSERT INTO sectors(sector_name,created_at) VALUES(?,?)', (name, timestamp()))
            for code, name in valid:
                sector_id = self._sector(name)['sector_id']
                self._tag(code, sector_id, source)
            report['applied'] = True
        return report


def sector_command(store, args, source=None, dry_run=False, output=None):
    manager = SectorManager(store)
    if not args:
        raise ValueError('sector add/tag/untag/show/list/untagged/import/export-untagged/summary/timeline 중 하나를 지정하세요.')
    action, *values = args
    arities = {'add': 1, 'tag': 2, 'untag': 2, 'show': 1, 'list': 0, 'untagged': 0, 'import': 1, 'export-untagged': 0}
    if action not in arities or len(values) != arities[action]:
        raise ValueError('sector 명령 인자가 잘못되었습니다. README의 명령 예시를 확인하세요.')
    if source is not None and action not in {'tag', 'untag', 'import'}:
        raise ValueError('--source는 sector tag/untag/import에서만 사용할 수 있습니다.')
    if dry_run and action != 'import':
        raise ValueError('--dry-run은 sector import에서만 사용할 수 있습니다.')
    if output is not None and action != 'export-untagged':
        raise ValueError('--output은 sector export-untagged에서만 사용할 수 있습니다.')
    if action == 'export-untagged':
        if output is None:
            raise ValueError('sector export-untagged에는 --output CSV경로가 필요합니다.')
        return manager.export_untagged(output), 0
    if action == 'import':
        report = manager.import_csv(values[0], source=source if source is not None else 'CSV', dry_run=dry_run)
        return report, 1 if report['errors'] else 0
    if action == 'add':
        return manager.add(*values), 0
    if action == 'tag':
        return {'result': manager.tag(*values, source=source if source is not None else '수동')}, 0
    if action == 'untag':
        return {'removed': manager.untag(*values, source=source if source is not None else '수동')}, 0
    return getattr(manager, action)(*values), 0
