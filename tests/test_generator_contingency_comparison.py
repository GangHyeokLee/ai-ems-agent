from ai_ems.agent.formatters import (
    format_generator_contingency_comparison,
)
from ai_ems.agent.tools import create_agent_tools


def _raw_result(slack_mode: str) -> dict:
    distributed = slack_mode == "distributed"
    return {
        "generator_id": "GEN-166#0",
        "contingency_id": "GEN_OUT_GEN-166#0",
        "slack_mode": slack_mode,
        "balance_type": (
            "PROPORTIONAL_TO_GENERATION_P_MAX" if distributed else None
        ),
        "balancing_interpretation": "load-flow balancing assumption",
        "outage_generator": {
            "generator_id": "GEN-166#0",
            "actual_generation_mw": 686.49,
        },
        "base_converged": True,
        "post_contingency_converged": True,
        "base": {
            "loadflow": {},
            "power_balance": {},
        },
        "post_contingency": {
            "loadflow": {
                "components": [{"reference_bus_id": "VL-190_0"}],
                "distributed_active_power_mw": 696.55 if distributed else 0.0,
                "active_power_mismatch_mw": 0.21 if distributed else 710.69,
            },
            "power_balance": {},
        },
        "loss_change": {
            "line_active_power_loss_change_mw": 10.27 if distributed else 24.21,
        },
        "generator_changes": (
            [
                {
                    "generator_id": "GEN-82#5",
                    "generation_before_mw": 1378.26,
                    "generation_after_mw": 1392.16,
                    "delta_generation_mw": 13.90,
                }
            ]
            if distributed
            else [
                {
                    "generator_id": "GEN-10#0",
                    "generation_before_mw": 278.57,
                    "generation_after_mw": 278.57,
                    "delta_generation_mw": 0.0,
                }
            ]
        ),
        "major_overloads": [],
        "security_analysis": {
            "pre_status": "CONVERGED",
            "post_status": "CONVERGED",
            "pre_violated_equipment_count": 0,
            "violated_equipment_count": 0,
            "pre_violated_equipment": [],
            "violated_equipment": [],
        },
    }


def test_generator_contingency_comparison_runs_both_slack_modes(monkeypatch) -> None:
    calls: list[str] = []

    def fake_analyze_generator_contingency(**kwargs):
        slack_mode = kwargs["slack_mode"]
        calls.append(slack_mode)
        return _raw_result(slack_mode)

    monkeypatch.setattr(
        "ai_ems.agent.tools.analyze_generator_contingency",
        fake_analyze_generator_contingency,
    )

    tools = create_agent_tools(object(), case_path="dummy.mat")
    comparison_tool = next(
        tool for tool in tools if tool.name == "generator_contingency_comparison"
    )

    result = comparison_tool.invoke({"generator_id": "GEN-166#0"})

    assert calls == ["single", "distributed"]
    assert result["analysis_type"] == "Generator Contingency Comparison"
    assert result["single"]["slack_mode"] == "single"
    assert result["distributed"]["slack_mode"] == "distributed"
    assert result["single"]["post_loadflow"]["active_power_mismatch_mw"] == 710.69
    assert (
        result["distributed"]["post_loadflow"]["active_power_mismatch_mw"]
        == 0.21
    )


def test_generator_contingency_comparison_formatter_is_deterministic() -> None:
    single = {
        "analysis_type": "Generator Contingency Analysis",
        "generator_id": "GEN-166#0",
        "slack_mode": "single",
        "balance_type": None,
        "outage_generator": {
            "generator_id": "GEN-166#0",
            "actual_generation_mw": 686.49,
        },
        "base_converged": True,
        "post_contingency_converged": True,
        "post_loadflow": {
            "components": [{"reference_bus_id": "VL-190_0"}],
            "distributed_active_power_mw": 0.0,
            "active_power_mismatch_mw": 710.69,
        },
        "loss_change": {"line_active_power_loss_change_mw": 24.21},
        "top_generator_changes": [],
        "major_overloads": [],
        "security": {
            "pre_violated_equipment_count": 0,
            "post_violated_equipment_count": 0,
        },
    }
    distributed = {
        **single,
        "slack_mode": "distributed",
        "balance_type": "PROPORTIONAL_TO_GENERATION_P_MAX",
        "post_loadflow": {
            "components": [{"reference_bus_id": "VL-190_0"}],
            "distributed_active_power_mw": 696.55,
            "active_power_mismatch_mw": 0.21,
        },
        "loss_change": {"line_active_power_loss_change_mw": 10.27},
        "top_generator_changes": [
            {
                "generator_id": "GEN-82#5",
                "generation_before_mw": 1378.26,
                "generation_after_mw": 1392.16,
                "delta_generation_mw": 13.90,
            }
        ],
    }

    text = format_generator_contingency_comparison(
        {
            "analysis_type": "Generator Contingency Comparison",
            "generator_id": "GEN-166#0",
            "single": single,
            "distributed": distributed,
        }
    )

    assert "단일 슬랙 / 분산 슬랙 비교 결과" in text
    assert "사고 후 유효전력 mismatch: 710.69 MW" in text
    assert "잔여 유효전력 mismatch: 0.21 MW" in text
    assert "단일 710.69 MW / 분산 0.21 MW" in text
    assert "특정 발전기가 그 양을 실제로 공급했다는 의미가 아닙니다" in text
    assert "reference bus" not in text
    assert "운영자 Redispatch가 아닙니다" in text
