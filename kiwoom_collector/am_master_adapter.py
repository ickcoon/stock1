"""Enrich a C7 document from a separately reviewed, offline master/cache."""
from . import master_data as master
from .am_time_policy import from_document


def enrich(document, path, *, synthetic):
    policy = from_document(document)
    if policy is None:
        raise ValueError('master_adapter_requires_explicit_window_policy')
    reference = master.instant(document['selection_cutoff_at'])
    deadline = policy.deadline(reference)
    scope = {k:document[k] for k in ('source_kind','environment','market')}
    codes = sorted({s['stk_cd'] for r in document['rankings'] for s in r['stocks']})
    diagnostics = []
    with master.read_master(path,synthetic=synthetic) as (reader, provenance):
        for code in codes:
            stock = reader.stock(code,at=reference)
            tags = reader.classification(code,at=reference)
            cap = reader.capital(code,environment=document['environment'],at=deadline,ttl_seconds=policy.capital_ttl_seconds)
            sec = document['securities'].setdefault(code,{})
            diagnostic = dict(stock_code=code,stock_state=stock['state'],sector_state=tags['state'],capital_state=cap['state'])
            if stock['state']!='active':
                sec['sectors']=[]
                sec['market_cap']=[]
                diagnostics.append(diagnostic)
                continue
            if tags['state']=='conflict':
                sec['sectors']=[]
            if cap['state']=='conflict':
                sec['market_cap']=[]
            # A metadata lookup at the reference time is NOT an API observation.
            # Original source/review times are retained below and in the master.
            if stock['state']=='active':
                diagnostic['stock_source_updated_at'] = stock['source_updated_at']
                diagnostic['stock_registered_at'] = stock['registered_at']
            if tags['primary']:
                tag = tags['primary']
                existing = sec.get('sectors',[])
                if existing and {t['name'] for t in existing}!={tag['sector_name']}:
                    diagnostic['sector_state']='legacy_master_conflict'
                    sec['sectors']=[]
                else:
                    sec['sectors']=[dict(scope,name=tag['sector_name'],basis='master_reviewed_classification',
                        observed_at=reference.isoformat(),received_at=reference.isoformat())]
                diagnostic.update(sector_source_updated_at=tag['source_updated_at'],sector_registered_at=tag['registered_at'],
                    sector_valid_from=tag['valid_from'],sector_valid_to=tag['valid_to'],sector_record_hash=tag['record_hash'],
                    theme_tags=[t['sector_name'] for t in tags['tags'] if t['kind']=='theme'])
            if cap['state']=='hit':
                row=cap['record']
                value=dict(scope,value=row['normalized_won'],unit='KRW',verified=True,basis='master_verified_capital_cache',
                    observed_at=row['observed_at'],received_at=row['response_received_at'],cache_ttl_seconds=policy.capital_ttl_seconds)
                # Competing capital sources are not silently overwritten.
                if sec.get('market_cap'):
                    diagnostic['capital_state']='multiple_capital_sources'
                    sec['market_cap']=[]
                else:
                    sec['market_cap']=[value]
                diagnostic.update(capital_registered_at=row['registered_at'],capital_record_hash=row['record_hash'])
            diagnostics.append(diagnostic)
    document['master_diagnostics']=dict(version='7-C10-v1',sha256=provenance['sha256'],
        hash_unchanged=provenance['hash_unchanged'],metadata_time_kind='reference_lookup_with_original_source_times',records=diagnostics)
    return document
