# ai-ems-agent

Local LLM + LangGraph + PyPowSyBl 기반 **AI-EMS Agent PoC** 프로젝트.

이 프로젝트의 목적은 LLM이 전력계통 물리해석을 대체하는 것이 아니라, 사용자의 자연어 요청을 해석하고 적절한 PyPowSyBl 기반 Tool을 선택·호출한 뒤 실제 계산 결과를 운영자 관점에서 설명하는 흐름을 검증하는 것이다.

> **AI는 물리해석 엔진을 대체하지 않는다.**
> LLM은 자연어 의도 해석과 Tool orchestration을 담당하고, 계통 상태와 제어 효과는 Security Analysis, Sensitivity Analysis, AC Load Flow 등 물리해석 결과로 검증한다.

---

## Current Capabilities

### Power-system analysis

- KPG-193 기반 PyPowSyBl network load
- AC Load Flow
- Line N-1 Security Analysis
- Generator N-1 Security Analysis
- Generator N-1 full-batch screening
- Single / Distributed slack 발전기 사고 비교
- 발전기 N-1 balancing 메커니즘 분석
- pre / post contingency 위반 비교
- Generator Sensitivity Analysis
- line-outage Sensitivity 기반 balanced redispatch 후보 생성
- generator-outage post-contingency Sensitivity 기반 balanced redispatch 후보 생성
- 동일 Bus pair 중복 후보 제거
- 발전기 출력 범위 guardrail
- Redispatch 후 AC Power Flow 검증
- PyPowSyBl Operator Strategy 기반 whole-network Security 재검증
- 발전기 사고 후 Security → Sensitivity → Redispatch 후보 → AC/Security 재검증 workflow

### AI / Agent

- 자연어 요청 → Tool 선택
- LangGraph ToolNode 기반 Local LLM Tool Calling
- line-outage corrective-action workflow
- generator-outage 개별 분석 / slack 비교 / batch screening
- generator-outage corrective-action high-level workflow
- 발전기 N-1 Screening 결과를 이용한 멀티턴 후속 분석
- 고위험 계통 결과의 deterministic formatter
- Tool error의 자연어 설명
- 멀티턴 분석 요청
- 몽골 합성 AC PF 결과 읽기 전용 질의응답 (별도 Agent·Tool, [사양](docs/specs/MONGOLIA_PF_CHAT_SPEC.md))
- 몽골 합성 계통 선로 N-1 Security Analysis와 계통 분리 표시 (PyPowSyBl XIIDM, [사양](docs/specs/MONGOLIA_SECURITY_ANALYSIS.md))
- 몽골 계통 분리 없는 N-1 후 선로 조류 Sensitivity (유한차분 검증, [사양](docs/specs/MONGOLIA_SENSITIVITY_ANALYSIS.md))

### Web UI

- FastAPI 기반 Web UI
- KPG 계통 지도
- 선로 / 버스 / 발전기 선택
- Security / Sensitivity 직접 실행
- Generator Contingency 직접 실행
- 분석 패널 접기 / 펼치기
- 사고·위반·민감도·발전기 변화 시각화
- AI 채팅 Markdown 렌더링

### Physics API

Agent와 별도로 외부 Simulator / EMS Tester가 LLM 없이 물리해석 기능을 호출할 수 있는 REST API를 제공한다.

```text
External Simulator
      ↓ HTTP
AI-EMS Physics API
      ↓
Public API Adapter / Response Schema
      ↓
PyPowSyBl Domain Tools
```

---

## Architecture

```text
src/ai_ems/
├─ network.py
├─ config.py
├─ utils/
│  └─ kpg_powsybl_adapter.py
├─ tools/
│  ├─ network_tools.py
│  ├─ security_tools.py
│  ├─ generator_contingency_tools.py
│  ├─ generator_screening_tools.py
│  ├─ sensitivity_tools.py
│  ├─ control_tools.py
│  ├─ generator_control_tools.py
│  └─ workflow_tools.py
├─ agent/
│  ├─ graph.py
│  ├─ tools.py
│  └─ formatters/
│     ├─ __init__.py
│     ├─ common.py
│     ├─ contingency.py
│     ├─ generator_contingency.py
│     ├─ generator_comparison.py
│     ├─ generator_screening.py
│     └─ generator_response.py
└─ api/
   ├─ app.py
   ├─ routes.py
   ├─ schemas.py
   └─ adapters.py
```

