"""Opt-in C15 adapter for the existing frozen Workflow and Telegram transport."""
from copy import deepcopy
import json
from pathlib import Path
from .c12_latest import digest as briefing_digest

from . import c15_briefing, c15_preview, telegram_briefing as tg
from .am_time_policy import instant
from . import c15_references

SCHEMA='c15-morning-delivery-v1'
SLOTS=('09:40','09:45','09:50')
PM_SCHEMA='c15-afternoon-reference-v1'


def pm_summary(report):
    validate_pm(report)
    data=report['reference']
    lines=['[ARVIS 오후 참고용 분석] '+report['selection_cutoff_at'][:16].replace('T',' '),
           'TOP 섹터: '+' / '.join(g['name'] for g in data['sectors'])]
    for i,row in enumerate(data['candidates'],1):
        from decimal import Decimal
        cash=f"{Decimal(row['turnover_raw'])/100:,.0f}억" if row.get('turnover_raw') is not None else '미확인'
        lines.append(f"{i}. {row['stock_name']} {row['change_percent_raw']}% / {cash} / {row['sectors'][0]}")
        lines.append('   '+row['qualification']['reasons'][0])
    if not data['candidates']:lines.append('참고 후보 없음')
    lines.append('부분 수집·과거 시총 참고값·종가 유동성 조건 미충족 후보 포함. 매매 적격 확정·주문 신호 아님.')
    return '\n'.join(lines)


def wrap_pm(data):
    # Persist no source paths, DB rows, keys or external response bodies.
    reference=dict(candidates=data['reference']['candidates'],sectors=data['reference']['sectors'],
                   mode='reference_only',execution_allowed=False)
    report=dict(schema_version=PM_SCHEMA,selection_type='PM_CLOSING_REVIEW',source_kind='readonly_adapter',
        environment='real',market='KRX',trade_date=data['day'],selection_cutoff_at=data['analysis_at'],
        candidate_confirmed=False,reference=reference)
    report['evaluation_reference']=tg.digest(report)
    validate_pm(report)
    return report


def validate_pm(report):
    tg.require(set(report)==set('schema_version selection_type source_kind environment market trade_date selection_cutoff_at candidate_confirmed reference evaluation_reference'.split()))
    tg.require(report['schema_version']==PM_SCHEMA and report['selection_type']=='PM_CLOSING_REVIEW')
    tg.require(report['source_kind']=='readonly_adapter' and report['environment']=='real' and report['market']=='KRX')
    at=instant(report['selection_cutoff_at'])
    tg.require(at.strftime('%H:%M') in ('14:30','15:00') and report['trade_date']==at.date().isoformat())
    tg.require(report['candidate_confirmed'] is False and report['reference']['execution_allowed'] is False
               and report['reference']['mode']=='reference_only')
    rows=report['reference']['candidates']
    tg.require(isinstance(rows,list) and len(rows)<=5 and len({r['stock_code'] for r in rows})==len(rows))
    tg.require(all(r['candidate_confirmed'] is False and r['reference_only'] is True
                   and r['qualification']['final_review_allowed'] is False for r in rows))
    tg.require(report['evaluation_reference']==tg.digest({k:v for k,v in report.items() if k!='evaluation_reference'}),'report_fingerprint_mismatch')
    return report


