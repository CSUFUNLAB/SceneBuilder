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
    "disabled": 0.34,
    "tx_failed": 0.33,
    "rx_failed": 0.33,
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

    return {
        "selected_scenario": scenario,
        "fault_count": fault_count,
        "faulted_entities": faulted_entities,
    }
