"""C12-AE: stable read-only SQLite export for offline C12-AD, never delivery."""
import argparse
from contextlib import ExitStack
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from . import am_input_adapter as adapter, am_momentum as am, master_data
from .am_candidate_adapter import enrich
from .am_time_policy import ObservationPolicy
from .am_data_diagnostic import proof_for
from .cross_adapter import readonly_database
from .c12_replay import SLOTS
from .morning_briefing import freeze
from .telegram_briefing import scan


def stamp(value):
    try:
        am.instant(value)
        return value
    except (ValueError, TypeError):
        return None


def numeric(value):
    if isinstance(value, (str,int)) and not isinstance(value,bool) and re.fullmatch(r'[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?',str(value).strip()):
        return value
    return None


def code(value):
    return value if isinstance(value,str) and re.fullmatch(r'[0-9A-Z]{6}',value) else None


def bound_proof(record, source, evidence):
    record={k:v for k,v in record.items() if k not in ('now_rank','stored_turnover_rank')}
    proof=evidence.get('records',{}).get(adapter.fingerprint(record),{})
    return proof if proof.get('source_sha256')==source['sha256'] else {}


def stored_fields(path,role):
    """Only raw API date/time and stored unit declarations missing from C7."""
    result={}
    with readonly_database(path) as (db,_):
        table='ranking_records' if role=='ranking' else 'turnover_rows'
        for raw_row in db.execute('SELECT * FROM '+table):
            row=dict(raw_row)
            raw=json.loads(row.get('raw_json') or '{}')
            normalized=json.loads(row.get('normalized_json') or '{}')
            detail={k:raw[k] for k,n in (('dt',8),('date',8),('tm',6),('cntr_tm',6))
                    if isinstance(raw.get(k),str) and re.fullmatch(r'\d{'+str(n)+'}',raw[k])}
            unit=normalized.get('turnover_source_unit')
            if unit in ('million_KRW','KRW','hundred_million_KRW'):detail['declared_unit']=unit
            for key in ('unit_spec_verified','unit_runtime_verified'):
                if type(normalized.get(key)) is bool:detail[key]=normalized[key]
            key=(row.get('collection_id'),row.get('rank')) if role=='ranking' else (row.get('run_id'),row.get('page_no'),row.get('row_no'))
            result[key]=detail
    return result


