from __future__ import annotations

from typing import Any

import networkx as nx
import numpy as np

from ..rng import RandomManager
from ..utils.graph_utils import ordered_nodes, ordered_pairs
from ..utils.routing import resolve_routed_path
from ..utils.selection import weighted_pick


def _clamp_non_negative(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=float)
    matrix[matrix < 0] = 0.0
    return matrix


def _matrix_to_demand_map(nodes: list[str], matrix: np.ndarray) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for i, src in enumerate(nodes):
        for j, dst in enumerate(nodes):
            if src == dst:
                continue
            result[(src, dst)] = float(matrix[i, j])
    return result


def _try_tmgen(mode: str, nodes: list[str], rng: RandomManager) -> dict[tuple[str, str], float] | None:
    try:
        import tmgen  # type: ignore
    except Exception:
        return None

    n = len(nodes)

    candidates = [
        ("models", mode),
        (None, mode),
    ]

    for parent_name, func_name in candidates:
        try:
            parent = getattr(tmgen, parent_name) if parent_name else tmgen
            func = getattr(parent, func_name)
            matrix = np.asarray(func(n=n, seed=rng.seed), dtype=float)
            if matrix.shape != (n, n):
                continue
            np.fill_diagonal(matrix, 0.0)
            return _matrix_to_demand_map(nodes, _clamp_non_negative(matrix))
        except Exception:
            continue

    return None


def _active_tm_rule(mode: str, tm_cfg: dict[str, Any]) -> dict[str, Any]:
    if mode == "uniform":
        low, high = tm_cfg.get("uniform_range_mbps", [1.0, 100.0])
        return {"uniform_range_mbps": [float(low), float(high)]}
    if mode == "exponential":
        return {"exponential_scale": float(tm_cfg.get("exponential_scale", 20.0))}
    if mode == "gravity":
        gravity_cfg = tm_cfg.get("gravity", {})
        mass_low, mass_high = gravity_cfg.get("mass_range", [0.5, 2.0])
        return {
            "mass_range": [float(mass_low), float(mass_high)],
            "scale": float(gravity_cfg.get("scale", 100.0)),
        }
    if mode == "spike":
        spike_cfg = tm_cfg.get("spike", {})
        base_low, base_high = spike_cfg.get("baseline_range_mbps", [1.0, 20.0])
        return {
            "baseline_range_mbps": [float(base_low), float(base_high)],
            "spike_probability": float(spike_cfg.get("spike_probability", 0.05)),
            "spike_multiplier": float(spike_cfg.get("spike_multiplier", 10.0)),
        }
    return {}


def resolve_traffic_matrix_selection(tm_cfg: dict[str, Any], rng: RandomManager) -> str:
    mode_probabilities = dict(tm_cfg.get("mode_probabilities", {}))
    fallback_mode = str(tm_cfg.get("mode", "uniform"))
    return weighted_pick(mode_probabilities, fallback_mode, rng)


def _generate_tm_demands(
    nodes: list[str],
    tm_cfg: dict[str, Any],
    rng: RandomManager,
) -> tuple[dict[tuple[str, str], float], dict[str, Any]]:
    mode = resolve_traffic_matrix_selection(tm_cfg, rng)
    n = len(nodes)
    active_rule = _active_tm_rule(mode, tm_cfg)

    if mode in {"uniform", "exponential", "gravity", "spike"}:
        tmgen_result = _try_tmgen(mode, nodes, rng)
        if tmgen_result is not None:
            return tmgen_result, {
                "selected_mode": mode,
                "backend": "tmgen",
                "active_rule": active_rule,
            }

    if mode == "uniform":
        low, high = tm_cfg.get("uniform_range_mbps", [1.0, 100.0])
        matrix = rng.np_uniform(float(low), float(high), (n, n))
    elif mode == "exponential":
        scale = float(tm_cfg.get("exponential_scale", 20.0))
        matrix = rng.np_exponential(scale, (n, n))
    elif mode == "gravity":
        gravity_cfg = tm_cfg.get("gravity", {})
        mass_low, mass_high = gravity_cfg.get("mass_range", [0.5, 2.0])
        scale = float(gravity_cfg.get("scale", 100.0))
        masses = rng.np_uniform(float(mass_low), float(mass_high), n)
        outer = np.outer(masses, masses)
        if float(np.sum(outer)) > 0:
            matrix = scale * outer / float(np.max(outer))
        else:
            matrix = np.zeros((n, n), dtype=float)
    elif mode == "spike":
        spike_cfg = tm_cfg.get("spike", {})
        base_low, base_high = spike_cfg.get("baseline_range_mbps", [1.0, 20.0])
        spike_prob = float(spike_cfg.get("spike_probability", 0.05))
        multiplier = float(spike_cfg.get("spike_multiplier", 10.0))

        matrix = rng.np_uniform(float(base_low), float(base_high), (n, n))
        mask = rng.np_random((n, n)) < spike_prob
        matrix = np.where(mask, matrix * multiplier, matrix)
    else:
        raise ValueError(f"Unsupported traffic_matrix.mode: {mode}")

    matrix = _clamp_non_negative(np.asarray(matrix, dtype=float))
    np.fill_diagonal(matrix, 0.0)
    return _matrix_to_demand_map(nodes, matrix), {
        "selected_mode": mode,
        "backend": "builtin",
        "active_rule": active_rule,
    }


