"""C15-10 saved morning observations connected in time. No delivery or DB writes."""
import argparse
from datetime import datetime
from decimal import Decimal
import json
import sys
from pathlib import Path

from .am_momentum import number
from .am_time_policy import instant, KST
from .c12_latest import digest
from .c12_theme_audit import analyze_themes, attention_component
from . import c15_preview as preview, c15_leadership as leadership, c15_turnover_audit as amounts

STATES={'emerging':'신규 부상','persistent':'강세 지속','weakening':'주도력 약화',
        'held':'판단 보류','new_observation':'신규 관측'}
WATCH={'제약바이오':['제약바이오'],'신재생에너지':['신재생에너지'],'로봇':['로봇'],'은행':['은행'],
       '반도체':['반도체'],'전력설비':['전선·전력설비'],'재건':['일부 재건주'],
       '항공/여행':['항공/여행'],'게임':['게임']}


def available(report, api):
    # A cached reference is usable for preview, never a new flow measurement.
    if api=='0186' and report.get('turnover_freshness',{}).get('is_fallback'):return False
    chosen=report['analysis']['snapshot_selection']['selected'][api]
    s=chosen.get('snapshot')
    if chosen['status']!='fresh' or not s: return False
    try:
        at=instant(report['decision_deadline_at']);reference=instant(report['reference_at'])
        start=instant(s.get('request_started_at') or s['collection_started_at'])
        scope=(s.get('api_id')=='ka00198' and s.get('qry_tp')=='1') if api=='0198' else (
            s.get('api_id')=='ka10032' and s.get('profile')=={'mrkt_tp':'000','mang_stk_incls':'0','stex_tp':'1'})
        return (scope and s['environment']=='real' and start.date()==reference.date() and
                start.strftime('%H:%M')==reference.strftime('%H:%M') and
                all(instant(s[k])<=at for k in ('collection_started_at','request_started_at',
                    'response_received_at','db_committed_by') if s.get(k)) and bool(s.get('db_committed_by'))
                and start<=instant(s.get('response_received_at') or s['db_committed_by'])<=instant(s['db_committed_by']))
    except (KeyError,ValueError,TypeError):return False


def reference_available(report, api):
    if available(report,api):return True
    freshness=report.get('turnover_freshness',{})
    if api!='0186' or not freshness.get('is_fallback'):return False
    from .c15_turnover_selection import select
    chosen=report['analysis']['snapshot_selection']['selected'][api]
    if chosen['status']!='fresh' or not chosen.get('snapshot'):return False
    choice=select([chosen['snapshot']],reference_at=report['reference_at'],
        deadline_at=report['decision_deadline_at'],max_age_seconds=freshness['max_age_seconds'])
    return choice['freshness']['is_fallback'] and choice['freshness'].get('snapshot_id')==freshness.get('snapshot_id')


def connections(view):
    return {r['stock_code']:tuple(sorted(r['themes'])) for r in view['mapping']['connections']}


def eligible(s):
    return not s.get('explicitly_excluded') and s.get('product_status')!='preferred_hold' and s.get('capital_status')!='below_minimum'


def project(stocks, links, policy):
    """Reapply existing engine to an explicitly retrospective common panel."""
    tags=[dict(stock_code=c,sector_name=t,source='same_saved_membership') for c in sorted(stocks) for t in links[c]]
    report=analyze_themes(list(stocks.values()),tags,rising_policy=policy)
    rising=report['rising_analysis']
    scores={r['name']:r for r in rising['ranked_sectors']+rising['held_sectors']}
    memberships={c:len(links[c]) for c in stocks}
    result={}
    for t in report['themes']:
        s=scores[t['name']];members=[stocks[c] for c in t['stock_codes']]
        known=all(number(r.get('raw_change')) is not None for r in members)
        result[t['name']]=dict(score=s.get('score'),rank=s.get('rank'),rising_count=s['rising_count'],
            observed_count=t['distinct_stocks'],stock_codes=t['stock_codes'],
            strength=s.get('components',{}).get('strength','0') if known else None,
            rates_complete=known,leaders=s.get('leaders',[]),
            attention_score=str(attention_component(members,memberships,policy['attention'])),
            attention_count=t['attention_count'],top5_count=t['current_top5_count'])
    return result


