import csv
import json
from pathlib import Path

from network_scene_generator.runner import run


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
num_scenes: 2
scene_duration: 1800
topology_sources:
  - name: s
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
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
    for scene in scene_dirs:
        metadata = json.loads((scene / "metadata.json").read_text(encoding="utf-8"))
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
        assert metadata["summary"]["node_count"] == 2
        assert metadata["summary"]["link_count"] == 1
        assert metadata["summary"]["nic_count"] == 2
        assert "node_type_counts" not in metadata["summary"]
        assert "events" not in metadata["generation"]
        assert "event_count" not in metadata["summary"]
        assert "event_type_counts" not in metadata["summary"]
        assert "events.jsonl" not in metadata["output_files"]
        assert not (scene / "events.jsonl").exists()
        assert (scene / "traffic.jsonl").exists()
        assert (scene / "metadata.json").exists()
        assert not (scene / "events.csv").exists()
        assert not (scene / "traffic.csv").exists()


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
num_scenes: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    weight: 1.0
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
num_scenes: 105
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    weight: 1.0
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
num_scenes: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["numeric.brite"]
""",
        encoding="utf-8",
    )

    scene_dir = run(cfg)[0]
    with (scene_dir / "nodes.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with (scene_dir / "links.csv").open("r", encoding="utf-8", newline="") as handle:
        links = list(csv.DictReader(handle))
    with (scene_dir / "nics.csv").open("r", encoding="utf-8", newline="") as handle:
        nics = list(csv.DictReader(handle))
    with (scene_dir / "routing_matrix.csv").open("r", encoding="utf-8", newline="") as handle:
        routing_lines = [line.strip() for line in handle.readlines()]
    traffic_first = json.loads((scene_dir / "traffic.jsonl").read_text(encoding="utf-8").splitlines()[0])

    assert rows == [
        {
            "node_id": "N0001",
            "original_node_name": "2",
            "state": "normal",
            "latitude": "1.0",
            "longitude": "1.0",
        },
        {
            "node_id": "N0002",
            "original_node_name": "10",
            "state": "normal",
            "latitude": "0.0",
            "longitude": "0.0",
        },
    ]
    assert links[0]["src"] == "N0001"
    assert links[0]["dst"] == "N0002"
    assert nics[0]["nic_id"] == "IF0001"
    assert nics[0]["node"] == "N0001"
    assert nics[0]["interface_index"] == "1"
    assert traffic_first["src"] == "N0001"
    assert traffic_first["dst"] == "N0002"
    assert routing_lines == ["0,1", "1,0"]