def _float_range(spec: list[float] | tuple[float, float], default: tuple[float, float]) -> tuple[float, float]:
    if not isinstance(spec, (list, tuple)) or len(spec) != 2:
        return default
    return float(spec[0]), float(spec[1])


def resolve_flow_feature_selection(flow_feature_cfg: dict[str, Any], rng: RandomManager) -> dict[str, Any]:
    selection_mode_probabilities = dict(flow_feature_cfg.get("selection_mode_probabilities", {}))
    if selection_mode_probabilities:
        selected_mode = weighted_pick(selection_mode_probabilities, "mixed", rng)
    else:
        selected_mode = str(flow_feature_cfg.get("selection_mode", "mixed"))

    single_model_probabilities = dict(flow_feature_cfg.get("single_model_probabilities", {}))
    if single_model_probabilities:
        selected_single_model = weighted_pick(
            single_model_probabilities,
            str(flow_feature_cfg.get("single_model", "poisson")),
            rng,
        )
    else:
        selected_single_model = str(flow_feature_cfg.get("single_model", "poisson"))

    return {
        "selected_mode": selected_mode,
        "selected_single_model": selected_single_model,
    }


def _blank_row(flow_id: str, src: str, dst: str, demand: float, model: str) -> dict[str, Any]:
    return {
        "flow_id": flow_id,
        "src": src,
        "dst": dst,
        "demand_mbps": round(float(demand), 6),
        "feature_model": model,
    }


def _fill_feature_params(row: dict[str, Any], model: str, flow_feature_cfg: dict[str, Any], rng: RandomManager) -> None:
    if model == "poisson":
        low, high = _float_range(flow_feature_cfg.get("poisson", {}).get("lambda_range", [1.0, 50.0]), (1.0, 50.0))
        row["param_lambda"] = round(rng.uniform(low, high), 6)
    elif model == "on_off":
        on_low, on_high = _float_range(flow_feature_cfg.get("on_off", {}).get("on_mean_range", [0.2, 5.0]), (0.2, 5.0))
        off_low, off_high = _float_range(flow_feature_cfg.get("on_off", {}).get("off_mean_range", [0.2, 6.0]), (0.2, 6.0))
        peak_low, peak_high = _float_range(
            flow_feature_cfg.get("on_off", {}).get("peak_rate_range_mbps", [10.0, 200.0]),
            (10.0, 200.0),
        )
        row["param_on_mean"] = round(rng.uniform(on_low, on_high), 6)
        row["param_off_mean"] = round(rng.uniform(off_low, off_high), 6)
        row["param_peak_rate_mbps"] = round(rng.uniform(peak_low, peak_high), 6)
    elif model == "cbr":
        return
    else:
        raise ValueError(f"Unsupported flow feature model: {model}")


def _choose_feature_model(
    flow_feature_cfg: dict[str, Any],
    rng: RandomManager,
    selection: dict[str, Any],
) -> str:
    selection_mode = str(selection.get("selected_mode", "mixed"))
    if selection_mode == "single":
        return str(selection.get("selected_single_model", "poisson"))

    probs = flow_feature_cfg.get("mode_probabilities", {})
    models = list(probs.keys())
    weights = [float(v) for v in probs.values()]
    return str(rng.weighted_choice(models, weights))


def _select_flow_pairs(nodes: list[str], tm_cfg: dict[str, Any], rng: RandomManager) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    all_pairs = ordered_pairs(nodes, include_self=False)
    requested_range = tm_cfg.get("flow_count_range")
    total_pairs = len(all_pairs)

    if requested_range is None:
        return all_pairs, {
            "available_flow_pairs": int(total_pairs),
            "requested_flow_ratio_range": None,
            "selected_flow_ratio": 1.0,
            "selected_flow_count": int(total_pairs),
            "effective_flow_count": int(total_pairs),
            "sampled": False,
        }

    min_ratio = max(0.0, min(1.0, float(requested_range[0])))
    max_ratio = max(0.0, min(1.0, float(requested_range[1])))
    if min_ratio > max_ratio:
        min_ratio, max_ratio = max_ratio, min_ratio

    selected_ratio = rng.uniform(min_ratio, max_ratio)
    count = int(round(total_pairs * selected_ratio))
    effective_count = min(count, total_pairs)
    if effective_count >= total_pairs:
        return all_pairs, {
            "available_flow_pairs": int(total_pairs),
            "requested_flow_ratio_range": [float(min_ratio), float(max_ratio)],
            "selected_flow_ratio": round(float(selected_ratio), 6),
            "selected_flow_count": int(count),
            "effective_flow_count": int(total_pairs),
            "sampled": False,
        }

    selected = set(rng.sample(all_pairs, effective_count))
    selected_pairs = [pair for pair in all_pairs if pair in selected]
    return selected_pairs, {
        "available_flow_pairs": int(total_pairs),
        "requested_flow_ratio_range": [float(min_ratio), float(max_ratio)],
        "selected_flow_ratio": round(float(selected_ratio), 6),
        "selected_flow_count": int(count),
        "effective_flow_count": int(effective_count),
        "sampled": True,
    }


