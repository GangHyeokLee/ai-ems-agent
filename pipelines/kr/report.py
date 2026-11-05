"""Korean Markdown report for the topology analysis (rendered only from the computed summary)."""
from __future__ import annotations

from typing import Any

CAUSE_LABELS = {
    "1_unconnected_line_endpoints": "OSM에서 연결되지 않은 선로 끝점",
    "2_missing_substation_internal_connections": "변전소 내부 연결정보 누락",
    "3_missing_cross_voltage_transformers": "다른 전압 간 변압기 접속 누락",
    "4_geographic_crossing_is_not_connection": "지리적 교차와 전기적 접속의 차이",
    "5_shared_support_branching": "공유 지지물에서의 분기·연결",
    "6_nearby_endpoints_with_different_osm_nodes": "인접하지만 OSM 노드가 다른 끝점",
    "7_preprocessing_exclusions": "전처리에서 제외된 선로",
    "8_separate_systems_or_islands": "별도 계통·도서 설비 가능성",
    "unknown": "원인 근거 없음",
}
STATUS_LABELS = {
    "confirmed_by_model_definition": "모델 정의로 확정",
    "data_supported_candidate_unverified": "데이터 근거 있는 후보(미검증)",
    "data_observed_cause_unverified": "현상 관측, 원인 미검증",
    "excluded_by_definition": "정의상 연결 아님",
    "location_observed_electrical_status_unverified": "위치 관측, 전기적 상태 미검증",
    "not_assessed": "입력 없음으로 미평가",
    "unknown": "unknown",
}
TYPE_LABELS = {
    "same_substation_same_voltage": "같은 변전소·같은 전압",
    "same_substation_cross_voltage": "같은 변전소·다른 전압",
    "shared_osm_node_cross_voltage": "같은 OSM 노드·다른 전압",
    "nearby_bus_same_voltage": "근접 모선·같은 전압",
    "nearby_bus_cross_voltage": "근접 모선·다른 전압",
    "endpoint_near_line_same_voltage": "끝점-선로 근접·같은 전압",
    "excluded_way_bridge": "제외 선로가 성분 연결",
}
READINESS = [
    ("연결 토폴로지", "OSM 선로·노드 기반 그래프, 변전소 영역", "OSM 보완(변전소 내부 모선, 누락 태그)",
     "근접·동일 변전소 후보의 접속 여부", "단선도, 개폐기 상태, 모선 구성"),
    ("변압기 모델", "OSM 변압기 점 객체(정격·접속 없음)", "일부 변전소 전압 등급 태그",
     "전압별 성분을 잇는 변압기 위치", "변압기 목록, 권선별 접속, 임피던스, 탭, 정격"),
    ("선로 전기적 정수", "경로 길이(지리적)", "표준 도체 유형별 문헌 정수", "회선 수·도체 종류 추정",
     "회선별 R/X/B, 열적 정격, 병렬 회선 수"),
    ("발전기 모델·접속", "WRI 132건(과거 설비용량, 접속 모선 없음)", "OSM 발전소 객체, 공개 발전설비 통계",
     "발전소-모선 매칭", "운영 발전기 목록, 접속 모선, 출력 한계, 무효전력 능력"),
    ("부하 모델·접속", "없음", "지역별 전력 소비 통계", "변전소별 부하 배분", "변전소별 P/Q 실측 또는 계획값"),
    ("Slack·전압 제어", "없음", "-", "대형 발전소 Slack 가정", "운영 기준 Slack, AVR 설정, 무효전력 설비"),
    ("동일 시점 발전·부하", "없음", "전력거래소 공개 시간대별 총량", "지역 배분 가정", "같은 시점의 설비별 발전·부하·개폐 상태"),
]


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def render_report(summary: dict[str, Any], components: list[dict]) -> str:
    s, c = summary["statistics"], summary["candidates"]
    out = ["# 한국 OSM 송전망 토폴로지 분석", "",
           f"> 자동 생성 보고서. 지리적 토폴로지 분석이며 전기적으로 검증된 계통 모델이 아니다. AC PF 준비 상태: `{summary['ac_pf_readiness']}`.",
           "", "## 입력", "",
           _table(["역할", "파일", "크기(byte)", "SHA256"], [[i["role"], i["file"], i["bytes"], f"`{i['sha256']}`"] for i in summary["inputs"]]),
           "", f"OSM 기준 시점: {summary['osm_data_timestamp']}, 라이선스: {summary['license']}.", "",
           "## 그래프 구성 기준", ""]
    out += [f"- {k}: {v}" for k, v in summary["graph_definition"].items()]
    out += ["", "## 연결 성분 기본 통계", "",
            _table(["항목", "값"], [
                ["모선", s["buses"]], ["선로", s["lines"]], ["연결 성분", s["connected_components"]],
                ["최대 성분 모선/선로", f"{s['largest_component']['buses']} / {s['largest_component']['lines']} ({s['largest_component']['voltage_kv']} kV)"],
                ["최대 성분 모선 비율", f"{s['largest_component_bus_share']:.1%}"],
                ["단일 모선 고립 성분", s["isolated_single_bus_components"]], ["선로 1개 성분", s["one_line_components"]],
                ["다중 전압 성분", s["mixed_voltage_components"]], ["자기 루프", s["self_loops"]],
                ["병렬(같은 양단) 선로 그룹", s["parallel_line_groups"]], ["차수 1 모선(끝점)", s["dangling_buses"]],
                ["저장된 component_id 재현", summary["stored_component_ids_reproduced"]]]),
            "", "### 성분 크기 분포(모선 수)", "",
            _table(["모선 수", "성분 수"], [[k, v] for k, v in s["size_distribution"].items()]),
            "", "### 전압별", "",
            _table(["전압 kV", "모선", "선로", "성분", "최대 성분 모선/선로", "최대 성분 비율", "선로 1개 성분"],
                   [[kv, v["buses"], v["lines"], v["components"], f"{v['largest_component_buses']} / {v['largest_component_lines']}",
                     f"{v['largest_component_share_of_voltage_buses']:.1%}", v["one_line_components"]] for kv, v in s["by_voltage_kv"].items()])]
    out += ["", "## 기존 보고값·입력 간 비교", ""]
    baseline = summary.get("reported_baseline")
    if baseline:
        out.append(f"기존 보고 연결 성분 {baseline['connected_components']}개, 이번 계산 {s['connected_components']}개 (차이 {summary['difference_from_reported']}).")
    for comp in summary["input_comparisons"]:
        out.append(f"- `{comp['source']}`: 모선 {comp['buses']}, 선로 {comp['lines']}, 성분 {comp['connected_components']}; "
                   f"모선 ID 동일={comp['same_bus_ids']}, 선로 ID 동일={comp['same_line_ids']}, 양단 동일={comp['same_line_endpoints']}, "
                   f"성분 구성 동일={comp['same_component_membership']}")
    out += ["", "## 연결 단절 원인 후보", "",
            _table(["원인", "판정", "근거", "설명"],
                   [[CAUSE_LABELS[x["cause"]], STATUS_LABELS[x["status"]],
                     "<br>".join(f"{k}={v}" for k, v in x["evidence"].items() if not isinstance(v, dict)), x["note"]]
                    for x in summary["causes"]])]
    histogram = next(x for x in summary["causes"] if x["cause"].startswith("6_"))["evidence"]["nearest_component_pair_distance_histogram"]
    out += ["", "### 성분 쌍의 최근접 모선 거리 분포(성분 쌍 수)", "",
            _table(["거리"] + list(histogram["same_voltage"]), [[k] + list(v.values()) for k, v in histogram.items()])]
    out += ["", "## 접속 검토 후보", "",
            f"총 {c['total']}건(서로 다른 성분 쌍 {c['distinct_component_pairs']}개, 같은 전압 성분 쌍 {c['distinct_same_voltage_component_pairs']}개), "
            f"후보가 하나라도 있는 성분 {c['components_with_any_candidate']}개. 같은 성분 쌍이 여러 유형에 중복될 수 있어 `also_types` 열에 표시했다. "
            f"같은 OSM 노드·다른 전압 후보 중 {c['shared_node_cross_voltage_tower_tagged']}건은 노드가 `power=tower`(지지물)로 태깅됐다. "
            f"제외 선로 후보는 선로 {c.get('excluded_ways_with_bridge_candidates', 0)}개가 닿는 성분의 모든 쌍이다. "
            "`distance_target`이 거리의 측정 대상을 나타낸다(`bus_to_bus`: 두 모선 사이, `a_bus_to_line_route`: a 모선에서 "
            "선로 경로 위 최근접점(`route_nearest_*`)까지, `not_applicable`: 거리 없음). 끝점-선로 후보의 b 모선은 "
            "접근한 선로의 끝 모선(`b_bus_role=end_bus_of_approached_line`)일 뿐 측정 지점이 아니며, 두 모선 사이 거리는 "
            "`a_to_b_bus_distance_m`에 따로 적었다. "
            f"기준 거리: 모선 간 {summary['parameters']['bus_distance_m']:g} m, 끝점-선로 {summary['parameters']['line_distance_m']:g} m. "
            "모든 후보는 `unverified`이며 모델에 반영하지 않았다.", "",
            _table(["유형", "후보 수", "성분 쌍 수"], [[TYPE_LABELS.get(k, k), v, c["component_pairs_by_type"][k]] for k, v in c["by_type"].items()]),
            "", "### 가설적 민감도(모델 미반영)", "",
            _table(["가정", "성분 수"], [[k, v] for k, v in summary["hypothetical_component_counts"].items() if k != "note"]),
            "", summary["hypothetical_component_counts"]["note"]]
    small = [r for r in components if r["buses"] <= 3]
    out += ["", "## 고립·소규모 성분", "",
            f"모선 3개 이하 성분 {len(small)}개(모선 {sum(r['buses'] for r in small)}개). 후보가 없는 성분은 `components.csv`의 `review_flags=no_review_candidate`로 표시했다.", "",
            _table(["review_flags", "성분 수"], sorted(_flag_counts(components).items()))]
    out += ["", "## AC PF 모델 구축 준비도", "",
            _table(["항목", "확보", "공개자료로 보완 가능", "추정 필요(미검증)", "운영기관 자료 필요"], [list(r) for r in READINESS]),
            "", "지도상 연결성 개선과 물리적 계통 검증은 별개다. 위 자료 없이 AC PF·N-1·Sensitivity·OPF를 활성화하지 않는다.",
            "", "## 분석의 한계", "",
            "- OSM 변전소 영역은 점(node)으로만 그려진 경우 포함 판정이 되지 않는다.",
            "- 거리는 구면 근사(haversine)이며 측량 정확도가 아니다. 근접은 접속의 증거가 아니다.",
            "- 같은 변전소 영역 안의 성분도 개폐 상태에 따라 실제로 분리돼 있을 수 있다.",
            "- 공유 지지물 분기는 과연결 위험으로, 성분 수만으로는 드러나지 않는다.",
            "- 섬·HVDC 판단은 위치 근거이며 운전 상태는 확인하지 않았다.", ""]
    return "\n".join(out)


def _flag_counts(components: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in components:
        for flag in (row["review_flags"] or "none").split(";"):
            counts[flag] = counts.get(flag, 0) + 1
    return counts
