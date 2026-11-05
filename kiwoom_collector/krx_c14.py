"""AR-2: read-only KRX rows -> existing C14 contracts and immutable tracker.

KRX is not represented as ka10081. Receipts, trade dates, analysis clocks and
adapter materialization clocks remain distinct. No operational writers.
"""
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from html import escape

from . import krx_adr as aq, leader_tracking as tracking
from .am_candidate_adapter import normalize_code
from .daily_chart import fingerprint
from .analyst.observed_daily import analyze_observed_bars
from .analyst.context import analyze_context, high52_reference
from .analyst.card import chart_card

ROOT=aq.daily.master.PROJECT/'diagnostics/c12-ar2'
BASIS='krx-saved-price-adjustment-unverified-v1'


class SessionIndex:
    """Verified observed OPEN sessions; empty weekdays are UNKNOWN, never holidays.

    Delegate offsets to the original explicit-date calendar implementation.
    Weekends are closed; absent/empty weekdays stop traversal conservatively.
    """
    def __init__(self,open_dates,*,source,known_at):
        dates=tuple(sorted(set(date.fromisoformat(d) if isinstance(d,str) else d for d in open_dates)))
        self.open_dates=dates;self.source=source;self.known_at=tracking.timestamp(known_at)
        closed=[]
        if dates:
            d=dates[0]
            while d<=dates[-1]:
                if d.weekday()>4:closed.append(d)
                d+=timedelta(days=1)
        self._calendar=tracking.FixtureCalendar(dates,tuple(closed),source)

    def offset(self,origin,sessions):
        return self._calendar.offset(origin,sessions)

    def visible(self,as_of):
        return self if tracking.timestamp(as_of)>=self.known_at else SessionIndex((),source='session_index_not_yet_known',known_at=self.known_at)


def session_index(db):
    """Both markets must be published, row-complete and already actually received."""
    rows=db.execute('''SELECT m.day,m.market,s.received_at,
        (SELECT COUNT(*) FROM bars b WHERE b.snapshot_id=m.current_snapshot) AS actual_rows,
        (SELECT COUNT(*) FROM raw_rows r WHERE r.snapshot_id=m.current_snapshot) AS raw_rows,
        (SELECT COUNT(*) FROM raw_rows r WHERE r.snapshot_id=m.current_snapshot AND error IS NOT NULL) AS invalid
        FROM market_days m JOIN snapshots s ON s.id=m.current_snapshot ORDER BY m.day,m.market''').fetchall()
    byday=defaultdict(list)
    for r in rows:byday[r['day']].append(r)
    complete=[d for d,rs in byday.items() if {r['market'] for r in rs}=={'KOSPI','KOSDAQ'}
              and len(rs)==2 and all(r['actual_rows']==r['raw_rows']>0 and not r['invalid'] for r in rs)]
    known=max((tracking.timestamp(r['received_at']) for d in complete for r in byday[d]),default=datetime(9999,1,1,tzinfo=tracking.KST))
    return SessionIndex(complete,source='KRX_both_market_published_row_complete_index; empty_weekdays_unknown',known_at=known)


def read_inputs(codes,source=aq.SOURCE):
    codes=tuple(codes)
    if not codes or any(not isinstance(c,str) or normalize_code(c)!=c for c in codes):
        raise ValueError('exact_six_character_codes_required')
    with closing(aq.readonly(source)) as db:
        db.execute('BEGIN')
        if tuple(db.execute('SELECT version,synthetic FROM meta').fetchone())!=(1,0):
            raise ValueError('actual_krx_daily_required')
        index=session_index(db)
        result={c:[] for c in codes}
        for r in db.execute('SELECT * FROM daily_latest WHERE stock_code IN ('+','.join('?' for _ in codes)+') ORDER BY stock_code,day',codes):
            result[r['stock_code']].append(dict(r))
    return result,index


def usable(row):
    try:
        values={k:Decimal(row[k]) for k in ('open','high','low','close','volume')}
        return (all(v.is_finite() for v in values.values()) and values['volume']>0
            and values['low']>0 and values['low']<=min(values['open'],values['close'])
            <=max(values['open'],values['close'])<=values['high'] and not json.loads(row['issues_json']))
    except (TypeError,ValueError,KeyError,InvalidOperation):return False


