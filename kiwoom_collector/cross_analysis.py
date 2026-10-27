"""Pure 0198 x ka10032 evidence comparison. No DB, credentials or network access."""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
import re

KST = timezone(timedelta(hours=9))
CODE = re.compile(r'[0-9]{6}', re.ASCII)
NUMBER = re.compile(r'[+-]?[0-9]+(?:\.[0-9]+)?', re.ASCII)
TYPES = {'common_stock', 'preferred_stock', 'ETF', 'ETN', 'SPAC', 'REIT', 'other', 'unknown'}


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(KST) if result.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def number(value):
    if not isinstance(value, str) or len(value) > 40 or not NUMBER.fullmatch(value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def rank(value, maximum=None):
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,10}', value):
        value = int(value)
    return value if type(value) is int and value > 0 and (maximum is None or value <= maximum) else None


def indexed(rows, rank_field, maximum=None):
    if not isinstance(rows, list):
        raise ValueError('rows must be a list')
    codes = Counter(r.get('stk_cd') for r in rows if isinstance(r, dict) and isinstance(r.get('stk_cd'), str))
    ranks = Counter(rank(r.get(rank_field), maximum) for r in rows if isinstance(r, dict))
    duplicates = sorted(c for c, n in codes.items() if n > 1)
    invalid, result = [], {}
    for i, row in enumerate(rows):
        code = row.get('stk_cd') if isinstance(row, dict) else None
        position = rank(row.get(rank_field), maximum) if isinstance(row, dict) else None
        reasons = []
        if not isinstance(code, str) or CODE.fullmatch(code) is None:
            reasons.append('invalid_code')
        elif codes[code] > 1:
            reasons.append('duplicate_code')
        if position is None:
            reasons.append('invalid_rank')
        elif ranks[position] > 1:
            reasons.append('duplicate_rank')
        if reasons:
            invalid.append(dict(row_index=i, stock_code=code if isinstance(code, (str, int)) else None, reasons=reasons))
        else:
            result[code] = row
    return result, dict(duplicate_codes=duplicates, invalid_rows=invalid, input_rows=len(rows), usable_rows=len(result))


