from pathlib import Path

import pytest

from scene_generator.config import load_config


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
scenes_per_topology: 3
max_topology_nodes: 25
scene_duration: 600
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)
    assert loaded.seed == 123
    assert loaded.scenes_per_topology == 3
    assert loaded.max_topology_nodes == 25
    assert loaded.scene_duration == 600.0
    assert loaded.output_root.is_absolute()
    assert loaded.topology_sources[0].root_dirs[0].is_absolute()
    assert loaded.fault_generation["scenario_probabilities"] == {
        "normal": 0.5,
        "single": 0.3,
        "double": 0.2,
    }
    assert loaded.fault_generation["node_state_probabilities"] == {
        "disabled": 0.5,
        "routing_failed": 0.5,
    }
    assert loaded.fault_generation["channel_state_probabilities"] == {
        "disabled": 0.5,
        "degraded": 0.5,
    }
    assert loaded.fault_generation["channel_degradation_multipliers"] == [0.5, 0.2, 0.1]
    assert loaded.fault_generation["nic_state_probabilities"] == {
        "disabled": 1.0,
    }


def test_load_config_defaults_to_100_scenes_per_topology(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["sample.brite"]
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)
    assert loaded.scenes_per_topology == 100
    assert loaded.max_topology_nodes == 50
    assert loaded.traffic_matrix["flow_count_range"] == [0.1, 0.25]
    assert loaded.traffic_matrix["max_flow_count"] == 1000


def test_load_config_rejects_removed_num_scenes(tmp_path: Path) -> None:
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
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["sample.brite"]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="num_scenes has been removed; use scenes_per_topology"):
        load_config(cfg)


def test_load_config_rejects_removed_topology_weight(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    weight: 1.0
    root_dir: ./topologies
    glob_patterns: ["sample.brite"]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"topology_sources\[\]\.weight has been removed; use enabled"):
        load_config(cfg)


def test_load_config_ignores_legacy_routing_mode_and_random_static_fields(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 300
topology_sources:
  - name: s
    type: brite
    enabled: true
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


def test_load_config_supports_events_section(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
events:
  enabled: true
  count: 3
  event_type_probabilities:
    node:
      fault: 1.0
    channel:
      recovery: 1.0
    nic:
      fault: 1.0
    data_flow:
      increase: 1.0
  data_flow:
    increase_multiplier_range: [1.5, 2.0]
    decrease_multiplier_range: [0.2, 0.5]
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)

    assert loaded.events["enabled"] is True
    assert loaded.events["count"] == 3
    assert loaded.events["event_type_probabilities"]["node"] == {"fault": 1.0}
    assert loaded.events["event_type_probabilities"]["channel"] == {"recovery": 1.0}
    assert loaded.events["event_type_probabilities"]["nic"] == {"fault": 1.0}
    assert loaded.events["event_type_probabilities"]["data_flow"] == {"increase": 1.0}
    assert loaded.events["data_flow"]["increase_multiplier_range"] == [1.5, 2.0]
    assert loaded.events["data_flow"]["decrease_multiplier_range"] == [0.2, 0.5]


def test_load_config_allows_disabled_source_when_another_source_is_enabled(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: disabled
    type: brite
    enabled: false
    root_dir: ./topologies
    glob_patterns: ["missing.brite"]
  - name: enabled
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["sample.brite"]
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)

    assert [source.enabled for source in loaded.topology_sources] == [False, True]


def test_load_config_replaces_default_fault_scenario_probabilities(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
fault_generation:
  scenario_probabilities:
    single: 1.0
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)

    assert loaded.fault_generation["scenario_probabilities"] == {"single": 1.0}


def test_load_config_replaces_default_channel_state_probabilities(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
fault_generation:
  channel_state_probabilities:
    degraded: 1.0
  channel_degradation_multipliers: [0.1]
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)

    assert loaded.fault_generation["channel_state_probabilities"] == {"degraded": 1.0}
    assert loaded.fault_generation["channel_degradation_multipliers"] == [0.1]


def test_load_config_replaces_default_node_state_probabilities(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
fault_generation:
  node_state_probabilities:
    routing_failed: 1.0
""",
        encoding="utf-8",
    )

    loaded = load_config(cfg)

    assert loaded.fault_generation["node_state_probabilities"] == {"routing_failed": 1.0}


def test_load_config_rejects_directional_nic_failure_state(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
fault_generation:
  nic_state_probabilities:
    tx_failed: 1.0
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsupported fault_generation.nic_state_probabilities states"):
        load_config(cfg)


@pytest.mark.parametrize("section_name", ["nodes", "nics", "link_generation"])
def test_load_config_rejects_per_entity_state_probabilities(tmp_path: Path, section_name: str) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
{section_name}:
  state_probabilities:
    normal: 1.0
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=rf"{section_name}\.state_probabilities has been removed"):
        load_config(cfg)


def test_load_config_rejects_unknown_fault_scenario(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
fault_generation:
  scenario_probabilities:
    normal: 0.5
    triple: 0.5
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsupported fault_generation.scenario_probabilities scenarios"):
        load_config(cfg)


def test_load_config_rejects_fault_probabilities_that_do_not_sum_to_one(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
fault_generation:
  scenario_probabilities:
    normal: 0.6
    single: 0.3
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fault_generation.scenario_probabilities values must sum to 1"):
        load_config(cfg)


def test_load_config_rejects_unsupported_channel_degradation_multiplier(tmp_path: Path) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 123
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.brite"]
fault_generation:
  channel_degradation_multipliers: [0.25]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="may only contain 0.5, 0.2, or 0.1"):
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
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
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
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: brite
    enabled: true
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
scenes_per_topology: 1
scene_duration: 60
topology_sources:
  - name: s
    type: topologyzoo
    enabled: true
    root_dir: ./topologies
    glob_patterns: ["**/*.graphml", "**/*.gml"]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="graphml support has been removed"):
        load_config(cfg)
