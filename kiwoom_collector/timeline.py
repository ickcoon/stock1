"""Descriptive scheduled-sector comparisons, not price signals or investment decisions."""
from datetime import date

from .schedule import SLOTS, slot_at
from .summary import read_database, _rows, _summarize


def _stocks(summary):
    result = {stock['stock_code']: stock for stock in summary['unclassified_stocks']}
    for sector in summary['sectors']:
        result.update({stock['stock_code']: stock for stock in sector['stocks']})
    return result


def _change(current, previous):
    retained = set(current) & set(previous)
    return {
        'entered': [current[code] for code in sorted(set(current) - set(previous))],
        'exited': [previous[code] for code in sorted(set(previous) - set(current))],
        'retained_count': len(retained),
        'retention_percent': 100 * len(retained) / len(previous) if previous else None,
        'rank_changes': [{'stock_code': code, 'previous_rank': previous[code]['rank'],
                          'current_rank': current[code]['rank'],
                          'improvement': previous[code]['rank'] - current[code]['rank']}
                         for code in sorted(retained)],
    }


def _groups(summary):
    groups = {}
    for sector in summary['sectors']:
        members = {stock['stock_code']: stock for stock in sector['stocks']}
        groups[sector['sector_id']] = sector | {
            'top5_count': sum(stock['rank'] <= 5 for stock in members.values()),
            'top10_count': sum(stock['rank'] <= 10 for stock in members.values()),
            'top5_ratio_percent': sum(stock['rank'] <= 5 for stock in members.values()) * 100 / 5,
            'top10_ratio_percent': sum(stock['rank'] <= 10 for stock in members.values()) * 100 / 10,
            'mean_rank': sum(stock['rank'] for stock in members.values()) / len(members),
        }
    return groups


def compare(current, previous):
    if current is None or previous is None:
        return {'available': False, 'reason': '대응하는 성공 수집이 없어 비교 불가 (누락/빈 결과/실패를 0으로 대체하지 않음)'}
    current_groups, previous_groups = _groups(current), _groups(previous)
    sectors = []
    for key in sorted(set(current_groups) | set(previous_groups)):
        new, old = current_groups.get(key), previous_groups.get(key)
        now_members = {s['stock_code']: s for s in new['stocks']} if new else {}
        old_members = {s['stock_code']: s for s in old['stocks']} if old else {}
        sectors.append({
            'sector_id': key, 'sector_name': (new or old)['sector_name'],
            'previous_count': len(old_members), 'current_count': len(now_members),
            'count_delta': len(now_members) - len(old_members),
            'best_rank_improvement': old['best_rank'] - new['best_rank'] if new and old else None,
            'mean_rank_improvement': old['mean_rank'] - new['mean_rank'] if new and old else None,
            'new_cluster': len(now_members) >= 2 and len(old_members) < 2,
            **_change(now_members, old_members),
        })
    return {'available': True, **_change(_stocks(current), _stocks(previous)), 'sectors': sectors}


def sector_timeline(path, day, environment='mock', qry_tp='1'):
    try:
        parsed = date.fromisoformat(day)
        if parsed.isoformat() != day:
            raise ValueError()
    except ValueError:
        raise ValueError('--date는 YYYY-MM-DD 형식이어야 합니다.') from None
    if environment not in {'mock', 'real'} or qry_tp not in {'1', '2', '3', '4', '5'}:
        raise ValueError('분석 환경/집계 구분 오류')
    with read_database(path) as (db, resolved):
        columns = {row['name'] for row in _rows(db, 'PRAGMA table_info(collections)')}
        if 'scheduled_at' not in columns:
            raise ValueError('고정 일정 스키마가 없습니다. 백업 후 status로 마이그레이션하세요.')
        summaries, slots = {}, []
        for slot in SLOTS:
            planned = slot_at(parsed, slot).isoformat(timespec='seconds')
            runs = _rows(db, """SELECT id,collected_at,scheduled_at,status,error_stage,record_count FROM collections
                WHERE scheduled_at=? AND environment=? AND qry_tp=? AND api_id='ka00198' AND test_mode=0
                ORDER BY rowid DESC LIMIT 1""", (planned, environment, qry_tp))
            run = dict(runs[0]) if runs else None
            summary = _summarize(db, resolved, run['id']) if run and run['status'] == 'success' else None
            summaries[slot] = summary
            groups = list(_groups(summary).values()) if summary else []
            slots.append({'slot': slot, 'scheduled_at': planned, 'attempt': run,
                          'status': run['status'] if run else 'missing',
                          'sectors': groups, 'summary': summary})
        for index, item in enumerate(slots):
            slot = item['slot']
            current = summaries[slot]
            item['previous_slot'] = SLOTS[index - 1] if index else None
            item['versus_previous'] = compare(current, summaries[SLOTS[index - 1]]) if index else {'available': False, 'reason': '첫 예정 시점'}
            if slot in {'13:00', '14:00', '15:00'}:
                item['versus_0940'] = compare(current, summaries['09:40'])
                item['new_since_0940_clusters'] = [s for s in item['versus_0940'].get('sectors', []) if s['new_cluster']]
                item['afternoon_new_clusters_available'] = item['versus_previous']['available']
                item['afternoon_new_clusters'] = [s for s in item['versus_previous'].get('sectors', []) if s['new_cluster']]
                item['baseline_assessment'] = [
                    {'sector_id': s['sector_id'], 'sector_name': s['sector_name'],
                     'label': '오전 대비 포함수 유지/증가' if s['current_count'] >= s['previous_count'] else '오전 대비 포함수 감소',
                     'retained_count': s['retained_count'], 'retention_percent': s['retention_percent'],
                     'composition_changed': bool(s['entered'] or s['exited']),
                     'previous_count': s['previous_count'], 'current_count': s['current_count']}
                    for s in item['versus_0940'].get('sectors', []) if s['previous_count'] >= 2]
            if slot in {'09:40', '15:00'}:
                item['review_candidates'] = [group for group in item['sectors'] if group['cluster_candidate']]
                review = {stock['stock_code']: stock for group in item['review_candidates'] for stock in group['stocks']}
                item['review_stocks'] = sorted(review.values(), key=lambda stock: stock['rank'])
                item['review_purpose'] = '오전 1차 관심 군집 후보' if slot == '09:40' else '종가매매 검토용 관심 군집·종목 목록 (매수 신호 아님)'
        return {
            'date': day, 'environment': environment, 'is_mock': environment == 'mock', 'qry_tp': qry_tp,
            'schedule': list(SLOTS), 'slots': slots,
            'limitations': [
                '현재 수동 태그를 모든 시점에 적용; 과거 태그 재현 아님.',
                '고정 일정 회차만 사용; 기존 간격 수집이나 테스트 수집을 예정 회차로 추정하지 않음.',
                '이전 시점은 바로 직전 예정 시각; 누락 시 더 오래된 회차로 대체하지 않음.',
                '순위 개선은 이전 순위-현재 순위; 집중도는 top5/top10 포함 수와 평균 순위.',
                '가격 상승률·거래대금 확인 및 종가매매 적합성 판단 미구현/데이터 부족.',
                '군집 2종목 기준은 관심 동시 등장 관찰이며 주도섹터·매수 신호 확정이 아님.',
            ],
        }
