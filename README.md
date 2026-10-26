# ai-ems-agent

로컬 LLM + LangGraph + PyPowSyBl 기반 **AI-EMS Agent PoC** 프로젝트.

이 프로젝트의 목적은 LLM이 전력계통 물리해석을 대체하는 것이 아니라, 사용자의 자연어 요청을 해석하고 적절한 PyPowSyBl 기반 Tool을 선택·호출한 뒤 실제 계산 결과를 운영자 관점에서 설명하는 흐름을 검증하는 것이다.

> **AI는 물리해석 엔진을 대체하지 않는다.**
> LLM은 자연어 의도 해석과 Tool orchestration을 담당하고, 계통 상태와 제어 효과는 Security Analysis, Sensitivity Analysis, AC Load Flow 등 물리해석 결과로 검증한다.

---

## 현재 지원 기능

### 전력계통 해석

- KPG-193 기반 PyPowSyBl 계통 모델 로드
- AC 조류계산 (Load Flow)
- 선로 N-1 안전도 해석 (Security Analysis)
- 발전기 N-1 안전도 해석
- 발전기 전체 N-1 일괄 스크리닝
- 단일 / 분산 슬랙 (Single / Distributed slack) 발전기 사고 비교
- 발전기 N-1 balancing 메커니즘 분석
- 사고 전후 위반 비교
- 발전기 민감도 해석 (Sensitivity Analysis)
- 선로 사고 후 민감도 기반 균형 재급전 (balanced redispatch) 후보 생성
- 발전기 사고 후 민감도 기반 균형 재급전 후보 생성
- 동일 모선 쌍의 중복 후보 제거
- 발전기 출력 범위 제약 검사
- 재급전 후 AC 조류계산 검증
- PyPowSyBl Operator Strategy 기반 전체 계통 안전도 재검증
- 발전기 사고 후 안전도 → 민감도 → 재급전 후보 → AC/안전도 재검증 흐름

### AI / 에이전트

- 자연어 요청 → Tool 선택
- LangGraph ToolNode 기반 로컬 LLM 도구 호출 (Tool Calling)
- 선로 사고 후 대응방안 분석 흐름
- 발전기 사고 개별 분석 / 슬랙 비교 / 일괄 스크리닝
- 발전기 사고 후 대응방안 분석을 연결하는 고수준 도구
- 발전기 N-1 스크리닝 결과를 이용한 멀티턴 후속 분석
- 고위험 계통 결과의 결정적 포매터 (deterministic formatter)
- 도구 오류의 자연어 설명
- 멀티턴 분석 요청
- 몽골 합성 AC PF 결과 읽기 전용 질의응답 (별도 Agent·Tool, [사양](docs/specs/MONGOLIA_PF_CHAT_SPEC.md))
- 몽골 합성 계통 선로 N-1 Security Analysis와 계통 분리 표시 (PyPowSyBl XIIDM, [사양](docs/specs/MONGOLIA_SECURITY_ANALYSIS.md))
- 몽골 계통 분리 없는 N-1 후 선로 조류 Sensitivity (유한차분 검증, [사양](docs/specs/MONGOLIA_SENSITIVITY_ANALYSIS.md))

### 웹 UI

지원 계통은 다음 세 가지이며 기본 선택은 KPG-193을 유지한다.

| Grid ID | 계통 | 지원 범위 |
|---|---|---|
| `kr-kpg193` | KR · KPG-193 시험계통 | AC PF, N-1, Sensitivity, 발전기 사고, AI |
| `mn-osm-35k` | MN · 몽골 OSM 송전망 | 합성 AC PF, 선로 N-1, Sensitivity, AI |
| `kr-osm-transmission` | KR · 한국 OSM 송전망 (154/345/765 kV) | 지도 전용, 계산·AI 비활성 |

한국 지도는 원본 프로젝트를 변경하지 않고 다음 명령으로 검증·가져오기 한다.
실제 한국 원본 JSON 경로를 `--source`로 전달한다. 원본의 국가 폴리곤은 영해를 포함한
관할 경계이므로 표시하지 않고, 같은 OSM 추출본의 해안선(`natural=coastline`)으로 만든
육지 경계를 `--land-boundary`로 넣는다. 육지 경계 생성에는 osmium·shapely·pyproj가
필요하며 형제 프로젝트의 가상환경을 쓸 수 있다.

