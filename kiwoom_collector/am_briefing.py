"""Step 7-C6: deterministic, fixture-only AM sector review. No network or DB."""
import argparse
from decimal import Decimal
from html import escape
import hashlib
import json
from pathlib import Path

from . import am_momentum as am
from . import telegram_briefing as tg

SCHEMA = 'am-sector-briefing-v1'
EOK = Decimal('100000000')


def latest(document, code, kind, cutoff):
    """Use only verified, same-day, same-scope evidence known at cutoff.

    Market metrics must be observed in the cutoff minute. A closing turnover
    value can never substitute for morning cumulative turnover.
    """
    values = []
    for item in document.get('securities', {}).get(code, {}).get(kind, []):
        observed, received = am.timestamp(item.get('observed_at')), am.timestamp(item.get('received_at'))
        if not (observed and received and observed.date() == cutoff.date()
                and observed <= received <= cutoff
                and item.get('environment') == document['environment']
                and item.get('market') == document['market']
                and item.get('source_kind') == 'fixture' and item.get('verified') is True):
            continue
        if observed.strftime('%H:%M') != cutoff.strftime('%H:%M'):
            continue
        if kind == 'names':
            value = am.label(item.get('value'))
        else:
            value = am.number(item.get('value'))
            if value is None or item.get('unit') != ('percent' if kind == 'change_pct' else 'KRW'):
                continue
            if kind != 'change_pct' and value < 0:
                continue
            if kind == 'turnover' and item.get('period') != 'intraday_cumulative':
                continue
            value = str(value)
        am.label(item.get('basis'))
        values.append(dict(value=value, observed_at=observed.isoformat(), received_at=received.isoformat(),
                           basis=item['basis'], verified=True))
    values.sort(key=lambda v: (v['observed_at'], v['received_at']))
    if len({v['observed_at'] for v in values}) != len(values):
        raise ValueError('ambiguous_briefing_metric')
    return values[-1] if values else None


