"""C12-AL adapter for the inspected ARVIS trader SQLite schema. Read only.

Source observations remain historical external references, never permanent verified
membership or ARVIS selections. No fuzzy stock aliases, network or database writer.
"""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path

from . import external_theme_master as external
from .c12_latest import read_db,digest
from .c12_master_audit import match
from .c12_theme_audit import analyze_themes
from .theme_normalization import normalize
from .c12_theme_expansion import attention_view


# Explicit display aliases only. Uncertain composite labels are NOT split.
DISPLAY_ALIASES={
    '반도체 관련주':'반도체', '일부 반도체':'반도체',
    '로봇 관련주':'로봇', '일부 로봇':'로봇', '일부 로봇 관련주':'로봇',
    '보안주':'정보보안', '보안주(정보)':'정보보안', '정보 보안':'정보보안',
    '일부 정보 보안':'정보보안', '일부 정보보안':'정보보안',
    '일부 반도체 소부장':'반도체 소부장',
    '전선/전력설비':'전선·전력설비','전력설비/전선':'전선·전력설비',
    '2차전지 관련주':'2차전지','2차전지 등':'2차전지',
    '원전주':'원전','원전 관련':'원전','원자력발전':'원전',
    '방산주':'방산','일부 방산주':'방산', '제약/바이오':'제약바이오',
}


def label_view(raw):
    if raw in DISPLAY_ALIASES:
        return dict(raw=raw,name=DISPLAY_ALIASES[raw],state='explicit_display_alias')
    if '/' in raw or ' 등' in raw or raw.endswith((' 1',' 2')):
        return dict(raw=raw,name=raw,state='composite_or_numbered_label_needs_review')
    return dict(raw=raw,name=raw,state='original_label_preserved')


