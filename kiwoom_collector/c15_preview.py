"""Manual saved-data briefing. Read-only sources; no API, Store or delivery calls."""
import argparse
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from . import am_briefing, c12_latest as latest
from .am_time_policy import KST, instant
from .c12_external_theme_bridge import load as load_themes, reference_match, label_view

PROJECT = Path(__file__).resolve().parents[1]
SLOTS = ('09:40','09:45','09:50')


def path_from(value):
    path = Path(value)
    return path if path.is_absolute() else PROJECT/path


def exact_slot(snapshots, day, slot):
    """Never substitute another slot, date or environment, even if TTL is fresh."""
    reference = instant(day+'T'+slot+':00+09:00')
    current_rank = [s for s in snapshots if s['api']=='0198' and
                    s.get('scheduled_at')==reference.isoformat() and s['environment']=='real' and
                    reference<=instant(s['collection_started_at'])<=reference+timedelta(seconds=5)]
    current_turn = []
    for s in snapshots:
        if s['api']!='0186' or s['environment']!='real':
            continue
        try:
            at=instant(s['request_started_at'])
            if at.date()==reference.date() and at.strftime('%H:%M')==slot:
                current_turn.append(s)
        except (ValueError,TypeError,KeyError):
            pass
    current_ids={s['id'] for s in current_rank}
    history = [s for s in snapshots if s['api']=='0198' and
               (instant(s['collection_started_at'])<=reference or s['id'] in current_ids)] if current_rank else []
    return history+current_turn, current_rank, current_turn


def context(paths, day):
    result={}
    for key,sql in [
        ('analysis_daily_db','SELECT max(day),count(*) FROM daily_latest WHERE day<?'),
        ('analysis_adr_db',"SELECT day,market,value,status FROM adr20 WHERE day<? AND universe='common' AND day=(SELECT max(day) FROM adr20 WHERE day<?) ORDER BY market")]:
        p=path_from(paths[key])
        try:
            with latest.read_db(p) as db:
                values=[list(r) for r in db.execute(sql,(day,) if key=='analysis_daily_db' else (day,day))]
            result[key]=dict(path=str(p),status='retrospective_saved_reference',rows=values)
        except (OSError,ValueError,sqlite3.Error) as error:
            result[key]=dict(path=str(p),status='unverified',error_type=type(error).__name__)
    return result


