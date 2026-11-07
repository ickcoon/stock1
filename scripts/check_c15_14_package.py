"""Read-only runtime checks and isolated outer-Workflow integration; never deploys."""
import ast
from contextlib import closing
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'diagnostics/c15-14-package'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    m=json.loads((PACKAGE/'manifest.json').read_text(encoding='utf8'));runtime=Path(m['runtime']);stage=PACKAGE/'stage'
    for rel,e in m['files'].items():
        assert sha(runtime/rel)==e['before_sha256'],('runtime_drift',rel)
        assert sha(stage/rel)==e['after_sha256'],('package_drift',rel)
        if rel.endswith('.py'):compile((stage/rel).read_text(encoding='utf8'),rel,'exec')
    for rel,h in m['unchanged'].items():assert sha(runtime/rel)==h,('protected_drift',rel)
    def function(path,name):return next(ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(path.read_text(encoding='utf8'))) if isinstance(n,ast.FunctionDef) and n.name==name)
    assert function(runtime/'kiwoom_collector/operating_workflow.py','market_data_verification')==function(stage/'kiwoom_collector/operating_workflow.py','market_data_verification')
    for name in ('api_call','from_environment'):
        assert function(runtime/'kiwoom_collector/telegram_briefing.py',name)==function(stage/'kiwoom_collector/telegram_briefing.py',name)
    sys.path.insert(0,str(stage))
    from kiwoom_collector import c15_delivery,c15_references,c15_runtime,telegram_briefing as tg,schedule
    from kiwoom_collector.operating_workflow import Workflow
    from kiwoom_collector import operating_workflow as runtime_workflow
    from kiwoom_collector.am_time_policy import instant
    from kiwoom_collector.c12_latest import read_db
    assert Path(c15_runtime.__file__).is_relative_to(stage)
    output=PACKAGE/('integration-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'));output.mkdir()
    config=json.loads((stage/'config/c15-preview.json').read_text(encoding='utf8'));config['analysis_paths']=str(stage/'config/c15-analysis-paths.json')
    config_path=output/'preview-config.json';config_path.write_text(json.dumps(config),encoding='utf8')
    settings=dict(c15_briefing_enabled=True,c15_send_enabled=True,telegram_send_enabled=True,c15_preview_config=str(config_path))
    fake=tg.Configuration('123456:'+('a'*24),'1234567',('1234567',))
    actual=runtime/'data/stock1-real.db';evidence={}
    with read_db(actual) as db:
        for slot in c15_delivery.SLOTS:
            row=dict(db.execute('SELECT * FROM collections WHERE scheduled_at=?',('2026-11-05T'+slot+':00+09:00',)).fetchone())
            rows=[dict(r) for r in db.execute('SELECT rank,stk_cd,raw_json FROM ranking_records WHERE collection_id=? ORDER BY rank',(row['id'],))]
            evidence[slot]=(row,rows)
    # A 60-row extract is a development fixture, not a runtime DB clone.
    fixture=output/'data/stock1-real.db';fixture.parent.mkdir()
    with closing(sqlite3.connect(fixture)) as db:
        db.executescript('CREATE TABLE collections(id,collected_at,scheduled_at,environment,api_id,status,record_count,test_mode); CREATE TABLE ranking_records(collection_id,rank,stk_cd,raw_json);')
        for row,rows in evidence.values():
            db.execute('INSERT INTO collections VALUES(?,?,?,?,?,?,?,?)',tuple(row[k] for k in ('id','collected_at','scheduled_at','environment','api_id','status','record_count','test_mode')))
            db.executemany('INSERT INTO ranking_records VALUES(?,?,?,?)',[(row['id'],r['rank'],r['stk_cd'],r['raw_json']) for r in rows])
        db.commit()
    for slot in c15_delivery.SLOTS:
        p=output/'data/turnover/2026-11-05'/(slot.replace(':','')+'.db');p.parent.mkdir(parents=True,exist_ok=True);p.touch()
    state=output/'notification_state';state.mkdir();settings['notification_state']=str(state)
    results={};connect=sqlite3.connect
    def readonly(path,*args,**kw):
        assert '?mode=ro' in str(path) and kw.get('uri') is True,'read_only_required'
        return connect(path,*args,**kw)
    with patch('sqlite3.connect',side_effect=readonly),patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),patch.object(tg.Configuration,'from_environment',return_value=fake),patch('kiwoom_collector.weekly_tls.configure_windows_trust',return_value=None),patch.object(tg,'api_call',side_effect=RuntimeError('mock boundary before HTTP')) as boundary:
        with closing(sqlite3.connect(fixture.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
            store=SimpleNamespace(path=fixture,db=db)
            for slot in c15_delivery.SLOTS:
                due=instant('2026-11-05T'+slot+':00+09:00');w=Workflow(output,settings,clock=lambda:due+timedelta(seconds=30))
                row,_=evidence[slot];before=boundary.call_count
                w(None,store,'real',due,row['id'],dict(status='success'))
                ledger=json.loads((output/'reports/workflow/2026-11-05'/(slot.replace(':','')+'.json')).read_text(encoding='utf8'))
                assert ledger['market_data_verification']['recommendation_allowed'] is False
                assert ledger['c15_observation_context']['allowed'] is True
                review=ledger['review'];assert review['status']=='reviewed',review
                assert boundary.call_count-before==(0 if slot=='09:45' else 1)
                assert review['delivery']['status']==('record_only' if slot=='09:45' else 'failed_or_unknown')
                report=json.loads((Path(review['report'])/'telegram.json').read_text(encoding='utf8'));text=tg.summary(report)
                assert all(k in text for k in ('시총','대금','ADR20','0198','0186'))
                assert len(report['briefing']['analysis']['sectors'])==3
                assert all(len(s['leaders'])==3 for s in report['briefing']['analysis']['sectors'])
                if slot=='09:45':assert '09:40 (5분 전 대체)' in text
                replay=w.review(store,due,None);assert replay['delivery']['status']=='frozen_replay_no_send'
                if slot!='09:45':
                    duplicate=tg.deliver(report,text,None,fake,allow_readonly_report=True,state_root=state)
                    assert duplicate['status']=='duplicate_blocked'
                results[slot]=dict(review=review,replay=replay,context=ledger['c15_observation_context'])
            due=instant('2026-11-05T09:40:00+09:00');identifier=evidence['09:40'][0]['id']
            for name,now,opts in [('delayed',due+timedelta(seconds=121),{}),('old_replay',due+timedelta(days=1),{})]:
                r=c15_runtime.context(fixture,identifier,due,opts,now=now);assert not r['allowed'];results[name]=r
            holiday=output/'holiday.txt';holiday.write_text('2026-11-05',encoding='utf8')
            r=c15_runtime.context(fixture,identifier,due,dict(holidays_file=str(holiday)),now=due);assert not r['allowed'];results['holiday']=r
            original_write=runtime_workflow.write_new
            for name,now,opts in [('delayed',due+timedelta(seconds=121),{}),('old_replay',due+timedelta(days=1),{}),('holiday',due,dict(holidays_file=str(holiday)))]:
                case=output/('blocked-'+name)
                w=Workflow(output,dict(settings,**opts),clock=lambda:now)
                def isolated_write(path,value):original_write(case/Path(path).relative_to(output),value)
                with patch.object(w,'review',side_effect=AssertionError('blocked context must not analyze')),patch.object(runtime_workflow,'write_new',side_effect=isolated_write):
                    w(None,store,'real',due,identifier,dict(status='success'))
                ledger=json.loads((case/'reports/workflow/2026-11-05/0940.json').read_text(encoding='utf8'))
                assert ledger['review']['status']=='held_unverified_market_data'
                assert not ledger['c15_observation_context']['allowed']
                results['outer_'+name]=ledger['c15_observation_context']
        refs=c15_references.inspect(config,day='2026-11-05',slot='09:45');assert refs['ready'];results['references']=refs
        paths=json.loads(Path(config['analysis_paths']).read_text(encoding='utf8'));paths['theme_master_db']=str(output/'missing-theme.db')
        missing_paths=output/'missing-paths.json';missing_paths.write_text(json.dumps(paths),encoding='utf8')
        missing_config=output/'missing-config.json';missing_config.write_text(json.dumps(dict(config,analysis_paths=str(missing_paths))),encoding='utf8')
        w=Workflow(output/'missing-case',dict(settings,c15_preview_config=str(missing_config)),clock=lambda:due)
        held=c15_delivery.process(w,due,live_context=dict(allowed=True));assert held['status']=='held_missing_required_reference';results['missing_required']=held
        assert not (output/'missing-theme.db').exists()
        paths=json.loads(Path(config['analysis_paths']).read_text(encoding='utf8'));paths['analysis_adr_db']=str(output/'missing-adr.db')
        optional_paths=output/'optional-paths.json';optional_paths.write_text(json.dumps(paths),encoding='utf8')
        optional_config=output/'optional-config.json';optional_config.write_text(json.dumps(dict(config,analysis_paths=str(optional_paths))),encoding='utf8')
        w=Workflow(output/'optional-case',dict(settings,c15_preview_config=str(optional_config),c15_send_enabled=False),clock=lambda:due)
        optional=c15_delivery.process(w,due)
        assert optional['status']=='reviewed'
        assert 'ADR20 미확인' in (Path(optional['report'])/'telegram.txt').read_text(encoding='utf8')
        results['missing_optional_ADR']=optional
        assert not (output/'missing-adr.db').exists()
    for rel,e in m['files'].items():assert sha(runtime/rel)==e['before_sha256']
    for rel,h in m['unchanged'].items():assert sha(runtime/rel)==h
    results.update(result='OK',runtime_unchanged=True,actual_api_calls=0,actual_telegram_sends=0,output=str(output),
                   tested_clock='Workflow.clock injection in isolated test only',production_price_time_proof=False)
    (PACKAGE/'integration-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
    m.update(technical_ready_for_deployment=True,operator_approval_required=True,
             reference_preflight_passed=True,context_integration_passed=True,
             ready_for_live=False,deployed=False)
    (PACKAGE/'manifest.json').write_text(json.dumps(m,indent=2),encoding='utf8')
    print('C15-14 OUTER WORKFLOW INTEGRATION OK; NO DEPLOYMENT OR SEND')


if __name__=='__main__':main()
