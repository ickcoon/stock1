"""All master/cache/migration observations are synthetic; no operational source."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import master_data as m, market_cap as mc

DAY='2026-10-29'
EARLY=DAY+'T08:00:00+09:00'
NOW=DAY+'T09:50:45+09:00'


class MasterDataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.patch=patch.multiple(m,PROJECT=self.root,MASTER_ROOT=self.root/'master')
        self.patch.start();self.addCleanup(self.patch.stop)
        self.path=m.create_master('synthetic',synthetic=True,at=EARLY)
        with m.MasterStore(self.path) as store:
            store.put_stock('005930','합성기업','KOSPI','listed',source='synthetic_list',updated_at=EARLY,registered_at=EARLY,verified=True)

    def tag(self,store,sector='industry_01',**kwargs):
        args=dict(source='synthetic_review',evidence='synthetic_classification',updated_at=EARLY,
                  registered_at=EARLY,valid_from=EARLY,status='verified')
        args.update(kwargs);store.put_tag('005930',sector,**args)

    def add_cap(self,store,mac='3001',*,environment='mock',received=DAY+'T09:20:01+09:00',verified=True,observed=None):
        body=dict(return_code=0,stk_cd='005930',stk_nm='합성기업',mac=mac,flo_stk='001000',cur_prc='-030000')
        start=received
        parsed=mc.parse_response(body,requested_code='005930',environment=environment,
            request_started_at=start,response_received_at=received)
        proof=dict(for_record=parsed['fingerprint'],stock_code='005930',environment=environment,
            verified=True,unit='hundred_million_KRW',unit_runtime_verified=True,market_cap_basis_verified=True,
            observed_at=observed or received,trade_date=received[:10],evidence='SYNTHETIC unit review') if verified else None
        return store.put_capital(body,requested_code='005930',environment=environment,request_started_at=start,
            response_received_at=received,registered_at=received,source='synthetic_ka10001',proof=proof)

    def query(self):
        return m.read_master(self.path,synthetic=True)

    def test_schema_taxonomy_no_automatic_universe_and_writes_confined(self):
        with self.query() as (q,_):
            self.assertEqual(q.db.execute('SELECT count(*) FROM sector_master').fetchone()[0],16)
            self.assertEqual(q.db.execute('SELECT count(*) FROM stock_master').fetchone()[0],1)
        with self.assertRaises(ValueError):m.MasterStore(self.root/'operating.db')
        with self.assertRaises(FileExistsError):m.create_master('synthetic',synthetic=True)

    def test_multiple_industry_and_theme_tags_preserved_primary_resolves(self):
        with m.MasterStore(self.path) as store:
            store.put_sector('theme_ai','합성 AI',kind='theme',source='synthetic',updated_at=EARLY)
            self.tag(store,is_primary=True);self.tag(store,'industry_03');self.tag(store,'theme_ai')
        with self.query() as (q,_):
            result=q.classification('005930',at=NOW)
            self.assertEqual(result['state'],'classified');self.assertEqual(len(result['tags']),3)
            self.assertEqual(result['primary']['sector_name'],'반도체')

    def test_unclassified_proposed_and_conflicting_primary_are_held(self):
        with self.query() as (q,_):self.assertEqual(q.classification('005930',at=NOW)['state'],'unclassified')
        with m.MasterStore(self.path) as store:self.tag(store,status='proposed')
        with self.query() as (q,_):self.assertEqual(q.classification('005930',at=NOW)['state'],'unclassified')
        with m.MasterStore(self.path) as store:
            self.tag(store,is_primary=True);self.tag(store,'industry_03',is_primary=True)
        with self.query() as (q,_):self.assertEqual(q.classification('005930',at=NOW)['state'],'conflict')

    def test_duplicate_tag_idempotent_foreign_key_and_temporal_validation(self):
        with m.MasterStore(self.path) as store:
            self.tag(store);self.tag(store)
            self.assertEqual(store.db.execute('SELECT count(*) FROM stock_sector').fetchone()[0],1)
            with self.assertRaises(sqlite3.IntegrityError):self.tag(store,'missing')
            with self.assertRaises(ValueError):self.tag(store,valid_to='2026-10-28T00:00:00+09:00')

    def test_later_name_delisting_and_cache_invalidation_do_not_rewrite_history(self):
        with m.MasterStore(self.path) as store:
            self.add_cap(store)
            store.put_stock('005930','변경이름','KOSPI','listed',source='synthetic_revision',
                updated_at=DAY+'T09:30:00+09:00',registered_at=DAY+'T09:30:00+09:00',verified=True)
        with self.query() as (q,_):
            self.assertEqual(q.stock('005930',at=DAY+'T09:25:00+09:00')['stock_name'],'합성기업')
            self.assertEqual(q.stock('005930',at=NOW)['stock_name'],'변경이름')
            self.assertEqual(q.capital('005930',environment='mock',at=NOW)['state'],'stock_changed')
        with m.MasterStore(self.path) as store:
            store.put_stock('005930','변경이름','KOSPI','delisted',source='synthetic_delisting',
                updated_at=DAY+'T09:40:00+09:00',registered_at=DAY+'T09:40:00+09:00',verified=True)
        with self.query() as (q,_):self.assertEqual(q.capital('005930',environment='mock',at=NOW)['state'],'inactive_or_unverified')

    def test_alphanumeric_codes_duplicate_import_atomicity_and_market_validation(self):
        item=dict(stock_code='0035S0',stock_name='합성신규',market='KOSDAQ',listing_status='listed',
                  source='synthetic_list',updated_at=EARLY,registered_at=EARLY,verified=True)
        document=dict(schema_version='stock-master-import-v1',synthetic=True,records=[item])
        self.assertEqual(m.import_stocks(self.path,document)['imported'],1)
        document['records']=[item,item]
        with self.assertRaisesRegex(ValueError,'duplicate'):m.import_stocks(self.path,document)
        with m.MasterStore(self.path) as store:
            with self.assertRaises(ValueError):store.put_stock('5930','이름','KOSPI','listed',source='s',updated_at=EARLY,registered_at=EARLY)
        with self.query() as (q,_):self.assertEqual(q.stock('0035S0',at=NOW)['state'],'active')

    def test_cap_raw_preserved_conversion_verified_only_and_strict_threshold(self):
        for index,(raw,expected) in enumerate((('0003000',False),('+3000.01',True),('2999.99',False))):
            received=DAY+f'T09:2{index}:01+09:00'
            with m.MasterStore(self.path) as store:parsed=self.add_cap(store,raw,received=received)
            with self.query() as (q,_):
                row=q.capital('005930',environment='mock',at=NOW)
                self.assertEqual(row['state'],'hit');self.assertIs(row['above_3000_eok'],expected)
                self.assertEqual(row['record']['raw_mac'],raw)
            self.assertIsNone(parsed['listed_shares'])
            self.assertEqual(parsed['raw']['cur_prc'],'-030000')

    def test_unverified_invalid_units_negative_and_zero_cap_are_not_approved(self):
        with m.MasterStore(self.path) as store:self.add_cap(store,verified=False)
        with self.query() as (q,_):
            self.assertEqual(q.capital('005930',environment='mock',at=NOW)['state'],'unverified')
            self.assertIsNone(q.db.execute('SELECT normalized_won FROM market_cap_cache').fetchone()[0])
            self.assertIsNone(q.db.execute('SELECT trade_date FROM market_cap_cache').fetchone()[0])
        with m.MasterStore(self.path) as store:self.add_cap(store,'-3001',received=DAY+'T09:21:01+09:00')
        with self.query() as (q,_):self.assertEqual(q.capital('005930',environment='mock',at=NOW)['state'],'unverified')
        with m.MasterStore(self.path) as store:self.add_cap(store,'0',received=DAY+'T09:22:01+09:00')
        with self.query() as (q,_):self.assertFalse(q.capital('005930',environment='mock',at=NOW)['above_3000_eok'])

    def test_environment_date_ttl_future_and_stale_observation_are_not_joined(self):
        with m.MasterStore(self.path) as store:self.add_cap(store)
        with self.query() as (q,_):
            self.assertEqual(q.capital('005930',environment='real',at=NOW)['state'],'missing')
            self.assertEqual(q.capital('005930',environment='mock',at=NOW,ttl_seconds=30)['state'],'expired')
            self.assertEqual(q.capital('005930',environment='mock',at='2026-10-30T09:50:00+09:00')['state'],'expired')
            self.assertEqual(q.capital('005930',environment='mock',at=EARLY)['state'],'missing')
        with m.MasterStore(self.path) as store:self.add_cap(store,received=DAY+'T09:49:00+09:00',observed=EARLY)
        with self.query() as (q,_):self.assertEqual(q.capital('005930',environment='mock',at=NOW)['state'],'expired')

    def test_same_receipt_conflicting_cap_never_arbitrarily_selected(self):
        with m.MasterStore(self.path) as store:
            self.add_cap(store,'3001');self.add_cap(store,'9999')
        with self.query() as (q,_):self.assertEqual(q.capital('005930',environment='mock',at=NOW)['state'],'conflict')

    def test_refresh_plan_only_missing_or_expired_no_transport(self):
        with self.query() as (q,_):
            plan=q.refresh_plan(['005930','005930'],environment='mock',at=NOW,limit=1)
            self.assertEqual(plan['requests'],[{'stk_cd':'005930'}]);self.assertEqual(plan['actual_api_calls'],0)
        with m.MasterStore(self.path) as store:self.add_cap(store)
        with self.query() as (q,_):self.assertEqual(q.refresh_plan(['005930'],environment='mock',at=NOW)['requests'],[])

    def test_review_binding_and_fixture_scope_not_inferred(self):
        with self.assertRaises(ValueError):
            with m.read_master(self.path,synthetic=False):pass
        with m.MasterStore(self.path) as store:
            parsed=self.add_cap(store,verified=False)
            body=parsed['raw']|{'return_code':0}
            store.put_capital(body,requested_code='005930',environment='mock',request_started_at=DAY+'T09:21:00+09:00',
                response_received_at=DAY+'T09:21:00+09:00',registered_at=NOW,source='synthetic',
                proof={'for_record':'wrong','verified':True})
        with self.query() as (q,_):self.assertEqual(q.capital('005930',environment='mock',at=NOW)['state'],'unverified')

    def test_legacy_migration_preserves_source_and_hash_without_approval(self):
        legacy=self.root/'legacy.db'
        with closing(sqlite3.connect(legacy)) as db,db:
            db.executescript('CREATE TABLE stocks(stock_code,stock_name,updated_at);CREATE TABLE sectors(sector_id,sector_name);CREATE TABLE stock_sectors(stock_code,sector_id,source,updated_at);')
            db.execute('INSERT INTO stocks VALUES(?,?,?)',('0035S0','합성이전종목',EARLY))
            db.execute('INSERT INTO stocks VALUES(?,?,?)',('005930','다른이전이름',EARLY))
            db.executemany('INSERT INTO sectors VALUES(?,?)',[(1,'반도체'),(2,'세부합성테마')])
            db.executemany('INSERT INTO stock_sectors VALUES(?,?,?,?)',[('0035S0',i,'legacy_manual',EARLY) for i in (1,2)])
        before=hashlib.sha256(legacy.read_bytes()).hexdigest()
        report=m.migrate_legacy(legacy,self.path,registered_at=NOW,synthetic=True)
        self.assertEqual(report['tags'],2);self.assertEqual(hashlib.sha256(legacy.read_bytes()).hexdigest(),before)
        self.assertEqual(report['stock_conflicts'][0]['stock_code'],'005930')
        self.assertEqual(m.migrate_legacy(legacy,self.path,registered_at=NOW,synthetic=True)['state'],'already_imported')
        with self.query() as (q,_):
            rows=list(q.db.execute('SELECT source,status,source_sha256 FROM stock_sector'))
            self.assertEqual(len(rows),2);self.assertTrue(all(r['source']=='legacy_manual' and r['status']=='proposed' and r['source_sha256']==before for r in rows))
            self.assertEqual(q.stock('0035S0',at=NOW)['market'],'UNKNOWN')

    def test_failed_import_rolls_back_all_records(self):
        base=dict(stock_code='000001',stock_name='합성',market='KOSDAQ',listing_status='listed',
            source='synthetic',updated_at=EARLY,registered_at=EARLY,verified=True)
        bad=base|{'stock_code':'000002','market':'invalid'}
        with self.assertRaises(ValueError):m.import_stocks(self.path,dict(schema_version='stock-master-import-v1',synthetic=True,records=[base,bad]))
        with self.query() as (q,_):self.assertEqual(q.stock('000001',at=NOW)['state'],'unknown')

    def test_cache_conversion_and_unit_are_rechecked_on_read(self):
        with m.MasterStore(self.path) as store:
            self.add_cap(store)
            store.db.execute("UPDATE market_cap_cache SET normalized_won='999999999999999'")
        with self.query() as (q,_):self.assertEqual(q.capital('005930',environment='mock',at=NOW)['state'],'unverified')

    def test_expired_latest_tag_does_not_resurrect_older_verified_tag(self):
        with m.MasterStore(self.path) as store:
            self.tag(store)
            self.tag(store,registered_at='2026-10-29T09:40:00+09:00',valid_to='2026-10-29T09:30:00+09:00')
        with self.query() as (q,_):self.assertEqual(q.classification('005930',at=NOW)['state'],'unclassified')
