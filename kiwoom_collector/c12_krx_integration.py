"""AR-1 read-only reference overlay. Never changes C12 observations or decisions."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal
from html import escape
import json
from pathlib import Path
import shutil

from .am_candidate_adapter import normalize_code
from .c12_latest import read_db, digest
from .equity_eligibility import EXCLUDED_TYPES
from .krx_adr import sha
from . import master_data

ROOT = master_data.PROJECT/'diagnostics/c12-ar1'
MASTER = master_data.PROJECT/'diagnostics/master/krx-official-20261030.db'
AQ = master_data.PROJECT/'diagnostics/krx-aq/20261104-v2'


def references(path):
    """Reuse AO's confirmed exact matches and original, unmerged theme relations."""
    with read_db(path) as db:
        meta = dict(db.execute('SELECT * FROM ao_meta').fetchone())
        if meta['synthetic'] != 0:
            raise ValueError('live_official_reference_required')
        securities = {r['stock_code']:dict(r) for r in db.execute('SELECT * FROM krx_securities')}
        links = defaultdict(list)
        for r in db.execute('''SELECT r.*,m.basis_date,m.historical_identity_verified,m.state
            FROM external_theme_relations r JOIN external_name_matches m ON r.raw_name=m.raw_name
            WHERE m.confirmed=1 AND m.review_required=0 AND r.stock_code=m.stock_code'''):
            item=dict(r)
            item['original_relation']=json.loads(item.pop('source_json'))
            item['source_kind']='external_trader_close'
            item['historical_membership_verified']=False
            links[r['stock_code']].append(item)
        confirmed=db.execute('SELECT COUNT(*) FROM external_name_matches WHERE confirmed=1').fetchone()[0]
    return dict(securities=securities,themes=dict(links),basis_date=meta['basis_date'],
                confirmed_names=confirmed,confirmed_relations=sum(map(len,links.values())),
                source_sha256=sha(path),theme_source_sha256=meta['theme_sha256'])


def observations(document):
    """Accept the existing AG watchlist or AM input; retain invalid/unmatched rows."""
    watch=document.get('observations',{}).get('watchlist')
    if watch is not None:
        return deepcopy(watch)
    rows=[]
    for snapshot in document.get('rankings',[]):
        rows += [dict(stock_code=r['stk_cd'],name=r.get('stk_nm'),api='0198',rank=r.get('rank')) for r in snapshot['stocks']]
    for r in document.get('source_records',[]):
        if r.get('kind')=='turnover':
            rows.append(dict(stock_code=r.get('stock_code'),name=r.get('name'),api='0186',rank=r.get('turnover_rank')))
    if not rows:
        rows=[dict(stock_code=r['stock_code'],name=r.get('name')) for r in document.get('analysis',{}).get('rows',document.get('rows',[]))]
    return rows


def product_reference(stock):
    if stock is None:
        return dict(state='unmatched',reason='official_code_unmatched',trade_eligibility='unchanged')
    kind=stock['product_type']
    if not stock['type_verified'] or kind=='UNKNOWN':
        state,reason='pending','official_product_unknown'
    elif kind in EXCLUDED_TYPES or kind=='preferred_stock':
        state,reason='excluded','reference_product:'+kind
    elif kind=='common_stock':
        state,reason='common_reference','official_common_stock_snapshot'
    else:
        state,reason='pending','unsupported_product'
    return dict(state=state,reason=reason,trade_eligibility='unchanged',
                note='snapshot product reference is not a current listing, liquidity or order approval')


