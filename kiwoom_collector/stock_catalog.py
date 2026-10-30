"""ka10099 two-market collection and atomic C10 publication. Injected IO only."""
import argparse
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

from . import master_data as master

API_ID='ka10099'
API_PATH='/api/dostk/stkinfo'
MARKETS={'0':'KOSPI','10':'KOSDAQ'}
SECTORS=('반도체','자동차','로봇','원전·전력','조선','방산·우주','2차전지','제약·바이오','IT·AI',
         '금융','화학·소재','철강·기계','건설·인프라','소비재·유통','기타')
FIELDS=('code','name','listCount','auditInfo','regDay','lastPrice','state','marketCode','marketName',
        'upName','upSizeName','orderWarning','companyClassName','nxtEnable')
RUN_ROOT=master.PROJECT/'diagnostics'/'catalog_runs'
DEFAULT_RULES=master.PROJECT/'config'/'arvis_sector_mapping.json'
DDL=(
'''CREATE TABLE IF NOT EXISTS catalog_runs(run_id TEXT PRIMARY KEY,environment TEXT NOT NULL,synthetic INTEGER NOT NULL,
 started_at TEXT NOT NULL,finished_at TEXT NOT NULL,applied_at TEXT NOT NULL,dataset_sha256 TEXT NOT NULL UNIQUE,
 rules_sha256 TEXT NOT NULL,report_json TEXT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS catalog_taxonomy(taxonomy_key TEXT PRIMARY KEY,sector_id TEXT NOT NULL REFERENCES sector_master(sector_id),sector_name TEXT NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS catalog_items(run_id TEXT NOT NULL REFERENCES catalog_runs(run_id),stock_code TEXT NOT NULL REFERENCES stock_master(stock_code),
 market TEXT NOT NULL,stock_name TEXT NOT NULL,raw_industry TEXT NOT NULL,listing_date TEXT,raw_state TEXT NOT NULL,
 market_code TEXT NOT NULL,market_name TEXT NOT NULL,raw_json TEXT NOT NULL,present INTEGER NOT NULL,
 security_type TEXT NOT NULL,type_verified INTEGER NOT NULL,type_evidence TEXT NOT NULL,
 mapping_state TEXT NOT NULL,representative_sector_id TEXT,updated_at TEXT NOT NULL,source TEXT NOT NULL,
 PRIMARY KEY(run_id,stock_code))''',
'''CREATE INDEX IF NOT EXISTS idx_catalog_items_code ON catalog_items(stock_code,run_id)''',
'''CREATE TABLE IF NOT EXISTS catalog_pages(run_id TEXT NOT NULL REFERENCES catalog_runs(run_id),market TEXT NOT NULL,page_no INTEGER NOT NULL,
 row_count INTEGER NOT NULL,started_at TEXT NOT NULL,received_at TEXT NOT NULL,has_more INTEGER NOT NULL,
 continuation_fingerprint TEXT NOT NULL,PRIMARY KEY(run_id,market,page_no))''',
'''CREATE TABLE IF NOT EXISTS catalog_changes(run_id TEXT NOT NULL REFERENCES catalog_runs(run_id),stock_code TEXT NOT NULL,
 action TEXT NOT NULL,detail_json TEXT NOT NULL)''')


class CatalogError(ValueError):
    def __init__(self, reason):
        self.reason=reason
        super().__init__(reason)


@dataclass(frozen=True)
class Limits:
    max_pages: int=100
    max_rows: int=20000
    seconds: float=300
    spacing_seconds: float=1.2
    minimum_per_market: int=100
    maximum_drop_fraction: float=0.2

    def __post_init__(self):
        for key,lo,hi in (('max_pages',1,500),('max_rows',2,50000),('minimum_per_market',1,10000)):
            v=getattr(self,key)
            if type(v) is not int or not lo<=v<=hi: raise CatalogError('invalid_limits')
        if (not math.isfinite(self.seconds) or not 1<=self.seconds<=1800
                or not math.isfinite(self.spacing_seconds) or not 1.1<=self.spacing_seconds<=10
                or not math.isfinite(self.maximum_drop_fraction) or not 0<=self.maximum_drop_fraction<=0.5):
            raise CatalogError('invalid_limits')


