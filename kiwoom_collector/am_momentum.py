"""Fixture-only AM review and portable reports. No DB or API execution."""
import argparse
from datetime import date, datetime
from decimal import Decimal
import hashlib
from html import escape
import json
import re
from pathlib import Path

from .bubble_core import unique_stocks, complete
from .cross_analysis import timestamp, number
from .client import KST
from .quotes import CODE
from . import am_time_policy as timing

SCHEMA = 'am-momentum-review-v1'
INPUT_SCHEMA = 'am-momentum-fixture-v1'
ADAPTER_INPUT_SCHEMA = 'am-momentum-readonly-v1'
PROJECT = Path(__file__).resolve().parent.parent
REPORTS = PROJECT / 'reports'
SLOTS = ('09:20', '09:30', '09:40', '09:50')
NOTICE = '관심도·수급 분석 분류이며 매매 확정 신호가 아닙니다. 합성 fixture 보고서입니다.'
ADAPTER_NOTICE = '읽기 전용 수집 자료 검토이며 매매 확정 신호가 아닙니다. 미검증 자료는 보류합니다.'
SENSITIVE = re.compile(r'(?i)secret|token|password|authorization|cookie|app.?key|api.?key|account|credential|계좌|인증정보')


def privacy(value):
    """Reject sensitive fields/values even in metadata that is not exported."""
    if isinstance(value, dict):
        for key, item in value.items():
            if SENSITIVE.search(str(key)):
                raise ValueError('sensitive_input_rejected')
            privacy(item)
    elif isinstance(value, list):
        for item in value:
            privacy(item)
    elif isinstance(value, str):
        if re.search(r'(?i)\bBearer\s|\bgh[pousr]_|[A-Z]:[\\/]|\\\\|/(?:Users|home|tmp|var)/|-----BEGIN|https?://', value):
            raise ValueError('sensitive_or_path_value_rejected')


def label(value):
    if not isinstance(value, str) or not re.fullmatch(r'[\w .()·-]{1,80}', value) or SENSITIVE.search(value):
        raise ValueError('invalid_label')
    return value


