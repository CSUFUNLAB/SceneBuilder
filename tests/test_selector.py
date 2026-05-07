from pathlib import Path

from network_scene_generator.config import load_config
from network_scene_generator.rng import RandomManager
from network_scene_generator.topology.selector import select_topology_file


_BRITE_SAMPLE = """Nodes:
0 0 0
1 1 1
Edges:
0 0 1 0 0 100
"""


def test_topology_selection_is_reproducible(tmp_path: Path) -> None:
    src_a = tmp_path / "src_a"
    src_b = tmp_path / "src_b"
    src_a.mkdir()
    src_b.mkdir()

    (src_a / "a.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")
    (src_b / "b.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
output_root: ./out
seed: 7
topology_sources:
  - name: a
    type: brite
    weight: 1.0
    root_dir: ./src_a
    glob_patterns: ["**/*.brite"]
  - name: b
    type: brite
    weight: 1.0
    root_dir: ./src_b
    glob_patterns: ["**/*.brite"]
""",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    first = select_topology_file(cfg, RandomManager(7))
    second = select_topology_file(cfg, RandomManager(7))

    assert first.file_path == second.file_path

