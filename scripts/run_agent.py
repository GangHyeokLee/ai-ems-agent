"""Run the web app with an optional Windows Caddy process owned by this invocation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time

try:
    from dotenv import dotenv_values
except ImportError:
    sys.exit("python-dotenv가 필요합니다. .venv/bin/python -m pip install -r requirements.txt")

ROOT = Path(__file__).resolve().parents[1]
# The package's public imports include repository-local studies modules.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MANUAL = "Caddy를 수동 실행한 뒤 ./run_agent.sh를 사용할 수 있습니다."
APP_GRACE_SECONDS = 10
APP_TERMINATE_SECONDS = 5
APP_KILL_SECONDS = 5


def probe_llm(values: dict) -> str:
    """Check the app's endpoint and shared authentication rules, without logging secrets."""
    import httpx
    settings = {key: value for key, value in values.items() if value is not None}
    settings.update(os.environ)
    from ai_ems.config import get_llm_client_kwargs
    url = settings.get("AI_EMS_LLM_BASE_URL", "http://host.docker.internal:11434")
    try:
        kwargs = get_llm_client_kwargs(url, environ=settings)
    except (ValueError, TypeError):
        return "invalid_auth_config"
    try:
        response = httpx.get(url.rstrip("/") + "/api/tags", **kwargs,
                             timeout=8, follow_redirects=False)
        if response.status_code != 200:
            notice(f"설정된 LLM /api/tags: HTTP {response.status_code}.")
        if response.status_code in (401, 403):
            return "auth_error"
        if response.status_code == 502:
            return "upstream_502"
        if response.status_code != 200:
            return "http_error"
        data = response.json()
        return "ready" if isinstance(data, dict) and isinstance(data.get("models"), list) else "invalid_response"
    except httpx.TimeoutException:
        return "timeout"
    except httpx.RequestError:
        return "unreachable"
    except (ValueError, TypeError):
        return "invalid_response"


def report_llm(state: str) -> None:
    if state == "ready":
        notice("설정된 LLM /api/tags: HTTP 200 (Ollama 응답 확인).")
        return
    reasons = {"auth_error": "LLM 인증/접근 거부(HTTP 401/403)",
               "upstream_502": "LLM gateway/upstream 오류(HTTP 502)",
               "unreachable": "LLM 주소 연결 실패(Caddy 미실행 또는 네트워크 확인 필요)",
               "timeout": "LLM 응답 시간 초과", "http_error": "LLM HTTP 오류",
               "invalid_response": "Ollama tags 형식이 아닌 응답",
               "invalid_auth_config": "LLM 인증 설정 오류"}
    notice(f"{reasons.get(state, 'LLM 연결 미확인')}. 웹 UI는 계속 실행하지만 AI 사용이 불가능할 수 있습니다. "
           "기존 Caddy는 재시작하지 않습니다.")


def notice(message: str) -> None:
    print(f"[Caddy] {message}", file=sys.stderr, flush=True)


def dotenv_settings(root: Path) -> dict:
    """Read dotenv without shell or environment interpolation."""
    path = root / ".env"
    if not path.is_file():
        notice(".env가 없습니다. 내보낸 환경변수가 없으면 자동 시작하지 않습니다.")
        return {}
    try:
        return dotenv_values(path, interpolate=False)
    except (OSError, UnicodeError):
        notice(f".env를 읽을 수 없습니다. {MANUAL}")
        return {}


