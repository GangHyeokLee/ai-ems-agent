#!/usr/bin/env python3
"""Reproducible passive MN_35k PoC builder. No PF, generation or load scenario."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import pypsa

from .mn35k_lengths import audit_line_lengths, summarize_length_stages
from .mn35k_refinement import (assumption_register, build_model, export_deterministic,
                              line_type_parameters, osm_line_evidence, parameter_tables,
                              transformer_parameters)
from .validate_mn35k_poc import assert_electrical_equal, sha256, validate_model

from .artifacts import artifact_destination, new_artifact_directory
from .passive_inputs import contract, input_manifest, logical_input, read_config
from .paths import Paths


def ensure_sources(config, paths: Paths):
    if pypsa.__version__ != config["required_pypsa_version"]:
        raise ValueError("Expected PyPSA " + config["required_pypsa_version"])
    return input_manifest(config, paths)


def require_safe_output(output, config, paths: Paths):
    artifact_destination(paths, output)


def _csv(frame, name, index=False, *, report_dir: Path):
    frame.to_csv(report_dir / name, index=index, float_format="%.12g", encoding="utf-8-sig", lineterminator="\n")


def markdown_table(frame):
    """Small deterministic table without adding a formatting dependency."""
    def cell(value):
        if isinstance(value, (float, np.floating)):
            return f"{value:.6g}"
        return str(value).replace("|", "\\|").replace("\n", " ")
    return "| " + " | ".join(map(str, frame.columns)) + " |\n| " + " | ".join("---" for _ in frame.columns) + " |\n" + "\n".join("| " + " | ".join(cell(value) for value in row) + " |" for row in frame.itertuples(index=False, name=None))


def distributions(original, refined):
    derived = original.copy()
    derived.calculate_dependent_values()
    rows = []
    for stage, network in [("before_stored", original), ("before_calculated", derived), ("refined", refined)]:
        for component, fields in [("lines", ["length", "r", "x", "b", "s_nom"]), ("transformers", ["s_nom", "r", "x", "r_pu", "x_pu"])]:
            frame = getattr(network, component)
            group = frame.v_nom.astype(str) if component == "lines" else frame.apply(lambda row: "_".join(str(int(v)) for v in sorted(network.buses.loc[[row.bus0,row.bus1], "v_nom"])), axis=1)
            for voltage, items in frame.groupby(group):
                for field in fields:
                    values = items[field]
                    rows.append({"stage": stage, "component": component, "voltage": voltage, "parameter": field,
                                 "count": len(values), "min": values.min(), "median": values.median(), "mean": values.mean(), "max": values.max()})
    return pd.DataFrame(rows)


def scenario_parameters(config):
    rows = []
    for scenario, profile in config["line_35kv"].items():
        params = line_type_parameters(profile, config["line_common"])
        rows.append({"scenario": scenario, "equipment": "35kV_line", **profile,
                     **{key: params[key] for key in ["r_per_length", "x_per_length", "c_per_length", "i_nom"]},
                     "assumption_type": "STANDARD_ASSUMPTION"})
        for pair, scenarios in config["transformers"].items():
            profile = scenarios[scenario]
            params = transformer_parameters(profile, config["transformer_common"])
            rows.append({"scenario": scenario, "equipment": pair + "_transformer", **profile,
                         "r_pu_on_s_nom": params["r"], "x_pu_on_s_nom": params["x"],
                         "x_pu_on_1mva": params["x"] / params["s_nom"],
                         "assumption_type": "STANDARD_ASSUMPTION"})
    return pd.DataFrame(rows)


def _write_reports(config, lengths, register, validations, artifact, hashes, *, report_dir: Path):
    summary = lengths.groupby("voltage_kv").agg(
        선로수=("length_before_km", "size"), 보정=("correction_applied", "sum"),
        기존_km=("length_before_km", "sum"), 보완_km=("length_after_km", "sum"))
    summary["미해결"] = summary["선로수"] - summary["보정"]
    summary = summary.reset_index()
    reference = validations["reference"]
    unresolved = lengths.loc[~lengths.correction_applied, ["voltage_kv", "length_before_km", "unresolved_reason"]].reset_index()
    corrected = lengths.loc[lengths.correction_applied]
    scenario = scenario_parameters(config)
    scenario_line = scenario.loc[scenario.equipment.eq("35kV_line"), ["scenario", "r_per_length", "x_per_length", "c_per_length", "i_nom"]]
    scenario_trafo = scenario.loc[~scenario.equipment.eq("35kV_line"), ["scenario", "equipment", "s_nom_mva", "uk_percent", "load_loss_kw", "r_pu_on_s_nom", "x_pu_on_s_nom"]]
    source_links = "\n".join(f"- {key}: [{key}]({url})" for key, url in config["sources"].items())
    original_hashes = "\n".join(f"- `{name}`: `{digest}`" for name, digest in config["protected_models"].items())
    text = f"""# MN_35k PoC 전기적 모델 보완 보고서

