#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    AGENT_PYTHON="$PROJECT_ROOT/.venv/bin/python"
else
    AGENT_PYTHON="$(command -v python3 || command -v python || true)"
fi
if [[ -z "$AGENT_PYTHON" ]]; then
    echo 'Python을 찾을 수 없습니다. 가상환경과 requirements.txt를 먼저 설치하세요.' >&2
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$AGENT_PYTHON" "$PROJECT_ROOT/scripts/run_agent.py"
