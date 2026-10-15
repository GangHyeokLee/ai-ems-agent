"""Pure helpers for conservative adaptive balanced Redispatch screening.

Sensitivity predicts active-power flow (MW), not apparent-power overload (MVA).
Every candidate must subsequently pass AC and Security validation.
"""
from __future__ import annotations

from math import copysign, hypot, isfinite, sqrt
from typing import Any, Callable


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def calculate_feasible_delta_range(up: dict, down: dict) -> float:
    """Conservative paired MW headroom using target AND solved actual outputs.

    Missing min/max/target/actual information disqualifies automatic control.
    """
    limits = []
    for state, direction in ((up, 1), (down, -1)):
        if not state.get("connected", False):
            return 0.0
        lo = _finite(state.get("min_p_mw"))
        hi = _finite(state.get("max_p_mw"))
        target = _finite(state.get("target_p_mw"))
        actual = _finite(state.get("actual_generation_mw"))
        if None in (lo, hi, target, actual) or lo > hi:
            return 0.0
        if not (lo <= target <= hi and lo <= actual <= hi):
            return 0.0
        for point in (target, actual):
            limits.append((hi - point) if direction == 1 else (point - lo))
    return max(0.0, min(limits))


def estimate_redispatch_delta(
    p1_mw: float,
    q1_mvar: float | None,
    limit_mva: float | None,
    sensitivity_difference: float,
    max_delta_mw: float,
) -> float | None:
    """Estimate a starting delta under an explicitly constant-Q approximation.

    Returns None when the local MW approximation cannot estimate MVA relief.
    Never represents a physically validated control magnitude.
    """
    p = _finite(p1_mw)
    q = _finite(q1_mvar)
    limit = _finite(limit_mva)
    k = _finite(sensitivity_difference)
    bound = _finite(max_delta_mw)
    if None in (p, q, limit, k, bound):
        return None
    if limit <= 0 or bound <= 0 or abs(k) < 1e-9 or abs(q) >= limit:
        return None
    allowed_p = sqrt(max(0.0, limit * limit - q * q))
    if abs(p) <= allowed_p:
        return None
    desired_p = copysign(allowed_p, p)
    estimated = (desired_p - p) / k
    if not isfinite(estimated) or estimated <= 0:
        return None
    return min(estimated, bound)



def estimate_two_end_redispatch_delta(
    before_flows: dict[str, float],
    probe_flows: dict[str, float],
    probe_delta_mw: float,
    limit_mva: float | None,
    max_delta_mw: float,
) -> dict[str, float] | None:
    """Local AC finite-difference estimate for clearing BOTH line terminals.

    Approximate P and Q independently at each end using a small, feasible
    balanced redispatch. Find the first positive delta where S <= RateC.
    Neither the local slopes nor this estimate replace final AC/Security checks.
    """
    step = _finite(probe_delta_mw)
    limit = _finite(limit_mva)
    bound = _finite(max_delta_mw)
    if (
        step is None or step <= 1e-6 or limit is None or limit <= 0
        or bound is None or bound <= 0
    ):
        return None

    crossing_deltas: list[float] = []
    for side in (1, 2):
        p = _finite(before_flows.get(f"p{side}_mw"))
        q = _finite(before_flows.get(f"q{side}_mvar"))
        p_probe = _finite(probe_flows.get(f"p{side}_mw"))
        q_probe = _finite(probe_flows.get(f"q{side}_mvar"))
        if None in (p, q, p_probe, q_probe):
            return None
        if hypot(p, q) <= limit:
            continue

        dp = (p_probe - p) / step
        dq = (q_probe - q) / step
        a = dp * dp + dq * dq
        b = 2.0 * (p * dp + q * dq)
        c = p * p + q * q - limit * limit
        if a < 1e-12 or b >= 0:
            return None
        discriminant = b * b - 4.0 * a * c
        if discriminant < 0:
            return None
        earliest = (-b - sqrt(max(discriminant, 0.0))) / (2.0 * a)
        if not isfinite(earliest) or earliest <= 0:
            return None
        crossing_deltas.append(earliest)

    if not crossing_deltas:
        return None
    required = max(crossing_deltas)
    return {
        "estimated_delta_mw": min(required, bound),
        "unclamped_estimated_delta_mw": required,
        "estimate_clipped_by_headroom": required > bound,
    }


def generate_delta_candidates(
    estimate_mw: float | None,
    max_delta_mw: float,
    baseline_mw: float = 10.0,
    max_candidates: int = 5,
) -> list[float]:
    """Include the feasible maximum while respecting the evaluation budget.

    The previous baseline-first truncation could omit both the 1.25x estimate
    and the maximum. Priority is baseline, maximum, estimate, then additional
    nearby points. Duplicate/clipped magnitudes are removed.
    """
    bound = _finite(max_delta_mw)
    if bound is None or bound <= 0 or max_candidates < 1:
        return []
    estimate = _finite(estimate_mw)
    baseline = _finite(baseline_mw)
    if baseline is None or baseline <= 0:
        baseline = min(10.0, bound)

    if estimate is not None and estimate > 0:
        points = [
            baseline, bound, estimate, 1.25 * estimate,
            0.5 * estimate, 0.75 * estimate, 0.25 * estimate,
        ]
    else:
        points = [
            baseline, bound, 0.2 * bound, 0.4 * bound,
            0.6 * bound, 0.8 * bound,
        ]

    distinct: list[float] = []
    for raw in points:
        delta = min(bound, raw)
        if delta <= 1e-6:
            continue
        if not any(abs(delta - prior) <= 1e-6 for prior in distinct):
            distinct.append(delta)
    return distinct[:max_candidates]