def analyze(document, *, generated_at=None):
    base = am.analyze(document, generated_at=generated_at)
    tg.validate(base)
    cutoff = am.instant(base['selection_cutoff_at'])
    full = base['completeness']['top20_complete'] and base['completeness']['accepted_slots'] == list(am.SLOTS)
    candidates, groups = [], {}
    for row in base['rows']:
        code = row['stock_code']
        metrics = {kind: latest(document, code, kind, cutoff)
                   for kind in ('names', 'change_pct', 'turnover', 'market_cap')}
        value = lambda kind: Decimal(metrics[kind]['value']) if metrics[kind] else None
        cap, turnover, change = value('market_cap'), value('turnover'), value('change_pct')
        sectors = [t['name'] for t in row['sectors']]
        missing = []
        if not full: missing.append('incomplete_observations')
        if len(sectors) != 1: missing.append('sector_missing_or_ambiguous')
        if cap is None: missing.append('market_cap_unverified')
        elif cap <= 3000 * EOK: missing.append('market_cap_not_above_3000_eok')
        if turnover is None: missing.append('intraday_turnover_unverified')
        elif turnover < 500 * EOK: missing.append('turnover_below_500_eok')
        if change is None: missing.append('change_pct_unverified')
        elif change <= 0: missing.append('positive_change_unverified')
        current = row['rank_history'][-1]['rank'] if row['rank_history'] else None
        if row['top5_appearances'] < 2 or current is None: missing.append('repeated_attention_insufficient')
        status = 'held' if missing else 'priority' if turnover >= 1000 * EOK else 'preliminary'
        candidate = dict(stock_code=code, name=metrics['names']['value'] if metrics['names'] else None,
                         sector=sectors[0] if len(sectors) == 1 else None,
                         status=status, missing_evidence=missing, metrics=metrics,
                         top5_appearances=row['top5_appearances'], top20_appearances=row['top20_appearances'],
                         latest_rank=current, rank_change=row['rank_change'])
        candidates.append(candidate)
        if candidate['sector']:
            groups.setdefault(candidate['sector'], []).append(candidate)

    def stock_order(c):
        return (c['status'] != 'priority', -c['top5_appearances'], -c['top20_appearances'],
                c['latest_rank'] or 21, -(c['rank_change'] or 0),
                -Decimal(c['metrics']['change_pct']['value']), -Decimal(c['metrics']['turnover']['value']), c['stock_code'])

    sectors, held_sectors = [], []
    for name, members in groups.items():
        eligible = sorted((c for c in members if c['status'] != 'held'), key=stock_order)
        # Distinct stocks, not duplicated tags, form the co-strength evidence.
        if len(eligible) < 2:
            held_sectors.append(dict(name=name, reason='fewer_than_two_verified_companions'))
            continue
        evidence = dict(distinct_stocks=len(eligible), top20_appearances=sum(c['top20_appearances'] for c in eligible),
                        top5_appearances=sum(c['top5_appearances'] for c in eligible),
                        rising_stocks=len(eligible),
                        cumulative_turnover_won=str(sum((Decimal(c['metrics']['turnover']['value']) for c in eligible), Decimal(0))),
                        mean_change_pct=str(sum((Decimal(c['metrics']['change_pct']['value']) for c in eligible), Decimal(0))/len(eligible)))
        sectors.append(dict(name=name, status='priority' if sum(c['status'] == 'priority' for c in eligible) >= 2 else 'preliminary',
                            evidence=evidence, leaders=eligible[:3]))
    sectors.sort(key=lambda s: (s['status'] != 'priority', -s['evidence']['top20_appearances'],
                                -s['evidence']['top5_appearances'], -s['evidence']['rising_stocks'],
                                -Decimal(s['evidence']['mean_change_pct']),
                                -Decimal(s['evidence']['cumulative_turnover_won']), s['name']))
    for index, sector in enumerate(sectors[:3], 1):
        sector['rank'] = index
    result = dict(schema_version=SCHEMA, selection_type='AM_MOMENTUM', source_kind='fixture', environment=base['environment'], market=base['market'],
                  trade_date=base['trade_date'], selection_cutoff_at=base['selection_cutoff_at'],
                  candidate_confirmed=False, d0_storage_allowed=False, completeness=base['completeness'],
                  policy=dict(version='7-C6-v1', market_cap_exclusive_min_won=str(3000*EOK),
                              priority_turnover_min_won=str(1000*EOK), preliminary_turnover_min_won=str(500*EOK),
                              turnover_period='intraday_cumulative', closing_turnover_requirement_applied=False,
                              minimum_top5_hits=2, minimum_sector_companions=2, weighted_score=False,
                              ambiguous_sector='hold', stale_metrics='hold', validated_optimal_strategy=False),
                  sectors=sectors[:3], held_sectors=held_sectors, candidates=candidates, analysis=base,
                  input_fixture=json.loads(am.canonical(document)))
    result['evaluation_reference'] = hashlib.sha256(am.canonical(result).encode()).hexdigest()
    return result


def validate(report):
    if report.get('schema_version') != SCHEMA or report.get('source_kind') != 'fixture':
        raise ValueError('unsupported_briefing')
    unsigned = {k: v for k, v in report.items() if k != 'evaluation_reference'}
    if hashlib.sha256(am.canonical(unsigned).encode()).hexdigest() != report['evaluation_reference']:
        raise ValueError('briefing_fingerprint_mismatch')
    am.privacy(report)
    tg.validate(report['analysis'])
    # A checksum alone does not establish consistency: reproduce all decisions
    # from the retained point-in-time input, including excluded candidates.
    expected = analyze(report['input_fixture'], generated_at=report['analysis']['generated_at'])
    if report != expected:
        raise ValueError('briefing_analysis_mismatch')