```bash
../korea-grid-poc/.venv/bin/python scripts/build_kr_land_boundary.py \
  --pbf ../korea-grid-poc/data/raw/south-korea-261020.osm.pbf \
  --output data/grids/kr-land-boundary.json
.venv/bin/python scripts/import_kr_osm_map.py \
  --source ../korea-grid-poc/outputs/map/kr_grid_preview.json \
  --land-boundary data/grids/kr-land-boundary.json
```

기본 배포 파일은 `data/grids/kr-osm-transmission-map.json`이며 Git 제외 대상이다.
`--land-boundary` 없이 가져오면 국가 외곽선 없이 행정구역 경계와 송전망만 표시한다.
서버 설정 `AI_EMS_KOREA_MAP_FILE`로 승인된 외부 JSON을 읽기 전용으로 지정할 수도 있다.
경로는 서버 운영 설정이며 HTTP 요청으로 선택할 수 없다. 파일 누락·손상 시 한국만
사용 불가로 표시하며 KPG·몽골은 유지한다. 한국 지도는 실제 선로 경로와 OSM ID,
출처·추정 상태를 보존하며, WRI 발전소 132개는 과거 지도 레코드로 실제 운전 출력이나
검증된 접속 모선을 뜻하지 않는다. 구현·성능·검증 결과와 라이선스는
[한국 OSM 지도 통합 검증](docs/validation/korea-osm-map-integration.md)을 참조한다.

- FastAPI 기반 웹 UI
- KPG 계통 지도
- 선로 / 모선 / 발전기 선택
- 안전도 / 민감도 해석 직접 실행
- 발전기 상정사고 해석 직접 실행
- 분석 패널 접기 / 펼치기
- 사고·위반·민감도·발전기 변화 시각화
- AI 채팅 Markdown 렌더링

### 물리해석 API

에이전트와 별도로 외부 시뮬레이터 / EMS 테스터가 LLM 없이 물리해석 기능을 호출할 수 있는 REST API를 제공한다.

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

## 구조

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

### 도메인 계층

LLM과 독립적인 Python / PyPowSyBl 계층이다.

- `network.py`: 계통 로드 및 공통 AC 조류계산
- `network_tools.py`: 계통 요약 / 선로 / 발전기 조회
- `security_tools.py`: 선로 / 발전기 상정사고 안전도 해석 지원 기능
- `generator_contingency_tools.py`: 발전기 탈락 AC 조류계산, 슬랙 보상 비교 및 안전도 해석
- `generator_screening_tools.py`: 연결 발전기 전체 N-1 일괄 스크리닝
- `sensitivity_tools.py`: 선로 / 발전기 사고 이후 발전기 주입량 변화에 대한 선로 유효전력 조류 민감도
- `control_tools.py`: 선로 사고 후 균형 재급전 후보 / 제약 검사 / AC·안전도 재검증
- `generator_control_tools.py`: 발전기 사고 후 민감도 기반 재급전 후보 생성 / 후보 다양화 / AC·안전도 재검증
- `workflow_tools.py`: 선로 / 발전기 사고의 안전도 → 민감도 → 재급전 후보 → 물리 재검증 흐름

### 에이전트 계층

`src/ai_ems/agent/tools.py`는 도메인 함수를 LLM이 호출할 수 있는 도구 스키마로 노출한다.

현재 에이전트 도구:

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

고위험 계산 결과는 LLM이 수치를 다시 자유롭게 해석하지 않도록 결정적 포매터를 사용한다.

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

발전기 사고 대응방안 분석에서는 에이전트가 `generator_outage_response_analysis`를 선택하고, 해당 고수준 도구 내부에서 안전도 해석, 민감도 해석, 재급전 후보 생성, AC 조류계산 및 전체 계통 안전도 재검증을 수행한다.

### 외부 물리해석 API 계층

현재 엔드포인트:

```text
GET  /api/health
POST /api/v1/security-analysis
POST /api/v1/sensitivity-analysis
POST /api/v1/redispatch-validation
POST /api/v1/contingency-response
```

세부 계약은 [`docs/specs/API_SPEC.md`](docs/specs/API_SPEC.md)를 참고한다.

---

## 주요 분석 흐름

### 선로 사고 후 대응방안 분석

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

### 발전기 사고 분석

```text
Specific Generator Outage
      ↓
Single or Distributed Slack AC Load Flow
      ↓
Security Analysis
      ↓
Deterministic Result Explanation
```

