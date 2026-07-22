from __future__ import annotations

from pathlib import Path
import shlex
from types import SimpleNamespace

import main as scene_builder_main
import pytest


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


@pytest.mark.parametrize("argv", [[], ["unknown"]])
def test_cli_reports_available_modes_for_missing_or_invalid_command(argv, capsys) -> None:
    with pytest.raises(SystemExit, match="2"):
        scene_builder_main.main(argv)

    error = capsys.readouterr().err
    assert "可用模式: generate, twins, questions, clean" in error
    assert "python main.py --help" in error


def test_run_twins_writes_result_inside_each_scene(tmp_path: Path, monkeypatch) -> None:
    scenes_root = tmp_path / "generated_scenes"
    scene = _write_scene(scenes_root, "example_id0001_sample_t2s")
    (scene / "twin.jsonl").write_text("old\n", encoding="utf-8")
    twin_dir = scene / "twin"
    twin_dir.mkdir()
    (twin_dir / "0.jsonl").write_text("legacy", encoding="utf-8")
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
        scene_builder_main.label_path_for_twin(result_file).write_text(
            '{"label_type":"node_state","label":[]}\n'
            '{"label_type":"nic_state","label":[]}\n'
            '{"label_type":"channel_state","label":[]}\n'
            '{"label_type":"data_flow_state","label":[]}\n'
            '{"label_type":"network_state","label":"normal"}\n'
            '{"label_type":"data_flow_bandwidth_constraint","label":[]}\n'
            '{"label_type":"data_flow_failure_cause","label":[]}\n'
            '{"label_type":"data_flow_failure_type","label":[]}\n',
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(scene_builder_main, "run_command", fake_run_command)

    result = scene_builder_main.run_twins(
        scenes_root,
        ns3_root=ns3_root,
        no_build=True,
    )

    expected = scene / "twin.jsonl"
    expected_labels = scene / "labels.jsonl"
    assert result.complete is True
    assert result.generated_files == (expected,)
    assert expected.read_text(encoding="utf-8") == "{}\n"
    assert expected_labels.is_file()
    assert not twin_dir.exists()
    assert commands[0][0] == str(ns3_root / "ns3")
    assert f"--scene={scene}" in commands[0][2]
    assert f"--result={expected}" in commands[0][2]


def test_run_twins_dry_run_does_not_delete_or_create_twin_file(tmp_path: Path, monkeypatch) -> None:
    scenes_root = tmp_path / "generated_scenes"
    scene = _write_scene(scenes_root, "example_id0001_sample_t2s")
    existing_twin = scene / "twin.jsonl"
    existing_twin.write_text("keep\n", encoding="utf-8")
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
    assert result.generated_files == (existing_twin,)
    assert existing_twin.read_text(encoding="utf-8") == "keep\n"


def test_run_twins_cleans_all_selected_scenes_before_build(tmp_path: Path, monkeypatch) -> None:
    scenes_root = tmp_path / "generated_scenes"
    scenes = [
        _write_scene(scenes_root, "example_id0001_sample_t2s"),
        _write_scene(scenes_root, "example_id0002_sample_t2s"),
    ]
    for scene in scenes:
        (scene / "twin.jsonl").write_text("old\n", encoding="utf-8")

    ns3_root = tmp_path / "ns-3.44"
    ns3_root.mkdir()
    (ns3_root / "ns3").write_text("", encoding="utf-8")
    monkeypatch.setattr(scene_builder_main, "run_command", lambda *args, **kwargs: 1)

    result = scene_builder_main.run_twins(scenes_root, ns3_root=ns3_root)

    assert result.complete is False
    assert result.failures == (("build", 1),)
    assert all(not (scene / "twin.jsonl").exists() for scene in scenes)


@pytest.mark.parametrize(
    ("scene_argument", "expected_suffix"),
    [
        (None, "."),
        ("example_id0001_sample_t2s", "example_id0001_sample_t2s"),
        ("generated_scenes/example_id0001_sample_t2s", "example_id0001_sample_t2s"),
    ],
)
def test_twins_resolves_all_or_one_scene_relative_to_generated_scenes(
    scene_argument: str | None,
    expected_suffix: str,
) -> None:
    resolved = scene_builder_main._resolve_twin_scene_path(scene_argument)
    expected = scene_builder_main.DEFAULT_SCENE_ROOT.resolve()
    if expected_suffix != ".":
        expected /= expected_suffix
    assert resolved == expected


@pytest.mark.parametrize("scene_argument", ["/tmp/outside", "../outside"])
def test_twins_rejects_paths_outside_generated_scenes(scene_argument: str) -> None:
    with pytest.raises(ValueError, match="generated_scenes"):
        scene_builder_main._resolve_twin_scene_path(scene_argument)


def test_twins_does_not_accept_scene_generation_config(capsys) -> None:
    with pytest.raises(SystemExit, match="2"):
        scene_builder_main.main(["twins", "-c", "configs/example.yaml"])

    error = capsys.readouterr().err
    assert "unrecognized arguments: -c" in error


def test_questions_uses_configured_scene_root_when_no_override_is_given(monkeypatch, capsys) -> None:
    received: list[tuple[str, Path | None, str]] = []

    def fake_generate_questions(
        config_path: str,
        *,
        scenes_root: Path | None,
        question_type: str,
    ):
        received.append((config_path, scenes_root, question_type))
        return scene_builder_main.QuestionGenerationResult(scene_count=0, categories=())

    monkeypatch.setattr(scene_builder_main, "generate_questions", fake_generate_questions)

    rc = scene_builder_main.main(
        ["questions", "-t", "analysis", "--config", "questions.yaml"]
    )

    assert rc == 0
    assert received == [("questions.yaml", None, "analysis")]
    assert "Question type: analysis" in capsys.readouterr().out


def test_questions_clean_option_cleans_and_does_not_generate(monkeypatch, capsys) -> None:
    cleaned: list[tuple[str, Path | None]] = []

    def fake_clean(config_path: str, *, scenes_root: Path | None):
        cleaned.append((config_path, scenes_root))
        return SimpleNamespace(removed_files=(Path("a"), Path("b")), scene_count=3)

    monkeypatch.setattr(scene_builder_main, "clean_question_outputs", fake_clean)
    monkeypatch.setattr(
        scene_builder_main,
        "generate_questions",
        lambda *args, **kwargs: pytest.fail("question generation must not run during --clean"),
    )

    rc = scene_builder_main.main(["questions", "--clean", "-c", "questions.yaml"])

    assert rc == 0
    assert cleaned == [("questions.yaml", None)]
    assert "Removed 2 question file(s) from 3 scene(s)" in capsys.readouterr().out