def now():
    return datetime.now(timezone.utc).astimezone(master.mc.KST).isoformat()


def parse_row(row, market):
    if market not in MARKETS or not isinstance(row,dict): raise CatalogError('invalid_row')
    raw={}
    for field in FIELDS:
        value=row.get(field,'')
        if not isinstance(value,str) or len(value)>500: raise CatalogError('invalid_field_type_or_length')
        raw[field]=value
    try: master.code(raw['code'])
    except ValueError: raise CatalogError('invalid_stock_code') from None
    if not raw['name'].strip(): raise CatalogError('missing_stock_name')
    if raw['marketCode']!=market: raise CatalogError('response_market_mismatch')
    listing=None
    if raw['regDay']:
        try:
            listing=datetime.strptime(raw['regDay'],'%Y%m%d').date().isoformat()
            if len(raw['regDay'])!=8: raise ValueError()
        except ValueError: raise CatalogError('invalid_listing_date') from None
    return dict(stock_code=raw['code'],stock_name=raw['name'],market=MARKETS[market],raw_industry=raw['upName'],
                listing_date=listing,raw_state=raw['state'],market_code=raw['marketCode'],market_name=raw['marketName'],raw=raw)


def _page(envelope, market):
    if not isinstance(envelope,dict) or envelope.get('http_status')!=200: raise CatalogError('http_failed')
    body=envelope.get('body')
    if not isinstance(body,dict) or type(body.get('return_code')) not in (int,str) or str(body['return_code'])!='0':
        raise CatalogError('api_failed_or_missing_code')
    if not isinstance(body.get('list'),list): raise CatalogError('missing_or_invalid_list')
    headers=envelope.get('headers',{})
    if not isinstance(headers,dict): raise CatalogError('invalid_headers')
    headers={str(k).lower():v for k,v in headers.items()}
    continuation=headers.get('cont-yn','')
    key=headers.get('next-key','')
    if continuation not in ('','N','Y') or not isinstance(key,str) or len(key)>50:
        raise CatalogError('invalid_continuation')
    if continuation=='Y' and (not key.strip() or not body['list']): raise CatalogError('incomplete_continuation')
    if continuation!='Y' and key: raise CatalogError('terminal_key_conflict')
    start,received=master.instant(envelope.get('started_at')),master.instant(envelope.get('received_at'))
    if start>received or start.date()!=received.date(): raise CatalogError('invalid_page_interval')
    rows=[parse_row(r,market) for r in body['list']]
    return rows,continuation=='Y',key,start.isoformat(),received.isoformat()


