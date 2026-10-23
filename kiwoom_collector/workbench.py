"""Generate a local classification workspace. No DB writes, server, or API calls."""
from datetime import datetime, timedelta
import json

from .client import KST
from .external_tags import exclusive_output, priority_rows, tables
from .summary import _rows, read_database
from .workbench_priority import ranking_context, recent_order


def workspace_data(path, environment='mock', *, now=None):
    if environment not in {'mock', 'real'}:
        raise ValueError('환경은 mock 또는 real이어야 합니다.')
    at = now or datetime.now(KST)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('관측 기준 시각에는 시간대가 필요합니다.')
    at = at.astimezone(KST)
    since = at - timedelta(days=7)
    with read_database(path) as (db, _):
        stocks, excluded = priority_rows(db, environment)
        context = ranking_context(db, environment, at)
        for position, stock in enumerate(stocks, 1):
            stock.update(context['describe'](stock['stock_code']), legacy_priority=position, csv_supported=True)
        stocks = recent_order(stocks)
        for position, stock in enumerate(stocks, 1):
            stock['recent_priority'] = position
        sectors = [dict(r) for r in _rows(db, '''SELECT s.sector_id,s.sector_name,
            COUNT(t.stock_code) AS stock_count FROM sectors s LEFT JOIN stock_sectors t
            ON t.sector_id=s.sector_id GROUP BY s.sector_id ORDER BY s.sector_name''')]
        available = tables(db)
        aliases = [dict(r) for r in _rows(db, '''SELECT a.alias,a.source,s.sector_name
            FROM sector_aliases a JOIN sectors s ON s.sector_id=a.sector_id
            ORDER BY a.alias,a.source''')] if 'sector_aliases' in available else []
        sources = {r['source'] for r in _rows(db, 'SELECT DISTINCT source FROM stock_sectors')}
        if 'sector_external_sources' in available:
            sources.update(r['source'] for r in _rows(db, 'SELECT source FROM sector_external_sources'))
        return {'environment':environment, 'generated_at':at.isoformat(), 'recent_since':since.isoformat(),
                'stocks':stocks, 'sectors':sectors, 'aliases':aliases, 'sources':sorted(sources),
                'excluded_unsupported_code_count':excluded,
                'unsupported_stocks':context['unsupported_stocks'],
                'latest_ranking':context['latest_ranking'], 'previous_ranking':context['previous_ranking'],
                'latest_unclassified_count':len(context['latest_unclassified_codes']),
                'latest_processable_count':sum(s['in_latest_top20'] for s in stocks)}


def export_workbench(path, environment, output):
    data = workspace_data(path, environment)
    encoded = json.dumps(data, ensure_ascii=False).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    document = TEMPLATE.replace('__WORKSPACE_DATA__', encoded)
    output_path = exclusive_output(output, '.html', lambda handle: handle.write(document))
    return {'output':output_path, 'environment':environment, 'stock_count':len(data['stocks']),
            'generated_at':data['generated_at'], 'note':'DB 읽기 전용 스냅샷. HTML은 CSV만 내보내며 DB에 적용하지 않습니다.'}


