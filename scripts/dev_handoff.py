#!/usr/bin/env python3
"""Generate a local, read-only Git handoff; never run tests or contact an API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ANSI_COLOR = re.compile(r"\x1b\[[0-9;]*m")
COUNT = r"[0-9]+ (?:passed|failed|skipped|deselected|xfailed|xpassed|warnings?|errors?|rerun)"
PYTEST_SUMMARY = re.compile(
    rf"(?:{COUNT}(?:, {COUNT})*|no tests ran)"
    r"(?: in [0-9]+(?:\.[0-9]+)?s(?: \([0-9]+(?::[0-9]{2}){1,2}\))?)?"
)
CONFLICTS = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def git(root: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> str:
    """Read Git without optional index writes, pagers, or external diff helpers."""
    result = subprocess.run(
        ["git", "--no-optional-locks", "--no-pager", "-c", "core.fsmonitor=false", *args],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=False, timeout=30,
    )
    if result.returncode not in allowed:
        # Do not copy arbitrary subprocess diagnostics into the handoff.
        raise RuntimeError(f"Git {args[0]} 조회 실패 (exit {result.returncode})")
    return result.stdout.decode("utf-8", errors="replace")


def inline(value: str) -> str:
    """Keep unusual Git filenames/refs on one Markdown line without markup."""
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    escaped = escaped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~])", r"\\\1", escaped)


def fence(value: str, language: str = "text") -> str:
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    delimiter = "`" * max(3, longest + 1)
    return f"{delimiter}{language}\n{value.rstrip()}\n{delimiter}"


def parse_status(raw: str) -> dict[str, list[str]]:
    """Parse porcelain v1 -z, including spaces, renames, and merge conflicts."""
    groups: dict[str, list[str]] = {
        "staged": [], "unstaged": [], "untracked": [], "unmerged": [],
    }
    records = iter(raw.split("\0"))
    for record in records:
        if not record:
            continue
        status, path = record[:2], record[3:]
        display = inline(path)
        if "R" in status or "C" in status:
            # With -z, destination precedes source, without an arrow separator.
            source = next(records)
            display = f"{inline(source)} → {display}"
        entry = f"- `{status}` {display}"
        if status == "??":
            groups["untracked"].append(entry)
        elif status in CONFLICTS:
            groups["unmerged"].append(entry)
        else:
            if status[0] != " ":
                groups["staged"].append(entry)
            if status[1] != " ":
                groups["unstaged"].append(entry)
    return groups


def test_results(log: Path | None) -> str:
    if log is None:
        return "미확인 — 테스트 로그 미지정. 이 스크립트는 테스트를 실행하지 않았습니다."
    if not log.is_file():
        raise ValueError("테스트 로그를 읽을 수 없습니다. 일반 파일 경로를 지정하세요.")
    summaries = []
    with log.open(encoding="utf-8", errors="replace") as stream:
        for number, line in enumerate(stream, 1):
            candidate = ANSI_COLOR.sub("", line).strip().strip("=").strip()
            if PYTEST_SUMMARY.fullmatch(candidate):
                # Only allowlisted counts, outcomes and durations leave the log.
                summaries.append(f"- 줄 {number}: `{candidate}`")
    heading = f"입력 로그: {inline(str(log))}\n\n"
    if not summaries:
        return heading + "미확인 — 인식 가능한 pytest 요약 없음. 테스트 성공을 가정하지 않습니다."
    return heading + "\n".join(summaries) + (
        "\n\n지정 파일에서 추출한 요약이며 이번 스크립트의 테스트 실행 결과가 아닙니다. "
        "여러 요약은 개별 표시하며 합산하지 않습니다. 실행 명령·시점·커밋 일치 및 "
        "로그 완전성은 미확인입니다."
    )


def build_handoff(root: Path, log: Path | None = None) -> str:
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1)).strip()
    commit = git(root, "rev-parse", "--verify", "HEAD").strip()
    status = parse_status(git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"))
    state = (root / "docs/dev/STATE.md").read_text(encoding="utf-8")
    sections = [
        "# AI-EMS 에이전트 인수인계",
        "## Git 현재 상태",
        f"- 브랜치: {inline(branch) if branch else 'DETACHED HEAD (브랜치 없음)'}\n"
        f"- 현재 커밋: `{commit}`\n"
        "- Git 읽기 전용 조회. 생성 시점의 상태이며 이후 변경은 반영되지 않습니다.",
    ]
    for label, entries in status.items():
        sections.extend([f"### {label} ({len(entries)})", "\n".join(entries) or "없음"])
    sections.append(
        "상태 코드는 Git porcelain의 XY(index/worktree)입니다. 한 파일이 staged와 "
        "unstaged 양쪽에 나타날 수 있으며 충돌은 unmerged로 별도 표시합니다."
    )
    for label, flags in (("staged", ("--cached",)), ("unstaged", ())):
        stat = git(
            root, "diff", *flags, "--no-ext-diff", "--no-textconv", "--no-color",
            "--stat=1000,800", "--", ".",
        ).strip()
        sections.extend([f"## {label} diff 통계", fence(stat) if stat else "없음"])
    sections.extend([
        "untracked 파일은 위 목록에만 표시되며 Git diff 통계에는 포함되지 않습니다. "
        "파일 본문과 diff 본문은 수집하지 않습니다.",
        "## 선택한 테스트 로그의 검증 결과",
        test_results(log),
        "## docs/dev/STATE.md (저장된 상태 문서)",
        "아래 내용의 과거 검증과 현재 작업을 구분하세요. 위 Git 조회나 로그 추출이 "
        "상태 문서의 검증 결과를 새로 입증하지는 않습니다.",
        fence(state, "markdown"),
    ])
    return "\n\n".join(sections) + "\n"


def write_handoff(root: Path, markdown: str) -> Path:
    target = root / ".local/ai-handoff.md"
    # Refuse links so the fixed output cannot overwrite another file/location.
    if target.parent.is_symlink() or target.is_symlink():
        raise ValueError("출력 경로의 심볼릭 링크는 지원하지 않습니다.")
    if git(root, "ls-files", "--", ".local/ai-handoff.md").strip():
        raise ValueError("출력 파일이 이미 Git 추적 대상입니다. 저장하지 않았습니다.")
    if not git(root, "check-ignore", "--", ".local/ai-handoff.md", allowed=(0, 1)).strip():
        raise ValueError("출력 경로가 Git에서 제외되지 않았습니다. 저장하지 않았습니다.")
    target.parent.mkdir(exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write .local/ai-handoff.md")
    parser.add_argument("--test-log", type=Path, help="pytest log (relative to the caller's directory)")
    args = parser.parse_args(argv)
    try:
        root = Path(git(ROOT, "rev-parse", "--show-toplevel").strip())
        if root.resolve() != ROOT:
            raise ValueError("스크립트가 저장소 루트의 scripts/ 아래에 있어야 합니다.")
        markdown = build_handoff(root, args.test_log)
        if args.write:
            target = write_handoff(root, markdown)
            print(f"인수인계 저장: {target}", file=sys.stderr)
        else:
            sys.stdout.write(markdown)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        # Avoid dumping log contents, Git stderr, or potentially sensitive paths.
        message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
        print(f"인수인계 생성 실패: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