def project(record,source,evidence,start,end,stored=None,environment='mock'):
    """No raw JSON, source IDs, names, arbitrary labels, or private metadata."""
    proof=bound_proof(record,source,evidence)
    original={k:v for k,v in record.items() if k not in ('now_rank','stored_turnover_rank')}
    reviewed_at=stamp(proof.get('received_at'))
    validation_at=max(end,am.instant(reviewed_at)) if reviewed_at else end
    verified=proof_for(original,source,evidence,start.date(),validation_at,environment) or {}
    acquisition=stamp(record.get('request_finished_at')) or stamp(record.get('collected_at'))
    acquisition=acquisition or stamp(record.get('request_started_at')) or stamp(record.get('updated_at'))
    reviewed=stamp(verified.get('received_at'))
    filtered_at=reviewed or acquisition
    # Legacy metadata may predate the requested trading day; it is not a trade.
    if record['kind']=='sector':
        if not acquisition or am.instant(acquisition)>end:return None
    elif not filtered_at or not start<=am.instant(filtered_at)<=end:
        return None
    out=dict(kind=record['kind'],source_reference=adapter.fingerprint(original),source_sha256=source['sha256'],
        environment=record.get('environment') if record.get('environment') in ('mock','real') else None,
        source_kind=record.get('source_kind') if record.get('source_kind') in ('fixture','live_test') else None,
        status=record.get('status') if record.get('status') in ('running','success','empty','partial','error','interrupted') else None,
        api_id=record.get('api_id') if record.get('api_id') in ('ka00198','ka10032','ka10007','ka10095') else None,
        collection_started_at=stamp(record.get('collected_at')),
        scheduled_at=stamp(record.get('scheduled_at')),
        request_started_at=stamp(record.get('request_started_at')),
        response_received_at=stamp(record.get('request_finished_at')),
        market_observed_at=stamp(verified.get('observed_at')),
        reviewed_received_at=stamp(verified.get('received_at')),
        time_filter_basis='stored_metadata_updated_at' if record['kind']=='sector' else
                          'bound_review_received_at' if reviewed else
                          'stored_response_received_at' if record.get('request_finished_at') else
                          'stored_collection_started_at' if record.get('collected_at') else 'stored_request_started_at')
    if record['kind']=='ranking':
        out['stocks']=[dict(stock_code=code(r.get('stk_cd')),rank=r.get('rank') if type(r.get('rank')) is int else None)
                       for r in record['stocks']]
        for stock in out['stocks']:
            stock['stored_api_fields']=(stored or {}).get((record['id'],stock['rank']),{})
    elif record['kind']=='sector':
        out.update(stock_code=code(record.get('stock_code')),
                   sector=record.get('sector_name') if record.get('sector_name') in master_data.BROAD_SECTORS else None,
                   metadata_updated_at=stamp(record.get('updated_at')))
    else:
        details=(stored or {}).get((record.get('run_id'),record.get('page_no'),record.get('row_no')),{})
        unit=proof.get('units',{}).get('trde_prica') or details.get('declared_unit')
        out.update(stock_code=code(record.get('stock_code') or record.get('requested_code')),
                   rank=record.get('now_rank') if type(record.get('now_rank')) is int else None,
                   stored_rank=record.get('stored_turnover_rank'),turnover_raw=numeric(record.get('trde_prica')),
                   turnover_unit=unit if unit in ('million_KRW','KRW','hundred_million_KRW') else None,
                   unit_verified=bool(verified) and proof.get('units',{}).get('trde_prica') in ('million_KRW','KRW','hundred_million_KRW') and proof.get('unit_runtime_verified') is True,
                   unit_source='bound_verification_manifest' if proof.get('units',{}).get('trde_prica') else
                               'stored_normalized_json' if details.get('declared_unit') else 'not_stored_or_not_verified',
                   stored_api_fields=details,
                   stored_market_date=record.get('date') if isinstance(record.get('date'),str) and re.fullmatch(r'\d{8}',record['date']) else None,
                   stored_market_time=record.get('tm') if isinstance(record.get('tm'),str) and re.fullmatch(r'\d{6}',record['tm']) else None,
                   value_source_field='trde_prica')
        # 0186 raw rank is commonly stored as a numeric string; preserve its type.
        if numeric(record.get('now_rank')) is not None:out['rank']=record['now_rank']
        if type(out['stored_rank']) is not int:out['stored_rank']=None
    out['unavailable']=[k for k,v in out.items() if v is None]
    return out


def portable(document):
    """Drop free-text payloads; preserve engine inputs, numbers and source times."""
    result={k:deepcopy(document[k]) for k in ('schema_version','source_kind','trade_date','environment','market',
             'selection_cutoff_at','rankings','securities','observation_policy') if k in document}
    for sec in result['securities'].values():
        sec.pop('names',None)  # Not needed for replay; exclude arbitrary personal labels.
        for kind,values in list(sec.items()):
            if kind=='sectors':
                values=[v for v in values if v.get('name') in master_data.BROAD_SECTORS]
                sec[kind]=values
            for value in values:
                value['basis']='source_'+hashlib.sha256(str(value['basis']).encode()).hexdigest()
    if document.get('candidate_filter'):
        filters=document['candidate_filter']
        result['candidate_filter']=dict(version=filters['version'],at=filters['at'],decisions={
            c:{k:deepcopy(v[k]) for k in ('state','confidence','reasons') if k in v}
            for c,v in filters['decisions'].items()})
    return freeze(result)


