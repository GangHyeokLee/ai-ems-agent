"""Local-only isolated server support. No production process/config mutation."""
from contextlib import contextmanager
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from pipelines.mn.bundle import tree_files

ROOT = Path(__file__).resolve().parents[1]
BUNDLES = {
    "historical": ("mn-osm-35k-historical-20261029-review02", "9b81a942eceb86bc695b0a6771d04cc7a84ebcf06bf1f4f75c5e09da6e0fc59c"),
    "stage3a": ("mn-osm-35k-stage3a-20261029-review02", "953022c62fb8a4cbb377f8bdb96e248252027616e58b3085ddf0da4eafdf0bd9"),
}


def free_port():
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return stream.getsockname()[1]


def request(url, payload=None, timeout=90):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def get(base, path, payload=None):
    status, data = request(base+path, payload)
    assert status == 200, (path, status, data)
    return data


def prepare_workspace(parent):
    root = parent/"standalone-ems"
    root.mkdir()
    for name in ("src", "studies", "pipelines", "ui"):
        shutil.copytree(ROOT/name, root/name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    shutil.copyfile(ROOT/"web_app.py", root/"web_app.py")
    (root/"testtools").mkdir()
    shutil.copyfile(ROOT/"tests/stage3c_server_entry.py", root/"testtools/server_entry.py")
    for name in ("data/KPG193_ver2_0_powsybl_full.mat", "data/bus_location.csv",
                 "data/grids/kr-geography.json", "data/grids/kr-osm-transmission-map.json"):
        target = root/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/name, target)
    for source, (name, _) in BUNDLES.items():
        shutil.copytree(ROOT/"outputs/mn_stage3b/bundles"/name, root/"bundles"/source)
    original = ROOT.parent/"mongolia-grid-poc"
    blocked = [original/"scripts/convert_mn35k_to_powsybl.py",
               original/"pypsa-earth/networks/MN_35k_poc/base.nc", original/".venv-powsybl/bin/python",
               original/"reports/pypowsybl_conversion/element_mapping.csv", ROOT/".env", ROOT/"web_app.py",
               ROOT/"data/grids/mn-osm-35k-map.json", ROOT/"data/grids/mn35k-synthetic-pf-preview.json",
               ROOT/"outputs/mn_stage3b/bundles"/BUNDLES["historical"][0]/"manifest.json"]
    (root/"isolation.json").write_text(json.dumps(list(map(str, blocked))))
    (root/".venv").mkdir()  # Mounted read-only, never copied or modified.
    return root


class ScriptedOllama(http.server.BaseHTTPRequestHandler):
    """Only model responses are synthetic; graph, tools, API and UI are real."""
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = body["messages"]
        user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        tool_results = [m for m in messages[user+1:] if m["role"] == "tool"]
        text = messages[user]["content"]
        names = [t["function"]["name"] for t in body.get("tools", [])]
        assert names and all("mongolia" in n for n in names), names
        self.server.calls.append({"message": text, "bound_tools": names, "tool_result_count": len(tool_results)})
        action = None
        if not tool_results:
            if "PF" in text:
                action = ("get_mongolia_pf_summary", {})
            else:
                action = ("run_mongolia_line_contingency", {"outage_line_id": "751261406-1_0"})
        elif "민감도" in text and len(tool_results) == 1:
            action = ("run_mongolia_line_sensitivity", {"outage_line_id": "751261406-1_0", "top_n": 5})
        message = {"role": "assistant", "content": "합성 PF 결과이며 실제 몽골 운영 데이터가 아닙니다."}
        if action:
            message = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": action[0], "arguments": action[1]}}]}
        data = json.dumps({"model": "stage3c-scripted", "created_at": "2026-10-29T00:00:00Z",
                           "message": message, "done": True, "done_reason": "stop"}).encode()+b"\n"
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@contextmanager
def scripted_llm():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ScriptedOllama)
    server.calls = []
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server.calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=10)