단일 / 분산 슬랙은 조류계산의 전력 균형 가정이며 운영자 재급전이 아니다.

### 발전기 사고 후 대응방안 분석

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

기본 제어 후보는 한 발전기 `+10 MW`, 다른 발전기 `-10 MW`의 균형 재급전이며, 민감도는 후보 우선순위 선별에 사용한다. 실제 효과는 AC 조류계산과 전체 계통 안전도 해석으로 다시 검증한다.

`best_tested_candidate`는 **시험한 후보 중 최선의 결과**이며 OPF / SCED 기반 최적해나 위반 해소를 보장하는 제어안이 아니다.

### 발전기 N-1 일괄 스크리닝

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

스크리닝 순위는 AI 점수나 고장 확률이 아니라 물리해석 결과 기반 **검토 우선순위**다.

스크리닝 결과는 같은 대화의 후속 요청에서 특정 순위의 발전기 사고 상세분석 또는 대응방안 분석으로 연결할 수 있다.

---

## 환경 구성

Python 3.11 기준.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

프로젝트 설정은 저장소 루트의 `.env`를 사용하며 `src/ai_ems/config.py`가 자동 로드한다.

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

## 실행

### CLI 에이전트

```bash
python app.py
```

### 에이전트 웹 UI

```bash
./run_agent.sh
```

브라우저:

```text
http://127.0.0.1:8000/
```

### 독립 물리해석 API

```bash
./run_api.sh
```

Swagger UI:

```text
http://127.0.0.1:8001/docs
```


### Bearer 인증을 사용하는 로컬 Ollama 프록시 (Windows)

Windows Caddy는 `127.0.0.1:18080`에서만 요청을 수신하며, `GET /api/tags`와
`POST /api/chat`만 고정된 업스트림 `127.0.0.1:11434`로 전달한다.
설치된 ChatOllama는 동기·비동기·스트리밍·도구 호출에 `POST /api/chat`을 사용하며,
초기화 시 모델 검증은 활성화하지 않는다. 모델 관리와 `/api/generate`를 포함한
다른 경로·메서드는 403을 반환한다. Bearer 인증 정보가 없거나 잘못되면 업스트림에
요청하기 전에 401을 반환한다.

Windows에 Caddy를 설치하고 저장소의 `deploy/ollama-proxy` 소스를 원하는 Windows
디렉터리에 복사한다. PowerShell 실행 스크립트는 이름을 바꿔도 되지만, 지원 모듈은
같은 디렉터리에 둬야 한다. 기존 로컬 `.env`의 다른 설정을 유지하면서 다음 두 항목을
추가하고 Windows 전체 경로를 지정한다.

```ini
AI_EMS_CADDY_SCRIPT_WIN=%LOCALAPPDATA%\YourApp\Proxy\launch.ps1
AI_EMS_CADDY_AUTOSTART=false
```

예시 경로와 파일명은 실제 설치 위치로 바꾼다. 이 경로는 실행 설정이며 인증·접근 제어를
제공하지 않는다. `.env`는 Git에 포함하지 않고 기존 LLM 인증 정보와 프로젝트 설정은
유지한다. 실제 토큰을 소스·문서·스크립트 인자·실행 설정 로그에 넣지 않는다.

자동 실행을 사용하려면 `AI_EMS_CADDY_AUTOSTART=true`로 설정한 뒤 실행한다.

```bash
./run_agent.sh
```

웹 앱은 WSL 루프백에서 실행되며, `AI_EMS_WEB_PORT`로 포트를 선택한다(기본 8000).
기본 포트를 이미 사용 중이라면 `AI_EMS_WEB_PORT=8010 ./run_agent.sh`처럼 실행한다.

실행기는 `.env`를 `python-dotenv`의 데이터로 읽는다(`interpolate=False`). `source`나
`eval`로 실행하지 않으며, 위 두 설정은 내보낸 프로세스 환경변수가 `.env`보다 우선한다.
`%LOCALAPPDATA%` 등 Windows 환경변수는 PowerShell의 `ExpandEnvironmentVariables`로
확장하고 경로 자체를 그대로 호출한다.

