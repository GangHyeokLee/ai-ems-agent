"""Where the Chrome regression tests keep their data, and how a Chrome run is set up and cleaned up.

Test data stays in the project, outside Git (``/.local/`` in .gitignore):

    .local/browser-tests/profiles/     Chrome user-data directories (Linux Chrome)
    .local/browser-tests/screenshots/  screenshots, one folder per suite
    .local/browser-tests/logs/         probe results, Chrome stderr and web app logs, per suite

Windows Chrome started from WSL cannot keep its profile in the project: on the \\\\wsl.localhost share
Chrome's LockFileEx calls fail ("Incorrect function"), it cannot grant its network sandbox access to
the cache directories, and its SQLite databases report "database is locked". Windows Chrome profiles
therefore live in the Windows user's own temp folder
(``%LOCALAPPDATA%\\Temp\\ai-ems-agent\\browser-tests\\profiles``), never in C:\\Windows\\Temp, while its
screenshots and logs are still collected in the project.

Every run gets a new directory named ``ai-ems-<random>`` (kept short: Chrome's own files inside a long
profile path exceed the Windows 260-character limit; the suite and label go to the Chrome log). When the
run ends, or is interrupted, only Chrome processes whose command line contains that unique name are
stopped, and only that directory is removed; other Chrome profiles and sessions are never touched.
"""
from __future__ import annotations

import base64
import functools
import os
import re
import shutil
import subprocess
import time
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / ".local" / "browser-tests"
PROFILES_DIR = DATA_DIR / "profiles"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
LOGS_DIR = DATA_DIR / "logs"
POWERSHELL = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
WINDOWS_PROFILES = r"Temp\ai-ems-agent\browser-tests\profiles"  # under %LOCALAPPDATA%
SESSION = f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"  # one log file per suite and test session


def powershell(script: str, timeout: float = 60) -> str:
    """Run a PowerShell script from WSL; -EncodedCommand avoids every quoting layer in between."""
    script = "$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script
    done = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-EncodedCommand",
                           base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                          cwd="/mnt/c", capture_output=True, timeout=timeout)
    return done.stdout.decode("utf-8-sig", "replace").strip()


@functools.lru_cache(maxsize=None)
def windows_profiles_root() -> tuple[str, Path]:
    """The Windows Chrome profile root as (Windows path, the same folder seen from WSL)."""
    local = powershell("$env:LOCALAPPDATA")
    if not re.fullmatch(r"[A-Za-z]:\\[^\r\n%]+", local):
        raise RuntimeError(f"Windows %LOCALAPPDATA% is unavailable: {local!r}")
    windows = local.rstrip("\\") + "\\" + WINDOWS_PROFILES
    mapped = subprocess.run(["wslpath", "-u", windows], capture_output=True, text=True, timeout=30).stdout.strip()
    if not mapped.startswith("/"):
        raise RuntimeError(f"{windows} cannot be reached from WSL")
    return windows, Path(mapped)


def suite_dir(kind: Path, suite: str) -> Path:
    """screenshots/<suite> or logs/<suite>, created on demand."""
    path = kind / suite
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path(suite: str, name: str) -> Path:
    """A log file of this test session, e.g. logs/<suite>/web_app-<session>.log."""
    return suite_dir(LOGS_DIR, suite) / f"{name}-{SESSION}.log"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "run"


@dataclass
class ChromeRun:
    """One Chrome launch: its own profile and screenshot file, in paths Chrome and WSL understand."""
    run_id: str
    label: str
    windows: bool
    directory: Path        # the run directory as seen from WSL/Linux
    profile: str           # --user-data-dir value for Chrome
    screenshot: str        # --screenshot value for Chrome
    screenshot_file: Path  # the same file as seen from WSL/Linux

    def stop_processes(self) -> int:
        """Stop only the Chrome processes started with this run's directory; returns how many."""
        if self.windows:
            # Terminating the WSL interop process does not stop Windows chrome.exe.
            count = powershell(
                "$p=@(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                f"Where-Object {{ $_.CommandLine -like '*{self.run_id}*' }});"
                "$p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };$p.Count")
            return int(count) if count.isdigit() else 0
        return 0 if subprocess.run(["pkill", "-f", "--", self.run_id], capture_output=True).returncode else 1

    def remove(self) -> None:
        """Remove this run's directory, stopping its own leftover Chrome processes if they hold it."""
        for attempt in range(8):
            try:
                shutil.rmtree(self.directory)
                return
            except OSError:
                # Python 3.11 rmtree also raises FileNotFoundError for an entry vanishing under it
                # (e.g. a lockfile closed by a dying Chrome); only a missing directory means done.
                if not self.directory.exists():
                    return
                if attempt == 1:
                    self.stop_processes()
                time.sleep(0.5 * (attempt + 1))
        if self.directory.exists():
            warnings.warn(f"Chrome test profile left behind: {self.directory}", stacklevel=2)


