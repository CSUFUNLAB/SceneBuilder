from __future__ import annotations

import math
from typing import Any

import networkx as nx

from ..rng import RandomManager
from ..utils.graph_utils import ordered_nodes
from .routing import build_node_id_map

NODE_FIELDS = ["node_id", "state", "latitude", "longitude"]

_DEFAULT_TRUSTED_NODE_ROLE_FIELDS = [
    "source_node_role",
    "node_role",
    "role",
    "node_type",
    "type",
]

_NODE_ROLE_ALIASES = {
    "core": "core",
    "backbone": "core",
    "aggregation": "aggregation",
    "agg": "aggregation",
    "distribution": "aggregation",
    "edge": "edge",
    "access": "edge",
    "leaf": "edge",
}

_DEFAULT_NODE_ROLES = ("core", "aggregation", "edge")


def _node_sort_key(node: str) -> tuple[int, int | str]:
    text = str(node)
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def _as_optional_float(value: object) -> float | str:
    if value in (None, ""):
        return ""
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return ""


def _public_node_id(value: int) -> str:
    return f"N{int(value) + 1:04d}"


def _normalize_node_role(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    return _NODE_ROLE_ALIASES.get(text)


def _extract_trusted_node_role(attrs: dict[str, object], fields: list[str]) -> str | None:
    if not attrs:
        return None
    field_set = {str(field).strip().lower() for field in fields if str(field).strip()}
    for key, value in attrs.items():
        if str(key).strip().lower() not in field_set:
            continue
        role = _normalize_node_role(value)
        if role is not None:
            return role
    return None


def _configured_node_roles(nodes_cfg: dict[str, Any]) -> list[str]:
    configured = nodes_cfg.get("type_candidates", _DEFAULT_NODE_ROLES)
    roles: list[str] = []
    for value in configured:
        role = _normalize_node_role(value)
        if role is not None and role not in roles:
            roles.append(role)
    if not roles:
        raise ValueError("nodes.type_candidates must contain at least one supported node role")
    return roles


def _weighted_sample_without_replacement(
    candidates: list[str],
    weights: list[float],
    k: int,
    rng: RandomManager,
) -> list[str]:
    if k <= 0 or not candidates:
        return []

    k = min(int(k), len(candidates))
    pool_items = list(candidates)
    pool_weights = [float(max(0.0, w)) for w in weights]

    if not any(weight > 0 for weight in pool_weights):
        pool_weights = [1.0 for _ in pool_items]

    selected: list[str] = []
    for _ in range(k):
        picked = rng.weighted_choice(pool_items, pool_weights)
        idx = pool_items.index(picked)
        selected.append(picked)
        del pool_items[idx]
        del pool_weights[idx]
        if not pool_items:
            break
        if not any(weight > 0 for weight in pool_weights):
            pool_weights = [1.0 for _ in pool_items]

    return selected


def _range_pair(value: object, default: tuple[float, float]) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return default
    low = float(value[0])
    high = float(value[1])
    if low > high:
        low, high = high, low
    return low, high


def _ratio_in_range(
    cfg: dict[str, Any],
    key: str,
    default: tuple[float, float],
    rng: RandomManager,
) -> float:
    low, high = _range_pair(cfg.get(key), default)
    low = max(0.0, low)
    high = max(0.0, high)
    if low > high:
        low, high = high, low
    return rng.uniform(low, high)


def _highest_degree_nodes(nodes: list[str], degrees: dict[str, int]) -> list[str]:
    return sorted(nodes, key=lambda n: (-degrees.get(n, 0), _node_sort_key(n)))


def _lowest_degree_nodes(nodes: list[str], degrees: dict[str, int]) -> list[str]:
    return sorted(nodes, key=lambda n: (degrees.get(n, 0), _node_sort_key(n)))


def _promote_edge_endpoint(src: str, dst: str, degrees: dict[str, int]) -> str:
    src_degree = int(degrees.get(src, 0))
    dst_degree = int(degrees.get(dst, 0))
    if src_degree > dst_degree:
        return src
    if dst_degree > src_degree:
        return dst
    return src if _node_sort_key(src) >= _node_sort_key(dst) else dst


def _infer_component_roles(
    graph: nx.Graph,
    component_nodes: list[str],
    roles: dict[str, str],
    locked_nodes: set[str],
    inference_cfg: dict[str, Any],
    rng: RandomManager,
) -> None:
    degrees = {node: int(graph.degree(node)) for node in component_nodes}

    for node in component_nodes:
        if node in locked_nodes:
            continue
        if degrees[node] == 1:
            roles[node] = "edge"

    remaining_for_core = [node for node in component_nodes if node not in locked_nodes and node not in roles]
    if remaining_for_core:
        core_ratio = _ratio_in_range(inference_cfg, "core_ratio_range", (0.12, 0.18), rng)
        core_candidate_ratio = _ratio_in_range(inference_cfg, "core_candidate_ratio_range", (0.12, 0.18), rng)

        core_count = int(round(len(remaining_for_core) * core_ratio))
        core_candidate_count = max(core_count, int(math.ceil(len(remaining_for_core) * core_candidate_ratio)))
        core_candidate_count = min(core_candidate_count, len(remaining_for_core))

        if core_count > 0 and core_candidate_count > 0:
            core_candidates = _highest_degree_nodes(remaining_for_core, degrees)[:core_candidate_count]
            core_weights = [float(max(1, degrees[node])) for node in core_candidates]
            chosen_core = _weighted_sample_without_replacement(core_candidates, core_weights, core_count, rng)
            for node in chosen_core:
                roles[node] = "core"

    remaining_for_extra_edge = [node for node in component_nodes if node not in locked_nodes and node not in roles]
    if remaining_for_extra_edge:
        edge_ratio = _ratio_in_range(inference_cfg, "edge_extra_ratio_range", (0.20, 0.30), rng)
        edge_candidate_ratio = _ratio_in_range(inference_cfg, "edge_candidate_ratio_range", (0.20, 0.30), rng)

        edge_count = int(round(len(remaining_for_extra_edge) * edge_ratio))
        edge_candidate_count = max(edge_count, int(math.ceil(len(remaining_for_extra_edge) * edge_candidate_ratio)))
        edge_candidate_count = min(edge_candidate_count, len(remaining_for_extra_edge))

        if edge_count > 0 and edge_candidate_count > 0:
            edge_candidates = _lowest_degree_nodes(remaining_for_extra_edge, degrees)[:edge_candidate_count]
            edge_weights = [1.0 / float(max(1, degrees[node])) for node in edge_candidates]
            chosen_edge = _weighted_sample_without_replacement(edge_candidates, edge_weights, edge_count, rng)
            for node in chosen_edge:
                roles[node] = "edge"

    for node in component_nodes:
        if node in locked_nodes:
            continue
        roles.setdefault(node, "aggregation")

    if len(component_nodes) >= 5 and not any(roles.get(node) == "core" for node in component_nodes):
        mutable = [node for node in component_nodes if node not in locked_nodes]
        forced_candidates = mutable if mutable else list(component_nodes)
        forced = _highest_degree_nodes(forced_candidates, degrees)[0]
        roles[forced] = "core"

    component_graph = graph.subgraph(component_nodes)
    for src, dst in sorted((str(a), str(b)) for a, b in component_graph.edges()):
        if roles.get(src) != "edge" or roles.get(dst) != "edge":
            continue

        promote = _promote_edge_endpoint(src, dst, degrees)
        if promote in locked_nodes:
            alt = dst if promote == src else src
            if alt not in locked_nodes:
                promote = alt
        roles[promote] = "aggregation"


def infer_node_roles(graph: nx.Graph, nodes_cfg: dict[str, Any], rng: RandomManager) -> dict[str, str]:
    nodes = ordered_nodes(graph)
    if not nodes:
        return {}

    assignment_mode = str(nodes_cfg.get("assignment_mode", "topology_role"))
    if assignment_mode == "fixed":
        default_role = _normalize_node_role(nodes_cfg.get("default_node_type", "edge"))
        if default_role is None:
            raise ValueError(f"Unsupported nodes.default_node_type: {nodes_cfg.get('default_node_type')}")
        return {node: default_role for node in nodes}
    if assignment_mode == "random":
        candidates = _configured_node_roles(nodes_cfg)
        return {node: rng.choice(candidates) for node in nodes}
    if assignment_mode != "topology_role":
        raise ValueError(f"Unsupported nodes.assignment_mode: {assignment_mode}")

    trust_input = bool(nodes_cfg.get("trust_input_node_roles", False))
    trusted_fields = [str(item) for item in nodes_cfg.get("trusted_node_role_fields", _DEFAULT_TRUSTED_NODE_ROLE_FIELDS)]
    inference_cfg = nodes_cfg.get("topology_inference", {})

    roles: dict[str, str] = {}
    locked_nodes: set[str] = set()

    if trust_input:
        for node in nodes:
            attrs = graph.nodes[node]
            trusted_role = _extract_trusted_node_role(dict(attrs), trusted_fields)
            if trusted_role is None:
                continue
            roles[node] = trusted_role
            locked_nodes.add(node)

    undirected = graph.to_undirected() if graph.is_directed() else graph
    components = [sorted((str(n) for n in comp), key=_node_sort_key) for comp in nx.connected_components(undirected)]

    for component_nodes in components:
        _infer_component_roles(graph, component_nodes, roles, locked_nodes, inference_cfg, rng)

    for node in nodes:
        roles.setdefault(node, "aggregation")

    return roles


def generate_nodes(
    graph: nx.Graph,
    config: Any,
    rng: RandomManager,
    node_roles: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    nodes = ordered_nodes(graph)
    node_id_map = build_node_id_map(nodes)

    nodes_cfg = getattr(config, "nodes", {})

    if node_roles is None:
        infer_node_roles(graph, nodes_cfg, rng)

    rows: list[dict[str, Any]] = []
    for node in nodes:
        attrs = graph.nodes[node]
        rows.append(
            {
                "node_id": _public_node_id(int(node_id_map[node])),
                "state": "normal",
                "latitude": _as_optional_float(attrs.get("source_latitude")),
                "longitude": _as_optional_float(attrs.get("source_longitude")),
            }
        )

    return rows, node_id_map
