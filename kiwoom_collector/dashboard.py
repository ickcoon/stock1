"""Offline static HTML report: no server, remote scripts, browser automation or API."""
import json
from pathlib import Path

from .timeline import sector_timeline


def export_dashboard(db, day, environment, qry_tp, output):
    path = Path(output).resolve()
    if path.suffix.lower() != '.html':
        raise ValueError('대시보드 출력은 새 .html 파일 경로여야 합니다.')
    report = sector_timeline(db, day, environment, qry_tp)
    data = json.dumps(report, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    document = TEMPLATE.replace('__REPORT_DATA__', data)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open('x', encoding='utf-8', newline='')
    except FileExistsError:
        raise ValueError('HTML 파일이 이미 존재합니다. 다른 파일명으로 출력하세요.') from None
    try:
        with handle:
            handle.write(document)
    except BaseException:
        path.unlink()
        raise
    return {'output': str(path), 'environment': environment, 'date': day,
            'note': '브라우저에서 파일을 열어 보세요. 생성 시점의 정적 보고서이며 자동 갱신되지 않습니다.'}


TEMPLATE = '''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>stock1 · 섹터 관심도 추이</title>
<style>
body{font:15px system-ui,sans-serif;margin:0;background:#f4f7fa;color:#172738}main{max-width:1200px;margin:auto;padding:24px}h1{font-size:26px}section{background:white;border:1px solid #dbe3eb;border-radius:12px;padding:18px;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:10px;border-bottom:1px solid #dde5eb;text-align:left;white-space:nowrap}.scroll{overflow:auto}.badge{display:inline-block;padding:7px 12px;background:#153d65;color:white;border-radius:6px}button,select{padding:8px;margin:4px;border:1px solid #bccbd7;border-radius:5px;background:white;color:#172738}button:focus,button:hover{background:#e5eff8}.warn{color:#8b4100;background:#fff4df;padding:12px;border-radius:6px}.muted{color:#627587}.missing{background:#eceff2;color:#687681}svg{vertical-align:middle}.small{font-size:12px}#slots{display:flex;flex-wrap:wrap}td a{color:inherit}
</style></head><body><main>
<h1>stock1 · 섹터 관심도 추이</h1><p id="metadata" class="badge"></p>
<p class="warn">조회순위 관측 기반입니다. 주도섹터·매수 신호 확정이 아닙니다. 상승률·거래대금: 미검증. 주문 기능 없음.</p>
<section><h2>시간별 포함 종목 수</h2><p>다중 태그는 각 섹터에 반영되어 섹터 합계가 고유 종목 수보다 클 수 있습니다. — 는 누락·실패·빈 수집이며 0이 아닙니다. 성공 관측에서 해당 섹터가 없을 때만 0입니다.</p><div class="scroll" id="trend"></div></section>
<section><h2>시점 상세</h2><div id="slots"></div><p id="slotmeta"></p><div class="scroll" id="details"></div><h3>직전 예정 시점 대비 종목 교체</h3><div id="changes"></div><h3>09:40 대비 상태</h3><div id="states"></div></section>
<section><h2>15:00 관심종목 · 군집 2종목 이상</h2><p>최신 다른 회차로 대체하지 않습니다. 가격 상승률·거래대금·종가매매 적합성은 미검증입니다.</p><div id="review"></div></section>
<section><h2>규칙과 데이터 범위</h2><p>09:40에 2종목 이상인 군집: 현재 0종목이면 소멸, 1종목이면 약화. 2종목 이상이면 포함 수·TOP5 수·TOP10 수·평균 순위의 방향을 비교합니다. 전부 비악화이면서 하나 이상 개선이면 강화, 전부 같으면 유지, 전부 비개선이면서 하나 이상 악화면 약화, 서로 엇갈리면 혼조입니다. 평균 순위는 낮을수록 개선입니다.</p><p>오전 2종목 미만 → 현재 2종목 이상이며 직전 오후 비교에서 신규 군집이면 오후 신규 부상으로 표시합니다. 직전 성공 자료가 없거나 이미 군집이면 오전 대비 신규 군집으로만 표시합니다. 절대적인 첫 형성을 확정하지 않습니다. 유지/강화여도 구성 종목은 교체될 수 있습니다.</p><ul id="limitations"></ul><p class="muted">생성 시점 정적 보고서 · 현재 수동 태그 기준 · test_mode 회차 제외 · 로컬 파일에서 동작</p></section>
</main><script type="application/json" id="data">__REPORT_DATA__</script><script>
const report=JSON.parse(document.getElementById('data').textContent);
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
function table(headers,rows){const t=el('table'),h=el('tr');headers.forEach(x=>h.append(el('th',x)));t.append(h);rows.forEach(row=>{const tr=el('tr');row.forEach(x=>tr.append(el('td',x===null||x===undefined?'—':String(x))));t.append(tr);});return t;}
function names(stocks){return stocks.map(s=>`${s.stock_name||'이름 미제공'} (${s.stock_code||'코드 미제공'}, ${s.rank}위)`).join(', ')||'없음';}
const fmt=n=>n===null||n===undefined?'—':Number(n).toFixed(1);
document.getElementById('metadata').textContent=`${report.date} KST · ${report.environment==='mock'?'모의투자 API 수집':'실전 API 수집'} · qry_tp=${report.qry_tp} · 테스트 회차 제외`;
report.limitations.forEach(s=>document.getElementById('limitations').append(el('li',s)));
const sectors=new Map();report.slots.forEach(s=>s.sectors.forEach(g=>sectors.set(g.sector_id,g.sector_name)));
const trend=table(['섹터',...report.schedule,'추이'],[...sectors].map(([id,name])=>[name,...report.slots.map(s=>s.summary?(s.sectors.find(g=>g.sector_id===id)?.stock_count||0):null),'']));
[...sectors].forEach(([id,name],index)=>{const tr=trend.rows[index+1];report.slots.forEach((s,i)=>{const td=tr.cells[i+1];if(!s.summary){td.className='missing';td.title=s.status;}else{const count=s.sectors.find(g=>g.sector_id===id)?.stock_count||0;td.style.background=`rgba(24,120,160,${Math.min(count/10,.6)})`;td.title='성공 관측의 고유 포함 종목 수';}const b=el('button',td.textContent);b.onclick=()=>show(i);td.replaceChildren(b);});const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('width','150');svg.setAttribute('height','42');svg.setAttribute('role','img');svg.setAttribute('aria-label',name+' 포함 수 추이; 결측 구간은 연결 안 함');let last=null;report.slots.forEach((s,i)=>{const value=s.summary?(s.sectors.find(g=>g.sector_id===id)?.stock_count||0):null;if(value===null){last=null;return;}const point=[5+i*15,37-value*1.6];if(last){const line=document.createElementNS(svg.namespaceURI,'line');['x1','y1','x2','y2'].forEach((key,j)=>line.setAttribute(key,[...last,...point][j]));line.setAttribute('stroke','#176e9a');svg.append(line);}const dot=document.createElementNS(svg.namespaceURI,'circle');dot.setAttribute('cx',point[0]);dot.setAttribute('cy',point[1]);dot.setAttribute('r','2');dot.setAttribute('fill','#176e9a');svg.append(dot);last=point;});tr.cells[tr.cells.length-1].append(svg);});
document.getElementById('trend').append(trend);if(!sectors.size)document.getElementById('trend').append(el('p','성공 관측에서 분류된 섹터가 없습니다.'));
function show(i){const s=report.slots[i],summary=s.summary;document.getElementById('slotmeta').textContent=`예정 ${s.scheduled_at} · 실제 ${s.attempt?.collected_at||'없음'} · 상태 ${s.status}`;const d=document.getElementById('details');d.replaceChildren();if(!summary){d.append(el('p','관측 불충분: 종목 수와 섹터 지표를 0으로 처리하지 않습니다.'));}else{d.append(el('p',`관측 ${summary.observed_record_count}건 · 고유 ${summary.unique_stock_count}종목 · 미분류 ${summary.unclassified_stock_count}종목 · 코드 미식별 ${summary.unidentified_record_count}건`));if(summary.observed_record_count<20){const w=el('p','20건 미만 관측: 표본 부족으로 상태 분류는 잠정적입니다.');w.className='warn';d.append(w);}d.append(table(['섹터','종목 수','TOP5','TOP10','평균 순위','관측 종목'],s.sectors.map(g=>[g.sector_name,g.stock_count,g.top5_count,g.top10_count,fmt(g.mean_rank),names(g.stocks)])));d.append(el('p','미분류: '+names(summary.unclassified_stocks)));}
const c=document.getElementById('changes');c.replaceChildren();if(s.versus_previous.available){c.append(el('p',`신규: ${names(s.versus_previous.entered)} / 이탈: ${names(s.versus_previous.exited)} / 잔존율 ${fmt(s.versus_previous.retention_percent)}%`));c.append(table(['섹터','신규 진입','이탈','잔존 수'],s.versus_previous.sectors.map(g=>[g.sector_name,names(g.entered),names(g.exited),g.retained_count])));}else c.append(el('p',s.versus_previous.reason));const states=document.getElementById('states');states.replaceChildren();if(s.classification?.available){states.append(el('p',s.classification.rotation_candidate?`관심군집 교체 후보: 약화/소멸 ${s.classification.weakening_sectors.join(', ')} → 강화/신규 ${s.classification.rising_sectors.join(', ')} (인과·주도 교체 미확정)`:'동시 약화·부상에 따른 교체 후보 없음'));states.append(table(['섹터','상태','09:40 → 현재','구성 교체','가격/거래대금'],s.classification.sectors.map(g=>[g.sector_name,g.label,`${g.morning_count} → ${g.current_count}`,g.composition_changed?'있음':'없음','미검증 / 미검증'])));}else states.append(el('p',s.classification?.reason||'상태 분류는 13:00/14:00/15:00에서 제공; 오전은 군집 형성 관측'));
}
report.slots.forEach((s,i)=>{const b=el('button',`${s.slot} · ${s.status}`);b.onclick=()=>show(i);document.getElementById('slots').append(b);});
const close=report.slots.find(s=>s.slot==='15:00');const review=document.getElementById('review');if(close?.summary){review.append(table(['종목코드','종목명','조회순위','상승률','거래대금'],close.review_stocks.map(s=>[s.stock_code,s.stock_name,s.rank,'미검증','미검증'])));if(!close.review_stocks.length)review.append(el('p','2종목 이상 군집의 관심종목이 없습니다.'));}else review.append(el('p','15:00 성공 관측 없음: 관심종목 선정 불가'));
show(Math.max(0,report.slots.findLastIndex?report.slots.findLastIndex(s=>s.summary):0));
</script></body></html>'''
