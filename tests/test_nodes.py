import networkx as nx

from scene_generator.generators.nodes import generate_nodes
from scene_generator.rng import RandomManager


class _Config:
    nodes = {
        "type_candidates": ["edge"],
        "assignment_mode": "random",
        "default_node_type": "edge",
        "role_ratios": {"backbone": 0.2, "aggregation": 0.3, "edge": 0.5},
    }


def test_generate_nodes_contains_id_and_state() -> None:
    graph = nx.Graph()
    graph.add_nodes_from(["0", "1", "2"])

    rows, node_id_map = generate_nodes(graph, _Config(), RandomManager(1))

    assert node_id_map == {"0": 0, "1": 1, "2": 2}
    assert rows[0]["node_id"] == "N0001"
    assert "original_node_name" not in rows[0]
    assert "node_type" not in rows[0]
    assert rows[0]["state"] == "normal"
    assert rows[0]["latitude"] == ""
    assert rows[0]["longitude"] == ""

def test_generate_nodes_with_location_from_topology_attrs() -> None:
    graph = nx.Graph()
    graph.add_node("0", source_latitude=31.2304, source_longitude=121.4737)
    graph.add_node("1")

    rows, _ = generate_nodes(graph, _Config(), RandomManager(1))
    row1 = next(row for row in rows if row["node_id"] == "N0001")
    row2 = next(row for row in rows if row["node_id"] == "N0002")

    assert row1["latitude"] == 31.2304
    assert row1["longitude"] == 121.4737
    assert row2["latitude"] == ""
    assert row2["longitude"] == ""


def test_generate_nodes_does_not_export_original_name_from_topology_attrs() -> None:
    graph = nx.Graph()
    graph.add_node("0", source_original_node_name="Ruzomberok")

    rows, _ = generate_nodes(graph, _Config(), RandomManager(1))

    assert "original_node_name" not in rows[0]
