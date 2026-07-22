import csv
import json
from pathlib import Path

import pytest

from scene_generator.runner import run


_BRITE_SAMPLE = """Nodes:
0 0 0
1 1 1
Edges:
0 0 1 0 0 100
"""


_NUMERIC_BRITE_SAMPLE = """Nodes:
10 0 0
2 1 1
Edges:
0 10 2 0 0 100
"""


_THREE_NODE_BRITE_SAMPLE = """Nodes:
0 0 0
1 1 1
2 2 2
Edges:
0 0 1 0 0 100
1 1 2 0 0 100
"""


def _assert_tm_metadata(tm_metadata: dict[str, object]) -> None:
    selected_mode = str(tm_metadata["selected_mode"])
    assert selected_mode in {"uniform", "exponential", "gravity", "spike"}

    active_rule = dict(tm_metadata["active_rule"])
    if selected_mode == "uniform":
        assert active_rule["uniform_range_mbps"] == [1.0, 100.0]
    elif selected_mode == "exponential":
        assert active_rule["exponential_scale"] == 20.0
    elif selected_mode == "gravity":
        assert active_rule["mass_range"] == [0.5, 2.0]
        assert active_rule["scale"] == 100.0
    elif selected_mode == "spike":
        assert active_rule["baseline_range_mbps"] == [1.0, 20.0]
        assert active_rule["spike_probability"] == 0.05
        assert active_rule["spike_multiplier"] == 10.0