def classify(before, after, config, *, comparable):
    """Initial hypotheses about the common observed panel, never long-term leadership."""
    result=dict(state='held',reason='insufficient_comparable_evidence',long_term_confirmed=False)
    if before is None and after is not None:
        return dict(result,state='new_observation',reason='previously_unobserved_not_proven_emerging')
    if not comparable or before is None or after is None:return result
    if config['minimum_comparable_points']>2 or not before['rates_complete'] or not after['rates_complete']:return result
    n0,n1=before['rising_count'],after['rising_count']
    strength0,strength1=Decimal(before['strength']),Decimal(after['strength'])
    epsilon=Decimal(config['strength_epsilon'])
    if n1<n0 or strength1<strength0-epsilon:
        return dict(result,state='weakening',reason='common_panel_positive_strength_or_companions_decreased')
    rank0,rank1=before.get('rank'),after.get('rank')
    if n1>=config['minimum_companions'] and strength1>strength0+epsilon and rank1 is not None and (rank0 is None or rank1<rank0):
        return dict(result,state='emerging',reason='common_panel_strength_and_rank_improved')
    if (n0>=config['minimum_companions'] and n1>=config['minimum_companions'] and
            rank0 is not None and rank1 is not None and max(rank0,rank1)<=config['strong_rank_max']):
        return dict(result,state='persistent',reason='common_panel_multiple_risers_and_strong_rank_maintained')
    return dict(result,reason='no_clear_leadership_transition')


def compare(left, right, config):
    lv,rv=left['morning_leadership'],right['morning_leadership']
    lm,rm=connections(lv),connections(rv)
    ls={s['stock_code']:s for s in lv['stocks'] if eligible(s)}
    rs={s['stock_code']:s for s in rv['stocks'] if eligible(s)}
    stable={c for c in ls.keys() & rs.keys() if lm.get(c) and lm.get(c)==rm.get(c)
            and ls[c].get('product_status')==rs[c].get('product_status')}
    lp,rp={c:ls[c] for c in sorted(stable)},{c:rs[c] for c in sorted(stable)}
    same_policy=lv['analysis']['policy']==rv['analysis']['policy'] and left['time_basis']==right['time_basis']
    ranks_ready=all(available(r,'0198') for r in (left,right))
    strength_ready=ranks_ready and all(available(r,'0186') for r in (left,right)) and same_policy
    full_left,full_right=project(lp,lm,lv['analysis']['policy']),project(rp,rm,rv['analysis']['policy'])
    attention_codes={c for c in stable if ls[c].get('ranking_rank') and rs[c].get('ranking_rank')}
    # The same rank-contribution formula, without a positive-price predicate:
    # this is raw attention only and can never establish rising leadership.
    att_left=project({c:dict(ls[c],raw_change=None) for c in sorted(attention_codes)},lm,lv['analysis']['policy'])
    att_right=project({c:dict(rs[c],raw_change=None) for c in sorted(attention_codes)},rm,rv['analysis']['policy'])
    for attention in (att_left,att_right):
        for i,(name,row) in enumerate(sorted(attention.items(),key=lambda p:(-Decimal(p[1]['attention_score']),p[0])),1):
            row['attention_rank']=i
    all_names=sorted({t for names in list(lm.values())+list(rm.values()) for t in names})
    sectors=[]
    for name in all_names:
        base={c for c in ls if name in lm.get(c,())};current={c for c in rs if name in rm.get(c,())}
        common=base & stable
        coverage=Decimal(len(common))/len(base) if base else Decimal(0)
        b,a=full_left.get(name),full_right.get(name)
        sufficient=strength_ready and bool(common) and coverage>=Decimal(config['required_previous_sector_coverage'])
        state=classify(b,a,config,comparable=sufficient)
        if not base and current and strength_ready:
            state=classify(None,dict(observed_count=len(current)),config,comparable=False)
        if not sufficient and state['state']=='held':
            state['reason']=('cached_0186_not_new_measurement' if any(r.get('turnover_freshness',{}).get('is_fallback') for r in (left,right)) else
                             'missing_0198_or_0186_at_one_or_both_times' if not strength_ready else
                             'previous_sector_members_missing_or_mapping_changed')
        row=dict(sector=name,**state,confidence='provisional_saved_observation',
            observation_status='newly_observed' if not base and current else 'not_observed_now' if base and not current else 'observed_at_both',
            total_score_grade='C' if not strength_ready else 'A' if set(ls)==set(rs) and lm==rm and b and a and b['rates_complete'] and a['rates_complete'] else 'B',
            common_strength_grade='A' if sufficient and b and a and b['rates_complete'] and a['rates_complete'] else 'C',
            common_attention_grade='A' if ranks_ready and same_policy and name in att_left and name in att_right else 'C',
            previous_members=sorted(base),current_members=sorted(current),common_members=sorted(common),
            added_members=sorted(current-base),missing_members=sorted(base-current),previous_coverage=str(coverage),
            before=b,after=a,attention_before=att_left.get(name),attention_after=att_right.get(name))
        if row['common_strength_grade']!='A':
            # Retained diagnostic numbers are not comparisons when a source is
            # absent. Never turn missing 0186 into zero contribution.
            row['before']=b if available(left,'0186') else None
            row['after']=a if available(right,'0186') else None
        sectors.append(row)
    return dict(from_slot=left['reference_at'][11:16],to_slot=right['reference_at'][11:16],
                as_of=right['decision_deadline_at'],projection_kind='retrospective_pair_diagnostic_not_past_decision',
                original_scores_rewritten=False,common_panel_codes=sorted(stable),
                common_0198_codes=sorted(attention_codes),sectors=sectors)


