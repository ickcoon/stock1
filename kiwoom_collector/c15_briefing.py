"""C15-11 compact, preview-only briefing over the existing C15 theme engine."""
import argparse
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sys

from . import c15_preview as preview, c15_leadership, c15_briefing_data as evidence
from . import c15_turnover_audit, telegram_briefing as telegram
from .am_time_policy import instant, KST
from .c12_latest import digest
from .c12_theme_audit import analyze_themes


def run_existing(stocks, tags, policy):
    return analyze_themes([s for s in stocks if not s.get('explicitly_excluded') and
        not s.get('product_type_held') and s.get('product_status')!='preferred_hold'],tags,rising_policy=policy)['rising_analysis']


def statistics(stocks, tags):
    retained=[s for s in stocks if not s.get('explicitly_excluded')]
    mapped=analyze_themes(retained,tags)
    return dict(raw_observed=len(stocks),after_product_exclusion=len(retained),
        excluded_products=len(stocks)-len(retained),
        type_pending=sum(s.get('product_status') in ('UNKNOWN','unknown') for s in retained),
        unclassified=len(mapped['unclassified_stock_codes']),mapping_coverage=mapped['coverage'],
        preferred_held=sum(s.get('product_status')=='preferred_hold' for s in retained),
        below_capital_minimum_held=sum(s.get('capital_status')=='below_minimum' for s in retained))