## 판정과 범위

**SCENARIO_MODEL_READY_WITH_ASSUMPTIONS**. PyPSA {pypsa.__version__}에서 수치적으로 유효한 수동 AC 선로·변압기 모델을 별도로 생성했다. 이는 실제 몽골 운영계통 검증이나 설비 정격 확정이 아니다. **AC Power Flow는 실행하지 않았으며 수렴 여부는 미검증**이다. 발전기·부하·슬랙 및 운전점은 추가하지 않았다.

생성 파일: `{artifact['path']}` ({artifact['bytes']:,} bytes). SHA-256: `{artifact['sha256']}`. 모선/선로/변압기 173/159/27, 발전기/부하 0/0, 연결요소 166/3/2/1/1 및 고립 모선 118/124를 보존했다.

## 이전 검토와 이번 결정

실제 원본 개수와 PyPSA 1.2.4는 요청의 요약과 일치했다. 기존 보고서는 실설비 자료 부족으로 합성 모델 생성을 보류했지만 이번 요청은 공개 기술자료에 기반한 명시적 합성을 허용했다. 이전 보고서를 수정하지 않고, 새 설정·생성기·검증기 및 가정 등록부로 그 결정 범위를 분리했다. 기존 110/220 kV 변압기 10개도 신규 17개와 동일한 2000 MVA/r=0/x=0.1 기본값임을 확인했다.

## 35kV 선로 보완

48개 모두 110 kV fallback을 제거하고 `POC_35kV_SYNTHETIC_ACSR_reference`를 등록했다. 대표값은 ACSR 150/25 카탈로그 저항·직경, 1.8 m 인접 상간거리 및 50°C 저항 온도를 조합한 정상분 등가다. 33 kV 외국 설계 간격을 비교 근거로 삼았으며 몽골 35 kV 실제 배열로 간주하지 않는다. 정전용량은 외반경, 인덕턴스는 중실 원형 상당 GMR로 계산한다. DC→AC 저항 근사, 완전 연가 및 회선 상호결합 생략을 명시한다. 상세 근거와 20 kV 비교는 [technical_sources.md](technical_sources.md)에 있다.

단위: R/X=Ω/km, C=nF/km, I=kA.

{markdown_table(scenario_line)}

회선별 `r=R×km/n`, `x=X×km/n`, `b=2πf×C×10⁻⁹×km×n`, `s_nom=√3×kV×kA×n`을 검증했다. 대표 1회선 용량은 28.4922358 MVA, 2회선은 56.9844716 MVA다. 카탈로그 전류의 기상·허용온도 조건이 없어 정격 후보 신뢰도는 낮다. 선택한 저항 온도에서 검증된 열정격이 아니다.

110/220 kV는 기존 표준 타입을 유지하고 길이에 따라 r/x/b를 재계산했다. 이들 도체·번들·열정격 역시 실설비 검증값이 아니다. 다중전압 way 484276912의 두 branch에 각각 설정된 3회선, 110 kV 선로 291610988-1_0/751270980-1_0의 wires=3과 단도체 타입 불일치는 원본을 유지하고 UNRESOLVED로 분류했다.

## 변압기27개 보완

35/110 kV 17개는 대표 25 MVA, uk=10.5%, 부하손 105 kW를 적용했다. 제조사의 110/35 kV 2권선 기술표를 용량·직렬정수의 대표 근거로 사용했다. 110/220 kV 10개는 PyPSA 표준 100 MVA/uk=12%/저항성 단락전압 0.26%를 사용했다. 어느 것도 개별 합성 ID의 명판 확인을 뜻하지 않는다.

