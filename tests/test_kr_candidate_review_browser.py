"""Opt-in real Chrome: offline map, trusted input, actual download/upload and XSS isolation."""
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pipelines.kr import review_candidates as review
from test_browser_stale_responses import find_chrome,free_port
from test_kr_topology_pipeline import fixture_map,write,bus,line
from pipelines.kr.sources import load_map_graph
from pipelines.kr.topology import analyze
from pipelines.kr.candidates import substation_candidates

pytestmark=pytest.mark.skipif(os.environ.get('AI_EMS_BROWSER_TESTS')!='1',reason='set AI_EMS_BROWSER_TESTS=1 for actual Chrome')


@contextmanager
def browser():
    import websocket
    chrome=find_chrome()
    if chrome is None:pytest.skip('Chrome missing; set AI_EMS_CHROME')
    path,windows=chrome
    if windows:
        pytest.skip('file:// review probe requires Linux Chrome; set AI_EMS_CHROME')
    port=free_port();profile=f'C:\\Windows\\Temp\\ai-ems-review-{port}' if windows else f'/tmp/ai-ems-review-{port}'
    font_conf=ROOT/'.cache/korean-fonts/fonts.conf'
    env={**os.environ,**({'FONTCONFIG_FILE':str(font_conf)} if font_conf.is_file() else {})}
    process=subprocess.Popen([str(path),'--headless=new','--no-first-run',f'--user-data-dir={profile}',f'--remote-debugging-port={port}','--window-size=1600,1200','about:blank'],cwd='/mnt/c' if windows else None,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    connection=None;counter=0
    try:
        deadline=time.monotonic()+30
        while True:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/json',timeout=1) as f:targets=json.load(f)
                target=next(t for t in targets if t['type']=='page')
                connection=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=30,suppress_origin=True);break
            except (OSError,StopIteration):
                if process.poll() is not None:raise RuntimeError(process.stderr.read().decode()[-2000:])
                if time.monotonic()>deadline:raise TimeoutError('Chrome start')
                time.sleep(.1)
        def call(method,params=None):
            nonlocal counter
            counter+=1;identifier=counter;connection.send(json.dumps(dict(id=identifier,method=method,params=params or {})))
            while True:
                reply=json.loads(connection.recv())
                if reply.get('id')==identifier:
                    if 'error' in reply:raise RuntimeError(reply['error'])
                    return reply.get('result',{})
        def evaluate(expression):
            reply=call('Runtime.evaluate',dict(expression=expression,returnByValue=True))
            if 'exceptionDetails' in reply:raise RuntimeError(reply['exceptionDetails'])
            return reply.get('result',{}).get('value')
        yield call,evaluate
    finally:
        if connection:connection.close()
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)


def load_file(call,evaluate,path):
    chrome=find_chrome()
    if chrome[1]:
        # Windows Chrome cannot read Linux file:// paths; a local HTTP file server is used by legacy tests.
        pytest.skip('this file:// offline probe requires Linux Chrome; set AI_EMS_CHROME')
    call('Page.navigate',dict(url=path.resolve().as_uri()))
    deadline=time.monotonic()+30
    while not evaluate('!!window.KRReview'):
        if time.monotonic()>deadline:raise TimeoutError('review page load')
        time.sleep(.02)


def drag_input(call,stage):
    x,y=stage['x'],stage['y'];dx=stage.get('dx',60);dy=stage.get('dy',20)
    call('Input.dispatchMouseEvent',dict(type='mouseMoved',x=x,y=y))
    call('Input.dispatchMouseEvent',dict(type='mousePressed',x=x,y=y,button='left',buttons=1,clickCount=1))
    for fraction in [.15,.5,1]:
        call('Input.dispatchMouseEvent',dict(type='mouseMoved',x=x+dx*fraction,y=y+dy*fraction,button='left',buttons=1))
    call('Input.dispatchMouseEvent',dict(type='mouseReleased',x=x+dx,y=y+dy,button='left',buttons=0,clickCount=1))


