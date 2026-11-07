"""C15-8 saved-slot adapter for the existing AJ theme engine. No I/O writers."""
import json
from decimal import Decimal

from .am_momentum import number
from .am_candidate_adapter import normalize_code
from .c12_external_theme_bridge import label_view, reference_match
from .c12_theme_audit import analyze_themes
from .equity_eligibility import EXCLUDED_TYPES


def map_themes(stocks, records, day, *, official_records=None, as_of=None):
    """Reuse exact code/name matching; a supplied code never falls back to name.

    Historical observations are reference memberships, not verified permanent
    classifications. Dated rows only: aggregate tables may include future edges.
    """
    tags, matches = [], {}
    official_matches = {}
    if official_records is not None:
        from .c15_identity import exact_code
        from .am_time_policy import instant
        from .krx_master import match_names, normalized_name
        if as_of is None: raise ValueError('official_mapping_requires_cutoff')
        official_records = [r for r in official_records if r['basis_date']<=day and instant(r['available_at'])<=instant(as_of)]
        official_matches = {r['raw_name']:r for r in match_names(
            [r['raw_stock_name'] for r in records if r['trade_date']<=day], official_records, basis_date=day)}
    watched = {s['stock_code']:s for s in stocks}
    official_by_code = {r['stock_code']:r for r in official_records or []}
    for record in records:
        if record['trade_date'] > day or (official_records is not None and record['trade_date']==day):
            continue
        raw_code = record.get('raw_stock_code')
        code = ((exact_code(raw_code, official_records, record.get('market')) if official_records is not None else normalize_code(raw_code))
                if raw_code not in (None, '') else None)
        if raw_code not in (None, ''):
            if code not in watched:
                continue
            kind = 'code'
        else:
            official = official_matches.get(record['raw_stock_name'], {})
            if official.get('confirmed'):
                code = official['stock_code']
                if code not in watched: continue
                target = official_by_code[code]
                if normalized_name(watched[code]['name']) not in {
                        normalized_name(target['official_name']), normalized_name(target['abbreviated_name'])}:
                    continue  # Observed code/name conflicts cannot become verified names.
                kind = 'official_master_name'
            elif official.get('state')=='multiple_candidates':
                continue
            else:
                match = reference_match(record, stocks)
                code = match.get('watchlist_stock_code')
                kind = 'exact_name_reference'
        if not code:
            continue
        matches.setdefault(code, set()).add(kind)
        tag = dict(stock_code=code, sector_name=label_view(record['raw_theme'])['name'],
                   source='historical_theme_master_'+kind)
        if kind=='official_master_name':
            tag.update(identity_basis=official_by_code[code]['name_basis'],
                       official_basis_date=official_by_code[code]['basis_date'],
                       identity_available_at=official_by_code[code]['available_at'])
        tags.append(tag)
    return tags, matches