### Domain Layer

LLM과 독립적인 Python / PyPowSyBl 계층이다.

- `network.py`: 계통 load 및 공통 AC Load Flow
- `network_tools.py`: 계통 요약 / 선로 / 발전기 조회
- `security_tools.py`: line / generator contingency Security Analysis 지원 기능
- `generator_contingency_tools.py`: 발전기 탈락 AC Load Flow, slack balancing 비교 및 Security Analysis
- `generator_screening_tools.py`: 연결 발전기 전체 N-1 batch screening
- `sensitivity_tools.py`: line / generator outage 이후 발전기 injection 변화에 대한 선로 유효전력 조류 민감도
- `control_tools.py`: line-outage balanced redispatch 후보 / 제약 guardrail / AC·Security 재검증
- `generator_control_tools.py`: generator-outage Sensitivity 기반 Redispatch 후보 생성 / 후보 다양화 / AC·Security 재검증
- `workflow_tools.py`: line-outage 및 generator-outage Security → Sensitivity → Redispatch 후보 → 물리 재검증 workflow

### Agent Layer

`src/ai_ems/agent/tools.py`는 Domain function을 LLM-facing Tool schema로 노출한다.

현재 Agent Tool:

```text
network_summary
line_list
line_detail
generator_list
line_contingency
generator_contingency_analysis
generator_contingency_comparison
generator_contingency_screening
generator_sensitivity
balanced_redispatch_validation
contingency_response_analysis
generator_outage_response_analysis
```

고위험 계산 결과는 LLM이 수치를 다시 자유롭게 해석하지 않도록 deterministic formatter를 사용한다.

```text
User
 ↓
LLM: intent / Tool selection
 ↓
Physical-analysis Tool or High-level Workflow Tool
 ↓
Structured Result
 ↓
Deterministic Formatter
 ↓
Operator-facing Response
```

발전기 사고 대응방안 분석에서는 Agent가 `generator_outage_response_analysis`를 선택하고, 해당 고수준 Tool 내부 workflow에서 Security Analysis, Sensitivity Analysis, Redispatch 후보 생성, AC Load Flow 및 whole-network Security 재검증을 수행한다.

### Public Physics API Layer

현재 endpoint:

```text
GET  /api/health
POST /api/v1/security-analysis
POST /api/v1/sensitivity-analysis
POST /api/v1/redispatch-validation
POST /api/v1/contingency-response
```

세부 계약은 [`docs/specs/API_SPEC.md`](docs/specs/API_SPEC.md)를 참고한다.

---

## Main Workflows

### Line-outage corrective-action workflow

```text
Line Contingency
      ↓
Security Analysis
      ↓
Most Severe Violation Selection
      ↓
Sensitivity Analysis
      ↓
Balanced Redispatch Candidate Generation
      ↓
Generator Feasibility Guardrail
      ↓
AC Validation
      ↓
Whole-network Security Re-validation
      ↓
Candidate Ranking
```

`best_tested_candidate`는 **시험한 후보 중 최선의 결과**이며 OPF / SCED 기반 최적해를 의미하지 않는다.

### Generator-outage analysis workflow

```text
Specific Generator Outage
      ↓
Single or Distributed Slack AC Load Flow
      ↓
Security Analysis
      ↓
Deterministic Result Explanation
```

Single / Distributed slack은 Load Flow balancing 가정이며 운영자 Redispatch가 아니다.

### Generator-outage corrective-action workflow

```text
Specific Generator Outage
      ↓
Generator Security Analysis
      ↓
Most Severe Overloaded Line Selection
      ↓
Post-contingency Generator Sensitivity Analysis
      ↓
Balanced Redispatch Candidate Generation
      ↓
Same Bus-pair Duplicate Filtering
      ↓
AC Load Flow Validation
      ↓
Whole-network Security Re-validation
      ↓
Validated Candidate Ranking
```

