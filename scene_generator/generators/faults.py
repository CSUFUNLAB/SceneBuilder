from __future__ import annotations

from typing import Any

from ..rng import RandomManager
from ..utils.selection import weighted_pick

_FAULT_COUNTS = {
    "normal": 0,
    "single": 1,
    "double": 2,
}

_DEFAULT_CHANNEL_STATE_PROBABILITIES = {
    "disabled": 0.5,
    "degraded": 0.5,
}
_DEFAULT_CHANNEL_DEGRADATION_MULTIPLIERS = [0.5, 0.2, 0.1]
_DEFAULT_NIC_STATE_PROBABILITIES = {
    "disabled": 1.0,
}


def apply_scene_faults(
    node_rows: list[dict[str, Any]],
    channel_rows: list[dict[str, Any]],
    nic_rows: list[dict[str, Any]],
    fault_config: dict[str, Any],
    rng: RandomManager,
) -> dict[str, Any]:
    entity_groups = (
        ("node", "node_id", node_rows),
        ("channel", "channel_id", channel_rows),
        ("nic", "nic_id", nic_rows),
    )
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for entity_type, id_field, rows in entity_groups:
        for row in rows:
            row["state"] = "normal"
            if entity_type == "channel":
                row["capacity_multiplier"] = 1.0
            candidates.append((entity_type, str(row[id_field]), row))

    probabilities = dict(fault_config.get("scenario_probabilities", {}))
    scenario = weighted_pick(probabilities, "normal", rng)
    fault_count = _FAULT_COUNTS[scenario]
    if fault_count > len(candidates):
        raise ValueError(
            f"Fault scenario '{scenario}' requires {fault_count} entities, "
            f"but the scene contains only {len(candidates)}"
        )

    selected = rng.sample(candidates, fault_count)
    channel_state_probabilities = dict(
        fault_config.get("channel_state_probabilities", _DEFAULT_CHANNEL_STATE_PROBABILITIES)
    )
    channel_degradation_multipliers = [
        float(value)
        for value in fault_config.get(
            "channel_degradation_multipliers",
            _DEFAULT_CHANNEL_DEGRADATION_MULTIPLIERS,
        )
    ]
    nic_state_probabilities = dict(
        fault_config.get("nic_state_probabilities", _DEFAULT_NIC_STATE_PROBABILITIES)
    )

    faulted_entities: list[dict[str, Any]] = []
    for entity_type, entity_id, row in selected:
        if entity_type == "channel":
            row["state"] = weighted_pick(channel_state_probabilities, "disabled", rng)
            if row["state"] == "degraded":
                row["capacity_multiplier"] = float(rng.choice(channel_degradation_multipliers))
        elif entity_type == "nic":
            row["state"] = weighted_pick(nic_state_probabilities, "disabled", rng)
        else:
            row["state"] = "disabled"

        fault_entry: dict[str, Any] = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "state": str(row["state"]),
        }
        if entity_type == "channel" and row["state"] == "degraded":
            fault_entry["capacity_multiplier"] = float(row["capacity_multiplier"])
        faulted_entities.append(fault_entry)

    metadata = {
        "selected_scenario": scenario,
        "fault_count": fault_count,
        "faulted_entities": faulted_entities,
    }
    return metadata


def derive_routing_failed_nodes(
    node_rows: list[dict[str, Any]],
    channel_rows: list[dict[str, Any]],
    nic_rows: list[dict[str, Any]],
    route_map: dict[tuple[str, str], str],
    ordered_graph_nodes: list[str],
) -> list[str]:
    """Derive stale-route failures after physical faults without editing routes."""

    if len(node_rows) != len(ordered_graph_nodes):
        raise ValueError("Node rows do not match the routing graph")

    row_by_graph_node = {
        str(graph_node): row
        for graph_node, row in zip(ordered_graph_nodes, node_rows)
    }
    disabled_nodes = {
        graph_node
        for graph_node, row in row_by_graph_node.items()
        if str(row.get("state", "normal")) == "disabled"
    }
    disabled_channel_ids = {
        str(row.get("channel_id", ""))
        for row in channel_rows
        if str(row.get("state", "normal")) == "disabled"
    }
    disabled_channel_ids.update(
        str(row.get("channel_id", ""))
        for row in nic_rows
        if str(row.get("state", "normal")) == "disabled"
    )

    channels_by_pair: dict[frozenset[str], set[str]] = {}
    for channel in channel_rows:
        pair = frozenset((str(channel["src"]), str(channel["dst"])))
        channels_by_pair.setdefault(pair, set()).add(str(channel["channel_id"]))

    routing_failed_nodes: set[str] = set()
    for (source, destination), next_hop in route_map.items():
        source = str(source)
        destination = str(destination)
        next_hop = str(next_hop)
        if (
            source == destination
            or source in disabled_nodes
            or next_hop == "-1"
        ):
            continue
        if next_hop in disabled_nodes:
            routing_failed_nodes.add(source)
            continue

        pair_channel_ids = channels_by_pair.get(
            frozenset((source, next_hop)),
            set(),
        )
        if pair_channel_ids and pair_channel_ids.issubset(disabled_channel_ids):
            routing_failed_nodes.add(source)

    for graph_node in sorted(routing_failed_nodes):
        row_by_graph_node[graph_node]["state"] = "routing_failed"

    return [
        str(row_by_graph_node[graph_node]["node_id"])
        for graph_node in sorted(routing_failed_nodes)
    ]