def build_adaptive_redispatch_candidates(
    post_generators: dict[str, dict],
    sensitivities: dict[str, float],
    post_p1_mw: float,
    post_q1_mvar: float | None,
    post_p2_mw: float | None,
    post_q2_mvar: float | None,
    limit_mva: float | None,
    top_n: int = 3,
    max_deltas_per_pair: int = 5,
    baseline_mw: float = 10.0,
    flow_probe: Callable[[str, str, float], dict[str, float] | None] | None = None,
) -> list[dict[str, Any]]:
    """Select diversified generator pairs, then propose bounded MW magnitudes.

    P1 sensitivity ranks generator pairs. An optional small AC probe supplies
    local P/Q slopes at both ends for magnitude estimation. If that probe fails,
    use conservative bounded fallback points without claiming an estimate.
    """
    if top_n < 1 or max_deltas_per_pair < 1:
        raise ValueError("top_n and max_deltas_per_pair must be positive.")
    p1 = _finite(post_p1_mw)
    if p1 is None:
        return []
    p2 = _finite(post_p2_mw)
    q1 = _finite(post_q1_mvar)
    q2 = _finite(post_q2_mvar)
    estimate_supported = (
        p2 is not None and q1 is not None and q2 is not None
        and hypot(p1, q1) >= hypot(p2, q2) - 1e-6
    )
    pairs = []
    ids = sorted(set(post_generators) & set(sensitivities))
    for up_id in ids:
        up = post_generators[up_id]
        up_sens = _finite(sensitivities[up_id])
        if up_sens is None:
            continue
        for down_id in ids:
            if up_id == down_id:
                continue
            down = post_generators[down_id]
            down_sens = _finite(sensitivities[down_id])
            if down_sens is None:
                continue
            bound = calculate_feasible_delta_range(up, down)
            if bound <= 1e-6:
                continue
            k = up_sens - down_sens
            if abs(k) < 1e-9:
                continue
            probe = min(baseline_mw, bound)
            predicted_p1 = p1 + k * probe
            reduction = abs(p1) - abs(predicted_p1)
            if reduction <= 0:
                continue
            pairs.append((reduction, up_id, down_id, bound, k))

    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    used_bus_pairs = set()
    candidates = []
    selected_pair_count = 0
    for _, up_id, down_id, bound, k in pairs:
        up = post_generators[up_id]
        down = post_generators[down_id]
        up_bus = up.get("bus_id")
        down_bus = down.get("bus_id")
        # Use the existing same-bus pair diversification policy.
        pair_key = (
            ("bus", str(up_bus), str(down_bus))
            if up_bus is not None and down_bus is not None
            else ("generator", up_id, down_id)
        )
        if pair_key in used_bus_pairs:
            continue
        used_bus_pairs.add(pair_key)
        selected_pair_count += 1
        estimate_details = None
        estimation_method = "BOUNDED_FALLBACK"
        if flow_probe is not None:
            probe_delta = min(float(baseline_mw), bound * 0.25)
            if probe_delta > 1e-4:
                try:
                    probe_flows = flow_probe(up_id, down_id, probe_delta)
                except (RuntimeError, ValueError, ArithmeticError):
                    probe_flows = None
                if probe_flows is not None:
                    estimate_details = estimate_two_end_redispatch_delta(
                        before_flows={
                            "p1_mw": p1,
                            "q1_mvar": q1,
                            "p2_mw": p2,
                            "q2_mvar": q2,
                        },
                        probe_flows=probe_flows,
                        probe_delta_mw=probe_delta,
                        limit_mva=limit_mva,
                        max_delta_mw=bound,
                    )
                    if estimate_details is not None:
                        estimation_method = "AC_TWO_END_FINITE_DIFFERENCE"
        elif estimate_supported:
            estimate = estimate_redispatch_delta(p1, q1, limit_mva, k, bound)
            if estimate is not None:
                estimate_details = {
                    "estimated_delta_mw": estimate,
                    "unclamped_estimated_delta_mw": None,
                    "estimate_clipped_by_headroom": None,
                }
                estimation_method = "CONSTANT_Q_P1_APPROXIMATION"

        estimated_delta = (
            estimate_details["estimated_delta_mw"]
            if estimate_details is not None else None
        )
        for delta in generate_delta_candidates(
            estimated_delta, bound, baseline_mw, max_deltas_per_pair
        ):
            predicted_p1 = p1 + k * delta
            predicted_relief = abs(p1) - abs(predicted_p1)
            if predicted_relief <= 0:
                continue
            candidates.append({
                "rank": len(candidates) + 1,
                "up_generator_id": up_id,
                "down_generator_id": down_id,
                "up_bus_id": up_bus,
                "down_bus_id": down_bus,
                "delta_mw": delta,
                "feasible_delta_max_mw": bound,
                "estimated_delta_mw": estimated_delta,
                "estimated_delta_unclamped_mw": (
                    estimate_details.get("unclamped_estimated_delta_mw")
                    if estimate_details else None
                ),
                "estimate_clipped_by_headroom": (
                    estimate_details.get("estimate_clipped_by_headroom")
                    if estimate_details else None
                ),
                "estimation_method": estimation_method,
                "net_requested_change_mw": 0.0,
                "up_sensitivity_mw_per_mw": sensitivities[up_id],
                "down_sensitivity_mw_per_mw": sensitivities[down_id],
                "predicted_active_power_change_mw": k * delta,
                "predicted_p1_mw": predicted_p1,
                "predicted_abs_p1_reduction_mw": predicted_relief,
            })
        if selected_pair_count >= top_n:
            break
    return candidates


