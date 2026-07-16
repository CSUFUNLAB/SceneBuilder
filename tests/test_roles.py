import networkx as nx

from scene_generator.generators.channels import generate_channels
from scene_generator.generators.nodes import infer_node_roles
from scene_generator.rng import RandomManager


def test_infer_node_roles_component_rules_and_fixes() -> None:
    graph = nx.Graph()
    graph.add_edges_from(
        [
            ("1", "2"),
            ("1", "3"),
            ("1", "4"),
            ("1", "5"),
            ("1", "6"),
            ("7", "8"),
        ]
    )

    cfg = {
        "assignment_mode": "topology_role",
        "trust_input_node_roles": False,
        "topology_inference": {
            "core_ratio_range": [0.0, 0.0],
            "core_candidate_ratio_range": [0.0, 0.0],
            "edge_extra_ratio_range": [0.0, 0.0],
            "edge_candidate_ratio_range": [0.0, 0.0],
        },
    }

    roles = infer_node_roles(graph, cfg, RandomManager(1))

    assert roles["1"] == "core"
    for node in ("2", "3", "4", "5", "6"):
        assert roles[node] == "edge"

    assert {roles["7"], roles["8"]} == {"edge", "aggregation"}


def test_infer_node_roles_prefers_trusted_input_roles() -> None:
    graph = nx.Graph()
    graph.add_edge("1", "2")
    graph.add_edge("2", "3")
    graph.nodes["1"]["source_node_role"] = "edge"
    graph.nodes["2"]["source_node_role"] = "core"

    cfg = {
        "assignment_mode": "topology_role",
        "trust_input_node_roles": True,
        "trusted_node_role_fields": ["source_node_role"],
        "topology_inference": {
            "core_ratio_range": [0.0, 0.0],
            "core_candidate_ratio_range": [0.0, 0.0],
            "edge_extra_ratio_range": [0.0, 0.0],
            "edge_candidate_ratio_range": [0.0, 0.0],
        },
    }

    roles = infer_node_roles(graph, cfg, RandomManager(2))
    assert roles["1"] == "edge"
    assert roles["2"] == "core"


def test_infer_node_roles_fixed_mode_uses_default_node_type() -> None:
    graph = nx.path_graph(["1", "2", "3", "4"])
    cfg = {
        "assignment_mode": "fixed",
        "default_node_type": "aggregation",
        "type_candidates": ["core", "aggregation", "edge"],
    }

    roles = infer_node_roles(graph, cfg, RandomManager(3))

    assert roles == {"1": "aggregation", "2": "aggregation", "3": "aggregation", "4": "aggregation"}


def test_infer_node_roles_random_mode_uses_type_candidates() -> None:
    graph = nx.path_graph(["1", "2", "3", "4", "5", "6"])
    cfg = {
        "assignment_mode": "random",
        "default_node_type": "aggregation",
        "type_candidates": ["core", "edge"],
    }

    roles = infer_node_roles(graph, cfg, RandomManager(4))

    assert set(roles) == set(graph.nodes)
    assert set(roles.values()) <= {"core", "edge"}


class _LinkCfgDeterministic:
    nodes = {}
    link_generation = {
        "mode": "role_based_random",
        "preserve_input_bandwidth": False,
        "treat_as_undirected": True,
        "pure_random": {"bandwidth_candidates_mbps": [999]},
        "role_based_random": {
            "role_bandwidth_mbps": {
                "backbone": {"bandwidth_candidates_mbps": [10000]},
                "uplink": {"bandwidth_candidates_mbps": [2000]},
                "access": {"bandwidth_candidates_mbps": [100]},
                "lateral": {"bandwidth_candidates_mbps": [3000]},
            },
            "derived_link_role": {
                "aggregation_aggregation_lateral_probability": 1.0,
                "core_edge_uplink_probability": 1.0,
            },
            "trust_input_link_roles": False,
        },
    }


def test_channel_roles_are_mapped_from_node_roles() -> None:
    graph = nx.Graph()
    graph.add_edges_from(
        [
            ("1", "2"),  # core-core -> backbone
            ("1", "3"),  # core-aggregation -> uplink
            ("3", "4"),  # aggregation-edge -> access
            ("3", "5"),  # aggregation-aggregation -> lateral (p=1.0)
            ("1", "4"),  # core-edge -> uplink (p=1.0)
            ("4", "6"),  # edge-edge -> access
        ]
    )
    node_roles = {
        "1": "core",
        "2": "core",
        "3": "aggregation",
        "4": "edge",
        "5": "aggregation",
        "6": "edge",
    }

    rows = generate_channels(graph, _LinkCfgDeterministic(), RandomManager(3), node_roles=node_roles)
    by_pair = {(int(row["src"]), int(row["dst"])): int(row["bandwidth_mbps"]) for row in rows}
    by_pair_role = {(int(row["src"]), int(row["dst"])): str(row["channel_type"]) for row in rows}

    assert by_pair[(1, 2)] == 10000
    assert by_pair[(1, 3)] == 2000
    assert by_pair[(3, 4)] == 100
    assert by_pair[(3, 5)] == 3000
    assert by_pair[(1, 4)] == 2000
    assert by_pair[(4, 6)] == 100
    assert by_pair_role[(1, 2)] == "backbone"
    assert by_pair_role[(1, 3)] == "uplink"
    assert by_pair_role[(3, 4)] == "access"
    assert by_pair_role[(3, 5)] == "lateral"
    assert by_pair_role[(1, 4)] == "uplink"
    assert by_pair_role[(4, 6)] == "access"
    assert all(row["state"] == "normal" for row in rows)


class _LinkCfgPerturb:
    nodes = {}
    link_generation = {
        "mode": "role_based_random",
        "preserve_input_bandwidth": False,
        "treat_as_undirected": True,
        "pure_random": {"bandwidth_candidates_mbps": [999]},
        "role_based_random": {
            "role_bandwidth_mbps": {
                "backbone": {"bandwidth_candidates_mbps": [10000]},
                "uplink": {"bandwidth_candidates_mbps": [2000]},
                "access": {"bandwidth_candidates_mbps": [100]},
                "lateral": {"bandwidth_candidates_mbps": [3000]},
            },
            "derived_link_role": {
                "aggregation_aggregation_lateral_probability": 0.0,
                "core_edge_uplink_probability": 0.0,
            },
            "trust_input_link_roles": False,
        },
    }


def test_channel_role_perturbation_probabilities_take_effect() -> None:
    graph = nx.Graph()
    graph.add_edges_from(
        [
            ("1", "2"),  # aggregation-aggregation -> uplink when p_lateral=0
            ("1", "3"),  # core-edge -> access when p_uplink=0
        ]
    )
    node_roles = {
        "1": "aggregation",
        "2": "aggregation",
        "3": "edge",
    }
    node_roles_core_edge = {
        "1": "core",
        "2": "aggregation",
        "3": "edge",
    }

    rows_agg = generate_channels(
        graph.subgraph(["1", "2"]).copy(),
        _LinkCfgPerturb(),
        RandomManager(4),
        node_roles=node_roles,
    )
    row_agg = rows_agg[0]
    assert int(row_agg["bandwidth_mbps"]) == 2000
    assert str(row_agg["channel_type"]) == "uplink"

    rows_core_edge = generate_channels(
        graph.subgraph(["1", "3"]).copy(),
        _LinkCfgPerturb(),
        RandomManager(5),
        node_roles=node_roles_core_edge,
    )
    row_core_edge = rows_core_edge[0]
    assert int(row_core_edge["bandwidth_mbps"]) == 100
    assert str(row_core_edge["channel_type"]) == "access"
