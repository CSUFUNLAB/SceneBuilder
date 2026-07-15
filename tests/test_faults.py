import pytest

from scene_generator.generators.faults import apply_scene_faults
from scene_generator.rng import RandomManager


def _rows() -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    nodes = [
        {"node_id": "N0001", "state": "disabled"},
        {"node_id": "N0002", "state": "disabled"},
    ]
    channels = [{"channel_id": "C0001", "state": "disabled"}]
    nics = [
        {"nic_id": "IF0001", "state": "disabled"},
        {"nic_id": "IF0002", "state": "disabled"},
    ]
    return nodes, channels, nics


def _faulted_entities(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for rows in groups for row in rows if row["state"] != "normal"]


@pytest.mark.parametrize(
    ("scenario", "expected_count"),
    [("normal", 0), ("single", 1), ("double", 2)],
)
def test_apply_scene_faults_sets_exact_scenario_fault_count(scenario: str, expected_count: int) -> None:
    nodes, channels, nics = _rows()

    metadata = apply_scene_faults(
        nodes,
        channels,
        nics,
        {"scenario_probabilities": {scenario: 1.0}},
        RandomManager(7),
    )

    faulted = _faulted_entities(nodes, channels, nics)
    assert len(faulted) == expected_count
    assert metadata["selected_scenario"] == scenario
    assert metadata["fault_count"] == expected_count
    assert len(metadata["faulted_entities"]) == expected_count
    assert len({item["entity_id"] for item in metadata["faulted_entities"]}) == expected_count


def test_apply_scene_faults_is_reproducible_for_same_seed() -> None:
    first = _rows()
    second = _rows()
    config = {"scenario_probabilities": {"double": 1.0}}

    first_metadata = apply_scene_faults(*first, config, RandomManager(23))
    second_metadata = apply_scene_faults(*second, config, RandomManager(23))

    assert first_metadata == second_metadata


def test_apply_scene_faults_rejects_scenario_larger_than_entity_pool() -> None:
    nodes = [{"node_id": "N0001", "state": "normal"}]

    with pytest.raises(ValueError, match="requires 2 entities"):
        apply_scene_faults(
            nodes,
            [],
            [],
            {"scenario_probabilities": {"double": 1.0}},
            RandomManager(1),
        )


@pytest.mark.parametrize("multiplier", [0.5, 0.2, 0.1])
def test_apply_scene_faults_can_degrade_channel_capacity(multiplier: float) -> None:
    channel = {"channel_id": "C0001", "state": "normal", "capacity_multiplier": 1.0}

    metadata = apply_scene_faults(
        [],
        [channel],
        [],
        {
            "scenario_probabilities": {"single": 1.0},
            "channel_state_probabilities": {"degraded": 1.0},
            "channel_degradation_multipliers": [multiplier],
        },
        RandomManager(11),
    )

    assert channel["state"] == "degraded"
    assert channel["capacity_multiplier"] == multiplier
    assert metadata["faulted_entities"] == [
        {
            "entity_type": "channel",
            "entity_id": "C0001",
            "state": "degraded",
            "capacity_multiplier": multiplier,
        }
    ]


def test_apply_scene_faults_keeps_full_capacity_for_disabled_channel() -> None:
    channel = {"channel_id": "C0001", "state": "normal", "capacity_multiplier": 0.1}

    apply_scene_faults(
        [],
        [channel],
        [],
        {
            "scenario_probabilities": {"single": 1.0},
            "channel_state_probabilities": {"disabled": 1.0},
            "channel_degradation_multipliers": [0.5, 0.2, 0.1],
        },
        RandomManager(11),
    )

    assert channel == {
        "channel_id": "C0001",
        "state": "disabled",
        "capacity_multiplier": 1.0,
    }


@pytest.mark.parametrize("state", ["disabled", "tx_failed", "rx_failed"])
def test_apply_scene_faults_can_select_nic_failure_mode(state: str) -> None:
    nic = {"nic_id": "IF0001", "state": "normal"}

    metadata = apply_scene_faults(
        [],
        [],
        [nic],
        {
            "scenario_probabilities": {"single": 1.0},
            "nic_state_probabilities": {state: 1.0},
        },
        RandomManager(17),
    )

    assert nic["state"] == state
    assert metadata["faulted_entities"] == [
        {
            "entity_type": "nic",
            "entity_id": "IF0001",
            "state": state,
        }
    ]