def visible_rows(rows,*,code,as_of):
    cutoff=tracking.timestamp(as_of);visible=[];held=[]
    for r in rows:
        if r['stock_code']!=code:raise ValueError('krx_stock_code_mismatch')
        # A date or file mtime never proves availability. Unknown clocks are excluded.
        try:available=tracking.timestamp(r['collected_at'])
        except (KeyError,ValueError,TypeError):
            held.append(dict(day=r['day'],reason='availability_unknown'));continue
        if available>cutoff or date.fromisoformat(r['day'])>=cutoff.date():
            held.append(dict(day=r['day'],reason='not_available_or_completed_before_analysis'));continue
        if available<tracking.timestamp(r['day']+'T16:00:00+09:00'):
            held.append(dict(day=r['day'],reason='daily_receipt_before_conservative_close_boundary'));continue
        visible.append(r)
    if len({r['day'] for r in visible})!=len(visible):raise ValueError('duplicate_krx_stock_day')
    return sorted(visible,key=lambda r:r['day']),held


def c14_input(rows,index,*,code,name,as_of,environment='real'):
    cutoff=tracking.timestamp(as_of)
    visible,unavailable=visible_rows(rows,code=code,as_of=cutoff)
    idx=index.visible(cutoff)
    sessions=[d.isoformat() for d in idx.open_dates if d<cutoff.date()]
    bars=[]
    for r in visible:
        bars.append(dict(trade_date=r['day'],stock_code=code,market='KRX',environment=environment,
            **{k:r[k] for k in ('open','high','low','close','volume')},source='KRX_SQLite:'+r['snapshot_id'],
            source_reference=dict(snapshot_id=r['snapshot_id'],provider='KRX',market_segment=r['market']),
            available_at=r['collected_at'],market_at=r['day']+'T16:00:00+09:00',
            finalized_at=r['collected_at'],status='observed_finality_unverified',basis_id=BASIS,
            price_unit='KRW',volume_unit='share',session_basis='KRX_saved_daily_unverified_adjustment'))
    document=dict(schema_version='analyst-input-v1',stock_code=code,stock_name=name,
        environment=environment,market='KRX',data_origin='api_response',as_of=cutoff.isoformat(),
        session_basis='KRX_saved_daily_unverified_adjustment',bars=bars,
        price_basis=dict(id=BASIS,mode='unverified',verified=False,corporate_actions_checked=False,
            comparable=False,volume_comparable=False,source='KRX_saved_daily',available_at=cutoff.isoformat()),
        calendar=dict(verified=False,completed_sessions=sessions,source=index.source,
            available_at=index.known_at.isoformat(),as_of=cutoff.isoformat()),
        provider='KRX',selection=None,price=None)
    return document,visible,unavailable,sessions


def make_card(rows,index,*,code,name,as_of,png=None):
    doc,visible,unavailable,sessions=c14_input(rows,index,code=code,name=name,as_of=as_of)
    strict=analyze_context(doc)
    byday={r['day']:r for r in visible}
    # Retain only the trailing uninterrupted valid price history for the mini chart.
    # No missing/new-listing/zero-price row is silently bridged to reach 60.
    eligible=[]
    for d in reversed(sessions):
        r=byday.get(d)
        if r is None or not usable(r):break
        eligible.append(r)
    eligible.reverse()
    bars=[dict(trade_date=r['day'],**{k:Decimal(r[k]) for k in ('open','high','low','close','volume')},
        page_fingerprint=r['snapshot_id'],row_fingerprint=fingerprint([r['snapshot_id'],r['official_code_raw']])) for r in eligible]
    observed=analyze_observed_bars(bars,stock_code=code,environment='real',market='KRX',as_of=as_of,
        data_origin='api_response',base_date=sessions[-1].replace('-','') if sessions else 'unavailable',
        adjustment_request='unverified_KRX_not_a_Kiwoom_request',units_basis='KRX_saved_OHLCV_no_amount_conversion',
        page_fingerprints=list(dict.fromkeys(r['snapshot_id'] for r in eligible)))
    high=high52_reference(sessions,{r['day']:r for r in eligible},Decimal(eligible[-1]['close']) if eligible else None)
    if high['status']=='available':high['status']='provisional'
    observed['high52']=high
    observed['provider']='KRX'
    observed['availability']=dict(excluded_count=len(unavailable),visible_rows=len(visible),
        contiguous_valid_rows=len(eligible),received_times=[r['collected_at'] for r in eligible])
    observed['analysis_reference']=fingerprint({k:v for k,v in observed.items() if k!='analysis_reference'})
    card=chart_card(strict,observed_daily=observed,mini_chart_output=png)
    card['provenance']['provider']='KRX'
    card['provenance']['price_basis_status']='adjustment_and_corporate_actions_unverified'
    card['provenance']['market_at_status']='nominal_16h_daily_boundary_not_official_market_timestamp'
    card['provenance']['availability_filter']='actual_collected_at <= analysis_as_of; trade_date < analysis_date'
    card['card_reference']=fingerprint({k:v for k,v in card.items() if k!='card_reference'})
    return dict(input=doc,strict_context=strict,observed=observed,card=card,excluded=unavailable)