def slot_rows(report):
    view=report['morning_leadership']; themes={t['name']:t for t in view['mapping']['themes']}
    scores={s['name']:s for s in view['analysis']['ranked_sectors']+view['analysis']['held_sectors']}
    presence={api:reference_available(report,api) for api in ('0198','0186')}
    return {name:dict(sector=name,score=scores.get(name,{}).get('score'),rank=scores.get(name,{}).get('rank'),
        rising_count=scores.get(name,{}).get('rising_count') if presence['0186'] else None,
        observed_count=t['distinct_stocks'],stock_codes=t['stock_codes'],
        leaders=scores.get(name,{}).get('leaders',[]),change_available_count=t['change_available_count'],
        source_presence=presence,fresh_source_presence={api:available(report,api) for api in ('0198','0186')},
        turnover_freshness=report.get('turnover_freshness'),
        mapping_status='historical_theme_reference',mapping_coverage=view['mapping']['coverage'],
        data_status=view['data_status']) for name,t in themes.items()}


def common_attention_series(reports):
    """One fixed retrospective 0198 cohort across every requested slot."""
    views=[r['morning_leadership'] for r in reports]
    policy=views[0]['analysis']['policy']
    if (not all(available(r,'0198') for r in reports) or any(v['analysis']['policy']!=policy for v in views)
            or len({r['time_basis'] for r in reports})!=1):
        return dict(grade='C',reason='0198_or_policy_mismatch',slots={},cohort_codes=[])
    stocks=[{s['stock_code']:s for s in v['stocks'] if eligible(s) and s.get('ranking_rank')} for v in views]
    mappings=[connections(v) for v in views]
    common=set.intersection(*(set(s) for s in stocks))
    common={c for c in common if mappings[0].get(c) and all(m.get(c)==mappings[0][c] for m in mappings)
            and len({s[c].get('product_status') for s in stocks})==1}
    slots={}
    for r,index in zip(reports,stocks):
        projected=project({c:dict(index[c],raw_change=None,turnover_rank=None) for c in sorted(common)},mappings[0],policy)
        slots[r['reference_at'][11:16]]={name:dict(attention_score=row['attention_score'],attention_rank=i,
            attention_count=row['attention_count'],top5_count=row['top5_count'])
            for i,(name,row) in enumerate(sorted(projected.items(),key=lambda p:(-Decimal(p[1]['attention_score']),p[0])),1)}
    return dict(grade='A' if common else 'C',cohort_codes=sorted(common),slots=slots,
                as_of=reports[-1]['decision_deadline_at'],
                basis='retrospective_fixed_0198_intersection_existing_attention_component',
                rising_or_buying_strength_inferred=False)


