from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .utils.validation import validate_scene_config


@dataclass(frozen=True)
class TopologySourceConfig:
    name: str
    type: str
    weight: float
    root_dirs: list[Path]
    glob_patterns: list[str]


@dataclass(frozen=True)
class SceneConfig:
    output_root: Path
    seed: int
    num_scenes: int
    scene_duration: float
    topology_sources: list[TopologySourceConfig]
    link_generation: dict[str, Any]
    nics: dict[str, Any]
    nodes: dict[str, Any]
    routing: dict[str, Any]
    traffic_matrix: dict[str, Any]
    flow_feature: dict[str, Any]
    events: dict[str, Any]
    config_path: Path


_DEFAULT_GLOBS: dict[str, list[str]] = {
    "brite": ["**/*.brite", "**/*.BRITE"],
    "topologyzoo": ["**/*.gml"],
}

_DEFAULT_LINK_CONFIG = {
    "mode": "pure_random",
    "preserve_input_bandwidth": True,
    "treat_as_undirected": True,
    "pure_random": {
        "bandwidth_candidates_mbps": [100.0, 1000.0, 10000.0],
        "uniform_range_mbps": [100.0, 10000.0],
    },
    "role_based_random": {
        "role_bandwidth_mbps": {
            "backbone": {"bandwidth_candidates_mbps": [10000.0, 40000.0, 100000.0]},
            "uplink": {"bandwidth_candidates_mbps": [1000.0, 10000.0]},
            "access": {"bandwidth_candidates_mbps": [100.0, 1000.0]},
            "lateral": {"bandwidth_candidates_mbps": [1000.0, 10000.0]},
        },
        "derived_link_role": {
            "aggregation_aggregation_lateral_probability": 0.7,
            "core_edge_uplink_probability": 0.8,
        },
        "trust_input_link_roles": False,
        "trusted_link_role_fields": [
            "source_link_role",
            "link_role",
            "edge_role",
            "role",
            "link_type",
            "edge_type",
            "type",
        ],
    },
}

_DEFAULT_NIC_CONFIG = {
    "queue_policy_mode": "mixed",
    "queue_policy_mode_probabilities": {},
    "queue_policy_candidates": ["FIFO", "RED", "CoDel", "FqCoDel"],
    "queue_policy_probabilities": {},
    "single_queue_policy": "FIFO",
    "single_queue_policy_probabilities": {},
    "queue_size_range_packets": [128, 2048],
    "ip_cidr": "10.0.0.0/8",
    "ip_cidr_candidates": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
    "ip_cidr_probabilities": {},
    "link_subnet_prefix": 30,
    "link_subnet_prefix_probabilities": {28: 0.15, 29: 0.35, 30: 0.5},
    "mac": {"locally_administered": True},
}

_DEFAULT_NODE_CONFIG = {
    "type_candidates": ["core", "aggregation", "edge"],
    "assignment_mode": "topology_role",
    "default_node_type": "edge",
    "trust_input_node_roles": False,
    "trusted_node_role_fields": [
        "source_node_role",
        "node_role",
        "role",
        "node_type",
        "type",
    ],
    "topology_inference": {
        "core_ratio_range": [0.12, 0.18],
        "core_candidate_ratio_range": [0.12, 0.18],
        "edge_extra_ratio_range": [0.20, 0.30],
        "edge_candidate_ratio_range": [0.20, 0.30],
    },
}

_DEFAULT_ROUTING_CONFIG = {
    "weight_range": [1.0, 10.0],
}

_DEFAULT_TM_CONFIG = {
    "mode": "uniform",
    "mode_probabilities": {
        "uniform": 0.25,
        "exponential": 0.25,
        "gravity": 0.25,
        "spike": 0.25,
    },
    "uniform_range_mbps": [1.0, 100.0],
    "exponential_scale": 20.0,
    "gravity": {"mass_range": [0.5, 2.0], "scale": 100.0},
    "spike": {
        "baseline_range_mbps": [1.0, 20.0],
        "spike_probability": 0.05,
        "spike_multiplier": 10.0,
    },
}