def load(path):
    """Static reviewed joins, fail closed on a different schema."""
    with read_db(path) as db:
        expected={
            'reviewed_stock_records':{'id','theme_record_id','stock_name_raw','stock_code','pct_change','read_status','source_position'},
            'reviewed_theme_records':{'id','trade_date','raw_theme','screenshot','transcription_status','note'},
            'stock_observations':{'id','theme_observation_id','stock_name_raw','stock_code','pct_change','confidence','position_in_source','source_id'},
            'theme_observations':{'id','trade_date','theme_raw','note','source_id'},
            'sources':{'source_id','source_label','capture_name','source_type','notes'},
        }
        for table,columns in expected.items():
            actual={r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
            if not columns<=actual:raise ValueError('external_schema_mismatch:'+table)
        reviewed=[dict(r) for r in db.execute('''SELECT s.id AS stock_record_id,t.id AS theme_record_id,
            t.trade_date,t.raw_theme,s.stock_name_raw AS raw_stock_name,s.stock_code AS raw_stock_code,
            t.screenshot AS source,t.transcription_status,t.note,s.pct_change AS source_pct_change,
            s.read_status,s.source_position FROM reviewed_stock_records s
            JOIN reviewed_theme_records t ON t.id=s.theme_record_id LIMIT 10001''')]
        original=[dict(r) for r in db.execute('''SELECT s.id AS stock_record_id,t.id AS theme_record_id,
            t.trade_date,t.theme_raw AS raw_theme,s.stock_name_raw AS raw_stock_name,s.stock_code AS raw_stock_code,
            p.capture_name AS source,p.source_label,p.source_type,t.note,
            s.pct_change AS source_pct_change,s.confidence AS read_status,s.position_in_source AS source_position
            FROM stock_observations s JOIN theme_observations t ON t.id=s.theme_observation_id
            JOIN sources p ON p.source_id=s.source_id LIMIT 10001''')]
        themes=[dict(r) for r in db.execute('SELECT id,trade_date,raw_theme,screenshot AS source,transcription_status,note FROM reviewed_theme_records LIMIT 10001')]
        themes += [dict(r) for r in db.execute('''SELECT t.id,t.trade_date,t.theme_raw AS raw_theme,
            p.capture_name AS source,t.note FROM theme_observations t JOIN sources p ON p.source_id=t.source_id LIMIT 10001''')]
        metadata={table:[dict(r) for r in db.execute('SELECT * FROM '+table+' LIMIT 10001')]
            for table in ('master_stock_names','master_themes','master_stock_theme','sources','provenance_notes',
                          'stock_name_aliases','theme_aliases','tentative_alias_groups','missing_capture_dates','capture_inventory')}
        logical_orphans=db.execute('''SELECT COUNT(*) FROM master_stock_theme r
            LEFT JOIN master_stock_names s ON r.stock_name_raw=s.stock_name_raw
            LEFT JOIN master_themes t ON r.theme_label=t.theme_label
            WHERE s.stock_name_raw IS NULL OR t.theme_label IS NULL''').fetchone()[0]
    if max([len(reviewed),len(original),len(themes)]+[len(v) for v in metadata.values()])>10000:
        raise ValueError('external_record_limit')
    unique={}
    for kind,rows in (('reviewed_visual_first_pass',reviewed),('initial_screenshot_transcription',original)):
        for row in rows:
            date.fromisoformat(row['trade_date'])
            key=(row['trade_date'],row['raw_theme'],row['raw_stock_name'],row['raw_stock_code'])
            item=unique.setdefault(key,dict(trade_date=row['trade_date'],raw_theme=row['raw_theme'],
                raw_stock_name=row['raw_stock_name'],raw_stock_code=row['raw_stock_code'],
                source_kind=external.SOURCE_KIND,raw_records=[],sources=[]))
            item['raw_records'].append(dict(row,source_table_family=kind))
            if row['source'] not in item['sources']:item['sources'].append(row['source'])
    records=list(unique.values())
    row_pairs={(r['raw_stock_name'],r['raw_theme']) for r in records}
    master_pairs={(r['stock_name_raw'],r['theme_label']) for r in metadata['master_stock_theme']}
    reconciliation=dict(observation_pair_count=len(row_pairs),master_pair_count=len(master_pairs),
        raw_pairs_only_in_observations=sorted(row_pairs-master_pairs),
        raw_pairs_only_in_master=sorted(master_pairs-row_pairs),
        pairs_agree_after_name_edge_whitespace_only=
            {(name.strip(),theme) for name,theme in row_pairs}==master_pairs,
        distinct_raw_observed_names=len({r['raw_stock_name'] for r in records}),
        distinct_edge_trimmed_observed_names=len({r['raw_stock_name'].strip() for r in records}))
    return dict(records=records,theme_records=themes,metadata=metadata,logical_orphans=logical_orphans,
        aggregate_reconciliation=reconciliation,
        raw_stock_observation_count=len(reviewed)+len(original),unique_stock_observation_count=len(records),
        duplicates_collapsed=len(reviewed)+len(original)-len(records),transcription_complete=False)


def reference_match(record,watchlist):
    """Exact raw-name reference only; do not convert absent/unverified codes into proof."""
    raw_code=record.get('raw_stock_code')
    if raw_code not in (None,''):
        try:external.validate_code(raw_code)
        except ValueError:return dict(state='unconfirmed',reason='invalid_external_code')
    master=[dict(stock_code=s['stock_code'],stock_name=s['name']) for s in watchlist]
    result=match(dict(stock_code=record.get('raw_stock_code') or '',name=record['raw_stock_name']),master)
    if result['state'] not in ('code','unique_name'):return dict(state='unconfirmed',reason=result['state'])
    return dict(state='exact_name_reference_needs_code_review',
                watchlist_stock_code=result['record']['stock_code'],match_kind=result['state'],
                external_stock_code=record.get('raw_stock_code'),code_verified=False)


def calendar(loaded,day,*,arvis_records=()):
    result=external.calendar_day(day,external_records=loaded['records'],arvis_records=arvis_records)
    themes=[r for r in loaded['theme_records'] if r['trade_date']==day]
    result['external_trader_close'].update(theme_records=themes,
        distinct_raw_themes=len({r['raw_theme'] for r in themes}),
        distinct_raw_stock_names=len({r['raw_stock_name'] for r in result['external_trader_close']['records']}),
        explicit_missing_capture=[r for r in loaded['metadata']['missing_capture_dates'] if r['trade_date']==day],
        transcription_complete=False)
    if themes:result['external_trader_close']['coverage']='partial_transcription'
    return result


def analyze(path,ak,observation,*,legacy_db=None):
    path=Path(path).resolve(strict=True)
    inspection=external.inspect(path,full_integrity=True)
    if inspection['quick_check']!=['ok'] or inspection['integrity_check']!=['ok'] or inspection['foreign_key_violations']:
        raise ValueError('external_database_integrity_failed')
    loaded=load(path)
    watch=observation['observations']['watchlist'];at=observation['as_of']
    by_code={s['stock_code']:[] for s in watch}
    reference_tags=[];issues=[]
    for record in loaded['records']:
        if record['trade_date']>at[:10]:continue
        joined=reference_match(record,watch)
        if joined['state']=='unconfirmed':continue
        code=joined['watchlist_stock_code'];view=label_view(record['raw_theme'])
        by_code[code].append(dict(record,identity=joined,display_theme=view))
        if view['state']=='composite_or_numbered_label_needs_review':
            issues.append(dict(stock_code=code,raw_name=record['raw_stock_name'],raw_theme=record['raw_theme'],
                               reason='composite_or_numbered_theme_not_split',trade_date=record['trade_date']))
            continue
        reference_tags.append(dict(stock_code=code,sector_name=view['name'],source='external_exact_name_historical_reference'))
    reference_obs=analyze_themes(watch,reference_tags)
    # Reuse AJ's explicit parent links for an independently labeled reference view.
    ref_doc=dict(connections=[dict(stock_code=s['stock_code'],name=s['name'],status='reference_only',
        themes=sorted({t['sector_name'] for t in reference_tags if t['stock_code']==s['stock_code']})) for s in watch],
        theme_observations=reference_obs)
    reference_norm=normalize(ref_doc,watch)
    baseline=ak['full_report']
    connections=[dict(stock_code=row['stock_code'],name=row['name'],confirmed_themes=deepcopy(row['themes']),
                     external_reference_records=by_code[row['stock_code']],
                     external_status='name_reference_only_code_unverified' if by_code[row['stock_code']] else 'no_exact_name_reference')
                 for row in baseline['connections']]
    # No external row is promoted: all source symbols are unresolved and source is a draft.
    coverage=deepcopy(ak['after']);own=[]
    names={s['stock_code']:s['name'] for s in watch}
    for row in observation['analysis']['rows']:
        own.append(dict(trade_date=at[:10],slot='09:50',observed_at=at,source_kind='arvis_selection',
            record_kind='candidate_evaluation',stock_code=row['stock_code'],name=names.get(row['stock_code']),
            selection_status=row['candidate_status'],selected=row['candidate_confirmed'],
            selection_basis=dict(reasons=row['reasons'],missing_evidence=row['missing_evidence']),
            source_report_reference=observation['evaluation_reference']))
    master_reference=[]
    if legacy_db:
        with read_db(legacy_db) as db:legacy=[dict(r) for r in db.execute('SELECT stock_code,stock_name,updated_at FROM stocks')]
        for row in loaded['metadata']['master_stock_names']:
            candidate=match(dict(stock_code=row['stock_code'] or '',name=row['stock_name_raw']),legacy)
            if candidate.get('record'):
                master_reference.append(dict(raw_name=row['stock_name_raw'],candidate=candidate,
                                             status='legacy_name_reference_not_historical_code_verification'))
    raw_labels=[r['theme_label'] for r in loaded['metadata']['master_themes']]
    days=sorted({r['trade_date'] for r in loaded['theme_records']})
    result=dict(schema_version='c12-al-connected-master-v1',requested_filename='arvis_theme_master_20261103.sqlite',
        actual_source_path=str(path),source_inspection=inspection,source_data=loaded,
        source_is_partial_transcription=True,observation_as_of=at,retrospective_enrichment=True,
        external_identity=dict(master_names=len(loaded['metadata']['master_stock_names']),
            supplied_nonempty_codes=sum(bool(r['stock_code']) for r in loaded['metadata']['master_stock_names']),
            verified_code_connections=0,verified_code_percent=0,
            exact_watchlist_name_references=sum(bool(v) for v in by_code.values()),
            exact_watchlist_name_percent=round(100*sum(bool(v) for v in by_code.values())/len(watch),4),
            legacy_master_exact_name_candidates=master_reference),
        theme_normalization=dict(raw_label_count=len(raw_labels),display_label_count=len({label_view(s)['name'] for s in raw_labels}),
            aliases=DISPLAY_ALIASES,review_required_labels=[label_view(s) for s in raw_labels
                if label_view(s)['state']=='composite_or_numbered_label_needs_review']),
        before=deepcopy(ak['after']),after=coverage,new_confirmed_stock_count=0,
        connections=connections,external_reference_only_observations=reference_norm,
        external_reference_attention=attention_view(reference_norm),external_review_issues=issues,
        observed_date_range=dict(first=days[0],last=days[-1],distinct_dates=len(days)),
        calendars=[calendar(loaded,day,arvis_records=own) for day in ('2026-03-25','2026-04-23','2026-11-02','2026-11-03')],
        external_data_used_as_arvis_selection=False,confirmed_leading_themes=[],trade_eligible_ranking=[],
        limitations=['all_external_codes_unresolved','visual_transcription_not_independently_verified',
            'historical_name_reference_not_permanent_membership','partial_capture_and_date_coverage',
            'unverified_market_time_and_capital_eligibility'],
        actual_kiwoom_api_calls=0,telegram_sent=0)
    result['source_hash_unchanged_after_all_reads']=hashlib.sha256(path.read_bytes()).hexdigest()==inspection['sha256']
    if not result['source_hash_unchanged_after_all_reads']:raise ValueError('external_database_changed_during_audit')
    result['evaluation_reference']=digest(result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('db','ak-report','observation-report','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--legacy-db',type=Path)
    p.add_argument('--calendar-day',action='append',help='Read actual external/ARVIS records for this ISO date')
    a=p.parse_args()
    result=analyze(a.db,json.loads(a.ak_report.read_text(encoding='utf8')),
        json.loads(a.observation_report.read_text(encoding='utf8')),legacy_db=a.legacy_db)
    if a.calendar_day:
        own=[row for day in result['calendars'] for row in day['arvis_selection']['records']]
        result['calendars']=[calendar(result['source_data'],day,arvis_records=own) for day in a.calendar_day]
        result['evaluation_reference']=digest({k:v for k,v in result.items() if k!='evaluation_reference'})
    with a.output.open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(identity=result['external_identity']|{'legacy_master_exact_name_candidates':len(result['external_identity']['legacy_master_exact_name_candidates'])},
                         before=result['before']['mapped'],after=result['after']['mapped'])))


if __name__=='__main__':main()