def instant(value):
    result = timestamp(value)
    if result is None:
        raise ValueError('timezone_aware_timestamp_required')
    return result


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def analyze(document, *, generated_at=None):
    privacy(document)
    source = document.get('source_kind')
    if (document.get('schema_version'), source) not in ((INPUT_SCHEMA, 'fixture'), (ADAPTER_INPUT_SCHEMA, 'readonly_adapter')):
        raise ValueError('explicit_synthetic_fixture_required')
    day = date.fromisoformat(document['trade_date'])
    env, market = document.get('environment'), document.get('market')
    if env not in ('mock', 'real') or market not in ('KRX', 'NXT', 'SOR') or (env == 'mock' and market != 'KRX'):
        raise ValueError('explicit_environment_and_market_required')
    cutoff = instant(document.get('selection_cutoff_at', day.isoformat()+'T09:50:00+09:00'))
    window = timing.from_document(document)
    deadline = window.deadline(cutoff) if window else cutoff
    if cutoff.date() != day or not '09:20' <= cutoff.strftime('%H:%M') <= '09:50':
        raise ValueError('invalid_am_cutoff')
    generated = instant(generated_at or datetime.now(KST).isoformat())
    if generated < deadline:
        raise ValueError('generation_before_cutoff')
    expected = [s for s in SLOTS if instant(day.isoformat()+'T'+s+':00+09:00') <= cutoff]
    diagnostics, accepted, seen = [], [], set()

    def scope(item):
        if item.get('environment') != env or item.get('market') != market or item.get('source_kind') != source:
            raise ValueError('mixed_environment_market_or_source')

    def usable(item):
        scope(item)
        observed, received = timestamp(item.get('observed_at')), timestamp(item.get('received_at'))
        return timing.available(window,observed,received,cutoff)

    for index, snapshot in enumerate(document.get('rankings', [])):
        scope(snapshot)
        slot = snapshot.get('slot')
        if not usable(snapshot):
            diagnostics.append(dict(input_index=index, reason='ranking_time_missing_or_outside_cutoff'))
            continue
        observed = instant(snapshot['observed_at'])
        anchor = instant(day.isoformat()+'T'+slot+':00+09:00') if slot in expected else None
        valid_slot = (timing.near(window, observed, snapshot['received_at'], anchor) if window and anchor
                      else bool(anchor and observed >= anchor and observed.strftime('%H:%M') == slot))
        if not valid_slot:
            diagnostics.append(dict(input_index=index, reason='invalid_or_late_slot'))
            continue
        if snapshot.get('api_id') != 'ka00198' or snapshot.get('qry_tp') != '1' or snapshot.get('status') != 'success':
            diagnostics.append(dict(input_index=index, reason='invalid_ranking_contract'))
            continue
        if slot in seen:
            raise ValueError('duplicate_slot')
        raw = snapshot.get('stocks', [])
        stocks = unique_stocks({'stocks': raw})
        if len(stocks) != len(raw) or len({s['rank'] for s in stocks.values()}) != len(stocks):
            raise ValueError('ambiguous_ranking_rows')
        seen.add(slot)
        accepted.append(dict(slot=slot, observed_at=observed.isoformat(),
                             received_at=instant(snapshot['received_at']).isoformat(), stocks=stocks))
    accepted.sort(key=lambda s: s['slot'])
    all_codes = sorted(set().union(*(set(s['stocks']) for s in accepted)))
    product_filter = document.get('candidate_filter')
    if product_filter and (product_filter.get('version') != 'c12-am-candidate-join-v1'
                           or instant(product_filter['at']) != cutoff):
        raise ValueError('candidate_filter_scope_mismatch')
    def product_allowed(code):
        return product_filter is None or product_filter.get('decisions', {}).get(code, {}).get('state') == 'analysis_candidate'
    full = len(accepted) == len(expected) and all(complete(s['stocks'], 20) for s in accepted)
    securities = document.get('securities', {})
    for code in securities:
        if not CODE.fullmatch(code):
            raise ValueError('invalid_security_code')
    # Metadata and evidence are point-in-time observations, not current tags applied retrospectively.
    sectors, evidence = {}, {}
    for code in all_codes:
        tags = {}
        for tag in securities.get(code, {}).get('sectors', []):
            if usable(tag):
                name, basis = label(tag['name']), label(tag['basis'])
                previous = tags.get(name)
                if previous is None or instant(tag['received_at']) > instant(previous['received_at']):
                    tags[name] = dict(name=name, basis=basis, observed_at=instant(tag['observed_at']).isoformat(),
                                      received_at=instant(tag['received_at']).isoformat())
        sectors[code] = sorted(tags.values(), key=lambda t: t['name']) if product_allowed(code) else []
        metrics = {}
        for kind, unit in [('price', 'KRW'), ('turnover', 'KRW'), ('market_cap', 'KRW'), ('liquidity', 'KRW')]:
            values = []
            for item in securities.get(code, {}).get(kind, []):
                if not usable(item):
                    continue
                value = number(item.get('value'))
                if value is None or value < 0 or (kind in ('price', 'market_cap') and value == 0):
                    continue
                if item.get('unit') != unit or item.get('verified') is not True:
                    continue
                basis = label(item['basis'])
                values.append(dict(value=str(value), unit=unit, verified=True, basis=basis,
                                   observed_at=instant(item['observed_at']).isoformat(),
                                   received_at=instant(item['received_at']).isoformat()))
            values.sort(key=lambda v: (instant(v['observed_at']), instant(v['received_at'])))
            # Revisions at one observation instant are ambiguous; do not invent a trend.
            if len({v['observed_at'] for v in values}) != len(values):
                raise ValueError('duplicate_metric_observation')
            delta = str(Decimal(values[-1]['value'])-Decimal(values[0]['value'])) if len(values) >= 2 else None
            metrics[kind] = dict(available=bool(values), observations=values, change=delta,
                                 change_per_second=(str(Decimal(delta)/Decimal(str((instant(values[-1]['observed_at'])-instant(values[0]['observed_at'])).total_seconds())))
                                                    if delta is not None else None))
        evidence[code] = metrics
    rows = []
    for code in all_codes:
        history = [dict(slot=s['slot'], observed_at=s['observed_at'], received_at=s['received_at'],
                        rank=s['stocks'].get(code, {}).get('rank'), top20_complete=complete(s['stocks'], 20)) for s in accepted]
        ranks = [h['rank'] for h in history if h['rank'] is not None]
        hits = sum(r <= 5 for r in ranks)
        companions = []
        for tag in sectors[code]:
            peers = sorted({other for other in all_codes if other != code and
                            tag['name'] in {t['name'] for t in sectors[other]} and
                            any(code in s['stocks'] and other in s['stocks'] and
                                instant(tag['received_at']) <= instant(s['received_at']) and
                                any(t['name'] == tag['name'] and instant(t['received_at']) <= instant(s['received_at']) for t in sectors[other])
                                for s in accepted)})
            companions.append(dict(sector=tag['name'], stock_codes=peers, count=len(peers)))
        m = evidence[code]
        missing = []
        if not product_allowed(code): missing.append('product_candidate_filter_blocked')
        if not full: missing.append('incomplete_ranking_window')
        if not sectors[code]: missing.append('sector_classification_unavailable')
        if not any(c['count'] for c in companions): missing.append('sector_companion_unavailable')
        for kind in m:
            if not m[kind]['available']: missing.append(kind+'_missing_or_unverified')
        for kind in ('price', 'turnover'):
            if m[kind]['change'] is None: missing.append(kind+'_change_unavailable')
        cap_ok = bool(m['market_cap']['available'] and Decimal(m['market_cap']['observations'][-1]['value']) >= Decimal('300000000000'))
        liquid_ok = bool(m['liquidity']['available'] and Decimal(m['liquidity']['observations'][-1]['value']) > 0)
        if not cap_ok: missing.append('market_cap_3000_eok_not_verified')
        if not liquid_ok: missing.append('intraday_liquidity_not_verified')
        attention = product_allowed(code) and hits >= 2 and len(ranks) >= 2 and history[-1]['rank'] is not None and history[-1]['rank'] <= 5
        increasing = all(m[k]['change'] is not None and Decimal(m[k]['change']) > 0 for k in ('price', 'turnover'))
        momentum = attention and full and increasing and cap_ok and liquid_ok and any(c['count'] for c in companions)
        status = 'momentum_candidate' if momentum else 'attention_leader' if attention else 'insufficient_evidence'
        reasons = ['top5_repeated'] if hits >= 2 else []
        if len(ranks) == len(expected) and full: reasons.append('top20_persistent')
        if increasing: reasons.append('observed_price_and_turnover_increase')
        if any(c['count'] for c in companions): reasons.append('same_sector_coappearance')
        if not attention: reasons.append('attention_threshold_not_met')
        rows.append(dict(stock_code=code, market=market, environment=env, selection_type='AM_MOMENTUM',
                         candidate_status=status, candidate_confirmed=False, rank_history=history,
                         top5_appearances=hits, top20_appearances=len(ranks),
                         top20_observed_ratio=str(Decimal(len(ranks))/Decimal(len(accepted))),
                         rank_change=ranks[0]-ranks[-1] if len(ranks)>=2 else None,
                         sectors=sectors[code], sector_companions=companions, evidence=m,
                         reasons=reasons, missing_evidence=sorted(set(missing)),
                         d0_storage_allowed=False))
    priority = {'momentum_candidate': 0, 'attention_leader': 1, 'insufficient_evidence': 2}
    rows.sort(key=lambda r: (priority[r['candidate_status']], -r['top5_appearances'], -r['top20_appearances'], r['stock_code']))
    result = dict(schema_version=SCHEMA, selection_type='AM_MOMENTUM', source_kind=source,
                  trade_date=day.isoformat(), selection_cutoff_at=cutoff.isoformat(), generated_at=generated.isoformat(),
                  environment=env, market=market, notice=NOTICE if source == 'fixture' else ADAPTER_NOTICE, candidate_confirmed=False, d0_storage_allowed=False,
                  policy=dict(version='exploratory-v1', minimum_top5_hits=2, latest_observed_rank_max=5,
                              market_cap_min_won='300000000000', closing_turnover_requirement_applied=False,
                              liquidity='positive_intraday_value_with_explicit_verified_basis',
                              validated_optimal_strategy=False),
                  completeness=dict(expected_slots=expected, accepted_slots=[s['slot'] for s in accepted],
                                    missing_slots=[s for s in expected if s not in seen], top20_complete=full),
                  diagnostics=diagnostics, rows=rows)
    if window:
        result.update(observation_policy=window.document(),decision_deadline_at=deadline.isoformat())
    result['evaluation_reference'] = hashlib.sha256(canonical(result).encode('utf-8')).hexdigest()
    return result


