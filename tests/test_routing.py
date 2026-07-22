import networkx as nx

from scene_generator.generators.routing import build_operational_graph, generate_routing_matrix
from scene_generator.rng import RandomManager


class _Config:
    routing = {
        "weight_range": [1.0, 2.0],
    }


def _follow_route(route_map: dict[tuple[str, str], str], src: str, dst: str) -> list[str]:
    path = [src]
    current = src
    visited = {src}

    while current != dst:
        next_hop = route_map[(current, dst)]
        assert next_hop != "-1"
        assert next_hop not in visited
        path.append(next_hop)
        visited.add(next_hop)
        current = next_hop

    return path


def test_routing_unreachable_is_minus_one() -> None:
    graph = nx.Graph()
    graph.add_edge("A", "B")
    graph.add_node("C")

    rows, route_map = generate_routing_matrix(graph, _Config(), RandomManager(1))

    assert rows
    assert rows == [
        [0, 1, -1],
        [0, 1, -1],
        [-1, -1, 2],
    ]

    assert route_map[("A", "C")] == "-1"
    assert route_map[("A", "A")] == "A"

def test_weighted_shortest_path_routes_reach_destination_without_loops() -> None:
    graph = nx.Graph()
    graph.add_edges_from(
        [
            ("1", "2"),
            ("2", "3"),
            ("3", "4"),
            ("4", "5"),
            ("5", "6"),
            ("6", "1"),
            ("2", "5"),
            ("3", "6"),
        ]
    )

    _, route_map = generate_routing_matrix(graph, _Config(), RandomManager(7))

    for src in sorted(graph.nodes()):
        for dst in sorted(graph.nodes()):
            if src == dst:
                assert route_map[(src, dst)] == src
                continue
            path = _follow_route(route_map, src, dst)
            assert path[0] == src
            assert path[-1] == dst


def test_operational_graph_excludes_disabled_nodes_channels_and_nics() -> None:
    graph = nx.Graph()
    graph.add_edges_from([("0", "1"), ("1", "2"), ("0", "2")])
    nodes = [
        {"node_id": "N0001", "state": "normal"},
        {"node_id": "N0002", "state": "disabled"},
        {"node_id": "N0003", "state": "normal"},
    ]
    channels = [
        {"channel_id": "C0001", "src": "0", "dst": "1", "state": "normal"},
        {"channel_id": "C0002", "src": "0", "dst": "2", "state": "disabled"},
        {"channel_id": "C0003", "src": "1", "dst": "2", "state": "degraded"},
    ]
    nics = [
        {"channel_id": "C0001", "state": "normal"},
        {"channel_id": "C0002", "state": "normal"},
        {"channel_id": "C0003", "state": "disabled"},
    ]

    operational = build_operational_graph(graph, nodes, channels, nics)

    assert list(operational.edges()) == []
    assert sorted(operational.nodes()) == ["0", "1", "2"]


def test_routing_uses_an_available_alternate_path_after_channel_failure() -> None:
    graph = nx.Graph()
    graph.add_edges_from([("0", "1"), ("0", "2"), ("2", "1")])
    nodes = [
        {"node_id": "N0001", "state": "normal"},
        {"node_id": "N0002", "state": "normal"},
        {"node_id": "N0003", "state": "normal"},
    ]
    channels = [
        {"channel_id": "C0001", "src": "0", "dst": "1", "state": "disabled"},
        {"channel_id": "C0002", "src": "0", "dst": "2", "state": "normal"},
        {"channel_id": "C0003", "src": "1", "dst": "2", "state": "degraded"},
    ]

    operational = build_operational_graph(graph, nodes, channels, [])
    _, route_map = generate_routing_matrix(operational, _Config(), RandomManager(9))

    assert route_map[("0", "1")] == "2"
    assert route_map[("2", "1")] == "1"