def format_telegram(report):
    """Independent formatter: consume the same immutable analysis as HTML/JSON."""
    validate(report)
    cutoff = am.instant(report['selection_cutoff_at'])
    clock = f'{cutoff.hour}시 {cutoff.minute}분' + (f' {cutoff.second}초' if cutoff.second else '')
    environment = '모의 환경' if report['environment'] == 'mock' else '실전 환경 표기'
    lines = [f'아르비스 오전 브리핑. {clock} 기준.',
             f'합성 테스트 데이터. {environment}. 관측 기반 관심 후보입니다.']
    if report['completeness']['missing_slots']:
        lines.append('관측 누락: ' + ', '.join(s.replace(':', '시 ')+'분' for s in report['completeness']['missing_slots']) + '.')
    if not report['sectors']:
        lines.append('근거 부족으로 섹터와 주도주 순위를 보류합니다.')
    for sector in report['sectors']:
        qualifier = ' 예비 순위' if sector['status'] == 'preliminary' else ''
        lines += ['', f"{sector['rank']}위 {sector['name']}.{qualifier + '.' if qualifier else ''}"]
        for index, candidate in enumerate(sector['leaders'], 1):
            change = candidate['metrics']['change_pct']
            turnover = candidate['metrics']['turnover']
            change_text = f"상승 {Decimal(change['value']):g}%" if change else '등락률 미확인'
            # Strip decimal zeroes only; integer trailing zeroes carry value.
            if turnover:
                turnover_text = format(Decimal(turnover['value'])/EOK, ',f')
                if '.' in turnover_text:
                    turnover_text = turnover_text.rstrip('0').rstrip('.')
                turnover_text += '억'
            else:
                turnover_text = '미확인'
            name = candidate['name'] or ('종목명 미확인 ' + candidate['stock_code'])
            preliminary = ' 예비 후보.' if candidate['status'] == 'preliminary' else ''
            lines.append(f'{index}등 {name}, {change_text}, 누적 거래대금 {turnover_text}.{preliminary}')
        if len(sector['leaders']) < 3:
            lines.append('추가 후보는 근거 부족으로 보류합니다.')
    if 0 < len(report['sectors']) < 3:
        lines.append('나머지 섹터 순위는 보류합니다.')
    if any(c['status'] == 'held' for c in report['candidates']):
        lines.append('검증 자료 미확인 또는 기준 미달 후보는 제외했습니다.')
    lines += ['', '브리핑 종료.']
    text = '\n'.join(lines)
    if len(text.encode('utf-16-le'))//2 > 4096:
        raise ValueError('message_too_long')
    return text


def render(report):
    text = format_telegram(report)
    payload = am.canonical(report).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>AM 주도섹터 검토</title>'
            '<style>body{font:16px system-ui;max-width:1000px;margin:32px auto;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            '<h1>AM 주도섹터 및 관심 후보</h1><pre id="telegram-text">'+escape(text)+'</pre>'
            '<details><summary>상세 선정 근거와 보류 사유</summary><pre>'+escape(json.dumps(report, ensure_ascii=False, indent=2))+
            '</pre></details><script type="application/json" id="analysis-data">'+payload+'</script></html>')


def write_reports(report, name):
    # Follow the existing writer's exclusive creation and rollback policy.
    import re
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
        raise ValueError('invalid_report_name')
    root = am.REPORTS.resolve()
    if root != am.PROJECT.resolve()/'reports':
        raise ValueError('reports_directory_symlink_forbidden')
    contents = {'.json': json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
                '.html': render(report), '.txt': format_telegram(report)}
    root.mkdir(exist_ok=True)
    paths = [root/(name+suffix) for suffix in contents]
    if any(path.exists() or path.is_symlink() for path in paths):
        raise ValueError('existing_report_preserved')
    created = []
    try:
        for path, content in zip(paths, contents.values()):
            with path.open('x', encoding='utf-8', newline='\n') as stream:
                created.append(path)
                stream.write(content)
    except BaseException:
        for path in created:
            path.unlink()
        raise
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description='Synthetic AM briefing dry-run only; no send or API mode')
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--name', required=True)
    args = parser.parse_args(argv)
    try:
        report = analyze(json.loads(args.fixture.read_text(encoding='utf-8')))
        write_reports(report, args.name)
        print(json.dumps(dict(status='dry_run', message=format_telegram(report)), ensure_ascii=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError, ArithmeticError):
        print(json.dumps(dict(status='rejected', reason='fixture_or_output_invalid')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
