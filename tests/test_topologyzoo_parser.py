from pathlib import Path

from scene_generator.topology.topologyzoo_parser import parse_topologyzoo


def test_parse_topologyzoo_accepts_duplicate_labels_by_using_gml_ids(tmp_path: Path) -> None:
    path = tmp_path / "dup_labels.gml"
    path.write_text(
        """
graph [
  node [
    id 0
    label "None"
    Latitude 49.08333
    Longitude 19.31667
  ]
  node [
    id 1
    label "None"
  ]
  edge [
    source 0
    target 1
  ]
]
""".strip(),
        encoding="utf-8",
    )

    graph = parse_topologyzoo(path)

    assert sorted(graph.nodes()) == ["0", "1"]
    assert graph.nodes["0"]["source_original_node_name"] == "None"
    assert graph.nodes["1"]["source_original_node_name"] == "None"
    assert graph.nodes["0"]["source_latitude"] == 49.08333
    assert graph.nodes["0"]["source_longitude"] == 19.31667


def test_parse_topologyzoo_accepts_duplicate_edges_by_treating_input_as_multigraph(tmp_path: Path) -> None:
    path = tmp_path / "dup_edges.gml"
    path.write_text(
        """
graph [
  node [ id 0 label "A" ]
  node [ id 1 label "B" ]
  edge [ source 0 target 1 ]
  edge [ source 0 target 1 ]
]
""".strip(),
        encoding="utf-8",
    )

    graph = parse_topologyzoo(path)

    assert sorted(graph.nodes()) == ["0", "1"]
    assert graph.number_of_edges() == 1
