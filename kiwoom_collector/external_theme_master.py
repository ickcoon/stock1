"""C12-AL read-only inspection and explicit schema projection; never creates a DB.

No external SQL, scripts or instructions are executed. Layouts are reviewed column
names, not queries. Actual schema mapping remains gated until the supplied DB exists.
"""
import argparse
import hashlib
import json
from pathlib import Path

from .c12_latest import read_db
from .c12_master_audit import match
from .am_time_policy import instant
from .master_data import code as validate_code

SOURCE_KIND='external_trader_close'
SLOTS=frozenset(('09:40','09:45','09:50','15:00'))


def inspect(path,*,full_integrity=False):
    path=Path(path).resolve()
    if not path.is_file():
        return dict(state='missing',path=str(path),database_created=False,actual_rows_read=0)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    with read_db(path) as db:
        # Inventory names only; never execute DDL or text found inside the file.
        tables=[r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        structure=[]
        for name in tables:
            quoted='"'+name.replace('"','""')+'"'
            columns=[dict(r) for r in db.execute('PRAGMA table_info('+quoted+')')]
            count=db.execute('SELECT COUNT(*) FROM '+quoted).fetchone()[0]
            structure.append(dict(table=name,row_count=count,columns=columns))
        quick=[r[0] for r in db.execute('PRAGMA quick_check(1)')]
        integrity=[r[0] for r in db.execute('PRAGMA integrity_check')] if full_integrity else None
        fk=[tuple(r) for r in db.execute('PRAGMA foreign_key_check').fetchmany(101)]
    after=hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(state='schema_review_required',path=str(path),tables=structure,
        quick_check=quick,foreign_key_violations=fk[:100],foreign_key_check_truncated=len(fk)>100,
        integrity_check=integrity,
        sha256=before,hash_unchanged=before==after,database_created=False,
        integrity_scope='sqlite_quick_check_and_declared_foreign_keys_not_content_verification')


def project(path,layout):
    """Read a reviewed flat relation table. Relational joins need a schema-specific review.

    Required logical columns: trade_date/raw_theme/raw_stock_name/source.
    Optional: raw_stock_code, observed_at. Raw values are never normalized away.
    """
    path=Path(path).resolve(strict=True)
    required={'trade_date','raw_theme','raw_stock_name','source'}
    columns=layout['columns']
    if not required<=columns.keys() or not columns.keys()<=(required|{'raw_stock_code','observed_at'}):
        raise ValueError('reviewed_projection_columns_required')
    with read_db(path) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if layout['table'] not in tables:raise ValueError('projection_table_missing')
        quote=lambda value:'"'+value.replace('"','""')+'"'
        table=quote(layout['table'])
        available={r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
        if not set(columns.values())<=available:raise ValueError('projection_column_missing')
        select=','.join(quote(v)+' AS '+quote(k) for k,v in columns.items())
        records=[dict(r) for r in db.execute('SELECT '+select+' FROM '+table+' LIMIT 10001')]
    if len(records)>10000:raise ValueError('external_record_limit')
    # Same date/theme/name/code is one observation across sources; retain each source.
    unique={}
    for row in records:
        key=tuple(row.get(k) for k in ('trade_date','raw_theme','raw_stock_name','raw_stock_code'))
        entry=unique.setdefault(key,dict(row,source_kind=SOURCE_KIND,raw_records=[]))
        entry['raw_records'].append(dict(row))
    return dict(state='projected',records=list(unique.values()),raw_count=len(records),
                unique_count=len(unique),duplicate_count=len(records)-len(unique),layout=layout,
                transcription_complete=False)


def identify(record,master_rows,*,as_of,aliases=()):
    """Reuse exact code/name matching. Date-bounded reviewed aliases only.

    A legacy name match is a reference match, not verified listing/type evidence.
    Common/preferred distinctions require distinct canonical names and codes.
    """
    from datetime import date
    try:day=date.fromisoformat(record['trade_date'])
    except (ValueError,TypeError):return dict(state='unconfirmed',reason='trade_date_invalid')
    if day>instant(as_of).date():return dict(state='unconfirmed',reason='after_cutoff')
    raw_name=record['raw_stock_name'];raw_code=record.get('raw_stock_code')
    name=raw_name;code=raw_code
    reviews=[a for a in aliases if a['raw_name']==raw_name and a.get('verified') is True
             and a.get('evidence_url') and a['valid_from']<=record['trade_date']
             and (not a.get('valid_to') or record['trade_date']<a['valid_to'])]
    if reviews:
        if len({(a['stock_code'],a['canonical_name']) for a in reviews})!=1:
            return dict(state='unconfirmed',reason='alias_conflict')
        review=reviews[0]
        if raw_code and raw_code!=review['stock_code']:return dict(state='unconfirmed',reason='alias_code_conflict')
        code,name=review['stock_code'],review['canonical_name']
    if code is not None:
        try:validate_code(code)
        except ValueError:return dict(state='unconfirmed',reason='exact_string_code_required')
    result=match(dict(stock_code=code or '',name=name),master_rows)
    found=result.get('record')
    if not found:return dict(state='unconfirmed',reason=result['state'])
    # Never claim verified historical identity from an undated observation list.
    if (found.get('verified') is not True or not found.get('valid_from')
            or not found['valid_from']<=record['trade_date']
            or (found.get('valid_to') and record['trade_date']>=found['valid_to'])
            or not found.get('source')):
        return dict(state='reference_match_needs_review',stock_code=found['stock_code'],reason='historical_identity_unverified')
    return dict(state='identified',stock_code=found['stock_code'],canonical_name=found['stock_name'],
                raw_name=raw_name,raw_code=raw_code,matched_by=result['state'],trade_date=record['trade_date'])


def calendar_day(day,*,external_records=(),arvis_records=()):
    """Two provenance lanes, no synthetic selection records for missing slots."""
    from datetime import date
    date.fromisoformat(day)
    external=[dict(r) for r in external_records if r['trade_date']==day]
    own=[dict(r) for r in arvis_records if r['trade_date']==day]
    if any(r.get('source_kind')!=SOURCE_KIND for r in external):raise ValueError('external_source_kind_required')
    for row in own:
        if row.get('slot') not in SLOTS or not row.get('observed_at') or not row.get('selection_basis'):
            raise ValueError('arvis_selection_clock_and_basis_required')
        if row.get('source_kind')!='arvis_selection':raise ValueError('arvis_source_kind_required')
    return dict(trade_date=day,external_trader_close=dict(records=external,
        coverage='partial_transcription' if external else 'not_transcribed_or_unknown',
        absence_means_no_theme=False),
        arvis_selection=dict(records=own,missing_slots=sorted(SLOTS-{r['slot'] for r in own}),
                             missing_means_no_candidate=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',required=True,type=Path)
    p.add_argument('--layout',type=Path)
    p.add_argument('--full-integrity',action='store_true')
    a=p.parse_args();result=inspect(a.db,full_integrity=a.full_integrity)
    if result['state']!='missing' and a.layout:
        result['projection']=project(a.db,json.loads(a.layout.read_text(encoding='utf8')))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
