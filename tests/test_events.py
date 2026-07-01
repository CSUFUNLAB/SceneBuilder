from types import SimpleNamespace

from scene_generator.generators.events import generate_events
from scene_generator.rng import RandomManager


def test_generate_events_samples_each_entity_at_most_once() -> None:
    config = SimpleNamespace(
        scene_duration=10.0,
        events={
            "enabled": True,
            "count": 10,
            "event_type_probabilities": {
                "node": {"fault": 1.0},
                "channel": {"recovery": 1.0},
                "nic": {"fault": 1.0},
                "data_flow": {"increase": 1.0},
            },
            "data_flow": {
                "increase_multiplier_range": [1.5, 1.5],
                "decrease_multiplier_range": [0.5, 0.5],
            },
        },
    )
    nodes_rows = [{"node_id": "N0001", "state": "disabled"}]
    channel_rows = [{"channel_id": "C0001", "state": "normal"}]
    nics_rows = [{"nic_id": "IF0001", "state": "disabled"}]
    traffic_rows = [{"flow_id": "F000001"}]

    rows, overrides = generate_events(
        nodes_rows,
        channel_rows,
        nics_rows,
        traffic_rows,
        config,
        RandomManager(1),
    )

    keys = {(row["entity_type"], row["entity_id"]) for row in rows}
    assert len(rows) == 4
    assert len(keys) == 4
    assert overrides[("node", "N0001")] == "normal"
    assert overrides[("channel", "C0001")] == "disabled"
    assert overrides[("nic", "IF0001")] == "normal"
    flow_event = next(row for row in rows if row["entity_type"] == "data_flow")
    assert flow_event["event_type"] == "increase"
    assert flow_event["rate_multiplier"] == 1.5