기본 제어 후보는 한 발전기 `+10 MW`, 다른 발전기 `-10 MW`의 balanced Redispatch이며, 민감도는 후보 우선순위 선별에 사용한다. 실제 효과는 AC Load Flow와 whole-network Security Analysis로 다시 검증한다.

`best_tested_candidate`는 **시험한 후보 중 최선의 결과**이며 OPF / SCED 기반 최적해나 위반 해소를 보장하는 제어안이 아니다.

### Generator N-1 batch screening

```text
All Connected Generators
      ↓
Independent Generator N-1 Contingencies
      ↓
AC Security Analysis
      ↓
Physics-first Deterministic Ranking
      ↓
Top-N Review Candidates
```

Screening ranking은 AI score나 고장 확률이 아니라 물리해석 결과 기반 **review priority**다.

Screening 결과는 같은 대화의 후속 요청에서 특정 순위의 발전기 사고 상세분석 또는 대응방안 분석으로 연결할 수 있다.

---

## Environment

Python 3.11 기준.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

프로젝트 설정은 repository root의 `.env`를 사용하며 `src/ai_ems/config.py`가 자동 로드한다.

주요 설정:

```dotenv
AI_EMS_MODEL=qwen3.5:9b
AI_EMS_LLM_BASE_URL=http://127.0.0.1:11434
AI_EMS_CASE_FILE=data/KPG193_ver2_0_powsybl_full.mat
AI_EMS_BUS_LOCATION_FILE=data/bus_location.csv
AI_EMS_WEB_HOST=127.0.0.1
AI_EMS_WEB_PORT=8000
AI_EMS_PHYSICS_HOST=127.0.0.1
AI_EMS_PHYSICS_PORT=8001
```

---

## Run

### CLI Agent

```bash
python app.py
```

### Agent Web UI

```bash
./run_agent.sh
```

브라우저:

```text
http://127.0.0.1:8000/
```

### Standalone Physics API

```bash
./run_api.sh
```

Swagger UI:

```text
http://127.0.0.1:8001/docs
```


### Bearer-authenticated local Ollama proxy (Windows)

Windows Caddy listens only on 127.0.0.1:18080 and forwards only GET /api/tags
and POST /api/chat to the fixed upstream 127.0.0.1:11434.
The installed ChatOllama uses POST /api/chat for sync, async, streaming and
tool calls; model validation on initialization is not enabled.
Other paths/methods, including model management and /api/generate, return 403.
Missing/invalid Bearer credentials return 401 before any upstream request.

Install Windows Caddy and copy the repository's `deploy/ollama-proxy` sources to a
Windows directory of your choice. The PowerShell launcher may be renamed; keep its
supporting module beside it. Add these two entries to the existing local `.env`,
keeping all other settings, and specify the full Windows path:

```ini
AI_EMS_CADDY_SCRIPT_WIN=%LOCALAPPDATA%\YourApp\Proxy\launch.ps1
AI_EMS_CADDY_AUTOSTART=false
```

Replace the example with your own path and filename. The path is configuration,
not authentication or access control. Keep `.env` out of Git. Existing LLM
credentials and other project settings remain unchanged. Do not put real tokens
in source, documentation, script arguments, or runtime-config logs.

To enable automatic startup, set `AI_EMS_CADDY_AUTOSTART=true`, then run:

```bash
./run_agent.sh
```

The web app stays on WSL loopback; `AI_EMS_WEB_PORT` selects its port (default
8000). For example, use `AI_EMS_WEB_PORT=8010 ./run_agent.sh` to avoid an app
already using the default port.

The launcher parses `.env` with `python-dotenv` as data (`interpolate=False`),
never `source` or `eval`. Exported process variables override the two dotenv
settings. `%LOCALAPPDATA%` and other Windows environment variables are expanded
by PowerShell with `ExpandEnvironmentVariables`; the path is invoked literally.
With autostart disabled or unspecified, the web app remains available for manual
proxy operation. With `AI_EMS_CADDY_AUTOSTART=true`, invalid settings stop the
launcher with exit code 2; failed Windows interop, missing/invalid server token,
port conflict or Caddy startup failure stops it with exit code 3 **before Uvicorn
starts**. An owned proxy is cleaned up on failure. Set `false` to use your usual
foreground PowerShell launcher manually. In PowerShell, after setting the
configured path as a process variable, invoke any chosen filename safely:

```powershell
& ([Environment]::ExpandEnvironmentVariables($env:AI_EMS_CADDY_SCRIPT_WIN))
```

A new proxy needs `OLLAMA_PROXY_TOKEN` in the launching Windows process or WSL
process (64 lowercase hex characters, generated with a cryptographic RNG).
An explicit WSL process token takes precedence and travels over stdin; otherwise
the Windows PowerShell process's existing token is used. Only the source name is
reported. This adds no credential store, registry lookup, command-line secret or
plaintext token file. A running Caddy needs no server token to be reused; client
credentials are checked separately. Use your approved method to inject process
credentials; never save real tokens to Git.

The complete TCP listener list for port 18080 must contain exactly one entry,
bound to IPv4 `127.0.0.1`, owned by Caddy before reuse is allowed. Wildcard
`0.0.0.0`/`::`, IPv6 `::1`, other interface addresses, and multiple entries
(including a valid IPv4 entry mixed with any other listener) return `occupied`.
The same address check runs after a new proxy starts, before reporting `started`.
A rejected owned proxy is cleaned up; existing processes and configurations are
preserved, and no alternate port is selected automatically.

A Caddy listener is only a reuse candidate. After startup/reuse, the launcher
requests `/api/tags` at the existing `AI_EMS_LLM_BASE_URL`, using the app's shared
Bearer/Cloudflare authentication rules and existing dotenv/process credentials.
Redirects are not followed. HTTP 200 must contain an Ollama `models` list.
HTTP 401/403, gateway/upstream 502, connection failure, timeout and invalid
configuration/response are reported separately, without URLs, headers or bodies.
These API failures leave the web UI available with an explicit warning that AI
may be unavailable. They do not trigger a Caddy restart, including while Ollama
is temporarily down. The configured URL may be remote: its successful response
validates that route, not the local proxy's routing configuration.

On Ctrl+C, SIGTERM, normal exit or application failure, only a Caddy descendant
started by this invocation is cleaned up using its retained process handle.
Existing Caddy and other listeners are never terminated. The app gets 10 seconds
to handle its first signal, then 5 seconds after SIGTERM and up to 5 seconds after
SIGKILL; the controller cleanup wait is bounded to 25 seconds (about 46 seconds
total including polling). App termination errors still enter Caddy cleanup.
A controller timeout produces a manual-check warning instead of killing an
unverified process. Abrupt host/WSL shutdown or SIGKILL of the launcher cannot
guarantee cleanup. No name-based `taskkill` or Windows installation changes occur.

The launcher refuses missing/invalid tokens before starting Caddy.
Runtime JSON containing the token is sent via stdin only.
Admin API, config persistence, automatic HTTPS and runtime logging are disabled;
access logging is not configured. Responses flush at 10ms intervals.
Stop the foreground launcher with Ctrl+C. No Windows service is installed.

AI-EMS clients can set these process environment variables:

```dotenv
AI_EMS_LLM_BASE_URL=http://127.0.0.1:18080
AI_EMS_LLM_API_KEY=<same-token>
```

AI_EMS_LLM_API_KEY sends Authorization: Bearer on both sync/async clients.
Remote authentication requires HTTPS. The only HTTP exception is the exact
local test URL http://127.0.0.1:18080 (optional trailing slash).
localhost aliases, other ports, userinfo and remote HTTP are rejected.
Set neither authentication mode to preserve existing unauthenticated local calls.
Bearer and Cloudflare credentials cannot be combined.
The proxy removes Authorization before forwarding to Ollama.

This stage does not enable Tailscale Serve/Funnel, change OLLAMA_HOST, open
firewall/router ports, or expose AI-EMS ports 8000/8001.
WSL localhost differs from Windows localhost under NAT. Keep the deployment's
existing Tailscale HTTPS 8443 route in `.env`; these tests do not change it.
The user confirmed `/api/tags` HTTP 200 while AI-EMS owns a running proxy and
HTTP 502 after Ctrl+C stops it. The latter is expected with Caddy absent.
A 502 during an Ollama update is also an API/upstream condition; listener
addresses are checked separately through Windows TCP state.