def caddy_settings(root: Path, values: dict | None = None) -> str | None:
    """Process variables override dotenv values; return only an enabled script path."""
    if values is None:
        values = dotenv_settings(root)
    enabled = os.environ.get("AI_EMS_CADDY_AUTOSTART", values.get("AI_EMS_CADDY_AUTOSTART"))
    if enabled is None:
        notice(f"AI_EMS_CADDY_AUTOSTART가 없습니다. true 또는 false를 지정하세요. {MANUAL}")
        return None
    if enabled.strip().lower() not in {"true", "false"}:
        notice(f"AI_EMS_CADDY_AUTOSTART는 true 또는 false여야 합니다. 자동 시작을 생략합니다. {MANUAL}")
        return None
    if enabled.strip().lower() == "false":
        notice(f"자동 시작이 꺼져 있습니다. {MANUAL}")
        return None
    script = os.environ.get("AI_EMS_CADDY_SCRIPT_WIN", values.get("AI_EMS_CADDY_SCRIPT_WIN"))
    if not script or not script.strip() or any(c in script for c in "\r\n\0"):
        notice(f"AI_EMS_CADDY_SCRIPT_WIN에 Windows PowerShell 스크립트 전체 경로를 지정하세요. {MANUAL}")
        return None
    return script.strip()


def powershell() -> str | None:
    found = shutil.which("powershell.exe")
    if found:
        return found
    # Windows interpreter location only; the user's Caddy script is never hard-coded.
    fallback = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    return str(fallback) if fallback.is_file() else None


