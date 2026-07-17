from __future__ import annotations

import json
from pathlib import Path
import random

import pytest

from question_generator.generators.analysis import AnalysisQuestionGenerator
from question_generator.models import QuestionTemplate
from question_generator.runner import _target_counts, clean_question_outputs, run
from question_generator.scene import SceneData
from question_generator.templates import load_templates


def _entity(
    entity_type: str,
    entity_id: str,
    label: str,
    *,
    properties: dict[str, object] | None = None,
    relations: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "label": label,
        "properties": properties or {},
        "relations": relations or {},
    }


def _write_scene(root: Path, scene_name: str, entities: list[dict[str, object]]) -> Path:
    scene_dir = root / scene_name
    scene_dir.mkdir(parents=True)
    path = scene_dir / "twin.jsonl"
    path.write_text(
        "".join(f"{json.dumps(entity, separators=(',', ':'))}\n" for entity in entities),
        encoding="utf-8",
    )
    return path


def _template(
    template_id: str,
    question: str,
    answers: tuple[str, ...],
    placeholders: tuple[str, ...],
) -> QuestionTemplate:
    return QuestionTemplate(
        template_id=template_id,
        category="analysis",
        question=question,
        answer_values=answers,
        placeholders=placeholders,
    )


def test_target_counts_balance_labels_and_exempt_id_answers() -> None:
    labeled = _template("analysis_001", "Question?", ("a", "b", "c"), ())
    id_answer = _template("analysis_002", "Question?", ("channel_id",), ())

    assert _target_counts(labeled, 6) == [("a", 2), ("b", 2), ("c", 2)]
    assert _target_counts(id_answer, 5) == [("channel_id", 5)]
    with pytest.raises(ValueError, match="must be divisible"):
        _target_counts(labeled, 5)


def test_all_analysis_templates_have_generation_logic() -> None:
    project_root = Path(__file__).resolve().parents[1]
    templates = load_templates(project_root / "question_generator/templates/analysis.txt", "analysis")
    generator = AnalysisQuestionGenerator()
    empty_scene = SceneData("empty", Path("empty/twin.jsonl"), [])

    assert len(templates) == 9
    for template in templates:
        for target_label in template.answer_values:
            generator.generate_candidate(empty_scene, template, target_label, random.Random(1))


def test_run_randomizes_scene_order_reproducibly_and_uses_entity_labels(tmp_path: Path) -> None:
    scenes_root = tmp_path / "scenes"
    _write_scene(scenes_root, "scene_001", [_entity("node", "N0001", "normal")])
    _write_scene(scenes_root, "scene_002", [_entity("node", "N0002", "disabled")])
    _write_scene(scenes_root, "scene_003", [_entity("node", "N0003", "normal")])
    for scene_name in ("scene_001", "scene_002", "scene_003"):
        scene_dir = scenes_root / scene_name
        (scene_dir / "analysis_questions.jsonl").write_text("stale\n", encoding="utf-8")
        (scene_dir / "evolution_questions.jsonl").write_text("stale\n", encoding="utf-8")
    scene_without_twin = scenes_root / "scene_004"
    scene_without_twin.mkdir()
    (scene_without_twin / "metadata.json").write_text("{}\n", encoding="utf-8")
    (scene_without_twin / "analysis_questions.jsonl").write_text("stale\n", encoding="utf-8")

    template_path = tmp_path / "analysis.txt"
    template_path.write_text(
        "What is the current status of node $node_id$ (normal or disabled)? ||| [normal, disabled]\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "seed: 7",
                "scenes_root: scenes",
                "categories:",
                "  analysis:",
                "    enabled: true",
                "    questions_per_question: 2",
                "    template_file: analysis.txt",
                "    output_file: questions.jsonl",
            ]
        ),
        encoding="utf-8",
    )

    result = run(config_path)

    assert result.complete is True
    output_text = (tmp_path / "questions.jsonl").read_text(encoding="utf-8")
    output_rows = [json.loads(line) for line in output_text.splitlines()]
    assert [(row["label"], row["scene_name"]) for row in output_rows] == [
        ("normal", "scene_003"),
        ("disabled", "scene_002"),
    ]
    assert output_rows[0]["question"] == "What is the current status of node N0003 (normal or disabled)?"
    assert "$node_id$" not in output_rows[1]["question"]

    run(config_path)
    assert (tmp_path / "questions.jsonl").read_text(encoding="utf-8") == output_text

    category_result = result.categories[0]
    assert category_result.scene_output_files == (
        scenes_root / "scene_001" / "analysis_questions.jsonl",
        scenes_root / "scene_002" / "analysis_questions.jsonl",
        scenes_root / "scene_003" / "analysis_questions.jsonl",
    )
    for scene_name in ("scene_001", "scene_002", "scene_003"):
        scene_file = scenes_root / scene_name / "analysis_questions.jsonl"
        scene_rows = [
            json.loads(line)
            for line in scene_file.read_text(encoding="utf-8").splitlines()
        ]
        expected_rows = [row for row in output_rows if row["scene_name"] == scene_name]
        assert scene_rows == expected_rows
        assert not (scenes_root / scene_name / "evolution_questions.jsonl").exists()

    assert (scenes_root / "scene_001" / "analysis_questions.jsonl").read_text(encoding="utf-8") == ""
    assert not (scene_without_twin / "analysis_questions.jsonl").exists()


