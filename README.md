# ai-ems-agent

Local LLM + LangGraph + PyPowSyBl 기반 AI-EMS Agent 실습 프로젝트.

목표 흐름은 다음과 같다.

`자연어 요청 -> Local LLM -> LangGraph -> PyPowSyBl Tool -> 구조화 결과 -> 자연어 설명`

가능하면 기존 KPG 계통 지도 UI를 재사용해 사고 설비와 한계 위반 설비를 시각화한다.

## Project Structure

```text
ai-ems-agent/
├─ src/
│  └─ ai_ems/
│     ├─ __init__.py
│     ├─ network.py
│     └─ tools/
│        ├─ __init__.py
│        ├─ network_tools.py
│        └─ security_tools.py
│
├─ data/
│  ├─ README.md
│  └─ KPG193_ver2_0_pypowsybl.mat
│
├─ tests/
│  └─ smoke_test.py
├─ requirements.txt
├─ .gitignore
└─ README.md
```

현재는 **Phase 1 - Plain Python Tool Layer**를 구현한다. 처음부터 LLM과 LangGraph를 붙이지 않고, PyPowSyBl 기능을 일반 Python 함수로 안정적으로 노출하며 JSON 직렬화 가능한 구조화 결과를 만드는 것이 우선이다.

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
- Sensitivity Analysis Tool
- 자동 제어 후보 생성

## KPG-193 Test System

이 프로젝트의 테스트 계통은 **KPG-193 v2.0**을 기반으로 한다.

KPG 193은 KENTECH AGM Center에서 개발한 synthetic Korean power grid test system이며, upstream repository에서는 Open Database License (ODbL) 1.0으로 제공하고 있다.

- Upstream repository: https://github.com/agm-center/kpg-testgrid
- KPG Test System documentation: https://agm.kentech.ac.kr/docs/kpg-test-system/
- Paper: Geonho Song and Jip Kim, *KPG 193: A Synthetic Korean Power Grid Test System for Decarbonization Studies*, arXiv:2411.14756, 2024.

인용 시 upstream 프로젝트가 안내하는 다음 논문을 사용한다.

```bibtex
@article{song2024kpg,
  title={KPG 193: A Synthetic Korean Power Grid Test System for Decarbonization Studies},
  author={Song, Geonho and Kim, Jip},
  journal={arXiv preprint arXiv:2411.14756},
  year={2024}
}
```

### PyPowSyBl-compatible case

`data/KPG193_ver2_0_pypowsybl.mat`은 upstream의 원본 MAT 파일을 그대로 복사한 파일이 아니라, 기존 `kpg-testgrid` 실습에서 PyPowSyBl import를 위해 변환한 derived MATPOWER case이다.

현재 변환 방식은 다음과 같다.

- PyPowSyBl import 시 base voltage 정보를 사용
- 원본 `dcline` 항목은 derived case에서 제거
- 기존 KPG/PYPOWER 실습과 동일하게 fixed HVDC transfer를 양단 dummy generator로 표현
- PyPowSyBl AC Load Flow 수렴 검증 완료
- PyPowSyBl Security Analysis 단일 선로 사고 검증 완료

세부 provenance와 변환 내용은 `data/README.md`에 기록한다.

## Verified KPG Scenario

현재 대표 검증 시나리오는 다음과 같다.

- outage line: `LINE-16-28`
- monitored line: `LINE-16-22`
- analysis: PyPowSyBl AC Security Analysis
- observed result: 사고 후 `LINE-16-22`에서 `APPARENT_POWER` 한계 위반 확인

이 사례를 향후 Agent의 기본 demonstration scenario 중 하나로 사용한다.

## Installation

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
pip install -r requirements.txt
```

Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Smoke Test

`data/KPG193_ver2_0_pypowsybl.mat` 파일이 있으면 기본 검증 시나리오는 인자 없이 실행할 수 있다.

```bash
python tests/smoke_test.py
```

다른 case 또는 contingency를 시험하려면:

```bash
python tests/smoke_test.py \
  --case /path/to/case.mat \
  --outage LINE-16-28 \
  --monitor LINE-16-22
```

## Roadmap

1. Plain Python Tool 계층 검증
2. Tool 입력/출력 schema 고정
3. Network/Generator 조회 Tool 확장
4. Security Analysis Tool 확장
5. Sensitivity Analysis Tool 추가
6. LangGraph workflow 연결
7. Local LLM tool calling 연결
8. 자연어 설명 및 multi-turn interaction
9. 가능하면 KPG SVG 지도 UI 연동

발표 일정상 Agent 개발이 지연되면 무리하게 범위를 넓히지 않고, 기존 PyPowSyBl Security/Sensitivity 실습 결과만 발표자료에 반영하는 Plan B로 전환한다.