def track(snapshot,rows,index,*,as_of,materialized_at,diagnostic=False,basis_review=None):
    """Use original tracker, leave D0 intact. Production needs explicit basis review.

    Diagnostic arithmetic is opt-in and cannot be applied to a selection snapshot.
    All adapted events become available no earlier than this batch materialization.
    This permits re-evaluation today without inventing receipt times in the past.
    """
    original=tracking.canonical(snapshot.payload);cutoff=tracking.timestamp(as_of)
    materialized=tracking.timestamp(materialized_at)
    if diagnostic and snapshot.payload.get('record_kind')!='diagnostic_anchor_not_selection':
        raise ValueError('diagnostic_cannot_bypass_production_snapshot')
    try:
        reviewed=(isinstance(basis_review,dict) and basis_review.get('episode_id')==snapshot.episode_id
            and basis_review.get('basis_id')==BASIS and basis_review.get('verified') is True
            and bool(basis_review.get('evidence_reference'))
            and basis_review.get('price_adjustment_compatible') is True
            and basis_review.get('volume_basis_compatible') is True
            and tracking.timestamp(basis_review['verified_at'])<=cutoff)
    except (KeyError,ValueError,TypeError):reviewed=False
    holds=[]
    if not diagnostic and not reviewed:holds.append('d0_price_adjustment_compatibility_unverified')
    if snapshot.d0_bar and not diagnostic and (not reviewed or basis_review.get('d0_bar_fingerprint')!=snapshot.d0_bar.fingerprint):
        holds.append('d0_bar_basis_binding_unverified')
    events=[]
    idx=index.visible(cutoff)
    if not holds:
        visible,_=visible_rows(rows,code=snapshot.stock_code,as_of=cutoff)
        for r in visible:
            if not usable(r):continue
            available=max(materialized,tracking.timestamp(r['collected_at']))
            market_at=tracking.timestamp(r['day']+'T16:00:00+09:00')
            if available<market_at:continue
            events.append(tracking.Observation(snapshot.stock_code,snapshot.environment,snapshot.market,
                date.fromisoformat(r['day']),market_at,available,'post_market','KRX_SQLite_diagnostic' if diagnostic else 'KRX_SQLite_reviewed',
                **{k:Decimal(r[k]) for k in ('open','high','low','close','volume')},
                raw_json=tracking.canonical(dict(provider='KRX',snapshot_id=r['snapshot_id'],
                    original_collected_at=r['collected_at'],materialized_at=materialized.isoformat(),
                    price_basis=BASIS,nominal_market_time=True,diagnostic=diagnostic))))
    result=tracking.analyze(snapshot,events,calendar=idx,as_of=cutoff)
    if original!=tracking.canonical(snapshot.payload):raise ValueError('d0_snapshot_changed')
    result.update(provider='KRX',diagnostic=diagnostic,validation_status='provisional' if diagnostic else 'reviewed' if reviewed else 'held',
        stock_code=snapshot.stock_code,d0=snapshot.d0.isoformat(),
        holds=holds,price_basis_status='adjustment_unverified' if diagnostic else 'reviewed' if reviewed else 'unverified',
        d0_unchanged=True,market_at_status='nominal_daily_16h_not_exchange_time',
        adapted_events=len(events),materialized_at=materialized.isoformat(),order_signal=False)
    return result