def render(report):
    privacy(report)
    payload = canonical(report).replace('<', '\u003c').replace('>', '\u003e').replace('&', '\u0026')
    def display(value):
        return '미확인' if value is None else format(Decimal(value), ',f')
    counts = {status: sum(r['candidate_status'] == status for r in report['rows']) for status in ('momentum_candidate', 'attention_leader', 'insufficient_evidence')}
    summary = ' · '.join(status+': '+str(count) for status, count in counts.items())
    body = ''.join('<tr><td>'+escape(r['stock_code'])+'</td><td>'+escape(r['candidate_status'])+
                   '</td><td>'+str(r['top5_appearances'])+'</td><td>'+str(r['top20_appearances'])+
                   '</td><td>'+escape(display(r['rank_change']))+'</td><td>'+escape(', '.join(t['name'] for t in r['sectors']))+
                   '</td><td>'+escape(display(r['evidence']['price']['change']))+'</td><td>'+escape(display(r['evidence']['turnover']['change']))+'</td><td>'+'<details><summary>'+str(len(r['missing_evidence']))+'개 항목</summary>'+escape(', '.join(r['missing_evidence']))+'</details></td></tr>' for r in report['rows'])
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>AM Momentum Review</title><style>body{font:15px system-ui;background:#101826;color:#e8eef7;margin:32px}'
            'table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #36445a;text-align:left}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin-top:24px}td details{margin:0}th{white-space:nowrap}td:last-child{min-width:120px;max-width:350px;overflow-wrap:anywhere}.table-wrap{overflow:auto}summary{cursor:pointer}p{line-height:1.7}</style>'
            '<h1>AM_MOMENTUM 오전 관심도 검토</h1><p>'+escape(report['notice'])+'</p><p>'+escape(report['trade_date'])+
            ' · '+escape(report['environment'])+' · 기준 '+escape(report['selection_cutoff_at'])+'</p><p>수집 완전성: '+
            escape(canonical(report['completeness']))+'</p><p>'+escape(summary)+'</p><div class="table-wrap"><table><thead><tr><th>종목</th><th>분류</th><th>TOP5</th>'
            '<th>TOP20</th><th>순위 개선</th><th>섹터</th><th>가격 변화 KRW</th><th>거래대금 변화 KRW</th><th>부족 근거</th></tr></thead><tbody>'+body+
            '</tbody></table></div><details><summary>전체 분석 근거 · 순위 이력 · 수치 · 관측 시각</summary><pre>'+escape(canonical(report))+
            '</pre></details><script type="application/json" id="analysis-data">'+payload+'</script></html>')


