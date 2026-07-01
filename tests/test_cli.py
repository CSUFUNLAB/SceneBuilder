from pathlib import Path

from scene_generator.cli import main


_BRITE_SAMPLE = """Nodes:
0 0 0
1 1 1
Edges:
0 0 1 0 0 100
"""


def test_cli_default_command_generates_scenes(tmp_path: Path, capsys) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 7
num_scenes: 1
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

    rc = main(["-c", str(cfg)])

    assert rc == 0
    output = capsys.readouterr().out.strip().splitlines()
    assert len(output) == 1
    assert output[0].endswith("config_id0001_sample_t60s")


def test_cli_clean_removes_only_generated_scene_directories(tmp_path: Path, capsys) -> None:
    topo_dir = tmp_path / "topologies"
    topo_dir.mkdir(parents=True)
    (topo_dir / "sample.brite").write_text(_BRITE_SAMPLE, encoding="utf-8")

    output_root = tmp_path / "out"
    scene_a = output_root / "config_id0001_sample_t60s"
    scene_b = output_root / "config_id0002_sample_t60s"
    keep_dir = output_root / "keep_me"
    output_root.mkdir(parents=True)
    keep_dir.mkdir()
    (output_root / "notes.txt").write_text("keep", encoding="utf-8")

    for scene_dir in (scene_a, scene_b):
        scene_dir.mkdir()
        (scene_dir / "metadata.json").write_text("{}", encoding="utf-8")
        (scene_dir / "links.csv").write_text("link_id,src,dst\n", encoding="utf-8")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
output_root: ./out
seed: 7
num_scenes: 1
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

    rc = main(["clean", "-c", str(cfg)])

    assert rc == 0
    assert not scene_a.exists()
    assert not scene_b.exists()
    assert keep_dir.exists()
    assert (output_root / "notes.txt").exists()

    output = capsys.readouterr().out
    assert "Removed 2 scene directories" in output