def prepare(report, display_contract, turnover_contract, *, official_products=None, practical_products=False):
    """Retain original analysis. Reuse the same scorer only for new evidenced filters."""
    view=report['morning_leadership'];stocks=deepcopy(view['stocks'])
    tags=[tag for t in view['mapping']['themes'] for values in t['evidence'].values() for tag in values]
    policy=view['analysis']['policy'];at=report['reference_at']
    minimum=report['analysis'].get('policy',{}).get('market_cap_exclusive_min_won','300000000000')
    products={r['stock_code']:r for r in official_products or []}
    baseline_stats=statistics(stocks,tags)
    product_decisions={}
    existing_decisions=report['analysis'].get('candidate_filter',{}).get('decisions',{})
    for s in stocks:
        record=products.get(s['stock_code'],s.get('product_reference') or {})
        decision=evidence.product_evidence(s,record,as_of=at,existing_decision=existing_decisions.get(s['stock_code']))
        if s.get('explicitly_excluded') and decision['verified'] and not decision['excluded']:
            decision=dict(decision,verified=False,excluded=False,status='conflict_pending')
        if practical_products:
            decision=evidence.practical_product(s,decision)
        product_decisions[s['stock_code']]=decision
        s['product_type_held']=not decision['verified']
        s['explicitly_excluded']=decision['excluded']
        if decision['verified']:
            s['product_status']='preferred_hold' if decision['product_type']=='preferred_stock' else decision['product_type']
    product_stats=statistics(stocks,tags);product_analysis=run_existing(stocks,tags,policy)
    capitals={s['stock_code']:evidence.capital_reference(s.get('product_reference') or {},stock_code=s['stock_code'],as_of=at,
                contract=display_contract,minimum_won=minimum) for s in stocks}
    if practical_products:
        capitals={code:evidence.recent_capital(cap,at) for code,cap in capitals.items()}
    missing=[code for code,cap in capitals.items() if cap['won'] is None]
    cache,cache_info=evidence.kiwoom_capitals(report['paths'].get('eligibility_master'),missing,as_of=at)
    for code,cap in cache.items():
        cap['eligibility_status']='above_minimum_reference' if Decimal(cap['won'])>Decimal(minimum) else 'below_or_equal_minimum_reference'
        capitals[code]=cap
    for cap in capitals.values():
        cap['source_path']=(report['paths'].get('eligibility_master') if cap['status']=='verified_ka10001_cache' else
                            report.get('auxiliary',{}).get('analysis_daily_db',{}).get('path'))
    for s in stocks:
        cap=capitals[s['stock_code']]
        if cap['won'] is not None:
            # Same existing cap floor, now using an explicitly dated reference.
            # Above-floor capital does not pass the independent liquidity gates.
            s['capital_status']='above_minimum' if Decimal(cap['won'])>Decimal(minimum) else 'below_minimum'
    final=run_existing(stocks,tags,policy)
    turn=report['analysis']['snapshot_selection']['selected']['0186']
    snapshot=turn['snapshot'] if turn['status']=='fresh' else None
    amount_audit=c15_turnover_audit.audit(report['paths']['turnover'],snapshot,turnover_contract)
    amount_by_code={r['stock_code']:r for r in amount_audit.get('rows',[])} if amount_audit.get('storage_consistent') else {}
    measurements={}
    for s in stocks:
        code=s['stock_code'];amount=amount_by_code.get(code)
        measurements[code]=dict(stock_code=code,name=s['name'],product=product_decisions[code],capital=capitals[code],
            turnover=dict(status='documented_conversion' if amount else 'unverified_or_unobserved',
                source_field='trde_prica',raw_value=amount['raw_value'] if amount else None,
                source_path=report['paths']['turnover'],source_snapshot_id=(snapshot or {}).get('id'),
                eok=amount['documented_eok'] if amount else None,won=amount['documented_won'] if amount else None,
                collected_at=(snapshot or {}).get('collection_started_at') if amount else None,
                request_started_at=(snapshot or {}).get('request_started_at') if amount else None,
                response_received_at=(snapshot or {}).get('response_received_at') if amount else None,
                multiplier_to_eok='0.01',cumulative_period_verified=False,amount_score_enabled=False),
            change=dict(raw_value=s.get('raw_change'),source_field='flu_rt',
                observed_at=(snapshot or {}).get('request_started_at') if s.get('raw_change') is not None else None))
    result=dict(schema_version='c15-11-briefing-preview-v1',source_kind=report['source_kind'],
        reference_at=at,analysis=final,candidate_confirmed=False,api_calls=0,telegram_sent=False,
        input_policy='c15-11-prior-close-capital-reference-v1',
        source_presence={api:report['analysis']['snapshot_selection']['selected'][api]['status'] for api in ('0198','0186')},
        turnover_freshness=report.get('turnover_freshness'),
        diagnostics=dict(source_report=report,stock_measurements=measurements,turnover_audit=amount_audit,
            comparison=dict(before=baseline_stats,after_product=product_stats,after_capital=statistics(stocks,tags),
                before_top3=deepcopy(view['analysis']['sectors']),after_product_top3=product_analysis['sectors'],
                after_capital_top3=final['sectors']),
            unclassified_stocks=[s for s in stocks if s['stock_code'] in view['mapping']['unclassified_stock_codes']],
            product_evidence_limitations=['saved_equity_master_not_complete_ETF_ETN_master',
                'historical_catalog_report_not_substitute_for_missing_raw_master',
                'name_patterns_review_only_never_exclusion'],
            ka10001_cache=cache_info,capital_minimum_exclusive_won=minimum,
            capital_usage='latest_available_prior_close_reference_no_intraday_or_liquidity_promotion',
            score_policy_unchanged=policy==final['policy']))
    if practical_products:
        result['input_policy']='c15-12-practical-products-v1'
        result['diagnostics']['product_evidence_limitations']=['conservative_name_exclusion_not_official_type_confirmation','unknown_types_held']
    result['text']=format_briefing(result)
    telegram.scan(result['text'],())
    result['telegram_preview_messages']=c15_leadership.telegram_chunks(result['text'])
    result['evaluation_reference']=digest(result)
    return result