def analyze(report, records, products, *, day, policy, official_records=None):
    if official_records is not None:
        from .am_time_policy import instant
        at=instant(report['as_of'])
        official_records=[r for r in official_records if r['basis_date']<=day and instant(r['available_at'])<=at]
        products={r['stock_code']:r for r in official_records}
    selected = report['snapshot_selection']['selected']
    # Only fresh exact-slot snapshots supplied by C15-7 are admitted. No TTL
    # fallback or independent DB loader here.
    rank = selected['0198']['snapshot'] if selected['0198']['status']=='fresh' else None
    turn = selected['0186']['snapshot'] if selected['0186']['status']=='fresh' else None
    stocks = {r['stk_cd']:dict(stock_code=r['stk_cd'], name=r['stk_nm'], ranking_rank=r['rank'])
              for r in (rank or {}).get('rows', [])}
    for r in (turn or {}).get('rows', []):
        if r.get('validation_status') != 'syntax_valid': continue
        raw = json.loads(r['raw_json'])
        if raw.get('stk_cd') != r['stock_code']: continue
        s = stocks.setdefault(r['stock_code'], dict(stock_code=r['stock_code'], name=raw.get('stk_nm'), ranking_rank=None))
        s.update(raw_change=raw.get('flu_rt'), turnover_rank=r['turnover_rank'],
                 raw_turnover=raw.get('trde_prica'))
    decisions = report.get('candidate_filter', {}).get('decisions', {})
    candidates = {c['stock_code']:c for c in report.get('candidates', [])}
    official_by_code = {r['stock_code']:r for r in official_records or []}
    for code, s in stocks.items():
        d = decisions.get(code, {})
        product = products.get(code, {})
        s['product_reference'] = product
        s['product_status'] = ('preferred_hold' if product.get('product_type')=='preferred_stock' else
                               product.get('product_type') or 'UNKNOWN')
        if official_records is not None:
            from .c15_identity import exact_code
            from .krx_master import normalized_name
            official=official_by_code.get(code)
            s['code_validation']=dict(syntax_valid=exact_code(code)==code,
                market=official['market'] if official else None,
                official_name_consistent=(normalized_name(s['name']) in {
                    normalized_name(official['official_name']),normalized_name(official['abbreviated_name'])}) if official else None,
                namespace='six_character_short_code_not_market_inferred')
        s['explicitly_excluded'] = (d.get('state') in ('confirmed_excluded', 'provisional_excluded') or
                                    product.get('product_type') in EXCLUDED_TYPES)
        c = candidates.get(code, {})
        cap = c.get('metrics', {}).get('market_cap')
        s['capital_status'] = ('above_minimum' if Decimal(cap['value']) > Decimal(3000)*100000000 else 'below_minimum') if cap else 'unverified'
        # Reuse the C7 strict verified candidate result. KRX prior-day common
        # stock is useful reference evidence, never an invented cap approval.
        s['eligibility_status'] = ('excluded' if s['explicitly_excluded'] else
                                   'eligible' if c.get('status')=='priority' else 'pending')
        s['turnover_won'] = (c.get('metrics', {}).get('turnover') or {}).get('value')
        s['change_status'] = 'reported_saved_0186_not_runtime_unit_verified' if number(s.get('raw_change')) is not None else 'missing'
    values = list(stocks.values())
    tags, matches = map_themes(values, records, day, official_records=official_records, as_of=report.get('as_of'))
    # Excluded instruments remain in mapping coverage but cannot contribute to
    # scores or companion counts. Pending classifications stay explicitly held.
    mapping = analyze_themes(values, tags)
    observed = [s for s in values if not s['explicitly_excluded'] and
                not (official_records is not None and s['product_status']=='preferred_hold')]
    analysis = analyze_themes(observed, tags, rising_policy=policy)['rising_analysis']
    excluded = [s for s in values if s['explicitly_excluded']]
    mapping['coverage'].update(code_mapped=sum('code' in v for v in matches.values()),
                               official_name_mapped=sum('code' not in v and 'official_master_name' in v for v in matches.values()),
                               name_only_mapped=sum('code' not in v and 'official_master_name' not in v for v in matches.values()))
    ranking_codes={r['stk_cd'] for r in (rank or {}).get('rows', [])}
    turnover_codes={r['stock_code'] for r in (turn or {}).get('rows', []) if r['stock_code'] in stocks}
    return dict(schema_version='c15-8-leadership-v1', mapping=mapping, analysis=analysis,
                stocks=values, excluded_stocks=excluded,
                preferred_stocks=[s for s in values if s['product_status']=='preferred_hold'],
                unknown_type_stocks=[s for s in values if s['product_status']=='UNKNOWN'],
                denominator=dict(ranking_unique=len(ranking_codes),turnover_unique=len(turnover_codes),
                    intersection=len(ranking_codes & turnover_codes),union=len(stocks),
                    basis='fresh_exact_slot_valid_rows_unique_stock_code_before_product_exclusion'),
                unclassified_stocks=[s for s in values if s['stock_code'] in mapping['unclassified_stock_codes']],
                data_status='미확인' if not rank or not turn else '부분' if turn.get('original_status')=='partial' else '실제',
                scope='0198 TOP20 + 확보된 동일 슬롯 0186 페이지의 합집합',
                theme_basis='historical_reference_not_verified_permanent_membership',
                candidate_confirmed=False, api_calls=0, telegram_sent=False)