def _feature_rate_limit(row: dict[str, Any]) -> float | None:
    model = str(row.get("feature_model", ""))
    if model == "on_off":
        peak_rate = row.get("param_peak_rate_mbps")
        if peak_rate in (None, ""):
            return None
        peak_value = float(peak_rate)
        if peak_value <= 0:
            return None
        return peak_value

    return None


def apply_hard_traffic_constraints(
    traffic_rows: list[dict[str, Any]],
    routing_map: dict[tuple[str, str], str],
    channel_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    del channel_rows
    constrained_rows: list[dict[str, Any]] = []

    unreachable_flows_zeroed = 0
    invalid_route_flows_zeroed = 0
    flows_capped_by_feature_limit = 0

    for row in traffic_rows:
        new_row = dict(row)
        src = str(new_row["src"])
        dst = str(new_row["dst"])
        demand = float(new_row.get("demand_mbps", 0.0))

        path = resolve_routed_path(src, dst, routing_map)
        if path is None:
            if demand > 0:
                unreachable_flows_zeroed += 1
            new_row["demand_mbps"] = 0.0
            constrained_rows.append(new_row)
            continue

        constrained_demand = float(demand)
        feature_limit = _feature_rate_limit(new_row)
        if feature_limit is not None and constrained_demand > feature_limit:
            constrained_demand = float(feature_limit)
            flows_capped_by_feature_limit += 1

        new_row["demand_mbps"] = round(float(constrained_demand), 6)

        constrained_rows.append(new_row)

    return constrained_rows, {
        "drop_unreachable_demands": True,
        "cap_per_flow_to_path_bottleneck": False,
        "cap_per_flow_to_feature_limit": True,
        "unreachable_flows_zeroed": int(unreachable_flows_zeroed),
        "invalid_route_flows_zeroed": int(invalid_route_flows_zeroed),
        "flows_capped_by_path_bottleneck": 0,
        "flows_capped_by_feature_limit": int(flows_capped_by_feature_limit),
    }


def generate_traffic(
    graph: nx.Graph,
    config: Any,
    rng: RandomManager,
    include_metadata: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    nodes = ordered_nodes(graph)
    tm_demands, tm_metadata = _generate_tm_demands(nodes, config.traffic_matrix, rng)
    flow_feature_cfg = config.flow_feature
    flow_feature_selection = resolve_flow_feature_selection(flow_feature_cfg, rng)
    flow_pairs, sampling_metadata = _select_flow_pairs(nodes, config.traffic_matrix, rng)

    rows: list[dict[str, Any]] = []
    flow_idx = 1

    for src, dst in flow_pairs:
        demand = tm_demands.get((src, dst), 0.0)
        model = _choose_feature_model(flow_feature_cfg, rng, flow_feature_selection)

        row = _blank_row(f"F{flow_idx:06d}", src, dst, demand, model)
        _fill_feature_params(row, model, flow_feature_cfg, rng)

        rows.append(row)
        flow_idx += 1

    if not include_metadata:
        return rows

    selected_flow_mode = str(flow_feature_selection.get("selected_mode", "mixed"))
    if selected_flow_mode == "single":
        selected_model = str(flow_feature_selection.get("selected_single_model", "poisson"))
        flow_feature_metadata = {
            "selection_mode": selected_flow_mode,
            "active_rule": {
                "model": selected_model,
                "parameters": dict(flow_feature_cfg.get(selected_model, {})),
            },
        }
    else:
        active_models = [str(model) for model in flow_feature_cfg.get("mode_probabilities", {}).keys()]
        flow_feature_metadata = {
            "selection_mode": selected_flow_mode,
            "active_rule": {
                "mode_probabilities": dict(flow_feature_cfg.get("mode_probabilities", {})),
                "model_parameters": {model: dict(flow_feature_cfg.get(model, {})) for model in active_models},
            },
        }

    return rows, {
        "traffic_matrix": {**tm_metadata, "flow_sampling": sampling_metadata},
        "flow_feature": flow_feature_metadata,
    }