class CaddySession:
    def __init__(self, script: str, root: Path = ROOT, *, listen_port: int = 18080):
        self.script, self.root = script, root
        self.listen_port = listen_port
        self.process: subprocess.Popen | None = None
        self.state: str | None = None

    def start(self) -> bool:
        engine = powershell()
        if not engine or not shutil.which("wslpath"):
            notice(f"Windows PowerShell/WSL 상호운용을 사용할 수 없습니다. {MANUAL}")
            return False
        try:
            bridge = subprocess.run(
                ["wslpath", "-w", str(self.root / "deploy/ollama-proxy/Manage-AgentProxy.ps1")],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.process = subprocess.Popen(
                [engine, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", bridge,
                 "-ListenPort", str(self.listen_port)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", start_new_session=True,
            )
            # Secrets travel via stdin only, never arguments, files, or logs.
            payload = {"script": self.script, "token": os.environ.get("OLLAMA_PROXY_TOKEN") or None}
            self.process.stdin.write(json.dumps(payload) + "\n")
            self.process.stdin.flush()
            ready: queue.Queue = queue.Queue()
            process = self.process
            threading.Thread(target=lambda: ready.put(process.stdout.readline(4096)), daemon=True).start()
            result = json.loads(ready.get(timeout=40))
            self.state = result["state"]
            if self.state == "started":
                notice("이번 실행에서 Caddy를 시작했습니다. 앱 종료 시 이 인스턴스만 정리합니다.")
                source = {"wsl_process": "WSL 프로세스", "windows_process": "Windows 프로세스"}
                notice(f"시작 토큰 출처: {source.get(result.get('token_source'), '미확인')}. API 연결은 별도로 검증합니다.")
                return True
            elif self.state == "reused":
                notice("기존 Caddy 프로세스를 보존합니다. 정상 연결 여부는 API 응답으로 별도 검증합니다.")
                self.close()
                return True
            else:
                reasons = {"occupied": "포트 점유 충돌입니다. 단일 IPv4 127.0.0.1 Caddy 리스너만 허용합니다. "
                                       "와일드카드·IPv6·다중 리스너·다른 프로세스는 재사용하지 않습니다.",
                           "invalid_path": "스크립트 경로가 없거나 올바른 전체 .ps1 경로가 아닙니다.",
                           "launch_failed": "실행 스크립트·Caddy 명령·OLLAMA_PROXY_TOKEN 설정을 확인하세요.",
                           "token_missing": "시작할 Windows/WSL 프로세스에 OLLAMA_PROXY_TOKEN이 없습니다.",
                           "token_invalid": "시작 토큰 형식이 유효하지 않습니다(소문자 16진수 64자리 필요).",
                           "timeout": "Caddy 시작 확인 시간이 초과되었습니다.",
                           "busy": "다른 실행에서 Caddy를 시작 중입니다."}
                notice(f"{reasons.get(self.state, 'Caddy 실행에 실패했습니다.')} {MANUAL}")
                self.close()
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, queue.Empty):
            notice(f"Windows Caddy 자동 시작을 확인할 수 없습니다. {MANUAL}")
            self.close()
        return False

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                try:
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
            output, _ = process.communicate(timeout=25)
            if self.state == "started":
                stopped = any(json.loads(line).get("state") == "stopped" for line in output.splitlines() if line.strip())
                if stopped and process.returncode == 0:
                    notice("이번 실행에서 시작한 Caddy를 정리했습니다.")
                else:
                    notice("Caddy 정리 완료를 확인하지 못했습니다. 수동 확인이 필요합니다.")
            if process.returncode:
                notice("Caddy 수명 관리가 정상 종료되지 않았습니다. 수동 확인이 필요합니다.")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            # Never use taskkill by name or kill an unverified Windows listener.
            notice("Caddy 정리 완료를 확인하지 못했습니다. 수동 확인이 필요합니다.")


def run_app(root: Path = ROOT, command: list[str] | None = None) -> int:
    values = dotenv_settings(root)
    script = caddy_settings(root, values)
    enabled = os.environ.get("AI_EMS_CADDY_AUTOSTART", values.get("AI_EMS_CADDY_AUTOSTART") or "false")
    if enabled.strip().lower() == "true" and not script:
        notice("자동 시작 설정 오류로 앱 실행을 중단합니다. 수동 실행 시 AUTOSTART=false를 사용하세요.")
        return 2
    if command is None:
        port = os.environ.get("AI_EMS_WEB_PORT", values.get("AI_EMS_WEB_PORT") or "8000")
        if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
            print("[Agent] AI_EMS_WEB_PORT는 1~65535 정수여야 합니다.", file=sys.stderr)
            return 2
        command = [sys.executable, "-m", "uvicorn", "web_app:app", "--host", "127.0.0.1",
                   "--port", str(int(port)), "--log-level", "info"]
    session = CaddySession(script, root) if script else None
    child = None
    previous = {}
    stop_deadline = None

    def stop(signum, _frame):
        nonlocal stop_deadline
        if child is not None and child.poll() is None:
            if stop_deadline is None:
                stop_deadline = time.monotonic() + APP_GRACE_SECONDS
                try:
                    child.send_signal(signum)
                except ProcessLookupError:
                    pass
        else:
            raise KeyboardInterrupt

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, stop)
    try:
        if session:
            if not session.start():
                notice("Caddy 자동 시작 실패로 앱 실행을 중단합니다(exit 3). AI 연결을 확인한 뒤 "
                       "수동 실행하려면 AUTOSTART=false를 사용하세요.")
                return 3
            report_llm(probe_llm(values))
        child = subprocess.Popen(command, cwd=root, start_new_session=True)
        while True:
            try:
                return child.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                if stop_deadline is not None and time.monotonic() >= stop_deadline:
                    notice("앱의 시그널 종료 대기 시간이 초과되었습니다. 소유한 앱 프로세스를 정리합니다.")
                    return 130
    except KeyboardInterrupt:
        return 130
    finally:
        # A second Ctrl+C must not interrupt the Windows ownership cleanup.
        for signum in previous:
            signal.signal(signum, signal.SIG_IGN)
        try:
            if child is not None and child.poll() is None:
                try:
                    child.terminate()
                    child.wait(timeout=APP_TERMINATE_SECONDS)
                except subprocess.TimeoutExpired:
                    child.kill()  # Only the app process created above.
                    try:
                        child.wait(timeout=APP_KILL_SECONDS)
                    except subprocess.TimeoutExpired:
                        notice("앱 강제 종료 완료를 확인하지 못했습니다. Caddy 정리를 계속합니다.")
                except ProcessLookupError:
                    pass
        finally:
            try:
                if session:
                    session.close()
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(run_app())
