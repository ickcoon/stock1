"""Final read-only file/environment audit. Writes only development evidence."""
import ast
import csv
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'diagnostics/c15-14-package'
OUT=ROOT/'diagnostics/c15-15-preflight'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    OUT.mkdir(exist_ok=True)
    manifest=json.loads((PACKAGE/'manifest.json').read_text(encoding='utf8'))
    runtime=Path(manifest['runtime']);stage=PACKAGE/'stage'
    rows=[];conflicts=[]
    for relative,entry in manifest['files'].items():
        current=sha(runtime/relative);packaged=sha(stage/relative)
        if current!=entry['before_sha256'] or packaged!=entry['after_sha256']:conflicts.append(relative)
        source=(stage/relative).read_text(encoding='utf8')
        deps=[];purpose='C15 configuration contract/path settings'
        if relative.endswith('.py'):
            tree=ast.parse(source);purpose=(ast.get_docstring(tree) or '').splitlines()[0]
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom) and node.level:
                    deps.extend([node.module] if node.module else [n.name for n in node.names])
            compile(source,relative,'exec')
        rows.append(dict(path=relative,action=entry['action'],purpose=purpose,
            current_runtime_sha256=current,expected_before_sha256=entry['before_sha256'],
            package_sha256=packaged,normalized_lf_sha256=hashlib.sha256(source.rstrip('\n').encode('utf8')).hexdigest(),
            dependencies=sorted(set(deps)),conflict=relative in conflicts))
    unchanged={rel:sha(runtime/rel)==h for rel,h in manifest['unchanged'].items()}
    sys.path.insert(0,str(stage))
    imports={}
    with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
        for row in rows:
            if row['path'].endswith('.py'):
                module=row['path'][:-3].replace('/','.')
                try:importlib.import_module(module);imports[module]='OK'
                except Exception as error:imports[module]=type(error).__name__
        from kiwoom_collector import c15_references
        config=json.loads((stage/'config/c15-preview.json').read_text(encoding='utf8'))
        config['analysis_paths']=str(stage/'config/c15-analysis-paths.json')
        refs=c15_references.inspect(config,day='2026-11-05',slot='09:45')
    packages={}
    for name in ('requests','python-dotenv','websocket-client','truststore','urllib3','numpy','matplotlib'):
        try:packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:packages[name]='missing'
    settings=json.loads((runtime/'workflow-config.json').read_text(encoding='utf-8-sig'))
    safe_settings={k:settings.get(k) for k in ('profile','deployment_approved','telegram_send_enabled','text_only','automatic_deletion','turnover_max_pages','holidays_file','notification_state')}
    result=dict(host=os.environ.get('COMPUTERNAME'),runtime_accessible=runtime.is_dir(),
        interpreter=sys.executable,python=sys.version,packages=packages,
        package_files=len(rows),patched=sum(r['action']=='patch' for r in rows),new=sum(r['action']=='add' for r in rows),
        conflicts=conflicts,unchanged_runtime_files=unchanged,imports=imports,reference_check=refs,
        safe_workflow_settings=safe_settings,files=rows,
        package_sha256={p.name:sha(p) for p in (PACKAGE/'review.patch',PACKAGE/'manifest.json',PACKAGE/'settings-overlay.json')},
        file_preflight_passed=not conflicts and all(unchanged.values()) and all(v=='OK' for v in imports.values()) and refs['ready'],
        deployment_approved=False,operational_state_verification='requires_owner_process_and_task_check',
        operational_writes=0,api_calls=0,telegram_sent=False)
    (OUT/'file-environment-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    with (OUT/'deployment-files.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    backup=dict(files=[dict(path=r['path'],sha256=r['current_runtime_sha256']) for r in rows if r['action']=='patch']+
        [dict(path=p,sha256=sha(runtime/p)) for p in ('workflow-config.json','run_real_collector.py','Start-RealCollector.ps1','CollectorConsole.ps1')],
        preserve_without_reset=['notification_state','reports','data','credentials'],database_copy_required_for_code_only_deployment=False)
    (OUT/'backup-plan.json').write_text(json.dumps(backup,indent=2),encoding='utf8')
    print(json.dumps({k:result[k] for k in ('host','interpreter','package_files','patched','new','conflicts','file_preflight_passed','operational_state_verification')},indent=2))
    return 0 if result['file_preflight_passed'] else 2


if __name__=='__main__':raise SystemExit(main())
