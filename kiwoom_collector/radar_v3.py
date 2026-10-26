"""Read-only intraday Radar v3. No collection, credentials or schema changes.

Run: python -m kiwoom_collector.radar_v3 --date YYYY-MM-DD --output new.html
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile

from .client import KST
from .radar import radar_report, render_diagnostics, timestamp
from .radar_v2 import (STYLE, e, interest_key, money, rate_label, spread_label,
                       stock_details, summarize, turnover_label)
from .summary import read_database


# Presentation of the existing schedule; never starts or modifies a scheduler.
INTRADAY_SLOTS = ('09:20', '09:30', '09:40', '09:50', '10:00',
                  '11:00', '12:00', '13:00', '14:00', '15:00')


def slot_states(day, rows, as_of):
    states = []
    for slot in INTRADAY_SLOTS:
        at = datetime.fromisoformat(f'{day}T{slot}:00').replace(tzinfo=KST)
        matches = [r for r in rows if timestamp(r.get('scheduled_at')) == at]
        statuses = list(dict.fromkeys(r['status'] for r in matches))
        state = ('success' if 'success' in statuses else '/'.join(statuses)) if matches else (
            'future' if at > as_of else 'missing')
        states.append(dict(slot=slot, at=at.isoformat(), state=state,
                           attempts=len(matches), statuses=statuses))
    return states


def analyze(legacy, attempts=(), *, test_mode=0, as_of=None):
    """Pure analysis of one day, one environment, qry_tp=1 and one mode."""
    as_of = as_of or datetime.now(KST)
    if as_of.tzinfo is None:
        raise ValueError('기준 시각에는 UTC 오프셋이 필요합니다.')
    day, environment = legacy['date'], legacy['environment']

    def in_scope(r):
        at = timestamp(r.get('collected_at'))
        return (at is not None and at.date().isoformat() == day and at <= as_of
                and r.get('environment') == environment and r.get('api_id') == 'ka00198'
                and r.get('qry_tp') == '1' and r.get('test_mode') == test_mode)

    runs = sorted([r for r in legacy['runs'] if in_scope(r) and r['status'] == 'success'],
                  key=lambda r: (timestamp(r['collected_at']), r['sequence']))
    attempts = [r for r in attempts if in_scope(r)]
    slots = slot_states(day, attempts, as_of)
    snapshots = [summarize(r) for r in runs]
    selected_legacy = dict(legacy, runs=runs)
    selected_legacy.pop('overview', None)
    result = dict(date=day, environment=environment, qry_tp='1', test_mode=test_mode,
                  generated_at=as_of.isoformat(), snapshots=snapshots, slots=slots,
                  legacy=selected_legacy, current=None, previous=None,
                  groups=[], increases=[], decreases=[], persistent=[],
                  entrants=None, retained=None, exits=None, repeated_stocks=[],
                  gap_seconds=None, intervening_slots=[], cumulative=None)
    if not snapshots:
        return result
    current = snapshots[-1]
    previous = snapshots[-2] if len(snapshots) > 1 else None
    complete = lambda s: (s['market']['unique_count'] == 20
                          and not s['market']['unidentified_count']
                          and len(s['collection']['stocks']) == 20)
    result.update(current=current, previous=previous,
                  complete=complete(current), comparison_complete=bool(previous and complete(current) and complete(previous)))
    histories = [{g['name']: g for g in s['sectors']} for s in snapshots]
    current_groups = histories[-1]
    old_groups = histories[-2] if previous else {}
    names = set().union(*(h.keys() for h in histories)) - {'미분류'}
    counts = Counter(s['stk_cd'] for snapshot in snapshots for s in snapshot['stocks'])
    top5_counts = Counter(s['stk_cd'] for snapshot in snapshots for s in snapshot['stocks'] if s['rank'] <= 5)
    stock_maps = [{s['stk_cd']: s for s in snapshot['stocks']} for snapshot in snapshots]

    def streak(flags):
        n = 0
        for flag in reversed(flags):
            if not flag:
                break
            n += 1
        return n

    for name in names:
        history = [h.get(name) for h in histories]
        present = current_groups.get(name)
        old = old_groups.get(name)
        # A sector absent in a complete observed snapshot is observed zero.
        now_codes = {s['stk_cd'] for s in present['stocks']} if present else set()
        old_codes = {s['stk_cd'] for s in old['stocks']} if old else set()
        n20, n5 = (present['interest_count'], present['top5']) if present else (0, 0)
        top3 = sum(name in [g['name'] for g in sorted(
            [g for g in snapshot['sectors'] if g['name'] != '미분류'], key=interest_key)[:3]] for snapshot in snapshots)
        group = dict(name=name, current=present, count=n20, top5=n5,
                     delta20=n20 - (old['interest_count'] if old else 0) if previous else None,
                     delta5=n5 - (old['top5'] if old else 0) if previous else None,
                     appearances=sum(bool(g) for g in history), observations=len(snapshots),
                     top5_appearances=sum(bool(g and g['top5']) for g in history),
                     streak=streak([bool(g) for g in history]), top3_appearances=top3,
                     history=[g['interest_count'] if g else 0 for g in history],
                     top5_history=[g['top5'] if g else 0 for g in history],
                     new_codes=sorted(now_codes-old_codes) if previous else None,
                     retained_codes=sorted(now_codes & old_codes) if previous else None,
                     exited_codes=sorted(old_codes-now_codes) if previous else None)
        result['groups'].append(group)
    result['groups'].sort(key=lambda g: (-g['count'], -g['top5'],
                                        -(g['current']['top10'] if g['current'] else 0), g['name']))
    # Values are observed changes even on incomplete samples; conclusions are qualified in UI.
    result['increases'] = sorted([g for g in result['groups'] if g['delta20'] is not None and g['delta20'] > 0],
                                key=lambda g: (-g['delta20'], -g['count'], -g['top5'],
                                               -g['current']['top10'], g['name']))
    result['decreases'] = sorted([g for g in result['groups'] if g['delta20'] is not None and g['delta20'] < 0],
                                key=lambda g: (g['delta20'], -old_groups[g['name']]['interest_count'], g['name']))
    result['persistent'] = sorted([g for g in result['groups'] if g['count']],
                                 key=lambda g: (-g['appearances'], -g['streak'], -g['top3_appearances'],
                                                -g['count'], g['name']))
    current_codes = stock_maps[-1].keys()
    if previous:
        old_codes = stock_maps[-2].keys()
        result.update(entrants=[s for s in current['stocks'] if s['stk_cd'] not in old_codes],
                      retained=[s for s in current['stocks'] if s['stk_cd'] in old_codes],
                      exits=[s for s in previous['stocks'] if s['stk_cd'] not in current_codes],
                      gap_seconds=(timestamp(runs[-1]['collected_at'])-timestamp(runs[-2]['collected_at'])).total_seconds())
        result['intervening_slots'] = [s for s in slots if timestamp(runs[-2]['collected_at']) < timestamp(s['at']) < timestamp(runs[-1]['collected_at'])]
    for code, stock in stock_maps[-1].items():
        result['repeated_stocks'].append(dict(code=code, name=stock['stk_nm'], rank=stock['rank'],
                                              appearances=counts[code], top5_appearances=top5_counts[code],
                                              streak=streak([code in m for m in stock_maps])))
    result['repeated_stocks'].sort(key=lambda s: (-s['appearances'], -s['streak'], s['rank'], s['code']))
    result['overlap'] = [s for s in current['stocks'] if len({t['sector_name'] for t in s['tags']}) > 1]
    return result


def report(path, day, environment='mock', test_mode=0):
    if test_mode not in (0, 1):
        raise ValueError('test_mode는 0 또는 1이어야 합니다.')
    # One live read transaction, including WAL, supplies both ranking and attempt metadata.
    # All subsequent v2 reads run against this private snapshot, never a writable Store.
    with tempfile.TemporaryDirectory(prefix='stock1-radar-v3-') as folder:
        snapshot = Path(folder) / 'snapshot.db'
        with read_database(path) as (source, resolved):
            with closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
        legacy = radar_report(snapshot, day, environment)
        legacy['db'] = str(resolved)
        with read_database(snapshot) as (db, _):
            attempts = [dict(r) for r in db.execute('SELECT rowid AS sequence,* FROM collections')]
        return analyze(legacy, attempts, test_mode=test_mode)


V3_STYLE = '''
.v3 .kpis{grid-template-columns:1.15fr 1.15fr 1.2fr 1.5fr}.v3 .card{padding:10px 12px}
.v3 h1{font-size:22px}.v3 .kpis h2{font-size:14px}.v3 .layout{grid-template-columns:minmax(0,3.2fr) minmax(255px,1fr)}
.v3 .scroll{max-height:none}.v3 .scroll>table{min-width:720px}.v3 th,.v3 td{padding:6px 7px}
.v3 .down{color:#a55b17}.v3 .up{color:#245aa7}.v3 .timeline{margin-top:10px}.v3 .timeline table{min-width:900px}
.v3 .timeline td,.v3 .timeline th{text-align:center;padding:4px}.v3 .timeline td:first-child,.v3 .timeline th:first-child{text-align:left}
.v3 .slotline{font-size:11px;color:#617087;margin:4px 0}.v3 .compact{font-size:12px}.v3 .chips{display:flex;gap:8px;flex-wrap:wrap}
.v3 .stocklist{padding:0;list-style:none;margin:0}.v3 .stocklist li{padding:3px 0}.v3 .rep{font-size:12px}
.v3 .kpis p{margin:3px 0}.v3 th,.v3 td{padding:4px 7px}.v3 .timeline td{font-size:12px;line-height:1.2;padding:4px}
.v3 .timeline .pair{color:#617087;font-size:11px}
@media(max-width:850px){.v3 .kpis{grid-template-columns:1fr 1fr}.v3 .kpis .card:last-child{grid-column:auto}.v3 .layout{grid-template-columns:1fr}}
@media(max-width:480px){.v3 .kpis{grid-template-columns:1fr}}
'''


def change(value):
    return '—' if value is None else f'{value:+d}'


def render(data):
    cur, prev = data['current'], data['previous']
    parts = ['<!doctype html><html lang="ko"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             '<title>Sector Radar v3 · 장중 관심 변화</title><style>'+STYLE+V3_STYLE+'</style></head><body><main class="v3">',
             f'<header><div><h1>Sector Radar <span class="muted">v3</span> · 장중 관심 변화</h1></div><div class="chips"><span class="badge">{e(data["date"])}</span><span class="badge">{e(data["environment"])} · {"모의" if data["environment"] == "mock" else "실전"}</span><span class="badge">1분 조회 · qry_tp=1 · {"테스트" if data["test_mode"] else "운영"}</span></div></header>']
    if cur is None:
        parts.append('<section>선택 날짜·환경·조회 유형·운영/테스트 구분에 성공한 순위 수집이 없습니다.</section>')
    else:
        now_at = timestamp(cur['collection']['collected_at']).strftime('%H:%M:%S')
        old_at = timestamp(prev['collection']['collected_at']).strftime('%H:%M:%S') if prev else '비교 대상 없음'
        gap = f" · 실제 간격 {data['gap_seconds']/60:g}분" if prev else ''
        n = len(data['snapshots'])
        parts += [f'<p class="timing">현재 {now_at} · 직전 성공 {old_at} (KST){gap} · 당일 성공 관측 {n}회 · 생성 {e(data["generated_at"])}</p>',
                  '<div class="notice compact">조회순위는 시장 관심도이며 매수 신호가 아닙니다. 현재 태그를 과거 관측에도 적용합니다. '+
                  ('모의 등락률·금액은 실전 검증을 증명하지 않습니다. ' if data['environment'] == 'mock' else '')+
                  '등락률은 v2 검증 정책의 보조 관측입니다. 관심 지속은 시간 점유율이 아니며 주도 확정을 뜻하지 않습니다.</div>']
        bad_slots = [s for s in data['intervening_slots'] if s['state'] != 'success']
        if bad_slots:
            parts.append('<p class="warning compact">중간 미관측/미완료: '+e(' · '.join(s['slot']+' '+s['state'] for s in bad_slots))+' · 직전 성공 회차와 비교합니다.</p>')
        if (prev and not data['comparison_complete']) or not data['complete']:
            parts.append('<p class="warning compact">불완전 TOP20 표본: 변화는 관측된 수의 차이이며 감소·순환 판단을 보류합니다.</p>')

        def group_list(groups, empty):
            return ''.join(f'<p><b>{e(g["name"])}</b> · 20위 내 <span class="{"up" if (g["delta20"] or 0)>0 else "down"}">{change(g["delta20"])}</span> / 5위 내 {change(g["delta5"])}</p>' for g in groups[:2]) or f'<p class="muted">{empty}</p>'

        parts += ['<div class="kpis"><div class="card"><h2>관심도 급상승 · 관측 증가</h2>',
                  group_list(data['increases'], '증가 섹터 없음' if prev else '비교 대상 없음'),
                  '</div><div class="card"><h2>관심도 감소</h2>',
                  group_list(data['decreases'], '감소 섹터 없음' if prev else '비교 대상 없음'),
                  '</div><div class="card"><h2>진입 · 유지 · 이탈</h2>',
                  '<p class="big">'+(' · '.join(str(len(data[k])) for k in ('entrants','retained','exits')) if prev else '—')+'</p><small>현재 / 직전 TOP20 고유 코드 기준</small></div>',
                  '<div class="card"><h2>관심도 유지 · 당일 반복</h2>']
        parts += [f'<p><b>{e(g["name"])}</b> {g["appearances"]}/{n}회 · 관측 연속 {g["streak"]}</p>' for g in data['persistent'][:3]] or ['<p>분류된 섹터 없음</p>']
        parts += ['</div></div>']
        # Current leaders, movers (including disappeared sectors), then persistent sectors.
        displayed = []
        candidates = [g for g in data['groups'] if g['count']][:3]+data['increases']+data['decreases']+data['persistent']
        for g in candidates:
            if g not in displayed:
                displayed.append(g)
            if len(displayed) == 6:
                break

        def sector_table(groups):
            rows = ['<div class="scroll"><table><thead><tr><th>섹터 / 대표 종목</th><th class="num">TOP20 · Δ</th><th class="num">TOP5 · Δ</th><th class="num">당일 지속성</th><th>시세 검증</th></tr></thead><tbody>']
            for g in groups:
                c = g['current']
                reps = ' · '.join(s['stk_nm'] or s['stk_cd'] for s in c['stocks'][:2]) if c else '현재 표본 없음'
                rows.append(f'<tr><td><b>{e(g["name"])}</b><div class="rep">{e(reps)}</div></td><td class="num">{g["count"]} · {change(g["delta20"])}</td><td class="num">{g["top5"]} · {change(g["delta5"])}</td><td class="num">{g["appearances"]}/{n}회 · 연속 {g["streak"]}<small>TOP5 출현 {g["top5_appearances"]} · 관심 TOP3 {g["top3_appearances"]}</small></td><td class="compact">'+(f'{e(spread_label(c))}<small>{e(turnover_label(c, data["environment"]))} · 적용 {c["turnover_count"]}/{c["interest_count"]}</small>' if c else '현재 시세 대상 없음')+'</td></tr>')
            if not groups:
                rows.append('<tr><td colspan="5">분류된 섹터 없음</td></tr>')
            return ''.join(rows)+'</tbody></table></div>'

        def stocks_list(stocks, limit=None):
            if stocks is None:
                return '<p class="muted">비교 대상 없음</p>'
            if not stocks:
                return '<p class="muted">없음</p>'
            selected = stocks[:limit] if limit else stocks
            return '<ul class="stocklist">'+''.join(f'<li>{s["rank"]}위 · {e(s["stk_nm"] or s["stk_cd"])}<small>{e(s["stk_cd"])} · {e(rate_label(s))}</small></li>' for s in selected)+'</ul>'

        parts += ['<div class="layout"><section><h2>섹터별 관심 변화 · 지속성</h2>', sector_table(displayed),
                  f'<p class="foot">TOP20 고유 {cur["market"]["unique_count"]}종목 · 거래대금 적용 {cur["market"]["turnover_count"]}/{cur["market"]["unique_count"]} · 중복 제거 합계 {e(money(cur["market"]["turnover_won"]))} · 시장 전체 수급이 아닙니다.</p>',
                  f'<details><summary>전체 섹터 {len(data["groups"])}개 · 진입/유지/이탈 코드</summary>', sector_table(data['groups']),
                  '<pre>'+e(json.dumps([{k:g[k] for k in ('name','new_codes','retained_codes','exited_codes')} for g in data['groups']],ensure_ascii=False,indent=2))+'</pre></details></section>',
                  '<section><h2>신규 진입 / 이탈 종목</h2><div class="compact"><b>진입</b>',stocks_list(data['entrants'],2),
                  '<b>이탈 · 직전 순위/시세</b>',stocks_list(data['exits'],2),
                  '<details><summary>전체 진입 · 유지 · 이탈</summary><h3>진입</h3>',stocks_list(data['entrants']),'<h3>유지</h3>',stocks_list(data['retained']),'<h3>이탈 · 직전 관측</h3>',stocks_list(data['exits']),'</details>',
                  '<details><summary>반복 종목 · 현재 TOP20</summary>',
                  ''.join(f'<p>{e(s["name"] or s["code"])} · {s["appearances"]}/{n}회 · TOP5 {s["top5_appearances"]} · 관측 연속 {s["streak"]}</p>' for s in data['repeated_stocks']),
                  '</details></div></section></div>']
        parts += [f'<details><summary>당일 관측 흐름 · {n}회 · TOP20 / TOP5</summary><section class="timeline"><h2>당일 관측 흐름 · 셀: TOP20 / TOP5</h2><div class="scroll"><table><thead><tr><th>섹터</th>']
        visible_snapshots = data['snapshots'][-10:]
        for i, snap in enumerate(visible_snapshots):
            at = timestamp(snap['collection']['collected_at'])
            original_i = len(data['snapshots'])-len(visible_snapshots)+i
            before = data['snapshots'][original_i-1] if original_i else None
            gap_label = f'{(at-timestamp(before["collection"]["collected_at"])).total_seconds()/60:g}분 간격' if before else '첫 관측'
            parts.append(f'<th>{at:%H:%M}<small>{gap_label}</small></th>')
        parts.append('</tr></thead><tbody>')
        for g in displayed[:3]:
            parts.append(f'<tr><td>{e(g["name"])}</td>'+''.join(f'<td>{a} <span class="pair">/ {b}</span></td>' for a,b in zip(g['history'][-10:],g['top5_history'][-10:]))+'</tr>')
        parts += ['</tbody></table></div><p class="slotline">'+e(' · '.join(s['slot']+' '+{'success':'성공','future':'예정','missing':'기록 없음'}.get(s['state'],s['state']) for s in data['slots']))+'</p>',
                  '<small>요약 3섹터 · 최근 최대 10관측 · 미관측은 보간하지 않습니다. 관측 연속은 시간상 연속이 아닙니다.</small>',
                  '<details><summary>전체 섹터 · 모든 관측 흐름</summary><div class="scroll"><table><thead><tr><th>섹터</th>'+''.join(f'<th>{timestamp(s["collection"]["collected_at"]):%H:%M:%S}</th>' for s in data['snapshots'])+'</tr></thead><tbody>',
                  ''.join(f'<tr><td>{e(g["name"])}</td>'+''.join(f'<td>{a} / {b}</td>' for a,b in zip(g['history'],g['top5_history']))+'</tr>' for g in data['groups']),
                  '</tbody></table></div></details></section></details>']
        unclassified = [s for s in cur['stocks'] if not s['tags']]
        total = cur['market']['unique_count']
        percentage = f'{100*len(unclassified)/total:.0f}%' if total else '—'
        parts += [f'<section class="unclassified"><b>미분류 {len(unclassified)}종목 ({percentage})</b> · 복수 섹터 {len(data["overlap"])}종목 · 섹터 합계는 전체 고유 수와 다를 수 있습니다.',
                  '<details><summary>미분류 · 복수 소속 목록</summary>',stocks_list(unclassified),
                  ''.join(f'<p>{e(s["stk_nm"])} · {e(" / ".join(t["sector_name"] for t in s["tags"]))}</p>' for s in data['overlap']),'</details></section>']
    parts += ['<details><summary>당일 누적순위 · qry_tp=4 별도 영역</summary><p>향후 장 마감 후 하루 1회 결산을 위한 별도 영역입니다. 이번 v3에는 누적순위 수집·생성 기능이 없습니다. 장중 변화와 직접 비교하거나 합산하지 않습니다.</p></details>',
              '<details><summary>비교 기준 · 회차 상태 · 시세 검증 상세</summary>',
              '<p>동일 날짜·환경·qry_tp=1·test_mode 성공 회차만 비교합니다. 첫 관측 Δ는 —입니다. 누락·실패를 0으로 채우지 않습니다. 섹터가 관측 표본에 없으면 그 표본의 수는 0입니다. 불완전 표본의 차이는 순환 확정 근거가 아닙니다.</p>',
              '<p>거래대금과 등락률은 v2의 최신 배치·형식·상태·시점·단위·커버리지 검사를 그대로 사용합니다. 여러 회차 누적 거래대금을 더하지 않습니다. 등락률의 형식 통과는 실전 검증 완료를 보증하지 않습니다.</p>',
              '<pre>'+e(json.dumps(data['slots'],ensure_ascii=False,indent=2))+'</pre>']
    if cur:
        parts += [f'<p>현재 UUID {e(cur["collection"]["id"])} · 직전 UUID {e(prev["collection"]["id"]) if prev else "—"}</p>']
        parts += [f'<p>{e(g["name"])} · 거래대금 {e(money(g["turnover_won"]))} · 평균 {e(money(g["average_turnover_won"]))} · 적용 {g["turnover_count"]}/{g["interest_count"]}'+(' · 부분합' if 0 < g['turnover_count'] < g['interest_count'] else '')+'</p>' for g in cur['sectors']]
        parts += [stock_details(s) for s in cur['stocks']]
    parts += ['</details><details><summary>기존 v1 진단 · 선택한 장중 흐름 전체</summary>',
              render_diagnostics(data['legacy']).split('<main>',1)[1].split('</main>',1)[0],
              '</details><p class="foot">로컬 읽기 전용 · 외부 리소스 없음 · 수집·주문 없음</p></main></body></html>']
    return ''.join(parts)


def export(path, day, environment, output, test_mode=0):
    target, source = Path(output).absolute(), Path(path).resolve()
    if target.is_symlink() or target.exists():
        raise ValueError('기존 출력 파일은 덮어쓰지 않습니다.')
    if target.resolve() == source or target.suffix.lower() not in ('.html','.htm'):
        raise ValueError('DB와 다른 새 .html 경로를 지정하세요.')
    data = report(source, day, environment, test_mode)
    with target.open('x',encoding='utf-8') as handle:
        handle.write(render(data))
    return dict(output=str(target), date=day, environment=environment, qry_tp='1',
                test_mode=test_mode, collections=len(data['snapshots']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/stock1.db')
    parser.add_argument('--date',default=datetime.now(KST).date().isoformat())
    parser.add_argument('--environment',choices=('mock','real'),default='mock')
    parser.add_argument('--test-mode',choices=(0,1),type=int,default=0)
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    try:
        result = export(args.db,args.date,args.environment,args.output,args.test_mode)
    except (ValueError,OSError,sqlite3.Error) as error:
        parser.exit(1,str(error)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
