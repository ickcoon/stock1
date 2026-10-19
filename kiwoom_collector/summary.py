"""Read-only sector grouping of the latest successful ka00198 observation."""
from contextlib import closing
from pathlib import Path
import sqlite3


def _rows(db, sql, parameters=()):
    with closing(db.execute(sql, parameters)) as cursor:
        return cursor.fetchall()


def sector_summary(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('DB가 없습니다. 수집 또는 기존 DB 경로를 확인하세요. summary는 DB를 생성하지 않습니다.')
    # mode=ro keeps WAL visibility; immutable=1 would incorrectly ignore live WAL.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        with closing(db.execute('PRAGMA query_only=ON')):
            pass
        with closing(db.execute('BEGIN')):
            pass
        try:
            tables = {row['name'] for row in _rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'collections', 'ranking_records', 'stocks', 'sectors', 'stock_sectors'} <= tables:
                raise ValueError('섹터 분석에 필요한 DB 스키마가 없습니다. 백업 후 status로 마이그레이션하세요. summary 자체는 스키마를 변경하지 않습니다.')
            return _summarize(db, path)
        finally:
            db.rollback()  # End the consistent read snapshot before connection close.


def _summarize(db, path):
    result = {
        'db': str(path), 'collection': None, 'top_n': 20,
        'observed_record_count': 0, 'unique_stock_count': 0,
        'tagged_unique_stock_count': 0, 'unclassified_stock_count': 0,
        'unclassified_stocks': [], 'unidentified_record_count': 0, 'unidentified_records': [],
        'sectors': [],
        'note': '현재 섹터 태그 기준 분석. 군집 후보는 2종목 이상이라는 뜻이며 주도섹터·투자 판단이 아닙니다. 비율 분모는 항상 20입니다.',
    }
    runs = _rows(db, """SELECT id,collected_at,environment,test_mode,qry_tp,record_count FROM collections
        WHERE status='success' AND api_id='ka00198'
        ORDER BY collected_at DESC,rowid DESC LIMIT 1""")
    if not runs:
        result['note'] = '성공한 ka00198 수집 이력이 없습니다. ' + result['note']
        return result
    run = dict(runs[0])
    run['test_mode'] = bool(run['test_mode'])
    run['is_mock'] = run['environment'] == 'mock'
    result['collection'] = run
    observations = _rows(db, """SELECT rank,stk_cd,stk_nm FROM ranking_records
        WHERE collection_id=? AND rank BETWEEN 1 AND 20 ORDER BY rank""", (run['id'],))
    result['observed_record_count'] = len(observations)
    stocks = {}
    for row in observations:
        stock = {'stock_code': row['stk_cd'], 'stock_name': row['stk_nm'], 'rank': row['rank']}
        if not isinstance(row['stk_cd'], str) or not row['stk_cd'].strip():
            result['unidentified_records'].append(stock)
        else:
            # Count each real stock code once; first observation has its best rank.
            stocks.setdefault(row['stk_cd'], stock)
    result['unique_stock_count'] = len(stocks)
    result['unidentified_record_count'] = len(result['unidentified_records'])
    tags = []
    if stocks:
        placeholders = ','.join('?' for _ in stocks)
        tags = _rows(db, f"""SELECT t.stock_code,s.sector_id,s.sector_name
            FROM stock_sectors t JOIN sectors s ON s.sector_id=t.sector_id
            WHERE t.stock_code IN ({placeholders}) ORDER BY s.sector_name,s.sector_id,t.stock_code""", tuple(stocks))
    groups, classified = {}, set()
    for tag in tags:
        group = groups.setdefault(tag['sector_id'], {'sector_id': tag['sector_id'], 'sector_name': tag['sector_name'], 'members': {}})
        group['members'][tag['stock_code']] = stocks[tag['stock_code']]
        classified.add(tag['stock_code'])
    for group in groups.values():
        members = sorted(group.pop('members').values(), key=lambda stock: stock['rank'])
        count = len(members)
        result['sectors'].append(group | {
            'stock_count': count, 'stocks': members, 'best_rank': min(stock['rank'] for stock in members),
            'top20_ratio_percent': count * 100 / 20,
            'cluster_candidate': count >= 2, 'label': '군집 후보' if count >= 2 else '단일 종목',
        })
    result['sectors'].sort(key=lambda group: (-group['stock_count'], group['best_rank'], group['sector_name'], group['sector_id']))
    result['tagged_unique_stock_count'] = len(classified)
    result['unclassified_stocks'] = [stock for code, stock in stocks.items() if code not in classified]
    result['unclassified_stock_count'] = len(result['unclassified_stocks'])
    return result
