"""Offline, read-only sector radar; never reconstruct historical prices."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape
import json
from pathlib import Path

from .client import KST
from .quotes import SPEC_VERSION
from .summary import read_database, _rows

MAX_SKEW_SECONDS = 300


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(KST) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def object_json(value):
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def assess(quote, ranked_at):
    result = dict(quote or {})
    result.update(eligible=False, direction=None, candidate=False, warnings=[], delta_seconds=None,
                  api_delta_seconds=None, change_percent=None, turnover_won=None)
    if quote is None:
        result['warnings'] = ['시세 미수집']
        return result
    norm = object_json(quote.get('normalized_json'))
    ranked, observed = timestamp(ranked_at), timestamp(quote.get('request_finished_at'))
    result['observed_at'] = observed.isoformat() if observed else None
    try:
        api_text = str(norm.get('api_date_raw')) + str(norm.get('api_time_raw'))
        if len(api_text) != 14 or not api_text.isascii() or not api_text.isdigit():
            raise ValueError()
        api_at = datetime.strptime(api_text, '%Y%m%d%H%M%S').replace(tzinfo=KST)
    except ValueError:
        api_at = None
    result['api_at'] = api_at.isoformat() if api_at else None
    aligned = ranked is not None and observed is not None and api_at is not None
    if ranked and observed:
        result['delta_seconds'] = (observed-ranked).total_seconds()
    if ranked and api_at:
        result['api_delta_seconds'] = (api_at-ranked).total_seconds()
    if aligned:
        aligned = (ranked.date() == observed.date() == api_at.date()
                   and abs(result['delta_seconds']) <= MAX_SKEW_SECONDS
                   and abs(result['api_delta_seconds']) <= MAX_SKEW_SECONDS)
    if not aligned:
        known_mismatch = (ranked and observed and
                          (ranked.date() != observed.date() or abs(result['delta_seconds']) > MAX_SKEW_SECONDS))
        if ranked and api_at:
            known_mismatch = known_mismatch or ranked.date() != api_at.date() or abs(result['api_delta_seconds']) > MAX_SKEW_SECONDS
        result['warnings'].append('시점 불일치·분석 제외' if known_mismatch else '시각 미검증·분석 제외')
    verified = (norm.get('version') == 1 and quote.get('spec_version') == SPEC_VERSION
                and quote.get('api_id') in ('ka10007', 'ka10095'))
    rate = number(norm.get('change_percent')) if verified else None
    if rate is None:
        result['warnings'].append('필드 미검증: 등락률')
    else:
        result['change_percent'] = str(rate)
    if norm.get('price_status') != 'verified':
        result['warnings'].append('가격 부호 미확정')
    turnover = number(norm.get('turnover_won')) if verified else None
    if norm.get('turnover_source_unit') != 'million_KRW' or turnover is None or turnover < 0:
        result['warnings'].append('거래대금 단위/값 미검증')
    else:
        result['turnover_won'] = str(turnover)
    if quote.get('status') not in ('success', 'partial'):
        result['warnings'].append('시세 수집 실패/미완료')
    result['eligible'] = bool(aligned and rate is not None and quote.get('status') in ('success', 'partial'))
    if result['eligible']:
        result['direction'] = 'up' if rate > 0 else 'down' if rate < 0 else 'flat'
        result['candidate'] = rate > 0
    return result


def radar_report(path, day, environment='mock'):
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('--date는 YYYY-MM-DD 형식이어야 합니다.') from None
    if environment not in ('mock', 'real'):
        raise ValueError('환경은 mock/real이어야 합니다.')
    with read_database(path) as (db, resolved):
        runs = [dict(r) for r in _rows(db, "SELECT * FROM collections WHERE environment=? AND api_id='ka00198' AND status='success' ORDER BY collected_at,rowid", (environment,))
                if timestamp(r['collected_at']) and timestamp(r['collected_at']).date().isoformat() == day]
        tables = {r['name'] for r in _rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}
        tags = _rows(db, 'SELECT t.stock_code,s.sector_name,t.source FROM stock_sectors t JOIN sectors s ON t.sector_id=s.sector_id ORDER BY s.sector_name')
        for run in runs:
            stocks = []
            for row in _rows(db, 'SELECT * FROM ranking_records WHERE collection_id=? ORDER BY rank', (run['id'],)):
                stock = dict(row)
                quotes = []
                if {'quote_batches', 'quote_observations'} <= tables:
                    quotes = [dict(q) for q in _rows(db, '''SELECT o.*,b.spec_version FROM quote_observations o JOIN quote_batches b
                        ON b.id=o.batch_id AND b.collection_id=o.collection_id
                        WHERE o.collection_id=? AND o.rank=? AND o.requested_code=? AND b.environment=? AND b.api_id=o.api_id''',
                        (run['id'], row['rank'], row['stk_cd'], environment))]
                observations = [assess(q, run['collected_at']) for q in quotes]
                # Prefer an aligned usable observation, then latest attempt; retain all evidence.
                observations.sort(key=lambda q: (q['eligible'], q.get('request_finished_at') or '', q.get('api_id') or '', q.get('attempt', 0)), reverse=True)
                stock['quote'] = observations[0] if observations else assess(None, run['collected_at'])
                stock['observations'] = observations
                stock['tags'] = [dict(t) for t in tags if t['stock_code'] == row['stk_cd']]
                stocks.append(stock)
            groups = {}
            for stock in stocks:
                for name in dict.fromkeys(t['sector_name'] for t in stock['tags']) or ['미분류']:
                    groups.setdefault(name, []).append(stock)
            run['stocks'] = stocks
            run['sectors'] = []
            for name, members in groups.items():
                counts = {direction: sum(s['quote']['direction'] == direction for s in members) for direction in ('up', 'down', 'flat')}
                run['sectors'].append(dict(name=name, stocks=members, interest_count=len(members),
                    top5=sum(s['rank'] <= 5 for s in members), top10=sum(s['rank'] <= 10 for s in members),
                    excluded=sum(not s['quote']['eligible'] for s in members), **counts))
        return dict(date=day, environment=environment, db=str(resolved), runs=runs)


def render_radar(report):
    def e(value):
        return escape('—' if value is None else str(value), quote=True)
    parts = ['''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>주도섹터 레이더 v1</title><style>
body{font-family:system-ui,sans-serif;background:#f3f6fb;color:#18263a;margin:0;padding:32px;line-height:1.6}main{max-width:1500px;margin:auto}h1{margin-bottom:0}section{background:white;border:1px solid #d8e0ec;border-radius:12px;padding:22px;margin:24px 0}.popular{color:#255da8}.candidate{color:#00664f;font-weight:bold;background:#def5eb}.warning{color:#8b4513}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;border-bottom:1px solid #dde3ec;padding:10px;vertical-align:top}th{background:#eef3fa}small{display:block;color:#526176}.scroll{overflow:auto}summary{cursor:pointer}code{overflow-wrap:anywhere}@media(max-width:700px){body{padding:12px}section{padding:12px}} </style><main><h1>주도섹터 레이더 <small>v1 · 로컬 읽기 전용 보고서</small></h1>''',
        f"<p>{e(report['date'])} · {e(report['environment'])} {'· 모의 데이터' if report['environment']=='mock' else ''}</p>",
        '<section><b class="popular">파랑: 조회순위상 인기</b> · <b class="candidate">초록: 시세 상승이 확인된 주도주 후보</b><p>후보 = 시점 일치 + 검증된 등락률 &gt; 0. 섹터 강도는 유효 시세의 상승·하락·보합 수로만 표시합니다. 가격 부호와 거래대금 검증 상태는 별개이며 후보 확정·매수 신호를 뜻하지 않습니다.</p><p>시점 일치: 순위 수집과 요청 완료 및 API 시세 시각이 같은 KST 날짜이며 두 시간 차이 모두 ±300초 이내. 사후 조회 시세는 과거 시세가 아닙니다. 누락값은 —, 집계 제외는 별도 표시합니다.</p><p>현재 저장된 수동/검토 태그를 사용합니다(과거 태그 재현 아님). 복수 섹터 소속은 각 섹터에 포함되므로 섹터 합계는 중복될 수 있습니다. 회차별 TOP20만 비교하며 서로 다른 회차를 합산하지 않습니다.</p></section>']
    if not report['runs']:
        parts.append('<section>선택 날짜·환경에 성공한 순위 수집이 없습니다.</section>')
    for run in report['runs']:
        parts.append(f"<section><h2>순위 {e(run['collected_at'])}</h2><small>회차 {e(run['id'])} · qry_tp {e(run['qry_tp'])} · test_mode {e(run['test_mode'])}</small>")
        for group in run['sectors']:
            parts.append(f"<h3 class='popular'>{e(group['name'])} · 관심 {group['interest_count']}종목</h3><p>TOP5 {group['top5']} · TOP10 {group['top10']} | <b>유효 시세 상승 {group['up']} · 하락 {group['down']} · 보합 {group['flat']}</b> · 분석 제외 {group['excluded']}</p><div class='scroll'><table><thead><tr><th>순위 / 종목</th><th>판단</th><th>등락률</th><th>거래대금 (원)</th><th>관측 시각 / 시간 차이</th><th>검증 상태</th></tr></thead><tbody>")
            for stock in group['stocks']:
                q = stock['quote']
                label = '시세 상승이 확인된 주도주 후보' if q['candidate'] else '시점 일치·하락/보합' if q['eligible'] else '조회순위상 인기 · 분석 제외'
                parts.append(f"<tr><td>{stock['rank']} · {e(stock['stk_nm'])}<small>{e(stock['stk_cd'])} · {e(', '.join(t['sector_name']+' ['+t['source']+']' for t in stock['tags']))}</small></td><td class={'candidate' if q['candidate'] else 'popular'}>{label}</td><td>{e(q['change_percent'])}%</td><td>{e(q['turnover_won'])}</td><td>요청 완료 {e(q.get('observed_at'))}<small>API {e(q.get('api_at'))}<br>순위 대비 요청 {e(q['delta_seconds'])}초 / API {e(q['api_delta_seconds'])}초</small></td><td class='warning'>{e(' · '.join(q['warnings']) or '검증 완료')}<small>{e(q.get('api_id'))} / {e(q.get('status'))}</small>")
                parts.append('<details><summary>모든 시세 관측 근거</summary>')
                for observation in stock['observations']:
                    parts.append('<p>'+e(f"{observation.get('api_id')} / attempt={observation.get('attempt',0)} / {observation.get('request_finished_at')} / Δ={observation['delta_seconds']}초 / {', '.join(observation['warnings'])}")+ '<br><code>'+e(observation.get('issues_json'))+'</code></p>')
                parts.append('</details></td></tr>')
            parts.append('</tbody></table></div>')
        parts.append('</section>')
    return ''.join(parts) + '</main></html>'


def export_radar(path, day, environment, output):
    target = Path(output).resolve()
    source = Path(path).resolve()
    if target == source or (target.exists() and source.exists() and target.samefile(source)) or target.suffix.lower() not in ('.html', '.htm'):
        raise ValueError('DB와 다른 .html 출력 경로를 지정하세요.')
    report = radar_report(path, day, environment)
    target.write_text(render_radar(report), encoding='utf-8')
    return dict(output=str(target), date=day, environment=environment, collections=len(report['runs']))
