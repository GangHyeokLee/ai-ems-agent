# PH4-02-MAINT-02 — PH4-02B1/B2 `web_app.py` 코드 변경 해시 체인

기준: PR [#20](https://github.com/GangHyeokLee/ai-ems-agent/pull/20) 브랜치 `feature/ph4-02b2-agent-artifact-ledger` `4c4db69` / Issue [#14](https://github.com/GangHyeokLee/ai-ems-agent/issues/14).
검증 계약만 보완하는 유지보수 작업이다. B1/B2 기능 코드, 계통 데이터, 기존 해시 기록·검증 증거는 바꾸지 않았다.

## 1. 변경 전 실패 원인

`tests/test_mn_pipeline.py`의 두 테스트가 통합 기준 `6a4df5e`부터 실패했다.

- `test_phase4_code_record_pins_only_console_chat_asset`: **현재 HEAD**의 `web_app.py`가 PH4-01 기록의 `after`(`6628d110…`)와 같다고 단언했다.
- `test_real_asset_checks_are_deterministic_and_keep_model_roles`: 현재 `web_app.py`가 체인의 최신 기록(PH4-01) `after`와 같다고 단언했다.

체인의 규칙은 "각 기록은 이전 기록의 정확한 바이트를 잇고, 최신 기록만 현재 바이트를 고정한다"이다.
B1(`6a4df5e`, `a3d46cd0…`)과 B2(`3dd3ece`, `5a7abf5c…`)가 `web_app.py`를 바꿨지만 PH4-01 뒤를 잇는 기록이 없었다.
B0 트리(`d6ecf79`) 사본에서는 같은 테스트가 통과했다. B1의 회귀 범위에 `test_mn_pipeline.py`가 없어 발견되지 않았다.

## 2. 기존 기록의 연속성 확인

기록 바이트와 실제 Git blob으로 32개 항목을 확인했다(모두 통과, 증거: `history-chain-probe.txt`).

| 구간 | `web_app.py` SHA256 | 실제 Git 근거 |
|---|---|---|
| Stage 3C before → after | `7596544b…` → `715c1974…` | 각각 `7f3ac1f`, `41c8689`에서 처음 등장 |
| Phase 2 (`commits` 4개) | `715c1974…` → `392b7afc…` | `41c8689`·`b15ed38`·`672c56b`·`257b95c`의 blob SHA256이 기록과 일치 |
| Phase 3 | `392b7afc…` → `78f7f946…` | `base_commit` `d542ba3`이 before, `266475f`에서 after 등장 |
| PH4-01 | `78f7f946…` → `6628d110…` | `base_commit` `3f7b179`이 before, `3094305`(blob `d38aad38`)가 after, B0 `d6ecf79`까지 동일 |
| PH4-02B1 | `6628d110…` → `a3d46cd0…` | `6a4df5e`(blob `2f104914`). `3094305..6a4df5e`에서 `web_app.py`를 바꾼 커밋은 이것뿐 |
| PH4-02B2 | `a3d46cd0…` → `5a7abf5c…` | `3dd3ece`(blob `546b19a3`). `6a4df5e..3dd3ece`에서 유일, HEAD·작업 트리와 동일 |

기록 간 연결 SHA256(`stage3c_record_sha256`·`phase2_record_sha256`·`phase3_record_sha256`)과 4개 기록의 Stage 1 계약 SHA256
(`bde2dbd9…`), 각 기록의 `before`=이전 `after`도 일치했다.

## 3. 새 기록: `docs/validation/kems-console-ph402-code-hashes.json`

| 필드 | 내용 |
|---|---|
| `schema` | `kems-ph402-code-change-audit-1` |
| `stage1_contract_sha256` | 원래 Stage 1 계약 `bde2dbd9…` (수정하지 않음) |
| `phase4_record_sha256` | PH4-01 기록 파일의 정확한 SHA256 `fa71f907…` |
| `files["ems_code.web_app.py"]` | `path=web_app.py`, `before`=PH4-01 `after`(`6628d110…`), `after`=현재(`5a7abf5c…`) |
| `commits` | 순서대로 PH4-01 버전(`3094305`), B1(`6a4df5e`), B2(`3dd3ece`). 각 항목에 commit·Git blob ID·SHA256·설명 |
| `edits` (B1, B2 단계) | 그 단계의 정확한 줄 변경. `' '` 문맥, `'-'` 삭제, `'+'` 추가이며 각 변경 위치가 유일하도록 최소 문맥을 둔다 |

기록은 실제 Git blob에서 생성했다(`build_ph402_record.py`). 생성 시 정방향·역방향 적용이 각 blob 바이트와 정확히 같음을 확인했고,
줄 수는 `git diff --stat`과 같다(B1 −2/+15, B2 −20/+60). 파일은 ASCII(한글 리터럴은 `\u` escape)라 locale과 무관하다.
`3dd3ece`는 PR #20 브랜치 커밋이며 squash 병합 후 통합 커밋 ID는 달라지지만 blob은 같다.

## 4. 테스트 변경(검증 계약)

- 신규 `ph402_code_changes`: 기존 `phaseN_code_changes`와 같은 방식으로 이전 기록 SHA256, Stage 1 계약, 역할·경로,
  `before`=PH4-01 `after`≠`after`, 단계 순서·형식, commit/blob/SHA256 중복 금지를 검사한다.
- 신규 `ph402_versions`: 현재 바이트에서 기록된 edits만 역순 적용해 각 과거 버전을 복원하고, 단계마다 SHA256과 Git blob ID
  (`sha1("blob <len>\0"+bytes)`)가 기록과 같아야 한다. 마지막에 PH4-01 고정값과 같아야 한다. 테스트는 Git을 실행하지 않는다.
- `test_phase4_code_record_pins_only_console_chat_asset`: 바이트 출처만 HEAD에서 **체인으로 복원한 PH4-01 바이트**로 바꿨다.
  `sha256 == PH4-01 after`, `"api-chat.js", ` 정확히 1회, 제거 시 `before`라는 기존 단언은 그대로 유지한다.
- `test_real_asset_checks_are_deterministic_and_keep_model_roles`: 체인 끝에 새 기록 한 개를 이었다. Stage 3C·Phase 2/3/4 `after`는 과거 고정값으로 남는다.
  Stage 1 계약에서 3개 역할만 다르다는 기존 단언과 그 밖의 단언은 바꾸지 않았다.
- 신규 테스트 25개: 모든 버전 복원 1개, 기록되지 않은 현재 바이트 거부 3개, 격리 사본의 기록 변이 21개.
  변이는 PH4-01 `after` 재작성, PH4-01 건너뛰기, 무변경, `after`가 최신 단계가 아님, 중간 SHA256·blob 조작, 중간 SHA256·commit 재사용,
  단계 교환·역순·B1 누락, edit 누락·변조·모호한 무의미 edit, 첫 단계 edits, 경로, 이전 기록 SHA256(임의값·더 오래된 Phase 3 기록),
  Stage 1 계약, schema, 역할 추가다. 각 변이를 거부하는 단언 줄을 `mutation-catch-lines.txt`에 기록했다.

기존 Stage 1·3C·Phase 2·3·PH4-01 기록 파일과 그 체인·변이 테스트는 바꾸지 않았다. assertion 삭제·skip·xfail은 없다.

## 5. 호환성과 운영 규칙

- 앞으로 `web_app.py`를 바꾸는 작업(예: B3)은 이 기록을 수정하지 않는다. `ph402` 기록의 SHA256을 잇고
  자기 단계의 edits를 담은 새 기록을 추가해야 한다. 그 전까지 체인 테스트가 실패하는 것은 의도된 동작이다.
- commit ID는 기록 시점에 Git으로 확인한 설명 정보이며 테스트는 commit 객체에 의존하지 않는다.
  따라서 squash 병합·shallow clone·브랜치 삭제 후에도 작업 트리 파일과 기록만으로 검증된다.

## 6. 검증

| 검증 | 수정 전 | 수정 후 |
|---|---|---|
| 해시 체인 관련 테스트(`-k "code_record or real_asset or ph402"`) | **2 failed, 26 passed** | **53 passed** (기존 28 + 신규 25) |
| 전체 비브라우저 | B2 최종 실행 기록: 2 failed, 768 passed, 103 skipped (이번 재실행 아님) | **795 passed, 103 skipped, 1 warning (64.91s)** |
| 기록 변이 21개(격리 JSON 사본) | — | 21/21 거부 |
| 검증기 약화 변이 7개(격리 저장소 사본) | — | 7/7 검출 |

검증기 약화 변이는 이전 기록 SHA256, 중간 SHA256, 끝점 순서, 경로, edit 유일성, PH4-01 고정값, 단계 유일성 검사를 하나씩 제거했다.
최초 실행에서 끝점 순서·edit 유일성·단계 유일성 제거는 검출되지 않았다. 이에 맞는 기록 변이 3개
(`after`가 최신 단계가 아님, 모호한 무의미 edit, commit 재사용)를 추가한 뒤 7/7이 검출됐다.
edit 유일성은 무결성이 아니라 형식 규칙이다(잘못된 치환은 다음 SHA256 검사에서 실패한다). 이 규칙은 모호한 기록을 거부한다.

제한: 신규·관련 테스트 각 40초 SIGALRM과 명령 180초, 전체 비브라우저는 테스트별 120초와 명령 900초(+kill).
`git diff --check` 통과, 변경 허용 목록 밖 기존 파일 SHA256 보존을 확인했다. 실제 LLM·Chrome은 이 작업과 무관해 실행하지 않았다.
증거: `.local/ph4-02-maint-02/20261104-165048/`(수정 전후 로그, `history_chain_probe.py`, `build_ph402_record.py`, 변이 probe와 결과).

## 7. 남은 범위

- B2의 전체 비브라우저 "수정 전" 수치는 B2 최종 실행 기록을 인용했다. 이번 작업에서 수정 전 상태로 전체 실행을 다시 하지는 않았다
  (수정 전 해시 체인 관련 테스트는 이번에 재실행했다).
- B3 구현은 시작하지 않았다. PR #20의 B2 미해결 위험(첫 AC 거부 안내, 실제 LLM smoke, MN 연결 등)은 그대로다.