def enrich(document, reference, adr=None):
    before=digest(document)
    cutoff=document.get('as_of') or document.get('selection_cutoff_at')
    if not cutoff:
        raise ValueError('c12_analysis_cutoff_required')
    analysis_day=date.fromisoformat(cutoff[:10]).isoformat()
    unique={}
    for i,r in enumerate(observations(document)):
        raw=r.get('stock_code',r.get('stk_cd'));code=normalize_code(raw)
        key=code or 'invalid:'+str(i)
        item=unique.setdefault(key,dict(stock_code=code,raw_codes=[],source_observations=[]))
        if raw not in item['raw_codes']:item['raw_codes'].append(raw)
        item['source_observations'].append(deepcopy(r))
    rows=[]
    for item in unique.values():
        code=item['stock_code'];stock=reference['securities'].get(code)
        links=deepcopy(reference['themes'].get(code,[])) if stock else []
        official=None if stock is None else {k:deepcopy(stock[k]) for k in (
            'stock_code','standard_code','market','official_name','abbreviated_name','product_type',
            'type_verified','basis_date','listing_date','classification_json')}
        names={r.get('name',r.get('stk_nm')) for r in item['source_observations']} - {None,''}
        agreement=None if not stock or not names else all(n in (stock['official_name'],stock['abbreviated_name']) for n in names)
        rows.append(dict(**item,identity_status='official_code_match' if stock else 'unmatched',
            official=official,name_agrees_with_snapshot=agreement,product_reference=product_reference(stock),
            raw_theme_labels=list(dict.fromkeys(r['raw_theme'] for r in links)),theme_references=links,
            retained_in_attention=True,existing_decisions_unchanged=True))
    adr_view=dict(state='unavailable',reason='ADR 자료 없음 · 0으로 대체하지 않습니다.',latest={},analysis_day=analysis_day)
    if adr:
        latest=deepcopy(adr['report']['latest'])
        basis=latest['COMBINED']['common']['day']
        delta=(date.fromisoformat(analysis_day)-date.fromisoformat(basis)).days
        relation='same_day' if delta==0 else 'older_than_analysis' if delta>0 else 'after_analysis_reference_only'
        asof={m:next((deepcopy(r) for r in reversed(adr['series']) if r['market']==m and r['universe']=='common' and r['day']<=analysis_day),None)
              for m in ('KOSPI','KOSDAQ','COMBINED')}
        adr_view=dict(state='available',basis_date=basis,analysis_day=analysis_day,date_relation=relation,
            calendar_day_gap=delta,latest=latest,asof_analysis=asof,
            status='provisional',automatic_signal=False,policy=adr['report']['policy'])
    result=dict(schema_version='c12-ar1-reference-overlay-v1',analysis_as_of=cutoff,
        analysis_day=analysis_day,c12_source_kind=document.get('source_kind','unspecified'),
        environment=document.get('environment'),c12=deepcopy(document),c12_sha256=before,
        official_basis_date=reference['basis_date'],master_time_relation=(
            'after_analysis_reference_only' if reference['basis_date']>analysis_day else 'dated_snapshot_reference'),
        confirmed_theme_names=reference['confirmed_names'],confirmed_theme_relations=reference['confirmed_relations'],
        master_sha256=reference.get('source_sha256'),theme_source_sha256=reference.get('theme_source_sha256'),
        rows=rows,counts=dict(observed=len(rows),code_matched=sum(r['official'] is not None for r in rows),
            unmatched=sum(r['official'] is None for r in rows),theme_mapped=sum(bool(r['raw_theme_labels']) for r in rows),
            multi_theme=sum(len(r['raw_theme_labels'])>1 for r in rows),
            product_states=dict(Counter(r['product_reference']['state'] for r in rows))),
        adr=adr_view,ak_comparison=dict(state='not_reproducible_without_original_47_stock_snapshot',
            reported_baseline=24,reported_total=47,recalculated_baseline=None,new_mapped=None),
        operational_writes=0,api_calls=0,telegram_sent=0,d0_writes=0)
    if digest(document)!=before or result['c12']!=document:
        raise ValueError('c12_input_changed')
    return result


def load_adr(folder):
    """Read existing AQ results and cross-check every saved value against AQ SQLite."""
    folder=Path(folder)
    if not all((folder/name).is_file() for name in ('adr20.json','adr20.db','adr20.html')):
        return None
    payload=json.loads((folder/'adr20.json').read_text(encoding='utf8'))
    bykey={(r['day'],r['market'],r['universe']):r for r in payload['series']}
    with read_db(folder/'adr20.db') as db:
        rows=db.execute('SELECT day,market,universe,value,status FROM adr20').fetchall()
        if len(rows)!=len(bykey):raise ValueError('aq_database_json_mismatch')
        for r in rows:
            value=bykey[(r['day'],r['market'],r['universe'])]
            if (r['value'],r['status'])!=(value['value'],value['status']):
                raise ValueError('aq_database_json_mismatch')
    return payload