def process_pm(workflow,due,*,ranking_path,turnover_path,folder):
    """Existing PM schedule, existing one-shot transport; no replay catch-up."""
    from .c16_sessions import replay_afternoon
    from .operating_workflow import write_new
    path=folder/'telegram.json';replay=path.exists();snapshot_status=None
    try:
        if replay:report=validate_pm(json.loads(path.read_text(encoding='utf8')))
        else:
            data=replay_afternoon(workflow.root/'reports',due.date().isoformat(),due.strftime('%H:%M'),
                ranking_path=ranking_path,turnover_path=turnover_path,settings=workflow.settings)
            if not data or not data.get('reference'):return dict(status='held',reason='saved_pm_pair_unavailable',delivery=dict(status='not_generated'))
            report=wrap_pm(data);write_new(path,report)
            try:
                from .c16_calendar import capture_pm
                origin='analysis_capture' if 0<=(instant(workflow.clock())-instant(due)).total_seconds()<=120 else 'posthoc_reference'
                snapshot_status=capture_pm(data,root=workflow.root/'reports/leader-snapshots',source=str(path),origin=origin)
            except Exception as error:snapshot_status=dict(status='snapshot_failed',error_type=type(error).__name__)
        text=tg.summary(report)
        preview=folder/'telegram.txt'
        if not preview.exists():preview.write_text(text,encoding='utf8')
    except (ValueError,OSError,KeyError,TypeError) as error:
        return dict(status='message_generation_failed',error_type=type(error).__name__,delivery=dict(status='not_attempted'))
    age=(instant(workflow.clock())-instant(due)).total_seconds()
    status=('frozen_replay_no_send' if replay else 'disabled' if workflow.settings.get('telegram_send_enabled') is not True
            else 'expired_no_catchup' if not 0<=age<=120 else 'ready')
    delivery=dict(status=status)
    if status=='ready':
        try:
            delivery=tg.deliver(report,text,None,tg.Configuration.from_environment(),allow_readonly_report=True,
                windows_trust_store=True,state_root=workflow.settings['notification_state'])
        except Exception as error:delivery=dict(status='send_failed',error_type=type(error).__name__)
    return dict(status='reviewed',report=str(folder),delivery=delivery,reference_only=True,snapshot=snapshot_status)


def send_policy(settings,due,now,*,replay=False):
    slots=settings.get('c15_send_slots',['09:40','09:50'])
    lag=settings.get('c15_send_max_lag_seconds',120)
    tg.require(isinstance(slots,list) and len(slots)==len(set(slots)) and all(s in SLOTS for s in slots),'invalid_c15_send_slots')
    tg.require(type(lag) is int and 0<lag<=300,'invalid_c15_send_window')
    if replay:return 'frozen_replay_no_send'
    if settings.get('c15_send_enabled') is not True or settings.get('telegram_send_enabled') is not True:return 'disabled'
    if due.strftime('%H:%M') not in slots:return 'record_only'
    age=(instant(now)-instant(due)).total_seconds()
    if not 0<=age<=lag or instant(now).date()!=instant(due).date():return 'expired_no_catchup'
    return 'ready'


def portable(value):
    if isinstance(value,dict):
        return {k:portable(v) for k,v in value.items() if not tg.am.SENSITIVE.search(k)}
    if isinstance(value,list):return [portable(v) for v in value]
    if isinstance(value,str):
        try:tg.scan(value)
        except ValueError:return 'diagnostic_only'
    return value


def wrap(briefing):
    briefing=deepcopy(briefing)
    diagnostic=briefing['diagnostics'];source=diagnostic['source_report']
    selected={s['stock_code'] for sector in briefing['analysis']['sectors'] for s in sector['leaders']}
    briefing['diagnostics']=dict(
        source_report=dict(auxiliary=dict(analysis_adr_db=source.get('auxiliary',{}).get('analysis_adr_db',{})),
                           analysis=dict(snapshot_selection=source['analysis']['snapshot_selection'])),
        stock_measurements={k:v for k,v in diagnostic['stock_measurements'].items() if k in selected})
    briefing=portable(briefing)
    briefing['evaluation_reference']=briefing_digest({k:v for k,v in briefing.items() if k!='evaluation_reference'})
    at=instant(briefing['reference_at'])
    report=dict(schema_version=SCHEMA,selection_type='AM_MOMENTUM',
        source_kind='readonly_adapter',environment='real',market='KRX',
        trade_date=at.date().isoformat(),selection_cutoff_at=at.isoformat(),
        candidate_confirmed=False,briefing=deepcopy(briefing))
    report['evaluation_reference']=tg.digest(report)
    validate(report)
    return report