def test_run_generates_multiple_scenes_when_configured(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 11
scenes_per_topology: 2
scene_duration: 1800
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
fault_generation:
  scenario_probabilities:
    normal: 1.0
""",
        encoding="utf-8",
    )

    scene_dirs = run(cfg)
    assert len(scene_dirs) == 2
    assert scene_dirs[0] != scene_dirs[1]
    assert all(scene.exists() for scene in scene_dirs)
    names = [scene.name for scene in scene_dirs]
    assert names[0] == "config_id0001_sample_t1800s"
    assert names[1] == "config_id0002_sample_t1800s"
    for topology_scene_index, scene in enumerate(scene_dirs, start=1):
        metadata = json.loads((scene / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["topology_scene_index"] == topology_scene_index
        assert metadata["scenes_per_topology"] == 2
        assert metadata["scene_duration"] == 1800.0
        assert metadata["generation"]["routing"]["mode"] == "weighted_shortest_path"
        assert metadata["generation"]["routing"]["weight_range"] == [1.0, 10.0]
        assert metadata["generation"]["routing"]["unreachable_value"] == -1
        assert "random_static_routing" not in metadata["generation"]["routing"]
        assert metadata["topology"]["file_name"] == "sample.brite"
        _assert_tm_metadata(metadata["generation"]["traffic_matrix"])
        assert metadata["generation"]["nics"]["queue_policy_mode"] == "mixed"
        assert metadata["generation"]["flow_feature"]["selection_mode"] == "mixed"
        assert metadata["generation"]["nics"]["active_rule"]["queue_policy_candidates"] == [
            "FIFO",
            "RED",
            "CoDel",
            "FqCoDel",
        ]
        assert metadata["generation"]["flow_feature"]["active_rule"]["mode_probabilities"] == {
            "poisson": 0.4,
            "on_off": 0.3,
            "cbr": 0.3,
        }
        assert metadata["generation"]["traffic_constraints"]["drop_unreachable_demands"] is True
        assert metadata["generation"]["traffic_constraints"]["cap_per_flow_to_path_bottleneck"] is False
        assert metadata["generation"]["traffic_constraints"]["cap_per_flow_to_feature_limit"] is True
        assert metadata["generation"]["fault_generation"] == {
            "scenario_probabilities": {"normal": 1.0},
            "node_state_probabilities": {
                "disabled": 0.5,
                "routing_failed": 0.5,
            },
            "channel_state_probabilities": {"disabled": 0.5, "degraded": 0.5},
            "channel_degradation_multipliers": [0.5, 0.2, 0.1],
            "nic_state_probabilities": {
                "disabled": 1.0,
            },
            "selected_scenario": "normal",
            "fault_count": 0,
            "faulted_entities": [],
        }
        assert metadata["summary"]["node_count"] == 2
        assert metadata["summary"]["channel_count"] == 1
        assert metadata["summary"]["nic_count"] == 2
        assert metadata["summary"]["fault_scenario"] == "normal"
        assert metadata["summary"]["fault_count"] == 0
        assert "node_type_counts" not in metadata["summary"]
        assert "events" not in metadata["generation"]
        assert "event_count" not in metadata["summary"]
        assert "event_type_counts" not in metadata["summary"]
        assert "events.jsonl" not in metadata["output_files"]
        assert not (scene / "events.jsonl").exists()
        assert (scene / "traffic.jsonl").exists()
        assert (scene / "channels.csv").exists()
        assert (scene / "metadata.json").exists()
        assert not (scene / "links.csv").exists()
        assert not (scene / "events.csv").exists()
        assert not (scene / "traffic.csv").exists()


def test_run_only_generates_topologies_within_configured_node_limit(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "included.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")
    (topo_dir / "excluded.brite").write_text(_THREE_NODE_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 11
scenes_per_topology: 1
max_topology_nodes: 2
scene_duration: 10
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
fault_generation:
  scenario_probabilities:
    normal: 1.0
""",
        encoding="utf-8",
    )

    scene_dirs = run(cfg)

    assert len(scene_dirs) == 1
    metadata = json.loads((scene_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["topology"]["file_name"] == "included.brite"


def test_run_generates_configured_count_for_every_topology(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "a.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")
    (topo_dir / "b.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 11
scenes_per_topology: 2
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["*.brite"]
fault_generation:
  scenario_probabilities:
    normal: 1.0
""",
        encoding="utf-8",
    )

    scene_dirs = run(cfg)
    metadata = [json.loads((scene / "metadata.json").read_text(encoding="utf-8")) for scene in scene_dirs]

    assert len(scene_dirs) == 4
    assert [scene.name for scene in scene_dirs] == [
        "config_id0001_a_t60s",
        "config_id0002_a_t60s",
        "config_id0003_b_t60s",
        "config_id0004_b_t60s",
    ]
    assert [(item["topology"]["file_name"], item["topology_scene_index"]) for item in metadata] == [
        ("a.brite", 1),
        ("a.brite", 2),
        ("b.brite", 1),
        ("b.brite", 2),
    ]


def test_run_applies_exactly_two_scene_level_faults(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 19
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["sample.brite"]
fault_generation:
  scenario_probabilities:
    double: 1.0
""",
        encoding="utf-8",
    )

    scene = run(cfg)[0]
    with (scene / "nodes.csv").open(encoding="utf-8", newline="") as handle:
        nodes = list(csv.DictReader(handle))
    with (scene / "channels.csv").open(encoding="utf-8", newline="") as handle:
        channels = list(csv.DictReader(handle))
    with (scene / "nics.csv").open(encoding="utf-8", newline="") as handle:
        nics = list(csv.DictReader(handle))
    metadata = json.loads((scene / "metadata.json").read_text(encoding="utf-8"))

    faulted_entities = {
        *(('node', row['node_id']) for row in nodes if row['state'] != 'normal'),
        *(('channel', row['channel_id']) for row in channels if row['state'] != 'normal'),
        *(('nic', row['nic_id']) for row in nics if row['state'] != 'normal'),
    }
    reported_entities = {
        (item["entity_type"], item["entity_id"])
        for item in metadata["generation"]["fault_generation"]["faulted_entities"]
    }

    assert len(faulted_entities) == 2
    assert reported_entities == faulted_entities
    assert metadata["generation"]["fault_generation"]["selected_scenario"] == "double"
    assert metadata["summary"]["fault_scenario"] == "double"
    assert metadata["summary"]["fault_count"] == 2


def test_run_does_not_generate_runtime_events_when_legacy_config_is_enabled(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 11
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["sample.brite"]
events:
  enabled: true
  count: 2
""",
        encoding="utf-8",
    )

    scene = run(cfg)[0]
    metadata = json.loads((scene / "metadata.json").read_text(encoding="utf-8"))

    assert not (scene / "events.jsonl").exists()
    assert "events.jsonl" not in metadata["output_files"]
    assert "events" not in metadata["generation"]
    assert "event_count" not in metadata["summary"]


def test_run_cleans_old_scene_directories_before_generating(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    output_root = tmp_path / "out"
    old_scene = output_root / "config_id9999_old_t60s"
    keep_dir = output_root / "keep_me"
    old_scene.mkdir(parents=True)
    keep_dir.mkdir()
    (old_scene / "metadata.json").write_text("{}", encoding="utf-8")
    (old_scene / "links.csv").write_text("link_id,src,dst\n", encoding="utf-8")
    (old_scene / "stale.txt").write_text("stale", encoding="utf-8")
    (keep_dir / "notes.txt").write_text("keep", encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 11
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
""",
        encoding="utf-8",
    )

    scene_dirs = run(cfg)

    assert len(scene_dirs) == 1
    assert not old_scene.exists()
    assert keep_dir.exists()
    assert (keep_dir / "notes.txt").exists()
    assert scene_dirs[0].exists()


def test_run_scene_id_can_distinguish_more_than_100_scenes(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config_many.yaml"
    cfg.write_text(
        """
output_root: ./out_many
seed: 21
scenes_per_topology: 105
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
""",
        encoding="utf-8",
    )

    scene_dirs = run(cfg)
    assert len(scene_dirs) == 105
    assert scene_dirs[0].name == "config_many_id0001_sample_t60s"
    assert scene_dirs[-1].name == "config_many_id0105_sample_t60s"


def test_run_assigns_internal_ids_using_natural_numeric_node_order(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "numeric.brite").write_text(_NUMERIC_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config_numeric.yaml"
    cfg.write_text(
        """
output_root: ./out_numeric
seed: 31
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["numeric.brite"]
fault_generation:
  scenario_probabilities:
    normal: 1.0
""",
        encoding="utf-8",
    )

    scene_dir = run(cfg)[0]
    with (scene_dir / "nodes.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with (scene_dir / "channels.csv").open("r", encoding="utf-8", newline="") as handle:
        channels = list(csv.DictReader(handle))
    with (scene_dir / "nics.csv").open("r", encoding="utf-8", newline="") as handle:
        nics = list(csv.DictReader(handle))
    with (scene_dir / "routing_matrix.csv").open("r", encoding="utf-8", newline="") as handle:
        routing_lines = [line.strip() for line in handle.readlines()]
    traffic_first = json.loads((scene_dir / "traffic.jsonl").read_text(encoding="utf-8").splitlines()[0])

    assert rows == [
        {
            "node_id": "N0001",
            "state": "normal",
            "latitude": "1.0",
            "longitude": "1.0",
        },
        {
            "node_id": "N0002",
            "state": "normal",
            "latitude": "0.0",
            "longitude": "0.0",
        },
    ]
    assert channels[0]["channel_id"] == "C0001"
    assert channels[0]["src"] == "N0001"
    assert channels[0]["dst"] == "N0002"
    assert nics[0]["nic_id"] == "IF0001"
    assert nics[0]["node"] == "N0001"
    assert nics[0]["interface_index"] == "1"
    assert nics[0]["channel_id"] == "C0001"
    assert {traffic_first["src"], traffic_first["dst"]} == {"N0001", "N0002"}
    assert routing_lines == ["0,1", "1,0"]


@pytest.mark.parametrize("fault_entity_type", ["node", "channel"])
def test_run_builds_routes_from_post_fault_reachability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_entity_type: str,
) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "line.brite").write_text(_THREE_NODE_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 41
scenes_per_topology: 1
scene_duration: 10
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["line.brite"]
fault_generation:
  scenario_probabilities:
    single: 1.0
""",
        encoding="utf-8",
    )

    def force_fault(nodes, channels, nics, fault_config, rng, routing_rows=None):
        del fault_config, rng, routing_rows
        for node in nodes:
            node["state"] = "normal"
        for channel in channels:
            channel["state"] = "normal"
            channel["capacity_multiplier"] = 1.0
        for nic in nics:
            nic["state"] = "normal"

        if fault_entity_type == "node":
            nodes[1]["state"] = "disabled"
            entity_id = nodes[1]["node_id"]
        else:
            channels[1]["state"] = "disabled"
            entity_id = channels[1]["channel_id"]
        return {
            "selected_scenario": "single",
            "fault_count": 1,
            "faulted_entities": [
                {
                    "entity_type": fault_entity_type,
                    "entity_id": entity_id,
                    "state": "disabled",
                }
            ],
        }

    monkeypatch.setattr("scene_generator.runner.apply_scene_faults", force_fault)

    scene_dir = run(cfg)[0]
    with (scene_dir / "routing_matrix.csv").open("r", encoding="utf-8", newline="") as handle:
        routing_rows = [[int(value) for value in row] for row in csv.reader(handle)]

    assert routing_rows[0][2] == -1
    assert routing_rows[2][0] == -1