자동 실행을 끄거나 지정하지 않으면 프록시를 수동으로 실행하는 방식으로 웹 앱을 사용할
수 있다. `AI_EMS_CADDY_AUTOSTART=true`일 때 설정이 잘못되면 종료 코드 2로 중단한다.
Windows 상호운용 실패, 서버 토큰 누락·형식 오류, 포트 충돌, Caddy 시작 실패는
**Uvicorn 실행 전에** 종료 코드 3으로 중단하며, 이번 실행에서 소유한 프록시는 정리한다.
기존 PowerShell 실행기를 포그라운드에서 직접 사용하려면 `false`로 설정한다.
PowerShell에서 설정 경로를 프로세스 환경변수로 지정한 뒤 다음과 같이 호출할 수 있다.

```powershell
& ([Environment]::ExpandEnvironmentVariables($env:AI_EMS_CADDY_SCRIPT_WIN))
```

새 프록시는 실행하는 Windows 또는 WSL 프로세스에 `OLLAMA_PROXY_TOKEN`이 필요하다.
값은 암호학적 난수 생성기로 만든 소문자 16진수 64자리다. WSL 프로세스가 토큰을
명시하면 우선 사용하고 표준입력으로 전달한다. 그렇지 않으면 Windows PowerShell
프로세스의 기존 토큰을 사용한다. 로그에는 출처 이름만 기록한다. 인증 저장소·레지스트리
조회·명령행 비밀값·평문 토큰 파일은 추가하지 않는다. 실행 중인 Caddy를 재사용할 때는
서버 토큰이 필요하지 않으며, 클라이언트 인증은 별도로 확인한다. 승인된 방식으로
프로세스에 인증 정보를 주입하고 실제 토큰은 Git에 저장하지 않는다.

포트 18080의 전체 TCP 리스너가 정확히 하나이고 Caddy 소유의 IPv4 `127.0.0.1`에
바인딩된 경우에만 재사용한다. 와일드카드 `0.0.0.0`/`::`, IPv6 `::1`, 다른 인터페이스,
여러 리스너(정상 IPv4와 다른 리스너의 혼합 포함)는 `occupied`로 거부한다. 새 프록시도
`started`를 알리기 전에 같은 주소 검사를 수행한다. 거부된 소유 프록시는 정리하되
기존 프로세스·설정은 보존하며, 다른 포트를 자동 선택하지 않는다.

Caddy 리스너 확인은 재사용 가능 여부만 판정한다. 시작·재사용 후 기존
`AI_EMS_LLM_BASE_URL`의 `/api/tags`에 앱의 공통 Bearer/Cloudflare 인증 규칙과
기존 `.env`·프로세스 인증 정보를 적용해 요청한다. 리디렉션은 따라가지 않으며,
HTTP 200 응답에 Ollama `models` 목록이 있어야 한다. HTTP 401/403, 게이트웨이·업스트림
502, 연결 실패, 시간 초과, 잘못된 설정·응답은 URL·헤더·본문을 노출하지 않고 구분한다.
이러한 API 오류에서는 AI를 사용하지 못할 수 있음을 안내하고 웹 UI를 계속 실행한다.
Ollama 일시 중단을 포함해 API 오류로 기존 Caddy를 재시작하지 않는다. 설정 URL이
원격 주소라면 성공 응답은 해당 경로의 확인이며, 로컬 프록시 라우팅 검증은 아니다.

Ctrl+C, SIGTERM, 정상 종료, 앱 오류 시 보관한 프로세스 핸들로 이번 실행에서 시작한
Caddy 하위 프로세스만 정리한다. 기존 Caddy와 다른 리스너는 종료하지 않는다.
앱의 첫 시그널 처리에 10초, 이후 SIGTERM에 5초, SIGKILL 후 최대 5초를 기다린다.
컨트롤러 정리 대기는 최대 25초이며 폴링을 포함한 전체 대기는 약 46초다. 앱 종료 처리에
오류가 생겨도 Caddy 정리를 진행한다. 컨트롤러 시간 초과 시 확인되지 않은 프로세스를
강제 종료하는 대신 수동 확인을 안내한다. 호스트·WSL의 갑작스러운 종료나 실행기의
SIGKILL에서는 정리를 보장할 수 없다. 프로세스 이름 기반 `taskkill`이나 Windows 설치
설정 변경은 수행하지 않는다.

토큰이 없거나 형식이 잘못되면 Caddy 시작 전에 거부한다. 토큰을 포함한 실행용 JSON은
표준입력으로만 전달한다. 관리 API, 설정 영속화, 자동 HTTPS, 실행 로그는 비활성화하며,
접근 로그는 설정하지 않는다. 응답은 10ms 간격으로 플러시한다. 포그라운드 실행기는
Ctrl+C로 종료한다. Windows 서비스는 설치하지 않는다.