CSS='''<style>
.ar1{margin:18px;background:white;border:1px solid #dce5ed;border-radius:12px;padding:18px;color:#26394d;font:13px/1.5 system-ui,"Malgun Gothic",sans-serif}.ar1 h2{margin:0 0 8px;font-size:18px}.ar1 p{margin:6px 0}.ar1 .note{color:#65778a}.ar1 .date-warning{background:#fff5e2;border:1px solid #ead9b1;border-radius:7px;padding:10px}.ar1 .metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0}.ar1 .metric{background:#f5f8fb;padding:12px;border-radius:8px}.ar1 .metric b{display:block;font-size:27px}.ar1 .metric small{display:block}.ar1 .scroll{overflow:auto;max-height:480px}.ar1 table{border-collapse:collapse;width:100%;min-width:850px}.ar1 td,.ar1 th{padding:8px;border-bottom:1px solid #e2e8ef;text-align:left;vertical-align:top}.ar1 th{background:#f5f8fb;position:sticky;top:0}.ar1 details{margin-top:10px}.ar1 iframe{width:100%;height:1650px;border:0;border-radius:8px}.ar1 a{color:#245aa7}.ar1 .pending{color:#98601c}@media(max-width:650px){.ar1{margin:10px;padding:12px}.ar1 .metrics{gap:6px}.ar1 .metric{padding:9px}.ar1 .metric b{font-size:22px}.ar1 iframe{height:2100px}}
</style>'''


def section(bundle, *, chart_name='adr20.html'):
    e=lambda v:escape(str(v),quote=True)
    number=lambda v:'—' if v is None else f'{Decimal(v):.4f}'
    adr=bundle['adr']
    source_label={'stored_mock_0198_reference_not_live_or_fixture':'저장된 모의 0198 관측',
                  'fixture_existing_demo_top5':'기존 합성 DEMO Top5', 'fixture':'합성 테스트',
                  'readonly_adapter':'읽기 전용 저장 스냅샷'}.get(bundle['c12_source_kind'],'저장된 C12 분석')
    master_relation='분석일 이후 마스터 · 회고 참조' if bundle['master_time_relation']=='after_analysis_reference_only' else '기준일 스냅샷 참조'
    parts=['<section class="ar1" id="krx-reference"><h2>KRX 공식 식별 · 외부 테마 참조</h2>',
        '<p class="date-warning">기존 C12 관심도·주도섹터 TOP3·핵심 종목 및 매매 적격성 결과를 유지합니다. 아래 연결은 회고 참조이며 새로운 매수 후보를 만들지 않습니다.</p>',
        '<p>C12 분석 기준 '+e(bundle['analysis_as_of'])+' · '+e(source_label)+
        ' · KRX 마스터 '+e(bundle['official_basis_date'])+' ('+e(master_relation)+')</p>',
        '<p>관측 '+str(bundle['counts']['observed'])+' · 코드 연결 '+str(bundle['counts']['code_matched'])+
        ' · 미연결 '+str(bundle['counts']['unmatched'])+' (관심도 관측 유지) · 테마 연결 '+str(bundle['counts']['theme_mapped'])+
        ' · 복수 테마 '+str(bundle['counts']['multi_theme'])+'</p>',
        '<p class="note">원문 테마를 유사명·부모명으로 통합하지 않았습니다. 확정 이름 '+str(bundle['confirmed_theme_names'])+
        '개 / 연결 관계 '+str(bundle['confirmed_theme_relations'])+'개. 테마 소속과 과거 시점의 매매 적격성은 확정하지 않습니다.</p>',
        '<details><summary>관측 종목별 공식 식별 · 상품 정책 · 원문 테마·출처</summary><div class="scroll"><table><thead><tr><th>관측 코드/이름</th><th>시장·공식명/약명</th><th>상품 참조</th><th>원문 테마</th><th>근거</th></tr></thead><tbody>']
    for r in bundle['rows']:
        stock=r['official']
        names=' / '.join(dict.fromkeys(str(o.get('name',o.get('stk_nm')) or '') for o in r['source_observations']))
        identity='미매칭 · 관측 유지' if not stock else stock['market']+' · '+stock['official_name']+' / '+stock['abbreviated_name']
        product='미확인' if not stock else {'common_stock':'보통주','preferred_stock':'우선주','UNKNOWN':'미확정 상품'}.get(stock['product_type'],stock['product_type'])
        product_state={'common_reference':'보통주 참조 · 매매 적격 미확정','excluded':'기본 상품 범위 제외',
                       'pending':'상품 검토 대기','unmatched':'공식 코드 미연결'}[r['product_reference']['state']]
        proof='<details><summary>출처 '+str(len(r['theme_references']))+'개</summary><pre style="white-space:pre-wrap">'+e(json.dumps(r['theme_references'],ensure_ascii=False,indent=2))+'</pre></details>' if r['theme_references'] else '연결 근거 없음'
        parts.append('<tr><td>'+e(r['stock_code'] or r['raw_codes'])+'<br>'+e(names)+'</td><td>'+e(identity)+
            '</td><td>'+e(product)+'<br>'+e(product_state)+'</td><td>'+e(' / '.join(r['raw_theme_labels']) or '미연결')+'</td><td>'+proof+'</td></tr>')
    parts.append('</tbody></table></div></details><p class="note">AK 47종목 원본 입력 미확보: 전달 기준 24/47의 신규 개선율은 재현 불가입니다. 현재 입력의 연결 수와 혼동하지 않습니다.</p></section>')
    parts.append('<section class="ar1" id="adr-reference"><h2>시장 폭 · ADR20</h2>')
    if adr['state']!='available':
        parts.append('<p class="date-warning">'+e(adr['reason'])+'</p></section>')
        return ''.join(parts)
    relation={'same_day':'분석일과 같은 날짜의 저장 자료', 'older_than_analysis':'과거 저장 ADR · 오늘 실시간 값 아님',
              'after_analysis_reference_only':'분석일 이후의 최신 자료 · 당시 의사결정에 사용 불가'}[adr['date_relation']]
    parts.append('<p class="date-warning">ADR 기준일 '+e(adr['basis_date'])+' · C12 분석일 '+e(adr['analysis_day'])+
                 ' · '+e(relation)+' · 잠정(provisional)</p><div class="metrics">')
    for market in ('KOSPI','KOSDAQ','COMBINED'):
        row=adr['latest'][market]['common']
        parts.append('<div class="metric">'+e('통합' if market=='COMBINED' else market)+'<b>'+number(row['value'])+
                     '</b><small>'+e(row['day'])+' · '+e(row['status'])+'</small></div>')
    parts.append('</div><p class="note">70·100·130은 경험적 참고선입니다. 자동 매수·매도 신호가 아닙니다. 기존 AQ 계산·차트를 그대로 재사용합니다.</p>')
    context=adr['asof_analysis'].get('COMBINED')
    if context:
        parts.append('<p>분석일 이하 통합 ADR: '+number(context['value'])+' · '+e(context['day'])+' · '+e(context['status'])+' (현재 보유한 과거 정정자료 기준)</p>')
    parts.append('<p><a href="'+e(chart_name)+'" target="_blank" rel="noopener">독립 ADR 그래프 열기 · 3개월 / 6개월 / 1년</a></p>'+
        '<iframe id="adr-chart" title="KRX ADR20 · 독립 그래프" src="'+e(chart_name)+'" sandbox="allow-scripts" loading="lazy"></iframe></section>')
    return ''.join(parts)


