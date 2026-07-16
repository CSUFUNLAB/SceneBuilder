import networkx as nx

from scene_generator.generators.traffic import apply_hard_traffic_constraints, generate_traffic
from scene_generator.rng import RandomManager


class _Config:
    traffic_matrix = {
        "mode_probabilities": {"uniform": 1.0},
        "uniform_range_mbps": [5.0, 5.0],
    }
    flow_feature = {
        "selection_mode": "mixed",
        "selection_mode_probabilities": {},
        "single_model": "poisson",
        "single_model_probabilities": {},
        "mode_probabilities": {"poisson": 1.0},
        "poisson": {"lambda_range": [3.0, 3.0]},
        "on_off": {},
        "cbr": {},
    }


def test_traffic_contains_parameter_values_without_summary_field() -> None:
    graph = nx.Graph()
    graph.add_edge("A", "B")

    rows = generate_traffic(graph, _Config(), RandomManager(2))
    assert len(rows) == 2
    assert all(row["feature_model"] == "poisson" for row in rows)
    assert all(str(row["param_lambda"]) != "" for row in rows)
    assert "feature_summary" not in rows[0]


class _CbrConfig:
    traffic_matrix = {
        "mode_probabilities": {"uniform": 1.0},
        "uniform_range_mbps": [5.0, 5.0],
    }
    flow_feature = {
        "selection_mode": "mixed",
        "selection_mode_probabilities": {},
        "single_model": "poisson",
        "single_model_probabilities": {},
        "mode_probabilities": {"cbr": 1.0},
        "poisson": {"lambda_range": [3.0, 3.0]},
        "on_off": {},
        "cbr": {},
    }


def test_traffic_cbr_rows_have_no_extra_rate_parameters() -> None:
    graph = nx.Graph()
    graph.add_edge("A", "B")

    rows = generate_traffic(graph, _CbrConfig(), RandomManager(3))
    assert len(rows) == 2
    for row in rows:
        assert row["feature_model"] == "cbr"
        assert "param_lambda" not in row
        assert "param_on_mean" not in row
        assert "param_off_mean" not in row
        assert "param_peak_rate_mbps" not in row
        assert "param_rate_mbps" not in row


def test_traffic_can_return_generation_metadata() -> None:
    graph = nx.Graph()
    graph.add_edge("A", "B")

    rows, metadata = generate_traffic(graph, _Config(), RandomManager(4), include_metadata=True)

    assert len(rows) == 2
    assert metadata["traffic_matrix"]["selected_mode"] == "uniform"
    assert metadata["traffic_matrix"]["active_rule"]["uniform_range_mbps"] == [5.0, 5.0]
    assert metadata["traffic_matrix"]["flow_sampling"] == {
        "node_count": 2,
        "available_flow_pairs": 2,
        "requested_flows_per_node_range": None,
        "selected_flows_per_node": 1.0,
        "selected_flow_count": 2,
        "max_flow_count": None,
        "effective_flow_count": 2,
        "capped_by_available_flow_pairs": False,
        "capped_by_max_flow_count": False,
        "sampled": False,
    }
    assert metadata["flow_feature"]["selection_mode"] == "mixed"
    assert metadata["flow_feature"]["active_rule"]["mode_probabilities"] == {"poisson": 1.0}


class _SampledTrafficConfig:
    traffic_matrix = {
        "mode_probabilities": {"uniform": 1.0},
        "uniform_range_mbps": [5.0, 5.0],
        "flow_count_range": [0.5, 0.5],
    }
    flow_feature = _Config.flow_feature


def test_traffic_can_sample_a_configured_number_of_flows_per_node() -> None:
    graph = nx.Graph()
    graph.add_edges_from([("A", "B"), ("B", "C")])

    rows, metadata = generate_traffic(graph, _SampledTrafficConfig(), RandomManager(7), include_metadata=True)

    assert len(rows) == 2
    assert len({(row["src"], row["dst"]) for row in rows}) == 2
    assert [row["flow_id"] for row in rows] == ["F000001", "F000002"]
    assert metadata["traffic_matrix"]["flow_sampling"] == {
        "node_count": 3,
        "available_flow_pairs": 6,
        "requested_flows_per_node_range": [0.5, 0.5],
        "selected_flows_per_node": 0.5,
        "selected_flow_count": 2,
        "max_flow_count": None,
        "effective_flow_count": 2,
        "capped_by_available_flow_pairs": False,
        "capped_by_max_flow_count": False,
        "sampled": True,
    }


class _CappedTrafficConfig:
    traffic_matrix = {
        "mode_probabilities": {"uniform": 1.0},
        "uniform_range_mbps": [5.0, 5.0],
        "flow_count_range": [2.0, 2.0],
        "max_flow_count": 2,
    }
    flow_feature = _Config.flow_feature


def test_traffic_caps_sampled_flow_count() -> None:
    graph = nx.Graph()
    graph.add_edges_from([("A", "B"), ("B", "C")])

    rows, metadata = generate_traffic(graph, _CappedTrafficConfig(), RandomManager(9), include_metadata=True)

    assert len(rows) == 2
    assert metadata["traffic_matrix"]["flow_sampling"] == {
        "node_count": 3,
        "available_flow_pairs": 6,
        "requested_flows_per_node_range": [2.0, 2.0],
        "selected_flows_per_node": 2.0,
        "selected_flow_count": 6,
        "max_flow_count": 2,
        "effective_flow_count": 2,
        "capped_by_available_flow_pairs": False,
        "capped_by_max_flow_count": True,
        "sampled": True,
    }


class _SampledTrafficRangeConfig:
    traffic_matrix = {
        "mode_probabilities": {"uniform": 1.0},
        "uniform_range_mbps": [5.0, 5.0],
        "flow_count_range": [0.3, 0.7],
    }
    flow_feature = _Config.flow_feature