def _violation_key(item: dict) -> tuple[str, str, str]:
    return (
        str(item.get("equipment_id", "")),
        str(item.get("limit_type", "")),
        str(item.get("limit_name", "")),
    )


def normalized_violation_excess(item: dict) -> float | None:
    """Unit-free excess relative to each limit (not a probability/risk score)."""
    value = _finite(item.get("value"))
    limit = _finite(item.get("limit"))
    typ = item.get("limit_type")
    if value is None or limit is None or limit <= 0:
        return None
    if typ == "LOW_VOLTAGE":
        return max(0.0, (limit - value) / limit)
    if typ in {"HIGH_VOLTAGE", "CURRENT", "ACTIVE_POWER", "APPARENT_POWER"}:
        return max(0.0, (value - limit) / limit)
    return None


def evaluate_violation_risk(
    before: list[dict],
    after: list[dict],
    post_status: str,
    strategy_status: str,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Assess a single contingency's residual violations, not all N-1 cases."""
    bmap = {_violation_key(item): item for item in before}
    amap = {_violation_key(item): item for item in after}
    before_excess = {key: normalized_violation_excess(v) for key, v in bmap.items()}
    after_excess = {key: normalized_violation_excess(v) for key, v in amap.items()}
    metrics_available = all(x is not None for x in (
        list(before_excess.values()) + list(after_excess.values())
    ))
    new_keys = set(amap) - set(bmap)
    worsened = [
        key for key in set(amap) & set(bmap)
        if before_excess[key] is not None and after_excess[key] is not None
        and after_excess[key] > before_excess[key] + tolerance
    ]
    after_values = list(after_excess.values())
    before_values = list(before_excess.values())
    reasons = []
    if post_status != "CONVERGED" or strategy_status != "CONVERGED":
        reasons.append("SECURITY_NOT_CONVERGED")
    if not metrics_available:
        reasons.append("UNQUANTIFIED_VIOLATION")
    if new_keys:
        reasons.append("NEW_VIOLATION")
    if worsened:
        reasons.append("EXISTING_VIOLATION_WORSENED")
    before_total = sum(before_values) if metrics_available else None
    after_total = sum(after_values) if metrics_available else None
    if before_total is not None and after_total is not None:
        if after_total >= before_total - tolerance:
            reasons.append("NO_GLOBAL_SEVERITY_IMPROVEMENT")
    return {
        "recommendation_eligible": not reasons,
        "rejection_reasons": reasons,
        "risk_metrics": {
            "metrics_available": metrics_available,
            "new_violation_count": len(new_keys),
            "worsened_existing_count": len(worsened),
            "remaining_violation_count": len(amap),
            "remaining_equipment_count": len({k[0] for k in amap}),
            "max_normalized_excess": (
                max(after_values, default=0.0) if metrics_available else None
            ),
            "total_normalized_excess": after_total,
            "baseline_total_normalized_excess": before_total,
        },
    }


def adaptive_candidate_sort_key(item: dict) -> tuple:
    validation = item.get("ac_validation") or {}
    assessment = item.get("risk_assessment") or {}
    risk = assessment.get("risk_metrics") or {}
    after = validation.get("after_redispatch") or {}
    whole = validation.get("whole_network_validation") or {}
    physically_valid = (
        after.get("converged") is True
        and whole.get("operator_strategy_status") == "CONVERGED"
    )
    def metric(name: str) -> float:
        result = _finite(risk.get(name))
        return result if result is not None else float("inf")
    return (
        0 if assessment.get("recommendation_eligible") else 1,
        0 if physically_valid else 1,
        metric("max_normalized_excess"),
        metric("total_normalized_excess"),
        metric("remaining_violation_count"),
        -(_finite(validation.get("improvement_mva")) or 0.0),
        _finite(item.get("redispatch", {}).get("delta_mw")) or float("inf"),
        item.get("rank", 0),
    )
