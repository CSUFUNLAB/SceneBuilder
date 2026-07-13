from __future__ import annotations

from typing import Any

from ..rng import RandomManager
from ..utils.selection import weighted_pick

EVENT_FIELDS = ["event_id", "time", "entity_type", "entity_id", "event_type", "rate_multiplier"]


def _event_candidates(
    nodes_rows: list[dict[str, Any]],
    channel_rows: list[dict[str, Any]],
    nics_rows: list[dict[str, Any]],
    traffic_rows: list[dict[str, Any]],
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for row in nodes_rows:
        candidates.append({"entity_type": "node", "entity_id": str(row["node_id"])})
    for row in channel_rows:
        candidates.append({"entity_type": "channel", "entity_id": str(row["channel_id"])})
    for row in nics_rows:
        candidates.append({"entity_type": "nic", "entity_id": str(row["nic_id"])})
    for row in traffic_rows:
        candidates.append({"entity_type": "data_flow", "entity_id": str(row["flow_id"])})
    return candidates


def _select_event_type(events_cfg: dict[str, Any], entity_type: str, rng: RandomManager) -> str:
    probabilities = dict(events_cfg.get("event_type_probabilities", {}).get(entity_type, {"fault": 1.0}))
    return weighted_pick(probabilities, "fault", rng)


def _flow_rate_multiplier(events_cfg: dict[str, Any], event_type: str, rng: RandomManager) -> float:
    flow_cfg = dict(events_cfg.get("data_flow", {}))
    if event_type == "increase":
        low, high = flow_cfg.get("increase_multiplier_range", [1.2, 2.0])
    else:
        low, high = flow_cfg.get("decrease_multiplier_range", [0.2, 0.8])
    return round(float(rng.uniform(float(low), float(high))), 6)


def generate_events(
    nodes_rows: list[dict[str, Any]],
    channel_rows: list[dict[str, Any]],
    nics_rows: list[dict[str, Any]],
    traffic_rows: list[dict[str, Any]],
    config: Any,
    rng: RandomManager,
) -> list[dict[str, Any]]:
    events_cfg = dict(getattr(config, "events", {}))
    if not bool(events_cfg.get("enabled", False)):
        return []

    count = int(events_cfg.get("count", 0))
    if count <= 0:
        return []

    candidates = _event_candidates(nodes_rows, channel_rows, nics_rows, traffic_rows)
    if not candidates:
        return []

    rows: list[dict[str, Any]] = []
    scene_duration = float(getattr(config, "scene_duration", 0.0))

    selected_candidates = rng.sample(candidates, min(count, len(candidates)))
    for index, candidate in enumerate(selected_candidates, start=1):
        entity_type = candidate["entity_type"]
        entity_id = candidate["entity_id"]
        event_type = _select_event_type(events_cfg, entity_type, rng)

        row: dict[str, Any] = {
            "event_id": f"E{index:06d}",
            "time": round(rng.uniform(0.0, scene_duration), 6),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "event_type": event_type,
        }
        if entity_type == "data_flow" and event_type in {"increase", "decrease"}:
            row["rate_multiplier"] = _flow_rate_multiplier(events_cfg, event_type, rng)
        rows.append(row)

    rows.sort(key=lambda row: (float(row["time"]), str(row["event_id"])))
    return rows