@contextmanager
def isolated_server(root, source, llm_url, output, *, app="web_app:app", override=None):
    assert shutil.which("bwrap"), "Mount-isolated runtime test requires bwrap"
    port = free_port()
    env = os.environ.copy()
    for name in ("AI_EMS_LLM_API_KEY", "AI_EMS_CF_ACCESS_CLIENT_ID", "AI_EMS_CF_ACCESS_CLIENT_SECRET"):
        env.pop(name, None)
    env.update(PYTHONPATH=f"{root}/src:{root}", PYTHONDONTWRITEBYTECODE="1", PATH="/usr/bin:/bin",
               AI_EMS_MN_BUNDLE_DIR=str(root/"bundles"/source), AI_EMS_MN_BUNDLE_MANIFEST_SHA256=BUNDLES[source][1],
               AI_EMS_CASE_FILE=str(root/"data/KPG193_ver2_0_powsybl_full.mat"),
               AI_EMS_BUS_LOCATION_FILE=str(root/"data/bus_location.csv"),
               AI_EMS_KOREA_MAP_FILE=str(root/"data/grids/kr-osm-transmission-map.json"),
               AI_EMS_MN_XIIDM_FILE="/blocked-legacy-mongolia/model.xiidm",
               AI_EMS_MN_POWSYBL_REPORTS_DIR="/blocked-legacy-mongolia/reports",
               AI_EMS_MODEL="stage3c-scripted", AI_EMS_LLM_BASE_URL=llm_url)
    env.update(override or {})
    command = ["bwrap", "--ro-bind", "/", "/", "--bind", "/tmp", "/tmp", "--dev-bind", "/dev", "/dev", "--proc", "/proc",
               "--ro-bind", str(root), str(root), "--ro-bind", str(ROOT/".venv"), str(root/".venv"),
               "--tmpfs", str(ROOT.parent/"mongolia-grid-poc"), "--tmpfs", str(ROOT), "--chdir", str(root), "--",
               str(root/".venv/bin/python"), "-B", str(root/"testtools/server_entry.py"), app, str(port)]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        info = {"pid": process.pid, "port": port, "command": command, "source": source, "stopped": False}
        child_fd = None
        try:
            deadline = time.monotonic()+90
            while True:
                try:
                    get(f"http://127.0.0.1:{port}", "/api/health")
                    break
                except (OSError, AssertionError):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError(f"Owned test server startup failed: {output.read_text()[-5000:]}")
                    time.sleep(.1)
            # bwrap is a supervisor, not the Uvicorn process. Pin the actual
            # child so shutdown cannot leave an earlier bundle server running
            # or accidentally signal a recycled PID.
            match = re.search(r"Started server process \[(\d+)\]", output.read_text())
            assert match, "Owned Uvicorn PID missing from startup log"
            info["server_pid"] = int(match[1])
            child_fd = os.pidfd_open(info["server_pid"])
            yield f"http://127.0.0.1:{port}", info
        finally:
            if process.poll() is None:
                if child_fd is not None:
                    signal.pidfd_send_signal(child_fd, signal.SIGTERM)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                info["forced_shutdown"] = True
            finally:
                if child_fd is not None:
                    os.close(child_fd)
            with socket.socket() as probe:
                probe.settimeout(2)
                port_closed = probe.connect_ex(("127.0.0.1", port)) != 0
            shutdown_log = output.read_text()
            # Uvicorn re-raises SIGTERM after completing its lifespan. bwrap
            # reports the child's signal as exit status 128+15 (143).
            info.update(stopped=port_closed, port_closed=port_closed, returncode=process.returncode,
                        shutdown_signal="SIGTERM",
                        graceful_shutdown=process.returncode in (0, 128 + signal.SIGTERM)
                        and "Application shutdown complete." in shutdown_log
                        and f"Finished server process [{info.get('server_pid')}]" in shutdown_log)
    proof = next(json.loads(line) for line in output.read_text().splitlines() if line.startswith('{"isolation_proof"'))
    info["isolation"] = proof


def collect_api(base):
    from pipelines.mn.inventory import Checks, compare_numerical
    diagnostic = get(base, "/api/grids/mn-osm-35k/runtime-status")
    catalog = get(base, "/api/grids")
    maps = {grid: get(base, f"/api/grids/{grid}/map") for grid in ("kr-kpg193", "kr-osm-transmission", "mn-osm-35k")}
    preview = get(base, "/api/grids/mn-osm-35k/pf-preview")
    reference = get(base, "/api/grids/mn-osm-35k/reference-layers")
    numerical = {"status": get(base, "/api/grids/mn-osm-35k/security-status"),
                 "loadflow_parameters": diagnostic["loadflow_parameters"], "sensitivity_definition": diagnostic["sensitivity_definition"],
                 "n1": [get(base, "/api/grids/mn-osm-35k/security-analysis?outage_line_id="+name)
                        for name in ("222087807-1_0", "751261406-1_0", "484333456-1_0")],
                 "sensitivity": [get(base, "/api/grids/mn-osm-35k/sensitivity-analysis?outage_line_id=751261406-1_0&top_n=45&monitored_line_id="+name)
                                 for name in ("303374638-1_0", "484276912-1_0", "259474322-1_0")]}
    contract = json.loads((ROOT/"pipelines/mn/bundle_runtime_contract.json").read_text())
    checks = Checks()
    compare_numerical(numerical, contract["numerical_reference"], checks)
    assert len(checks.items) == 186 and checks.ready(), checks.items
    chat = [get(base, "/api/grids/mn-osm-35k/chat", {"session_id": "same-session-on-each-new-process", "message": question})
            for question in ("PF 요약을 도구로 조회해 줘", "751261406-1_0 N-1 후 민감도 Top 5")]
    assert chat[0]["tools_used"] == ["get_mongolia_pf_summary"], chat[0]
    assert chat[1]["tools_used"] == ["run_mongolia_line_contingency", "run_mongolia_line_sensitivity"], chat[1]
    assert [u["type"] for u in chat[1]["ui_updates"]] == ["security", "sensitivity"]
    return {"diagnostic": diagnostic, "catalog": catalog, "map_counts": {grid: {k: len(v[k]) for k in ("buses", "lines", "transformers")} for grid, v in maps.items()},
            "pf_metadata": preview["metadata"], "reference_counts": {k: len(reference[k]) for k in ("lines", "substations")},
            "numeric_checks": checks.items, "numerical": numerical, "chat": chat, "llm_mode": "SCRIPTED_LOCAL_OLLAMA_PROTOCOL"}
