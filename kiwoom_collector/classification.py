"""Transparent attention-only state labels; no price/volume or trading model."""


def classify(current, baseline, previous_comparison):
    if current is None or baseline is None:
        return {'available': False, 'label': '판단 불가', 'reason': '현재 또는 09:40 성공 관측 없음'}
    now = {s['sector_id']: s for s in current}
    morning = {s['sector_id']: s for s in baseline}
    transitions = {s['sector_id']: s for s in previous_comparison.get('sectors', [])}
    results = []
    for key in sorted(set(now) | set(morning)):
        new, old = now.get(key), morning.get(key)
        count, original = (new['stock_count'] if new else 0), (old['stock_count'] if old else 0)
        if original >= 2:
            if count == 0:
                state = '소멸'
            elif count < 2:
                state = '약화'
            else:
                changes = [count-original, new['top5_count']-old['top5_count'],
                           new['top10_count']-old['top10_count'], old['mean_rank']-new['mean_rank']]
                if all(value >= 0 for value in changes):
                    state = '오전 대비 강화' if any(value > 0 for value in changes) else '오전 강세 유지(관심도 기준)'
                elif all(value <= 0 for value in changes):
                    state = '약화'
                else:
                    state = '혼조'
        elif count >= 2:
            transition = transitions.get(key)
            state = '오후 신규 부상(직전 대비)' if transition and transition['new_cluster'] else '오전 대비 신규 군집(발생 시점 미확인)'
        else:
            state = '군집 미형성'
        current_codes = {s['stock_code'] for s in new['stocks']} if new else set()
        original_codes = {s['stock_code'] for s in old['stocks']} if old else set()
        results.append({'sector_id': key, 'sector_name': (new or old)['sector_name'], 'label': state,
                        'morning_count': original, 'current_count': count,
                        'composition_changed': current_codes != original_codes,
                        'retained_count': len(current_codes & original_codes),
                        'price_change_verified': False, 'trading_value_verified': False})
    weakening = [s['sector_name'] for s in results if s['label'] in {'약화', '소멸'}]
    rising = [s['sector_name'] for s in results if s['label'] in {'오전 대비 강화', '오후 신규 부상(직전 대비)', '오전 대비 신규 군집(발생 시점 미확인)'}]
    return {'available': True, 'sectors': results,
            'rotation_candidate': bool(weakening and rising), 'weakening_sectors': weakening,
            'rising_sectors': rising, 'rotation_note': '동시에 약화/소멸 군집과 강화/신규 군집이 보이면 관심군집 교체 후보; 인과관계나 주도섹터 교체 확정 아님'}