Final local launcher security validation (Windows PowerShell 5.1 + WSL2, 2026-10-21):

```bash
.venv/bin/python tests/windows_caddy_lifecycle_probe.py
.venv/bin/python -m pytest -q tests
AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests
```

The actual WSL process had no `OLLAMA_PROXY_TOKEN`; a fresh Windows PowerShell
process inherited a valid-format token. With no listener on an isolated Windows
port, the real launcher used that Windows credential, received Ollama tags HTTP
200, started real Uvicorn, and cleaned its owned Caddy after Ctrl+C. The existing
WSL `.env` LLM route initially returned HTTP 200 with an Ollama `models` list.
Agent-run checks observed HTTP 502 with no default listener and HTTP 403 after
startup, including in this final probe. Earlier focused checks returned HTTP 200
from Windows localhost using the existing dotenv client key; in-memory comparison
confirmed that key matches the Windows-inherited server token. The agent-run
HTTPS 403 remains distinct from the user's subsequently confirmed 200/502
lifecycle above. It does not identify a listener binding failure. No credential
storage or network settings were changed to work around it.

Binding validation executes the production PowerShell `Listener` function with
12 mocked address lists. Whole-manager tests use a real Caddy PID to verify all
10 rejected address combinations cannot enter reuse, preserving that process.
Three post-start tests inject wildcard/IPv6/mixed snapshots while actual Caddy
stays bound to loopback, then verify rejection and owned-process cleanup.
No wildcard or externally reachable test listener is opened.

Real Windows cases cover existing-Caddy reuse, missing/invalid credentials,
missing path, another process occupying the port, Bearer rejection (401), the
unchanged API allowlist (403), and unavailable upstream (502) without restarting
Caddy. The 502 case uses a temporary proxy pointing to a closed port; the real
Ollama process stays running. A real child that ignores SIGINT/SIGTERM verifies
bounded escalation and owned Windows Caddy cleanup; unit tests cover both signal
entries and the UI continuation policy after API failures.

`./run_agent.sh` was checked both with an existing default listener and with
that port free, using the Windows-inherited token. Reuse preserves the listener's
PID/start time through Ctrl+C and app failure. Isolated ports also exercise owned
startup with the same Python launcher, keeping baseline listeners untouched.
Probe scripts are temporary source copies; installed Windows files, credential
storage and Tailscale settings stay unchanged.
Generated test tokens remain in memory and captured logs are checked for leaks.

Results: Windows lifecycle probe **20 PASS** checks; default pytest **381 passed,
12 skipped**; browser-enabled pytest **393 passed**. Both pytest runs reported
one existing Starlette/AnyIO deprecation warning. The skipped tests in the default
run are the opt-in browser suite. The probe reports configured-route failures as
observations separately from its lifecycle assertions.

### Remote Ollama via Cloudflare Tunnel + Access

Run AI-EMS Web UI and Physics API on each user's localhost.
Only Windows Ollama on the GPU PC is shared remotely.

Run cloudflared on Windows with origin http://127.0.0.1:11434 and
origin HTTP Host header localhost:11434. Do not add routes for ports 8000/8001.
Cloudflare account/domain configuration, cloudflared installation, Tunnel
creation/startup, and automatic startup are separate system operations.
Before enabling the public route, protect the entire Ollama hostname with
an Access self-hosted application and a **Service Auth** policy allowing
the selected Service Token.

Set these in each client's ignored local .env or process environment:

```dotenv
AI_EMS_LLM_BASE_URL=https://ollama.example.com
AI_EMS_MODEL=qwen3.5:9b
AI_EMS_CF_ACCESS_CLIENT_ID=<service-token-client-id>
AI_EMS_CF_ACCESS_CLIENT_SECRET=<service-token-client-secret>
```

