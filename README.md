# ai-ems-agent

Local LLM + LangGraph + PyPowSyBl 기반 AI-EMS Agent 실습 프로젝트.

목표 흐름은 다음과 같다.

`자연어 요청 -> Local LLM -> LangGraph -> PyPowSyBl Tool -> 구조화 결과 -> 자연어 설명`

가능하면 기존 KPG 계통 지도 UI를 재사용해 사고 설비와 한계 위반 설비를 시각화한다.

## 현재 단계: Phase 1 - Plain Python Tool Layer

처음부터 LLM과 LangGraph를 붙이지 않는다. 먼저 PyPowSyBl 기능을 일반 Python 함수로 안정적으로 노출하고 JSON 직렬화 가능한 구조화 결과를 만든다.

현재 구현 범위:

- KPG/MATPOWER network load
- network summary
- line 조회
- AC load flow
- 단일 선로 contingency Security Analysis
- limit violation / monitored branch 결과 구조화

아직 포함하지 않는 것:

- LangGraph
- Local LLM
- FastAPI / Web UI
- SVG 지도 연동
- Sensitivity Analysis
- 자동 제어 후보 생성

## KPG 데이터

KPG-193 데이터 자체는 이 저장소에 복사하지 않는다. `kpg-testgrid`에서 생성한 PyPowSyBl 호환 MATPOWER 파일의 경로를 실행 시 전달한다.

검증된 파일 예:

`../kpg-testgrid/kpg193_v2_0/network/mat/KPG193_ver2_0_pypowsybl.mat`

MATPOWER import 시 기존 검증과 동일하게 다음 parameter를 사용한다.

`matpower.import.ignore-base-voltage=false`

AC load flow는 우선 기존 KPG 실습과 비교하기 위해 `distributed_slack=False`로 실행한다.

## 설치

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

## Smoke Test

```bash
python smoke_test.py \
  --case ../kpg-testgrid/kpg193_v2_0/network/mat/KPG193_ver2_0_pypowsybl.mat \
  --outage LINE-16-28 \
  --monitor LINE-16-22
```

기존 검증 사례에서는 `LINE-16-28` 탈락 후 `LINE-16-22`에서 `APPARENT_POWER` 한계 위반이 발생한다.

## 단계별 확장

1. Plain Python Tool 계층 검증
2. Tool 입력/출력 schema 고정
3. LangGraph에서 deterministic tool routing 연결
4. Local LLM tool calling 연결
5. 대화형 자연어 설명
6. 가능하면 KPG SVG 지도 UI 연결

발표 일정상 Agent 개발이 지연되면 여기서 무리하게 범위를 넓히지 않고, 기존 PyPowSyBl Security/Sensitivity 실습 결과를 발표자료에 반영하는 Plan B로 전환한다.
