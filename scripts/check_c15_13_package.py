"""Read-only runtime preflight + isolated package replay. No apply/start/send mode."""
import ast
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'diagnostics/c15-13-package'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    manifest=json.loads((PACKAGE/'manifest.json').read_text(encoding='utf8'))
    runtime=Path(manifest['runtime']);stage=PACKAGE/'stage'
    for rel,entry in manifest['files'].items():
        assert sha(runtime/rel)==entry['before_sha256'],('runtime_drift',rel)
        assert sha(stage/rel)==entry['after_sha256'],('package_drift',rel)
        if rel.endswith('.py'):compile((stage/rel).read_text(encoding='utf8'),rel,'exec')
    for rel,digest in manifest['unchanged'].items():assert sha(runtime/rel)==digest,('protected_drift',rel)
    def method(path,name):
        return next(ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(path.read_text(encoding='utf8'))) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    for name in ('__call__','market_data_verification'):
        assert method(runtime/'kiwoom_collector/operating_workflow.py',name)==method(stage/'kiwoom_collector/operating_workflow.py',name)
    for name in ('api_call','from_environment'):
        assert method(runtime/'kiwoom_collector/telegram_briefing.py',name)==method(stage/'kiwoom_collector/telegram_briefing.py',name)
    sys.path.insert(0,str(stage))
    from kiwoom_collector import c15_preview,c15_delivery,telegram_briefing as tg,schedule
    from kiwoom_collector.operating_workflow import Workflow
    from kiwoom_collector.am_time_policy import instant
    assert Path(c15_delivery.__file__).is_relative_to(stage)
    assert schedule.TURNOVER_SLOTS.count('09:45')==1
    before=ast.literal_eval(next(n.value for n in ast.parse((runtime/'kiwoom_collector/schedule.py').read_text(encoding='utf8')).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TURNOVER_SLOTS' for t in n.targets)))
    assert tuple(s for s in schedule.TURNOVER_SLOTS if s!='09:45')==before
    config=json.loads((stage/'config/c15-preview.json').read_text(encoding='utf8'))
    config['analysis_paths']=str(stage/'config/c15-analysis-paths.json')
    config_path=PACKAGE/'replay-config.json';config_path.write_text(json.dumps(config),encoding='utf8')
    output=PACKAGE/'replay'
    if output.exists():output=PACKAGE/('replay-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    output.mkdir(exist_ok=False)
    state=output/'mock/notification_state';state.mkdir(parents=True)
    settings=dict(c15_preview_config=str(config_path),c15_briefing_enabled=True,c15_send_enabled=False,telegram_send_enabled=False)
    connect=sqlite3.connect
    def readonly(path,*args,**kwargs):
        assert '?mode=ro' in str(path) and kwargs.get('uri') is True,'read_only_uri_required'
        return connect(path,*args,**kwargs)
    results={}
    with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),patch('sqlite3.connect',side_effect=readonly),patch.object(tg.Configuration,'from_environment',side_effect=AssertionError('no credentials')):
        for slot in c15_delivery.SLOTS:
            due=instant('2026-11-05T'+slot+':00+09:00')
            w=Workflow(output,settings,clock=lambda:due+timedelta(seconds=30))
            # Exercise the patched runtime's review, not development morning_review.
            first=w.review(None,due,None)
            frozen=w.review(None,due,None)
            assert first['delivery']['status']=='disabled'
            assert frozen['delivery']['status']=='frozen_replay_no_send'
            folder=Path(first['report']);r=json.loads((folder/'telegram.json').read_text(encoding='utf8'));text=tg.summary(r)
            assert all(word in text for word in ('ADR20','0198','0186','시총','대금','1위','2위','3위'))
            assert all(len(s['leaders'])==3 for s in r['briefing']['analysis']['sectors'])
            assert '09:40 (5분 전 대체)' in text if slot=='09:45' else True
            fake=tg.Configuration('123456:'+('a'*24),'1234567',('1234567',))
            with patch.object(tg,'api_call',side_effect=RuntimeError('stop before HTTP')) as boundary:
                a=tg.deliver(r,text,None,fake,allow_readonly_report=True,state_root=state)
                b=tg.deliver(r,text,None,fake,allow_readonly_report=True,state_root=state)
                assert a['status']=='failed_or_unknown' and b['status']=='duplicate_blocked' and boundary.call_count==1
            results[slot]=dict(review=first['delivery'],replay=frozen['delivery'],mock=a,duplicate=b)
        # Preserve and exercise the actual outer operational guard. Do not
        # manufacture market-time verification just to reach the new adapter.
        gate_root=output/'market-gate'
        w=Workflow(gate_root,settings,clock=lambda:due)
        store=SimpleNamespace(path=gate_root/'data/stock1-real.db',
                              db=SimpleNamespace(execute=lambda *args:SimpleNamespace(fetchall=lambda:[])))
        with patch.object(schedule,'TURNOVER_SLOTS',()),patch.object(w,'review',side_effect=AssertionError('market guard must hold')):
            w(None,store,'real',due,'simulation',{})
        ledger=json.loads((gate_root/'reports/workflow/2026-11-05/0950.json').read_text(encoding='utf8'))
        assert ledger['review']['status']=='held_unverified_market_data'
        results['outer_workflow_guard']=ledger['review']
    results.update(runtime_unchanged=True,api_calls=0,actual_telegram_sends=0,
        replay_directory=str(output),preserved_market_gate=True,ready_for_live=False,blockers=manifest['blockers'])
    for rel,entry in manifest['files'].items():assert sha(runtime/rel)==entry['before_sha256']
    (PACKAGE/'preflight-results.json').write_text(json.dumps(results,indent=2),encoding='utf8')
    print('PACKAGE REPLAY OK; LIVE NOT READY: market-time gate preserved; external references need review')


if __name__=='__main__':main()