def analyze(reports, config):
    if (config.get('policy_status')!='initial_hypothesis' or config['minimum_companions']<2 or
            config['strong_rank_max']<1 or config['minimum_comparable_points']<2 or
            not 0<Decimal(config['required_previous_sector_coverage'])<=1 or Decimal(config['strength_epsilon'])<0):
        raise ValueError('invalid_flow_hypothesis')
    reports=sorted(reports,key=lambda r:r['reference_at'])
    if len(reports)<2 or len({r['reference_at'][11:16] for r in reports})!=len(reports):raise ValueError('distinct_slots_required')
    day=reports[0]['reference_at'][:10]
    if any(r['reference_at'][:10]!=day or instant(r['reference_at'])>instant(r['decision_deadline_at']) for r in reports):
        raise ValueError('same_day_ordered_cutoffs_required')
    if len({r['source_kind'] for r in reports})!=1:raise ValueError('mixed_source_kinds')
    slots={r['reference_at'][11:16]:slot_rows(r) for r in reports}
    pairs=[compare(a,b,config) for a,b in zip(reports,reports[1:])]
    valid=[r for r in reports if available(r,'0198') and available(r,'0186')]
    bridge=compare(valid[0],valid[-1],config) if len(valid)>=2 and len(reports)>2 else None
    names=sorted({name for rows in slots.values() for name in rows})
    result=dict(schema_version='c15-10-sector-flow-v1',trade_date=day,source_kind=reports[0]['source_kind'],
        config=config,score_policy=reports[0]['morning_leadership']['analysis']['policy'],slots=slots,
        sectors=names,adjacent_comparisons=pairs,available_endpoint_comparison=bridge,
        common_0198_series=common_attention_series(reports),
        skipped_strength_slots=[r['reference_at'][11:16] for r in reports if not available(r,'0186')],
        watch_sectors={name:dict(observed_labels=[t for t in labels if t in names],
            exact_label_observed=name in names,note='display_reference_only_no_theme_added_or_aggregated') for name,labels in WATCH.items()},
        limitations=['partial_pages_not_market_wide','historical_theme_receipt_times_unverified',
            'common_panel_is_retrospective_diagnostic','0198_attention_not_rising_or_buying_flow',
            'no_long_term_leadership_confirmation'],api_calls=0,telegram_sent=False)
    result['text']=format_text(result)
    result['telegram_preview_messages']=leadership.telegram_chunks(result['text'])
    result['evaluation_reference']=digest(result)
    return result


def attach_unit_audits(result, reports, contract):
    """Display documented conversions; do not pass them into the score engine."""
    result['turnover_unit_audits']={r['reference_at'][11:16]:amounts.audit(r['paths']['turnover'],
        r['analysis']['snapshot_selection']['selected']['0186']['snapshot'] if reference_available(r,'0186') else None,contract) for r in reports}
    for r in reports:
        result['turnover_unit_audits'][r['reference_at'][11:16]]['reference_freshness']=r.get('turnover_freshness')
    result['amount_score_comparison']=dict(before_enabled=False,after_enabled=False,
        before_top3={r['reference_at'][11:16]:[s['name'] for s in r['morning_leadership']['analysis']['sectors']] for r in reports},
        after_documented_conversion_top3={r['reference_at'][11:16]:[s['name'] for s in r['morning_leadership']['analysis']['sectors']] for r in reports},
        enabled_ranking=None,reason='empirical_unit_and_intraday_accumulation_proof_missing_no_new_weight_invented')
    result['text']=format_text(result)
    result['telegram_preview_messages']=leadership.telegram_chunks(result['text'])
    result['evaluation_reference']=digest({k:v for k,v in result.items() if k!='evaluation_reference'})
    return result