AI-EMS 클라이언트에서는 다음 프로세스 환경변수를 사용할 수 있다.

```dotenv
AI_EMS_LLM_BASE_URL=http://127.0.0.1:18080
AI_EMS_LLM_API_KEY=<same-token>
```

`AI_EMS_LLM_API_KEY`는 동기·비동기 클라이언트 모두에 Authorization: Bearer 헤더를
설정한다. 원격 인증에는 HTTPS가 필요하다. HTTP 예외는 정확히
`http://127.0.0.1:18080`인 로컬 테스트 URL뿐이다(마지막 `/` 허용). localhost 별칭,
다른 포트, 사용자 정보가 포함된 URL, 원격 HTTP 주소는 거부한다. 기존의 인증 없는 로컬
호출을 유지하려면 두 인증 방식을 모두 설정하지 않는다. Bearer와 Cloudflare 인증 정보는
함께 사용할 수 없다. 프록시는 Ollama에 전달하기 전에 Authorization 헤더를 제거한다.

이 단계에서는 Tailscale Serve/Funnel 활성화, `OLLAMA_HOST` 변경, 방화벽·라우터 포트
개방, AI-EMS의 8000/8001 포트 노출을 수행하지 않는다.
NAT 환경에서 WSL과 Windows의 localhost는 다르다. 배포 환경의 기존 Tailscale
HTTPS 8443 경로는 `.env`에서 유지하며 테스트로 변경하지 않는다. 사용자는 AI-EMS가
소유한 프록시가 실행 중일 때 `/api/tags` HTTP 200, Ctrl+C로 종료한 뒤 HTTP 502를
확인했다. Caddy가 없을 때의 502는 예상된 동작이다. Ollama 업데이트 중의 502도
API·업스트림 상태이며, 리스너 주소는 Windows TCP 상태로 별도 확인한다.

기존 로컬 실행기 보안 검증 기록(Windows PowerShell 5.1 + WSL2, 2026-10-21):

```bash
.venv/bin/python tests/windows_caddy_lifecycle_probe.py
.venv/bin/python -m pytest -q tests
AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests
```

당시 WSL 프로세스에는 `OLLAMA_PROXY_TOKEN`이 없었고, 새 Windows PowerShell
프로세스가 올바른 형식의 토큰을 상속했다. 분리된 Windows 포트에 리스너가 없는 상태에서
실제 실행기가 Windows 인증 정보를 사용해 Ollama tags HTTP 200을 확인하고 실제
Uvicorn을 시작한 뒤 Ctrl+C에서 소유한 Caddy를 정리했다. 기존 WSL `.env`의 LLM 경로도
초기에는 HTTP 200과 Ollama `models` 목록을 반환했다.

당시 에이전트 실행에서는 기본 리스너가 없을 때 HTTP 502, 시작 후에는 최종 probe를
포함해 HTTP 403을 관측했다. 앞선 개별 검증에서는 기존 `.env`의 클라이언트 키로
Windows localhost에서 HTTP 200을 받았고, 메모리 내 비교로 Windows가 상속한 서버
토큰과 일치함을 확인했다. 당시 에이전트의 HTTPS 403은 사용자가 이후 확인한 위의
200/502 수명주기 결과와 구분하며, 리스너 바인딩 실패를 뜻하지 않는다. 우회 목적으로
인증 저장소나 네트워크 설정을 변경하지 않았다.

바인딩 검증은 운영 PowerShell `Listener` 함수에 모의 주소 목록 12개를 전달한다.
수명 관리 스크립트 전체 검증에서는 실제 Caddy PID로 거부 대상 주소 조합 10개가 모두 재사용을
막는지 확인하고 해당 프로세스를 보존한다. 시작 후 검사 3개는 실제 Caddy를 루프백에
유지한 채 와일드카드·IPv6·혼합 주소 스냅샷을 주입해 거부와 소유 프로세스 정리를
확인한다. 와일드카드나 외부 접근이 가능한 테스트 리스너는 열지 않는다.

