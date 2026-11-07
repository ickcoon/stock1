"""Configured reference existence/schema preflight. All SQLite opens are read-only."""
import json
from pathlib import Path
import sqlite3

from .c12_latest import read_db
from .c15_preview import path_from


def inspect(config,*,day,slot):
    try:paths=json.loads(path_from(config['analysis_paths']).read_text(encoding='utf-8-sig'))
    except (KeyError,OSError,ValueError):return dict(ready=False,reason='reference_config_missing_or_invalid',sources=[])
    if not all(isinstance(paths.get(k),str) and paths[k] for k in ('theme_master_db','analysis_daily_db','analysis_adr_db')):
        return dict(ready=False,reason='reference_paths_incomplete',sources=[])
    runtime=path_from(config['runtime_root'])
    sources=[('0198',runtime/config.get('ranking_relative','data/stock1-real.db'),True,{'collections','ranking_records'}),
        ('0186',runtime/config.get('turnover_relative','data/turnover')/day/(slot.replace(':','')+'.db'),False,{'turnover_runs','turnover_rows'}),
        ('theme',path_from(paths['theme_master_db']),True,set()),
        ('capital',path_from(paths['analysis_daily_db']),False,{'daily_latest'}),
        ('ADR20',path_from(paths['analysis_adr_db']),False,{'adr20'})]
    if config.get('identity_checkpoint'):sources.append(('capital_identity',path_from(config['identity_checkpoint']),False,set()))
    if config.get('eligibility_master_db'):sources.append(('ka10001_cache',path_from(config['eligibility_master_db']),False,{'market_cap_cache'}))
    rows=[]
    for role,path,required,tables in sources:
        item=dict(role=role,path=str(path),required=required,connection='SQLite URI mode=ro; query_only=ON',
                  status='missing',missing_policy='hold_briefing' if required else 'unverified_optional_reference')
        try:
            if path.is_file():
                with read_db(path) as db:
                    actual={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
                    if role=='theme':
                        # Reuse the existing loader's supported schemas, not an
                        # arbitrary SQLite file that happens to open.
                        from .c12_external_theme_bridge import load
                        load(path)
                    item['status']='available' if tables<=actual else 'schema_unverified'
        except (OSError,ValueError,sqlite3.Error,KeyError):item['status']='unreadable_or_unsupported'
        rows.append(item)
    return dict(ready=all(r['status']=='available' for r in rows if r['required']),sources=rows,
                optional_missing=[r['role'] for r in rows if not r['required'] and r['status']!='available'])


def main():
    import argparse
    from datetime import date
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--date',required=True)
    p.add_argument('--slot',choices=('09:40','09:45','09:50'),required=True)
    args=p.parse_args();date.fromisoformat(args.date)
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    result=inspect(config,day=args.date,slot=args.slot)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['ready'] else 2


if __name__=='__main__':raise SystemExit(main())