def generate(config, *, day, slot, runtime_root=None, master_db=None, theme_db=None):
    date.fromisoformat(day)
    if slot not in SLOTS:
        raise ValueError('morning_slot_required')
    runtime=path_from(runtime_root or config['runtime_root'])
    paths=json.loads(path_from(config['analysis_paths']).read_text(encoding='utf8'))
    ranking=runtime/config.get('ranking_relative','data/stock1-real.db')
    turnover=runtime/config.get('turnover_relative','data/turnover')/day/(slot.replace(':','')+'.db')
    requested_turnover=turnover
    fallback_enabled=config.get('turnover_fallback',False)
    source_paths={}
    master=master_db or config.get('eligibility_master_db')
    master=path_from(master) if master else None
    theme=path_from(theme_db or paths['theme_master_db'])
    logs=sorted((runtime/'logs').glob('*.log'))
    ledger=runtime/'reports/workflow'/day/(slot.replace(':','')+'.json')
    warnings=[]
    snapshots=[]
    try:
        if fallback_enabled:
            snapshots=latest.load(ranking,day=day)
            candidates=sorted(turnover.parent.glob('*.db'))
            if len(candidates)>1000:raise ValueError('turnover_day_file_limit')
            for candidate in candidates:
                source_ledger=ledger.parent/(candidate.stem+'.json')
                try:
                    loaded=latest.load(ranking,[candidate],day=day,
                        ledgers=[source_ledger] if source_ledger.is_file() else [])
                    for snapshot in loaded:
                        if snapshot['api']=='0186':
                            snapshots.append(snapshot)
                            source_paths[snapshot['id']]=candidate
                except (OSError,ValueError,sqlite3.Error,KeyError) as error:
                    warnings.append('0186 개별 원본 미검증: '+candidate.name+' '+type(error).__name__)
        else:
            snapshots=latest.load(ranking,[turnover] if turnover.is_file() else [],day=day,
                                  ledgers=[ledger] if ledger.is_file() else [])
        completions={}
        for log in logs:
            for identifier, evidence in latest.log_completions(log).items():
                if identifier in completions and completions[identifier]!=evidence:
                    raise ValueError('ambiguous_completion_logs')
                completions[identifier]=evidence
        for s in snapshots:
            if s['api']=='0198':
                evidence=completions.get(s['id'],{})
                if evidence.get('record_count')==len(s['rows']):
                    s['db_committed_by']=evidence['db_committed_by']
                    s['completion_basis']=evidence['completion_basis']
                s['snapshot_sha256']=latest.digest({k:v for k,v in s.items() if k!='snapshot_sha256'})
    except (OSError,ValueError,sqlite3.Error,KeyError) as error:
        warnings.append('수집 원본 미검증:'+type(error).__name__)
    paired, raw_rank, raw_turn = exact_slot(snapshots,day,slot)
    deadline=instant(day+'T'+slot+':00+09:00')+timedelta(seconds=45)
    exact_turn_count=sum(len(s['rows']) for s in raw_turn)
    freshness=None
    if fallback_enabled:
        from .c15_turnover_selection import select
        choice=select(snapshots,reference_at=deadline.replace(second=0).isoformat(),
                      deadline_at=deadline.isoformat(),max_age_seconds=config.get('turnover_fallback_max_age_seconds',600))
        freshness=choice['freshness']
        raw_turn=[choice['snapshot']] if choice['snapshot'] else []
        paired=[s for s in paired if s['api']=='0198']+raw_turn
        if raw_turn:turnover=source_paths[raw_turn[0]['id']]
    units=config.get('unit_evidence')
    evidence=json.loads(path_from(units).read_text(encoding='utf8')) if units and path_from(units).is_file() else None
    if master and not master.is_file():
        warnings.append('종목 적격성 마스터 미검증: 파일 미확인')
        master=None
    report=latest.analyze(paired,as_of=deadline.isoformat(),environment='real',master_db=master,unit_evidence=evidence)
    # C12's candidate observations intentionally use top 40. The preview may
    # display the full saved page without changing C12 scoring or unit gates.
    observation_rows=[dict(s) for s in report['observations']['watchlist']]
    turn=report['snapshot_selection']['selected']['0186']['snapshot']
    page={r['stock_code']:r for r in (turn or {}).get('rows',[]) if r.get('validation_status')=='syntax_valid'}
    for stock in observation_rows:
        row=page.get(stock['stock_code'])
        if row:
            raw=json.loads(row['raw_json'])
            if raw.get('stk_cd')==stock['stock_code']:
                stock.update(turnover_rank=row['turnover_rank'],raw_change=raw.get('flu_rt'),
                    raw_turnover=dict(value=raw.get('trde_prica'),verification='unverified'))
    # The original engine owns all candidate and sector ordering.
    selected=report['snapshot_selection']['selected']
    for api,raw in [('0198',raw_rank),('0186',raw_turn)]:
        chosen=selected[api]['snapshot']
        allowed={s['id'] for s in raw}
        if chosen and chosen['id'] not in allowed:
            raise ValueError('selected_snapshot_not_in_requested_slot')
    theme_records=[]
    try:
        theme_records=[r for r in load_themes(theme)['records'] if r['trade_date']<=day]
    except (OSError,ValueError,sqlite3.Error,KeyError) as error:
        warnings.append('테마 DB 미검증:'+type(error).__name__)
    references={}
    watch=[dict(stock_code=r['stock_code'],name=r['name']) for r in report['observations']['watchlist']]
    for record in theme_records:
        match=reference_match(record,watch)
        code=match.get('watchlist_stock_code')
        if code:
            references.setdefault(code,set()).add(label_view(record['raw_theme'])['name'])
    auxiliary=context(paths,day)
    product_reference={}
    try:
        with latest.read_db(path_from(paths['analysis_daily_db'])) as db:
            codes=tuple(sorted({s['stock_code'] for s in observation_rows} | set(page)))
            rows=list(db.execute('''SELECT stock_code,day,product_type,collected_at FROM daily_latest
                WHERE day<? AND stock_code IN ('''+','.join('?' for _ in codes)+') ORDER BY day DESC',
                (day,*codes))) if codes else []
            for row in rows:product_reference.setdefault(row['stock_code'],dict(row))
    except (OSError,ValueError,sqlite3.Error) as error:
        warnings.append('KRX 종목 분류 참고 미검증:'+type(error).__name__)
    result=dict(schema_version='c15-saved-preview-v1',source_kind='real_saved_data',
        reference_at=deadline.replace(second=0).isoformat(),decision_deadline_at=deadline.isoformat(),
        time_basis='collection_slot_not_verified_exchange_market_time',
        paths=dict(ranking=str(ranking),turnover=str(turnover),theme=str(theme),
                   eligibility_master=str(master) if master else None,
                   logs=[str(p) for p in logs],ledger=str(ledger)),
        raw_counts={'0198':sum(len(s['rows']) for s in raw_rank),'0186':sum(len(s['rows']) for s in raw_turn)},
        raw_times={'0198':[s['collection_started_at'] for s in raw_rank],
                   '0186':[s['response_received_at'] for s in raw_turn]},
        raw_turnover_status=[dict(status=s.get('original_status'),rows=len(s['rows'])) for s in raw_turn],
        saved_observations=observation_rows,
        krx_product_reference=product_reference,
        theme_references={c:sorted(v) for c,v in references.items()},
        auxiliary=auxiliary,analysis=report,warnings=warnings,
        validation_status='paired_saved_observations' if all(v['status']=='fresh' for v in selected.values()) else 'unverified_or_missing_slot_data',
        api_calls=0,telegram_sent=False)
    if freshness is not None:
        result['turnover_freshness']=freshness
        result['requested_slot_raw_counts']={'0198':sum(len(s['rows']) for s in raw_rank),'0186':exact_turn_count}
        result['paths']['turnover_requested']=str(requested_turnover)
        result['paths']['turnover_source_ledger']=str(ledger.parent/(turnover.stem+'.json'))
        if freshness['is_fallback'] and selected['0198']['status']=='fresh':
            result['validation_status']='fallback_saved_observations_not_synchronous'
    from . import c15_leadership
    policy_path=path_from(config.get('leadership_policy','config/c15-leadership.json'))
    policy=json.loads(policy_path.read_text(encoding='utf8'))
    official_records=None
    leadership_products=product_reference
    identity_info=None
    if config.get('identity_validation',False):
        from . import c15_identity
        try:
            official_records,identity_info=c15_identity.load_official(
                path_from(paths['analysis_daily_db']),path_from(config['identity_checkpoint']),as_of=deadline.isoformat())
        except (OSError,ValueError,sqlite3.Error,KeyError) as error:
            official_records=[]
            identity_info=dict(status='unverified',error_type=type(error).__name__)
        # Legacy preview's retrospective reference remains separate; only
        # identity evidence available before this cutoff enters the new view.
        leadership_products={r['stock_code']:r for r in official_records}
    result['morning_leadership']=c15_leadership.analyze(report,theme_records,leadership_products,day=day,
                                                     policy=policy,official_records=official_records)
    if freshness is not None:
        result['morning_leadership']['turnover_freshness']=freshness
        if freshness['is_fallback']:
            result['morning_leadership']['scope']='0198 현재 TOP20 + 표시된 이전 0186 페이지의 합집합 (혼합 시각 참고)'
    if identity_info is not None:
        view=result['morning_leadership']
        view['identity_validation']=identity_info
        chosen=selected['0186']['snapshot'] if selected['0186']['status']=='fresh' else None
        try:
            view['turnover_validation']=c15_identity.turnover_evidence(turnover,chosen)
        except (OSError,ValueError,sqlite3.Error,KeyError,TypeError) as error:
            view['turnover_validation']=dict(status='unverified',error_type=type(error).__name__,amount_score_allowed=False)
        view['tracking_observations']=tracking_observations(result)
    result['text']=format_preview(result)+'\n\n'+c15_leadership.format_text(result)
    # Telegram-ready plain text only; this module has no delivery entry point.
    result['telegram_preview_text']=c15_leadership.format_text(result)
    result['telegram_preview_messages']=c15_leadership.telegram_chunks(result['telegram_preview_text'])
    return result


