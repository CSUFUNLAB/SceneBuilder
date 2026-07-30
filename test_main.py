from pathlib import Path

import main as scene_builder
import pytest
from question_generator import evidence
from question_generator.scene import EntityRecord, SceneData
from question_generator.templates import load_template_bundle


def _make_scene(path: Path) -> None:
    path.mkdir(parents=True)
    for name in (
        "metadata.json",
        "nodes.csv",
        "nics.csv",
        "routing_matrix.csv",
        "traffic.jsonl",
        "channels.csv",
    ):
        (path / name).write_text("", encoding="utf-8")


def test_clear_dependent_evolution_outputs(tmp_path: Path) -> None:
    origin_root = tmp_path / "generated_scenes" / "origin" / "scenes"
    origin_scene = origin_root / "origin-scene"
    _make_scene(origin_scene)

    evolution_group = tmp_path / "generated_scenes" / "evo"
    evolved_scene = evolution_group / "scenes" / "evolved-scene"
    _make_scene(evolved_scene)
    partial_entry = evolution_group / "scenes" / ".partial"
    partial_entry.write_text("partial", encoding="utf-8")
    output_file = evolution_group / "evolution_questions.jsonl"
    output_file.write_text("{}\n", encoding="utf-8")
    exported_template = evolution_group / "question_template.yaml"
    exported_template.write_text("templates: []\n", encoding="utf-8")
    unrelated_file = evolution_group / "keep.txt"
    unrelated_file.write_text("keep", encoding="utf-8")

    removed_scenes, removed_artifacts = (
        scene_builder._clear_dependent_evolution_outputs(
            origin_root,
            output_file,
        )
    )

    assert removed_scenes == (partial_entry, evolved_scene)
    assert removed_artifacts == (output_file, exported_template)
    assert list((evolution_group / "scenes").iterdir()) == []
    assert unrelated_file.read_text(encoding="utf-8") == "keep"
    assert origin_scene.is_dir()


def test_clear_question_outputs_removes_global_and_local_artifacts(
    tmp_path: Path,
) -> None:
    scenes_root = tmp_path / "generated_scenes" / "origin" / "scenes"
    first_scene = scenes_root / "first"
    second_scene = scenes_root / "second"
    _make_scene(first_scene)
    _make_scene(second_scene)
    local_output = first_scene / "analysis_questions.jsonl"
    local_output.write_text("{}\n", encoding="utf-8")
    unrelated_output = second_scene / "evolution_questions.jsonl"
    unrelated_output.write_text("{}\n", encoding="utf-8")
    output_file = scenes_root.parent / "analysis_questions.jsonl"
    output_file.write_text("{}\n", encoding="utf-8")
    exported_template = scenes_root.parent / "question_template.yaml"
    exported_template.write_text("templates: []\n", encoding="utf-8")

    removed = scene_builder._clear_question_outputs(
        scenes_root,
        output_file,
        "analysis",
    )

    assert removed == (local_output, output_file, exported_template)
    assert unrelated_output.is_file()


def test_run_twins_invalidates_dependents_before_origin_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    scene = tmp_path / "origin" / "scenes" / "origin-scene"
    _make_scene(scene)
    twin_file = scene / scene_builder.TWIN_FILE_NAME
    label_file = scene / scene_builder.LABEL_FILE_NAME
    twin_file.write_text("{}\n", encoding="utf-8")
    label_file.write_text("{}\n", encoding="utf-8")

    ns3_root = tmp_path / "ns3"
    ns3_root.mkdir()
    (ns3_root / "ns3").write_text("", encoding="utf-8")
    callback_calls: list[str] = []

    def invalidate() -> None:
        assert twin_file.is_file()
        assert label_file.is_file()
        callback_calls.append("invalidated")

    monkeypatch.setattr(scene_builder, "run_command", lambda *args, **kwargs: 1)

    result = scene_builder.run_twins(
        scene.parent,
        scenes=[scene],
        ns3_root=ns3_root,
        no_build=True,
        before_output_reset=invalidate,
    )

    assert callback_calls == ["invalidated"]
    assert not twin_file.exists()
    assert not label_file.exists()
    assert not result.complete


