"""Session orchestration over existing C12/6-D/7-A decisions; no scoring or API."""
from copy import deepcopy
import gzip
import json
from pathlib import Path

from . import am_briefing, c12_chart_bridge, leader_tracking
from .schedule import AM_DECISION_SLOTS, PM_REVIEW_SLOTS


def morning_transition(report, slot, previous=None):
    if slot not in AM_DECISION_SLOTS:raise ValueError('invalid_morning_phase')
    am_briefing.validate(report)
    if report['selection_cutoff_at'][11:16]!=slot:raise ValueError('phase_cutoff_mismatch')
    identity=(report['trade_date'],report['environment'],report['market'])
    state=deepcopy(previous) if previous else dict(day=identity[0],environment=identity[1],market=identity[2],
        phases=[],selected=[],primary_sector=None,revision=0,closed=False)
    if (state['day'],state['environment'],state['market'])!=identity:raise ValueError('session_scope_mismatch')
    if slot in state['phases']:return state,dict(action='duplicate_phase',send=False)
    if state['closed'] or (state['phases'] and slot<state['phases'][-1]):raise ValueError('closed_or_reversed_session')
    selected=c12_chart_bridge.published_top(report)
    codes=[r['stock_code'] for r in selected]
    sector=report['sectors'][0]['name'] if report['sectors'] else None
    old=set(state['selected']); new=set(codes)
    action='held_unselected' if not old else 'maintained'
    send=False
    if new and (new!=old or sector!=state['primary_sector']):
        action='first_selection' if not old else 'important_change'
        state.update(selected=codes,primary_sector=sector,revision=state['revision']+1)
        send=True
    elif old and not new:
        # Missing observations never count as proof that a selected stock failed.
        decisions=report.get('candidate_filter',{}).get('decisions',{})
        excluded=all(decisions.get(code,{}).get('state')=='confirmed_excluded' for code in old)
        if excluded and report['completeness']['top20_complete']:
            state.update(selected=[],primary_sector=None,revision=state['revision']+1)
            action='verified_withdrawal';send=True
        else:action='previous_selection_evidence_held'
    state['phases'].append(slot)
    state['closed']=slot=='09:50'
    if slot=='09:50' and not state['selected'] and state['revision']==0:
        return state,dict(action='final_insufficient',send=True,revision=1,session='AM_SINGLE_DECISION')
    return state,dict(action=action,send=send,revision=state['revision'],session='AM_SINGLE_DECISION')


def afternoon_review(report, slot):
    if slot not in PM_REVIEW_SLOTS:raise ValueError('invalid_afternoon_phase')
    # Step 6-D already determines whether final closing review is allowed.
    selected=[deepcopy(row) for row in report['rows']
        if row.get('qualification',{}).get('final_review_allowed') is True
        and row['qualification'].get('review_status')=='검토 가능'
        and row.get('candidate_confirmed') is False and not row.get('separate_type_review')]
    return dict(phase=slot,candidates=selected[:5],reviewable=selected,status='reviewable' if selected else 'provisional',
        send=slot=='15:00',candidate_confirmed=False,order_signal=False)


def tracking_records(report, selected_codes, *, captured_at, market='KRX'):
    result=[]
    # Reuse Step 7-A's immutable snapshot and evidence chronology checks.
    for row in report['rows']:
        code=row['stock_code']
        snapshot=leader_tracking.snapshot_from_review(report,stock_code=code,captured_at=captured_at,
            created_at=captured_at,market=market,d0_bar=None,stock_name=row.get('stock_name'))
        result.append(dict(episode_id=snapshot.episode_id,stock_code=code,
            role='selected' if code in selected_codes else 'comparison',
            snapshot=dict(stock_code=snapshot.stock_code,environment=snapshot.environment,market=snapshot.market,
                d0=snapshot.d0.isoformat(),captured_at=snapshot.captured_at.isoformat(),created_at=snapshot.created_at.isoformat(),
                payload=json.loads(snapshot.payload_json)),
            performance_status='awaiting_verified_calendar_and_D0_D1_D2',is_execution_record=False))
    return result


def review_performance(record, events, *, calendar, as_of):
    """A verified caller-provided calendar/events feed the original Step 7-A engine."""
    from datetime import date
    value=record['snapshot']
    snapshot=leader_tracking.Snapshot(value['stock_code'],value['environment'],value['market'],
        date.fromisoformat(value['d0']),leader_tracking.timestamp(value['captured_at']),
        leader_tracking.timestamp(value['created_at']),leader_tracking.canonical(value['payload']))
    if snapshot.episode_id!=record['episode_id']:raise ValueError('episode_scope_mismatch')
    return leader_tracking.analyze(snapshot,events,calendar=calendar,as_of=as_of)


def write_daily_summary(path, document):
    """Retain originals; compression is not deletion or a fabricated D+ outcome."""
    path=Path(path)
    if path.is_symlink() or path.exists():raise ValueError('existing_summary_preserved')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as stream:
            stream.write(json.dumps(document,ensure_ascii=False,allow_nan=False).encode('utf-8'))
    return dict(original_retention_trading_days_proposal=10,automatic_deletion=False,
        summary_retention='long_term',performance_retention='long_term')