def test_clean_question_outputs_removes_only_question_files(tmp_path: Path) -> None:
    scenes_root = tmp_path / "scenes"
    for scene_name in ("scene_001", "scene_002"):
        _write_scene(scenes_root, scene_name, [_entity("node", "N0001", "normal")])
        scene_dir = scenes_root / scene_name
        (scene_dir / "traffic.jsonl").write_text("keep\n", encoding="utf-8")
        for question_type in ("analysis", "evolution", "optimization"):
            (scene_dir / f"{question_type}_questions.jsonl").write_text(
                "remove\n",
                encoding="utf-8",
            )

    template_path = tmp_path / "analysis.txt"
    template_path.write_text("Question? ||| [normal]\n", encoding="utf-8")
    config_path = tmp_path / "questions.yaml"
    config_path.write_text(
        "\n".join(
            [
                "seed: 1",
                "scenes_root: scenes",
                "categories:",
                "  analysis:",
                "    enabled: true",
                "    questions_per_question: 1",
                "    template_file: analysis.txt",
                "    output_file: total_analysis.jsonl",
                "  evolution:",
                "    enabled: false",
                "    output_file: total_evolution.jsonl",
                "  optimization:",
                "    enabled: false",
                "    output_file: total_optimization.jsonl",
            ]
        ),
        encoding="utf-8",
    )
    for question_type in ("analysis", "evolution", "optimization"):
        (tmp_path / f"total_{question_type}.jsonl").write_text("remove\n", encoding="utf-8")

    result = clean_question_outputs(config_path)

    assert result.scene_count == 2
    assert len(result.removed_files) == 9
    for scene_name in ("scene_001", "scene_002"):
        scene_dir = scenes_root / scene_name
        assert (scene_dir / "twin.jsonl").is_file()
        assert (scene_dir / "traffic.jsonl").read_text(encoding="utf-8") == "keep\n"
        for question_type in ("analysis", "evolution", "optimization"):
            assert not (scene_dir / f"{question_type}_questions.jsonl").exists()
    for question_type in ("analysis", "evolution", "optimization"):
        assert not (tmp_path / f"total_{question_type}.jsonl").exists()