def test_actual_candidate_review_in_chrome(tmp_path):
    source=ROOT/'outputs/kr_topology/review/index.html'
    if not source.is_file():pytest.skip('generate actual review first')
    output=ROOT/'outputs/kr_topology/review/browser';output.mkdir(parents=True,exist_ok=True)
    with browser() as (call,evaluate):
        call('Browser.setDownloadBehavior',dict(behavior='allow',downloadPath=str(tmp_path)))
        load_file(call,evaluate,source)
        evaluate((ROOT/'tests/browser/candidate_review_probe.js').read_text())
        deadline=time.monotonic()+90;handled=set()
        while True:
            report=evaluate('window.__reviewReport||null')
            if report:
                report['browser']=call('Browser.getVersion')
                (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                (output/'review.png').write_bytes(base64.b64decode(call('Page.captureScreenshot',dict(format='png'))['data']))
                assert report['ok'],report
                assert len(report['results'])>=25
                exported=json.loads((tmp_path/'review_decisions.json').read_text())
                assert exported['decisions'][0]['status']=='electrical_evidence_obtained'
                embedded=source.read_text().split('<script id="review-data" type="application/json">')[1].split('</script>')[0]
                assert len(review.import_decisions(json.loads(embedded),exported)['decisions'])==1
                break
            stage=evaluate('window.__reviewInput||null')
            if stage and stage['name'] not in handled:
                handled.add(stage['name']);x,y=stage['x'],stage['y']
                if stage['name']=='wheel':call('Input.dispatchMouseEvent',dict(type='mouseWheel',x=x,y=y,deltaX=0,deltaY=-400))
                else:drag_input(call,stage)
                evaluate('window.__reviewInputSent=true')
            if evaluate('window.__reviewFileStage||null')=='download':
                downloaded=tmp_path/'review_decisions.json'
                if downloaded.is_file():
                    root=call('DOM.getDocument')['root']['nodeId'];node=call('DOM.querySelector',dict(nodeId=root,selector='#import'))['nodeId']
                    call('DOM.setFileInputFiles',dict(nodeId=node,files=[str(downloaded)]));evaluate("window.__reviewFileStage='uploaded'")
            if time.monotonic()>deadline:raise TimeoutError('review interaction probe: '+str(evaluate('window.__reviewFileStage')))
            time.sleep(.02)


def test_malicious_names_and_missing_polygon_in_chrome(tmp_path):
    source=fixture_map();source['substations'][0]['name']='</script><img src=x onerror="window.PWNED=1">'
    source['substations'][0]['geometry']=None
    graph=load_map_graph(write(tmp_path/'map.json',source));rows=substation_candidates(graph,analyze(graph)['bus_component'])
    for i,r in enumerate(rows):r['candidate_id']=str(i)
    payload=review.build_review(graph,rows,'a'*64)
    path=tmp_path/'index.html';path.write_text(review.render_html(payload))
    with browser() as (call,evaluate):
        load_file(call,evaluate,path)
        assert evaluate('!window.PWNED && !document.querySelector("img")')
        assert evaluate('document.getElementById("selection-title").textContent.includes("<img")')
        assert evaluate('!document.querySelector("[data-layer=station]")')
        assert evaluate('document.getElementById("geometry-note").textContent.includes("지리 정보가 없습니다")')


@pytest.mark.parametrize('station',['way/887782992','way/156580308'],ids=['gogang','shinokcheon'])
def test_viewport_equipment_updates_in_chrome(station):
    source=ROOT/'outputs/kr_topology/review/index.html'
    if not source.is_file():pytest.skip('generate actual review first')
    output=ROOT/'outputs/kr_topology/review/browser';output.mkdir(parents=True,exist_ok=True)
    with browser() as (call,evaluate):
        load_file(call,evaluate,source)
        evaluate('window.__viewportStation='+json.dumps(station))
        evaluate((ROOT/'tests/browser/candidate_review_viewport_probe.js').read_text())
        deadline=time.monotonic()+30;handled=False
        while True:
            report=evaluate('window.__viewportReport||null')
            if report:
                report['browser']=call('Browser.getVersion')
                (output/f'viewport-{station.replace("/","-")}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                assert report['ok'],report
                break
            stage=evaluate('window.__reviewInput||null')
            if stage and not handled:
                handled=True;drag_input(call,stage);evaluate('window.__reviewInputSent=true')
            if time.monotonic()>deadline:raise TimeoutError('viewport probe')
            time.sleep(.02)


def test_crossing_route_without_visible_vertices_in_chrome(tmp_path):
    source=fixture_map()
    a,b=bus('crossing-a',200,126,36,154),bus('crossing-b',201,128,36,154)
    c,d=bus('outside-c',202,126,35,154),bus('outside-d',203,128,37,154)
    crossing=line(200,a,b,154)
    decoy=line(201,c,d,154);decoy['coordinates']=[[126,35],[128,35],[128,37]]
    source['buses'] += [a,b,c,d];source['lines'] += [crossing,decoy]
    graph=load_map_graph(write(tmp_path/'map.json',source))
    rows=substation_candidates(graph,analyze(graph)['bus_component'])
    for i,r in enumerate(rows):r['candidate_id']=str(i)
    path=tmp_path/'index.html';path.write_text(review.render_html(review.build_review(graph,rows,'a'*64)))
    with browser() as (call,evaluate):
        load_file(call,evaluate,path)
        crossing_selector=f'[data-line-id="{crossing["line_id"]}"]'
        decoy_selector=f'[data-line-id="{decoy["line_id"]}"]'
        assert evaluate(f'!!document.querySelector({json.dumps(crossing_selector)})')
        assert evaluate(f'!document.querySelector({json.dumps(decoy_selector)})')
        # Both endpoints lie far outside the viewport, but the segment crosses its interior.
        assert evaluate('!document.querySelector(\'[data-bus-id="'+a['bus_id']+'"]\')')
        assert evaluate('!document.querySelector(\'[data-bus-id="'+b['bus_id']+'"]\')')