def master_inputs(document,path,synthetic):
    """Existing point-in-time readers; original metadata times stay metadata."""
    reference=am.instant(document['selection_cutoff_at'])
    scope={k:document[k] for k in ('source_kind','environment','market')}
    stored=[]
    with master_data.read_master(path,synthetic=synthetic) as (reader,_):
        for c in sorted({r['stk_cd'] for s in document['rankings'] for r in s['stocks']}):
            stock=reader.stock(c,at=reference)
            tags=reader.classification(c,at=reference)
            capital=reader.capital(c,environment=document['environment'],at=reference,ttl_seconds=3600)
            item=dict(stock_code=c,stock_state=stock['state'],sector_state=tags['state'],capital_state=capital['state'],
                      eligibility_origin='existing_policy_over_stored_master_not_a_stored_trade_signal')
            sec=document['securities'].setdefault(c,{})
            if tags['primary']:
                tag=tags['primary']
                item['sector']=dict(name=tag['sector_name'] if tag['sector_name'] in master_data.BROAD_SECTORS else None,
                    source_updated_at=stamp(tag['source_updated_at']),registered_at=stamp(tag['registered_at']),
                    valid_from=stamp(tag['valid_from']),valid_to=stamp(tag['valid_to']),
                    time_kind='stored_metadata_times_not_api_market_times')
                if item['sector']['name']:
                    sec.setdefault('sectors',[]).append(dict(scope,name=tag['sector_name'],basis='stored_master_metadata',
                        observed_at=tag['source_updated_at'],received_at=tag['registered_at']))
            if capital.get('record'):
                row=capital['record']
                item['capital']=dict(api_id=row['api_id'],raw_mac=numeric(row['raw_mac']),
                    declared_unit=row['declared_unit'] if row['declared_unit'] in ('hundred_million_KRW','KRW') else None,
                    verified_unit=row['verified_unit'] if row['verified_unit'] in ('hundred_million_KRW','KRW') else None,
                    normalized_won=numeric(row['normalized_won']),observed_at=stamp(row['observed_at']),
                    response_received_at=stamp(row['response_received_at']),registered_at=stamp(row['registered_at']))
                if capital['state']=='hit':
                    sec.setdefault('market_cap',[]).append(dict(scope,value=row['normalized_won'],unit='KRW',verified=True,
                        basis='stored_verified_capital_cache',observed_at=row['observed_at'],
                        received_at=row['response_received_at'],cache_ttl_seconds=3600))
            stored.append(item)
    return enrich(document,path,synthetic=synthetic),stored


