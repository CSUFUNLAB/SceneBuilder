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
_DEFAULT_NODE_STATE_PROBABILITIES = {
    "disabled": 0.5,
    "routing_failed": 0.5,
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
    routing_rows: list[list[int]] | None = None,
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
    node_state_probabilities = dict(
        fault_config.get("node_state_probabilities", _DEFAULT_NODE_STATE_PROBABILITIES)
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
            row["state"] = weighted_pick(node_state_probabilities, "disabled", rng)

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
    if routing_rows is not None:
        apply_routing_failures(node_rows, routing_rows, metadata, rng)
    return metadata


def apply_routing_failures(
    node_rows: list[dict[str, Any]],
    routing_rows: list[list[int]],
    fault_metadata: dict[str, Any],
    rng: RandomManager,
) -> bool:
    """Apply routing-table faults after physical-topology routing is computed.

    Returns ``False`` when a selected routing-failure node has no physically
    reachable destination. Such a node is converted to ``disabled`` so the
    caller can rebuild routing against the updated operational topology.
    """

    routing_failure_indices = [
        index
        for index, node in enumerate(node_rows)
        if str(node.get("state", "normal")) == "routing_failed"
    ]
    reachable_by_node: dict[int, list[int]] = {}
    for node_index in routing_failure_indices:
        reachable_destinations = [
            destination_index
            for destination_index, out_interface in enumerate(routing_rows[node_index])
            if destination_index != node_index and int(out_interface) > 0
        ]
        if reachable_destinations:
            reachable_by_node[node_index] = reachable_destinations
            continue

        node_rows[node_index]["state"] = "disabled"
        node_id = str(node_rows[node_index]["node_id"])
        for entry in fault_metadata.get("faulted_entities", []):
            if entry.get("entity_type") == "node" and str(entry.get("entity_id")) == node_id:
                entry["state"] = "disabled"
                entry.pop("unreachable_destination_nodes", None)
                break

    if len(reachable_by_node) != len(routing_failure_indices):
        return False

    for node_index in routing_failure_indices:
        reachable_destinations = reachable_by_node[node_index]
        failure_count = (
            1
            if len(reachable_destinations) == 1
            else rng.randint(1, len(reachable_destinations) - 1)
        )
        failed_destinations = sorted(rng.sample(reachable_destinations, failure_count))
        for destination_index in failed_destinations:
            routing_rows[node_index][destination_index] = -1

        node_id = str(node_rows[node_index]["node_id"])
        for entry in fault_metadata.get("faulted_entities", []):
            if entry.get("entity_type") == "node" and str(entry.get("entity_id")) == node_id:
                entry["unreachable_destination_nodes"] = [
                    str(node_rows[destination_index]["node_id"])
                    for destination_index in failed_destinations
                ]
                break

    return True