def test_traffic_randomizes_flows_per_node_within_configured_range() -> None:
    graph = nx.path_graph([f"N{index}" for index in range(10)])

    rows, metadata = generate_traffic(graph, _SampledTrafficRangeConfig(), RandomManager(8), include_metadata=True)

    selected_flows_per_node = metadata["traffic_matrix"]["flow_sampling"]["selected_flows_per_node"]
    selected_count = metadata["traffic_matrix"]["flow_sampling"]["selected_flow_count"]
    assert 0.3 <= selected_flows_per_node <= 0.7
    assert 3 <= selected_count <= 7
    assert len(rows) == selected_count


def test_traffic_only_generates_flows_between_reachable_nodes() -> None:
    graph = nx.Graph()
    graph.add_edges_from([("A", "B"), ("C", "D")])

    rows, metadata = generate_traffic(graph, _Config(), RandomManager(10), include_metadata=True)

    components = [{"A", "B"}, {"C", "D"}]
    assert len(rows) == 4
    assert all(any({row["src"], row["dst"]} <= component for component in components) for row in rows)
    assert metadata["traffic_matrix"]["flow_sampling"]["available_flow_pairs"] == 4


class _SingleFeatureConfig:
    traffic_matrix = {
        "mode_probabilities": {"uniform": 1.0},
        "uniform_range_mbps": [5.0, 5.0],
    }
    flow_feature = {
        "selection_mode": "mixed",
        "selection_mode_probabilities": {"single": 1.0},
        "single_model": "poisson",
        "single_model_probabilities": {"on_off": 1.0},
        "mode_probabilities": {"poisson": 1.0},
        "poisson": {"lambda_range": [3.0, 3.0]},
        "on_off": {
            "on_mean_range": [1.0, 1.0],
            "off_mean_range": [2.0, 2.0],
            "peak_rate_range_mbps": [3.0, 3.0],
        },
        "cbr": {},
    }


def test_traffic_can_use_single_feature_mode() -> None:
    graph = nx.Graph()
    graph.add_edge("A", "B")

    rows, metadata = generate_traffic(graph, _SingleFeatureConfig(), RandomManager(5), include_metadata=True)

    assert len(rows) == 2
    assert all(row["feature_model"] == "on_off" for row in rows)
    assert metadata["flow_feature"]["selection_mode"] == "single"
    assert metadata["flow_feature"]["active_rule"]["model"] == "on_off"


class _SpikeTmConfig:
    traffic_matrix = {
        "mode_probabilities": {"spike": 1.0},
        "spike": {
            "baseline_range_mbps": [2.0, 2.0],
            "spike_probability": 1.0,
            "spike_multiplier": 3.0,
        },
    }
    flow_feature = {
        "selection_mode": "mixed",
        "selection_mode_probabilities": {},
        "single_model": "poisson",
        "single_model_probabilities": {},
        "mode_probabilities": {"poisson": 1.0},
        "poisson": {"lambda_range": [3.0, 3.0]},
        "on_off": {},
        "cbr": {},
    }


def test_traffic_can_randomly_select_traffic_matrix_mode_per_scene() -> None:
    graph = nx.Graph()
    graph.add_edge("A", "B")

    rows, metadata = generate_traffic(graph, _SpikeTmConfig(), RandomManager(6), include_metadata=True)

    assert len(rows) == 2
    assert metadata["traffic_matrix"]["selected_mode"] == "spike"
    assert metadata["traffic_matrix"]["active_rule"] == {
        "baseline_range_mbps": [2.0, 2.0],
        "spike_probability": 1.0,
        "spike_multiplier": 3.0,
    }


def test_hard_traffic_constraints_zero_unreachable_without_capping_to_path_bottleneck() -> None:
    traffic_rows = [
        {"flow_id": "F000001", "src": "1", "dst": "3", "demand_mbps": 9.0, "feature_model": "poisson"},
        {"flow_id": "F000002", "src": "1", "dst": "4", "demand_mbps": 4.0, "feature_model": "poisson"},
    ]
    routing_map = {
        ("1", "3"): "2",
        ("2", "3"): "3",
        ("1", "4"): "-1",
    }
    channel_rows = [
        {"channel_id": "C0001", "src": "1", "dst": "2", "bandwidth_mbps": 10.0},
        {"channel_id": "C0002", "src": "2", "dst": "3", "bandwidth_mbps": 5.0},
    ]

    constrained_rows, stats = apply_hard_traffic_constraints(traffic_rows, routing_map, channel_rows)

    assert constrained_rows[0]["demand_mbps"] == 9.0
    assert constrained_rows[1]["demand_mbps"] == 0.0
    assert stats["cap_per_flow_to_path_bottleneck"] is False
    assert stats["drop_unreachable_demands"] is True
    assert stats["flows_capped_by_path_bottleneck"] == 0
    assert stats["unreachable_flows_zeroed"] == 1


def test_hard_traffic_constraints_cap_to_feature_limit_when_needed() -> None:
    traffic_rows = [
        {
            "flow_id": "F000003",
            "src": "1",
            "dst": "2",
            "demand_mbps": 18.0,
            "feature_model": "on_off",
            "param_peak_rate_mbps": 12.0,
        }
    ]
    routing_map = {
        ("1", "2"): "2",
    }
    channel_rows = [
        {"channel_id": "C0001", "src": "1", "dst": "2", "bandwidth_mbps": 20.0},
    ]

    constrained_rows, stats = apply_hard_traffic_constraints(traffic_rows, routing_map, channel_rows)

    assert constrained_rows[0]["demand_mbps"] == 12.0
    assert stats["cap_per_flow_to_feature_limit"] is True
    assert stats["flows_capped_by_feature_limit"] == 1