def format_text(report):
    slots=list(report['slots']);bridge=report['available_endpoint_comparison']
    summary={s['sector']:s for s in (bridge or report['adjacent_comparisons'][-1])['sectors']}
    lines=['[ARVIS 오전 섹터 흐름]','날짜: '+report['trade_date'],
           '부분 관측의 잠정 변화이며 실제 매수세·장기 주도권 교체 확정 아님.',
           '섹터 | '+' | '.join(slots)+' | 상태(비교 가능한 확보 시점)']
    for name in report['sectors']:
        cells=[]
        for slot in slots:
            row=report['slots'][slot].get(name)
            if not row:cells.append('미관측');continue
            if not row['source_presence']['0186']:cells.append(f"0186 없음 / 관측 {row['observed_count']}");continue
            score=f"{Decimal(row['score']):.3f}" if row['score'] is not None else '미선정'
            cells.append(f"{score}({str(row['rank'])+'위' if row['rank'] else '순위 보류'}; ↑{row['rising_count']}/{row['observed_count']})")
            if (row.get('turnover_freshness') or {}).get('is_fallback'):
                cells[-1]+=' [0186 '+row['turnover_freshness']['request_started_at'][11:16]+' 대체]'
        state=summary.get(name,dict(state='held',reason='unobserved'))
        lines.append(name+' | '+' | '.join(cells)+' | '+STATES[state['state']]+'(잠정)')
    attention=report['common_0198_series']
    lines += ['', f"[0198 공통 관심 비교: {attention['grade']}, 고정 {len(attention['cohort_codes'])}종목]",
              '기존 관심 구성요소의 같은 가중치·테마 분할 재사용. 상승 여부를 판정하지 않는 사후 관심 비교점수.']
    for name in sorted({n for rows in attention['slots'].values() for n in rows}):
        cells=[f"{slot} {Decimal(rows[name]['attention_score']):.3f}({rows[name]['attention_rank']}위)"
               if name in rows else slot+' 미관측' for slot,rows in attention['slots'].items()]
        lines.append('- '+name+': '+' → '.join(cells))
    for pair in report['adjacent_comparisons']+([bridge] if bridge else []):
        lines += ['',f"[비교 {pair['from_slot']} → {pair['to_slot']}]",
                  f"동일 종목 진단 {len(pair['common_panel_codes'])}개 / 공통 0198 종목 {len(pair['common_0198_codes'])}개. 과거 원점수 재작성 없음."]
        for row in pair['sectors']:
            before,after=row['before'],row['after']
            def metric(value):
                return '비교 불가' if value is None else f"강도 {value['strength']}, ↑{value['rising_count']}/{value['observed_count']}, 순위 {value['rank']}"
            att0,att1=row['attention_before'],row['attention_after']
            attention=f"{att0['attention_score']} → {att1['attention_score']}" if att0 and att1 else '미관측/비교 불가'
            lines += [f"- {row['sector']}: {STATES[row['state']]}(잠정); {row['reason']}",
                      f"  총점 {row['total_score_grade']} / 동일 종목 강도 {row['common_strength_grade']} / 0198 관심 {row['common_attention_grade']}",
                      f"  {metric(before)} → {metric(after)}; 0198 관심 비교점수 {attention}",
                      f"  추가 관측 {','.join(row['added_members']) or '없음'} / 미관측 전환 {','.join(row['missing_members']) or '없음'}"]
    lines += ['', '[시각별 대표 관측 종목 및 자료 상태]']
    for slot,rows in report['slots'].items():
        audit=report.get('turnover_unit_audits',{}).get(slot,{})
        amount_rows={r['stock_code']:r for r in audit.get('rows',[])} if audit.get('storage_consistent') else {}
        for name,row in rows.items():
            def leader(s):
                amount=amount_rows.get(s['stock_code'],{}).get('documented_eok')
                return f"{s['name']}({s['raw_change']}%"+(f", {Decimal(amount):,.2f}억원 명세환산·실측미검증)" if amount is not None else ')')
            leaders=', '.join(leader(s) for s in row['leaders']) or '선정 보류'
            lines.append(f"- {slot} {name}: {leaders}; 자료 {row['source_presence']}; 매핑 과거 참고 {row['mapping_coverage']['theme_linked']}/{row['mapping_coverage']['total']}")
            if row.get('turnover_freshness'):
                from .c15_turnover_selection import format_source
                lines.append('  분석 '+slot+' / 0198 기준 '+slot+' / '+format_source(row['turnover_freshness']))
    lines += ['', '[관찰 요청 테마 — 집계나 신규 연결 아님]']
    lines += [f"- {name}: {', '.join(info['observed_labels']) or '실제 관측 테마 없음'}" for name,info in report['watch_sectors'].items()]
    if report.get('turnover_unit_audits'):
        lines += ['', '[0186 REST 거래대금 단위 검증]',
                  '공식 trde_prica: 백만원. 아래 원/억원은 명세 환산이며 실측 검증·금액 점수 활성화 아님.']
        for slot,audit in report['turnover_unit_audits'].items():
            lines.append(f"- {slot}: {audit['status']}; 대조 {audit.get('checked_rows',0)}행; 금액 점수 비활성")
            if audit.get('storage_consistent'):
                for row in audit['rows'][:3]:
                    lines.append(f"  {row['name']}: 원본 {row['raw_value']} → 명세환산 {row['documented_won']}원 / {Decimal(row['documented_eok']):,.2f}억원")
        lines.append('금액 점수 활성화 후 순위는 검증 부족으로 미계산. 명세환산 표시 전후의 기존 순위는 동일.')
    lines += ['', '[주의]', 'A: 동일 저장 출처·종목·계산식의 제한된 비교. B: 관측 범위 차이로 참고용. C: 비교 불가.',
              '표시된 0186 대체 자료는 참고용이며 새 시세·거래대금 증가 관측으로 처리하지 않음. 미래 자료 사용 없음.',
              '조회 관심 점수는 하락 종목도 포함하며 상승 판정에 사용하지 않음.',
              '확보 시점 간 진단은 마지막 시각에서의 사후 비교다. 미래 시세·테마를 과거 원점수에 주입하지 않음.',
              '미관측은 하락·거래대금 부족 아님. API 호출 0 / Telegram 발송 없음.']
    return '\n'.join(lines)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date',required=True)
    p.add_argument('--config',type=Path,default=preview.PROJECT/'config/c15-preview.json')
    p.add_argument('--flow-config',type=Path,default=preview.PROJECT/'config/c15-flow.json')
    p.add_argument('--contract',type=Path,default=preview.PROJECT/'config/c15-turnover-contract.json')
    p.add_argument('--output-dir',type=Path)
    p.add_argument('--strict-slots',action='store_true',help='Legacy C15-10 exact-slot-only replay')
    args=p.parse_args(argv)
    output=(args.output_dir or preview.PROJECT/'diagnostics/c15-flow'/datetime.now(KST).strftime('%Y%m%d-%H%M%S-%f')).absolute()
    if output.resolve()!=output or not output.is_relative_to(preview.PROJECT/'diagnostics'):
        raise ValueError('new_development_directory_required')
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    if args.strict_slots:config['turnover_fallback']=False
    reports=[preview.generate(config,day=args.date,slot=slot) for slot in preview.SLOTS]
    result=analyze(reports,json.loads(args.flow_config.read_text(encoding='utf8')))
    contract=json.loads(args.contract.read_text(encoding='utf8'))
    attach_unit_audits(result,reports,contract)
    output.mkdir(parents=True,exist_ok=False)
    for r in reports:
        folder=output/r['reference_at'][11:16].replace(':','');folder.mkdir()
        (folder/'preview.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8')
    (output/'flow.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    (output/'flow.txt').write_text(result['text'],encoding='utf8')
    (output/'telegram-preview.txt').write_text(result['text'],encoding='utf8')
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    print(result['text']);print('\n개발 결과: '+str(output))
    return 0


if __name__=='__main__':raise SystemExit(main())