`z=uk/100`, `r=Pcu_kW/(1000×S_MVA)`, `x=sqrt(z²-r²)`로 입력하고 PyPSA의 1 MVA 기준 `r_pu=r/S`, `x_pu=x/S`를 별도 검증했다. 용량과 임피던스를 함께 계산해 일관된 기준용량을 적용했다.

{markdown_table(scenario_trafo)}

35/110 kV 저/고 사례는 40/16 MVA 제조사 대표값이다. 110/220 kV의 9.6/14.4%는 12%의 ±20% 분석용 uk 변경이며 실제 명판 범위가 아니다. 민감도는 전기정수와 Ybus까지 검증했다.

모든 27개 접속은 동일 station의 인접 전압 연결로 생성된 **SYNTHETIC_TOPOLOGY**, 적용 정수는 **STANDARD_ASSUMPTION**이다. 명판 대응은 **UNRESOLVED**다. `type=''` 수동값, 원래 LV→HV 순서, 탭비 1/위상 0을 유지한다. 제조사 표의 YNd11 결선을 채택한 것이 아니다. 철손·여자전류는 g=b=0으로 생략했으며 실제 손실이 0이라는 뜻이 아니다. 탭·벡터그룹·병렬기 수·제3권선은 후속 확인 대상이다.

몽골 설계사의 [공개 프로젝트]({config['sources']['mongolia_projects']})에는 110/35/10 kV 2×25 MVA, 220/110/35 kV 2×125 MVA가 있으나 모델 ID와 대응하지 못한다. 이 자료는 실제 다중기·제3권선 구성의 가능성을 보여주며, 개별 모델 branch의 정수 검증에는 부족하다.

## 길이 보완

{markdown_table(summary)}

최종 geometry를 선로 ID별로 원본 OSM→정제 부모→빌드 CSV/GeoJSON→NetCDF와 연결했다. 모두 ID와 geometry를 대조했으나 단순 bus-to-bus 경로가 아닌 18개(단절 11, 분기/루프 7)는 기존 길이를 유지했다. 141개만 WGS84 측지거리로 재산출했다. 이 141개에는 원경로 외 합성 연결구간 **{corrected.virtual_connector_geodesic_km.sum():.6f} km**가 포함되며 물리 선로 실측으로 분류하지 않는다. 전체 저장 길이는 {lengths.length_before_km.sum():.6f}→{lengths.length_after_km.sum():.6f} km다.

빌더는 분할 뒤 EPSG:3857 길이를 저장하고 이후 접속 geometry를 추가하지만 끝점 좌표 갱신이 주석 처리되어 일부 오래된 끝점 접속이 남는다. 따라서 각 경로의 추적 결과로 보정 여부를 판단했다. 기하체의 sub-mm 수치 조각만 topology 검사에서 허용하고 실제 단절은 거부한다. 110/220 kV에도 같은 문제가 있다. 길이 단위는 빌드 m→모델 km, 전기정수는 Ω/km·nF/km로 통일했다.

미해결 선로는 다음과 같다. 세부 원경로/접속구간 및 투영길이는 [line_length_changes.csv](line_length_changes.csv), 전압별 처리단계는 [line_length_stages.csv](line_length_stages.csv)에 있다.

{markdown_table(unresolved)}

## 출처·가정·검증

[assumption_register.csv](assumption_register.csv)는 {len(register):,}행으로 설비 ID, 파라미터, 원본/변경값, 단위, 출처, 선택 이유, 유형, 신뢰도, 실측 확보 시 교체 여부를 기록한다. SOURCE_VERIFIED는 원문 태그 확인 범위이며 field_verified는 모두 False다. [osm_line_evidence.csv](osm_line_evidence.csv)는 회선/번들 태그를 기본 가정과 분리한다. 변경 전 저장값 0과 기존 타입으로 계산한 값도 별도로 기록했다.

원본 SHA-256은 작업 전후 동일하다. 원본 OSM·정제·기존 보고서 등 {len(hashes)}개 입력의 해시는 [input_hashes.json](input_hashes.json)에 있다.

{original_hashes}

