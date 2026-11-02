"""Opt-in glue for the existing scheduler/collectors/analysis; no new API engine."""
from datetime import datetime
import json
import logging
from pathlib import Path
import time

from .client import KST
from . import schedule, online_snapshot, am_input_adapter as adapter, am_briefing
from . import cross_adapter, eligibility_dashboard, c12_chart_bridge, telegram_briefing as tg
from . import session_review
from .am_time_policy import ObservationPolicy
from .turnover_rank import LiveTransport, Profile, collect as collect_turnover

LOG=logging.getLogger(__name__)

def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)

class Workflow:
    def __init__(self,root,settings,*,clock=lambda:datetime.now(KST),sleep=time.sleep):
        if settings.get('text_only',True) is not True:raise ValueError('text_only_required')
        self.root=Path(root).resolve();self.settings=settings;self.clock=clock;self.sleep=sleep

    def __call__(self,client,store,environment,due,identifier,result):
        if environment!='real' or store.path.resolve()!=self.root/'data/stock1-real.db':
            raise ValueError('explicit_real_database_required')
        slot=due.strftime('%H:%M');day=due.date().isoformat()
        turn_dir=self.root/'data/turnover'/day
        ledger=dict(day=day,slot=slot,ranking_result=result,collection_id=identifier,environment='real')
        if slot in schedule.TURNOVER_SLOTS:
            turn_dir.mkdir(parents=True,exist_ok=True)
            path=turn_dir/(slot.replace(':','')+'.db')
            if path.exists():ledger['turnover']=dict(status='existing_attempt_preserved',api_calls=0)
            else:
                transport=LiveTransport(client.credentials,token_manager=client._token_manager)
                try:
                    from .weekly_tls import configure_windows_trust
                    configure_windows_trust(transport.session)
                    transport.token=client.token
                    transport._last_request=client._last_request
                    transport.request_interval=max(1.2,client.request_interval)
                    ledger['turnover']=collect_turnover(path,transport,Profile('000','0','1'),'real',self.settings.get('turnover_max_pages',1))
                finally:
                    if transport._last_request is not None:client._last_request=transport._last_request
                    transport.close()
        try:
            if slot in (*schedule.AM_DECISION_SLOTS,*schedule.PM_REVIEW_SLOTS):
                turns=sorted(p for p in turn_dir.glob('*.db') if p.stem<=slot.replace(':',''))
                ledger['review']=self.review(store,due,turns[-1] if turns else None)
            if slot=='16:00':ledger['retention']=self.close_day(store,due)
        except Exception as error:
            ledger['review']=dict(status='held',reason=type(error).__name__)
        write_new(self.root/'reports/workflow'/day/(slot.replace(':','')+'.json'),ledger)

    def review(self,store,due,turnover_path):
        day=due.date().isoformat();slot=due.strftime('%H:%M')
        policy=ObservationPolicy()
        if slot in schedule.AM_DECISION_SLOTS:
            remaining=(policy.deadline(due)-self.clock()).total_seconds()
            if remaining>0:self.sleep(remaining)
        capture=online_snapshot.capture_snapshot(store.path,name='c12x-'+due.strftime('%Y%m%d-%H%M'))
        if capture.get('status')!='success':return dict(status='held',reason='snapshot_unavailable')
        snapshot=Path(capture['snapshot_path'])
        generated=self.clock().isoformat()
        folder=self.root/'reports'/('c12x-'+due.strftime('%Y%m%d-%H%M'))
        folder.mkdir(parents=True,exist_ok=False)
        cutoff=due if slot in schedule.AM_DECISION_SLOTS else due.replace(hour=9,minute=50)
        master=self.settings.get('master_db')
        evidence_path=self.settings.get('verification_manifest')
        evidence=json.loads(Path(evidence_path).read_text(encoding='utf-8')) if evidence_path else None
        document=adapter.adapt(snapshot,trade_date=day,cutoff_at=cutoff.isoformat(),environment='real',
            turnover_db=turnover_path,evidence=evidence,test_mode=False,synthetic=False,
            master_db=master,candidate_filter=bool(master),observation_policy=policy.document(),
            capital_json=self.settings.get('am_capital_json'))
        analysis=am_briefing.analyze(document,generated_at=generated)
        write_new(folder/'c12.json',analysis)
        ranking=adapter.read_sources(snapshot)
        runs=[r for r in ranking['records'] if r['kind']=='ranking' and r['scheduled_at']==due.isoformat()]
        run=runs[0] if len(runs)==1 and runs[0]['status']=='success' else None
        turn=adapter.read_sources(turnover_path,role='turnover')['records'] if turnover_path else []
        if any(r.get('source_kind')!='live_test' or r.get('environment')!='real' for r in turn):
            raise ValueError('synthetic_or_mixed_turnover_forbidden')
        step6=None
        if run and turnover_path:
            cross=cross_adapter.adapt(snapshot,turnover_path,ranking_at=run['collected_at'],environment='real')
            bundle=eligibility_dashboard.build(cross)
            caps=json.loads(Path(self.settings['step6_capital_json']).read_text(encoding='utf-8')) if self.settings.get('step6_capital_json') else []
            proofs=json.loads(Path(self.settings['step6_proofs_json']).read_text(encoding='utf-8')) if self.settings.get('step6_proofs_json') else None
            bundle['eligibility']=eligibility_dashboard.review(bundle['comparisons'][1000],caps,at=generated,proofs=proofs)
            step6=bundle['eligibility'];write_new(folder/'step6d.json',step6)
        artifacts=json.loads(Path(self.settings['chart_artifacts_json']).read_text(encoding='utf-8')) if self.settings.get('chart_artifacts_json') else {}
        chart=None
        if analysis.get('candidate_filter'):
            chart_dir=self.root/'diagnostics/daily_chart'/folder.name
            chart=c12_chart_bridge.integrate(analysis,artifacts,chart_dir,generated_at=generated)
            if step6:
                with (folder/'dashboard.html').open('x',encoding='utf-8') as f:
                    f.write(eligibility_dashboard.render(bundle,top5_json=chart_dir/'top5.json',output_html=folder/'dashboard.html'))
        decision=None;state=None
        if slot in schedule.AM_DECISION_SLOTS:
            previous=sorted((self.root/'reports/decisions'/day).glob('09*.json'))
            state=json.loads(previous[-1].read_text(encoding='utf-8')) if previous else None
            if analysis.get('candidate_filter'):
                state,decision=session_review.morning_transition(analysis,slot,state)
                write_new(self.root/'reports/decisions'/day/(slot.replace(':','')+'.json'),state)
            else:decision=dict(action='held_missing_master',send=False)
            report=analysis
        else:
            decision=session_review.afternoon_review(step6 or {'rows':[]},slot)
            report=self.observation_report(analysis,run,turn,decision,generated,due)
        write_new(folder/'telegram.json',report)
        text=tg.summary(report)
        with (folder/'telegram.txt').open('x',encoding='utf-8') as f:f.write(text)
        delivery=dict(status='disabled')
        if decision['send'] and self.settings.get('telegram_send_enabled') is True:
            config=tg.Configuration.from_environment()
            delivery=tg.deliver(report,text,None,config,allow_readonly_report=True,windows_trust_store=True,
                state_root=self.settings['notification_state'],
                decision_revision=decision.get('revision') if slot in schedule.AM_DECISION_SLOTS else None)
        if step6 and (slot=='15:00' or decision.get('candidates') or (state or {}).get('selected')):
            selected=(state or {}).get('selected') or [r['stock_code'] for r in decision.get('candidates',[])]
            for episode in session_review.tracking_records(step6,selected,captured_at=generated):
                path=self.root/'reports/tracking'/day/(episode['episode_id']+'.json')
                if not path.exists():write_new(path,episode)
        return dict(status='reviewed',decision=decision,delivery=delivery,
            c14_counts=chart['counts'] if chart else {},report=str(folder))

    @staticmethod
    def observation_report(analysis,run,turn,decision,generated,due):
        lookup={r['stored_code']:r for r in turn};rows=[]
        for stock in run['stocks'] if run else []:
            raw=lookup.get(stock['stk_cd'],{})
            rows.append(dict(stock_code=stock['stk_cd'],stock_name=stock['stk_nm'],rank=stock['rank'],
                change_percent=raw.get('flu_rt'),turnover_million_krw=raw.get('trde_prica')))
        reviewable=[dict(stock_code=r['stock_code'],qualification=r['qualification']) for r in decision['reviewable']]
        candidates=reviewable[:5]
        report=dict(schema_version=tg.OBSERVATION_SCHEMA,selection_type='PM_CLOSING_REVIEW',source_kind='readonly_adapter',
            trade_date=due.date().isoformat(),selection_cutoff_at=run['collected_at'] if run else due.isoformat(),
            generated_at=generated,environment='real',market='KRX',candidate_confirmed=False,d0_storage_allowed=False,
            analysis=analysis,observation=dict(ranking_at=run['collected_at'] if run else None,
                turnover_received_at=max((r['request_finished_at'] for r in turn if r.get('request_finished_at')),default=None),
                turnover_rows=len(turn),pagination_complete=bool(turn and all(r['status']=='success' for r in turn)),stocks=rows),
            step6d=dict(verified=len(reviewable),held=len(rows)-len(reviewable)),charts=dict(available=0,unavailable=len(rows)),
            pm_candidates=candidates,pm_reviewable=reviewable)
        report['evaluation_reference']=tg.digest(report);tg.validate(report)
        return report

    def close_day(self,store,due):
        day=due.date().isoformat()
        rows=[dict(r) for r in store.db.execute('SELECT id,collected_at,scheduled_at,status,record_count FROM collections WHERE environment=? AND substr(collected_at,1,10)=?',('real',day))]
        summary=dict(day=day,environment='real',ranking_runs=rows,finality='unverified_1600_observation',
            decisions=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((self.root/'reports/decisions'/day).glob('*.json'))],
            tracking=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((self.root/'reports/tracking'/day).glob('*.json'))],
            automatic_deletion=False)
        summary['ranking_records']=[dict(r) for r in store.db.execute(
            'SELECT r.collection_id,r.rank,r.stk_cd,r.stk_nm FROM ranking_records r JOIN collections c ON c.id=r.collection_id WHERE c.environment=? AND substr(c.collected_at,1,10)=?',('real',day))]
        summary['turnover_observations']=[]
        for path in sorted((self.root/'data/turnover'/day).glob('*.db')):
            summary['turnover_observations'].extend(adapter.read_sources(path,role='turnover')['records'])
        return session_review.write_daily_summary(self.root/'reports/daily'/(day+'.json.gz'),summary)

def run(client,store,settings,*,root,holidays=frozenset(),now=lambda:datetime.now(KST),sleep=time.sleep):
    workflow=Workflow(root,settings,clock=now,sleep=sleep)
    # The existing loop, lateness rules, DB claims and retries remain authoritative.
    return schedule.collect_schedule(client,store,'real','1',holidays,now=now,sleep=sleep,
        slots=schedule.C12X_SLOTS,after_slot=workflow)