def tracking_observations(result):
    """Output-only comparison evidence, including sectors outside TOP3.

    No state transition or DB migration. C15-10 must first require compatible
    scope/identity versions and comparable coverage rather than infer weakness
    from a missing theme or a disappearing page member.
    """
    view=result['morning_leadership']; analysis=view['analysis']
    scores={s['name']:s for s in analysis['ranked_sectors']+analysis['held_sectors']}
    common=dict(schema_version='c15-9-sector-observation-v1',
                reference_at=result['reference_at'],as_of=result['decision_deadline_at'],
                trade_date=result['reference_at'][:10],slot=result['reference_at'][11:16],
                time_basis=result['time_basis'], source_kind='real_saved_data',
                policy_hash=latest.digest(analysis['policy']), mapping_version='c15-9-official-name-v1',
                membership_hash=latest.digest(view['mapping']['connections']),
                mapping_coverage=view['mapping']['coverage'],
                turnover_freshness=result.get('turnover_freshness'),
                observed_universe_hash=latest.digest(sorted(s['stock_code'] for s in view['stocks'])),
                source_snapshots={api:(v['snapshot'] or {}).get('snapshot_sha256') for api,v in
                                  result['analysis']['snapshot_selection']['selected'].items()},
                official_basis_date=view['identity_validation'].get('basis_date'),
                data_status=view['data_status'],scope=view['scope'],
                theme_availability_status='prior_day_reference_receipt_time_unverified',
                point_in_time_certified=False,
                external_reference_used_in_score=False,transition_state='not_evaluated')
    return [dict(common,sector=t['name'],score=scores.get(t['name'],{}).get('score'),
                 components=scores.get(t['name'],{}).get('components'),
                 rising_count=scores.get(t['name'],{}).get('rising_count',0),
                 observed_count=scores.get(t['name'],{}).get('observed_count',0),
                 mapped_observed_count=t['distinct_stocks'],stock_codes=t['stock_codes'],
                 mapped_change_available_count=t['change_available_count'],
                 raw_reported_positive_count=t['reported_positive_count'],
                 judgment='held_pending_coverage_and_identity_validation') for t in view['mapping']['themes']]