실제 Windows 검증은 기존 Caddy 재사용, 인증 정보 누락·오류, 경로 누락, 다른 프로세스의
포트 점유, Bearer 거부(401), 기존 API 허용 목록(403), 업스트림 미응답(502)에서 Caddy를
재시작하지 않는 동작을 포함한다. 502 검증은 닫힌 포트로 연결하는 임시 프록시를 사용해
실제 Ollama를 계속 실행한다. SIGINT/SIGTERM을 무시하는 실제 하위 프로세스로 제한
시간 내 강제 종료와 소유한 Windows Caddy 정리를 확인한다. 단위 테스트는 두 시그널
경로와 API 오류 후 UI 실행 유지 정책을 검증한다.

`./run_agent.sh`는 기본 리스너가 있는 경우와 포트가 비어 있는 경우 모두 Windows에서
상속한 토큰으로 검증했다. 재사용 시 Ctrl+C와 앱 오류 이후에도 리스너 PID·시작 시간을
유지한다. 분리 포트에서도 같은 Python 실행기로 소유 프록시 시작을 검증하며 기존
리스너는 보존한다. Probe는 임시 소스 복사본을 사용하고, 설치된 Windows 파일·인증
저장소·Tailscale 설정은 변경하지 않는다. 생성한 테스트 토큰은 메모리에만 두고 수집한
로그에 노출되지 않았는지 확인한다.

당시 결과는 Windows 수명주기 probe **20 PASS**, 기본 pytest **381 passed,
12 skipped**, 브라우저 포함 pytest **393 passed**다. 두 pytest 실행에 기존
Starlette/AnyIO 폐기 예정 별칭 경고가 1건씩 있었다. 기본 실행에서 제외된 테스트는
선택 실행 방식의 브라우저 테스트다. Probe는 설정된 경로의 API 오류 관측과 수명주기
검증 결과를 구분해 보고한다.


### Cloudflare Tunnel + Access를 사용하는 원격 Ollama

AI-EMS 웹 UI와 물리해석 API는 사용자별 localhost에서 실행하고,
GPU PC의 Windows Ollama만 원격으로 공유한다.

Windows에서 cloudflared의 원본 주소를 `http://127.0.0.1:11434`, 원본 HTTP Host
헤더를 `localhost:11434`로 설정해 실행한다. 8000/8001 포트의 경로는 추가하지 않는다.
Cloudflare 계정·도메인 설정, cloudflared 설치, Tunnel 생성·시작·자동 실행은 별도
시스템 작업이다. 공개 경로를 활성화하기 전에 Ollama 호스트 이름 전체를 Access의
자체 호스팅 애플리케이션과 지정한 Service Token을 허용하는 **Service Auth** 정책으로
보호한다.

각 클라이언트에서 Git에 포함하지 않는 로컬 `.env` 또는 프로세스 환경변수를 설정한다.

```dotenv
AI_EMS_LLM_BASE_URL=https://ollama.example.com
AI_EMS_MODEL=qwen3.5:9b
AI_EMS_CF_ACCESS_CLIENT_ID=<service-token-client-id>
AI_EMS_CF_ACCESS_CLIENT_SECRET=<service-token-client-secret>
```

예시 호스트 이름을 실제 주소로 바꾸고 설정 변경 후 AI-EMS를 재시작한다.
두 Access 환경변수는 ChatOllama의 동기·비동기 요청 모두에서
`CF-Access-Client-Id`와 `CF-Access-Client-Secret` 헤더로 전달된다.
두 값이 모두 없거나 비어 있으면 기존 로컬 호출 동작을 유지한다. 하나만 설정하면
오류가 발생하며, 인증 정보를 사용하려면 HTTPS 기본 URL이 필요하다.
기존 프로세스 환경변수가 `.env`보다 우선한다.

실제 인증 정보는 Git에서 제외한 로컬 `.env` 또는 비밀 환경변수에만 둔다.
`.env.example`, 코드, 로그, 명령행 URL에 넣지 않는다.
Access Service Token과 Tunnel 커넥터 토큰은 서로 다른 인증 정보다.

로컬 테스트는 모의 HTTP 전송으로 인증이 있는 경우와 없는 경우의 동기·비동기 요청을
검증한다. Tunnel/Access 설정 후에는 누락·오류 토큰 거부를 확인한 뒤 유효한 인증
정보로 모델 목록, 추론, AI-EMS 도구 호출과 긴 응답을 시험한다.
GPU 동시 사용 시 지연 시간도 확인한다.

참고 자료:

