"""Recent classification priority extends the existing offline CSV workspace."""
from contextlib import closing
from datetime import datetime, timedelta
from html import unescape
import csv
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector.client import KST, Snapshot
from kiwoom_collector.storage import Store
from kiwoom_collector.sectors import SectorManager
from kiwoom_collector.conditions import migrate_conditions
from kiwoom_collector.external_tags import export_priority, external_command
from kiwoom_collector.workbench import workspace_data, export_workbench
from test_workbench import browser_path, browser_dom


NOW = datetime(2026, 10, 23, 12, tzinfo=KST)


class RecentWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder/'stock.db'
        with closing(Store(self.path)):
            pass

    def run_at(self, at, stocks, *, environment='mock', qry='1', test=False, state='success'):
        with closing(Store(self.path)) as store:
            run = store.begin(at, environment, qry, test_mode=test)
            if state == 'success':
                store.finish(run, Snapshot(at, [dict(bigd_rank=str(rank), stk_cd=code, stk_nm='종목 '+code)
                                                for code, rank in stocks]))
            elif state != 'running':
                store.fail(run, 'fixture')
        return run

    def data(self, environment='mock', now=NOW):
        return workspace_data(self.path, environment, now=now)

    def by_code(self, **options):
        return {s['stock_code']: s for s in self.data(**options)['stocks']}

    def test_recent_frequency_beats_old_lifetime_history(self):
        for i in range(5):
            self.run_at(NOW-timedelta(days=9, minutes=i), [('000001',1)])
        self.run_at(NOW, [('000002',15)])
        stocks = self.data()['stocks']
        self.assertEqual([s['stock_code'] for s in stocks], ['000002','000001'])
        self.assertEqual(stocks[1]['ranking_appearances'], 5)
        self.assertEqual(stocks[1]['recent_ranking_appearances'], 0)

    def test_count_then_top5_then_top10_without_weighted_score(self):
        for minute in (2,1):
            self.run_at(NOW-timedelta(minutes=minute), [('000001',11),('000002',6),('000003',1)])
        self.run_at(NOW, [('000004',20)])
        rows = self.data()['stocks']
        self.assertEqual([r['stock_code'] for r in rows], ['000003','000002','000001','000004'])
        self.assertEqual([(r['recent_ranking_appearances'],r['recent_top5_appearances'],r['recent_top10_appearances'])
                          for r in rows], [(2,2,2),(2,0,2),(2,0,0),(1,0,0)])

    def test_last_observation_uses_real_time_not_offset_string_order(self):
        old = self.run_at(NOW-timedelta(hours=2), [('000001',1)])
        new = self.run_at(NOW-timedelta(hours=1), [('000002',1)])
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE collections SET collected_at=? WHERE id=?', ('2026-10-23T02:00:00+00:00',new))
            db.commit()
        rows = self.data()['stocks']
        self.assertEqual(rows[0]['stock_code'], '000002')
        self.assertEqual(rows[0]['last_ranking_seen'], '2026-10-23T11:00:00+09:00')

    def test_equal_recent_fields_keep_legacy_priority_and_csv_format(self):
        self.run_at(NOW, [('000001',1),('000002',2)])
        before = self.path.read_bytes()
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            legacy = export_priority(db,self.folder/'priority.csv','mock')
        rows = self.data()['stocks']
        self.assertEqual([s['stock_code'] for s in rows], [s['stock_code'] for s in legacy['priorities']])
        self.assertEqual([s['legacy_priority'] for s in rows], [1,2])
        with (self.folder/'priority.csv').open(encoding='utf-8-sig',newline='') as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, ['stock_code','stock_name','sector_name'])
            self.assertEqual(len(list(reader)),2)
        self.assertEqual(before, self.path.read_bytes())

    def test_recent_window_inclusive_boundaries_and_no_future_counts(self):
        self.run_at(NOW-timedelta(days=7), [('000001',5)])
        self.run_at(NOW-timedelta(days=7,seconds=1), [('000002',5)])
        self.run_at(NOW+timedelta(seconds=1), [('000003',5)])
        rows = self.by_code()
        self.assertEqual(rows['000001']['recent_ranking_appearances'],1)
        self.assertEqual(rows['000002']['recent_ranking_appearances'],0)
        self.assertEqual(rows['000003']['recent_ranking_appearances'],0)
        self.assertFalse(rows['000003']['in_latest_top20'])
        self.assertIsNone(rows['000003']['last_ranking_date'])

    def test_mock_real_test_failed_and_running_separation(self):
        self.run_at(NOW, [('000001',1)])
        self.run_at(NOW, [('000002',1)], environment='real')
        self.run_at(NOW-timedelta(minutes=1), [('000003',1)], test=True)
        self.run_at(NOW-timedelta(minutes=2), [('000004',1)], state='error')
        self.run_at(NOW-timedelta(minutes=3), [('000005',1)], state='running')
        self.assertEqual(set(self.by_code()), {'000001'})
        self.assertEqual(set(self.by_code(environment='real')), {'000002'})

    def test_new_entry_compares_same_day_environment_query_and_operating_mode(self):
        old = self.run_at(NOW-timedelta(hours=1), [('000001',1)])
        self.run_at(NOW-timedelta(minutes=20), [('000002',1)], qry='2')
        self.run_at(NOW-timedelta(minutes=10), [('000002',1)], test=True)
        self.run_at(NOW-timedelta(minutes=5), [('000002',1)], environment='real')
        new = self.run_at(NOW, [('000001',1),('000002',2)])
        data = self.data();rows = self.by_code()
        self.assertEqual(data['latest_ranking']['id'],new)
        self.assertEqual(data['previous_ranking']['id'],old)
        self.assertTrue(rows['000002']['new_in_latest'])
        self.assertFalse(rows['000001']['new_in_latest'])

    def test_no_previous_same_day_never_claims_new_entry(self):
        self.run_at(NOW-timedelta(days=1), [('000001',1)])
        self.run_at(NOW, [('000002',1)])
        data = self.data()
        self.assertIsNone(data['previous_ranking'])
        self.assertIsNone(self.by_code()['000002']['new_in_latest'])
        self.assertTrue(self.by_code()['000002']['in_latest_top20'])

    def test_date_comparison_uses_kst(self):
        old = self.run_at(NOW-timedelta(hours=1), [('000001',1)])
        self.run_at(NOW, [('000001',1),('000002',2)])
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE collections SET collected_at=? WHERE id=?', ('2026-10-23T02:00:00+00:00',old))
            db.commit()
        self.assertTrue(self.by_code()['000002']['new_in_latest'])

    def test_tagged_and_multitag_stocks_are_excluded_without_mutation(self):
        self.run_at(NOW, [('000001',1),('000002',2)])
        with closing(Store(self.path)) as store:
            manager = SectorManager(store)
            manager.add('A');manager.add('B')
            manager.tag('000001','A','manual-a');manager.tag('000001','B','manual-b')
        before = self.path.read_bytes()
        data = self.data()
        self.assertEqual([s['stock_code'] for s in data['stocks']], ['000002'])
        self.assertEqual(data['latest_unclassified_count'],1)
        self.assertEqual(data['stocks'][0]['current_tags'],[])
        self.assertTrue(data['stocks'][0]['is_unclassified'])
        self.assertEqual(before,self.path.read_bytes())

    def test_unsupported_codes_are_preserved_readonly_never_added_to_csv_queue(self):
        self.run_at(NOW, [('0035S0',1),('0158S0',2),('000001',3)])
        before = self.path.read_bytes()
        data = self.data()
        self.assertEqual([s['stock_code'] for s in data['stocks']], ['000001'])
        self.assertEqual({s['stock_code'] for s in data['unsupported_stocks']}, {'0035S0','0158S0'})
        self.assertTrue(all(not s['csv_supported'] for s in data['unsupported_stocks']))
        self.assertEqual((data['latest_unclassified_count'],data['latest_processable_count']), (3,1))
        self.assertEqual(before,self.path.read_bytes())

    def test_invalid_or_naive_collection_times_are_not_recent(self):
        run = self.run_at(NOW, [('000001',1)])
        for value in ('bad','2026-10-23T12:00:00'):
            with closing(sqlite3.connect(self.path)) as db:
                db.execute('UPDATE collections SET collected_at=? WHERE id=?',(value,run));db.commit()
            data = self.data()
            self.assertIsNone(data['latest_ranking'])
            self.assertEqual(data['stocks'][0]['recent_ranking_appearances'],0)
        with self.assertRaises(ValueError): self.data(now=NOW.replace(tzinfo=None))

    def test_condition_only_stock_retains_legacy_queue_with_zero_ranking_counts(self):
        with closing(Store(self.path)) as store:
            migrate_conditions(store.db)
            with store.db:
                store.db.execute("INSERT INTO condition_definitions VALUES(1,'mock','seed','fixture','7','7',?)",(NOW.isoformat(),))
                store.db.execute("INSERT INTO condition_runs(id,batch_id,definition_id,requested_at,completed_at,status,record_count) VALUES('c','c',1,?,?,'success',1)",(NOW.isoformat(),NOW.isoformat()))
                store.db.execute("INSERT INTO condition_members VALUES('c','000001','A000001','condition-only','{}')")
        row = self.data()['stocks'][0]
        self.assertEqual((row['recent_ranking_appearances'],row['recent_top5_appearances'],row['recent_top10_appearances']), (0,0,0))
        self.assertIsNone(row['last_ranking_date'])
        self.assertEqual(row['condition_appearances'],1)
        self.assertFalse(row['in_latest_top20'])

    def test_readonly_cli_requires_no_credentials_or_store(self):
        from kiwoom_collector.__main__ import main
        self.run_at(NOW, [('000001',1)])
        before = self.path.read_bytes()
        with patch('kiwoom_collector.__main__.Store',side_effect=AssertionError('no Store')), \
             patch('kiwoom_collector.__main__.load_credentials',side_effect=AssertionError('no credentials')), \
             patch('socket.socket.connect',side_effect=AssertionError('offline')),patch('builtins.print'):
            self.assertEqual(main(['sector','workbench','--environment','mock','--db',str(self.path),
                                   '--output',str(self.folder/'workbench.html')]),0)
        self.assertEqual(before,self.path.read_bytes())

    def test_empty_workspace_and_old_dates_do_not_invent_recent_appearances(self):
        self.assertEqual(self.data()['stocks'],[])
        self.assertEqual(self.data()['unsupported_stocks'],[])
        self.assertEqual(self.data()['latest_unclassified_count'],0)
        self.run_at(NOW-timedelta(days=40), [('000001',1)])
        row = self.data()['stocks'][0]
        self.assertEqual(row['recent_ranking_appearances'],0)
        self.assertEqual(row['ranking_appearances'],1)
        self.assertEqual(row['last_ranking_date'],'2026-09-13')

    def test_unsupported_market_name_cannot_inject_html_or_unknown_fields(self):
        self.run_at(NOW, [('0035S0',1)])
        hostile = '</script><img src=x onerror=alert(1)>'
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE ranking_records SET stk_nm=?,raw_json=?',
                       (hostile,json.dumps(dict(token='PRIVATE_UNKNOWN_RESPONSE'))))
            db.commit()
        target = self.folder/'escaped.html'
        export_workbench(self.path,'mock',target)
        text = target.read_text('utf-8')
        self.assertNotIn(hostile,text)
        self.assertNotIn('PRIVATE_UNKNOWN_RESPONSE',text)
        self.assertNotIn('innerHTML',text)
        encoded = text.split('<script id="workspace-data" type="application/json">',1)[1].split('</script>',1)[0]
        self.assertEqual(json.loads(encoded)['unsupported_stocks'][0]['stock_name'],hostile)

    @unittest.skipUnless(browser_path(),'Chromium/Edge unavailable')
    def test_browser_priority_filters_current_tags_and_csv_validation_remain_safe(self):
        self.run_at(NOW-timedelta(hours=1), [('000001',1),('000002',2)])
        self.run_at(NOW, [('000002',1),('000003',2),('0035S0',3)])
        with closing(Store(self.path)) as store:
            SectorManager(store).add('Broad category')
        data = self.data()
        # Inject fixed NOW only for a deterministic offline browser fixture.
        with patch('kiwoom_collector.workbench.workspace_data',return_value=data):
            export_workbench(self.path,'mock',self.folder/'workbench.html')
        driver = r'''<script>
try{
function check(value,message){if(!value)throw Error(message);}
check(byId('stock-title').textContent.includes('000002'),'recent frequency first');
check(byId('stock-facts').textContent.includes('TOP5 2'),'recent top5 shown');
check(byId('stock-current-tags').textContent.includes('없음'),'current tags explicit');
check(byId('unsupported-stocks').textContent.includes('0035S0'),'unsupported code preserved');
check(!data.stocks.some(s=>s.stock_code==='0035S0'),'unsupported cannot edit');
byId('latest-only').checked=true;byId('latest-only').dispatchEvent(new Event('change'));
check(byId('stocks').querySelectorAll('button').length===2,'latest filter');
check(!byId('stocks').textContent.includes('000001'),'old-only hidden');
byId('stock-filter').value='000003';byId('stock-filter').dispatchEvent(new Event('input'));
check(byId('stock-title').textContent.includes('000003'),'filtered active selection stays visible');
check(byId('stock-facts').textContent.includes('신규 진입'),'new entry shown');
byId('theme-search').value='Broad';byId('theme-search').dispatchEvent(new Event('input'));
byId('catalog').querySelector('button').click();
check(buildExport().rows.length===0,'selection never auto-confirms');
const values={source:'fixture',observed_at:'2026-10-18',terms_url:'https://example.com/terms',terms_note:'fixture only',terms_checked_at:'2026-10-18'};
for(const [key,value]of Object.entries(values)){const input=byId('default-'+key);input.value=value;input.dispatchEvent(new Event('input'));}
byId('copy-defaults').click();byId('confirm-stock').click();
const result=buildExport();check(result.rows.length===1&&result.rows[0].stock_code==='000003','confirmed compatible CSV');
check(result.csv.charCodeAt(0)===0xfeff,'BOM retained');
byId('stock-filter').value='missing';byId('stock-filter').dispatchEvent(new Event('input'));
check(byId('add-theme').disabled,'no hidden active stock can be edited');
byId('stock-filter').value='';byId('latest-only').checked=false;
byId('priority-sort').value='legacy';byId('priority-sort').dispatchEvent(new Event('change'));
const expected=[...data.stocks].sort((a,b)=>a.legacy_priority-b.legacy_priority);
check([...byId('stocks').querySelectorAll('button')].every((b,i)=>b.textContent.includes(expected[i].stock_code)),'legacy order selectable');
check(expected[1].stock_code==='000001','legacy fallback order retained');
check(byId('csv-workflow').textContent.includes('--dry-run'),'read-only validation instructions');
const out=document.createElement('pre');out.id='browser-test-result';out.textContent=JSON.stringify({ok:true,csv:result.csv});document.body.append(out);
}catch(error){const out=document.createElement('pre');out.id='browser-test-result';out.textContent=JSON.stringify({ok:false,message:error.message});document.body.append(out);}
</script>'''
        target = self.folder/'browser.html'
        target.write_text((self.folder/'workbench.html').read_text('utf-8').replace('</body>',driver+'</body>'),encoding='utf-8')
        before = self.path.read_bytes()
        dom = browser_dom(target,self.folder/'profile')
        match = re.search(r'<pre id="browser-test-result">(.*?)</pre>',dom,re.S)
        self.assertIsNotNone(match,dom[-3000:])
        result = json.loads(unescape(match.group(1)))
        self.assertTrue(result['ok'],result)
        csv_path = self.folder/'reviewed.csv'
        csv_path.write_text(result['csv'],encoding='utf-8')
        report, code = external_command(self.path,['import',str(csv_path)],environment='mock',dry_run=True)
        self.assertEqual(code,0)
        self.assertEqual(report['errors'],[])
        self.assertFalse(report['applied'])
        self.assertEqual(before,self.path.read_bytes())
