from pathlib import Path

from scene_generator.config import load_config
from scene_generator.topology.selector import collect_topologies


_BRITE_SAMPLE = """Nodes:
0 0 0
1 1 1
Edges:
0 0 1 0 0 100
"""


def test_topology_collection_includes_every_enabled_file_in_stable_order(tmp_path: Path) -> None:
    src_a = tmp_path / "src_a"
    src_b = tmp_path / "src_b"
    src_a.mkdir()
    src_b.mkdir()

    (src_a / "a.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")
    (src_a / "c.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")
    (src_b / "b.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
output_root: ./out
seed: 7
topology_sources:
  - name: a
    type: brite
    enabled: true
    root_dir: ./src_a
    glob_patterns: ["**/*.brite"]
  - name: b
    type: brite
    enabled: true
    root_dir: ./src_b
    glob_patterns: ["**/*.brite"]
""",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    first = collect_topologies(cfg)
    second = collect_topologies(cfg)

    assert first == second
    assert [selected.file_path.name for selected in first] == ["a.brite", "c.brite", "b.brite"]

