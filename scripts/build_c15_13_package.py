"""Build a review-only, hash-bound package. Never writes to runtime."""
import ast
import difflib
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=Path('C:/Users/dada1/Downloads/stock1-dev/runtime/c12v-real-20261102')
OUT=ROOT/'diagnostics/c15-13-package'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def build():
    OUT.mkdir(exist_ok=False)
    stage=OUT/'stage';stage.mkdir()
    for path in (RUNTIME/'kiwoom_collector').rglob('*.py'):
        target=stage/path.relative_to(RUNTIME);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    changes={}
    def change(relative,new):
        old=RUNTIME/relative;target=stage/relative
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(new,encoding='utf8')
        changes[relative]=dict(before_sha256=sha(old),after_sha256=sha(target),action='patch' if old.exists() else 'add')
    rel='kiwoom_collector/operating_workflow.py';s=(RUNTIME/rel).read_text(encoding='utf8')
    anchor='    def review(self,store,due,turnover_path):\n'
    assert s.count(anchor)==1
    change(rel,s.replace(anchor,anchor+"        if due.strftime('%H:%M') in schedule.AM_DECISION_SLOTS and self.settings.get('c15_briefing_enabled') is True:\n            from . import c15_delivery\n            return c15_delivery.process(self,due)\n"))
    rel='kiwoom_collector/schedule.py';s=(RUNTIME/rel).read_text(encoding='utf8')
    assert s.count("TURNOVER_SLOTS = ('09:40','09:50',")==1
    change(rel,s.replace("TURNOVER_SLOTS = ('09:40','09:50',","TURNOVER_SLOTS = ('09:40','09:45','09:50',"))
    rel='kiwoom_collector/telegram_briefing.py';s=(RUNTIME/rel).read_text(encoding='utf8')
    dev=(ROOT/rel).read_text(encoding='utf8')
    # Only C15 additions, retaining all other runtime transport/credentials code.
    s=s.replace('    scan(report, secrets)\n','    scan(report, secrets)\n    if isinstance(report,dict) and report.get("schema_version")=="c15-morning-delivery-v1":\n        from . import c15_delivery\n        return c15_delivery.validate(report)\n',1)
    s=s.replace('def summary(report, *, attached=False):\n','def summary(report, *, attached=False):\n    if report.get("schema_version")=="c15-morning-delivery-v1":\n        validate(report)\n        return report["briefing"]["text"]\n')
    s=s.replace("return am.SCHEMA if report['schema_version'] == 'am-sector-briefing-v1'", "return am.SCHEMA if report['schema_version'] in ('am-sector-briefing-v1','c15-morning-delivery-v1')")
    a=dev.index("    if report['schema_version']=='c15-morning-delivery-v1':",dev.index('def deliver('));b=dev.index('    decision_signature=None',a)
    s=s.replace('    decision_signature=None',dev[a:b]+'    decision_signature=None',1)
    a=dev.index("        if report['schema_version']=='c15-morning-delivery-v1' and not path.exists():",dev.index('def preflight('));b=dev.index("        require(not path.is_symlink()",a)
    anchor="        path = root/(key+'.json')\n        require(not path.is_symlink(), 'invalid_state_file')\n        return dict(status='duplicate_blocked'"
    assert anchor in s
    s=s.replace(anchor,"        path = root/(key+'.json')\n"+dev[a:b]+"        require(not path.is_symlink(), 'invalid_state_file')\n        return dict(status='duplicate_blocked'")
    change(rel,s)
    # Add only missing dependencies; never replace existing runtime helpers.
    pending=['c15_delivery'];seen=set()
    while pending:
        name=pending.pop()
        if name in seen:continue
        seen.add(name);relative='kiwoom_collector/'+name+'.py';source=ROOT/relative
        if (RUNTIME/relative).exists():continue
        if not source.is_file():continue
        text=source.read_text(encoding='utf8');change(relative,text)
        for node in ast.walk(ast.parse(text)):
            if isinstance(node,ast.ImportFrom) and node.level==1:
                if node.module:pending.append(node.module.split('.')[0])
                else:pending.extend(alias.name for alias in node.names)
    for name in ('c15-display-contract','c15-turnover-contract','c15-leadership'):
        change('config/'+name+'.json',(ROOT/'config'/ (name+'.json')).read_text(encoding='utf8'))
    config=json.loads((ROOT/'config/c15-preview.json').read_text(encoding='utf8'))
    config['analysis_paths']=str(RUNTIME/'config/c15-analysis-paths.json')
    config['identity_checkpoint']=str(ROOT/config['identity_checkpoint'])
    change('config/c15-preview.json',json.dumps(config,indent=2))
    paths=json.loads((ROOT/'config/analysis-paths.json').read_text(encoding='utf8'))
    paths={k:str(ROOT/v) for k,v in paths.items()}
    change('config/c15-analysis-paths.json',json.dumps(paths,indent=2))
    # External read-only references are explicit; no database is copied.
    settings=dict(c15_briefing_enabled=False,c15_send_enabled=False,
        c15_send_slots=['09:40','09:50'],c15_send_max_lag_seconds=120,
        c15_preview_config=str(RUNTIME/'config/c15-preview.json'))
    (OUT/'settings-overlay.json').write_text(json.dumps(settings,indent=2),encoding='utf8')
    patch=[]
    for relative,entry in changes.items():
        before=(RUNTIME/relative).read_text(encoding='utf8').splitlines(True) if entry['action']=='patch' else []
        after=(stage/relative).read_text(encoding='utf8').splitlines(True)
        if before and not before[-1].endswith('\n'):before[-1]+='\n'
        if after and not after[-1].endswith('\n'):after[-1]+='\n'
        patch.extend(difflib.unified_diff(before,after,fromfile='a/'+relative if before else '/dev/null',tofile='b/'+relative))
    (OUT/'review.patch').write_text(''.join(patch),encoding='utf8',newline=chr(10))
    immutable=['run_real_collector.py','workflow-config.json']+[str(p.relative_to(RUNTIME)).replace(chr(92),'/') for p in (RUNTIME/'kiwoom_collector').rglob('*.py') if str(p.relative_to(RUNTIME)).replace(chr(92),'/') not in changes]
    manifest=dict(runtime=str(RUNTIME),files=changes,unchanged={p:sha(RUNTIME/p) for p in immutable},
        external_readonly_references=paths,ready_for_live=False,
        blockers=['runtime_market_data_verification_blocks_review','external_reference_paths_require_operator_acceptance'],
        schedule_patch_applied=False)
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    print('Review-only package:',OUT,'files:',len(changes))


if __name__=='__main__':build()
