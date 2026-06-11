from __future__ import annotations

from typing import Any

import networkx as nx

from ..rng import RandomManager
from ..utils.entity_states import pick_state
from .nodes import infer_node_roles

LINK_FIELDS = ["link_id", "src", "dst", "link_type", "bandwidth_mbps", "state"]

_DEFAULT_TRUSTED_LINK_ROLE_FIELDS = [
    "source_link_role",
    "link_role",
    "edge_role",
    "role",
    "link_type",
    "edge_type",
    "type",
]

_NODE_ROLE_ALIASES = {
    "core": "core",
    "backbone": "core",
    "aggregation": "aggregation",
    "agg": "aggregation",
    "edge": "edge",
    "access": "edge",
}

_LINK_ROLE_ALIASES = {
    "backbone": "backbone",
    "core": "backbone",
    "uplink": "uplink",
    "access": "access",
    "lateral": "lateral",
}


def _sample_bandwidth(spec: dict[str, Any], rng: RandomManager) -> float:
    candidates = [float(v) for v in spec.get("bandwidth_candidates_mbps", [])]
    if candidates:
        return float(rng.choice(candidates))

    value_range = spec.get("uniform_range_mbps") or spec.get("range_mbps")
    if isinstance(value_range, (list, tuple)) and len(value_range) == 2:
        low, high = float(value_range[0]), float(value_range[1])
        return float(rng.uniform(low, high))

    raise ValueError("Bandwidth generation config must define candidates or a range")


def _ordered_edges(graph: nx.Graph, treat_as_undirected: bool) -> list[tuple[str, str, dict[str, Any]]]:
    result: list[tuple[str, str, dict[str, Any]]] = []
    if treat_as_undirected:
        seen: set[tuple[str, str]] = set()
        for src, dst, attrs in graph.edges(data=True):
            a, b = sorted((str(src), str(dst)))
            key = (a, b)
            if key in seen:
                continue
            seen.add(key)
            result.append((a, b, dict(attrs)))
    else:
        for src, dst, attrs in graph.edges(data=True):
            result.append((str(src), str(dst), dict(attrs)))

    return sorted(result, key=lambda item: (item[0], item[1]))


def _normalize_node_role(value: object) -> str:
    if value in (None, ""):
        return "edge"
    text = str(value).strip().lower()
    return _NODE_ROLE_ALIASES.get(text, "edge")


def _normalize_link_role(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    return _LINK_ROLE_ALIASES.get(text)


def _extract_trusted_link_role(attrs: dict[str, Any], fields: list[str]) -> str | None:
    field_set = {str(field).strip().lower() for field in fields if str(field).strip()}
    for key, value in attrs.items():
        if str(key).strip().lower() not in field_set:
            continue
        role = _normalize_link_role(value)
        if role is not None:
            return role
    return None


def _derive_link_role_from_nodes(
    src_role: str,
    dst_role: str,
    role_cfg: dict[str, Any],
    rng: RandomManager,
) -> str:
    src_norm = _normalize_node_role(src_role)
    dst_norm = _normalize_node_role(dst_role)
    pair = {src_norm, dst_norm}

    agg_agg_lateral_p = float(role_cfg.get("aggregation_aggregation_lateral_probability", 0.7))
    core_edge_uplink_p = float(role_cfg.get("core_edge_uplink_probability", 0.8))

    if src_norm == "core" and dst_norm == "core":
        return "backbone"
    if pair == {"core", "aggregation"}:
        return "uplink"
    if pair == {"aggregation", "edge"}:
        return "access"
    if src_norm == "aggregation" and dst_norm == "aggregation":
        return "lateral" if rng.probability(agg_agg_lateral_p) else "uplink"
    if pair == {"core", "edge"}:
        return "uplink" if rng.probability(core_edge_uplink_p) else "access"
    if src_norm == "edge" and dst_norm == "edge":
        return "access"
    return "lateral"


def _role_bandwidth_spec(role_bandwidth: dict[str, Any], link_role: str) -> dict[str, Any]:
    if link_role in role_bandwidth:
        return dict(role_bandwidth[link_role])

    fallback_keys: dict[str, list[str]] = {
        "backbone": ["core"],
        "uplink": ["aggregation"],
        "access": ["edge"],
        "lateral": ["aggregation"],
    }
    for key in fallback_keys.get(link_role, []):
        if key in role_bandwidth:
            return dict(role_bandwidth[key])
    return {}


def generate_links(
    graph: nx.Graph,
    config: Any,
    rng: RandomManager,
    node_roles: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    link_cfg = config.link_generation
    mode = str(link_cfg.get("mode", "pure_random"))
    preserve_input = bool(link_cfg.get("preserve_input_bandwidth", True))
    treat_as_undirected = bool(link_cfg.get("treat_as_undirected", True))
    state_probabilities = dict(link_cfg.get("state_probabilities", {"normal": 1.0}))

    pure_random_cfg = link_cfg.get("pure_random", {})
    role_cfg = link_cfg.get("role_based_random", {})
    role_bandwidth = role_cfg.get("role_bandwidth_mbps", {})
    trust_input_link_roles = bool(role_cfg.get("trust_input_link_roles", False))
    trusted_link_role_fields = [
        str(item) for item in role_cfg.get("trusted_link_role_fields", _DEFAULT_TRUSTED_LINK_ROLE_FIELDS)
    ]
    derived_role_cfg = role_cfg.get("derived_link_role", {})

    if mode == "role_based_random" and node_roles is None:
        node_roles = infer_node_roles(graph, getattr(config, "nodes", {}), rng)
    role_map = node_roles or {}

    rows: list[dict[str, Any]] = []
    for idx, (src, dst, attrs) in enumerate(_ordered_edges(graph, treat_as_undirected), start=1):
        trusted_link_role = None
        if trust_input_link_roles:
            trusted_link_role = _extract_trusted_link_role(attrs, trusted_link_role_fields)

        if trusted_link_role is not None:
            link_role = trusted_link_role
        else:
            src_role = role_map.get(src, "edge")
            dst_role = role_map.get(dst, "edge")
            link_role = _derive_link_role_from_nodes(src_role, dst_role, derived_role_cfg, rng)

        bandwidth: float | None = None

        source_bw = attrs.get("source_bandwidth_mbps")
        if preserve_input and source_bw not in (None, ""):
            try:
                bandwidth = float(source_bw)
            except (TypeError, ValueError):
                bandwidth = None

        if bandwidth is None:
            if mode == "pure_random":
                bandwidth = _sample_bandwidth(pure_random_cfg, rng)
            elif mode == "role_based_random":
                role_spec = _role_bandwidth_spec(role_bandwidth, link_role)
                try:
                    bandwidth = _sample_bandwidth(role_spec, rng)
                except ValueError:
                    bandwidth = _sample_bandwidth(pure_random_cfg, rng)
            else:
                raise ValueError(f"Unsupported link generation mode: {mode}")

        rows.append(
            {
                "link_id": f"L{idx:04d}",
                "src": src,
                "dst": dst,
                "link_type": link_role,
                "bandwidth_mbps": round(float(bandwidth), 6),
                "state": pick_state(state_probabilities, "normal", rng, key=f"link:{src}:{dst}"),
            }
        )

    return rows