재계산·NetCDF 재로드·독립 재생성의 전기값과 파일 bytes 동일성 검증을 통과했다. PyPSA가 통상 생략하는 b_pu/g_pu도 명시적으로 저장해 raw 재로드를 대조한다. NetCDF3의 정렬된 변수로 결정적인 파일 출력을 만든다. 검증기는 연결요소 membership만 구성해 Ybus를 조립하므로 슬랙 자동 지정을 수행하지 않는다.

## 재실행

```bash
# EMS 저장소 루트에서 명시적인 검증 전용 실행. 런타임 전환 없음.
# KEMS_MN_SOURCE_PROJECT, KEMS_MN_EARTH_CHECKOUT, PROJ_DATA는 실행 환경에서 주입.
python -B -c 'from types import SimpleNamespace; from pipelines.mn.paths import resolve; from pipelines.mn.build_mn35k_poc import rebuild_for_verification; p=resolve(SimpleNamespace()); rebuild_for_verification(p, p.output_dir / "verification-new")'
```

설정은 `configs/mn35k_poc.json`, 수정은 `scripts/mn35k_refinement.py`, 길이 감사는 `scripts/mn35k_lengths.py`, 검증은 `scripts/validate_mn35k_poc.py`로 분리했다. 기존 감사 스크립트의 측지거리·연결요소 함수를 재사용했다. Pixi 환경에 이미 설치된 PyPSA 1.2.4를 사용하며 원본 hash나 버전이 다르면 중단한다.

## 기술 출처

{source_links}
"""
    (report_dir / "model_refinement_report.md").write_text(text, encoding="utf-8")
    scenario_check_rows = [{"시나리오": key, "검사수": len(value["checks"]), "실패": sum(not x["passed"] for x in value["checks"]), "Ybus수": len(value["ybus"]), "선별경고": len(value["screening_warnings"])} for key, value in validations.items()]
    ybus_rows = [{"모선수": item["shape"][0], "비영원소": item["nnz"], "유한": item["finite"], "대칭오차": item["symmetry_error"], "고립영행렬": item["isolated_zero_matrix"]} for item in reference["ybus"]]
    validation_text = f"""# MN_35k PoC 자동 검증 보고서

판정: **SCENARIO_MODEL_READY_WITH_ASSUMPTIONS**. AC PF는 실행하지 않았고 수렴은 미검증이다.

{markdown_table(pd.DataFrame(scenario_check_rows))}

구조/ID/끝점/활성상태/모선 좌표·전압/8760 스냅샷, 연결요소, 고립 모선, 필수 정수의 유한성·부호, 타입 전압 정합, 단위·회선수·용량식, 변압기 시험값·pu 기준용량, 반복 재계산을 검사했다. 전압별 선로수는 35 kV 48개, 110 kV 92개, 220 kV 19개다.

## Ybus

{markdown_table(pd.DataFrame(ybus_rows))}

참조 슬랙·PV 제어·주입값을 추가하지 않고 별도 메모리 복사본의 수동 branch로 조립했다. 고립 모선 118/124는 1×1 영행렬이다. 유한한 Ybus를 구성했다는 사실은 역행렬 존재, AC PF 수렴 또는 실운전 가능성의 증거가 아니다.

## 저장 및 재현성

- 원본 {len(hashes)}개 입력 해시가 작업 전후 동일하다.
- NetCDF 저장→raw 로드→dependent 값 재계산 후 전기 입력/파생값/사용자 타입/metadata가 동일하다.
- 독립적으로 모델을 다시 만들어 NetCDF bytes와 SHA-256이 동일함을 확인했다.
- 생성 파일 SHA-256: `{artifact['sha256']}`.
- 전체 결과: [validation_results.json](validation_results.json).

## 분포와 선별경고

변경 전 저장값/기존 타입 계산값/보완값의 전압별 min·median·mean·max는 [parameter_distributions.csv](parameter_distributions.csv)에 있다. 대표 모델의 {len(reference['screening_warnings'])}개 후보가 설정의 넓은 선별 범위를 벗어나 [screening_warnings.csv](screening_warnings.csv)에 기록됐다. 범위는 분석자의 후속 검토 기준이며 제조사 한계가 아니다. 수치 유효성 실패와 구분한다.

