"""Generate a local classification workspace. No DB writes, server, or API calls."""
from datetime import datetime, timedelta
import json

from .client import KST
from .external_tags import exclusive_output, priority_rows, tables
from .summary import _rows, read_database


def workspace_data(path, environment='mock', *, now=None):
    if environment not in {'mock', 'real'}:
        raise ValueError('환경은 mock 또는 real이어야 합니다.')
    at = (now or datetime.now(KST)).astimezone(KST)
    since = at - timedelta(days=7)
    with read_database(path) as (db, _):
        stocks, excluded = priority_rows(db, environment)
        counts = {r['stk_cd']: r['count'] for r in _rows(db, '''
            SELECT r.stk_cd,COUNT(DISTINCT c.id) AS count FROM ranking_records r
            JOIN collections c ON c.id=r.collection_id WHERE c.status='success'
            AND c.api_id='ka00198' AND c.environment=? AND c.test_mode=0
            AND c.collected_at>=? AND c.collected_at<=? GROUP BY r.stk_cd''',
            (environment, since.isoformat(), at.isoformat()))}
        for stock in stocks:
            stock['recent_ranking_appearances'] = counts.get(stock['stock_code'], 0)
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
                'excluded_unsupported_code_count':excluded}


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
</style></head><body><main>
<h1>stock1 · 미분류 종목 작업실</h1><p id="metadata" class="notice"></p>
<p class="muted">이 화면은 생성 시점의 DB 스냅샷입니다. 기존 태그를 삭제하거나 덮어쓰지 않습니다. 선택은 이 창에만 보관되며 창을 닫거나 새로고침하면 사라집니다. 작업 중에는 CSV를 다운로드해 보관하세요.</p>
<section><div class="toolbar"><button id="export" class="primary" disabled>확인한 태그 CSV 다운로드</button><span id="progress" aria-live="polite"></span></div><p id="status" role="status">종목과 테마를 선택한 뒤 출처·확인일·이용조건을 직접 확인하세요.</p><p class="muted">미확인 태그는 내보내지 않습니다. 확인 표시를 했어도 필수 값이 잘못되면 내보내기를 중단합니다. CSV 적용은 PowerShell의 sector import --dry-run → sector import로 진행하세요. 이 화면은 DB에 직접 적용하지 않습니다.</p></section>
<div class="layout"><aside><h2>분류 우선순위</h2><label>종목 검색<input id="stock-filter" placeholder="종목코드 또는 종목명" type="search"></label><p id="stock-count" class="muted"></p><div id="stocks" class="stock-list"></div></aside>
<div><section><h2 id="stock-title">종목 선택</h2><p id="stock-facts" class="muted"></p>
<label>기존 테마 검색 · 별칭도 검색 가능<input id="theme-search" type="search" placeholder="테마명 검색"></label><div id="catalog" class="catalog"></div>
<div class="toolbar"><input id="new-theme" aria-label="새 테마명" placeholder="새 테마명 (CSV 적용 때 등록)"><button id="add-theme">새 테마 선택</button></div>
<p class="muted">기존 테마의 괄호 숫자는 현재 DB 소속 종목 수입니다. 한 종목에 여러 테마를 선택할 수 있습니다. 별칭을 검색하면 정식 이름을 선택하며 자동 추론·병합하지 않습니다.</p></section>
<section><details><summary>반복 입력용 메타데이터 (확인 표시 자동 복사 안 함)</summary><div id="defaults" class="fields"></div><button id="copy-defaults">현재 종목의 미확인 태그에 입력값 복사</button></details><div class="toolbar"><h2>이번 CSV의 태그</h2><button id="confirm-stock">현재 종목 태그를 직접 확인했음</button></div><div id="tags"></div></section>
</div></div>
<datalist id="sources"></datalist>
<section><h2>데이터 범위와 적용 절차</h2><p>우선순위는 sector export-priority와 같습니다. 환경별 전체 성공 [0198]·조건검색 이력의 미분류 합집합이며 테스트 회차는 제외합니다. 최근 7일 횟수는 아래 생성 시각까지의 성공 [0198] 회차를 다시 센 값입니다. 오래된 종목도 누적 우선순위에 남을 수 있습니다. 조건검색 등장 여부는 수집된 결과에서의 등장 여부이며 검색 자체의 실행 여부를 뜻하지 않습니다.</p><p>CSV는 UTF-8 BOM이며 stock_code,stock_name,sector_name,source,observed_at,terms_url,terms_note,terms_checked_at 형식입니다. 여러 테마는 여러 행입니다. 종목코드는 문자열로 유지하므로 Excel에서는 텍스트 열로 가져오세요. 새 테마는 실제 import 때 생성되고 기존 종목명·태그는 보존됩니다. 이용조건의 진위·허가 범위를 시스템이 검증하지 않으므로 사용자가 직접 확인해야 합니다.</p><p class="warn">브라우저의 선택 해제는 이번 CSV에서만 제외합니다. CSV에 없는 기존 태그는 삭제되지 않습니다. 다운로드 후에도 원본 DB가 바뀔 수 있으므로 반드시 dry-run 결과를 다시 검토하세요.</p></section>
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
function renderStocks(){const filter=byId('stock-filter').value.trim().toLowerCase();byId('stocks').replaceChildren();const filtered=data.stocks.filter(s=>(s.stock_code+' '+s.stock_name).toLowerCase().includes(filter));byId('stock-count').textContent=`전체 ${data.stocks.length}종목 / 검색 ${filtered.length}종목`;for(const s of filtered){const b=node('button');b.className='stock'+(s.stock_code===active?' active':'');b.append(node('strong',`${s.stock_code} · ${s.stock_name||'이름 미제공'}`),node('span',`최근 7일 [0198] ${s.recent_ranking_appearances}회 · 누적 ${s.ranking_appearances}회`),node('span',`조건검색 ${s.condition_appearances?'등장 '+s.condition_appearances+'회':'등장 기록 없음'} · 선택 ${(drafts.get(s.stock_code)||[]).length}태그`));b.onclick=()=>{active=s.stock_code;renderStocks();renderActive();};byId('stocks').append(b);}if(!filtered.length)byId('stocks').append(node('p','해당 미분류 종목이 없습니다.'));}
function selectTheme(name){name=name.trim();if(!active||!validLabel(name)){byId('status').textContent='종목을 선택하고 비어 있지 않은 테마명을 입력하세요.';return;}const tags=drafts.get(active)||[];if(tags.some(t=>t.sector_name===name)){byId('status').textContent='이미 선택한 테마입니다.';return;}tags.push({sector_name:name,...defaults,confirmed:false});drafts.set(active,tags);if(!catalog.has(name))catalog.set(name,null);byId('new-theme').value='';renderStocks();renderActive();progress();}
function renderCatalog(){const term=byId('theme-search').value.trim().toLowerCase();byId('catalog').replaceChildren();for(const [name,count] of catalog){if(!name.toLowerCase().includes(term)&&!data.aliases.some(a=>a.sector_name===name&&a.alias.toLowerCase().includes(term)))continue;const b=node('button',name+(count===null?' · 새 테마':` (${count})`));b.disabled=!active||(drafts.get(active)||[]).some(t=>t.sector_name===name);b.onclick=()=>selectTheme(name);byId('catalog').append(b);}if(!byId('catalog').childElementCount)byId('catalog').append(node('p','일치하는 테마가 없습니다. 새 테마명을 직접 입력할 수 있습니다.'));}
function metaInputs(parent,model,onchange,prefix){for(const [key,title,type] of metaFields){const label=node('label',title),input=node('input');input.type=type;input.value=model[key];input.dataset.field=key;input.id=prefix+'-'+key;if(['source','terms_note'].includes(key))input.maxLength=200;if(key==='source')input.setAttribute('list','sources');input.oninput=()=>{model[key]=input.value;onchange();};label.append(input);parent.append(label);}}
function renderTags(){const area=byId('tags');area.replaceChildren();const tags=drafts.get(active)||[];for(const [index,tag] of tags.entries()){const card=node('div');card.className='tag';card.dataset.theme=tag.sector_name;const title=node('h3',tag.sector_name),fields=node('div');fields.className='fields';const check=node('input');check.type='checkbox';check.checked=tag.confirmed;check.dataset.field='confirmed';const status=node('span');const refresh=()=>{check.checked=tag.confirmed;status.textContent=tag.confirmed?(validateTag(tag).length?'확인 내용 수정 필요':'확인 표시됨'):'미확인 · CSV에서 제외';progress();};metaInputs(fields,tag,()=>{tag.confirmed=false;refresh();},'tag-'+index);const label=node('label');label.append(check,document.createTextNode(' 이 테마의 출처 자료와 이용조건을 직접 확인했습니다'));check.onchange=()=>{tag.confirmed=check.checked;refresh();};const remove=node('button','이번 CSV 선택 해제');remove.onclick=()=>{tags.splice(index,1);renderStocks();renderActive();progress();};card.append(title,fields,label,status,document.createTextNode(' '),remove);area.append(card);refresh();}if(!tags.length)area.append(node('p','기존 테마를 선택하거나 새 테마명을 입력하세요.'));}
function renderActive(){const stock=data.stocks.find(s=>s.stock_code===active);byId('stock-title').textContent=stock?`${stock.stock_code} · ${stock.stock_name||'이름 미제공'}`:'미분류 대상 없음';byId('stock-facts').textContent=stock?`최근 7일 [0198] ${stock.recent_ranking_appearances}회 / 누적 ${stock.ranking_appearances}회 · 조건검색 누적 ${stock.condition_appearances}회 (${stock.condition_aliases.join(', ')||'등장 기록 없음'}) · 최종 관측 ${stock.last_seen} · 마스터 ${stock.in_master?'등록':'미등록 (이름 확인 필요)'}`:'';byId('confirm-stock').disabled=!stock;byId('copy-defaults').disabled=!stock;byId('add-theme').disabled=!stock;renderCatalog();renderTags();}
byId('stock-filter').oninput=renderStocks;byId('theme-search').oninput=renderCatalog;
byId('add-theme').onclick=()=>{const name=byId('new-theme').value.trim();if(data.aliases.some(a=>a.alias===name&&a.sector_name!==name)){byId('status').textContent='이미 등록된 별칭입니다. 기존 테마 검색에서 정식 이름을 선택하세요.';return;}selectTheme(name);};
metaInputs(byId('defaults'),defaults,()=>{},'default');
byId('copy-defaults').onclick=()=>{for(const tag of drafts.get(active)||[])if(!tag.confirmed)Object.assign(tag,defaults);renderTags();byId('status').textContent='미확인 태그에 입력값만 복사했습니다. 직접 확인 표시가 필요합니다.';};
byId('confirm-stock').onclick=()=>{const tags=drafts.get(active)||[],errors=tags.flatMap(t=>validateTag(t).map(e=>t.sector_name+': '+e));if(!tags.length||errors.length){byId('status').textContent=errors.join('\\n')||'먼저 테마를 선택하세요.';return;}tags.forEach(t=>t.confirmed=true);renderTags();byId('status').textContent='현재 종목의 태그를 직접 확인한 것으로 표시했습니다. DB에는 저장하지 않았습니다.';};
byId('export').onclick=()=>{const result=buildExport();if(result.issues.length||!result.rows.length){byId('status').textContent=result.issues.join('\\n')||'내보낼 확인 완료 태그가 없습니다.';return;}const url=URL.createObjectURL(new Blob([result.csv],{type:'text/csv;charset=utf-8'})),a=node('a');a.href=url;a.download='stock1-sector-tags-'+new Date().toISOString().replaceAll(/[:.]/g,'-')+'.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);byId('status').textContent=`확인한 ${result.rows.length}행 CSV 다운로드 · 미확인 ${result.unconfirmed}행 제외. 다운로드 파일로 dry-run을 실행하세요.`;};
for(const source of data.sources){const option=node('option');option.value=source;byId('sources').append(option);}
byId('metadata').textContent=`${data.environment==='mock'?'모의':'실전'} 관측 · 생성 ${data.generated_at} · 최근 7일 ${data.recent_since} ~ ${data.generated_at} · DB 변경 없음`;
renderStocks();renderActive();progress();
</script></body></html>'''