def analyze(document):
    """Input is an explicit snapshot export, never a DB connection/path.

    Verification records are supplied external evidence, bound to this pair.
    Even fully verified synthetic evidence never confirms a trading candidate.
    """
    a, b = document['ranking'], document['turnover']
    if a.get('api_id') != 'ka00198' or b.get('api_id') != 'ka10032':
        raise ValueError('Expected ka00198 and ka10032 snapshots')
    for source in (a, b):
        if source.get('environment') not in ('mock', 'real') or type(source.get('test_mode')) is not bool:
            raise ValueError('Explicit environment and boolean test_mode required')
        if not isinstance(source.get('id'), str) or not source['id']:
            raise ValueError('Explicit snapshot ID required')
    if a['environment'] != b['environment']:
        raise ValueError('Mixed mock/real comparison forbidden')
    if a['test_mode'] != b['test_mode']:
        raise ValueError('Mixed test/operational snapshots forbidden')
    aa, ad = indexed(a['rows'], 'rank', 20)
    bb, bd = indexed(b['rows'], 'now_rank')
    at, bt = timestamp(a.get('collected_at')), timestamp(b.get('collected_at'))
    delta = (bt-at).total_seconds() if at and bt else None
    market_keys = ('mrkt_tp', 'mang_stk_incls', 'stex_tp')
    markets = [s.get('market') for s in (a, b)]
    market_ok = all(isinstance(m, dict) and all(isinstance(m.get(k), str) for k in market_keys) for m in markets)
    market_ok = market_ok and markets[0] == markets[1] and markets[0]['mrkt_tp'] in ('000', '001', '101') and markets[0]['mang_stk_incls'] in ('0', '1') and markets[0]['stex_tp'] in ('1', '2', '3')
    if a['environment'] == 'mock' and market_ok and markets[0]['stex_tp'] != '1':
        market_ok = False
    v = document.get('verification', {})
    bound = (v.get('ranking_id') == a['id'] and v.get('turnover_id') == b['id'] and v.get('environment') == a['environment'])
    def verified(key):
        item = v.get(key, {})
        return bool(bound and isinstance(item, dict) and item.get('verified') is True and isinstance(item.get('evidence'), str) and item['evidence'].strip())
    time_ok = bool(verified('time') and at and bt and at.date() == bt.date() and v['time'].get('ranking_collected_at') == a.get('collected_at') and v['time'].get('turnover_collected_at') == b.get('collected_at'))
    unit_ok = verified('unit') and v['unit'].get('unit') == 'million_KRW'
    coverage_ok = bool(verified('coverage') and v['coverage'].get('scope'))
    quotes_ok = verified('quotes')
    common = []
    if not market_ok: common.append('market_or_conditions_unverified')
    if not time_ok: common.append('time_alignment_unverified')
    if not unit_ok: common.append('turnover_unit_unverified')
    if not coverage_ok: common.append('coverage_unverified')
    if not quotes_ok: common.append('quotes_unverified')
    if a.get('qry_tp') != '1': common.append('ranking_query_not_intraday')
    if any(s.get('status') != 'success' for s in (a, b)): common.append('source_incomplete')
    if ad['invalid_rows'] or bd['invalid_rows']: common.append('source_rows_ambiguous')
    counts = Counter(c for c, row in aa.items() if a.get('status') == 'success' and rank(row['rank'], 20) <= 5)
    accepted, rejected, seen = [], [], {a['id']}
    for h in document.get('history', []):
        reason = None
        ht = timestamp(h.get('collected_at'))
        if h.get('id') in seen or not isinstance(h.get('id'), str): reason = 'duplicate_or_missing_id'
        elif any(h.get(k) != a.get(k) for k in ('environment', 'test_mode', 'market', 'qry_tp')): reason = 'scope_mismatch'
        elif h.get('status') != 'success' or not ht or not at or ht.date() != at.date() or ht >= at: reason = 'invalid_time_or_status'
        else:
            hi, hd = indexed(h.get('rows', []), 'rank', 20)
            if hd['invalid_rows']: reason = 'ambiguous_history_rows'
        if reason:
            rejected.append(dict(id=h.get('id'), reason=reason))
        else:
            seen.add(h['id']); accepted.append(h['id'])
            counts.update(c for c, row in hi.items() if rank(row['rank'], 20) <= 5)
    metadata = document.get('securities', {})
    rows = []
    for code, r in sorted(aa.items(), key=lambda item: rank(item[1]['rank'], 20)):
        t = bb.get(code)
        meta = metadata.get(code, {})
        sectors = sorted(set(s for s in meta.get('sectors', []) if isinstance(s, str) and s.strip()))
        kind = meta.get('security_type', 'unknown')
        if kind not in TYPES: kind = 'unknown'
        reasons = list(common)
        if not sectors: reasons.append('unclassified_sector')
        if kind == 'unknown': reasons.append('security_type_unverified')
        elif kind != 'common_stock': reasons.append('security_type_requires_separate_review')
        if t is None: reasons.append('missing_turnover_match')
        amount = number(t.get('trde_prica')) if t else None
        price = number(t.get('cur_prc')) if t else None
        rate = number(t.get('flu_rt')) if t else None
        volume = number(t.get('now_trde_qty')) if t else None
        if t and (amount is None or amount < 0 or price is None or rate is None or volume is None or volume < 0 or volume != volume.to_integral_value()):
            reasons.append('invalid_market_values')
        with localcontext() as ctx:
            ctx.prec = 100
            won = format(amount * Decimal(1000000), 'f') if amount is not None and amount >= 0 else None
        rows.append(dict(stock_code=code, stock_name=r.get('stk_nm'), turnover_stock_name=t.get('stk_nm') if t else None,
            sectors=sectors, security_type=kind, ranking_0198=rank(r['rank'], 20), top5_appearances=counts[code],
            turnover_rank=rank(t['now_rank']) if t else None, turnover_raw=t.get('trde_prica') if t else None,
            turnover_declared_unit='million_KRW', unit_status='verified' if unit_ok else 'unverified',
            turnover_won_diagnostic=won, coverage_status='verified' if coverage_ok else 'unverified',
            price_raw=t.get('cur_prc') if t else None, change_percent_raw=t.get('flu_rt') if t else None,
            volume_raw=t.get('now_trde_qty') if t else None, time_delta_seconds=delta,
            cross_status='validation_hold' if reasons else 'verified_observation', reasons=reasons,
            category='검증 보류' if reasons else '후보 검토' if rank(r['rank'],20) <= 5 else '관찰',
            candidate_confirmed=False))
    return dict(schema_version=1, environment=a['environment'], test_mode=a['test_mode'],
        sources={k:{f:s.get(f) for f in ('id','collected_at','market','status','qry_tp')} for k,s in (('ranking',a),('turnover',b))},
        time_delta_seconds=delta, rows=rows, diagnostics=dict(ranking=ad, turnover=bd,
            missing_turnover_codes=sorted(set(aa)-set(bb)), turnover_only_codes=sorted(set(bb)-set(aa)),
            unclassified_codes=[r['stock_code'] for r in rows if not r['sectors']],
            history_accepted=accepted, history_rejected=rejected,
            top5_success_observations=len(accepted)+int(a.get('status')=='success'),
            empty_sources=[name for name,s in (('ranking',a),('turnover',b)) if not s['rows']]),
        verification_status=dict(time=time_ok, unit=unit_ok, coverage=coverage_ok, quotes=quotes_ok,
                                 coverage_scope=v.get('coverage', {}).get('scope') if coverage_ok else None),
        verification_evidence=v,
        policy=dict(top20='sector_detection', top5='priority_review_only', scores=False,
                    candidate_confirmed=False, orders=False))


def render_table(report):
    def cell(value):
        if value is None: return '미확인'
        return str(value).replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')
    lines = ['| 코드 | 종목명 | 섹터 | 유형 | 0198 | TOP5 횟수 | 0186 | 거래대금 원문(백만원) | 단위 | 현재가 원문(원) | 등락률(%) | 거래량(주) | 시차(초) | 상태 | 구분 |',
             '|---|---|---|---|---:|---:|---:|---|---|---|---|---|---:|---|---|']
    for r in report['rows']:
        values = [r['stock_code'],r['stock_name'],', '.join(r['sectors']) or '미분류',r['security_type'],r['ranking_0198'],r['top5_appearances'],r['turnover_rank'],r['turnover_raw'],r['unit_status'],r['price_raw'],r['change_percent_raw'],r['volume_raw'],r['time_delta_seconds'],r['cross_status'],r['category']]
        lines.append('| '+' | '.join(cell(v) for v in values)+' |')
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True, type=Path)
    parser.add_argument('--format', choices=('json','markdown'), default='json')
    args = parser.parse_args(argv)
    # Reject database/config paths before opening anything. No DB adapters here.
    if args.fixture.suffix.lower() != '.json': parser.error('JSON snapshot fixture required')
    result = analyze(json.loads(args.fixture.read_text(encoding='utf-8-sig')))
    print(render_table(result) if args.format == 'markdown' else json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