def test_analysis_compound_questions_use_labels_properties_and_relations(tmp_path: Path) -> None:
    entities = [
        _entity("node", "N0001", "normal"),
        _entity("node", "N0002", "normal"),
        _entity("node", "N0003", "disabled"),
        _entity("node", "N0004", "normal"),
        _entity("node", "N0005", "normal"),
        _entity("node", "N0006", "normal"),
    ]

    channel_specs = [
        ("C0001", "saturated", "N0001", "N0002", 100.0, 5.0),
        ("C0002", "degraded", "N0002", "N0004", 100.0, 50.0),
        ("C0003", "disabled", "N0003", "N0004", 100.0, 0.0),
        ("C0004", "disabled", "N0004", "N0005", 100.0, 0.0),
        ("C0005", "saturated", "N0005", "N0006", 100.0, 5.0),
    ]
    for index, (channel_id, label, left_node, right_node, capacity, available) in enumerate(channel_specs, start=1):
        left_nic = f"{left_node}:IF{index:06d}L"
        right_nic = f"{right_node}:IF{index:06d}R"
        entities.extend(
            [
                _entity("nic", left_nic, "normal", relations={"node": left_node, "channel": channel_id}),
                _entity("nic", right_nic, "normal", relations={"node": right_node, "channel": channel_id}),
                _entity(
                    "channel",
                    channel_id,
                    label,
                    properties={"capacity_mbps": capacity, "available_bandwidth_mbps": available},
                    relations={"connects": [left_nic, right_nic]},
                ),
            ]
        )

    entities.extend(
        [
            _entity(
                "data_flow",
                "F000001",
                "degraded",
                properties={"demand_mbps": 60.0},
                relations={"path_nodes": ["N0001", "N0002"]},
            ),
            _entity(
                "data_flow",
                "F000002",
                "normal",
                properties={"demand_mbps": 40.0},
                relations={"path_nodes": ["N0001", "N0002"]},
            ),
            _entity(
                "data_flow",
                "F000003",
                "degraded",
                properties={"demand_mbps": 20.0},
                relations={"path_nodes": ["N0002", "N0004"]},
            ),
            _entity(
                "data_flow",
                "F000004",
                "normal",
                properties={"demand_mbps": 100.0},
                relations={"path_nodes": ["N0005", "N0006"]},
            ),
        ]
    )
    scene = SceneData.from_jsonl(_write_scene(tmp_path, "scene_001", entities))
    generator = AnalysisQuestionGenerator()

    network_template = _template(
        "analysis_001",
        "Network?",
        ("normal", "congested", "faulty"),
        (),
    )
    degradation_template = _template(
        "analysis_006",
        "Cause for $data_flow_id$?",
        ("channel_saturation", "channel_degradation"),
        ("data_flow_id",),
    )
    unavailable_template = _template(
        "analysis_008",
        "Cause for $channel_id$?",
        ("channel_fault", "endpoint_node_fault"),
        ("channel_id",),
    )
    saturation_template = _template(
        "analysis_009",
        "Cause for $channel_id$?",
        ("single_large_flow", "multiple_flow_aggregation"),
        ("channel_id",),
    )

    assert generator.generate_candidate(scene, network_template, "faulty", random.Random(1)) is not None
    saturation_flow = generator.generate_candidate(
        scene,
        degradation_template,
        "channel_saturation",
        random.Random(1),
    )
    degraded_flow = generator.generate_candidate(
        scene,
        degradation_template,
        "channel_degradation",
        random.Random(1),
    )
    assert saturation_flow is not None and saturation_flow.replacements == {"data_flow_id": "F000001"}
    assert degraded_flow is not None and degraded_flow.replacements == {"data_flow_id": "F000003"}

    own_fault = generator.generate_candidate(scene, unavailable_template, "channel_fault", random.Random(1))
    endpoint_fault = generator.generate_candidate(
        scene,
        unavailable_template,
        "endpoint_node_fault",
        random.Random(1),
    )
    assert own_fault is not None and own_fault.replacements == {"channel_id": "C0004"}
    assert endpoint_fault is not None and endpoint_fault.replacements == {"channel_id": "C0003"}

    single = generator.generate_candidate(scene, saturation_template, "single_large_flow", random.Random(1))
    aggregate = generator.generate_candidate(
        scene,
        saturation_template,
        "multiple_flow_aggregation",
        random.Random(1),
    )
    assert single is not None and single.replacements == {"channel_id": "C0005"}
    assert aggregate is not None and aggregate.replacements == {"channel_id": "C0001"}


def test_bottleneck_id_answer_is_selected_from_valid_candidates(tmp_path: Path) -> None:
    entities = [
        _entity("node", "N0001", "normal"),
        _entity("node", "N0002", "normal"),
        _entity("node", "N0003", "normal"),
        _entity("nic", "N0001:IF000001", "normal", relations={"node": "N0001", "channel": "C0001"}),
        _entity("nic", "N0002:IF000001", "normal", relations={"node": "N0002", "channel": "C0001"}),
        _entity("nic", "N0002:IF000002", "normal", relations={"node": "N0002", "channel": "C0002"}),
        _entity("nic", "N0003:IF000001", "normal", relations={"node": "N0003", "channel": "C0002"}),
        _entity(
            "channel",
            "C0001",
            "normal",
            properties={"available_bandwidth_mbps": 10.0},
            relations={"connects": ["N0001:IF000001", "N0002:IF000001"]},
        ),
        _entity(
            "channel",
            "C0002",
            "normal",
            properties={"available_bandwidth_mbps": 20.0},
            relations={"connects": ["N0002:IF000002", "N0003:IF000001"]},
        ),
        _entity("data_flow", "F000001", "normal", relations={"path_nodes": ["N0001", "N0002"]}),
        _entity("data_flow", "F000002", "normal", relations={"path_nodes": ["N0002", "N0003"]}),
    ]
    scene = SceneData.from_jsonl(_write_scene(tmp_path, "scene_001", entities))
    template = _template(
        "analysis_007",
        "Which channel is the bottleneck for $data_flow_id$?",
        ("channel_id",),
        ("data_flow_id",),
    )
    generator = AnalysisQuestionGenerator()

    first = generator.generate_candidate(scene, template, "channel_id", random.Random(9))
    second = generator.generate_candidate(scene, template, "channel_id", random.Random(9))

    assert first == second
    assert first is not None
    assert first.label in {"C0001", "C0002"}
    assert first.replacements["data_flow_id"] in {"F000001", "F000002"}
