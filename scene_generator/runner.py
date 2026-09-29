from __future__ import annotations

from collections import Counter
import re
from pathlib import Path

import networkx as nx

from .config import load_config
from .generators.channels import CHANNEL_FIELDS, generate_channels
from .generators.events import generate_events
from .generators.faults import apply_scene_faults
from .generators.nics import NIC_FIELDS, generate_nics, resolve_queue_policy_selection
from .generators.nodes import NODE_FIELDS, generate_nodes, infer_node_roles
from .generators.routing import generate_routing_matrix
from .generators.traffic import apply_hard_traffic_constraints, generate_traffic
from .generators.wifi import (
    POSITION_FIELDS,
    WIFI_ASSOCIATION_FIELDS,
    WIFI_BSS_FIELDS,
    WIFI_INTERFACE_FIELDS,
    build_wired_graph,
    generate_wifi_scene,
)
from .rng import RandomManager
from .topology.selector import SelectedTopology, collect_topologies, load_topology
from .utils.graph_utils import as_working_graph, ordered_nodes
from .writers.csv_writer import write_csv
from .writers.json_writer import write_json
from .writers.jsonl_writer import write_jsonl
from .writers.matrix_writer import write_matrix_csv
from .scene_schema import NIC_COMMON_FIELDS, CHANNEL_COMMON_FIELDS, validate_scene_records


LEGACY_RUNTIME_EVENTS_ENABLED = False
ORIGINAL_SCENES_DIR_NAME = "origin"
INPUT_DIR_NAME = "input"


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value)