def format_briefing(result):
    report=result['diagnostics']['source_report'];at=instant(result['reference_at'])
    rows=report.get('auxiliary',{}).get('analysis_adr_db',{}).get('rows',[])
    adr=next((r for r in rows if r[1]=='COMBINED'),None)
    adr_text=f"ADR20 {Decimal(adr[2]):.2f} ({adr[0][5:].replace('-','/')}·사후참고)" if adr and adr[2] is not None else 'ADR20 미확인'
    lines=['[ARVIS 오전 브리핑]',at.strftime('%m/%d %H:%M')+' | '+adr_text]
    if result['source_kind']!='real_saved_data':lines.append('합성 테스트 미리보기 · 실데이터 아님')
    rank=report['analysis']['snapshot_selection']['selected']['0198']
    rank_clock=instant(rank['snapshot']['collection_started_at']).strftime('%H:%M') if rank['status']=='fresh' and rank['snapshot'] else '미확인'
    lines.append('0198 기준 '+rank_clock)
    freshness=result.get('turnover_freshness')
    turn=report['analysis']['snapshot_selection']['selected']['0186']
    snapshot=turn['snapshot'] if turn['status']=='fresh' else None
    clock=instant(snapshot['request_started_at']).strftime('%H:%M') if snapshot else '미확인'
    fallback=bool((freshness or {}).get('is_fallback'))
    age=f" ({freshness['slot_lag_minutes']}분 전 대체)" if fallback else ''
    lines.append('0186 기준 '+clock+age)
    lines.append('등락률·대금 '+clock+' 기준'+(' / 최신 실시간 아님' if fallback else ''))
    measurements=result['diagnostics']['stock_measurements'];capital_dates=set();capital_kinds=set()
    for sector in result['analysis']['sectors']:
        lines += ['',f"{sector['rank']}위 {sector['name']}",
                  f"점수 {Decimal(sector['score']):.2f} | 상승 관측 {sector['rising_count']}종목"]
        for s in sector['leaders']:
            m=measurements[s['stock_code']];cap=m['capital'];cash=m['turnover']
            if cap['basis_date']:capital_dates.add(cap['basis_date']);capital_kinds.add(cap['status'])
            cash_text=f"대금 {Decimal(cash['eok']):,.2f}억" if cash['eok'] is not None else '거래대금 미확인'
            lines += [f"{s['name']} {Decimal(s['raw_change']):+g}%",
                      '  '+evidence.format_capital(cap['won'])+' | '+cash_text]
    if not result['analysis']['sectors']:lines += ['','선정 보류: 동반 상승·적격성 근거 부족']
    elif len(result['analysis']['sectors'])<3:lines.append('나머지 순위는 근거 부족으로 보류')
    dates=', '.join(d[5:].replace('-','/') for d in sorted(capital_dates)) or '미확인'
    capital_footer=('시총 '+dates+' 종가 참고(산식 대조)' if capital_kinds=={'prior_close_unit_corroborated'} else
                    '시총 기준일 '+dates+' (자료별 기준은 진단 참조)')
    lines += ['', '잠정 관측 후보 · 확정 주도주 아님',capital_footer,
              '시총은 분석 이전 확보된 최근 자료만 사용',
              '대금 '+clock+' 기준·명세 환산 / 누적 기준 미검증',
              '0186 부분 수집·미관측은 대금 부족 아님' if snapshot and snapshot.get('original_status')=='partial' else '0186 미확인' if not snapshot else '0186 저장 범위 내 관측',
              '혼합 시각 참고점수·신규 매수세 해석 금지' if fallback else '시총·유동성 전체 적격성 확정 아님',
              '금액 점수 OFF · 실제 발송 없음']
    return '\n'.join(lines)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date',required=True)
    p.add_argument('--slot',choices=(*preview.SLOTS,'all'),default='all')
    p.add_argument('--config',type=Path,default=preview.PROJECT/'config/c15-preview.json')
    p.add_argument('--output-dir',type=Path)
    p.add_argument('--strict-slots',action='store_true')
    args=p.parse_args(argv)
    output=(args.output_dir or preview.PROJECT/'diagnostics/c15-briefings'/datetime.now(KST).strftime('%Y%m%d-%H%M%S-%f')).absolute()
    if output.resolve()!=output or not output.is_relative_to(preview.PROJECT/'diagnostics'):
        raise ValueError('new_development_directory_required')
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    if args.strict_slots:config['turnover_fallback']=False
    display=json.loads((preview.PROJECT/'config/c15-display-contract.json').read_text(encoding='utf8'))
    turnover=json.loads((preview.PROJECT/'config/c15-turnover-contract.json').read_text(encoding='utf8'))
    output.mkdir(parents=True,exist_ok=False)
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    for slot in preview.SLOTS if args.slot=='all' else [args.slot]:
        source=preview.generate(config,day=args.date,slot=slot)
        result=prepare(source,display,turnover)
        folder=output/slot.replace(':','');folder.mkdir()
        (folder/'telegram-preview.txt').write_text(result['text'],encoding='utf8')
        (folder/'diagnostics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        (folder/'source-preview.txt').write_text(source['text'],encoding='utf8')
        print(result['text']);print()
    print('개발 미리보기: '+str(output))
    return 0


if __name__=='__main__':raise SystemExit(main())
