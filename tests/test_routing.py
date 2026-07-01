import networkx as nx

from scene_generator.generators.routing import generate_routing_matrix
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
