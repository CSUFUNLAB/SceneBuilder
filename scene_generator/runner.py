from __future__ import annotations

from collections import Counter
import re
from pathlib import Path

import networkx as nx

from .cleaner import clean_output_root
from .config import load_config
from .generators.channels import CHANNEL_FIELDS, generate_channels
from .generators.events import generate_events
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


def _as_public_node_id(value: object) -> object:
    if value is None:
        return ""
    text = str(value).strip()
    if text == "":
        return ""
    if text == "-1":
        return -1
    if text.isdigit():
        return f"N{int(text) + 1:04d}"
    return value


def _convert_rows_node_fields_to_public_ids(rows: list[dict[str, object]], keys: list[str]) -> list[dict[str, object]]:
    converted: list[dict[str, object]] = []
    for row in rows:
        new_row = dict(row)
        for key in keys:
            if key in new_row:
                new_row[key] = _as_public_node_id(new_row[key])
        converted.append(new_row)
    return converted


def _build_interface_routing_rows(
    graph: nx.Graph,
    route_map: dict[tuple[str, str], str],
    channel_rows: list[dict[str, object]],
    nics_rows: list[dict[str, object]],
) -> list[list[int]]:
    channels_by_id = {str(row["channel_id"]): row for row in channel_rows}
    interface_by_hop: dict[tuple[str, str], int] = {}

    for nic in nics_rows:
        channel = channels_by_id.get(str(nic["channel_id"]))
        if channel is None:
            continue
        node = str(nic["node"])
        src = str(channel["src"])
        dst = str(channel["dst"])
        if node == src:
            neighbor = dst
        elif node == dst:
            neighbor = src
        else:
            continue
        interface_by_hop[(node, neighbor)] = int(nic["interface_index"])

    rows: list[list[int]] = []
    nodes = ordered_nodes(graph)
    for src in nodes:
        row: list[int] = []
        for dst in nodes:
            if src == dst:
                row.append(0)
                continue
            next_hop = str(route_map.get((src, dst), "-1"))
            if next_hop == "-1":
                row.append(-1)
                continue
            try:
                row.append(interface_by_hop[(src, next_hop)])
            except KeyError as exc:
                raise ValueError(f"No interface index found for route hop ({src}, {next_hop})") from exc
        rows.append(row)
    return rows


def _sorted_count_map(rows: list[dict[str, object]], key: str) -> dict[str, int]:
    counter = Counter(str(row[key]) for row in rows if key in row and row[key] not in (None, ""))
    return {name: int(counter[name]) for name in sorted(counter)}


def _build_metadata(
    config,
    selected: SelectedTopology,
    scene_dir: Path,
    scene_index: int,
    graph: nx.Graph,
    channel_rows: list[dict[str, object]],
    nodes_rows: list[dict[str, object]],
    nics_rows: list[dict[str, object]],
    nics_metadata: dict[str, object],
    traffic_rows: list[dict[str, object]],
    traffic_metadata: dict[str, object],
    event_rows: list[dict[str, object]],
) -> dict[str, object]:
    undirected = graph.to_undirected() if graph.is_directed() else graph
    output_files = [
        "metadata.json",
        "channels.csv",
        "nodes.csv",
        "routing_matrix.csv",
        "nics.csv",
        "traffic.jsonl",
    ]
    if event_rows:
        output_files.append("events.jsonl")

    generation = {
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
        "channels": {
            "mode": str(config.link_generation.get("mode", "")),
            "preserve_input_bandwidth": bool(config.link_generation.get("preserve_input_bandwidth", True)),
            "treat_as_undirected": bool(config.link_generation.get("treat_as_undirected", True)),
            "derived_channel_role": dict(config.link_generation.get("role_based_random", {}).get("derived_link_role", {})),
        },
        "nics": {
            "queue_policy_mode": str(nics_metadata.get("selected_mode", "mixed")),
            "active_rule": dict(nics_metadata.get("active_rule", {})),
        },
        "traffic_matrix": dict(traffic_metadata.get("traffic_matrix", {})),
        "flow_feature": dict(traffic_metadata.get("flow_feature", {})),
        "traffic_constraints": dict(traffic_metadata.get("hard_constraints", {})),
    }
    summary = {
        "node_count": int(len(nodes_rows)),
        "channel_count": int(len(channel_rows)),
        "nic_count": int(len(nics_rows)),
        "flow_count": int(len(traffic_rows)),
        "connected_components": int(nx.number_connected_components(undirected)),
        "channel_type_counts": _sorted_count_map(channel_rows, "channel_type"),
        "queue_policy_counts": _sorted_count_map(nics_rows, "queue_policy"),
        "flow_feature_counts": _sorted_count_map(traffic_rows, "feature_model"),
    }
    if event_rows:
        generation["events"] = {
            "enabled": bool(config.events.get("enabled", False)),
            "count": int(config.events.get("count", 0)),
            "event_type_probabilities": dict(config.events.get("event_type_probabilities", {})),
        }
        summary["event_count"] = int(len(event_rows))
        summary["event_type_counts"] = _sorted_count_map(event_rows, "event_type")

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
        "generation": generation,
        "summary": summary,
        "output_files": output_files,
    }


