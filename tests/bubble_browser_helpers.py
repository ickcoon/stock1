"""Owned offline Chromium session for native Bubble Radar interactions."""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import time
from urllib.request import build_opener, ProxyHandler
from websocket import create_connection, WebSocketException
from test_workbench import browser_path


@contextmanager
def browser_page(html, folder, width=1360, height=900):
    profile=Path(folder)/('bubble-profile-'+str(time.time_ns()))
    process=subprocess.Popen([browser_path(),'--headless','--no-sandbox','--disable-gpu',
        '--disable-extensions','--disable-background-networking','--disable-component-update',
        '--disable-sync','--no-first-run','--no-default-browser-check',
        '--host-resolver-rules=MAP * ~NOTFOUND','--remote-debugging-port=0',
        '--remote-debugging-address=127.0.0.1','--remote-allow-origins=*',
        '--user-data-dir='+str(profile),'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    socket=None
    try:
        limit=time.monotonic()+20
        while not (profile/'DevToolsActivePort').exists():
            if process.poll() is not None or time.monotonic()>limit:
                raise AssertionError('Headless browser startup failed')
            time.sleep(.05)
        port=(profile/'DevToolsActivePort').read_text().splitlines()[0]
        with build_opener(ProxyHandler({})).open('http://127.0.0.1:'+port+'/json/list',timeout=5) as response:
            target=next(t for t in json.load(response) if t['type']=='page')
        socket=create_connection(target['webSocketDebuggerUrl'],timeout=15,http_no_proxy=['127.0.0.1','localhost'])
        sequence=0
        def command(method,params=None):
            nonlocal sequence
            sequence+=1
            socket.send(json.dumps(dict(id=sequence,method=method,params=params or {})))
            while True:
                response=json.loads(socket.recv())
                if response.get('id')==sequence:
                    if 'error' in response: raise AssertionError(response)
                    return response.get('result',{})
        def evaluate(expression):
            result=command('Runtime.evaluate',dict(expression=expression,returnByValue=True))
            if 'exceptionDetails' in result: raise AssertionError(result)
            return result['result'].get('value')
        def click(selector, touch=False):
            point=evaluate(f'(()=>{{const r=document.querySelector({json.dumps(selector)}).getBoundingClientRect();return {{x:r.x+r.width/2,y:r.y+r.height/2}};}})()')
            if touch:
                command('Input.dispatchTouchEvent',dict(type='touchStart',touchPoints=[point]))
                command('Input.dispatchTouchEvent',dict(type='touchEnd',touchPoints=[]))
            else:
                command('Input.dispatchMouseEvent',dict(type='mousePressed',button='left',clickCount=1,**point))
                command('Input.dispatchMouseEvent',dict(type='mouseReleased',button='left',clickCount=1,**point))
        command('Emulation.setDeviceMetricsOverride',dict(width=width,height=height,deviceScaleFactor=1,mobile=False))
        command('Emulation.setTouchEmulationEnabled',dict(enabled=True,maxTouchPoints=1))
        frame=command('Page.getFrameTree')['frameTree']['frame']['id']
        command('Page.setDocumentContent',dict(frameId=frame,html=html))
        time.sleep(.15)
        yield command,evaluate,click
    finally:
        if socket:
            try: command('Browser.close')
            except (OSError,WebSocketException): pass
            socket.close()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
