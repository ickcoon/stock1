"""ka10099 fixture-only pagination, atomic master changes and no live IO."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime,timedelta
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from kiwoom_collector import stock_catalog as c, master_data as m
from kiwoom_collector.stock_catalog_live import ListTransport,after_close

FIXTURE=Path(__file__).parent/'fixtures'/'ka10099_synthetic.json'
RULES=Path(__file__).parent.parent/'config'/'arvis_sector_mapping.json'
LIMITS=c.Limits(minimum_per_market=1,maximum_drop_fraction=0.5)


class Clock:
    def __init__(self):self.value=0.0
    def __call__(self):return self.value
    def sleep(self,seconds):self.value+=seconds


class StockCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.patch=patch.multiple(m,MASTER_ROOT=self.root/'master',PROJECT=self.root)
        self.patch.start();self.addCleanup(self.patch.stop)
        self.run_patch=patch.object(c,'RUN_ROOT',self.root/'runs');self.run_patch.start();self.addCleanup(self.run_patch.stop)
        self.now_patch=patch.object(c,'now',return_value='2026-10-30T16:01:00+09:00')
        self.now_patch.start();self.addCleanup(self.now_patch.stop)
        self.path=m.create_master('fixture',synthetic=True,at='2026-10-29T08:00:00+09:00')
        self.fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.rules=json.loads(RULES.read_text(encoding='utf-8'))
        for rule in self.rules['rules']:rule['reviewed']=True
        self.rules['security_reviews']={code:dict(type='SPAC' if code=='000004' else 'common_stock',verified=True,
            evidence='SYNTHETIC type review',reviewed_at='2026-10-29T15:00:00+09:00') for code in ('000001','000002','000003','000004','0035S0','000005')}

    def dataset(self,document=None,limits=LIMITS):
        clock=Clock();transport=c.FixtureTransport(document or self.fixture)
        result=c.collect(transport,environment='mock',synthetic=True,limits=limits,clock=clock,sleep=clock.sleep)
        return result

    def publish(self,dataset=None,at='2026-10-30T16:01:00+09:00',rules=None):
        return c.publish(self.path,dataset or self.dataset(),rules=rules or self.rules,limits=LIMITS,applied_at=at)

    def query(self):return m.read_master(self.path,synthetic=True)

    def shifted(self):
        doc=deepcopy(self.fixture)
        for page in doc['pages']:
            for key in ('started_at','received_at'):
                page['response'][key]=(m.instant(page['response'][key])+timedelta(days=1)).isoformat()
        return doc

    def test_complete_two_market_collection_and_original_metadata(self):
        data=self.dataset();self.assertEqual(len(data['rows']),5);self.assertEqual(len(data['pages']),3)
        report=self.publish(data)
        self.assertEqual(report['collected_counts'],{'KOSPI':3,'KOSDAQ':2})
        with self.query() as (q,_):
            self.assertEqual(q.db.execute('SELECT count(*) FROM catalog_taxonomy').fetchone()[0],15)
            row=q.catalog_info('000001',at='2026-10-30T16:02:00+09:00')
            self.assertEqual(row['raw_industry'],'반도체');self.assertEqual(row['listing_date'],'2020-01-02')
            self.assertEqual(row['updated_at'],'2026-10-30T16:00:01+09:00')
            self.assertEqual(q.stock('000001',at='2026-10-30T16:02:00+09:00')['state'],'active')
            self.assertEqual(q.classification('000001',at='2026-10-30T16:02:00+09:00')['primary']['sector_name'],'반도체')

    def test_continuation_headers_and_global_pacing(self):
        clock=Clock();source=c.FixtureTransport(self.fixture);starts=[]
        def transport(**kwargs):starts.append(clock());return source(**kwargs)
        c.collect(transport,environment='mock',synthetic=True,limits=LIMITS,clock=clock,sleep=clock.sleep)
        self.assertEqual(starts,[0.0,1.2,2.4]);self.assertEqual(source.index,3)

    def test_duplicate_codes_across_pages_or_markets_rejected(self):
        for index in (1,2):
            doc=deepcopy(self.fixture);doc['pages'][index]['response']['body']['list'][0]['code']='000001'
            with self.assertRaisesRegex(c.CatalogError,'duplicate_stock_code'):self.dataset(doc)

    def test_api_http_missing_list_and_market_errors_rejected(self):
        for mutation,reason in ((lambda r:r.update(http_status=429),'http_failed'),
                (lambda r:r['body'].update(return_code=1700),'api_failed'),
                (lambda r:r['body'].pop('list'),'missing_or_invalid_list'),
                (lambda r:r['body']['list'][0].update(marketCode='10'),'response_market_mismatch')):
            doc=deepcopy(self.fixture);mutation(doc['pages'][0]['response'])
            with self.assertRaisesRegex(c.CatalogError,reason):self.dataset(doc)

    def test_continuation_empty_key_loop_and_page_limit_are_not_success(self):
        doc=deepcopy(self.fixture);doc['pages'][0]['response']['headers']['next-key']=''
        with self.assertRaisesRegex(c.CatalogError,'incomplete_continuation'):self.dataset(doc)
        doc=deepcopy(self.fixture);doc['pages'][1]['response']['headers']={'cont-yn':'Y','next-key':'fixture-next-1'}
        with self.assertRaisesRegex(c.CatalogError,'continuation_loop'):self.dataset(doc)
        with self.assertRaisesRegex(c.CatalogError,'page_limit_incomplete'):self.dataset(limits=c.Limits(max_pages=1,minimum_per_market=1))

    def test_minimum_empty_market_deadline_and_limits(self):
        with self.assertRaisesRegex(c.CatalogError,'market_count_below_minimum'):
            self.dataset(limits=c.Limits(minimum_per_market=3))
        clock=Clock();source=c.FixtureTransport(self.fixture)
        def slow(**kwargs):clock.value+=2;return source(**kwargs)
        with self.assertRaisesRegex(c.CatalogError,'collection_deadline'):
            c.collect(slow,environment='mock',synthetic=True,limits=c.Limits(seconds=1,minimum_per_market=1),clock=clock,sleep=clock.sleep)
        for kwargs in ({'max_pages':0},{'seconds':float('nan')},{'spacing_seconds':0.2}):
            with self.assertRaises(c.CatalogError):c.Limits(**kwargs)

    def test_partial_failure_cli_preserves_existing_master_byte_for_byte(self):
        self.publish();before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc=self.shifted();doc['pages'][2]['response']['body']['return_code']=999
        fixture=self.root/'bad.json';fixture.write_text(json.dumps(doc),encoding='utf-8')
        with redirect_stdout(io.StringIO()) as output:
            exit_code=c.main(['fixture','--db',str(self.path),'--environment','mock','--input',str(fixture),
                '--rules',str(RULES),'--name','bad'])
        self.assertEqual(exit_code,2);self.assertNotIn('authorization',output.getvalue())
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        manifest=json.loads((self.root/'runs/bad/manifest.json').read_text())
        self.assertFalse(manifest['database_updated']);self.assertEqual(manifest['query_attempts'],3)

    def test_late_publication_failure_rolls_back_schema_stocks_and_tags(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest();real=c._insert_item;counter=[0]
        def fail(*args,**kwargs):
            counter[0]+=1
            if counter[0]==3:raise c.CatalogError('injected_failure')
            return real(*args,**kwargs)
        with patch.object(c,'_insert_item',side_effect=fail):
            with self.assertRaisesRegex(c.CatalogError,'injected_failure'):self.publish()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        with self.query() as (q,_):
            self.assertEqual(q.db.execute('SELECT count(*) FROM stock_master').fetchone()[0],0)
            self.assertIsNone(q.db.execute("SELECT name FROM sqlite_master WHERE name='catalog_runs'").fetchone())

    def test_add_name_change_transfer_missing_and_reappearance_history(self):
        self.publish();doc=self.shifted()
        doc['pages'][0]['response']['body']['list'][0]['name']='합성변경명'
        moved=doc['pages'][1]['response']['body']['list'].pop();moved.update(marketCode='10',marketName='코스닥')
        new=deepcopy(doc['pages'][0]['response']['body']['list'][0]);new.update(code='000005',name='합성신규')
        doc['pages'][0]['response']['body']['list'].append(new)
        doc['pages'][2]['response']['body']['list'].pop();doc['pages'][2]['response']['body']['list'].append(moved)
        report=self.publish(self.dataset(doc),at='2026-10-31T16:01:00+09:00')
        self.assertEqual(report['changes']['added'],1);self.assertEqual(report['changes']['name_changed'],1)
        self.assertEqual(report['changes']['market_transferred'],1);self.assertEqual(report['missing_possible'],['000004'])
        with self.query() as (q,_):
            self.assertEqual(q.stock('000001',at='2026-10-30T16:02:00+09:00')['stock_name'],'합성반도체')
            self.assertEqual(q.stock('000001',at='2026-10-31T16:02:00+09:00')['stock_name'],'합성변경명')
            self.assertEqual(q.stock('000004',at='2026-10-31T16:02:00+09:00')['state'],'catalog_missing_review')
            self.assertEqual(q.db.execute("SELECT listing_status FROM stock_master WHERE stock_code='000004'").fetchone()[0],'listed')
        again=self.shifted()
        for page in again['pages']:
            for key in ('started_at','received_at'):page['response'][key]=(m.instant(page['response'][key])+timedelta(days=1)).isoformat()
        report=self.publish(self.dataset(again),at='2026-11-01T16:01:00+09:00')
        self.assertGreaterEqual(report['changes']['reappeared'],1)

    def test_sudden_drop_preserves_previous_master(self):
        self.publish();before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc=self.shifted();doc['pages'][2]['response']['body']['list'].pop()
        with self.assertRaisesRegex(c.CatalogError,'unexpected_market_count_drop'):
            c.publish(self.path,self.dataset(doc),rules=self.rules,limits=c.Limits(minimum_per_market=1),applied_at='2026-10-31T16:01:00+09:00')
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)

    def test_manual_industry_and_themes_preserved_and_conflict_held(self):
        with m.MasterStore(self.path) as store:
            store.put_stock('000001','수작업기업','KOSPI','listed',source='manual',updated_at='2026-10-29T08:00:00+09:00',registered_at='2026-10-29T08:00:00+09:00',verified=True)
            store.put_tag('000001','industry_02',source='manual',evidence='manual_auto_review',updated_at='2026-10-29T08:00:00+09:00',registered_at='2026-10-29T08:00:00+09:00',valid_from='2026-10-29T08:00:00+09:00',status='verified',is_primary=True)
            store.put_sector('manual_theme','수작업테마',kind='theme',source='manual',updated_at='2026-10-29T08:00:00+09:00')
            store.put_tag('000001','manual_theme',source='manual',evidence='manual_theme_review',updated_at='2026-10-29T08:00:00+09:00',registered_at='2026-10-29T08:00:00+09:00',valid_from='2026-10-29T08:00:00+09:00',status='verified')
            before=[tuple(r) for r in store.db.execute("SELECT * FROM stock_sector WHERE source='manual' ORDER BY id")]
        self.publish()
        with self.query() as (q,_):
            self.assertEqual([tuple(r) for r in q.db.execute("SELECT * FROM stock_sector WHERE source='manual' ORDER BY id")],before)
            self.assertEqual(q.catalog_info('000001',at='2026-10-30T16:02:00+09:00')['mapping_state'],'manual_review_needed')
            self.assertEqual(q.classification('000001',at='2026-10-30T16:02:00+09:00')['state'],'conflict')

    def test_no_name_guess_unreviewed_mapping_and_conflicting_rules(self):
        self.assertEqual(c.map_industry('',self.rules)['state'],'unclassified')
        raw=self.fixture['pages'][1]['response']['body']['list'][0]|{'name':'로봇 원전 반도체 AI 테마기업'}
        parsed=c.parse_row(raw,'0');self.assertEqual(c.map_industry(parsed['raw_industry'],self.rules)['state'],'unclassified')
        unreviewed=json.loads(RULES.read_text(encoding='utf-8'))
        self.assertEqual(c.map_industry('반도체',unreviewed)['state'],'review_needed')
        self.rules['rules'].append({'raw_industry':'반도체','sector':'로봇','reviewed':True})
        report=self.publish()
        self.assertEqual(report['mapping_counts']['conflict'],1)

    def test_unknown_spac_hint_and_reviewed_etf_are_separate_from_common_stock(self):
        rules=deepcopy(self.rules);rules['security_reviews']={}
        self.publish(rules=rules)
        with self.query() as (q,_):
            spac=q.stock('000004',at='2026-10-30T16:02:00+09:00')
            self.assertEqual(spac['security_type'],'SPAC');self.assertFalse(spac['security_type_verified'])
            self.assertEqual(q.stock('000001',at='2026-10-30T16:02:00+09:00')['state'],'catalog_security_review')
        rules=deepcopy(self.rules);rules['security_reviews']['000002']['type']='ETF'
        self.publish(self.dataset(self.shifted()),rules=rules,at='2026-10-31T16:01:00+09:00')
        with self.query() as (q,_):self.assertEqual(q.stock('000002',at='2026-10-31T16:02:00+09:00')['state'],'catalog_security_excluded')

    def test_environment_scope_tampering_replay_and_stale_dataset_rejected(self):
        data=self.dataset();tampered=deepcopy(data);tampered['rows'][0]['stock_name']='tampered'
        with self.assertRaisesRegex(c.CatalogError,'dataset_integrity_failed'):self.publish(tampered)
        self.publish(data)
        with self.assertRaises(c.CatalogError):self.publish(data,at='2026-10-30T16:02:00+09:00')
        other=self.dataset(self.shifted());other['environment']='real';other['fingerprint']=m.digest({k:v for k,v in other.items() if k!='fingerprint'})
        with self.assertRaisesRegex(c.CatalogError,'catalog_environment_mismatch'):self.publish(other,at='2026-10-31T16:01:00+09:00')

    def test_future_listing_and_bad_listing_date(self):
        doc=deepcopy(self.fixture);doc['pages'][0]['response']['body']['list'][0]['regDay']='20270101'
        self.publish(self.dataset(doc))
        with self.query() as (q,_):self.assertEqual(q.stock('000001',at='2026-10-30T16:02:00+09:00')['listing_status'],'unknown')
        doc['pages'][0]['response']['body']['list'][0]['regDay']='20260230'
        with self.assertRaisesRegex(c.CatalogError,'invalid_listing_date'):self.dataset(doc)

    def test_cli_fixture_success_and_same_name_not_overwritten(self):
        with redirect_stdout(io.StringIO()):
            result=c.main(['fixture','--db',str(self.path),'--environment','mock','--input',str(FIXTURE),'--rules',str(RULES),'--name','success'])
        self.assertEqual(result,0)
        manifest=json.loads((self.root/'runs/success/manifest.json').read_text(encoding='utf-8'))
        self.assertTrue(manifest['success']);self.assertEqual(manifest['actual_api_calls'],0)
        with redirect_stdout(io.StringIO()):
            result=c.main(['fixture','--db',str(self.path),'--environment','mock','--input',str(FIXTURE),'--rules',str(RULES),'--name','success'])
        self.assertEqual(result,2)
        self.assertEqual(json.loads((self.root/'runs/success/manifest.json').read_text(encoding='utf-8')),manifest)

    def test_plan_and_unapproved_live_do_not_load_credentials_or_transport(self):
        real=m.create_master('empty_real',synthetic=False,at='2026-10-29T08:00:00+09:00')
        with patch('kiwoom_collector.stock_catalog_live.ListTransport') as transport,redirect_stdout(io.StringIO()):
            self.assertEqual(c.main(['plan','--db',str(real),'--environment','real','--rules',str(RULES)]),0)
            self.assertEqual(c.main(['collect','--db',str(real),'--environment','real','--rules',str(RULES),'--name','unapproved']),2)
            transport.assert_not_called()

    def test_allowlist_does_not_stage_auth_or_server_messages(self):
        doc=deepcopy(self.fixture);response=doc['pages'][0]['response']
        response['body'].update(return_msg='PRIVATE_SERVER_MESSAGE',authorization='DO_NOT_EXPORT_SECRET')
        response['body']['list'][0]['token']='DO_NOT_EXPORT_SECRET'
        output=json.dumps(self.dataset(doc))
        self.assertNotIn('DO_NOT_EXPORT_SECRET',output);self.assertNotIn('PRIVATE_SERVER_MESSAGE',output)

    def test_representative_aggregation_does_not_double_count_theme_membership(self):
        observations=[{'stock_code':'000001','turnover_won':'100'},{'stock_code':'000002','turnover_won':'200'}]
        tags=[dict(stock_code='000001',sector_id='semi',kind='industry',is_primary=True,verified=True),
            dict(stock_code='000001',sector_id='robot',kind='theme',is_primary=True,verified=True),
            dict(stock_code='000002',sector_id='semi',kind='industry',is_primary=True,verified=True)]
        report=c.aggregate_turnover(observations,tags)
        self.assertEqual(report['sector_totals'],{'semi':'300'});self.assertEqual(report['unique_turnover_won'],'300')
        tags.append(dict(stock_code='000001',sector_id='power',kind='industry',is_primary=True,verified=True))
        self.assertEqual(c.aggregate_turnover(observations,tags)['unique_turnover_won'],'200')
        with self.assertRaises(c.CatalogError):c.aggregate_turnover(observations+observations,tags)

    def test_staging_io_failure_does_not_publish_but_postcommit_io_is_reported_truthfully(self):
        real_write=Path.write_text
        def fail_staging(path,*args,**kwargs):
            if path.name=='dataset.json':raise OSError('synthetic disk failure')
            return real_write(path,*args,**kwargs)
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        args=['fixture','--db',str(self.path),'--environment','mock','--input',str(FIXTURE),'--rules',str(RULES)]
        with patch.object(Path,'write_text',new=fail_staging),redirect_stdout(io.StringIO()):
            self.assertEqual(c.main(args+['--name','staging-error']),2)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        def fail_report(path,*args,**kwargs):
            if path.name=='changes.json':raise OSError('synthetic disk failure')
            return real_write(path,*args,**kwargs)
        with patch.object(Path,'write_text',new=fail_report),redirect_stdout(io.StringIO()):
            self.assertEqual(c.main(args+['--name','report-error']),3)
        audit=json.loads((self.root/'runs/report-error/manifest.json').read_text())
        self.assertTrue(audit['database_updated']);self.assertEqual(audit['status'],'published_report_error')

    def test_incomplete_rehashed_dataset_and_fixture_live_label_rejected(self):
        data=self.dataset();data['pages'].pop()
        data['fingerprint']=m.digest({k:v for k,v in data.items() if k!='fingerprint'})
        with self.assertRaisesRegex(c.CatalogError,'incomplete_dataset_pages'):self.publish(data)
        with self.assertRaisesRegex(c.CatalogError,'fixture_cannot_be_live_source'):
            c.collect(c.FixtureTransport(self.fixture),environment='mock',synthetic=False)

    def test_later_manual_review_is_effective_without_refetching_catalog(self):
        self.publish()
        with m.MasterStore(self.path) as store:
            store.put_tag('000001','industry_02',source='manual',evidence='synthetic_new_manual_review',
                updated_at='2026-10-30T16:02:00+09:00',registered_at='2026-10-30T16:02:00+09:00',
                valid_from='2026-10-30T16:02:00+09:00',is_primary=True,status='verified')
        with self.query() as (q,_):
            self.assertEqual(q.classification('000001',at='2026-10-30T16:01:30+09:00')['primary']['sector_name'],'반도체')
            self.assertEqual(q.classification('000001',at='2026-10-30T16:03:00+09:00')['primary']['sector_name'],'자동차')

    def test_type_review_cannot_override_conflicting_current_reported_hint(self):
        rules=deepcopy(self.rules);rules['security_reviews']['000004']['type']='common_stock'
        self.publish(rules=rules)
        with self.query() as (q,_):
            value=q.stock('000004',at='2026-10-30T16:02:00+09:00')
            self.assertEqual(value['state'],'catalog_security_review')
            self.assertEqual(value['security_type'],'SPAC')


class LiveBoundaryTests(unittest.TestCase):
    def test_injected_session_only_allowed_tr_and_no_retry_or_oauth(self):
        response=Mock(status_code=200,headers={'cont-yn':'N','next-key':''})
        response.iter_content.return_value=[b'{"return_code":0,"list":[]}']
        session=Mock();session.post.return_value=response
        transport=ListTransport('injected-private-value',environment='real',approved=True,session=session)
        envelope=transport(market='0',cont_yn='',next_key='',remaining_seconds=30)
        args,kwargs=session.post.call_args
        self.assertEqual(args[0],'https://api.kiwoom.com/api/dostk/stkinfo')
        self.assertEqual(kwargs['headers']['api-id'],'ka10099');self.assertEqual(kwargs['json'],{'mrkt_tp':'0'})
        self.assertTrue(kwargs['verify']);self.assertFalse(kwargs['allow_redirects'])
        with self.assertRaises(c.CatalogError):transport(market='8',cont_yn='',next_key='',remaining_seconds=30)
        self.assertEqual(session.post.call_count,1);response.close.assert_called_once();transport.close()

    def test_transport_failures_hide_token_and_do_not_retry(self):
        session=Mock();session.post.side_effect=RuntimeError('injected-private-token')
        transport=ListTransport('injected-private-token',environment='mock',approved=True,session=session)
        with self.assertRaises(c.CatalogError) as error:transport(market='10',cont_yn='',next_key='',remaining_seconds=5)
        self.assertNotIn('private-token',str(error.exception));self.assertEqual(session.post.call_count,1)

    def test_after_close_guard_and_approval_boundary(self):
        with self.assertRaises(c.CatalogError):after_close(datetime.fromisoformat('2026-10-30T15:00:00+09:00'))
        after_close(datetime.fromisoformat('2026-10-30T16:00:00+09:00'))
        with self.assertRaises(c.CatalogError):ListTransport('private',environment='real',approved=False,session=Mock())