def _duration_token(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text.replace(".", "p")


def _build_scene_dir(config, selected: SelectedTopology, scene_index: int, total_scene_count: int) -> Path:
    config_stem = _sanitize_name(config.config_path.stem)
    topo_stem = _sanitize_name(selected.file_path.stem)
    id_width = max(4, len(str(int(total_scene_count))))
    duration = _duration_token(float(config.scene_duration))
    scene_name = (
        f"{config_stem}_"
        f"id{scene_index:0{id_width}d}_"
        f"{topo_stem}_"
        f"t{duration}s"
    )
    return (
        config.output_root
        / ORIGINAL_SCENES_DIR_NAME
        / INPUT_DIR_NAME
        / scene_name
    )


def _build_internal_id_graph(graph: nx.Graph) -> nx.Graph:
    original_nodes = ordered_nodes(graph)
    original_to_internal = {original: str(index) for index, original in enumerate(original_nodes, start=0)}
    return nx.relabel_nodes(graph, original_to_internal, copy=True)


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
    wifi_interface_rows: list[dict[str, object]] | None = None,
    wifi_association_rows: list[dict[str, object]] | None = None,
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

    wifi_interface_by_id = {
        str(row["nic_id"]): row for row in (wifi_interface_rows or [])
    }
    for association in wifi_association_rows or []:
        ap = str(association["ap_node"])
        sta = str(association["sta_node"])
        ap_nic = wifi_interface_by_id.get(str(association["ap_nic_id"]))
        sta_nic = wifi_interface_by_id.get(str(association["sta_nic_id"]))
        if ap_nic is not None:
            interface_by_hop[(ap, sta)] = int(ap_nic["interface_index"])
        if sta_nic is not None:
            interface_by_hop[(sta, ap)] = int(sta_nic["interface_index"])

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


def _build_unified_node_rows(
    nodes_rows: list[dict[str, object]],
    position_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    positions = {str(row["node_id"]): row for row in position_rows}
    result: list[dict[str, object]] = []
    for node in nodes_rows:
        row = {key: value for key, value in node.items() if value not in (None, "")}
        position = positions.get(str(node["node_id"]))
        if position is not None:
            row.update(
                {
                    "x_m": position["x_m"],
                    "y_m": position["y_m"],
                    "z_m": position["z_m"],
                    "mobility_model": position["mobility_model"],
                    "velocity_x_mps": position["velocity_x_mps"],
                    "velocity_y_mps": position["velocity_y_mps"],
                    "velocity_z_mps": position["velocity_z_mps"],
                }
            )
        result.append(row)
    return result


def _build_unified_nic_rows(
    wired_nics: list[dict[str, object]],
    wifi_interfaces: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for interface in wired_nics:
        row = {**interface, "interface_type": "wired"}
        rows.append({key: row[key] for key in NIC_COMMON_FIELDS})
    for interface in wifi_interfaces:
        row = {**interface, "interface_type": "wifi", "channel_id": interface["bss_id"]}
        rows.append({**{key: row[key] for key in NIC_COMMON_FIELDS}, "wifi_role": row["wifi_role"]})
    return rows


def _build_unified_channel_rows(
    wired_channels: list[dict[str, object]],
    wifi_bss: list[dict[str, object]],
    wifi_associations: list[dict[str, object]],
    wifi_interfaces: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for channel in wired_channels:
        row = dict(channel)
        row["channel_role"] = row.pop("channel_type", "")
        row["medium_type"] = "wired"
        rows.append({**{key: row[key] for key in CHANNEL_COMMON_FIELDS},
                     **{key: value for key, value in row.items() if key not in CHANNEL_COMMON_FIELDS}})

    # An AP interface exists independently of whether any STA uses it.
    ap_interfaces_by_bss: dict[str, dict[str, object]] = {}
    for interface in wifi_interfaces:
        if interface["wifi_role"] != "ap":
            continue
        bss_id = str(interface["bss_id"])
        if bss_id in ap_interfaces_by_bss:
            raise ValueError(f"Multiple Wi-Fi AP interfaces for channel {bss_id}")
        ap_interfaces_by_bss[bss_id] = interface

    associations_by_bss: dict[str, list[dict[str, object]]] = {}
    for association in wifi_associations:
        associations_by_bss.setdefault(str(association["bss_id"]), []).append(association)
    for bss in wifi_bss:
        bss_id = str(bss["bss_id"])
        ap_interface = ap_interfaces_by_bss.get(bss_id)
        if ap_interface is None:
            raise ValueError(f"Missing Wi-Fi AP interface for channel {bss_id}")
        if str(ap_interface["node"]) != str(bss["ap_node"]):
            raise ValueError(f"Wi-Fi AP node mismatch for channel {bss_id}")
        associations = associations_by_bss.get(bss_id, [])
        rows.append(
            {
                "channel_id": bss_id,
                "medium_type": "wifi",
                "state": bss["state"],
                "standard": bss["standard"],
                "channel_number": bss["channel_number"],
                "channel_width_mhz": bss["channel_width_mhz"],
                "tx_power_dbm": bss["tx_power_dbm"],
                "loss_exponent": bss["loss_exponent"],
                "rate_manager": bss["rate_manager"],
                "ap_nic_id": ap_interface["nic_id"],
                "sta_nic_ids": [row["sta_nic_id"] for row in associations],
            }
        )
    return rows


def _build_unified_route_rows(
    nodes_rows: list[dict[str, object]],
    next_hop_rows: list[list[int]],
    routing_rows: list[list[int]],
) -> list[dict[str, object]]:
    node_ids = [str(row["node_id"]) for row in nodes_rows]
    routes: list[dict[str, object]] = []
    for src_index, src in enumerate(node_ids):
        for dst_index, dst in enumerate(node_ids):
            if src_index == dst_index:
                continue
            next_hop_index = int(next_hop_rows[src_index][dst_index])
            egress_interface = int(routing_rows[src_index][dst_index])
            if next_hop_index < 0 or egress_interface < 0:
                continue
            routes.append(
                {
                    "src": src,
                    "dst": dst,
                    "next_hop": node_ids[next_hop_index],
                    "egress_interface": egress_interface,
                }
            )
    return routes


def _sorted_count_map(rows: list[dict[str, object]], key: str) -> dict[str, int]:
    counter = Counter(str(row[key]) for row in rows if key in row and row[key] not in (None, ""))
    return {name: int(counter[name]) for name in sorted(counter)}


def _build_metadata(
    config,
    selected: SelectedTopology,
    scene_dir: Path,
    scene_index: int,
    topology_scene_index: int,
    graph: nx.Graph,
    channel_rows: list[dict[str, object]],
    nodes_rows: list[dict[str, object]],
    nics_rows: list[dict[str, object]],
    nics_metadata: dict[str, object],
    fault_metadata: dict[str, object],
    traffic_rows: list[dict[str, object]],
    traffic_metadata: dict[str, object],
    event_rows: list[dict[str, object]],
    wifi_bss_rows: list[dict[str, object]],
    wifi_interface_rows: list[dict[str, object]],
    wifi_association_rows: list[dict[str, object]],
    wifi_metadata: dict[str, object],
) -> dict[str, object]:
    undirected = graph.to_undirected() if graph.is_directed() else graph
    if config.scene_format == "unified_jsonl":
        output_files = [
            "metadata.json",
            "channels.jsonl",
            "nodes.jsonl",
            "routes.jsonl",
            "nics.jsonl",
            "traffic.jsonl",
        ]
    else:
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
    if config.network_mode == "hybrid" and config.scene_format != "unified_jsonl":
        output_files.extend(
            [
                "wifi_bss.csv",
                "wifi_interfaces.csv",
                "wifi_associations.csv",
                "positions.csv",
                "next_hop_matrix.csv",
            ]
        )

    generation = {
        "routing": {
            "mode": "weighted_shortest_path",
            "weight_range": list(config.routing.get("weight_range", [])),
            "unreachable_value": -1,
            "generated_before_faults": True,
            "recomputed_after_faults": False,
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
        "fault_generation": {
            "scenario_probabilities": dict(config.fault_generation.get("scenario_probabilities", {})),
            "channel_state_probabilities": dict(
                config.fault_generation.get("channel_state_probabilities", {})
            ),
            "channel_degradation_multipliers": list(
                config.fault_generation.get("channel_degradation_multipliers", [])
            ),
            "nic_state_probabilities": dict(
                config.fault_generation.get("nic_state_probabilities", {})
            ),
            "selected_scenario": str(fault_metadata.get("selected_scenario", "normal")),
            "fault_count": int(fault_metadata.get("fault_count", 0)),
            "faulted_entities": list(fault_metadata.get("faulted_entities", [])),
        },
        "traffic_matrix": dict(traffic_metadata.get("traffic_matrix", {})),
        "flow_feature": dict(traffic_metadata.get("flow_feature", {})),
        "traffic_constraints": dict(traffic_metadata.get("hard_constraints", {})),
    }
    if config.network_mode == "hybrid":
        generation["wifi"] = dict(wifi_metadata)
    summary = {
        "network_mode": str(config.network_mode),
        "node_count": int(len(nodes_rows)),
        "channel_count": int(len(channel_rows)),
        "nic_count": int(len(nics_rows)),
        "flow_count": int(len(traffic_rows)),
        "connected_components": int(nx.number_connected_components(undirected)),
        "channel_type_counts": _sorted_count_map(channel_rows, "channel_type"),
        "queue_policy_counts": _sorted_count_map(nics_rows + wifi_interface_rows, "queue_policy"),
        "flow_feature_counts": _sorted_count_map(traffic_rows, "feature_model"),
        "fault_scenario": str(fault_metadata.get("selected_scenario", "normal")),
        "fault_count": int(fault_metadata.get("fault_count", 0)),
        "wifi_bss_count": int(len(wifi_bss_rows)),
        "wifi_interface_count": int(len(wifi_interface_rows)),
        "wifi_association_count": int(len(wifi_association_rows)),
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
        "topology_scene_index": int(topology_scene_index),
        "scenes_per_topology": int(config.scenes_per_topology),
        "scene_duration": float(config.scene_duration),
        "seed": int(config.seed),
        "scene_format": str(config.scene_format),
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


def _generate_single_scene(
    config,
    rng: RandomManager,
    selected: SelectedTopology,
    parsed_graph: nx.Graph,
    scene_index: int,
    topology_scene_index: int,
    total_scene_count: int,
) -> Path:
    working_graph = as_working_graph(parsed_graph, treat_as_undirected=bool(config.link_generation.get("treat_as_undirected", True)))

    graph = _build_internal_id_graph(working_graph)

    node_roles = infer_node_roles(graph, getattr(config, "nodes", {}), rng)
    nics_metadata = resolve_queue_policy_selection(config.nics, rng)
    channel_graph = (
        build_wired_graph(graph, node_roles)
        if config.network_mode == "hybrid"
        else graph
    )
    channel_rows = generate_channels(channel_graph, config, rng, node_roles=node_roles)
    nodes_rows, node_id_map = generate_nodes(graph, config, rng, node_roles=node_roles)
    nics_rows = generate_nics(channel_rows, config, rng, selection=nics_metadata, node_roles=node_roles)
    wifi_bss_rows: list[dict[str, object]] = []
    wifi_interface_rows: list[dict[str, object]] = []
    wifi_association_rows: list[dict[str, object]] = []
    position_rows: list[dict[str, object]] = []
    wifi_metadata: dict[str, object] = {}
    if config.network_mode == "hybrid":
        (
            wifi_bss_rows,
            wifi_interface_rows,
            wifi_association_rows,
            position_rows,
            wifi_metadata,
        ) = generate_wifi_scene(
            graph,
            config,
            rng.fork("wifi"),
            node_roles,
            nics_rows,
            queue_selection=nics_metadata,
        )
    routing_rng = rng.fork("initial_routing")
    next_hop_rows, routing_map = generate_routing_matrix(
        graph,
        config,
        routing_rng,
        node_id_map=node_id_map,
    )
    routing_rows = _build_interface_routing_rows(
        graph,
        routing_map,
        channel_rows,
        nics_rows,
        wifi_interface_rows,
        wifi_association_rows,
    )
    fault_rng = rng.fork("fault_generation")
    fault_metadata = apply_scene_faults(
        nodes_rows,
        channel_rows,
        nics_rows,
        config.fault_generation,
        fault_rng,
    )
    traffic_rows, traffic_metadata = generate_traffic(graph, config, rng, include_metadata=True)
    traffic_rows, traffic_constraints = apply_hard_traffic_constraints(
        traffic_rows,
        routing_map,
        channel_rows,
    )
    traffic_metadata["hard_constraints"] = traffic_constraints
    event_rows: list[dict[str, object]] = []
    if LEGACY_RUNTIME_EVENTS_ENABLED:
        event_rows = generate_events(
            nodes_rows,
            channel_rows,
            nics_rows,
            traffic_rows,
            config,
            rng,
        )

    channel_rows = _convert_rows_node_fields_to_public_ids(channel_rows, ["src", "dst"])
    nics_rows = _convert_rows_node_fields_to_public_ids(nics_rows, ["node"])
    wifi_bss_rows = _convert_rows_node_fields_to_public_ids(wifi_bss_rows, ["ap_node"])
    wifi_interface_rows = _convert_rows_node_fields_to_public_ids(wifi_interface_rows, ["node"])
    wifi_association_rows = _convert_rows_node_fields_to_public_ids(
        wifi_association_rows,
        ["ap_node", "sta_node"],
    )
    position_rows = _convert_rows_node_fields_to_public_ids(position_rows, ["node_id"])
    traffic_rows = _convert_rows_node_fields_to_public_ids(traffic_rows, ["src", "dst"])
    scene_dir = _build_scene_dir(
        config,
        selected,
        scene_index=scene_index,
        total_scene_count=total_scene_count,
    )
    scene_dir.mkdir(parents=True, exist_ok=True)

    # Cleanup obsolete files from older versions of the selected format.
    obsolete_names = ["links.csv", "events.csv", "events.jsonl", "traffic.csv"]
    if config.scene_format == "unified_jsonl":
        obsolete_names.extend(
            [
                "channels.csv",
                "nodes.csv",
                "routing_matrix.csv",
                "nics.csv",
                "wifi_bss.csv",
                "wifi_interfaces.csv",
                "wifi_associations.csv",
                "positions.csv",
                "next_hop_matrix.csv",
            ]
        )
    for legacy_name in obsolete_names:
        legacy_path = scene_dir / legacy_name
        if legacy_path.exists():
            legacy_path.unlink()

    if config.scene_format == "unified_jsonl":
        unified_nodes = _build_unified_node_rows(nodes_rows, position_rows)
        unified_nics = _build_unified_nic_rows(nics_rows, wifi_interface_rows)
        unified_channels = _build_unified_channel_rows(
            channel_rows, wifi_bss_rows, wifi_association_rows, wifi_interface_rows
        )
        unified_routes = _build_unified_route_rows(nodes_rows, next_hop_rows, routing_rows)
        validate_scene_records(unified_nodes, unified_nics, unified_channels, unified_routes, traffic_rows)
        write_jsonl(scene_dir / "nodes.jsonl", unified_nodes)
        write_jsonl(scene_dir / "nics.jsonl", unified_nics)
        write_jsonl(scene_dir / "channels.jsonl", unified_channels)
        write_jsonl(scene_dir / "routes.jsonl", unified_routes)
    else:
        write_csv(scene_dir / "channels.csv", CHANNEL_FIELDS, channel_rows)
        write_csv(scene_dir / "nodes.csv", NODE_FIELDS, nodes_rows)
        write_matrix_csv(scene_dir / "routing_matrix.csv", routing_rows)
        write_csv(scene_dir / "nics.csv", NIC_FIELDS, nics_rows)
        if config.network_mode == "hybrid":
            write_csv(scene_dir / "wifi_bss.csv", WIFI_BSS_FIELDS, wifi_bss_rows)
            write_csv(scene_dir / "wifi_interfaces.csv", WIFI_INTERFACE_FIELDS, wifi_interface_rows)
            write_csv(
                scene_dir / "wifi_associations.csv",
                WIFI_ASSOCIATION_FIELDS,
                wifi_association_rows,
            )
            write_csv(scene_dir / "positions.csv", POSITION_FIELDS, position_rows)
            write_matrix_csv(scene_dir / "next_hop_matrix.csv", next_hop_rows)
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
            topology_scene_index=topology_scene_index,
            graph=graph,
            channel_rows=channel_rows,
            nodes_rows=nodes_rows,
            nics_rows=nics_rows,
            nics_metadata=nics_metadata,
            fault_metadata=fault_metadata,
            traffic_rows=traffic_rows,
            traffic_metadata=traffic_metadata,
            event_rows=event_rows,
            wifi_bss_rows=wifi_bss_rows,
            wifi_interface_rows=wifi_interface_rows,
            wifi_association_rows=wifi_association_rows,
            wifi_metadata=wifi_metadata,
        ),
    )

    return scene_dir


def run(config_path: str | Path) -> list[Path]:
    config = load_config(config_path)
    if config.output_root.exists():
        if config.output_root.is_symlink():
            raise ValueError(
                f"Refusing to generate into a symbolic-link output_root: "
                f"{config.output_root}"
            )
        if not config.output_root.is_dir():
            raise NotADirectoryError(
                f"output_root is not a directory: {config.output_root}"
            )
        existing_output = next(config.output_root.iterdir(), None)
        if existing_output is not None:
            raise ValueError(
                "Generated scene output already exists under "
                f"{config.output_root}; run 'python main.py clean' first"
            )
    eligible_topologies: list[tuple[SelectedTopology, nx.Graph]] = []
    for selected in collect_topologies(config):
        parsed_graph = load_topology(selected)
        if parsed_graph.number_of_nodes() <= int(config.max_topology_nodes):
            eligible_topologies.append((selected, parsed_graph))

    if not eligible_topologies:
        raise ValueError(
            f"No topology has at most {config.max_topology_nodes} nodes"
        )

    total_scene_count = len(eligible_topologies) * int(config.scenes_per_topology)
    (
        config.output_root
        / ORIGINAL_SCENES_DIR_NAME
        / INPUT_DIR_NAME
    ).mkdir(parents=True, exist_ok=True)
    root_rng = RandomManager(config.seed)

    generated: list[Path] = []
    scene_index = 0
    for selected, parsed_graph in eligible_topologies:
        for topology_scene_index in range(1, int(config.scenes_per_topology) + 1):
            scene_index += 1
            scene_rng = root_rng.fork(
                f"topology:{selected.source_type}:{selected.file_path}:scene:{topology_scene_index}"
            )
            generated.append(
                _generate_single_scene(
                    config,
                    scene_rng,
                    selected,
                    parsed_graph,
                    scene_index=scene_index,
                    topology_scene_index=topology_scene_index,
                    total_scene_count=total_scene_count,
                )
            )

    return generated
