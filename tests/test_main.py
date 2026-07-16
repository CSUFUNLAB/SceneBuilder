from __future__ import annotations

from pathlib import Path
import shlex
from types import SimpleNamespace

import main as scene_builder_main


def _write_scene(root: Path, name: str) -> Path:
    scene = root / name
    scene.mkdir(parents=True)
    for file_name in (
        "nodes.csv",
        "nics.csv",
        "channels.csv",
        "routing_matrix.csv",
        "traffic.jsonl",
    ):
        (scene / file_name).write_text("", encoding="utf-8")
    return scene


def test_run_twins_writes_result_inside_each_scene(tmp_path: Path, monkeypatch) -> None:
    scenes_root = tmp_path / "generated_scenes"
    scene = _write_scene(scenes_root, "example_id0001_sample_t2s")
    twin_dir = scene / "twin"
    twin_dir.mkdir()
    (twin_dir / "1.jsonl").write_text("stale", encoding="utf-8")

    ns3_root = tmp_path / "ns-3.44"
    ns3_root.mkdir()
    (ns3_root / "ns3").write_text("", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run_command(
        command: list[str],
        dry_run: bool,
        scene_label: str | None = None,
        cwd: Path | None = None,
    ) -> int:
        commands.append(command)
        run_arguments = shlex.split(command[2])
        result_value = next(part.removeprefix("--result=") for part in run_arguments if part.startswith("--result="))
        result_file = Path(result_value)
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text("{}\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(scene_builder_main, "run_command", fake_run_command)

    result = scene_builder_main.run_twins(
        scenes_root,
        ns3_root=ns3_root,
        no_build=True,
    )

    expected = scene / "twin" / "0.jsonl"
    assert result.complete is True
    assert result.generated_files == (expected,)
    assert expected.is_file()
    assert not (twin_dir / "1.jsonl").exists()
    assert commands[0][0] == str(ns3_root / "ns3")
    assert f"--scene={scene}" in commands[0][2]
    assert f"--result={expected}" in commands[0][2]


def test_run_twins_dry_run_does_not_create_twin_directory(tmp_path: Path, monkeypatch) -> None:
    scenes_root = tmp_path / "generated_scenes"
    scene = _write_scene(scenes_root, "example_id0001_sample_t2s")
    ns3_root = tmp_path / "ns-3.44"
    ns3_root.mkdir()
    (ns3_root / "ns3").write_text("", encoding="utf-8")

    monkeypatch.setattr(scene_builder_main, "run_command", lambda *args, **kwargs: 0)

    result = scene_builder_main.run_twins(
        scenes_root,
        ns3_root=ns3_root,
        no_build=True,
        dry_run=True,
    )

    assert result.complete is True
    assert result.generated_files == (scene / "twin" / "0.jsonl",)
    assert not (scene / "twin").exists()


def test_all_runs_generation_twins_and_questions_in_order(tmp_path: Path, monkeypatch) -> None:
    scene_root = tmp_path / "generated_scenes"
    scene = scene_root / "example_id0001_sample_t2s"
    calls: list[tuple[str, object]] = []

    def fake_generate_scenes(config_path: str) -> list[Path]:
        calls.append(("generate", config_path))
        return [scene]

    def fake_run_twin_stage(args, received_root: Path, scenes=None):
        calls.append(("twins", (received_root, tuple(scenes or ()))))
        return scene_builder_main.TwinGenerationResult((scene / "twin" / "0.jsonl",), ())

    def fake_generate_questions(config_path: str, *, scenes_root: Path):
        calls.append(("questions", (config_path, scenes_root)))
        return scene_builder_main.QuestionGenerationResult(scene_count=1, categories=())

    monkeypatch.setattr(scene_builder_main, "generate_scenes", fake_generate_scenes)
    monkeypatch.setattr(
        scene_builder_main,
        "load_scene_config",
        lambda config_path: SimpleNamespace(output_root=scene_root),
    )
    monkeypatch.setattr(scene_builder_main, "_run_twin_stage", fake_run_twin_stage)
    monkeypatch.setattr(scene_builder_main, "generate_questions", fake_generate_questions)

    rc = scene_builder_main.main(
        ["all", "--config", "scene.yaml", "--question-config", "questions.yaml"]
    )

    assert rc == 0
    assert calls == [
        ("generate", "scene.yaml"),
        ("twins", (scene_root, (scene,))),
        ("questions", ("questions.yaml", scene_root)),
    ]
