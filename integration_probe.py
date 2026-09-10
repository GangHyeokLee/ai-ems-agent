import json
import os
from urllib import error, request

BASE_URL = os.getenv(
    "AI_EMS_PHYSICS_BASE_URL",
    "http://127.0.0.1:8001",
)


MOCK_RISK_CANDIDATE = {
    "line_id": "LINE-183-190",
    "risk_score": 0.91,
}

MOCK_CONTROL_CANDIDATE = {
    "up_generator_id": "GEN-10#0",
    "down_generator_id": "GEN-190",
    "delta_mw": 10.0,
}


def post_json(
    path: str,
    payload: dict,
) -> dict:
    body = json.dumps(payload).encode("utf-8")

    req = request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))

    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8")

        raise RuntimeError(f"{path} failed: " f"HTTP {exc.code} {detail}") from exc


def main() -> None:
    outage_line_id = MOCK_RISK_CANDIDATE["line_id"]

    print("=== Mock Simulator Input ===")
    print(
        json.dumps(
            MOCK_RISK_CANDIDATE,
            indent=2,
        )
    )

    # 1. AI / GNN 위험 후보를 물리적으로 검증
    security = post_json(
        "/api/v1/security-analysis",
        {
            "outage_line_id": outage_line_id,
        },
    )

    print("\n=== Security Validation ===")
    print(
        "post violations:",
        security["post_violated_equipment_count"],
    )
    print(
        "new violations:",
        [item["equipment_id"] for item in security["new_violations"]],
    )

    # 2. 사고 후 문제 선로에 영향도가 큰 발전기 탐색
    sensitivity = post_json(
        "/api/v1/sensitivity-analysis",
        {
            "outage_line_id": outage_line_id,
            "top_n": 5,
        },
    )

    print("\n=== Sensitivity Analysis ===")
    print(
        "target line:",
        sensitivity["monitored_line_id"],
    )

    for candidate in sensitivity["candidates"]:
        print(
            candidate["generator_id"],
            candidate["sensitivity"],
        )

    # 3. 외부 시스템이 만든 제어 후보 검증
    redispatch = post_json(
        "/api/v1/redispatch-validation",
        {
            "outage_line_id": outage_line_id,
            "monitored_line_id": (sensitivity["monitored_line_id"]),
            **MOCK_CONTROL_CANDIDATE,
        },
    )

    print("\n=== Redispatch Validation ===")
    print(
        "redispatch:",
        MOCK_CONTROL_CANDIDATE,
    )
    print(
        "apparent power:",
        f"{redispatch['apparent_power_before_mva']:.2f}",
        "->",
        f"{redispatch['apparent_power_after_mva']:.2f}",
        "MVA",
    )
    print(
        "loading:",
        f"{redispatch['loading_before_percent']:.2f}",
        "->",
        f"{redispatch['loading_after_percent']:.2f}",
        "%",
    )
    print(
        "improved:",
        redispatch["improved"],
    )
    print(
        "violation remaining:",
        redispatch["violation_remaining"],
    )
    print(
        "new violation detected:",
        redispatch["new_violation_detected"],
    )

    print("\n=== Integration Summary ===")
    print(
        "risk score:",
        MOCK_RISK_CANDIDATE["risk_score"],
        "(kept by simulator)",
    )
    print(
        "physics target:",
        sensitivity["monitored_line_id"],
    )
    print(
        "physics validation:",
        ("CONVERGED" if redispatch["whole_network_converged"] else "NOT CONVERGED"),
    )


if __name__ == "__main__":
    main()