pytest는 기하 단절·분기·잘못된 끝점·단위·병렬회선·명판 변환·영/음/비유한값·사용자 타입 변조·NetCDF 재로드·결정성·원본 보존을 검사한다. 테스트 중 PF/LPF 호출은 강제로 실패시키므로 준비 작업이 조류계산을 실행하지 않음을 검사한다. 실제 실행 결과는 [test_results.txt](test_results.txt)에 기록한다.

기존 Pixi 환경의 NetCDF 로딩에서 numpy.ndarray 크기 관련 RuntimeWarning이 관찰됐다. import·export·재계산·Ybus·테스트는 통과했으나 후속 환경 갱신 시 ABI를 재검증해야 한다.
"""
    (report_dir / "model_validation_report.md").write_text(validation_text, encoding="utf-8")
    readiness = f"""# MN_35k PoC AC PF 준비도

## 판정

**SCENARIO_MODEL_READY_WITH_ASSUMPTIONS** — 수동 전기정수 모델과 Ybus 조립의 개발 기반을 확보했다. **AC Power Flow는 아직 실행하지 않았으며 수렴 여부는 미검증**이다. 발전기 0·부하 0이며 운전 시나리오와 슬랙은 정의하지 않았다. 실제 몽골 운영계통 검증을 뜻하지 않는다.

## 확보한 것

- 별도 `MN_35k_poc/base.nc` 생성, 원본 SHA 보존, 설정과 스크립트로 재현 가능.
- 35 kV 48개 전용 합성 타입, 변압기 27개 용량·단락임피던스·동손 기준 일치.
- 길이 141개 최종 연결 geometry 기반 보완, 18개 원본 유지·UNRESOLVED.
- 모든 필수 branch 전기값의 유한성·부호·단위 검사 통과, 3개 정수 시나리오 검증.
- 5개 Ybus 조립 가능, 고립 모선 118/124의 영행렬 명시.

## 남은 불확실성

1. 실제 도체/상간배열/AC 저항/GMR/열정격 미검증. 기존 110/220 kV 타입도 표준 가정이며 2개 회선수 분배, 2개 번들 태그 불일치를 유지했다.
2. 변압기 27개 전부 합성 station 접속이다. 명판/권선/벡터그룹/병렬기 수/탭/여자/철손은 미확인이다. 2000 MVA는 대체했지만 선택한 25/100 MVA도 대표 가정이다.
3. 기하 단절 11개, 분기/루프 7개의 길이는 EPSG:3857 기반 원본값을 유지했다. 보정 141개의 합성 접속 길이 {corrected.virtual_connector_geodesic_km.sum():.3f} km도 실제 경로로 확인되지 않았다. 운영 기반으로 쓰기 전에 선로별 보완이 필요하다.
4. 연결요소 166/3/2/1/1의 실제 계통경계/개폐상태/누락 자료는 미확인이다. 고립 모선을 임의 연결하지 않았다.
5. 발전소 27레코드의 중복·호기 구분·실접속점과 운전상태는 미확인이다. 이를 발전기 27대로 등록하지 않았다.

## 다음 단계

1. 후속 실험 목적과 대상 연결요소를 정하고 고립 모선/소형 섬의 처리 원칙을 명시한다.
2. 발전소 원본 중복 검증·실접속 자료와 발전기 제어/P/Q 한계, 부하 P/Q를 확보하거나 별도 명시적 합성 운전 시나리오로 작성한다.
3. 각 해석 대상 섬에 유효한 기준 전압원/slack, PV/PQ 제어, 전압 setpoint, 무효전력 한계 및 유효전력 balance를 정의한다.
4. 작은 검증 case에서 임피던스/탭 방향/위상 정의를 확인한 후 AC PF를 실행하고 잔차·수렴·전압·손실·정격 초과를 검토한다.
5. 대표/저/고 정수 시나리오의 조류 결과를 비교하고, 긴 미해결 선로·합성 접속·회선/번들·변압기 등가가 결론에 미치는 영향을 분리한다.

