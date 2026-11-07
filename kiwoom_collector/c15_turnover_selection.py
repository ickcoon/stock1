"""C15-10B bounded same-day fallback over existing C12 snapshot validation."""
import json

from .am_time_policy import instant
from . import c12_latest as latest
from .turnover_rank import normalize, Profile


def select(snapshots, *, reference_at, deadline_at, max_age_seconds=600):
    if type(max_age_seconds) is not int or not 0<max_age_seconds<=600:
        raise ValueError('turnover_fallback_limit_must_not_exceed_600_seconds')
    reference,deadline=instant(reference_at),instant(deadline_at)
    if not reference<=deadline or reference.date()!=deadline.date():raise ValueError('invalid_turnover_cutoff')
    exact,past,rejected=[],[],[]
    for s in snapshots:
        if s.get('api')!='0186':continue
        reason=None
        try:
            request=instant(s['request_started_at']);start=instant(s['collection_started_at'])
            same_slot=request.date()==reference.date() and request.strftime('%H:%M')==reference.strftime('%H:%M')
            bound=deadline if same_slot else reference
            chosen=latest.select([s],as_of=bound.isoformat(),environment='real')['selected']['0186']
            if chosen['status']!='fresh':reason='storage_scope_or_completion_invalid'
            elif not same_slot and not (0<=(reference-start).total_seconds()<=max_age_seconds):reason='outside_same_day_age_window'
            else:
                ranks=[]
                for row in s['rows']:
                    raw=json.loads(row['raw_json']);parsed=normalize(raw,Profile(**s['profile']))
                    if (row.get('validation_status')!='syntax_valid' or parsed['status']!='syntax_valid'
                            or parsed['normalized']['stock_code']!=row['stock_code']
                            or parsed['normalized']['turnover_rank']!=row['turnover_rank']):
                        raise ValueError('invalid_turnover_row')
                    ranks.append(row['turnover_rank'])
                if len(set(ranks))!=len(ranks):raise ValueError('duplicate_turnover_rank')
                (exact if same_slot else past).append((request,instant(s['response_received_at']),s))
        except (ValueError,TypeError,KeyError):reason='invalid_turnover_evidence'
        if reason:rejected.append(dict(id=s.get('id'),reason=reason))
    candidates=sorted(exact or past,key=lambda row:(row[0],row[1]))
    meta=dict(state='missing',is_fallback=False,reference_at=reference.isoformat(),
              decision_deadline_at=deadline.isoformat(),max_age_seconds=max_age_seconds,
              age_anchor='collection_started_at',rejected=rejected,
              market_asof_verified=False,reuse_proves_increase=False)
    if not candidates:return dict(snapshot=None,freshness=meta)
    if len(candidates)>1 and candidates[-1][:2]==candidates[-2][:2]:
        return dict(snapshot=None,freshness=dict(meta,state='ambiguous'))
    _,_,s=candidates[-1]
    fallback=not bool(exact)
    meta.update(state='fallback_within_10m' if fallback else 'exact_slot',is_fallback=fallback,
        snapshot_id=s['id'],snapshot_sha256=s['snapshot_sha256'],
        collection_started_at=s['collection_started_at'],request_started_at=s['request_started_at'],
        response_received_at=s['response_received_at'],db_committed_by=s['db_committed_by'],
        age_seconds_at_reference=max(0,(reference-instant(s['collection_started_at'])).total_seconds()),
        slot_lag_minutes=max(0,int((reference-instant(s['request_started_at']).replace(second=0,microsecond=0)).total_seconds()/60)),
        partial=s.get('original_status')=='partial')
    return dict(snapshot=s,freshness=meta)


def format_source(freshness):
    if freshness['state'] not in ('exact_slot','fallback_within_10m'):
        return '0186 기준 미확인 (검증된 동일 슬롯/과거 10분 이내 자료 선택 불가: '+freshness['state']+')'
    clock=freshness['request_started_at'][11:16]
    if freshness['is_fallback']:
        return f"0186 기준 {clock} ({freshness['slot_lag_minutes']}분 전 대체 자료, 최신 실시간 아님)"
    return f'0186 기준 {clock} (동일 슬롯)'