def collect(transport, *, environment, synthetic, limits=Limits(), clock=time.monotonic, sleep=time.sleep):
    """Collect both markets completely BEFORE opening a master writer. No retry."""
    if environment not in ('mock','real') or type(synthetic) is not bool or not callable(transport):
        raise CatalogError('invalid_collection_scope')
    if isinstance(transport,FixtureTransport) and not synthetic:
        raise CatalogError('fixture_cannot_be_live_source')
    deadline=clock()+limits.seconds
    rows=[];pages=[];seen=set();last_start=None
    for market in MARKETS:
        key='';keys=set();count=0
        for page_no in range(1,limits.max_pages+1):
            if last_start is not None:
                remaining=last_start+limits.spacing_seconds-clock()
                while remaining>0:
                    if clock()+remaining>=deadline: raise CatalogError('collection_deadline')
                    sleep(remaining);remaining=last_start+limits.spacing_seconds-clock()
            if clock()>=deadline: raise CatalogError('collection_deadline')
            last_start=clock()
            try:
                envelope=transport(market=market,cont_yn='Y' if key else '',next_key=key,
                                   remaining_seconds=max(0.001,deadline-clock()))
            except CatalogError: raise
            except Exception: raise CatalogError('transport_failed') from None
            if clock()>=deadline: raise CatalogError('collection_deadline')
            data,has_more,next_key,started,received=_page(envelope,market)
            if pages and master.instant(started)<master.instant(pages[-1]['received_at']):
                raise CatalogError('nonmonotonic_page_times')
            for row in data:
                if row['stock_code'] in seen: raise CatalogError('duplicate_stock_code')
                seen.add(row['stock_code']);rows.append(dict(row,updated_at=received));count+=1
            if len(rows)>limits.max_rows: raise CatalogError('row_limit_exceeded')
            pages.append(dict(market=MARKETS[market],page_no=page_no,row_count=len(data),started_at=started,
                received_at=received,has_more=has_more,continuation_fingerprint=master.digest(next_key)))
            if not has_more: break
            if next_key in keys: raise CatalogError('continuation_loop')
            keys.add(next_key);key=next_key
        else: raise CatalogError('page_limit_incomplete')
        if count<limits.minimum_per_market: raise CatalogError('market_count_below_minimum')
    if master.instant(pages[0]['started_at']).date()!=master.instant(pages[-1]['received_at']).date():
        raise CatalogError('collection_crosses_day')
    if synthetic and isinstance(transport,FixtureTransport) and transport.index!=len(transport.pages):
        raise CatalogError('unused_fixture_pages')
    dataset=dict(schema_version='ka10099-dataset-v1',environment=environment,synthetic=synthetic,
        source_kind='fixture' if synthetic else 'ka10099_live',started_at=pages[0]['started_at'],
        finished_at=pages[-1]['received_at'],rows=rows,pages=pages,pagination_complete=True)
    dataset['fingerprint']=master.digest(dataset)
    return dataset


class FixtureTransport:
    def __init__(self, document):
        if document.get('schema_version')!='ka10099-fixture-v1' or document.get('synthetic') is not True:
            raise CatalogError('explicit_synthetic_fixture_required')
        self.pages=document['pages'];self.index=0;self.calls=0

    def __call__(self, *, market,cont_yn,next_key,remaining_seconds):
        if self.index>=len(self.pages): raise CatalogError('fixture_exhausted')
        item=self.pages[self.index];self.index+=1
        self.calls+=1
        if item.get('request')!={'mrkt_tp':market,'cont-yn':cont_yn,'next-key':next_key}:
            raise CatalogError('fixture_request_chain_mismatch')
        return item['response']