def reference_afternoon(pair,*,as_of,project=None):
    """Reuse C15's original observed universe/scorer for reference display.

    Partial real-server observations are not execution/closing verification.
    No new weights, capital estimates, memberships or order permissions.
    """
    from pathlib import Path
    from . import c15_identity, c15_briefing, c15_briefing_data, cross_adapter
    from .c12_external_theme_bridge import load
    root=Path(project) if project else Path(__file__).resolve().parents[1]
    paths=json.loads((root/'config/analysis-paths.json').read_text(encoding='utf8'))
    path=lambda key:Path(paths[key]) if Path(paths[key]).is_absolute() else root/paths[key]
    policy=json.loads((root/'config/c15-leadership.json').read_text(encoding='utf8'))
    contract=json.loads((root/'config/c15-display-contract.json').read_text(encoding='utf8'))
    official,identity=c15_identity.load_official(path('analysis_daily_db'),path('collection_checkpoint'),as_of=as_of)
    by_code={r['stock_code']:r for r in official}
    registry={code:dict(verified=True,source='dated_saved_KRX',security_type=r['product_type']) for code,r in by_code.items()}
    compared=cross_adapter.analyze_pair(dict(pair,code_registry=registry),min_turnover_eok=1000)
    excluded={r['stock_code'] for r in compared['excluded_turnover']}
    invalid={r['stock_code'] for r in compared['review']['turnover'] if r['reasons']!=['official_validity_unverified']}
    ranks={r['stk_cd']:r['rank'] for r in pair['ranking']['rows']}
    stocks=[]
    for row in pair['turnover']['rows']:
        code=row['stk_cd'];record=by_code.get(code,{})
        s=dict(stock_code=code,name=row['stk_nm'],raw_change=row.get('flu_rt'),raw_turnover=row.get('trde_prica'),
            ranking_rank=ranks.get(code),turnover_rank=int(row['now_rank']),turnover_won=None,eligibility_status='pending',
            product_status=record.get('product_type','UNKNOWN'))
        product=c15_briefing_data.practical_product(s,c15_briefing_data.product_evidence(s,record,as_of=as_of))
        s.update(explicitly_excluded=product['excluded'] or code in invalid,product_type_held=not product['verified'],closing_numeric_pass=code not in excluded)
        cap=c15_briefing_data.capital_reference(record,stock_code=code,as_of=as_of,contract=contract,minimum_won='300000000000')
        s['capital_status']='unverified' if cap['won'] is None else 'above_minimum' if Decimal(cap['won'])>300000000000 else 'below_minimum'
        s['capital_reference']=cap;stocks.append(s)
    tags,_=map_themes(stocks,load(path('theme_master_db'))['records'],as_of[:10],official_records=official,as_of=as_of)
    analysis=c15_briefing.run_existing(stocks,tags,policy)
    candidates=[];seen=set()
    for group in analysis['sectors']:
        for stock in group['leaders']:
            code=stock['stock_code']
            if code in seen:continue
            seen.add(code);cap=stock['capital_reference']
            candidates.append(dict(stock_code=code,stock_name=stock['name'],change_percent_raw=stock['raw_change'],
                turnover_raw=stock['raw_turnover'],ranking_0198=stock['ranking_rank'],turnover_rank=stock['turnover_rank'],
                market_cap_won_reference=cap['won'],capital_basis_date=cap.get('basis_date'),
                sectors=[group['name']],sector_rank=group['rank'],sector_score=group['score'],
                closing_numeric_pass=stock['closing_numeric_pass'],
                candidate_confirmed=False,reference_only=True,
                qualification=dict(final_review_allowed=False,review_status='참고용 분석',checks={},
                    reasons=[f"동반 상승 {group['rising_count']}종목 · 기존 C15 섹터 점수 {Decimal(group['score']):.2f}",
                             '기존 거래대금 1,000억원·순위 40위 조건: '+('충족' if stock['closing_numeric_pass'] else '미충족 · 참고 표시만')])) )
    return dict(candidates=candidates[:5],sectors=[dict(name=g['name'],rank=g['rank'],rising_count=g['rising_count'],score=g['score']) for g in analysis['sectors']],
        policy=policy,mode='reference_only',candidate_confirmed=False,execution_allowed=False,
        capital_basis_date=identity.get('basis_date'),scope=f'동일 시각 확보된 거래대금 {len(stocks)}행과 조회순위 TOP20 · 순위 미관측은 미관측 표시',
        limitations=['연속조회 미완료 가능','test_mode는 기존 독립 실서버 조회 표기','거래대금 명세 단위 환산·장마감 누적 미검증',
                     '시총은 과거 종가 참고값','테마는 과거 저장 매핑','주문·최종 매매 적격성 미확정'])