@contextmanager
def chrome_run(chrome: tuple[Path, bool], suite: str, label: str):
    """A fresh run directory (profile + screenshot target) that is removed with its processes afterwards."""
    windows = chrome[1]
    run_id = f"ai-ems-{uuid.uuid4().hex[:12]}"
    if windows:
        root_windows, root = windows_profiles_root()
        directory = root / run_id
        prefix = f"{root_windows}\\{run_id}\\"  # Chrome gets the Windows path, WSL uses the mapped one
    else:
        directory = PROFILES_DIR / run_id
        prefix = f"{directory}/"
    (directory / "user-data").mkdir(parents=True)
    run = ChromeRun(run_id, f"{_slug(suite)}/{_slug(label)}", windows, directory, prefix + "user-data",
                    prefix + "screenshot.png", directory / "screenshot.png")
    try:
        yield run
    except BaseException:
        # Timeout, Ctrl-C or a failed probe: the interop process is gone but chrome.exe may still
        # hold the profile, so stop this run's processes before removing its directory.
        if windows:
            run.stop_processes()
        raise
    finally:
        run.remove()


def _log_run(log: Path, run: ChromeRun, url: str, attempt: int, exit_code, seconds: float, stderr: bytes) -> None:
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"=== {datetime.now():%Y-%m-%d %H:%M:%S} {run.run_id} {run.label} attempt={attempt + 1} "
                     f"exit={exit_code} seconds={seconds:.1f}\n{url}\n{stderr.decode('utf-8', 'replace')}\n")


def run_chrome_page(chrome: tuple[Path, bool], url: str, *, suite: str, label: str, screenshot: Path | None = None,
                    window: str = "1600,1200", budget: int | None = 600000, extra=(), attempts: int = 5,
                    pause: float = 2.0, timeout: float = 600) -> str:
    """Load url in headless Chrome and return the DOM, or save a screenshot to `screenshot` and return "".

    `budget` is the headless virtual-time budget in ms; None runs in real time (the DOM is dumped at the
    load event), which is the only way to measure real script and rendering cost.

    Each attempt uses a new profile. Retries cover harness start-up only: WSL2 localhost forwarding
    can lag behind a newly bound port, and a fresh Chrome profile can fail its first launch.
    Chrome's stderr is appended to logs/<suite>/chrome-<session>.log.
    """
    path, windows = chrome
    log = log_path(suite, "chrome")
    done = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(pause)
        with chrome_run(chrome, suite, label) as run:
            args = [str(path), "--headless=new", f"--user-data-dir={run.profile}", "--no-first-run", f"--window-size={window}",
                    *([f"--virtual-time-budget={budget}"] if budget else []), *extra,
                    f"--screenshot={run.screenshot}" if screenshot is not None else "--dump-dom", url]
            started = time.monotonic()
            try:
                done = subprocess.run(args, cwd="/mnt/c" if windows else None, capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                _log_run(log, run, url, attempt, "timeout", time.monotonic() - started, error.stderr or b"")
                raise  # chrome_run() stops this run's processes before removing the profile
            _log_run(log, run, url, attempt, done.returncode, time.monotonic() - started, done.stderr)
            if b"ERR_CONNECTION_REFUSED" in done.stderr:
                continue
            if screenshot is not None:
                # The run directory is new, so a file here can only come from this run.
                if run.screenshot_file.is_file():
                    screenshot.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(run.screenshot_file), screenshot)
                    return ""
            elif done.stdout:
                return done.stdout.decode("utf-8", "replace")
    raise RuntimeError(f"Chrome produced no output (see {log}): {(done.stderr if done else b'')[-500:]!r}")