Replace the example hostname and restart AI-EMS after configuration changes.
The two Access variables become CF-Access-Client-Id and CF-Access-Client-Secret
headers on both synchronous and asynchronous ChatOllama requests.
If both are unset/empty, existing local behavior is preserved.
Setting only one raises an error; credentials require an HTTPS base URL.
Existing process environment variables take precedence over .env.

Keep real credentials in the ignored local .env or secret environment variables.
Never put them in .env.example, code, logs, or command-line URLs.
Access Service Tokens and Tunnel connector tokens are separate credentials.

Local tests verify authenticated and unauthenticated sync/async requests using
mock HTTP transport. After Tunnel/Access setup, verify rejection of missing
and invalid tokens, then test model listing, inference, AI-EMS tool calls and
long responses with valid credentials. Check latency under concurrent GPU use.

References:
- [Ollama Tunnel configuration](https://docs.ollama.com/faq)
- [Cloudflare Service Tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)

### Full-batch study

```bash
python scripts/run_kpg193_full_batch.py \
  --generator-only \
  --generator-slack distributed \
  --chunk-size 100 \
  --no-sensitivity
```

`contingency_summary.csv`에는 deterministic review ranking이 포함되어 있으므로 별도 중복 ranking CSV는 생성하지 않는다. Sensitivity 결과가 없으면 빈 `topn_sensitivity.csv`도 생성하지 않는다.

---

## Test

Regression test:

```bash
python -m pytest -q
```

최신 검증 상태:

```text
59 passed
```

브라우저 지연 응답 회귀 테스트 (KPG·몽골 Security/Sensitivity, 계통 전환; Chrome 필요, opt-in):

```bash
AI_EMS_BROWSER_TESTS=1 python -m pytest -q tests/test_browser_stale_responses.py
```

Chrome은 `AI_EMS_CHROME` 또는 WSL의 Windows Chrome / PATH의 Chrome·Chromium을 사용한다.
결과(`results.json`)와 스크린샷은 `outputs/browser_stale_responses/`에 저장된다.
같은 명령으로 KPG·몽골 공통 UI 비교(`tests/browser/unified_ui_probe.js`, [사양](docs/specs/MULTI_GRID_UI.md))도 실행되며 결과는 `outputs/browser_unified_ui/`에 저장된다.
테마·도움말 검증(`tests/browser/theme_help_probe.js`, [사양](docs/specs/THEME_AND_HELP.md)) 결과는 `outputs/browser_theme_help/`에 저장된다.

실제 KPG case smoke test:

```bash
python tests/smoke_test.py
```

실험용 probe 스크립트는 `tests/`와 repository root에 남아 있으며, 자동 regression test와 달리 특정 통합 흐름을 수동 확인하는 용도다.

---

## Documentation

### Specifications

- [Physics API Specification](docs/specs/API_SPEC.md)
- [Physics Module Specification](docs/specs/MODULE_SPEC.md)
- [KPG-193 PyPowSyBl Adapter Specification](docs/specs/KPG_POWSYBL_ADAPTER_SPEC.md)
- [몽골 합성 AC PF 읽기 전용 AI 질의응답](docs/specs/MONGOLIA_PF_CHAT_SPEC.md)
- [몽골 합성 계통 선로 N-1 Security Analysis](docs/specs/MONGOLIA_SECURITY_ANALYSIS.md)
- [몽골 합성 계통 N-1 후 선로 조류 Sensitivity](docs/specs/MONGOLIA_SENSITIVITY_ANALYSIS.md)
- [멀티 계통 공통 UI (KPG·몽골)](docs/specs/MULTI_GRID_UI.md)
- [Light/Dark 테마 · 모델/AI Assistant 도움말](docs/specs/THEME_AND_HELP.md)

### Experiment Records

- [KPG-193 기반 PyPowSyBl 계통해석 및 제어 후보 검증](docs/experiments/KPG-193%20기반%20PyPowSyBl%20계통해석%20및%20제어%20후보%20검증.md)
- [PyPowSyBl Physics API 및 AI-EMS Agent 연계 PoC](docs/experiments/PyPowSyBl%20Physics%20API%20%EB%B0%8F%20AI-EMS%20Agent%20%EC%97%B0%EA%B3%84%20PoC.md)
- [KPG-193 발전기 N-1 Single/Distributed Slack 비교](docs/experiments/KPG-193%20%EB%B0%9C%EC%A0%84%EA%B8%B0%20N-1%20SingleDistributed%20Slack%20%EB%B9%84%EA%B5%90.md)
- [KPG-193 발전기 N-1 Slack 보상 메커니즘 분석](docs/experiments/KPG-193%20%EB%B0%9C%EC%A0%84%EA%B8%B0%20N-1%20Slack%20%EB%B3%B4%EC%83%81%20%EB%A9%94%EC%BB%A4%EB%8B%88%EC%A6%98%20%EB%B6%84%EC%84%9D.md)
- [KPG-193 발전기 사고 후 민감도 기반 Redispatch 후보 생성 및 재검증](docs/experiments/KPG-193%20%EB%B0%9C%EC%A0%84%EA%B8%B0%20%EC%82%AC%EA%B3%A0%20%ED%9B%84%20%EB%AF%BC%EA%B0%90%EB%8F%84%20%EA%B8%B0%EB%B0%98%20Redispatch%20%ED%9B%84%EB%B3%B4%20%EC%83%9D%EC%84%B1%20%EB%B0%8F%20%EC%9E%AC%EA%B2%80%EC%A6%9D.md)
- [AI Agent 기반 발전기 N-1 Screening 및 대응방안 Tool Orchestration](docs/experiments/AI%20Agent%20%EA%B8%B0%EB%B0%98%20%EB%B0%9C%EC%A0%84%EA%B8%B0%20N-1%20Screening%20%EB%B0%8F%20%EB%8C%80%EC%9D%91%EB%B0%A9%EC%95%88%20Tool%20Orchestration.md)

보관된 실행 결과는 `results/` 아래에 둔다.

---

## KPG-193 Data Policy

테스트 계통은 **KPG-193 v2.0**을 기반으로 한다.

실제 실습용 MATPOWER case와 위치 CSV는 public repository에 포함하지 않는다.

Local-only files:

```text
data/KPG193_ver2_0_powsybl_full.mat
data/bus_location.csv
```

PyPowSyBl에서는 `src/ai_ems/utils/kpg_powsybl_adapter.py`를 통해 KPG 병렬 dcline을 IIDM HVDC / VSC로 구성한다.

세부 사용법과 제약조건은 [`docs/specs/KPG_POWSYBL_ADAPTER_SPEC.md`](docs/specs/KPG_POWSYBL_ADAPTER_SPEC.md)를 참고한다.

---

## Limitations

현재 구현은 연구 / PoC 목적이다.

- 발전기 사고 후 corrective-action workflow는 구현되어 있으나, 현재 제어 후보는 기본적으로 고정된 `±10 MW` balanced Redispatch 조합을 민감도 기반으로 선별하는 heuristic이다.
- Redispatch 후보 다양화는 동일 Up Bus / Down Bus pair 중복 제거 기준을 사용하며, 전역 최적화나 운전비용 최소화를 수행하지 않는다.
- Redispatch 후보 생성은 OPF / SCED optimization이 아니다.
- `best_tested_candidate`는 시험한 후보 중 최선이며 전역 최적해 또는 위반 해소를 의미하지 않는다.
- Sensitivity Analysis는 local linearization이며 실제 제어 효과는 AC Load Flow / Security Analysis로 재검증한다.
- Single / Distributed slack은 Load Flow balancing 가정이며 운영자 Redispatch가 아니다.
- 실제 EMS 운영 절차, 경제성, 발전기 ramp, 예비력, AGC 상태, 제어 권한 등 운영제약은 현재 corrective-action 후보 생성에 포함하지 않는다.
- 정적 AC Load Flow / Security Analysis는 transient, frequency, rotor-angle 등 동특성 안정도를 검증하지 않는다.
- Agent는 사전에 정의된 Tool과 workflow를 선택·연결하는 PoC 수준이며, 다양한 자연어 표현과 모든 후속 문맥에 대한 routing 안정성을 포괄적으로 검증한 것은 아니다.
- 반복 batch / contingency 호출의 성능 최적화는 향후 과제다.
