from __future__ import annotations

from collections import Counter
import re
from pathlib import Path

import networkx as nx

from .config import load_config
from .generators.events import generate_events
from .generators.links import LINK_FIELDS, generate_links
from .generators.nics import NIC_FIELDS, generate_nics, resolve_queue_policy_selection
from .generators.nodes import NODE_FIELDS, generate_nodes, infer_node_roles
from .generators.routing import generate_routing_matrix
from .generators.traffic import apply_hard_traffic_constraints, generate_traffic
from .rng import RandomManager
from .topology.selector import SelectedTopology, select_and_load_topology
from .utils.graph_utils import as_working_graph, ordered_nodes
from .writers.csv_writer import write_csv
from .writers.json_writer import write_json
from .writers.jsonl_writer import write_jsonl
from .writers.matrix_writer import write_matrix_csv


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value)


def _duration_token(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text.replace(".", "p")


def _build_scene_dir(config, selected: SelectedTopology, scene_index: int) -> Path:
    config_stem = _sanitize_name(config.config_path.stem)
    topo_stem = _sanitize_name(selected.file_path.stem)
    id_width = max(4, len(str(int(config.num_scenes))))
    duration = _duration_token(float(config.scene_duration))
    scene_name = (
        f"{config_stem}_"
        f"id{scene_index:0{id_width}d}_"
        f"{topo_stem}_"
        f"t{duration}s"
    )
    return config.output_root / scene_name


def _build_internal_id_graph(graph: nx.Graph) -> tuple[nx.Graph, dict[str, str], dict[str, int]]:
    original_nodes = ordered_nodes(graph)
    original_to_internal = {original: str(index) for index, original in enumerate(original_nodes, start=0)}
    internal_to_original = {internal: original for original, internal in original_to_internal.items()}
    internal_graph = nx.relabel_nodes(graph, original_to_internal, copy=True)
    internal_id_map = {internal: int(internal) for internal in internal_to_original.keys()}
    return internal_graph, internal_to_original, internal_id_map


def _as_int_node_id(value: object) -> object:
    if value is None:
        return ""
    text = str(value).strip()
    if text == "":
        return ""
    if text == "-1":
        return -1
    if text.isdigit():
        return int(text)
    return value


def _convert_rows_node_fields(rows: list[dict[str, object]], keys: list[str]) -> list[dict[str, object]]:
    converted: list[dict[str, object]] = []
    for row in rows:
        new_row = dict(row)
        for key in keys:
            if key in new_row:
                new_row[key] = _as_int_node_id(new_row[key])
        converted.append(new_row)
    return converted


def _sorted_count_map(rows: list[dict[str, object]], key: str) -> dict[str, int]:
    counter = Counter(str(row[key]) for row in rows if key in row and row[key] not in (None, ""))
    return {name: int(counter[name]) for name in sorted(counter)}


def _build_metadata(
    config,
    selected: SelectedTopology,
    scene_dir: Path,
    scene_index: int,
    graph: nx.Graph,
    links_rows: list[dict[str, object]],
    nodes_rows: list[dict[str, object]],
    nics_rows: list[dict[str, object]],
    nics_metadata: dict[str, object],
    traffic_rows: list[dict[str, object]],
    traffic_metadata: dict[str, object],
    events_rows: list[dict[str, object]],
) -> dict[str, object]:
    undirected = graph.to_undirected() if graph.is_directed() else graph

    return {
        "scene_name": scene_dir.name,
        "scene_id": int(scene_index),
        "scene_duration": float(config.scene_duration),
        "seed": int(config.seed),
        "config": {
            "name": config.config_path.stem,
            "path": str(config.config_path),
        },
        "topology": {
            "source_name": selected.source_name,
            "source_type": selected.source_type,
            "file_name": selected.file_path.name,
            "file_stem": selected.file_path.stem,
            "file_path": str(selected.file_path),
        },
        "generation": {
            "routing": {
                "mode": "weighted_shortest_path",
                "weight_range": list(config.routing.get("weight_range", [])),
                "unreachable_value": -1,
            },
            "nodes": {
                "assignment_mode": str(config.nodes.get("assignment_mode", "")),
                "trust_input_node_roles": bool(config.nodes.get("trust_input_node_roles", False)),
                "topology_inference": dict(config.nodes.get("topology_inference", {})),
            },
            "links": {
                "mode": str(config.link_generation.get("mode", "")),
                "preserve_input_bandwidth": bool(config.link_generation.get("preserve_input_bandwidth", True)),
                "treat_as_undirected": bool(config.link_generation.get("treat_as_undirected", True)),
                "derived_link_role": dict(config.link_generation.get("role_based_random", {}).get("derived_link_role", {})),
            },
            "nics": {
                "queue_policy_mode": str(nics_metadata.get("selected_mode", "mixed")),
                "active_rule": dict(nics_metadata.get("active_rule", {})),
            },
            "traffic_matrix": dict(traffic_metadata.get("traffic_matrix", {})),
            "flow_feature": dict(traffic_metadata.get("flow_feature", {})),
            "traffic_constraints": dict(traffic_metadata.get("hard_constraints", {})),
            "events": {
                "enabled": bool(config.events.get("enabled", True)),
                "event_probability": float(config.events.get("event_probability", 0.0)),
                "event_type_candidates": list(config.events.get("event_type_candidates", [])),
                "failure_end_probability": float(config.events.get("failure_end_probability", 0.5)),
            },
        },
        "summary": {
            "node_count": int(len(nodes_rows)),
            "link_count": int(len(links_rows)),
            "nic_count": int(len(nics_rows)),
            "flow_count": int(len(traffic_rows)),
            "event_count": int(len(events_rows)),
            "connected_components": int(nx.number_connected_components(undirected)),
            "node_type_counts": _sorted_count_map(nodes_rows, "node_type"),
            "link_type_counts": _sorted_count_map(links_rows, "link_type"),
            "queue_policy_counts": _sorted_count_map(nics_rows, "queue_policy"),
            "flow_feature_counts": _sorted_count_map(traffic_rows, "feature_model"),
            "event_type_counts": _sorted_count_map(events_rows, "event_type"),
        },
        "output_files": [
            "metadata.json",
            "links.csv",
            "nodes.csv",
            "routing_matrix.csv",
            "nics.csv",
            "events.jsonl",
            "traffic.jsonl",
        ],
    }


def _generate_single_scene(config, rng: RandomManager, scene_index: int) -> Path:
    selected, parsed_graph = select_and_load_topology(config, rng)
    working_graph = as_working_graph(parsed_graph, treat_as_undirected=bool(config.link_generation.get("treat_as_undirected", True)))

    graph, internal_to_original, node_id_map = _build_internal_id_graph(working_graph)

    node_roles = infer_node_roles(graph, getattr(config, "nodes", {}), rng)
    nics_metadata = resolve_queue_policy_selection(config.nics, rng)
    links_rows = generate_links(graph, config, rng, node_roles=node_roles)
    nodes_rows, node_id_map = generate_nodes(graph, internal_to_original, config, rng, node_roles=node_roles)
    routing_rows, routing_map = generate_routing_matrix(graph, config, rng, node_id_map=node_id_map)
    nics_rows = generate_nics(links_rows, config, rng, selection=nics_metadata, node_roles=node_roles)
    events_rows = generate_events(graph, links_rows, routing_map, config, rng)
    traffic_rows, traffic_metadata = generate_traffic(graph, config, rng, include_metadata=True)
    traffic_rows, traffic_constraints = apply_hard_traffic_constraints(traffic_rows, routing_map, links_rows)
    traffic_metadata["hard_constraints"] = traffic_constraints

    links_rows = _convert_rows_node_fields(links_rows, ["src", "dst"])
    nics_rows = _convert_rows_node_fields(nics_rows, ["node"])
    traffic_rows = _convert_rows_node_fields(traffic_rows, ["src", "dst"])
    events_rows = _convert_rows_node_fields(
        events_rows,
        ["target_1", "target_2", "src", "dst", "old_next_hop", "new_next_hop"],
    )

    scene_dir = _build_scene_dir(config, selected, scene_index=scene_index)
    scene_dir.mkdir(parents=True, exist_ok=True)

    # Cleanup legacy files from older versions that wrote events/traffic as CSV.
    for legacy_name in ("events.csv", "traffic.csv"):
        legacy_path = scene_dir / legacy_name
        if legacy_path.exists():
            legacy_path.unlink()

    write_csv(scene_dir / "links.csv", LINK_FIELDS, links_rows)
    write_csv(scene_dir / "nodes.csv", NODE_FIELDS, nodes_rows)
    write_matrix_csv(scene_dir / "routing_matrix.csv", routing_rows)
    write_csv(scene_dir / "nics.csv", NIC_FIELDS, nics_rows)
    write_jsonl(scene_dir / "events.jsonl", events_rows)
    write_jsonl(scene_dir / "traffic.jsonl", traffic_rows)
    write_json(
        scene_dir / "metadata.json",
        _build_metadata(
            config=config,
            selected=selected,
            scene_dir=scene_dir,
            scene_index=scene_index,
            graph=graph,
            links_rows=links_rows,
            nodes_rows=nodes_rows,
            nics_rows=nics_rows,
            nics_metadata=nics_metadata,
            traffic_rows=traffic_rows,
            traffic_metadata=traffic_metadata,
            events_rows=events_rows,
        ),
    )

    return scene_dir


def run(config_path: str | Path) -> list[Path]:
    config = load_config(config_path)
    rng = RandomManager(config.seed)

    generated: list[Path] = []
    for index in range(1, int(config.num_scenes) + 1):
        generated.append(_generate_single_scene(config, rng, scene_index=index))

    return generated
