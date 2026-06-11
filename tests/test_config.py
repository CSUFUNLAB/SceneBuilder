from pathlib import Path

import pytest

from network_scene_generator.config import load_config


_BRITE_SAMPLE = """Nodes:
0 0 0
1 1 1
Edges:
0 0 1 0 0 100
"""


def test_load_config_with_relative_paths(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
num_scenes: 3
scene_duration: 600
topology_sources:
  - name: s
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)
    assert loaded.seed == 123
    assert loaded.num_scenes == 3
    assert loaded.scene_duration == 600.0
    assert loaded.output_root.is_absolute()
    assert loaded.topology_sources[0].root_dirs[0].is_absolute()


def test_load_config_ignores_legacy_routing_mode_and_random_static_fields(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
num_scenes: 1
scene_duration: 300
topology_sources:
  - name: s
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
routing:
  mode: random_static_routing
  weight_range: [3.0, 7.0]
  random_static_routing:
    k_shortest_paths: 3
    fallback: shortest_path
  unreachable_value: 99
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)

    assert loaded.routing == {"weight_range": [3.0, 7.0]}


def test_load_config_ignores_legacy_events_section(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
num_scenes: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
events:
  enabled: true
  event_probability: 0.3
  event_time_range: [10.0, 200.0]
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)

    assert not hasattr(loaded, "events")


def test_load_config_allows_zero_weight_source_when_another_source_is_positive(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
num_scenes: 1
scene_duration: 60
topology_sources:
  - name: disabled
    type: brite
    weight: 0
    root_dir: ./topologies
    glob_patterns: ["missing.brite"]
  - name: enabled
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["sample.brite"]
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)

    assert [source.weight for source in loaded.topology_sources] == [0.0, 1.0]


def test_load_config_rejects_unknown_entity_state(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
num_scenes: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
nodes:
  state_probabilities:
    normal: 0.9
    degraded: 0.1
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="nodes.state_probabilities"):
        load_config(cfg)


def test_load_config_replaces_default_state_probabilities(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
num_scenes: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
nodes:
  state_probabilities:
    disabled: 1.0
nics:
  state_probabilities:
    disabled: 1.0
link_generation:
  state_probabilities:
    degraded: 1.0
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)

    assert loaded.nodes["state_probabilities"] == {"disabled": 1.0}
    assert loaded.nics["state_probabilities"] == {"disabled": 1.0}
    assert loaded.link_generation["state_probabilities"] == {"degraded": 1.0}


def test_load_config_rejects_saturated_entity_state(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
num_scenes: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
nics:
  state_probabilities:
    normal: 0.9
    saturated: 0.1
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="nics.state_probabilities"):
        load_config(cfg)


def test_load_config_keeps_legacy_fixed_traffic_matrix_mode_when_probabilities_missing(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
num_scenes: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
traffic_matrix:
  mode: spike
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)

    assert loaded.traffic_matrix["mode"] == "spike"
    assert loaded.traffic_matrix["mode_probabilities"] == {}


def test_load_config_maps_legacy_abr_flow_feature_to_cbr(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
num_scenes: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
flow_feature:
  single_model: abr
  mode_probabilities:
    poisson: 0.5
    abr: 0.5
  single_model_probabilities:
    abr: 1.0
  abr:
    target_rate_range_mbps: [12.0, 34.0]
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)

    assert loaded.flow_feature["single_model"] == "cbr"
    assert loaded.flow_feature["mode_probabilities"] == {"poisson": 0.5, "cbr": 0.5}
    assert loaded.flow_feature["single_model_probabilities"] == {"cbr": 1.0}
    assert loaded.flow_feature["cbr"] == {}
    assert "abr" not in loaded.flow_feature


def test_load_config_rejects_topologyzoo_graphml_glob_patterns(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.gml").write_text("graph [ node [ id 0 ] ]", encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
num_scenes: 1
scene_duration: 60
topology_sources:
  - name: s
    type: topologyzoo
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["**/*.graphml", "**/*.gml"]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="graphml support has been removed"):
        load_config(cfg)
