"""Real Chrome input/paint timing without headless virtual time."""
import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path


def run_probe(chrome, url, probe, output, free_port):
    import websocket
    path, windows=chrome
    port=free_port()
    profile=f"C:\\Windows\\Temp\\ai-ems-kr-cdp-{port}" if windows else f"/tmp/ai-ems-kr-cdp-{port}"
    command=[str(path),"--headless=new",f"--user-data-dir={profile}","--no-first-run","--window-size=1600,1200",
             f"--remote-debugging-port={port}","about:blank"]
    process=subprocess.Popen(command,cwd="/mnt/c" if windows else None,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    connection=None
    counter=0
    output.mkdir(parents=True,exist_ok=True)
    try:
        deadline=time.monotonic()+30
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json",timeout=2) as response:
                    targets=json.load(response)
                target=next(t for t in targets if t["type"]=="page")
                connection=websocket.create_connection(target["webSocketDebuggerUrl"],timeout=120,suppress_origin=True)
                break
            except (OSError,StopIteration):
                if time.monotonic()>deadline: raise RuntimeError("Chrome DevTools connection unavailable")
                time.sleep(.1)
        def call(method,params=None):
            nonlocal counter
            counter+=1; identifier=counter
            connection.send(json.dumps(dict(id=identifier,method=method,params=params or {})))
            while True:
                message=json.loads(connection.recv())
                if message.get("id")==identifier:
                    if "error" in message: raise RuntimeError(message["error"])
                    return message.get("result",{})
        def evaluate(expression):
            reply=call("Runtime.evaluate",dict(expression=expression,returnByValue=True))
            if "exceptionDetails" in reply: raise RuntimeError(reply["exceptionDetails"])
            return reply.get("result",{}).get("value")
        call("Page.navigate",dict(url=url))
        deadline=time.monotonic()+240
        while not evaluate("document.readyState === 'complete' && typeof gridEntries !== 'undefined'"):
            if time.monotonic()>deadline: raise TimeoutError("Chrome page load")
            time.sleep(.02)
        evaluate(probe.read_text(encoding="utf-8"))
        handled=set()
        while True:
            report=evaluate("window.__krReport || null")
            if report is not None:
                report["browser"] = call("Browser.getVersion")
                (output/"results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
                return report
            stage=evaluate("window.__krTrustedInput || null")
            if stage and stage["name"] not in handled:
                handled.add(stage["name"])
                x,y=stage["x"],stage["y"]
                if stage["name"]=="zoom":
                    call("Input.dispatchMouseEvent",dict(type="mouseWheel",x=x,y=y,deltaX=0,deltaY=-500))
                else:
                    call("Input.dispatchMouseEvent",dict(type="mouseMoved",x=x,y=y))
                    call("Input.dispatchMouseEvent",dict(type="mousePressed",x=x,y=y,button="left",buttons=1,clickCount=1))
                    for dx in [10,25,45,65]:
                        call("Input.dispatchMouseEvent",dict(type="mouseMoved",x=x+dx,y=y+20,button="left",buttons=1))
                    call("Input.dispatchMouseEvent",dict(type="mouseReleased",x=x+65,y=y+20,button="left",buttons=0,clickCount=1))
                evaluate("window.__krInputSent = true")
            shot=evaluate("window.__krScreenshot || null")
            if shot and shot not in handled:
                handled.add(shot)
                data=call("Page.captureScreenshot",dict(format="png"))["data"]
                (output/f"{shot}.png").write_bytes(base64.b64decode(data))
                evaluate("window.__krScreenshotDone = true")
            if time.monotonic()>deadline: raise TimeoutError("Korea Chrome probe")
            time.sleep(.02)
    finally:
        if connection:
            try: connection.close()
            except OSError: pass
        process.terminate()
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=15)
