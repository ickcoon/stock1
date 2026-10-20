"""User-reviewed external CSV evidence, without network access or inferred themes.

stock_sectors remains the compatibility projection. Immutable dated evidence and
terms records retain multiple sources without overwriting that projection.
"""
from contextlib import closing
import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from types import SimpleNamespace

from .sectors import SectorManager, label, timestamp, valid_code, upsert_stock
from .summary import _rows, read_database
from .storage import Store

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS sector_external_imports (
        import_id TEXT PRIMARY KEY, registered_at TEXT NOT NULL, file_sha256 TEXT NOT NULL,
        file_name TEXT NOT NULL, report_json TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS sector_external_sources (
        source_id INTEGER PRIMARY KEY, source TEXT NOT NULL UNIQUE, registered_at TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS sector_source_terms (
        terms_id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sector_external_sources(source_id),
        terms_url TEXT NOT NULL, terms_note TEXT NOT NULL, checked_at TEXT NOT NULL, registered_at TEXT NOT NULL,
        UNIQUE(source_id,terms_url,terms_note,checked_at))''',
    '''CREATE TABLE IF NOT EXISTS sector_aliases (
        alias TEXT NOT NULL, source TEXT NOT NULL, sector_id INTEGER NOT NULL REFERENCES sectors(sector_id),
        registered_at TEXT NOT NULL, PRIMARY KEY(alias,source))''',
    '''CREATE TABLE IF NOT EXISTS sector_tag_evidence (
        evidence_id INTEGER PRIMARY KEY, stock_code TEXT NOT NULL REFERENCES stocks(stock_code),
        sector_id INTEGER NOT NULL REFERENCES sectors(sector_id),
        source_id INTEGER NOT NULL REFERENCES sector_external_sources(source_id),
        observed_at TEXT NOT NULL, registered_at TEXT NOT NULL,
        terms_id INTEGER NOT NULL REFERENCES sector_source_terms(terms_id),
        import_id TEXT NOT NULL REFERENCES sector_external_imports(import_id), input_sector_name TEXT NOT NULL,
        UNIQUE(stock_code,sector_id,source_id,observed_at))''',
    'CREATE INDEX IF NOT EXISTS idx_sector_evidence_stock ON sector_tag_evidence(stock_code,sector_id)',
)


def tables(db):
    return {r['name'] for r in _rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}


def migrate_external(db):
    # Caller owns BEGIN/commit: schema and imported rows roll back together.
    for sql in SCHEMA:
        with closing(db.execute(sql)):
            pass


def checked_date(value, field):
    if not isinstance(value, str):
        raise ValueError(f'{field}: YYYY-MM-DD 필요')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{field}: YYYY-MM-DD 필요') from None
    if parsed.isoformat() != value or parsed > date.fromisoformat(timestamp()[:10]):
        raise ValueError(f'{field}: 미래가 아닌 YYYY-MM-DD 필요')
    return value


def terms_url(value):
    parts = urlsplit(value)
    if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password or any(ord(c) < 32 for c in value):
        raise ValueError('terms_url: 인증정보 없는 http/https URL 필요')
    return value


def resolve_name(db, name, source):
    if 'sector_aliases' in tables(db):
        rows = _rows(db, '''SELECT s.sector_name FROM sector_aliases a JOIN sectors s ON s.sector_id=a.sector_id
            WHERE a.alias=? AND a.source IN (?, '*') ORDER BY (a.source=?) DESC LIMIT 1''', (name, source, source))
        if rows:
            return rows[0]['sector_name']
    return name


def add_alias(db, alias, canonical, source='*'):
    alias, canonical, source = label(alias, '별칭'), label(canonical, '섹터명'), label(source, '출처')
    with db:
        db.execute('BEGIN IMMEDIATE')
        migrate_external(db)
        target = db.execute('SELECT sector_id FROM sectors WHERE sector_name=?', (canonical,)).fetchone()
        if not target:
            raise ValueError('먼저 sector add로 정식 섹터명을 등록하세요.')
        existing = db.execute('SELECT sector_id FROM sectors WHERE sector_name=?', (alias,)).fetchone()
        if existing and existing['sector_id'] != target['sector_id']:
            raise ValueError('기존 섹터명을 다른 섹터의 별칭으로 바꿀 수 없습니다. 자동 병합하지 않습니다.')
        prior = db.execute('SELECT sector_id FROM sector_aliases WHERE alias=? AND source=?', (alias, source)).fetchone()
        if prior and prior['sector_id'] != target['sector_id']:
            raise ValueError('이미 다른 섹터에 연결된 별칭입니다. 기존 매핑을 덮어쓰지 않습니다.')
        db.execute('INSERT INTO sector_aliases VALUES(?,?,?,?) ON CONFLICT DO NOTHING', (alias, source, target['sector_id'], timestamp()))
    return {'alias': alias, 'sector_name': canonical, 'source': source, 'changed': not bool(prior)}


def is_external_csv(path):
    try:
        with Path(path).open(encoding='utf-8-sig', newline='') as handle:
            headers = csv.reader(handle,strict=True).__next__()
        return bool(set(headers) & {'source','observed_at','terms_url','terms_note','terms_checked_at'})
    except StopIteration:
        return False
    except (UnicodeError,csv.Error):
        raise ValueError('CSV는 올바른 UTF-8 CSV여야 합니다.') from None


def parse_csv(path, defaults):
    content = Path(path).read_bytes()
    allowed = {'stock_code', 'stock_name', 'sector_name', 'source', 'observed_at', 'terms_url', 'terms_note', 'terms_checked_at'}
    result, errors = [], []
    try:
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig'), newline=''), strict=True)
        headers = reader.fieldnames
        if not headers or len(headers) != len(set(headers)) or not {'stock_code','sector_name'} <= set(headers) or not set(headers) <= allowed:
            raise ValueError('외부 CSV: stock_code,sector_name 필수; 미지원/중복 헤더 확인 (README 참고)')
        for row in reader:
            line = reader.line_num
            try:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError('열 개수 불일치')
                values = {key: (row.get(key, '').strip() or defaults.get(key) or '') for key in allowed}
                if not valid_code(values['stock_code']):
                    raise ValueError('종목코드: 앞자리 0을 포함한 6자리 ASCII 숫자 필요')
                for key in ('sector_name','source'):
                    values[key] = label(values[key], key)
                for key in ('observed_at','terms_checked_at'):
                    values[key] = checked_date(values[key], key)
                values['terms_url'] = terms_url(values['terms_url'])
                values['terms_note'] = label(values['terms_note'], '이용조건 확인 메모')
                values['line'] = line
                result.append(values)
            except ValueError as exc:
                errors.append(f'{line}행: {exc}')
    except (UnicodeError, csv.Error):
        raise ValueError('CSV는 UTF-8 또는 UTF-8 BOM 및 올바른 CSV 구문이어야 합니다.') from None
    return content, result, errors


def observations(db, environment):
    """All completed historical appearances for classification-work priority only."""
    pool, excluded = {}, set()
    def add(code, name, at, origin, identifier, rank=None, alias=None):
        if not valid_code(code):
            excluded.add(str(code))
            return
        row = pool.setdefault(code, {'stock_code': code, 'stock_name': name or '', 'ranking_runs': set(),
            'condition_runs': set(), 'condition_aliases': set(), 'best_rank': None, 'last_seen': at})
        if at >= row['last_seen']:
            row['last_seen'] = at
            if name:
                row['stock_name'] = name
        row[origin].add(identifier)
        if alias:
            row['condition_aliases'].add(alias)
        if rank is not None:
            row['best_rank'] = rank if row['best_rank'] is None else min(rank, row['best_rank'])
    for row in _rows(db, '''SELECT r.stk_cd,r.stk_nm,r.rank,c.id,c.collected_at FROM ranking_records r
        JOIN collections c ON c.id=r.collection_id WHERE c.status='success' AND c.environment=?
        AND c.test_mode=0 AND c.api_id='ka00198' ORDER BY c.collected_at,c.rowid''', (environment,)):
        add(row['stk_cd'], row['stk_nm'], row['collected_at'], 'ranking_runs', row['id'], row['rank'])
    if {'condition_runs','condition_members','condition_definitions'} <= tables(db):
        for row in _rows(db, '''SELECT m.stock_code,m.stock_name,r.id,r.completed_at,d.alias FROM condition_members m
            JOIN condition_runs r ON r.id=m.run_id JOIN condition_definitions d ON d.id=r.definition_id
            WHERE r.status='success' AND d.environment=? ORDER BY r.completed_at,r.rowid''', (environment,)):
            add(row['stock_code'], row['stock_name'], row['completed_at'], 'condition_runs', row['id'], alias=row['alias'])
    return pool, len(excluded)


def coverage(db, pool):
    master = {r['stock_code'] for r in _rows(db, 'SELECT stock_code FROM stocks')}
    tags = _rows(db, '''SELECT t.stock_code,s.sector_name FROM stock_sectors t JOIN sectors s ON s.sector_id=t.sector_id''')
    classified = {r['stock_code'] for r in tags}
    def measure(codes):
        count = len(codes - classified)
        return {'stock_count': len(codes), 'unclassified_count': count,
                'unclassified_percent': count * 100 / len(codes) if codes else None}
    groups = {}
    for row in tags:
        group = groups.setdefault(row['sector_name'], {'master': set(), 'observed': set()})
        group['master'].add(row['stock_code'])
        if row['stock_code'] in pool:
            group['observed'].add(row['stock_code'])
    return {'master': measure(master), 'observed': measure(set(pool)),
            'sectors': {name: {'master_count': len(codes['master']), 'observed_count': len(codes['observed'])}
                        for name, codes in sorted(groups.items())}}


def prepare(db, raw, errors):
    rows, seen = [], set()
    report = {'errors': list(errors), 'new_tags': [], 'existing_tags': [], 'new_stocks': [],
              'new_evidence': [], 'existing_evidence': [], 'conflicts': [], 'applied': False}
    master = {r['stock_code']: r['stock_name'] for r in _rows(db, 'SELECT stock_code,stock_name FROM stocks')}
    has_evidence = 'sector_tag_evidence' in tables(db)
    pending_names, pending_sources, pending_themes = {}, {}, {}
    for values in raw:
        row = dict(values)
        code, source = row['stock_code'], row['source']
        row['canonical_name'] = resolve_name(db, row['sector_name'], source)
        name = row['canonical_name']
        key = code, name, source, row['observed_at']
        if key in seen:
            report['errors'].append(f"{row['line']}행: 별칭 정규화 후 중복 종목-섹터-출처-확인일")
            continue
        seen.add(key)
        if code not in master:
            try:
                row['stock_name'] = label(row['stock_name'], '미등록 종목의 stock_name')
            except ValueError:
                report['errors'].append(f"{row['line']}행: 미등록 종목에는 검토한 stock_name이 필요합니다.")
                continue
            if code in pending_names and pending_names[code] != row['stock_name']:
                report['errors'].append(f"{row['line']}행: 같은 신규 종목의 이름 불일치")
                continue
            pending_names[code] = row['stock_name']
        prior = db.execute('''SELECT s.sector_id,t.source FROM sectors s LEFT JOIN stock_sectors t
            ON t.sector_id=s.sector_id AND t.stock_code=? WHERE s.sector_name=?''', (code,name)).fetchone()
        item = {'stock_code': code, 'sector_name': name, 'input_sector_name': row['sector_name'],
                'source': source, 'observed_at': row['observed_at']}
        (report['existing_tags'] if prior and prior['source'] is not None else report['new_tags']).append(item)
        old = None
        other_sources = set()
        if prior and prior['source']:
            other_sources.add(prior['source'])
        if has_evidence and prior:
            all_evidence = _rows(db, '''SELECT e.observed_at,s.source,t.terms_url,t.terms_note,t.checked_at
                FROM sector_tag_evidence e JOIN sector_external_sources s ON s.source_id=e.source_id
                JOIN sector_source_terms t ON t.terms_id=e.terms_id WHERE e.stock_code=? AND e.sector_id=?''', (code,prior['sector_id']))
            other_sources.update(e['source'] for e in all_evidence)
            old = next((e for e in all_evidence if e['source'] == source and e['observed_at'] == row['observed_at']),None)
        if old:
            if (old['terms_url'],old['terms_note'],old['checked_at']) != (row['terms_url'],row['terms_note'],row['terms_checked_at']):
                report['errors'].append(f"{row['line']}행: 동일 근거의 이용조건 정보 변경 충돌. 기존 기록을 수정하지 않습니다.")
            report['existing_evidence'].append(item)
        else:
            report['new_evidence'].append(item)
        others = sorted((other_sources | pending_sources.get((code,name),set())) - {source})
        if others:
            report['conflicts'].append(item | {'kind':'additional_source', 'existing_sources':others,
                'resolution':'기존 출처/태그 보존, 새 근거만 추가'})
        other_themes = _rows(db, '''SELECT s.sector_name,t.source FROM stock_sectors t JOIN sectors s ON s.sector_id=t.sector_id
            WHERE t.stock_code=? AND s.sector_name<>?''', (code,name))
        other_themes = [dict(t) for t in other_themes] + [t for t in pending_themes.get(code,[]) if t['sector_name'] != name]
        if other_themes:
            report['conflicts'].append(item | {'kind':'different_theme', 'existing_tags':[dict(t) for t in other_themes],
                'resolution':'다중 테마로 공존; 의미상 모순 여부는 수동 검토'})
        pending_sources.setdefault((code,name),set()).add(source)
        pending_themes.setdefault(code,[]).append({'sector_name':name,'source':source})
        row['existing_evidence'] = bool(old)
        rows.append(row)
    for field in ('new_tags','existing_tags'):
        report[field] = list({(r['stock_code'],r['sector_name']):r for r in report[field]}.values())
    report['counts'] = {key:len(report[key]) for key in ('new_tags','existing_tags','new_evidence','existing_evidence','conflicts')}
    report['new_stocks'] = [{'stock_code':code,'stock_name':name} for code,name in sorted(pending_names.items())]
    return rows, report


def import_external(db, path, defaults, environment='mock', dry_run=False):
    content, raw, errors = parse_csv(path, defaults)
    # Dry-run caller uses read-only snapshot; apply revalidates inside one write transaction.
    if dry_run:
        rows, report = prepare(db, raw, errors)
        report.update(dry_run=True, environment=environment, valid_rows=len(rows),
                      file_sha256=hashlib.sha256(content).hexdigest())
        report['before'] = coverage(db, observations(db,environment)[0])
        report['note'] = 'conflicts는 검토용 공존 정보; errors가 있으면 전체 적용 금지. 예상치를 관측값으로 표시하지 않습니다.'
        return report
    with db:
        db.execute('BEGIN IMMEDIATE')
        rows, report = prepare(db, raw, errors)
        report.update(dry_run=False, environment=environment, valid_rows=len(rows),
                      file_sha256=hashlib.sha256(content).hexdigest())
        if report['errors']:
            return report
        pool, _ = observations(db, environment)
        report['before'] = coverage(db, pool)
        migrate_external(db)
        import_id, at = str(uuid4()), timestamp()
        db.execute('INSERT INTO sector_external_imports VALUES(?,?,?,?,?)', (import_id,at,report['file_sha256'],Path(path).name,'{}'))
        for stock in report['new_stocks']:
            upsert_stock(db, stock['stock_code'],stock['stock_name'])
        for row in rows:
            name, source = row['canonical_name'],row['source']
            db.execute('INSERT INTO sectors(sector_name,created_at) VALUES(?,?) ON CONFLICT DO NOTHING',(name,at))
            sector_id = db.execute('SELECT sector_id FROM sectors WHERE sector_name=?',(name,)).fetchone()[0]
            if not db.execute('SELECT 1 FROM stock_sectors WHERE stock_code=? AND sector_id=?',(row['stock_code'],sector_id)).fetchone():
                SectorManager(SimpleNamespace(db=db))._tag(row['stock_code'],sector_id,source)
            if row['existing_evidence']:
                continue
            db.execute('INSERT INTO sector_external_sources(source,registered_at) VALUES(?,?) ON CONFLICT DO NOTHING',(source,at))
            source_id = db.execute('SELECT source_id FROM sector_external_sources WHERE source=?',(source,)).fetchone()[0]
            terms = (source_id,row['terms_url'],row['terms_note'],row['terms_checked_at'])
            db.execute('INSERT INTO sector_source_terms(source_id,terms_url,terms_note,checked_at,registered_at) VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING',(*terms,at))
            terms_id = db.execute('SELECT terms_id FROM sector_source_terms WHERE source_id=? AND terms_url=? AND terms_note=? AND checked_at=?',terms).fetchone()[0]
            db.execute('''INSERT INTO sector_tag_evidence(stock_code,sector_id,source_id,observed_at,registered_at,terms_id,import_id,input_sector_name)
                VALUES(?,?,?,?,?,?,?,?)''',(row['stock_code'],sector_id,source_id,row['observed_at'],at,terms_id,import_id,row['sector_name']))
        report.update(applied=True, import_id=import_id, registered_at=at, after=coverage(db,pool))
        report['sector_changes'] = [ {'sector_name':name,
            'before': report['before']['sectors'].get(name,{'master_count':0,'observed_count':0}),
            'after': report['after']['sectors'].get(name,{'master_count':0,'observed_count':0})}
            for name in sorted(set(report['before']['sectors']) | set(report['after']['sectors']))]
        report['scope'] = '동일 트랜잭션의 고정된 전체 성공 관측 합집합. 환경 분리, 테스트 제외. 현재 태그 기준.'
        db.execute('UPDATE sector_external_imports SET report_json=? WHERE import_id=?',(json.dumps(report,ensure_ascii=False),import_id))
        return report


def exclusive_output(path, suffix, write):
    path = Path(path).resolve()
    if path.suffix.lower() != suffix:
        raise ValueError(f'출력은 {suffix} 파일이어야 합니다.')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open('x',encoding='utf-8-sig' if suffix == '.csv' else 'utf-8',newline='')
    except FileExistsError:
        raise ValueError('출력 파일이 이미 존재합니다. 덮어쓰지 않습니다.') from None
    try:
        with handle:
            write(handle)
    except BaseException:
        path.unlink()
        raise
    return str(path)


def priority_rows(db, environment):
    pool, excluded = observations(db,environment)
    tagged = {r['stock_code'] for r in _rows(db, 'SELECT DISTINCT stock_code FROM stock_sectors')}
    master = {r['stock_code']:r['stock_name'] for r in _rows(db,'SELECT stock_code,stock_name FROM stocks')}
    items = []
    for code, item in pool.items():
        if code in tagged:
            continue
        items.append({'stock_code':code,'stock_name':master.get(code,item['stock_name']),
            'ranking_appearances':len(item['ranking_runs']), 'condition_appearances':len(item['condition_runs']),
            'condition_aliases':sorted(item['condition_aliases']), 'best_rank':item['best_rank'],
            'last_seen':item['last_seen'], 'in_both':bool(item['ranking_runs'] and item['condition_runs']),
            'in_master':code in master})
    items.sort(key=lambda r: (-int(r['in_both']),-len(r['condition_aliases']),
        -(r['ranking_appearances']+r['condition_appearances']),r['best_rank'] or 999,r['stock_code']))
    return items, excluded


def export_priority(db, output, environment):
    items, excluded = priority_rows(db, environment)
    def write(handle):
        writer = csv.DictWriter(handle,fieldnames=['stock_code','stock_name','sector_name'])
        writer.writeheader()
        writer.writerows({k:r[k] if k != 'sector_name' else '' for k in ('stock_code','stock_name','sector_name')} for r in items)
    path = exclusive_output(output,'.csv',write)
    return {'output':path,'environment':environment,'encoding':'utf-8-sig','row_count':len(items),'priorities':items,
            'excluded_unsupported_code_count':excluded,
            'note':'전체 성공 이력'+'의 미분류 합집합; 양쪽 출현→조건식 종류 수→총 출현 회차→최고 조회순위→코드 순. 분류 작업 우선순위이며 투자 점수 아님.'}


def provenance(db, code):
    if not valid_code(code):
        raise ValueError('종목코드는 6자리 숫자 문자열이어야 합니다.')
    legacy = [dict(r) | {'observed_at':None} for r in _rows(db,'''SELECT s.sector_name,t.source,t.created_at,t.updated_at
        FROM stock_sectors t JOIN sectors s ON s.sector_id=t.sector_id WHERE t.stock_code=?''',(code,))]
    evidence = []
    if 'sector_tag_evidence' in tables(db):
        evidence = [dict(r) for r in _rows(db,'''SELECT e.evidence_id,s.sector_name,x.source,e.observed_at,e.registered_at,
            e.input_sector_name,e.import_id,t.terms_url,t.terms_note,t.checked_at AS terms_checked_at
            FROM sector_tag_evidence e JOIN sectors s ON s.sector_id=e.sector_id
            JOIN sector_external_sources x ON x.source_id=e.source_id JOIN sector_source_terms t ON t.terms_id=e.terms_id
            WHERE e.stock_code=? ORDER BY e.evidence_id''',(code,))]
    for tag in legacy:
        dates = [e['observed_at'] for e in evidence if e['sector_name'] == tag['sector_name'] and e['source'] == tag['source']]
        tag['observed_at'] = max(dates) if dates else None
    history = [dict(r) for r in _rows(db,'''SELECT s.sector_name,e.action,e.source,e.changed_at FROM stock_sector_events e
        JOIN sectors s ON s.sector_id=e.sector_id WHERE e.stock_code=? ORDER BY e.event_id''',(code,))]
    return {'stock_code':code,'current_tags':legacy,'evidence':evidence,'tag_history':history,
            'note':'근거가 없는 기존 단일 출처 태그의 확인일은 미상(null). 근거는 확인일별 추가 기록이며 CSV에서 빠졌다고 삭제하지 않음. 이용조건은 사용자 확인 기록, 이용 허가 보증 아님.'}


def external_command(path, args, *, source=None, observed_at=None, terms_link=None, terms_note=None,
                     terms_checked_at=None, dry_run=False, environment='mock', output=None):
    defaults = {'source':source,'observed_at':observed_at,'terms_url':terms_link,
                'terms_note':terms_note,'terms_checked_at':terms_checked_at}
    if args[:1] == ['import'] and len(args) == 2:
        if output is not None:
            raise ValueError('가져오기 보고서는 DB에 보존합니다. import-report로 내보내세요.')
        if dry_run:
            with read_database(path) as (db,_):
                report = import_external(db,args[1],defaults,environment,True)
        else:
            with closing(Store(path)) as store:
                report = import_external(store.db,args[1],defaults,environment)
        return report, 1 if report['errors'] else 0
    if dry_run or any(v is not None for k,v in defaults.items() if k != 'source'):
        raise ValueError('확인일/이용조건/--dry-run은 외부 sector import 전용입니다.')
    if args[:2] == ['alias','add'] and len(args) == 4:
        if output is not None:
            raise ValueError('alias add는 --output을 사용하지 않습니다.')
        with closing(Store(path)) as store:
            return add_alias(store.db,args[2],args[3],source or '*'),0
    if source is not None:
        raise ValueError('--source는 alias add 또는 import에서 사용하세요.')
    with read_database(path) as (db,_):
        if args == ['export-priority']:
            if output is None:
                raise ValueError('sector export-priority에는 --output CSV가 필요합니다.')
            return export_priority(db,output,environment),0
        if args[:1] == ['provenance'] and len(args) == 2:
            if output is not None:
                raise ValueError('provenance는 --output을 사용하지 않습니다.')
            return provenance(db,args[1]),0
        if args == ['alias','list']:
            return [dict(r) for r in _rows(db,'''SELECT a.alias,a.source,s.sector_name,a.registered_at
                FROM sector_aliases a JOIN sectors s ON s.sector_id=a.sector_id ORDER BY a.source,a.alias''')] if 'sector_aliases' in tables(db) else [],0
        if args[:1] == ['import-report'] and len(args) == 2:
            if 'sector_external_imports' not in tables(db):
                raise ValueError('외부 CSV 가져오기 이력이 없습니다.')
            rows = _rows(db,'SELECT report_json FROM sector_external_imports ORDER BY registered_at DESC,rowid DESC LIMIT 1') if args[1] == 'latest' else _rows(db,'SELECT report_json FROM sector_external_imports WHERE import_id=?',(args[1],))
            if not rows:
                raise ValueError('지정한 가져오기 보고서가 없습니다.')
            result = json.loads(rows[0]['report_json'])
            if output is not None:
                exclusive_output(output,'.json',lambda handle: json.dump(result,handle,ensure_ascii=False,indent=2))
            return result,0
    raise ValueError('외부 섹터 명령 인자를 확인하세요: README 참고')