_DEFAULT_FLOW_FEATURE_CONFIG = {
    "selection_mode": "mixed",
    "selection_mode_probabilities": {},
    "single_model": "poisson",
    "single_model_probabilities": {},
    "mode_probabilities": {"poisson": 0.4, "on_off": 0.3, "cbr": 0.3},
    "poisson": {"lambda_range": [1.0, 50.0]},
    "on_off": {
        "on_mean_range": [0.2, 5.0],
        "off_mean_range": [0.2, 6.0],
        "peak_rate_range_mbps": [10.0, 200.0],
    },
    "cbr": {
        "rate_range_mbps": [10.0, 100.0],
    },
}

_DEFAULT_EVENTS_CONFIG = {
    "enabled": True,
    "event_probability": 0.3,
    "event_type_candidates": ["node_failure", "link_failure", "route_switch"],
    "failure_end_probability": 0.5,
    "route_switch": {"require_reachable_to_dst": True},
}


def _resolve_path(base_dir: Path, raw_path: str | Path | None) -> Path:
    if raw_path is None:
        raise ValueError("Path value cannot be None")
    raw = Path(raw_path)
    if raw.is_absolute():
        return raw
    return (base_dir / raw).resolve()


def _deep_merge(default: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(default)
    if not override:
        return result

    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_topology_sources(raw_sources: Any, base_dir: Path) -> list[TopologySourceConfig]:
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("topology_sources must be a non-empty list")

    parsed: list[TopologySourceConfig] = []
    for source in raw_sources:
        if not isinstance(source, dict):
            raise ValueError("Each topology source must be a mapping")

        source_type = str(source.get("type", "")).strip()
        if not source_type:
            raise ValueError("Topology source type is required")

        root_dirs_raw = source.get("paths")
        if root_dirs_raw is None:
            root_dirs_raw = source.get("root_dirs")
        if root_dirs_raw is None:
            root_dirs_raw = source.get("root_dir")
        if root_dirs_raw is None:
            raise ValueError(f"Topology source {source.get('name', '<unknown>')} missing root_dir/paths")

        if isinstance(root_dirs_raw, (str, Path)):
            root_dirs = [_resolve_path(base_dir, root_dirs_raw)]
        elif isinstance(root_dirs_raw, list) and root_dirs_raw:
            root_dirs = [_resolve_path(base_dir, item) for item in root_dirs_raw]
        else:
            raise ValueError("root_dir/paths must be a non-empty string or list")

        glob_patterns = source.get("glob_patterns")
        if not glob_patterns:
            glob_patterns = _DEFAULT_GLOBS.get(source_type, ["**/*"])

        parsed.append(
            TopologySourceConfig(
                name=str(source.get("name", source_type)),
                type=source_type,
                weight=float(source.get("weight", 1.0)),
                root_dirs=root_dirs,
                glob_patterns=[str(pattern) for pattern in glob_patterns],
            )
        )

    return parsed


def _normalize_flow_feature_config(raw_flow_feature: Any) -> dict[str, Any] | None:
    if raw_flow_feature is None:
        return None
    if not isinstance(raw_flow_feature, dict):
        return raw_flow_feature

    normalized = deepcopy(raw_flow_feature)

    if "abr" in normalized and "cbr" not in normalized:
        abr_cfg = normalized.get("abr")
        if isinstance(abr_cfg, dict):
            rate_range = abr_cfg.get("target_rate_range_mbps")
            if rate_range is None:
                rate_range = abr_cfg.get("peak_rate_range_mbps")
            if rate_range is None:
                rate_range = [10.0, 100.0]
            normalized["cbr"] = {"rate_range_mbps": list(rate_range)}

    if str(normalized.get("single_model", "")).strip().lower() == "abr":
        normalized["single_model"] = "cbr"

    for key in ("mode_probabilities", "single_model_probabilities"):
        values = normalized.get(key)
        if not isinstance(values, dict):
            continue
        if "abr" in values:
            values = dict(values)
            abr_weight = values.pop("abr")
            values["cbr"] = float(values.get("cbr", 0.0)) + float(abr_weight)
            normalized[key] = values

    normalized.pop("abr", None)
    return normalized


def _normalize_routing_config(raw_routing: Any) -> dict[str, Any] | None:
    if raw_routing is None:
        return None
    if not isinstance(raw_routing, dict):
        return raw_routing

    normalized = deepcopy(raw_routing)
    normalized.pop("mode", None)
    normalized.pop("random_static_routing", None)
    normalized.pop("unreachable_value", None)
    return normalized


def _replace_explicit_mapping_overrides(merged: dict[str, Any], raw: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return merged
    for key in keys:
        value = raw.get(key)
        if isinstance(value, dict):
            merged[key] = deepcopy(value)
    return merged


def load_config(config_path: str | Path) -> SceneConfig:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file does not exist: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a YAML mapping")

    base_dir = path.parent

    output_root = _resolve_path(base_dir, raw.get("output_root", "./generated_scenes"))
    seed = int(raw.get("seed", 0))
    num_scenes = int(raw.get("num_scenes", 1))
    scene_duration = float(raw.get("scene_duration", 300.0))

    topology_sources = _parse_topology_sources(raw.get("topology_sources"), base_dir)

    raw_link_generation = raw.get("link_generation")
    raw_nics = raw.get("nics")
    raw_flow_feature = _normalize_flow_feature_config(raw.get("flow_feature"))
    raw_routing = _normalize_routing_config(raw.get("routing"))

    link_generation = _deep_merge(_DEFAULT_LINK_CONFIG, raw_link_generation)
    nics = _deep_merge(_DEFAULT_NIC_CONFIG, raw_nics)
    nics = _replace_explicit_mapping_overrides(
        nics,
        raw_nics,
        (
            "queue_policy_mode_probabilities",
            "queue_policy_probabilities",
            "single_queue_policy_probabilities",
            "ip_cidr_probabilities",
            "link_subnet_prefix_probabilities",
        ),
    )
    nodes = _deep_merge(_DEFAULT_NODE_CONFIG, raw.get("nodes"))
    routing = _deep_merge(_DEFAULT_ROUTING_CONFIG, raw_routing)
    raw_traffic_matrix = raw.get("traffic_matrix")
    traffic_matrix = _deep_merge(_DEFAULT_TM_CONFIG, raw_traffic_matrix)
    traffic_matrix = _replace_explicit_mapping_overrides(traffic_matrix, raw_traffic_matrix, ("mode_probabilities",))
    if isinstance(raw_traffic_matrix, dict) and "mode" in raw_traffic_matrix and "mode_probabilities" not in raw_traffic_matrix:
        traffic_matrix["mode_probabilities"] = {}
    flow_feature = _deep_merge(_DEFAULT_FLOW_FEATURE_CONFIG, raw_flow_feature)
    flow_feature = _replace_explicit_mapping_overrides(
        flow_feature,
        raw_flow_feature,
        ("selection_mode_probabilities", "mode_probabilities", "single_model_probabilities"),
    )
    flow_feature.pop("abr", None)
    events = _deep_merge(_DEFAULT_EVENTS_CONFIG, raw.get("events"))
    events.pop("event_time_range", None)

    config = SceneConfig(
        output_root=output_root,
        seed=seed,
        num_scenes=num_scenes,
        scene_duration=scene_duration,
        topology_sources=topology_sources,
        link_generation=link_generation,
        nics=nics,
        nodes=nodes,
        routing=routing,
        traffic_matrix=traffic_matrix,
        flow_feature=flow_feature,
        events=events,
        config_path=path,
    )

    validate_scene_config(config)
    return config
