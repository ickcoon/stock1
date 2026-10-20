"""Read-only workspace data plus real local Chromium UI/CSV round trip."""
from contextlib import closing
import csv
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from urllib.request import build_opener, ProxyHandler
from websocket import create_connection, WebSocketException
import unittest

from kiwoom_collector.client import KST, Snapshot
from kiwoom_collector.conditions import migrate_conditions
from kiwoom_collector.external_tags import export_priority, import_external, add_alias
from kiwoom_collector.sectors import SectorManager
from kiwoom_collector.storage import Store
from kiwoom_collector.workbench import workspace_data, export_workbench

NOW = datetime(2026,10,20,15,tzinfo=KST)


def browser_path():
    for name in ('chromium','chromium-browser','google-chrome','msedge'):
        if shutil.which(name): return shutil.which(name)
    for variable in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
        for relative in ('Microsoft/Edge/Application/msedge.exe','Google/Chrome/Application/chrome.exe'):
            path = Path(os.environ.get(variable,'/nonexistent'))/relative
            if path.is_file(): return str(path)
    return None


def browser_dom(target, profile):
    # CDP avoids Chromium 154 --dump-dom hanging on browser_ui targets.
    process = subprocess.Popen([browser_path(),'--headless','--no-sandbox','--disable-gpu',
        '--disable-dev-shm-usage','--disable-extensions','--disable-background-networking',
        '--disable-component-update','--disable-sync','--no-first-run','--no-default-browser-check',
        '--host-resolver-rules=MAP * ~NOTFOUND','--remote-debugging-port=0',
        '--remote-debugging-address=127.0.0.1','--remote-allow-origins=*',
        '--user-data-dir='+str(profile),'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    socket = None
    try:
        deadline = time.monotonic()+15
        port_file = profile/'DevToolsActivePort'
        while not port_file.exists():
            if time.monotonic()>deadline or process.poll() is not None:
                raise AssertionError('Headless browser failed to start')
            time.sleep(.05)
        port = port_file.read_text().splitlines()[0]
        opener = build_opener(ProxyHandler({}))
        with opener.open('http://127.0.0.1:'+port+'/json/list',timeout=5) as response:
            targets = json.load(response)
        page = next(t for t in targets if t['type']=='page')
        socket = create_connection(page['webSocketDebuggerUrl'],timeout=5,http_no_proxy=['127.0.0.1','localhost'])
        sequence = 0
        def command(method, params=None):
            nonlocal sequence
            sequence += 1
            socket.send(json.dumps({'id':sequence,'method':method,'params':params or {}}))
            while True:
                response = json.loads(socket.recv())
                if response.get('id') == sequence:
                    if 'error' in response: raise AssertionError(response)
                    return response.get('result',{})
        # Managed cloud Chromium blocks file:// navigation. Render our generated
        # document in memory; no server, policy override, or remote site is used.
        frame = command('Page.getFrameTree')['frameTree']['frame']['id']
        command('Page.setDocumentContent',{'frameId':frame,'html':target.read_text(encoding='utf-8')})
        while time.monotonic()<deadline:
            result = command('Runtime.evaluate',{'expression':"document.getElementById('browser-test-result')?.textContent || ''",'returnByValue':True})
            if result.get('result',{}).get('value'):
                return command('Runtime.evaluate',{'expression':'document.documentElement.outerHTML','returnByValue':True})['result']['value']
            time.sleep(.05)
        raise AssertionError('Workspace browser driver did not finish: '+str(command('Runtime.evaluate',{'expression':'document.documentElement.outerHTML','returnByValue':True}))[-2500:])
    finally:
        if socket is not None:
            try:
                socket.send(json.dumps({'id':999999,'method':'Browser.close'}))
            except (OSError, WebSocketException):
                pass  # The owned process is still reaped below if transport is broken.
            finally:
                socket.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder/'stock1.db'
        with closing(Store(self.path)) as store:
            for n,(at,environment,test) in enumerate(((NOW,'mock',False),(NOW-timedelta(days=8),'mock',False),
                    (NOW-timedelta(minutes=1),'real',False),(NOW-timedelta(minutes=2),'mock',True))):
                identifier = store.begin(at,environment,'1',test_mode=test)
                store.finish(identifier,Snapshot(at,[{'bigd_rank':'1','stk_cd':'005930','stk_nm':'삼성전자'},
                    {'bigd_rank':'2','stk_cd':'000660','stk_nm':'SK하이닉스'},
                    {'bigd_rank':'3','stk_cd':'035420','stk_nm':'NAVER'}]))
            manager = SectorManager(store)
            manager.add('반도체'); manager.add('인터넷')
            manager.tag('035420','인터넷','수동')
            add_alias(store.db,'반도체주','반도체','핀업')
            migrate_conditions(store.db)
            with store.db:
                store.db.execute("INSERT INTO condition_definitions VALUES(1,'mock','sector_seed','주도주종베','7','7','2026-10-20T15:00:00+09:00')")
                store.db.execute("""INSERT INTO condition_runs(id,batch_id,definition_id,requested_at,completed_at,status,record_count)
                    VALUES('run','batch',1,'2026-10-20T15:00:00+09:00','2026-10-20T15:00:01+09:00','success',1)""")
                store.db.execute("INSERT INTO condition_members VALUES('run','005930','A005930','삼성전자','{\"token\":\"MUST_NOT_EMBED\"}')")
        self.output = self.folder/'workbench.html'

    def dump(self):
        with closing(sqlite3.connect(self.path)) as db: return list(db.iterdump())

    def payload(self):
        text = self.output.read_text(encoding='utf-8')
        encoded = text.split('<script id="workspace-data" type="application/json">',1)[1].split('</script>',1)[0]
        return text,json.loads(encoded)

    def test_reuses_priority_order_and_metadata_without_modifying_database(self):
        before = self.dump()
        with closing(Store(self.path)) as store:
            priority = export_priority(store.db,self.folder/'priority.csv','mock')
        data = workspace_data(self.path,'mock',now=NOW)
        self.assertEqual([s['stock_code'] for s in data['stocks']], [s['stock_code'] for s in priority['priorities']])
        self.assertEqual([s['stock_code'] for s in data['stocks']],['005930','000660'])
        samsung = data['stocks'][0]
        self.assertEqual(samsung['ranking_appearances'],2)
        self.assertEqual(samsung['recent_ranking_appearances'],1)
        self.assertEqual(samsung['condition_appearances'],1)
        self.assertEqual(samsung['condition_aliases'],['sector_seed'])
        self.assertEqual(data['aliases'][0]['sector_name'],'반도체')
        self.assertEqual(next(s for s in data['sectors'] if s['sector_name']=='인터넷')['stock_count'],1)
        self.assertNotIn('MUST_NOT_EMBED',json.dumps(data))
        self.assertEqual(before,self.dump())

    def test_environment_separation_no_condition_schema_required(self):
        data = workspace_data(self.path,'real',now=NOW)
        self.assertEqual(data['stocks'][0]['condition_appearances'],0)
        self.assertTrue(all(s['ranking_appearances']==1 for s in data['stocks']))
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('DROP TABLE condition_members')
            db.execute('DROP TABLE condition_runs')
            db.execute('DROP TABLE condition_definitions')
        data = workspace_data(self.path,now=NOW)
        self.assertEqual(len(data['stocks']),2)
        self.assertTrue(all(s['condition_appearances']==0 for s in data['stocks']))

    def test_html_is_self_contained_with_escaped_database_text(self):
        hostile = '</script><script>alert("unsafe")</script>'
        with closing(Store(self.path)) as store:
            SectorManager(store).add(hostile)
        before = self.dump()
        export_workbench(self.path,'mock',self.output)
        text,data = self.payload()
        self.assertNotIn(hostile,text)
        self.assertIn(hostile,[s['sector_name'] for s in data['sectors']])
        self.assertIn("connect-src 'none'",text)
        self.assertNotRegex(text,r'<script[^>]+src=')
        self.assertNotIn('fetch(',text)
        self.assertNotIn('innerHTML',text)
        self.assertNotIn('MUST_NOT_EMBED',text)
        self.assertEqual(self.dump(),before)

    def test_missing_database_and_bad_environment_never_create_db(self):
        missing = self.folder/'absent.db'
        for path,environment in ((missing,'mock'),(self.path,'other')):
            with self.assertRaises(ValueError): export_workbench(path,environment,self.output)
        self.assertFalse(missing.exists()); self.assertFalse(self.output.exists())

    def test_output_protection_and_empty_workspace(self):
        self.output.write_text('user edits',encoding='utf-8')
        with self.assertRaises(ValueError): export_workbench(self.path,'mock',self.output)
        self.assertEqual(self.output.read_text(encoding='utf-8'),'user edits')
        with self.assertRaises(ValueError): export_workbench(self.path,'mock',self.path)
        with closing(Store(self.path)) as store:
            manager = SectorManager(store)
            for code in ('005930','000660'): manager.tag(code,'반도체')
        target = self.folder/'empty.html'
        self.assertEqual(export_workbench(self.path,'mock',target)['stock_count'],0)

    @unittest.skipUnless(browser_path(),'Chromium/Edge not installed; Python and CLI checks still run')
    def test_real_browser_editing_confirmation_and_csv_round_trip(self):
        export_workbench(self.path,'mock',self.output)
        # Append a test driver to the actual standalone HTML; production JS is unchanged.
        driver = r'''
<script>
(async()=>{const checks=[];function check(value,message){if(!value)throw Error(message);checks.push(message);}
try{
check(byId('stock-title').textContent.includes('005930'),'priority first stock');
byId('theme-search').value='반도체주';byId('theme-search').dispatchEvent(new Event('input'));
const option=[...byId('catalog').querySelectorAll('button')].find(b=>b.textContent.startsWith('반도체 ('));
check(!!option,'alias search finds canonical theme');option.click();
check(buildExport().rows.length===0,'new selection unconfirmed');
byId('new-theme').value='테스트 "테마", 신규';byId('add-theme').click();
check(drafts.get(active).length===2,'multiple themes and new theme');
byId('confirm-stock').click();check(!drafts.get(active).some(t=>t.confirmed),'missing terms cannot confirm');
const values={source:'검토 출처',observed_at:'2026-10-18',terms_url:'https://example.com/terms',terms_note:'한 줄 메모, "인용"',terms_checked_at:'2026-10-18'};
for(const [key,value]of Object.entries(values)){const input=byId('default-'+key);input.value=value;input.dispatchEvent(new Event('input'));}
byId('copy-defaults').click();check(buildExport().rows.length===0,'copy defaults does not confirm');
byId('confirm-stock').click();check(buildExport().rows.length===2,'explicit confirmation exports both themes');
const change=byId('tag-0-terms_note');change.value='수정 메모';change.dispatchEvent(new Event('input'));
check(buildExport().rows.length===1&&buildExport().unconfirmed===1,'editing resets confirmation');
byId('confirm-stock').click();
const second=[...byId('stocks').querySelectorAll('button')].find(b=>b.textContent.includes('000660'));second.click();
byId('new-theme').value='미확인 테마';byId('add-theme').click();
check(buildExport().rows.length===2&&buildExport().unconfirmed===1,'unconfirmed stock excluded');
const result=buildExport();check(result.csv.charCodeAt(0)===0xfeff,'CSV UTF-8 BOM');
let downloaded=false;const oldClick=HTMLAnchorElement.prototype.click;
HTMLAnchorElement.prototype.click=function(){downloaded=this.download.endsWith('.csv')&&this.href.startsWith('blob:');};
byId('export').click();HTMLAnchorElement.prototype.click=oldClick;check(downloaded,'download uses local Blob CSV');
const out=document.createElement('pre');out.id='browser-test-result';out.textContent=JSON.stringify({ok:true,checks,csv:result.csv});document.body.append(out);
}catch(error){const out=document.createElement('pre');out.id='browser-test-result';out.textContent=JSON.stringify({ok:false,message:error.message,stack:error.stack});document.body.append(out);}
})();
</script>'''
        target = self.folder/'browser-test.html'
        target.write_text(self.output.read_text(encoding='utf-8').replace('</body>',driver+'</body>'),encoding='utf-8')
        profile = self.folder/'browser-profile'
        dom = browser_dom(target,profile)
        match = re.search(r'<pre id="browser-test-result">(.*?)</pre>',dom,re.S)
        self.assertIsNotNone(match,dom[-3000:])
        from html import unescape
        data = json.loads(unescape(match.group(1)))
        self.assertTrue(data['ok'],data)
        csv_path = self.folder/'browser-export.csv'
        csv_path.write_text(data['csv'],encoding='utf-8')
        with csv_path.open(encoding='utf-8-sig',newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['stock_code']=='005930' for r in rows))
        self.assertIn('테스트 "테마", 신규',[r['sector_name'] for r in rows])
        before = self.dump()
        with closing(Store(self.path)) as store:
            preview = import_external(store.db,csv_path,{},dry_run=True)
            self.assertEqual(preview['errors'],[])
        self.assertEqual(before,self.dump())
        with closing(Store(self.path)) as store:
            applied = import_external(store.db,csv_path,{})
            self.assertTrue(applied['applied'])
            self.assertEqual(len(SectorManager(store).show('005930')['sectors']),2)
            self.assertEqual(SectorManager(store).show('035420')['sectors'][0]['sector_name'],'인터넷')


if __name__=='__main__': unittest.main()
