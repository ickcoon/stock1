"""Recent ranking context for the existing manual workspace, without writes."""
from datetime import timedelta

from .quotes import CODE
from .radar import timestamp
from .sectors import valid_code
from .summary import _rows


def recent_order(rows):
    """Stable, lexicographic criteria; the existing priority order breaks ties."""
    ordered = sorted(rows, key=lambda r: r.get('last_ranking_seen') or '', reverse=True)
    ordered.sort(key=lambda r: (-r['recent_ranking_appearances'],
                               -r['recent_top5_appearances'], -r['recent_top10_appearances']))
    return ordered


def ranking_context(db, environment, at):
    since = at - timedelta(days=7)
    runs = []
    for row in _rows(db, '''SELECT rowid AS sequence,id,collected_at,qry_tp FROM collections
            WHERE environment=? AND test_mode=0 AND api_id='ka00198' AND status='success' ''', (environment,)):
        observed = timestamp(row['collected_at'])
        if observed is not None and observed <= at:
            runs.append(dict(row, observed=observed))
    runs.sort(key=lambda r: (r['observed'], r['sequence']))
    by_run = {r['id']: r for r in runs}
    metrics, run_codes = {}, {r['id']: set() for r in runs}
    for row in _rows(db, '''SELECT r.collection_id,r.rank,r.stk_cd,r.stk_nm FROM ranking_records r
            JOIN collections c ON c.id=r.collection_id WHERE c.environment=? AND c.test_mode=0
            AND c.api_id='ka00198' AND c.status='success' AND r.rank BETWEEN 1 AND 20''', (environment,)):
        run, code = by_run.get(row['collection_id']), row['stk_cd']
        if run is None or not isinstance(code, str) or CODE.fullmatch(code) is None:
            continue
        observed = run['observed']
        run_codes[run['id']].add(code)
        item = metrics.setdefault(code, dict(stock_code=code, stock_name='', first=observed,
            last=observed, recent_last=None, name_order=None, appearances=set(), top5=set(), top10=set()))
        item['first'] = min(item['first'], observed)
        item['last'] = max(item['last'], observed)
        order = observed, run['sequence']
        if item['name_order'] is None or order >= item['name_order']:
            item.update(stock_name=row['stk_nm'] or '', name_order=order)
        if observed >= since:
            item['appearances'].add(run['id'])
            if row['rank'] <= 5:
                item['top5'].add(run['id'])
            if row['rank'] <= 10:
                item['top10'].add(run['id'])
            item['recent_last'] = max(item['recent_last'] or observed, observed)
    latest = runs[-1] if runs else None
    prior = [r for r in runs[:-1] if r['qry_tp'] == latest['qry_tp']
             and r['observed'].date() == latest['observed'].date()] if latest else []
    previous = prior[-1] if prior else None
    current_codes = run_codes[latest['id']] if latest else set()
    previous_codes = run_codes[previous['id']] if previous else None

    def describe(code):
        item = metrics.get(code)
        last = item['last'] if item else None
        return dict(recent_ranking_appearances=len(item['appearances']) if item else 0,
            recent_top5_appearances=len(item['top5']) if item else 0,
            recent_top10_appearances=len(item['top10']) if item else 0,
            last_ranking_seen=last.isoformat() if last else None,
            last_ranking_date=last.date().isoformat() if last else None,
            recent_last_ranking_seen=item['recent_last'].isoformat() if item and item['recent_last'] else None,
            first_ranking_seen=item['first'].isoformat() if item else None,
            in_latest_top20=code in current_codes,
            new_in_latest=(code in current_codes and code not in previous_codes) if previous_codes is not None else None,
            is_unclassified=True, current_tags=[])

    def context_run(run):
        return ({k: run[k] for k in ('id','collected_at','qry_tp')}
                | {'collected_at': run['observed'].isoformat()} if run else None)

    tagged = {r['stock_code'] for r in _rows(db, 'SELECT DISTINCT stock_code FROM stock_sectors')}
    unsupported = [dict(stock_code=code, stock_name=item['stock_name'], csv_supported=False,
                        reason='현재 종목 마스터·CSV importer는 6자리 숫자 코드만 지원', **describe(code))
                   for code, item in metrics.items() if not valid_code(code) and code not in tagged]
    unsupported.sort(key=lambda r: r['stock_code'])
    return dict(describe=describe, unsupported_stocks=recent_order(unsupported),
                latest_ranking=context_run(latest), previous_ranking=context_run(previous),
                latest_unclassified_codes=sorted(current_codes-tagged))