def validate(report):
    tg.require(set(report)==set('schema_version selection_type source_kind environment market trade_date selection_cutoff_at candidate_confirmed briefing evaluation_reference'.split()))
    tg.require(report['schema_version']==SCHEMA and report['selection_type']=='AM_MOMENTUM')
    tg.require(report['source_kind']=='readonly_adapter' and report['environment']=='real' and report['market']=='KRX')
    tg.require(report['candidate_confirmed'] is False)
    at=instant(report['selection_cutoff_at']);b=report['briefing']
    tg.require(at.strftime('%H:%M') in SLOTS and at.second==0 and at.microsecond==0)
    tg.require(report['trade_date']==at.date().isoformat() and instant(b['reference_at'])==at)
    tg.require(b['source_kind']=='real_saved_data' and b['candidate_confirmed'] is False
               and b['api_calls']==0 and b['telegram_sent'] is False)
    tg.require(b['input_policy']=='c15-12-practical-products-v1')
    tg.require(b['evaluation_reference']==briefing_digest({k:v for k,v in b.items() if k!='evaluation_reference'}))
    tg.require(report['evaluation_reference']==tg.digest({k:v for k,v in report.items() if k!='evaluation_reference'}))
    tg.require(b['text']==c15_briefing.format_briefing(b),'message_analysis_mismatch')
    tg.require(len(b['text'].encode('utf-16-le'))//2<=4096,'message_too_long')
    return report


def process(workflow,due,*,live_context=None):
    """Default disabled; existing delivery gates remain the only send path."""
    from .operating_workflow import write_new
    tg.require(due.strftime('%H:%M') in SLOTS,'morning_briefing_scope_mismatch')
    folder=workflow.root/'reports'/('c15-'+due.strftime('%Y%m%d-%H%M'))
    path=folder/'telegram.json'
    replay=path.exists();snapshot_status=None
    if replay:
        report=json.loads(path.read_text(encoding='utf8'));tg.validate(report)
        tg.require(instant(report['selection_cutoff_at'])==due,'frozen_briefing_scope_mismatch')
    else:
        if (folder/'diagnostics.json').exists():
            return dict(status='held_incomplete_frozen_result',report=str(folder),delivery=dict(status='disabled'),final=False)
        config=json.loads(Path(workflow.settings['c15_preview_config']).read_text(encoding='utf-8-sig'))
        references=c15_references.inspect(config,day=due.date().isoformat(),slot=due.strftime('%H:%M'))
        if not (folder/'references.json').exists():write_new(folder/'references.json',references)
        if not references['ready']:
            return dict(status='held_missing_required_reference',report=str(folder),delivery=dict(status='disabled'),final=False)
        source=c15_preview.generate(config,day=due.date().isoformat(),slot=due.strftime('%H:%M'))
        display=json.loads((c15_preview.PROJECT/'config/c15-display-contract.json').read_text(encoding='utf8'))
        turnover=json.loads((c15_preview.PROJECT/'config/c15-turnover-contract.json').read_text(encoding='utf8'))
        briefing=c15_briefing.prepare(source,display,turnover,practical_products=True)
        if live_context:write_new(folder/'live-context.json',live_context)
        write_new(folder/'diagnostics.json',briefing)
        report=wrap(briefing)
        write_new(path,report)
        try:
            from .c16_calendar import capture_am
            origin='analysis_capture' if 0<=(instant(workflow.clock())-instant(due)).total_seconds()<=120 else 'saved_result_restored'
            snapshot_status=capture_am(briefing,root=workflow.root/'reports/leader-snapshots',source=str(folder/'diagnostics.json'),origin=origin)
        except Exception as error:snapshot_status=dict(status='snapshot_failed',error_type=type(error).__name__)
    text=tg.summary(report)
    preview=folder/'telegram.txt'
    if not preview.exists():preview.write_text(text,encoding='utf8')
    status=send_policy(workflow.settings,due,workflow.clock(),replay=replay)
    if status=='ready' and not (live_context or {}).get('allowed'):
        status='live_context_required'
    delivery=dict(status=status)
    if status=='ready':
        delivery=tg.deliver(report,text,None,tg.Configuration.from_environment(),
            allow_readonly_report=True,windows_trust_store=True,state_root=workflow.settings['notification_state'])
    return dict(status='reviewed',report=str(folder),delivery=delivery,final=due.strftime('%H:%M')=='09:50',snapshot=snapshot_status)