def format_text(result):
    view = result['morning_leadership']; analysis = view['analysis']
    lines = ['[ARVIS 오전 주도주 분석]', '기준: '+result['reference_at'][:16].replace('T',' '),
             '데이터 상태: '+view['data_status'], '관측 범위: '+view['scope']]
    if result.get('turnover_freshness'):
        from .c15_turnover_selection import format_source
        ranking=result['analysis']['snapshot_selection']['selected']['0198']
        clock=ranking['snapshot']['collection_started_at'][11:16] if ranking['status']=='fresh' and ranking['snapshot'] else '미확인'
        lines.append('0198 기준 '+clock)
        lines.append(format_source(result['turnover_freshness']))
        if result['turnover_freshness']['is_fallback']:
            lines.append('대체 자료의 등락률·순위와 현재 조회순위를 결합한 참고 점수. 실시간 증가·매수세 강화 근거 아님.')
    adr = result['auxiliary'].get('analysis_adr_db', {}).get('rows', [])
    lines.append('시장환경: '+(' / '.join(f"ADR20 {m} {Decimal(v):.2f} ({d}, {status})" if v is not None else f'ADR20 {m} 미확인 ({d}, {status})' for d,m,v,status in adr) or 'ADR20 미확인'))
    for s in analysis['sectors']:
        lines += ['', f"섹터 TOP {s['rank']}: {s['name']} (잠정 관측)",
                  f"- 상승 관측 {s['rising_count']} / 해당 테마 관측 {s['observed_count']}종목",
                  f"- 점수 {Decimal(s['score']):.2f} (초기 가설값); 동반 상승 수 우선, 점수 차순",
                  '- 점수 구성: '+', '.join(f'{k}={Decimal(v):.2f}' for k,v in s['components'].items())]
        for c in s['leaders']:
            amount = f"{Decimal(c['turnover_won'])/100000000:,.1f}억원" if c.get('turnover_won') else '미검증(금액 점수 제외)'
            lines.append(f"  {c['name']} / {c['raw_change']}% / 조회 {c.get('ranking_rank') or '미관측'} / 0186 수집 내 순위 {c.get('turnover_rank') or '미관측'} / 거래대금 {amount}")
            eligibility={'pending':'미확인·보류','eligible':'기존 기준 충족','excluded':'제외'}[c['eligibility_status']]
            capital={'unverified':'미확인','above_minimum':'기존 기준 초과','below_minimum':'기존 기준 이하'}[c['capital_status']]
            lines.append(f"    대표 관측 후보: 적격성 {eligibility}, 시총 {capital}; 확정 주도주 아님")
    lines += ['', '확정 주도 섹터 없음', '[주의]',
              '- 잠정 순위는 관측된 등락률 기반. 시총·유동성·영구 테마 소속의 확정 판정과 구분.',
              '- 등락률은 표시된 실제 0186 수집 시각의 flu_rt 보고값이며 런타임 단위 검증과 구분.',
              '- 0198 조회 상위 하락 종목은 상승 점수·대표 관측 후보에서 제외.',
              '- 0186 부분 수집 순위를 전체 시장 순위로 해석하지 않음. 단위 미검증 금액은 점수 제외.',
              '- 수집 시각 기준이며 거래소 기준 시각 미확인. 대체 여부와 자료 시각은 별도 표시.',
              '- ADR20은 전일 저장값의 사후 참고이며 선정 필수조건이 아님.']
    for api in ('0198','0186'):
        state=result['analysis']['snapshot_selection']['selected'][api]['status']
        if api=='0186' and result.get('turnover_freshness',{}).get('is_fallback'):state='fallback_within_10m (신규 관측 아님)'
        lines.append(f"- {api}: {state}; 수집/수신 시각 {', '.join(result['raw_times'][api]) or '미확인'}")
    turn=result['analysis']['snapshot_selection']['selected']['0186'].get('snapshot') or {}
    if turn:
        lines.append(f"- 0186 요청 {turn.get('request_started_at')}; 수신 {turn.get('response_received_at')}; 저장 완료 상한 {turn.get('db_committed_by')}")
    lines.append(f"- 등락률 미확인 {sum(number(s.get('raw_change')) is None for s in view['stocks'])}종목 / 시총 미확인 {sum(s['capital_status']=='unverified' for s in view['stocks'])}종목; 미확인 적격성을 통과로 처리하지 않음.")
    coverage = view['mapping']['coverage']
    lines.append(f"- 테마 매핑 {coverage['theme_linked']}/{coverage['total']} ({coverage['theme_linked_percent']:.2f}%); 코드 {coverage['code_mapped']}, 공식 이름 {coverage.get('official_name_mapped',0)}, 이름 보조 {coverage['name_only_mapped']} (과거 테마 참고)")
    if view.get('identity_validation'):
        info=view['identity_validation']
        lines.append(f"- 공식 이름·유형 근거: {info['status']}, 기준일 {info.get('basis_date','미확인')}; 분석 시각 이후 수집 근거 제외.")
        lines.append('- 우선주 별도 보류: '+(', '.join(f"{s['name']}({s['stock_code']})" for s in view['preferred_stocks']) or '없음'))
        lines.append('- 유형 미확인: '+(', '.join(f"{s['name']}({s['stock_code']})" for s in view['unknown_type_stocks']) or '없음'))
    lines.append('- 미분류: '+(', '.join(f"{s['name']}({s['stock_code']})" for s in view['unclassified_stocks']) or '없음'))
    lines.append('- 명시적 상품 제외: '+(', '.join(f"{s['name']}({s['stock_code']})" for s in view['excluded_stocks']) or '없음'))
    lines.append('- 상품 마스터에 없는 유형은 이름으로 추정 제외하지 않으며 적격성 보류. 미분류 상품은 점수에 기여하지 않음.')
    lines.append('- 복수 테마: 테마 내 코드 중복 제거, 테마 간 점수 기여 분할. 테마별 종목 수 합산 금지.')
    lines.append('- 복수 테마 종목을 제외한 동반 상승 순서: '+(', '.join(analysis['exclusive_breadth_order']) or '없음'))
    lines.append('- 단일 상승 테마는 낮은 신뢰도로 보류: '+(', '.join(t['name'] for t in analysis['held_sectors'] if t.get('rising_count')==1) or '없음'))
    return '\n'.join(lines)


def telegram_chunks(text, limit=4096):
    """Plain-text payloads sized in Telegram's UTF-16 units; never send."""
    chunks, chunk, size = [], '', 0
    for character in text:
        units = len(character.encode('utf-16-le'))//2
        if size+units > limit:
            chunks.append(chunk)
            chunk, size = '', 0
        chunk += character
        size += units
    if chunk: chunks.append(chunk)
    return chunks