def export_charts(codes,names,output,*,as_of,source=aq.SOURCE):
    """Exclusive development artifact writer; source SQLite is always read-only."""
    output=Path(output).absolute()
    if output.resolve()!=output or ROOT.resolve()!=ROOT.absolute() or not output.is_relative_to(ROOT.absolute()) or output.exists():
        raise ValueError('new_ar2_diagnostics_directory_required')
    from .leader_calendar import supplemental_product
    products={c:supplemental_product(c) for c in codes if c=='122630'}
    selected=[c for c in codes if products.get(c,{}).get('policy_state')!='excluded']
    rows,index=(read_inputs(selected,source) if selected else ({},SessionIndex((),source='no_equity_codes',known_at=as_of)))
    output.mkdir(parents=True)
    items=[]
    for code in selected:
        folder=output/code;folder.mkdir()
        result=make_card(rows[code],index,code=code,name=names.get(code,code),as_of=as_of,png=folder/'mini.png')
        for kind,doc in result.items():
            (folder/(kind+'.json')).write_text(tracking.canonical(doc),encoding='utf8')
        items.append(dict(stock_code=code,stock_name=names.get(code,code),
            status=result['card']['mini_chart_status'],candles=result['card'].get('mini_chart_metadata',{}).get('candle_count',0),
            high52_status=result['card']['high52']['status'],contiguous_bars=result['observed']['bars_used']))
    summary=dict(schema_version='arvis-krx-c14-bundle-v1',as_of=tracking.timestamp(as_of).isoformat(),
        provider='KRX',price_basis_status='unverified',items=items,excluded_products=products,
        source_sha256=aq.sha(source),session_index=dict(open_dates=[d.isoformat() for d in index.open_dates],
            source=index.source,known_at=index.known_at.isoformat(),empty_weekdays='unknown_not_assumed_closed'),
        api_calls=0,production_d0_writes=0)
    (output/'summary.json').write_text(tracking.canonical(summary),encoding='utf8')
    cards=[]
    for item in items:
        c=item['stock_code']
        image='<img src="'+c+'/mini.png" alt="'+escape(item['stock_name'])+' KRX 저장 일봉">' if item['status']!='unavailable' else '<p>시세 부족·검토 필요: 차트 미산출</p>'
        cards.append('<article><h2>'+escape(c+' '+item['stock_name'])+'</h2>'+image+
            '<p>'+str(item['candles'])+'개 캔들 · '+escape(item['status'])+' · 252일 고점 '+escape(item['high52_status'])+
            '</p><a href="'+c+'/card.json">기존 C14 chart-card JSON</a></article>')
    html='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ARVIS KRX · C14 연동 검증</title><style>body{font:14px/1.5 system-ui,"Malgun Gothic",sans-serif;background:#f2f5f8;color:#26394d;margin:0}main{max-width:1320px;margin:auto;padding:22px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}article{background:white;border:1px solid #dce5ed;border-radius:10px;padding:14px;min-width:0}img{width:100%;height:auto}h1{font-size:25px}h2{font-size:17px}.notice{background:#fff5e2;padding:12px;border-radius:8px;margin:12px 0}a{color:#245aa7}@media(max-width:700px){main{padding:12px}.grid{grid-template-columns:1fr}}</style><main><h1>KRX 저장 일봉 · C14 차트 연동</h1><p>'''+escape(summary['as_of'])+''' 기준 · KRX 원본 시세</p><div class="notice">수정주가·기업행위·가격 비교 기준 미확정: 모든 수치는 잠정입니다. 실제 매수 선정이나 D0 기록이 아닙니다. 수집 시각이 분석시각 이후인 일봉은 제외합니다. 52주 기준은 기존 C14의 이전 252거래일 고점입니다.</div><div class="grid">'''+''.join(cards)+'</div></main></html>'
    (output/'charts.html').write_text(html,encoding='utf8')
    return summary,rows,index