TEMPLATE = '''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; form-action 'none'; base-uri 'none'">
<title>stock1 · 미분류 종목 작업실</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#183047;font:15px system-ui,sans-serif}main{max-width:1320px;margin:auto;padding:24px}h1{font-size:26px}h2{font-size:20px}h3{font-size:16px}section,aside{background:white;border:1px solid #d4deea;border-radius:10px;padding:18px;margin-bottom:16px}.layout{display:grid;grid-template-columns:350px 1fr;gap:18px}.notice{background:#e8f1fb;padding:12px;border-radius:6px}.muted{color:#536779;font-size:13px}.warn{color:#8b3e00}.fields{display:grid;grid-template-columns:1fr 1fr;gap:10px}label{display:block;font-size:13px}input,button{font:inherit;padding:9px;border:1px solid #bccada;border-radius:5px}input:not([type=checkbox]){width:100%;margin-top:4px}button{cursor:pointer;background:#edf4fc;color:#183047}button:hover{background:#dce9f8}button:disabled{cursor:default;opacity:.5}button:focus-visible,input:focus-visible{outline:3px solid #2682c6}.primary{background:#174e83;color:white}.stock{display:block;width:100%;text-align:left;margin:6px 0;background:white}.stock.active{border:2px solid #246da8;background:#eaf2fc}.stock span{display:block}.stock-list{max-height:70vh;overflow:auto}.catalog{display:flex;gap:6px;flex-wrap:wrap;max-height:200px;overflow:auto;margin:12px 0}.tag{border:1px solid #d4deea;border-radius:7px;padding:12px;margin-top:12px}.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.wide{grid-column:1/-1}#status{white-space:pre-wrap}small{font-size:12px}summary{cursor:pointer}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:7px;text-align:left;border-bottom:1px solid #d4deea}@media(max-width:800px){.layout,.fields{grid-template-columns:1fr}.stock-list{max-height:300px}main{padding:12px}}
p{overflow-wrap:anywhere}select{font:inherit;max-width:100%;padding:8px;border:1px solid #bccada;border-radius:5px;background:white;color:inherit}.layout>*{min-width:0}.stock .facts{font-size:13px;color:#536779;margin-top:3px}.stock .entry{font-size:13px;color:#245c89;margin-top:3px}.readonly-list{max-width:100%;overflow:auto}details:not([open])>.readonly-list{display:none}.readonly-list table{min-width:650px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6fa;padding:12px}.badge{display:inline-block;padding:2px 7px;border:1px solid #bccada;border-radius:4px;font-size:12px}.current-tags{background:#f3f6fa;padding:10px;border-radius:6px}
</style></head><body><main>
<h1>stock1 · 미분류 종목 작업실</h1><p id="metadata" class="notice"></p>
<p class="muted">이 화면은 생성 시점의 DB 스냅샷입니다. 기존 태그를 삭제하거나 덮어쓰지 않습니다. 선택은 이 창에만 보관되며 창을 닫거나 새로고침하면 사라집니다. 작업 중에는 CSV를 다운로드해 보관하세요.</p>
<section><div class="toolbar"><button id="export" class="primary" disabled>확인한 태그 CSV 다운로드</button><span id="progress" aria-live="polite"></span></div><p id="status" role="status">종목과 테마를 선택한 뒤 출처·확인일·이용조건을 직접 확인하세요.</p><p class="muted">미확인 태그는 내보내지 않습니다. 확인 표시를 했어도 필수 값이 잘못되면 내보내기를 중단합니다. CSV 적용은 PowerShell의 sector import --dry-run → sector import로 진행하세요. 이 화면은 DB에 직접 적용하지 않습니다.</p></section>
<p id="priority-context" class="notice"></p>
<div class="layout"><aside><h2>분류 우선순위</h2><label>정렬<select id="priority-sort"><option value="recent">최근 TOP20 · TOP5 · TOP10 우선</option><option value="legacy">기존 누적 우선순위</option></select></label><label><input id="latest-only" type="checkbox"> 최신 TOP20 미분류만</label><label>종목 검색<input id="stock-filter" placeholder="종목코드 또는 종목명" type="search"></label><p id="stock-count" class="muted"></p><div id="stocks" class="stock-list"></div></aside>
<div><section><h2 id="stock-title">종목 선택</h2><p id="stock-current-tags" class="current-tags"></p><p id="stock-facts" class="muted"></p><details><summary>누적 이력 · 관측 근거</summary><p id="stock-history" class="muted"></p></details>
<label>기존 테마 검색 · 별칭도 검색 가능<input id="theme-search" type="search" placeholder="테마명 검색"></label><div id="catalog" class="catalog"></div>
<div class="toolbar"><input id="new-theme" aria-label="새 테마명" placeholder="새 테마명 (CSV 적용 때 등록)"><button id="add-theme">새 테마 선택</button></div>
<p class="muted">기존 테마의 괄호 숫자는 현재 DB 소속 종목 수입니다. 한 종목에 여러 테마를 선택할 수 있습니다. 별칭을 검색하면 정식 이름을 선택하며 자동 추론·병합하지 않습니다.</p></section>
<section><details><summary>반복 입력용 메타데이터 (확인 표시 자동 복사 안 함)</summary><div id="defaults" class="fields"></div><button id="copy-defaults">현재 종목의 미확인 태그에 입력값 복사</button></details><div class="toolbar"><h2>이번 CSV의 태그</h2><button id="confirm-stock">현재 종목 태그를 직접 확인했음</button></div><div id="tags"></div></section>
</div></div>
<datalist id="sources"></datalist>
<section><details><summary id="unsupported-title">CSV 코드 호환성 검토 · 읽기 전용 보류 목록</summary><p class="warn">현재 종목 마스터·CSV importer는 6자리 숫자 코드만 지원합니다. 아래 코드는 원문 그대로 보존하며 숫자로 변환하거나 CSV에 넣지 않습니다. 코드 지원 확대는 별도 검증이 필요합니다.</p><div id="unsupported-stocks" class="readonly-list"></div></details></section>
<section><h2>데이터 범위와 적용 절차</h2><p>대상은 sector export-priority의 기존 미분류 합집합을 재사용합니다. 작업실 기본 정렬은 최근 7일 TOP20 횟수 → TOP5 → TOP10 → 최근 순위 관측 시각이며, 동률은 기존 누적 순서로 결정합니다. 복잡한 점수는 없습니다. 기존 누적 정렬도 선택할 수 있고 export-priority의 CSV 형식·정렬은 그대로입니다. 환경별 성공 운영 [0198]만 최근 통계에 사용하며 테스트·실패·미래·시간대 미확인 회차는 제외합니다. 최근 7일 횟수는 생성 시각 직전 7×24시간의 회차 수입니다. 여러 조회유형은 각각 한 회차로 세며 같은 날짜의 종목 수가 아닙니다.</p><p>신규 진입은 최신 성공 운영 TOP20과 같은 KST 날짜·환경·qry_tp의 직전 성공 회차를 비교합니다. 직전 회차가 없으면 비교 없음이며, 상장 신규·시장 최초 발견을 의미하지 않습니다. 최신 회차가 오래되어도 API를 호출하지 않고 관측 날짜를 그대로 표시합니다. 조건검색은 기존 누적 자료만 참고합니다.</p><p>반도체·반도체 소부장·로봇·2차전지 등 큰 범주를 먼저 검토하고 지나친 세분화를 피하세요. 종목명만으로 섹터를 확정하지 말고 실제 사업·공시 등 근거를 확인하세요. 복수 섹터는 허용하며 기존 태그를 임의로 삭제·통합하지 않습니다.</p><p>CSV는 UTF-8 BOM이며 stock_code,stock_name,sector_name,source,observed_at,terms_url,terms_note,terms_checked_at 형식입니다. 여러 테마는 여러 행입니다. 종목코드는 문자열로 유지하므로 Excel에서는 텍스트 열로 가져오세요. 새 테마는 실제 import 때 생성되고 기존 종목명·태그는 보존됩니다. 이용조건의 진위·허가 범위를 시스템이 검증하지 않으므로 사용자가 직접 확인해야 합니다.</p><details><summary>CSV 검증·가져오기 명령 확인</summary><pre id="csv-workflow"></pre><p class="warn">dry-run은 읽기 전용 검증입니다. 실제 import는 DB를 변경하므로 검토·승인 후 별도로 실행하세요. import-report는 실제 적용 이력이 있을 때만 열 수 있습니다. 이 HTML은 CSV 다운로드만 수행합니다.</p></details><p class="warn">브라우저의 선택 해제는 이번 CSV에서만 제외합니다. CSV에 없는 기존 태그는 삭제되지 않습니다. 다운로드 후에도 원본 DB가 바뀔 수 있으므로 반드시 dry-run 결과를 다시 검토하세요.</p></section>
</main><script id="workspace-data" type="application/json">__WORKSPACE_DATA__</script><script>
'use strict';
const data=JSON.parse(document.getElementById('workspace-data').textContent);
const columns=['stock_code','stock_name','sector_name','source','observed_at','terms_url','terms_note','terms_checked_at'];
const metaFields=[['source','출처','text'],['observed_at','테마 확인일','date'],['terms_url','이용조건 URL (직접 확인)','url'],['terms_note','이용조건 확인 메모 (한 줄, 200자 이내)','text'],['terms_checked_at','이용조건 확인일','date']];
const byId=id=>document.getElementById(id);
const node=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
const drafts=new Map(),catalog=new Map(data.sectors.map(s=>[s.sector_name,s.stock_count]));
const defaults=Object.fromEntries(metaFields.map(([key])=>[key,'']));
let active=data.stocks[0]?.stock_code||null;
const kstToday=()=>new Date(Date.now()+9*3600000).toISOString().slice(0,10);
const blankMeta=()=>Object.fromEntries(metaFields.map(([key])=>[key,'']));
function validLabel(value){return typeof value==='string'&&value.trim().length>0&&value.trim().length<=200&&!/[\\x00-\\x1f]/.test(value);}
function validDate(value){if(!/^\\d{4}-\\d{2}-\\d{2}$/.test(value))return false;const date=new Date(value+'T00:00:00Z');return !Number.isNaN(date.getTime())&&date.toISOString().slice(0,10)===value&&value<=kstToday();}
function validateTag(tag){const errors=[];for(const key of ['sector_name','source','terms_note'])if(!validLabel(tag[key]))errors.push(key+' 필수/형식 확인');for(const key of ['observed_at','terms_checked_at'])if(!validDate(tag[key]))errors.push(key+' 과거 또는 오늘의 YYYY-MM-DD 필요');try{const u=new URL(tag.terms_url);if(!['http:','https:'].includes(u.protocol)||u.username||u.password||/[\\x00-\\x1f]/.test(tag.terms_url))throw Error();}catch{errors.push('terms_url 유효한 http/https URL 필요');}return errors;}
function csvCell(value){return '"'+String(value??'').replaceAll('"','""')+'"';}
function buildExport(){const rows=[],issues=[];let unconfirmed=0;for(const stock of data.stocks){for(const tag of drafts.get(stock.stock_code)||[]){if(!tag.confirmed){unconfirmed++;continue;}const errors=validateTag(tag);if(errors.length){issues.push(stock.stock_code+' / '+tag.sector_name+': '+errors.join(', '));continue;}rows.push({stock_code:stock.stock_code,stock_name:stock.stock_name,...Object.fromEntries(['sector_name',...metaFields.map(([k])=>k)].map(k=>[k,tag[k].trim()]))});}}return {rows,issues,unconfirmed,csv:'\\ufeff'+[columns,...rows.map(r=>columns.map(k=>r[k]))].map(row=>row.map(csvCell).join(',')).join('\\r\\n')+'\\r\\n'};}
function progress(){const result=buildExport(),total=[...drafts.values()].reduce((n,rows)=>n+rows.length,0);byId('progress').textContent=`선택 ${total}태그 · 내보낼 수 있음 ${result.rows.length} · 미확인 제외 ${result.unconfirmed} · 오류 ${result.issues.length}`;byId('export').disabled=!result.rows.length||!!result.issues.length;}
function entryLabel(stock){return !stock.in_latest_top20?'최신 회차 밖':stock.new_in_latest===null?'신규 여부 비교 없음':stock.new_in_latest?'직전 대비 신규 진입':'최신 TOP20 유지';}
function renderStocks(){const filter=byId('stock-filter').value.trim().toLowerCase(),legacy=byId('priority-sort').value==='legacy';byId('stocks').replaceChildren();const filtered=data.stocks.filter(s=>(s.stock_code+' '+s.stock_name).toLowerCase().includes(filter)&&(!byId('latest-only').checked||s.in_latest_top20));if(legacy)filtered.sort((a,b)=>a.legacy_priority-b.legacy_priority);if(!filtered.some(s=>s.stock_code===active)){active=filtered[0]?.stock_code||null;renderActive();}byId('stock-count').textContent=`CSV 검토 가능 ${data.stocks.length}종목 / 표시 ${filtered.length}종목`;for(const s of filtered){const b=node('button');b.className='stock'+(s.stock_code===active?' active':'');const facts=node('span',`최근 7일 TOP20 ${s.recent_ranking_appearances}회 · TOP5 ${s.recent_top5_appearances} · TOP10 ${s.recent_top10_appearances}`);facts.className='facts';const last=node('span',`최근 등장 ${s.last_ranking_date||'TOP20 관측 없음'} · 현재 미분류`);last.className='facts';const entry=node('span',`${entryLabel(s)} · 선택 ${(drafts.get(s.stock_code)||[]).length}태그`);entry.className='entry';b.append(node('strong',`${legacy?s.legacy_priority:s.recent_priority}. ${s.stock_code} · ${s.stock_name||'이름 미제공'}`),facts,last,entry);b.onclick=()=>{active=s.stock_code;renderStocks();renderActive();};byId('stocks').append(b);}if(!filtered.length)byId('stocks').append(node('p','해당 미분류 종목이 없습니다.'));}
function selectTheme(name){name=name.trim();if(!active||!validLabel(name)){byId('status').textContent='종목을 선택하고 비어 있지 않은 테마명을 입력하세요.';return;}const tags=drafts.get(active)||[];if(tags.some(t=>t.sector_name===name)){byId('status').textContent='이미 선택한 테마입니다.';return;}tags.push({sector_name:name,...defaults,confirmed:false});drafts.set(active,tags);if(!catalog.has(name))catalog.set(name,null);byId('new-theme').value='';renderStocks();renderActive();progress();}
function renderCatalog(){const term=byId('theme-search').value.trim().toLowerCase();byId('catalog').replaceChildren();for(const [name,count] of catalog){if(!name.toLowerCase().includes(term)&&!data.aliases.some(a=>a.sector_name===name&&a.alias.toLowerCase().includes(term)))continue;const b=node('button',name+(count===null?' · 새 테마':` (${count})`));b.disabled=!active||(drafts.get(active)||[]).some(t=>t.sector_name===name);b.onclick=()=>selectTheme(name);byId('catalog').append(b);}if(!byId('catalog').childElementCount)byId('catalog').append(node('p','일치하는 테마가 없습니다. 새 테마명을 직접 입력할 수 있습니다.'));}
function metaInputs(parent,model,onchange,prefix){for(const [key,title,type] of metaFields){const label=node('label',title),input=node('input');input.type=type;input.value=model[key];input.dataset.field=key;input.id=prefix+'-'+key;if(['source','terms_note'].includes(key))input.maxLength=200;if(key==='source')input.setAttribute('list','sources');input.oninput=()=>{model[key]=input.value;onchange();};label.append(input);parent.append(label);}}
function renderTags(){const area=byId('tags');area.replaceChildren();const tags=drafts.get(active)||[];for(const [index,tag] of tags.entries()){const card=node('div');card.className='tag';card.dataset.theme=tag.sector_name;const title=node('h3',tag.sector_name),fields=node('div');fields.className='fields';const check=node('input');check.type='checkbox';check.checked=tag.confirmed;check.dataset.field='confirmed';const status=node('span');const refresh=()=>{check.checked=tag.confirmed;status.textContent=tag.confirmed?(validateTag(tag).length?'확인 내용 수정 필요':'확인 표시됨'):'미확인 · CSV에서 제외';progress();};metaInputs(fields,tag,()=>{tag.confirmed=false;refresh();},'tag-'+index);const label=node('label');label.append(check,document.createTextNode(' 이 테마의 출처 자료와 이용조건을 직접 확인했습니다'));check.onchange=()=>{tag.confirmed=check.checked;refresh();};const remove=node('button','이번 CSV 선택 해제');remove.onclick=()=>{tags.splice(index,1);renderStocks();renderActive();progress();};card.append(title,fields,label,status,document.createTextNode(' '),remove);area.append(card);refresh();}if(!tags.length)area.append(node('p','기존 테마를 선택하거나 새 테마명을 입력하세요.'));}
function renderActive(){const stock=data.stocks.find(s=>s.stock_code===active);byId('stock-title').textContent=stock?`${stock.stock_code} · ${stock.stock_name||'이름 미제공'}`:'미분류 대상 없음';byId('stock-current-tags').textContent=stock?'현재 DB 태그: 없음 · 생성 시점에 미분류 (아래 선택은 이번 CSV 초안)':'현재 선택한 종목 없음';byId('stock-facts').textContent=stock?`최근 7일 TOP20 ${stock.recent_ranking_appearances}회 · TOP5 ${stock.recent_top5_appearances} · TOP10 ${stock.recent_top10_appearances} · 최근 등장 ${stock.last_ranking_date||'TOP20 관측 없음'} · ${entryLabel(stock)}`:'';byId('stock-history').textContent=stock?`누적 [0198] ${stock.ranking_appearances}회 · 조건검색 누적 ${stock.condition_appearances}회 (${stock.condition_aliases.join(', ')||'등장 기록 없음'}) · 순위 최초 ${stock.first_ranking_seen||'없음'} · 순위 최종 ${stock.last_ranking_seen||'없음'} · 전체 최종 관측 ${stock.last_seen} · 마스터 ${stock.in_master?'등록':'미등록 (이름 확인 필요)'}`:'';byId('confirm-stock').disabled=!stock;byId('copy-defaults').disabled=!stock;byId('add-theme').disabled=!stock;renderCatalog();renderTags();}
byId('stock-filter').oninput=renderStocks;byId('theme-search').oninput=renderCatalog;byId('priority-sort').onchange=renderStocks;byId('latest-only').onchange=renderStocks;
byId('add-theme').onclick=()=>{const name=byId('new-theme').value.trim();if(data.aliases.some(a=>a.alias===name&&a.sector_name!==name)){byId('status').textContent='이미 등록된 별칭입니다. 기존 테마 검색에서 정식 이름을 선택하세요.';return;}selectTheme(name);};
metaInputs(byId('defaults'),defaults,()=>{},'default');
byId('copy-defaults').onclick=()=>{for(const tag of drafts.get(active)||[])if(!tag.confirmed)Object.assign(tag,defaults);renderTags();byId('status').textContent='미확인 태그에 입력값만 복사했습니다. 직접 확인 표시가 필요합니다.';};
byId('confirm-stock').onclick=()=>{const tags=drafts.get(active)||[],errors=tags.flatMap(t=>validateTag(t).map(e=>t.sector_name+': '+e));if(!tags.length||errors.length){byId('status').textContent=errors.join('\\n')||'먼저 테마를 선택하세요.';return;}tags.forEach(t=>t.confirmed=true);renderTags();byId('status').textContent='현재 종목의 태그를 직접 확인한 것으로 표시했습니다. DB에는 저장하지 않았습니다.';};
byId('export').onclick=()=>{const result=buildExport();if(result.issues.length||!result.rows.length){byId('status').textContent=result.issues.join('\\n')||'내보낼 확인 완료 태그가 없습니다.';return;}const url=URL.createObjectURL(new Blob([result.csv],{type:'text/csv;charset=utf-8'})),a=node('a');a.href=url;a.download='stock1-sector-tags-'+new Date().toISOString().replaceAll(/[:.]/g,'-')+'.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);byId('status').textContent=`확인한 ${result.rows.length}행 CSV 다운로드 · 미확인 ${result.unconfirmed}행 제외. 다운로드 파일로 dry-run을 실행하세요.`;};
for(const source of data.sources){const option=node('option');option.value=source;byId('sources').append(option);}
byId('metadata').textContent=`${data.environment==='mock'?'모의':'실전'} 관측 · 생성 ${data.generated_at} · 최근 7일 ${data.recent_since} ~ ${data.generated_at} · DB 변경 없음`;
byId('priority-context').textContent=`CSV 검토 가능 ${data.stocks.length}종목 · 최신 운영 TOP20 ${data.latest_ranking?.collected_at||'관측 없음'} · 해당 회차 미분류 ${data.latest_unclassified_count}종목 (CSV 검토 ${data.latest_processable_count} / 코드 보류 ${data.latest_unclassified_count-data.latest_processable_count})`;
byId('unsupported-title').textContent=`CSV 코드 호환성 검토 · 읽기 전용 보류 ${data.unsupported_stocks.length}종목`;
if(data.unsupported_stocks.length){const table=node('table'),head=node('tr');for(const text of ['종목','최근 TOP20 / TOP5 / TOP10','최근 등장','최신 회차 상태','처리 상태'])head.append(node('th',text));table.append(head);for(const stock of data.unsupported_stocks){const row=node('tr');for(const text of [`${stock.stock_code} · ${stock.stock_name}`,`${stock.recent_ranking_appearances} / ${stock.recent_top5_appearances} / ${stock.recent_top10_appearances}`,stock.last_ranking_date||'없음',entryLabel(stock),stock.reason])row.append(node('td',text));table.append(row);}byId('unsupported-stocks').append(table);}else byId('unsupported-stocks').append(node('p','현재 지원 코드 보류 목록이 없습니다.'));
byId('csv-workflow').textContent=`# 파일 경로는 실제 다운로드한 CSV 이름으로 바꾸세요.\n# 1. 읽기 전용 검증\npython -B -m kiwoom_collector sector import ./data/reviewed-tags.csv --environment ${data.environment} --db ./data/stock1.db --dry-run\n\n# 2. errors/conflicts와 근거를 검토하고 별도 승인 후에만 실제 적용\npython -B -m kiwoom_collector sector import ./data/reviewed-tags.csv --environment ${data.environment} --db ./data/stock1.db\n\n# 3. 실제 적용 이력이 있을 때만 결과 확인\npython -B -m kiwoom_collector sector import-report latest --db ./data/stock1.db`;
renderStocks();renderActive();progress();
</script></body></html>'''