def format_preview(result):
    report=result['analysis']; slot=result['reference_at'][11:16]
    lines=[f"[ARVIS 오전 브리핑 | {result['reference_at'][:10]} {slot}]",
           '실제 저장 자료 · 발송 전 미리보기',
           '수집 슬롯 기준 / 시장 기준시각 미검증 / 판정 대기 최대 45초']
    if result.get('turnover_freshness'):
        from .c15_turnover_selection import format_source
        rank=report['snapshot_selection']['selected']['0198']
        rank_clock=rank['snapshot']['collection_started_at'][11:16] if rank['status']=='fresh' and rank['snapshot'] else '미확인'
        lines.append('분석 '+slot+' / 0198 기준 '+rank_clock+' / '+format_source(result['turnover_freshness']))
    for api in ('0198','0186'):
        times=', '.join(t[11:19] for t in result['raw_times'][api] if t) or '미확인'
        state=report['snapshot_selection']['selected'][api]['status']
        if api=='0186' and result.get('turnover_freshness',{}).get('is_fallback'):
            state='fallback_within_10m (저장 검증 통과·신규 관측 아님)'
        lines.append(f"{api}: {times} KST / {result['raw_counts'][api]}종목 / 완료시각 판정 {state}")
    if any(r['status']=='partial' for r in result['raw_turnover_status']):
        lines.append('0186: 첫 페이지 부분 수집. 전체 시장 순위로 해석하지 않음')
    lines+=['','주도 섹터 TOP 3']
    if not report['sectors']:
        lines.append('미확인: 기존 선정 기준을 충족한 검증 자료 부족')
        if report.get('candidate_filter',{}).get('counts',{}).get('unmatched'):
            lines.append('종목 적격성 마스터 코드 연결 부족 / 실측 단위·시총 검증 미완료')
    else:
        for sector in report['sectors']:
            lines.append(f"{sector['rank']}. {sector['name']}")
            for c in sector['leaders']:
                m=c['metrics']; amount=Decimal(m['turnover']['value'])/Decimal(100000000)
                cross=next((s.get('turnover_rank') for s in report['observations']['watchlist'] if s['stock_code']==c['stock_code']),None)
                lines.append(f"  {c['name']} {m['change_pct']['value']}% / {amount:,.1f}억 / 조회 {c['latest_rank']}위 / 0186 {cross or '미확인'}")
    decisions=report.get('candidate_filter',{}).get('decisions',{})
    stocks=result['saved_observations']
    products=result.get('krx_product_reference',{})
    eligible=[s for s in stocks if s.get('ranking_rank') is not None and
        decisions.get(s['stock_code'],{}).get('state') not in ('confirmed_excluded','provisional_excluded') and
        (decisions.get(s['stock_code'],{}).get('state')=='analysis_candidate' or
         products.get(s['stock_code'],{}).get('product_type')=='common_stock')]
    lines+=['','조회순위 관측 상위 3 (주도주 선정 아님)']
    for s in eligible[:3]:
        raw=s.get('raw_turnover') or {}; value=raw.get('value')
        amount=f'{value} 백만원(명세 단위·실측 미검증)' if value is not None else '미확인'
        change=s.get('raw_change')
        cross=s.get('turnover_rank')
        lines.append(f"조회 {s['ranking_rank']}위 {s['name']} {s['stock_code']} | 등락 {change+'%' if change is not None else '미확인'} | 거래대금 {amount} | 0186 {'저장 순위 '+str(cross)+'위 교차' if cross else '페이지 내 미관측/미확인'}")
        refs=result['theme_references'].get(s['stock_code'],[])
        if refs:lines.append('  과거 테마 이름 참고: '+', '.join(refs[:3])+' (코드·소속 미확정)')
    if not eligible:lines.append('미확인: 종목 유형·적격성 또는 저장 완료 근거 부족')
    elif any(decisions.get(s['stock_code'],{}).get('state')!='analysis_candidate' for s in eligible[:3]):
        lines.append('위 관측의 보통주 분류는 전일 KRX 사후 참고이며 현재 적격성 확정 아님')
    lines.append('ETF·ETN·리츠·스팩 제외, 적격성 미확인 종목 선정 보류')
    adr=result['auxiliary'].get('analysis_adr_db',{})
    rows=adr.get('rows',[])
    lines+=['','ADR20 보조 환경 (전일까지 저장된 값의 사후 조회)']
    lines += [f"{d} {market}: {Decimal(value):.2f} ({state})" if value is not None else f'{d} {market}: 미확인 ({state})' for d,market,value,state in rows]
    if not rows:lines.append('미확인')
    lines+=['오전 당시 ADR 이용 가능 여부·시장시각·거래대금 실측 단위·시총 검증은 별도',
            '시세가 없는 항목은 추정하지 않음 · API 호출 0 / Telegram 발송 없음']
    lines+=result['warnings']
    return '\n'.join(lines)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=PROJECT/'config/c15-preview.json')
    p.add_argument('--runtime-root',type=Path)
    p.add_argument('--master-db',type=Path)
    p.add_argument('--theme-db',type=Path)
    p.add_argument('--date',required=True)
    p.add_argument('--slot',choices=SLOTS,default='09:50')
    p.add_argument('--output-dir',type=Path)
    p.add_argument('--fixture-input',type=Path,help='Explicitly synthetic format preview; no DB reads')
    p.add_argument('--strict-slots',action='store_true',help='Legacy exact-slot-only regression mode')
    args=p.parse_args(argv)
    if args.fixture_input:
        document=json.loads(args.fixture_input.read_text(encoding='utf8'))
        if document.get('source_kind')!='fixture':
            raise ValueError('explicit_synthetic_fixture_required')
        report=am_briefing.analyze(document)
        result=dict(source_kind='synthetic_fixture',analysis=report,paths={},auxiliary={},
                    text='[테스트 데이터 전용 · 실전 검증 아님]\n'+am_briefing.format_mobile(report),api_calls=0,telegram_sent=False)
    else:
        try:
            config=json.loads(args.config.read_text(encoding='utf-8-sig'))
            if args.strict_slots:config['turnover_fallback']=False
            result=generate(config,day=args.date,slot=args.slot,runtime_root=args.runtime_root,
                            master_db=args.master_db,theme_db=args.theme_db)
        except (OSError,sqlite3.Error,KeyError,ValueError) as error:
            print('미검증: 설정·저장 자료 접근 또는 형식 확인 필요 ('+type(error).__name__+')')
            return 0
    output=(args.output_dir or PROJECT/'diagnostics/c15-previews'/datetime.now(KST).strftime('%Y%m%d-%H%M%S-%f')).absolute()
    if output.resolve()!=output or not output.is_relative_to(PROJECT/'diagnostics'):
        raise ValueError('new_development_preview_directory_required')
    output.mkdir(parents=True,exist_ok=False)
    (output/'preview.txt').write_text(result['text'],encoding='utf8')
    if result.get('telegram_preview_text'):
        (output/'telegram-preview.txt').write_text(result['telegram_preview_text'],encoding='utf8')
    (output/'preview.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(result['text'])
    print('\nDB 경로: '+json.dumps({k:v for k,v in result['paths'].items() if k!='logs'},ensure_ascii=False))
    print('KRX/ADR 경로: '+json.dumps({k:v['path'] for k,v in result['auxiliary'].items()},ensure_ascii=False))
    print('저장: '+str(output))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