def write_reports(report, name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
        raise ValueError('invalid_report_name')
    privacy(report)
    root = REPORTS.resolve()
    if root != PROJECT.resolve()/'reports':
        raise ValueError('reports_directory_symlink_forbidden')
    root.mkdir(exist_ok=True)
    paths = [root/(name+'.json'), root/(name+'.html')]
    if any(p.exists() or p.is_symlink() for p in paths):
        raise ValueError('existing_report_preserved')
    # Exclusive creation prevents replacement. Roll back only files created by this call.
    created = []
    try:
        for path, content in zip(paths, (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), render(report))):
            with path.open('x', encoding='utf-8') as stream:
                created.append(path)
                stream.write(content)
    except BaseException:
        for path in created:
            path.unlink()
        raise
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline synthetic AM review; no DB or API access')
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--name', required=True, help='New basename inside development reports directory')
    args = parser.parse_args(argv)
    try:
        if args.fixture.suffix != '.json':
            raise ValueError('json_fixture_required')
        report = analyze(json.loads(args.fixture.read_text(encoding='utf-8')))
        write_reports(report, args.name)
    except (ValueError, KeyError, TypeError, OSError):
        print('AM review rejected: check fixture contract and new report name.')
        return 1
    print(json.dumps(dict(schema_version=SCHEMA, report_name=args.name, candidate_confirmed=False)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