def test_run_twins_dry_run_does_not_invalidate_dependents(
    tmp_path: Path,
) -> None:
    scene = tmp_path / "origin" / "scenes" / "origin-scene"
    _make_scene(scene)
    ns3_root = tmp_path / "ns3"
    ns3_root.mkdir()
    (ns3_root / "ns3").write_text("", encoding="utf-8")
    callback_calls: list[str] = []

    result = scene_builder.run_twins(
        scene.parent,
        scenes=[scene],
        ns3_root=ns3_root,
        no_build=True,
        dry_run=True,
        before_output_reset=lambda: callback_calls.append("invalidated"),
    )

    assert result.complete
    assert callback_calls == []


def test_require_completed_twins_allows_partial_origin_dataset(
    tmp_path: Path,
    capsys,
) -> None:
    dataset_root = tmp_path / "generated_scenes"
    complete_scene = dataset_root / "origin" / "scenes" / "complete"
    incomplete_scene = dataset_root / "origin" / "scenes" / "incomplete"
    _make_scene(complete_scene)
    _make_scene(incomplete_scene)
    (complete_scene / scene_builder.TWIN_FILE_NAME).write_text(
        "{}\n",
        encoding="utf-8",
    )
    (complete_scene / scene_builder.LABEL_FILE_NAME).write_text(
        "{}\n",
        encoding="utf-8",
    )

    scenes_root, completed = scene_builder._require_completed_twins(
        dataset_root,
        "origin",
        allow_partial=True,
    )

    assert scenes_root == dataset_root / "origin" / "scenes"
    assert completed == [complete_scene]
    assert (
        "Using 1 completed origin scene(s); skipping 1 incomplete scene(s)."
        in capsys.readouterr().out
    )


def test_require_completed_twins_remains_strict_by_default(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "generated_scenes"
    complete_scene = dataset_root / "evo" / "scenes" / "complete"
    incomplete_scene = dataset_root / "evo" / "scenes" / "incomplete"
    _make_scene(complete_scene)
    _make_scene(incomplete_scene)
    (complete_scene / scene_builder.TWIN_FILE_NAME).write_text(
        "{}\n",
        encoding="utf-8",
    )
    (complete_scene / scene_builder.LABEL_FILE_NAME).write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"1 evo scene\(s\) do not have complete Twin outputs",
    ):
        scene_builder._require_completed_twins(dataset_root, "evo")


def test_node_state_templates_only_allow_normal_and_disabled() -> None:
    project_root = Path(__file__).resolve().parent
    analysis = load_template_bundle(
        project_root / "question_generator" / "templates" / "analysis.yaml",
        "analysis",
    )
    evolution = load_template_bundle(
        project_root / "question_generator" / "templates" / "evolution.yaml",
        "evolution",
    )

    analysis_by_id = {
        template.template_id: template for template in analysis.templates
    }
    evolution_by_id = {
        template.template_id: template for template in evolution.templates
    }

    assert analysis_by_id["TA0001"].answer_values == (
        "normal",
        "disabled",
    )
    assert evolution_by_id["TE0017"].answer_values == (
        "unchanged",
        "disabled",
    )


def test_unusable_route_does_not_change_node_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    node = EntityRecord(
        entity_type="node",
        entity_id="N0001",
        label="",
        properties={"rx_packets": 0, "tx_packets": 0},
        relations={
            "interfaces": ["N0001:IF000001"],
            "routes": [
                {
                    "destination_nodes": ["N0002"],
                    "egress_interface": "N0001:IF000001",
                    "next_hop": "N0002",
                }
            ],
        },
    )
    scene = SceneData(
        "scene",
        tmp_path / "twin.jsonl",
        [
            node,
            EntityRecord(
                entity_type="nic",
                entity_id="N0001:IF000001",
                label="",
                properties={},
                relations={"node": "N0001", "channel": "C0001"},
            ),
            EntityRecord(
                entity_type="channel",
                entity_id="C0001",
                label="",
                properties={},
                relations={"connects": ["N0001:IF000001"]},
            ),
        ],
    )
    monkeypatch.setattr(
        evidence,
        "_infer_unique_physical_fault",
        lambda _: "C0001",
    )

    assert evidence.infer_node_state(scene, node) == "normal"
