"""AR-2 query interface only: external close records vs ARVIS selections/captures.

Never turns external close notes into morning knowledge or capture into selection.
Unknown historical availability is bounded by first adapter materialization, not mtime.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date

from . import c12_external_theme_bridge as bridge
from . import external_theme_master as external
from .leader_tracking import timestamp
from .equity_eligibility import decide


def available(record,materialized_at,*,external_close=False):
    known=timestamp(record.get('available_at') or materialized_at)
    if external_close:
        # This is a conservative daily boundary, not asserted publication time.
        known=max(known,timestamp(record['trade_date']+'T16:00:00+09:00'))
    observed=record.get('observed_at')
    if observed:known=max(known,timestamp(observed))
    return known


def query_day(day,loaded,*,as_of,materialized_at,arvis_records=(),captures=(),episodes=()):
    date.fromisoformat(day);cutoff=timestamp(as_of);materialized=timestamp(materialized_at)
    visible=deepcopy(loaded)
    if cutoff<materialized:
        visible['metadata']['missing_capture_dates']=[]
    withheld=dict(external_records=0,external_themes=0,arvis_records=0,captures=0,episodes=0)
    # The same cutoff applies to stock-less theme notes and stock observations.
    for key,count_key in (('records','external_records'),('theme_records','external_themes')):
        values=[]
        for r in loaded[key]:
            if available(r,materialized,external_close=True)<=cutoff:values.append(deepcopy(r))
            elif r['trade_date']==day:withheld[count_key]+=1
        visible[key]=values
    own=[];capture=[]
    for records,target,count_key in ((arvis_records,own,'arvis_records'),(captures,capture,'captures')):
        for r in records:
            if r['trade_date']!=day:continue
            if available(r,materialized)<=cutoff:target.append(deepcopy(r))
            else:withheld[count_key]+=1
    # Existing bridge validates ARVIS selection slots and keeps separate lanes.
    result=bridge.calendar(visible,day,arvis_records=own)
    result['arvis_capture']=dict(records=capture,selection_confirmed=False,
        notice='Capture is not a D0 selection or execution; original source_kind retained')
    repeats=defaultdict(set)
    for r in visible['records']:
        if r['trade_date']<=day:repeats[r['raw_theme']].add(r['trade_date'])
    result['external_theme_repetitions']=[dict(raw_theme=k,observed_dates=sorted(v),appearance_days=len(v),
        source_kind=external.SOURCE_KIND,leadership_confirmed=False) for k,v in sorted(repeats.items())]
    selected=[]
    for episode in episodes:
        if episode.get('d0')!=day and not any(d.get('trade_date')==day for d in episode.get('days',[])):continue
        if timestamp(episode['as_of'])>cutoff:withheld['episodes']+=1;continue
        selected.append(deepcopy(episode))
    result.update(schema_version='arvis-leader-calendar-query-v1',as_of=cutoff.isoformat(),
        materialized_at=materialized.isoformat(),availability_basis='explicit_available_at_or_first_adapter_read_never_file_mtime',
        withheld=withheld,tracking=selected,automatic_signal=False,
        actual_selection_count=len(result['arvis_selection']['records']),
        captured_observation_count=len(capture),external_is_arvis_selection=False)
    return result


def supplemental_product(code):
    """Evidence-scoped ETF resolution; do not rewrite AO/AR-1 or a D0 decision."""
    if code!='122630':return dict(stock_code=code,state='unresolved',policy_state='pending')
    record=dict(stock_code=code,security_type='ETF',type_verified=True,
        type_evidence='KRX KIND ETF issue summary: 122630 / KR7122630007',market='KOSPI')
    return dict(stock_code=code,name='KODEX 레버리지',standard_code='KR7122630007',
        product_type='ETF',identity_status='official_supplemental_product_match',
        state='identified',policy_state=decide(record)['state'],policy=decide(record),
        source='KRX_KIND_ETF_issue_summary',
        evidence_url='https://kind.krx.co.kr/disclosure/etfisudetail.do?method=searchEtfIsuSummary&strIsurCd=12263',
        issuer_evidence_url='https://m.samsungfund.com/sheet/20230215/2ETF25_20230131.pdf',
        reviewed_on='2026-11-04',primary_master_unchanged=True,
        attention_observation_retained=True,not_a_new_equity_master_row=True)