def rules_document(document):
    if document.get('schema_version')!='arvis-sector-mapping-v1' or tuple(document.get('sectors',()))!=SECTORS:
        raise CatalogError('invalid_mapping_taxonomy')
    if not isinstance(document.get('version'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',document['version']):
        raise CatalogError('invalid_mapping_version')
    for rule in document.get('rules',[]):
        if (not isinstance(rule.get('raw_industry'),str) or not rule['raw_industry'] or rule.get('sector') not in SECTORS
                or type(rule.get('reviewed')) is not bool): raise CatalogError('invalid_mapping_rule')
    for stock_code,review in document.get('security_reviews',{}).items():
        master.code(stock_code)
        if (review.get('type') not in ('common_stock','ETF','ETN','SPAC','preferred_stock','REIT','other')
                or review.get('verified') is not True or not isinstance(review.get('evidence'),str) or not review['evidence']
                or not master.instant(review.get('reviewed_at'))): raise CatalogError('invalid_security_review')
    return document


def map_industry(raw_industry, rules):
    matches=[r for r in rules['rules'] if r['raw_industry']==raw_industry]
    targets={r['sector'] for r in matches}
    if not targets: return dict(state='unclassified',sector=None,reviewed=False)
    if len(targets)!=1: return dict(state='conflict',sector=None,reviewed=False)
    return dict(state='mapped' if all(r['reviewed'] for r in matches) else 'review_needed',
                sector=next(iter(targets)),reviewed=all(r['reviewed'] for r in matches))


def security_type(row, rules, at):
    review=rules.get('security_reviews',{}).get(row['stock_code'])
    hint={'ETF':'ETF','ETN':'ETN','기업인수목적회사':'SPAC','스팩':'SPAC'}.get(row['raw']['companyClassName'],'unknown')
    if review and master.instant(review['reviewed_at'])<=master.instant(at):
        if hint!='unknown' and review['type']!=hint:
            return hint,False,'security_review_conflict'
        if row['listing_date'] and master.instant(review['reviewed_at']).date().isoformat()<row['listing_date']:
            return hint,False,'security_review_predates_listing'
        return review['type'],True,review['evidence']
    # Company classification is stored as an UNVERIFIED hint, never a name guess.
    return hint,False,'ka10099_type_unverified'


def _taxonomy(store,at):
    ids={}
    for index,name in enumerate(SECTORS,1):
        existing=store.db.execute("SELECT sector_id FROM sector_master WHERE kind='industry' AND sector_name=?",(name,)).fetchone()
        sector_id=existing[0] if existing else f'arvis_c11_{index:02}'
        if not existing: store.put_sector(sector_id,name,kind='industry',source='arvis_c11_taxonomy',updated_at=at)
        key=f'c11_{index:02}'
        store.db.execute('INSERT OR IGNORE INTO catalog_taxonomy VALUES(?,?,?)',(key,sector_id,name))
        old=store.db.execute('SELECT sector_id,sector_name FROM catalog_taxonomy WHERE taxonomy_key=?',(key,)).fetchone()
        if tuple(old)!=(sector_id,name): raise CatalogError('taxonomy_conflict')
        ids[name]=sector_id
    return ids


def _assignment(store,row,rules,ids,at):
    existing=[dict(r) for r in store.db.execute('''SELECT t.*,s.kind,s.sector_name FROM stock_sector t
        JOIN sector_master s ON s.sector_id=t.sector_id WHERE t.stock_code=? AND t.registered_at<=?
        ORDER BY t.registered_at,t.id''',(row['stock_code'],at))]
    latest={}
    for item in existing: latest[item['sector_id']]=item
    current=[r for r in latest.values() if r['kind']=='industry' and r['valid_from']<=at
             and (r['valid_to'] is None or r['valid_to']>at)]
    manual=[r for r in current if not r['source'].startswith('ka10099_mapping:')]
    mapping=map_industry(row['raw_industry'],rules)
    if manual:
        eligible=[r for r in manual if r['status']=='verified']
        primary=[r for r in eligible if r['is_primary']]
        chosen=primary[0] if len(primary)==1 else eligible[0] if len(eligible)==1 else None
        for item in current:
            if item['source'].startswith('ka10099_mapping:'):
                _expire_auto(store,item,at)
        if chosen and chosen['sector_name'] in SECTORS and (mapping['sector'] in (None,chosen['sector_name'])):
            return 'preserved_manual',chosen['sector_id']
        return 'manual_review_needed',None
    target=ids.get(mapping['sector'])
    for item in current:
        if item['sector_id']!=target or not mapping['reviewed']:
            _expire_auto(store,item,at)
    if target:
        store.put_tag(row['stock_code'],target,source='ka10099_mapping:'+rules['version'],evidence='exact_raw_industry_mapping',
            updated_at=row['updated_at'],registered_at=at,valid_from=at,is_primary=mapping['reviewed'],
            status='verified' if mapping['reviewed'] else 'proposed')
    return mapping['state'],target if mapping['reviewed'] else None


def _expire_auto(store,item,at):
    if item['valid_from']==at: raise CatalogError('duplicate_mapping_timestamp')
    store.put_tag(item['stock_code'],item['sector_id'],source=item['source'],evidence=item['evidence'],
        updated_at=item['source_updated_at'],registered_at=at,valid_from=item['valid_from'],valid_to=at,
        is_primary=bool(item['is_primary']),status=item['status'],source_sha256=item['source_sha256'])


def publish(path,dataset,*,rules,limits=Limits(),applied_at=None):
    unsigned={k:v for k,v in dataset.items() if k!='fingerprint'}
    if (dataset.get('schema_version')!='ka10099-dataset-v1' or dataset.get('pagination_complete') is not True
            or master.digest(unsigned)!=dataset.get('fingerprint')): raise CatalogError('dataset_integrity_failed')
    if (type(dataset.get('synthetic')) is not bool or dataset.get('environment') not in ('mock','real')
            or dataset.get('source_kind')!=('fixture' if dataset['synthetic'] else 'ka10099_live')):
        raise CatalogError('invalid_dataset_scope')
    rules=rules_document(rules)
    at=master.instant(applied_at or now()).isoformat()
    if master.instant(at)<master.instant(dataset['finished_at']): raise CatalogError('publication_before_capture')
    rows=dataset['rows'];incoming={r['stock_code']:r for r in rows}
    if len(incoming)!=len(rows) or set(r['market'] for r in rows)!=set(MARKETS.values()): raise CatalogError('invalid_dataset_scope')
    counts={market:sum(r['market']==market for r in rows) for market in MARKETS.values()}
    if any(n<limits.minimum_per_market for n in counts.values()): raise CatalogError('market_count_below_minimum')
    if len(rows)>limits.max_rows: raise CatalogError('row_limit_exceeded')
    for market in MARKETS.values():
        pages=[p for p in dataset['pages'] if p['market']==market]
        if (not pages or len(pages)>limits.max_pages or [p['page_no'] for p in pages]!=list(range(1,len(pages)+1))
                or pages[-1]['has_more'] is not False or any(p['has_more'] is not True for p in pages[:-1])
                or sum(p['row_count'] for p in pages)!=counts[market]):
            raise CatalogError('incomplete_dataset_pages')
    for row in rows:
        parsed=parse_row(row['raw'],row['market_code'])
        if any(row[k]!=v for k,v in parsed.items()): raise CatalogError('dataset_row_mismatch')
    with master.MasterStore(path) as store:
        if store.db.execute('SELECT synthetic FROM master_meta').fetchone()[0]!=int(dataset['synthetic']):
            raise CatalogError('master_source_scope_mismatch')
        for sql in DDL: store.db.execute(sql)
        prior=store.db.execute('SELECT * FROM catalog_runs ORDER BY applied_at DESC,rowid DESC LIMIT 1').fetchone()
        if prior and (prior['environment']!=dataset['environment'] or prior['synthetic']!=int(dataset['synthetic'])):
            raise CatalogError('catalog_environment_mismatch')
        if prior and prior['applied_at']>=at: raise CatalogError('nonmonotonic_publication')
        if prior and prior['finished_at']>=dataset['finished_at']: raise CatalogError('stale_dataset')
        if store.db.execute('SELECT 1 FROM catalog_runs WHERE dataset_sha256=?',(dataset['fingerprint'],)).fetchone():
            raise CatalogError('dataset_already_applied')
        previous={r['stock_code']:dict(r) for r in store.db.execute('SELECT * FROM stock_master')}
        before={market:sum(r['market']==market for r in previous.values()) for market in MARKETS.values()}
        if prior:
            previous_counts=json.loads(prior['report_json'])['collected_counts']
            if any(counts[m]<previous_counts[m]*(1-limits.maximum_drop_fraction) for m in counts):
                raise CatalogError('unexpected_market_count_drop')
        ids=_taxonomy(store,at)
        manual_before=[tuple(r) for r in store.db.execute("SELECT * FROM stock_sector WHERE instr(source,'ka10099_mapping:')<>1 ORDER BY id")]
        run_id=uuid.uuid4().hex
        report=dict(run_id=run_id,environment=dataset['environment'],synthetic=dataset['synthetic'],before_counts=before,
            collected_counts=counts,after_counts={},changes={},mapping_counts={},missing_possible=[],
            complete_pagination=True,universe_completeness='no_authoritative_total_in_response',
            source_api_id=API_ID,applied_at=at)
        store.db.execute('INSERT INTO catalog_runs VALUES(?,?,?,?,?,?,?,?,?)',(run_id,dataset['environment'],int(dataset['synthetic']),
            dataset['started_at'],dataset['finished_at'],at,dataset['fingerprint'],master.digest(rules),'{}'))

        def change(stock_code,action,detail):
            store.db.execute('INSERT INTO catalog_changes VALUES(?,?,?,?)',(run_id,stock_code,action,json.dumps(detail,ensure_ascii=False)))
            report['changes'][action]=report['changes'].get(action,0)+1

        for original in rows:
            row=dict(original)
            old=previous.get(row['stock_code'])
            if old is None: change(row['stock_code'],'added',{'market':row['market']})
            else:
                if old['stock_name']!=row['stock_name']: change(row['stock_code'],'name_changed',{'before':old['stock_name'],'after':row['stock_name']})
                if old['market']!=row['market']: change(row['stock_code'],'market_transferred',{'before':old['market'],'after':row['market']})
            listing_known=bool(row['listing_date'] and row['listing_date']<=row['updated_at'][:10])
            listing_status='listed' if listing_known else 'unknown'
            if old is None or any((old['stock_name']!=row['stock_name'],old['market']!=row['market'],
                                   old['listing_status']!=listing_status,bool(old['verified'])!=listing_known)):
                store.put_stock(row['stock_code'],row['stock_name'],row['market'],listing_status,source='ka10099:'+dataset['environment'],
                    updated_at=row['updated_at'],registered_at=at,verified=listing_known)
            kind,verified,evidence=security_type(row,rules,at)
            mapping,target=_assignment(store,row,rules,ids,at)
            report['mapping_counts'][mapping]=report['mapping_counts'].get(mapping,0)+1
            _insert_item(store,run_id,row,True,kind,verified,evidence,mapping,target)
            if prior:
                old_item=store.db.execute('SELECT present,raw_industry FROM catalog_items WHERE run_id=? AND stock_code=?',(prior['run_id'],row['stock_code'])).fetchone()
                if old_item and not old_item['present']: change(row['stock_code'],'reappeared',{})
                if old_item and old_item['raw_industry']!=row['raw_industry']: change(row['stock_code'],'industry_changed',{'before':old_item['raw_industry'],'after':row['raw_industry']})
                previous_item=store.db.execute('SELECT listing_date,raw_state,security_type,type_verified FROM catalog_items WHERE run_id=? AND stock_code=?',(prior['run_id'],row['stock_code'])).fetchone()
                if previous_item:
                    for action,old_value,new_value in (
                        ('listing_date_changed',previous_item['listing_date'],row['listing_date']),
                        ('state_changed',previous_item['raw_state'],row['raw_state']),
                        ('security_type_changed',(previous_item['security_type'],bool(previous_item['type_verified'])),(kind,verified))):
                        if old_value!=new_value: change(row['stock_code'],action,{'before':old_value,'after':new_value})
        for stock_code,old in previous.items():
            if stock_code in incoming or old['market'] not in MARKETS.values(): continue
            report['missing_possible'].append(stock_code)
            change(stock_code,'missing_from_requested_scope',{'listing_status_preserved':old['listing_status']})
            row=dict(stock_code=stock_code,stock_name=old['stock_name'],market=old['market'],raw_industry='',listing_date=None,
                raw_state='',market_code=next(k for k,v in MARKETS.items() if v==old['market']),market_name='',raw={},updated_at=old['source_updated_at'])
            old_item=store.db.execute('SELECT * FROM catalog_items WHERE run_id=? AND stock_code=?',(prior['run_id'],stock_code)).fetchone() if prior else None
            if old_item:
                for field in ('raw_industry','listing_date','raw_state','market_code','market_name','updated_at'): row[field]=old_item[field]
                row['raw']=json.loads(old_item['raw_json'])
            _insert_item(store,run_id,row,False,old_item['security_type'] if old_item else 'unknown',
                         bool(old_item['type_verified']) if old_item else False,
                         old_item['type_evidence'] if old_item else 'absence_is_not_delisting','review_needed',None)
        for page in dataset['pages']:
            store.db.execute('INSERT INTO catalog_pages VALUES(?,?,?,?,?,?,?,?)',(run_id,page['market'],page['page_no'],page['row_count'],
                page['started_at'],page['received_at'],int(page['has_more']),page['continuation_fingerprint']))
        report['after_counts']={m:store.db.execute('SELECT count(*) FROM stock_master WHERE market=?',(m,)).fetchone()[0] for m in counts}
        report['preserved_stock_sector_count']=store.db.execute('SELECT count(*) FROM stock_sector').fetchone()[0]
        manual_after=[tuple(r) for r in store.db.execute("SELECT * FROM stock_sector WHERE instr(source,'ka10099_mapping:')<>1 ORDER BY id")]
        if manual_before!=manual_after: raise CatalogError('manual_classification_changed')
        report['manual_tag_rows_preserved']=len(manual_before)
        store.db.execute('UPDATE catalog_runs SET report_json=? WHERE run_id=?',(json.dumps(report,ensure_ascii=False),run_id))
    return report


def _insert_item(store,run_id,row,present,kind,verified,evidence,mapping,target):
    store.db.execute('INSERT INTO catalog_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
        run_id,row['stock_code'],row['market'],row['stock_name'],row['raw_industry'],row['listing_date'],row['raw_state'],
        row['market_code'],row['market_name'],json.dumps(row['raw'],ensure_ascii=False),int(present),kind,int(verified),evidence,
        mapping,target,row['updated_at'],'ka10099'))


def aggregate_turnover(observations,assignments):
    """Only one representative industry per stock; theme totals are not additive."""
    amounts={}
    for row in observations:
        stock_code=master.code(row['stock_code'])
        try: value=Decimal(str(row['turnover_won']))
        except Exception: raise CatalogError('invalid_turnover') from None
        if not value.is_finite() or value<0 or stock_code in amounts: raise CatalogError('duplicate_or_invalid_turnover')
        amounts[stock_code]=value
    totals={};excluded={};used=set()
    for stock_code,value in amounts.items():
        primary={r['sector_id'] for r in assignments if r['stock_code']==stock_code and r.get('kind')=='industry'
                 and r.get('is_primary') is True and r.get('verified') is True}
        if len(primary)!=1:
            excluded[stock_code]='unclassified_or_conflicting_primary';continue
        sector=next(iter(primary));totals[sector]=totals.get(sector,Decimal(0))+value;used.add(stock_code)
    return dict(sector_totals={k:format(v,'f') for k,v in totals.items()},
        unique_turnover_won=format(sum((amounts[c] for c in used),Decimal(0)),'f'),excluded=excluded,
        theme_totals_additive=False)


def _run_directory(name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',name): raise CatalogError('invalid_run_name')
    if RUN_ROOT.resolve()!=RUN_ROOT.absolute(): raise CatalogError('redirected_run_root')
    RUN_ROOT.mkdir(parents=True,exist_ok=True)
    path=RUN_ROOT/name;path.mkdir()
    return path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('plan','fixture','collect'):
        p=sub.add_parser(command)
        p.add_argument('--db',type=Path,required=True);p.add_argument('--environment',choices=('mock','real'),required=True)
        p.add_argument('--rules',type=Path,default=DEFAULT_RULES)
        p.add_argument('--max-pages',type=int,default=100);p.add_argument('--seconds',type=float,default=300)
        p.add_argument('--minimum-per-market',type=int,default=1 if command=='fixture' else 100)
        p.add_argument('--maximum-drop-fraction',type=float,default=0.2)
        if command!='plan': p.add_argument('--name',required=True)
        if command=='fixture': p.add_argument('--input',type=Path,required=True)
        if command=='collect':
            p.add_argument('--execute-approved',action='store_true');p.add_argument('--quota-reviewed',action='store_true')
            p.add_argument('--token-env',default='STOCK1_KIWOOM_ACCESS_TOKEN')
    args=parser.parse_args(argv)
    audit=None;run=None;transport=None;resources=ExitStack()
    try:
        limits=Limits(max_pages=args.max_pages,seconds=args.seconds,minimum_per_market=args.minimum_per_market,
                      maximum_drop_fraction=args.maximum_drop_fraction)
        path=master.writable_path(args.db).resolve(strict=True)
        synthetic=args.command=='fixture'
        with master.read_master(path,synthetic=synthetic): pass
        rules=rules_document(json.loads(args.rules.read_text(encoding='utf-8-sig')))
        if args.command=='plan':
            print(json.dumps(dict(status='plan_only',api_id=API_ID,path=API_PATH,markets=MARKETS,
                environment=args.environment,maximum_query_attempts=limits.max_pages*2,request_spacing_seconds=limits.spacing_seconds,
                automatic_retries=0,oauth_calls=0,api_calls=0,publication='both_markets_complete_then_one_transaction')))
            return 0
        if args.command=='collect':
            if not args.execute_approved or not args.quota_reviewed: raise CatalogError('approval_and_quota_review_required')
            from .stock_catalog_live import after_close
            after_close(datetime.now(timezone.utc))
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',args.token_env): raise CatalogError('invalid_token_variable_name')
        run=_run_directory(args.name)
        audit=dict(status='collecting',success=False,database_updated=False,started_at=now(),environment=args.environment,
                   synthetic=synthetic,api_id=API_ID,source_db=str(path),automatic_retries=0)
        (run/'manifest.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
        if synthetic:
            fixture=json.loads(args.input.read_text(encoding='utf-8-sig'))
            if fixture.get('environment')!=args.environment: raise CatalogError('fixture_environment_mismatch')
            transport=FixtureTransport(fixture)
            virtual=[0.0]
            def clock(): return virtual[0]
            def sleep(seconds): virtual[0]+=seconds
            dataset=collect(transport,environment=args.environment,synthetic=True,limits=limits,clock=clock,sleep=sleep)
        else:
            import os
            from .api_resource import api_resource
            from .stock_catalog_live import ListTransport
            resources.enter_context(api_resource(path,args.environment))
            transport=ListTransport(os.environ.get(args.token_env,''),environment=args.environment,approved=True)
            dataset=collect(transport,environment=args.environment,synthetic=False,limits=limits)
        # Disk errors in staging abort BEFORE any master writes.
        (run/'dataset.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf-8')
        report=publish(path,dataset,rules=rules,limits=limits)
        audit.update(database_updated=True,run_id=report['run_id'])
        (run/'changes.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        audit.update(status='success',success=True,collected_counts=report['collected_counts'])
    except (ValueError,KeyError,TypeError,OSError,sqlite3.Error,KeyboardInterrupt) as error:
        reason=error.reason if isinstance(error,CatalogError) else 'local_scope_schema_or_io_failed'
        audit=audit or dict(success=False,database_updated=False,started_at=now())
        audit.update(status='published_report_error' if audit['database_updated'] else 'failed',reason=reason)
        if isinstance(error,KeyboardInterrupt): audit.update(status='cancelled',reason='user_cancelled')
    finally:
        if transport is not None and hasattr(transport,'close'): transport.close()
        resources.close()
        if audit is not None:
            audit.update(finished_at=now(),query_attempts=getattr(transport,'calls',0),
                         actual_api_calls=0 if audit.get('synthetic') else getattr(transport,'calls',0))
            if run:
                try: (run/'manifest.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
                except OSError: audit.update(status='audit_write_failed',success=False)
            print(json.dumps(audit,ensure_ascii=True))
    return 0 if audit and audit['success'] else 3 if audit and audit['database_updated'] else 130 if audit and audit['status']=='cancelled' else 2


if __name__=='__main__':
    raise SystemExit(main())