def export(ranking_db, *, trade_date, environment, synthetic, turnover_db=None, evidence=None,
           master_db=None, from_time='09:00', until='09:50', test_mode=False):
    date.fromisoformat(trade_date)
    start=am.instant(trade_date+'T'+from_time+':00+09:00')
    end=am.instant(trade_date+'T'+until+':00+09:00')
    slots=[s for s in SLOTS if start<=am.instant(trade_date+'T'+s+':00+09:00')<=end]
    if not slots or start>end:raise ValueError('export_window_requires_replay_phase')
    turnover_paths=list(turnover_db) if isinstance(turnover_db,(list,tuple)) else ([] if turnover_db is None else [turnover_db])
    if len({Path(p).resolve() for p in turnover_paths})!=len(turnover_paths):
        raise ValueError('duplicate_turnover_source')
    evidence=deepcopy(evidence or {})
    if evidence and (evidence.get('schema_version')!='am-verification-manifest-v1' or evidence.get('synthetic') is not synthetic):
        raise ValueError('verification_manifest_scope_invalid')
    # Evidence references are arbitrary free text; retain only a stable digest.
    for proof in evidence.get('records',{}).values():
        proof['evidence_reference']='proof_'+hashlib.sha256(str(proof.get('evidence_reference','')).encode()).hexdigest()
    with ExitStack() as stack:
        guards={}
        paths=[('ranking',ranking_db),('master',master_db)]+[
            ('turnover' if i==0 else f'turnover_{i+1}',path) for i,path in enumerate(turnover_paths)]
        for role,path in paths:
            if path is not None:guards[role]=stack.enter_context(readonly_database(path))[1]
        sources=[adapter.read_sources(ranking_db)]
        sources.extend(adapter.read_sources(path,role='turnover') for path in turnover_paths)
        raw_details=[stored_fields(ranking_db,'ranking')]+[stored_fields(path,'turnover') for path in turnover_paths]
        observations=[];omitted=[]
        for source,details in zip(sources,raw_details):
            for record in source.get('diagnostic_records',source['records']):
                same_environment=record.get('environment',environment)==environment
                row=project(record,source,evidence,start,end,details,environment) if same_environment else None
                if row is not None:
                    observations.append(row)
                else:
                    omitted.append(dict(source_sha256=source['sha256'],kind=record['kind'],
                        reason='outside_window_or_time_unavailable' if same_environment else 'other_environment'))
        documents=[];metadata=[];issues=[]
        for slot in slots:
            cutoff=trade_date+'T'+slot+':00+09:00'
            try:
                doc=adapter.adapt(ranking_db,trade_date=trade_date,cutoff_at=cutoff,environment=environment,
                    synthetic=synthetic,test_mode=test_mode,turnover_db=turnover_paths or None,evidence=evidence,
                    observation_policy=ObservationPolicy().document())
                if master_db is not None:
                    doc,stored=master_inputs(doc,master_db,synthetic)
                    metadata.append(dict(slot=slot,records=stored))
                doc=portable(doc)
                doc['rankings']=[r for r in doc['rankings'] if am.instant(r['observed_at'])>=start]
                for sec in doc['securities'].values():
                    for kind,values in sec.items():
                        if kind!='sectors':sec[kind]=[v for v in values if am.instant(v['observed_at'])>=start]
                documents.append(doc)
            except (ValueError,KeyError,TypeError,ArithmeticError):
                # Keep raw safe observations and an explicit unusable phase.
                documents.append(dict(schema_version=am.INPUT_SCHEMA if synthetic else am.ADAPTER_INPUT_SCHEMA,
                    source_kind='fixture' if synthetic else 'readonly_adapter',trade_date=trade_date,
                    selection_cutoff_at=cutoff,environment=environment,market='KRX',rankings=[],securities={}))
                issues.append(dict(slot=slot,reason='stored_data_or_verification_invalid'))
        result=dict(schema_version='c12-replay-input-v1',documents=documents,
            export=dict(version='c12-ae-v1',trade_date=trade_date,from_time=from_time,until=until,
                source_summaries=[dict(role=role,sha256=p['sha256']) for role,p in sorted(guards.items())],
                record_counts=[dict(role=s['role'],sha256=s['sha256'],records=len(s['records'])) for s in sources],
                observations=sorted(observations,key=am.canonical),omitted_records=sorted(omitted,key=am.canonical),
                master_snapshots=metadata,issues=issues,
                unavailable=[k for k,p in (('turnover_database',turnover_paths or None),('master_database',master_db)) if p is None]+
                            ([] if evidence else ['verification_manifest']),
                source_times='collection_start_and_response_receipt_are_not_market_observation_times',
                privacy='allowlisted_fields_only_no_names_or_raw_payloads'))
        scan(result)
    for source in result['export']['source_summaries']:
        source['hash_unchanged']=guards[source['role']]['hash_unchanged']
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ranking-db',required=True,type=Path)
    p.add_argument('--turnover-db',type=Path,action='append',help='repeat for existing per-slot 0186 databases')
    p.add_argument('--master-db',type=Path)
    p.add_argument('--evidence',type=Path)
    p.add_argument('--date',required=True)
    p.add_argument('--environment',required=True,choices=('mock','real'))
    p.add_argument('--synthetic',action='store_true')
    p.add_argument('--test-mode',action='store_true')
    p.add_argument('--from-time',default='09:00')
    p.add_argument('--until',default='09:50')
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args(argv)
    try:
        evidence=json.loads(a.evidence.read_text(encoding='utf-8')) if a.evidence else None
        result=export(a.ranking_db,trade_date=a.date,environment=a.environment,synthetic=a.synthetic,
            turnover_db=a.turnover_db,master_db=a.master_db,evidence=evidence,
            from_time=a.from_time,until=a.until,test_mode=a.test_mode)
        with a.output.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
        print(json.dumps(dict(status='exported',phases=len(result['documents']),
                              observations=len(result['export']['observations'])),ensure_ascii=False))
        return 0
    except (OSError,ValueError,KeyError,TypeError,ArithmeticError,RuntimeError,sqlite3.Error):
        print('Export rejected: source, scope, evidence or new output path invalid.')
        return 1


if __name__=='__main__':raise SystemExit(main())