- [Ollama Tunnel 설정](https://docs.ollama.com/faq)
- [Cloudflare Service Token](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)

### 전체 일괄 분석

```bash
python scripts/run_kpg193_full_batch.py \
  --generator-only \
  --generator-slack distributed \
  --chunk-size 100 \
  --no-sensitivity
```

`contingency_summary.csv`에는 결정적 검토 순위가 포함되어 있으므로 별도의 중복 순위 CSV는 생성하지 않는다. 민감도 결과가 없으면 빈 `topn_sensitivity.csv`도 생성하지 않는다.

---

## 테스트

회귀 테스트:

```bash
python -m pytest -q
```

최신 검증 결과(2026-10-22, PR #12 병합본 `8ef4f03` + Caddy 자동 실행 변경):

```text
관련 실행기·인증 테스트: 70 passed
전체 기본 회귀: 381 passed, 12 skipped, 1 warning
브라우저 포함 전체 회귀: 393 passed, 1 warning
실제 Windows Caddy 수명주기 probe: 20 PASS
```

전체 검증 명령:

```bash
.venv/bin/python -m pytest -q tests/test_run_agent.py tests/test_llm_access.py
.venv/bin/python -m pytest -q tests
AI_EMS_BROWSER_TESTS=1 .venv/bin/python -m pytest -q tests
.venv/bin/python tests/windows_caddy_lifecycle_probe.py
```

기본 실행의 skip 12개는 선택 실행 방식의 브라우저 테스트다. 경고 1건은 기존
Starlette/AnyIO 폐기 예정 별칭 경고다. Windows 통합 probe는 기존 리스너를 보존하면서
자동 시작·재사용·종료·인증·포트 충돌을 검증했다. 이번 실행에서 설정된 LLM의
`/api/tags`도 HTTP 200과 Ollama 형식을 확인했다. 이 검사는 추론 검증과 구분한다.

브라우저 지연 응답 회귀 테스트 (KPG·몽골 Security/Sensitivity, 계통 전환; Chrome 필요, 선택 실행):

```bash
AI_EMS_BROWSER_TESTS=1 python -m pytest -q tests/test_browser_stale_responses.py
```

Chrome은 `AI_EMS_CHROME` 또는 WSL의 Windows Chrome / PATH의 Chrome·Chromium을 사용한다.
결과(`results.json`)와 스크린샷은 `outputs/browser_stale_responses/`에 저장된다.
같은 명령으로 KPG·몽골 공통 UI 비교(`tests/browser/unified_ui_probe.js`, [사양](docs/specs/MULTI_GRID_UI.md))도 실행되며 결과는 `outputs/browser_unified_ui/`에 저장된다.
테마·도움말 검증(`tests/browser/theme_help_probe.js`, [사양](docs/specs/THEME_AND_HELP.md)) 결과는 `outputs/browser_theme_help/`에 저장된다.

실제 KPG 계통 모델 스모크 테스트:

```bash
python tests/smoke_test.py
```

실험용 probe 스크립트는 `tests/`와 저장소 루트에 있으며, 자동 회귀 테스트와 달리 특정 통합 흐름을 수동 확인하는 용도다.

---

## 문서

### 사양

- [물리해석 API 사양](docs/specs/API_SPEC.md)
- [물리해석 모듈 사양](docs/specs/MODULE_SPEC.md)
- [KPG-193 PyPowSyBl 어댑터 사양](docs/specs/KPG_POWSYBL_ADAPTER_SPEC.md)
- [몽골 합성 AC PF 읽기 전용 AI 질의응답](docs/specs/MONGOLIA_PF_CHAT_SPEC.md)
- [몽골 합성 계통 선로 N-1 Security Analysis](docs/specs/MONGOLIA_SECURITY_ANALYSIS.md)
- [몽골 합성 계통 N-1 후 선로 조류 Sensitivity](docs/specs/MONGOLIA_SENSITIVITY_ANALYSIS.md)
- [멀티 계통 공통 UI (KPG·몽골)](docs/specs/MULTI_GRID_UI.md)
- [Light/Dark 테마 · 모델/AI Assistant 도움말](docs/specs/THEME_AND_HELP.md)

### 실험 기록

- [KPG-193 기반 PyPowSyBl 계통해석 및 제어 후보 검증](docs/experiments/KPG-193%20기반%20PyPowSyBl%20계통해석%20및%20제어%20후보%20검증.md)
- [PyPowSyBl Physics API 및 AI-EMS Agent 연계 PoC](docs/experiments/PyPowSyBl%20Physics%20API%20%EB%B0%8F%20AI-EMS%20Agent%20%EC%97%B0%EA%B3%84%20PoC.md)
- [KPG-193 발전기 N-1 Single/Distributed Slack 비교](docs/experiments/KPG-193%20%EB%B0%9C%EC%A0%84%EA%B8%B0%20N-1%20SingleDistributed%20Slack%20%EB%B9%84%EA%B5%90.md)
- [KPG-193 발전기 N-1 Slack 보상 메커니즘 분석](docs/experiments/KPG-193%20%EB%B0%9C%EC%A0%84%EA%B8%B0%20N-1%20Slack%20%EB%B3%B4%EC%83%81%20%EB%A9%94%EC%BB%A4%EB%8B%88%EC%A6%98%20%EB%B6%84%EC%84%9D.md)
- [KPG-193 발전기 사고 후 민감도 기반 Redispatch 후보 생성 및 재검증](docs/experiments/KPG-193%20%EB%B0%9C%EC%A0%84%EA%B8%B0%20%EC%82%AC%EA%B3%A0%20%ED%9B%84%20%EB%AF%BC%EA%B0%90%EB%8F%84%20%EA%B8%B0%EB%B0%98%20Redispatch%20%ED%9B%84%EB%B3%B4%20%EC%83%9D%EC%84%B1%20%EB%B0%8F%20%EC%9E%AC%EA%B2%80%EC%A6%9D.md)
- [AI Agent 기반 발전기 N-1 Screening 및 대응방안 Tool Orchestration](docs/experiments/AI%20Agent%20%EA%B8%B0%EB%B0%98%20%EB%B0%9C%EC%A0%84%EA%B8%B0%20N-1%20Screening%20%EB%B0%8F%20%EB%8C%80%EC%9D%91%EB%B0%A9%EC%95%88%20Tool%20Orchestration.md)

보관된 실행 결과는 `results/` 아래에 둔다.

---

## KPG-193 데이터 정책

테스트 계통은 **KPG-193 v2.0**을 기반으로 한다.

실제 실습용 MATPOWER 계통 모델과 위치 CSV는 공개 저장소에 포함하지 않는다.

로컬 전용 파일:

```text
data/KPG193_ver2_0_powsybl_full.mat
data/bus_location.csv
```

PyPowSyBl에서는 `src/ai_ems/utils/kpg_powsybl_adapter.py`를 통해 KPG 병렬 dcline을 IIDM HVDC / VSC로 구성한다.

세부 사용법과 제약조건은 [`docs/specs/KPG_POWSYBL_ADAPTER_SPEC.md`](docs/specs/KPG_POWSYBL_ADAPTER_SPEC.md)를 참고한다.

---

## 한계

현재 구현은 연구 / PoC 목적이다.

- 발전기 사고 후 대응방안 분석은 구현되어 있으나, 현재 제어 후보는 기본적으로 고정된 `±10 MW` 균형 재급전 조합을 민감도 기반으로 선별하는 휴리스틱 방식이다.
- 재급전 후보 다양화는 출력을 늘릴 모선 / 줄일 모선 쌍의 중복 제거 기준을 사용하며, 전역 최적화나 운전비용 최소화를 수행하지 않는다.
- 재급전 후보 생성은 OPF / SCED 최적화가 아니다.
- `best_tested_candidate`는 시험한 후보 중 최선이며 전역 최적해 또는 위반 해소를 의미하지 않는다.
- 민감도 해석은 국소 선형화이며 실제 제어 효과는 AC 조류계산 / 안전도 해석으로 재검증한다.
- 단일 / 분산 슬랙은 조류계산의 전력 균형 가정이며 운영자 재급전이 아니다.
- 실제 EMS 운영 절차, 경제성, 발전기 출력 변화율, 예비력, AGC 상태, 제어 권한 등 운영제약은 현재 대응방안 후보 생성에 포함하지 않는다.
- 정적 AC 조류계산 / 안전도 해석은 과도·주파수·회전자각 등 동특성 안정도를 검증하지 않는다.
- 에이전트는 사전에 정의된 도구와 분석 흐름을 선택·연결하는 PoC 수준이며, 다양한 자연어 표현과 모든 후속 문맥에 대한 요청 분류 안정성을 포괄적으로 검증한 것은 아니다.
- 반복 일괄 / 상정사고 호출의 성능 최적화는 향후 과제다.