def _generate_single_scene(config, rng: RandomManager, scene_index: int) -> Path:
    selected, parsed_graph = select_and_load_topology(config, rng)
    working_graph = as_working_graph(parsed_graph, treat_as_undirected=bool(config.link_generation.get("treat_as_undirected", True)))

    graph, internal_to_original, node_id_map = _build_internal_id_graph(working_graph)

    node_roles = infer_node_roles(graph, getattr(config, "nodes", {}), rng)
    nics_metadata = resolve_queue_policy_selection(config.nics, rng)
    channel_rows = generate_channels(graph, config, rng, node_roles=node_roles)
    nodes_rows, node_id_map = generate_nodes(graph, internal_to_original, config, rng, node_roles=node_roles)
    _, routing_map = generate_routing_matrix(graph, config, rng, node_id_map=node_id_map)
    nics_rows = generate_nics(channel_rows, config, rng, selection=nics_metadata, node_roles=node_roles)
    traffic_rows, traffic_metadata = generate_traffic(graph, config, rng, include_metadata=True)
    traffic_rows, traffic_constraints = apply_hard_traffic_constraints(traffic_rows, routing_map, channel_rows)
    traffic_metadata["hard_constraints"] = traffic_constraints
    event_rows = generate_events(
        nodes_rows,
        channel_rows,
        nics_rows,
        traffic_rows,
        config,
        rng,
    )

    routing_rows = _build_interface_routing_rows(graph, routing_map, channel_rows, nics_rows)
    channel_rows = _convert_rows_node_fields_to_public_ids(channel_rows, ["src", "dst"])
    nics_rows = _convert_rows_node_fields_to_public_ids(nics_rows, ["node"])
    traffic_rows = _convert_rows_node_fields_to_public_ids(traffic_rows, ["src", "dst"])
    scene_dir = _build_scene_dir(config, selected, scene_index=scene_index)
    scene_dir.mkdir(parents=True, exist_ok=True)

    # Cleanup legacy files from older versions.
    for legacy_name in ("links.csv", "events.csv", "events.jsonl", "traffic.csv"):
        legacy_path = scene_dir / legacy_name
        if legacy_path.exists():
            legacy_path.unlink()

    write_csv(scene_dir / "channels.csv", CHANNEL_FIELDS, channel_rows)
    write_csv(scene_dir / "nodes.csv", NODE_FIELDS, nodes_rows)
    write_matrix_csv(scene_dir / "routing_matrix.csv", routing_rows)
    write_csv(scene_dir / "nics.csv", NIC_FIELDS, nics_rows)
    write_jsonl(scene_dir / "traffic.jsonl", traffic_rows)
    if event_rows:
        write_jsonl(scene_dir / "events.jsonl", event_rows)
    write_json(
        scene_dir / "metadata.json",
        _build_metadata(
            config=config,
            selected=selected,
            scene_dir=scene_dir,
            scene_index=scene_index,
            graph=graph,
            channel_rows=channel_rows,
            nodes_rows=nodes_rows,
            nics_rows=nics_rows,
            nics_metadata=nics_metadata,
            traffic_rows=traffic_rows,
            traffic_metadata=traffic_metadata,
            event_rows=event_rows,
        ),
    )

    return scene_dir


def run(config_path: str | Path) -> list[Path]:
    config = load_config(config_path)
    clean_output_root(config.output_root)
    rng = RandomManager(config.seed)

    generated: list[Path] = []
    for index in range(1, int(config.num_scenes) + 1):
        generated.append(_generate_single_scene(config, rng, scene_index=index))

    return generated