Ybus 구성 가능성만으로 AC PF 준비 완료 또는 운영계통 검증 완료라고 표시하지 않는다.
"""
    (report_dir / "pf_readiness_assessment.md").write_text(readiness, encoding="utf-8")


BUILD_FILES = (
    "base.nc", "line_parameter_changes.csv", "transformer_parameter_changes.csv",
    "line_length_changes.csv", "line_length_stages.csv", "osm_line_evidence.csv",
    "assumption_register.csv", "parameter_distributions.csv", "sensitivity_parameters.csv",
    "screening_warnings.csv", "validation_results.json", "input_hashes.json",
    "model_refinement_report.md", "model_validation_report.md", "pf_readiness_assessment.md",
    "build_provenance.json", "equivalence.json",
)


def owned_code_hashes(paths):
    files = ("build_mn35k_poc.py", "validate_mn_grid.py", "validate_mn35k_poc.py",
             "mn35k_lengths.py", "mn35k_refinement.py", "audit_mn35k_parameters.py",
             "artifacts.py", "paths.py", "passive_inputs.py", "passive_build_contract.json")
    return {"pipelines/mn/"+name: sha256(paths.ems_root / "pipelines/mn" / name) for name in files}


def compare_reference(candidate, reference, original, config):
    """Compare actual fields and sparse Ybus entries, without changing tolerances."""
    from .validate_mn35k_poc import LINE_FIELDS, TRAFO_FIELDS
    matrices, reference_matrices = {}, {}
    a = validate_model(candidate, original, config, matrices=matrices)
    b = validate_model(reference, original, config, matrices=reference_matrices)
    if not a["valid"] or not b["valid"]:
        raise ValueError("Candidate or preserved reference failed passive validation")
    assert_electrical_equal(candidate, reference)
    # The original tolerances above remain unchanged; parity also requires exact
    # stored numeric values, not a widened tolerance for this migration.
    for component, fields in (("buses", ["v_nom", "x", "y", "control"]),
                               ("lines", LINE_FIELDS), ("transformers", TRAFO_FIELDS)):
        pd.testing.assert_frame_equal(getattr(candidate, component)[fields],
                                      getattr(reference, component)[fields], check_dtype=False, check_exact=True)
    pd.testing.assert_frame_equal(candidate.line_types, reference.line_types, check_dtype=False, check_exact=True)
    if a != b:
        raise ValueError("Passive validation evidence differs from the preserved model")
    if matrices.keys() != reference_matrices.keys():
        raise ValueError("Ybus component identities differ")
    details = []
    for name, (buses, matrix) in matrices.items():
        ref_buses, ref_matrix = reference_matrices[name]
        if buses != ref_buses or matrix.shape != ref_matrix.shape:
            raise ValueError("Ybus ordering or shape differs: " + name)
        delta = matrix - ref_matrix
        error = float(np.max(np.abs(delta.data))) if delta.nnz else 0.0
        if error != 0.0:
            raise ValueError(f"Ybus entries differ: {name}: {error}")
        details.append(dict(component=name, buses=buses, shape=list(matrix.shape),
                            nnz=int(matrix.nnz), max_abs_delta=error))
    return dict(exact_equipment_values=True, metadata_equal=candidate.meta == reference.meta,
                snapshots_equal=candidate.snapshots.equals(reference.snapshots),
                components=a["components"], isolated_buses=a["isolated_buses"], ybus=details,
                original_tolerances={"rtol":1e-12, "atol":1e-14}, exact_numeric_parity=True)


def rebuild_for_verification(paths: Paths, destination: Path, *, config_path=None):
    """Explicit isolated verification rebuild; no EMS/API/CLI activation.

    Model, evidence and reports are one atomic new directory. The configuration's
    historical output_model is a provenance identifier, never the write target.
    """
    config = read_config(paths, config_path)
    require_safe_output(destination, config, paths)
    before = ensure_sources(config, paths)
    code_before = owned_code_hashes(paths)
    target = paths.safe_destination(destination)
    with new_artifact_directory(paths, destination, expected_files=BUILD_FILES,
                                protected_models=config["protected_models"]) as staged:
        original = pypsa.Network(logical_input(paths, config["input_model"]))
        lengths = audit_line_lengths(original, paths, **config["length_audit"])
        stages = summarize_length_stages(original, paths)
        evidence = osm_line_evidence(original, logical_input(paths, "pypsa-earth/resources/MN_35k/osm/raw/all_raw_lines.geojson"))
        validations, models = {}, {}
        for scenario in config["line_35kv"]:
            model = build_model(original, config, lengths, scenario)
            result = validate_model(model, original, config)
            if not result["valid"]:
                raise ValueError("Invalid model: " + scenario + ": " + json.dumps([x for x in result["checks"] if not x["passed"]]))
            again = model.copy()
            again.calculate_dependent_values()
            assert_electrical_equal(model, again)
            validations[scenario], models[scenario] = result, model
        model = models["reference"]
        first = staged / "base.nc"
        export_deterministic(model, first, paths=paths, protected_models=config["protected_models"])
        reloaded = pypsa.Network(first)
        assert_electrical_equal(model, reloaded)
        reloaded.calculate_dependent_values()
        assert_electrical_equal(model, reloaded)
        rebuilt = build_model(original, config, lengths)
        assert_electrical_equal(model, rebuilt)
        with tempfile.TemporaryDirectory(prefix=".repeat-", dir=staged) as directory:
            second = Path(directory) / "repeat.nc"
            export_deterministic(rebuilt, second, paths=paths, protected_models=config["protected_models"])
            if first.read_bytes() != second.read_bytes():
                raise ValueError("Repeated builds are not byte-identical")
        spec = contract()
        reference_path = paths.earth_checkout / spec["reference_model"]
        reference = pypsa.Network(reference_path)
        equivalence = compare_reference(pypsa.Network(first), reference, original, config)
        import xarray as xr
        with xr.open_dataset(first) as candidate_data, xr.open_dataset(reference_path) as reference_data:
            xr.testing.assert_identical(candidate_data, reference_data)
        equivalence.update(reference_sha256=spec["reference_sha256"], generated_sha256=sha256(first),
                           byte_identical_to_reference=sha256(first) == spec["reference_sha256"], netcdf_dataset_identical=True,
                           length_unresolved=int((~lengths.correction_applied).sum()))
        artifact = dict(path=str(target / "base.nc"), logical_model=config["output_model"], sha256=sha256(first),
                        bytes=first.stat().st_size, byte_identical_repeat=True, round_trip_identical=True,
                        recalculation_identical=True)
        line_changes, transformer_changes = parameter_tables(original, model, lengths)
        register = assumption_register(original, model, config, lengths, evidence)
        tables = ((line_changes, "line_parameter_changes.csv", False),
                  (transformer_changes, "transformer_parameter_changes.csv", False),
                  (lengths, "line_length_changes.csv", True), (stages, "line_length_stages.csv", False),
                  (evidence, "osm_line_evidence.csv", True), (register, "assumption_register.csv", False),
                  (distributions(original, model), "parameter_distributions.csv", False),
                  (scenario_parameters(config), "sensitivity_parameters.csv", False),
                  (pd.DataFrame(validations["reference"]["screening_warnings"], columns=["equipment", "parameter", "value", "reason"]), "screening_warnings.csv", False))
        for frame, name, index in tables:
            _csv(frame, name, index=index, report_dir=staged)
        result = dict(classification="SCENARIO_MODEL_READY_WITH_ASSUMPTIONS", pypsa_version=pypsa.__version__,
                      artifact=artifact, sources_unchanged=True, scenarios=validations,
                      ac_power_flow_executed=False, convergence_verified=False)
        _write_reports(config, lengths, register, validations, artifact, before, report_dir=staged)
        payloads = {"validation_results.json":result, "input_hashes.json":before, "equivalence.json":equivalence,
                    "build_provenance.json":dict(paths=paths.report(), owned_code_sha256=code_before,
                                                  historical_manifest_sha256=spec["historical_manifest_sha256"],
                                                  config_sha256=spec["config_sha256"], input_manifest_unchanged=True)}
        for name, value in payloads.items():
            (staged / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        for _, name, _ in tables:
            pd.read_csv(staged / name)
        if ensure_sources(config, paths) != before or owned_code_hashes(paths) != code_before:
            raise ValueError("Inputs or owned code changed during preparation")
    return dict(artifact=artifact, equivalence=equivalence, scenario_checks={k:len(v["checks"]) for k,v in validations.items()},
                assumption_rows=len(register), input_files=len(before), sources_unchanged=True, ac_pf_executed=False)