def attach_dashboard(base_html,bundle,*,chart_name='adr20.html'):
    if base_html.count('</main>')!=1:
        raise ValueError('single_dashboard_main_required')
    # No rewrites of original focus cards, iframe contents, selectors or analysis.
    return base_html.replace('</head>',CSS+'</head>',1).replace('</main>',section(bundle,chart_name=chart_name)+'</main>',1)


def export(document,base_html,output,*,master=MASTER,aq=AQ):
    output=Path(output).absolute()
    if output.resolve()!=output or ROOT.resolve()!=ROOT.absolute() or not output.is_relative_to(ROOT.absolute()) or output.exists():
        raise ValueError('new_ar1_diagnostics_directory_required')
    reference=references(master);adr=load_adr(aq)
    bundle=enrich(document,reference,adr)
    html=attach_dashboard(base_html,bundle)
    output.mkdir(parents=True)
    if adr:
        # Copy byte-for-byte; AQ remains independently usable and untouched.
        shutil.copyfile(Path(aq)/'adr20.html',output/'adr20.html')
    (output/'dashboard.html').write_text(html,encoding='utf8')
    (output/'integration.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2),encoding='utf8')
    return bundle


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--c12-json',type=Path,required=True);p.add_argument('--base-html',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    bundle=export(json.loads(a.c12_json.read_text(encoding='utf8')),a.base_html.read_text(encoding='utf8'),a.output)
    print(json.dumps(bundle['counts']))


if __name__=='__main__':main()
