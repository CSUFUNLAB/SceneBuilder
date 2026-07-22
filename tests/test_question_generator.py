from __future__ import annotations

import json
from pathlib import Path
import random

import pytest

from question_generator.generators.analysis import AnalysisQuestionGenerator
from question_generator.models import QuestionTemplate
from question_generator.runner import _target_counts, clean_question_outputs, run
from question_generator.scene import EntityRecord, SceneData
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


def _channel_properties(state: str, capacity: float = 100.0) -> dict[str, float]:
    effective = capacity * 0.5 if state == "degraded" else capacity
    utilization = 0.95 if state == "saturated" else 0.0
    available = 0.0 if state == "disabled" else effective * (1.0 - utilization)
    return {
        "capacity_mbps": capacity,
        "effective_capacity_mbps": effective,
        "available_bandwidth_mbps": available,
        "utilization": utilization,
    }


def _disabled_transit_node_entities(node_id: str, flow_id: str) -> list[dict[str, object]]:
    left_node = f"{node_id}L"
    right_node = f"{node_id}R"
    left_channel = f"C{flow_id}L"
    right_channel = f"C{flow_id}R"
    left_nics = (f"{left_node}:IF1", f"{node_id}:IF1")
    right_nics = (f"{node_id}:IF2", f"{right_node}:IF1")
    return [
        _entity("node", left_node, "normal", properties={"rx_packets": 0, "tx_packets": 0}),
        _entity("node", node_id, "disabled", properties={"rx_packets": 0, "tx_packets": 0}),
        _entity("node", right_node, "normal", properties={"rx_packets": 0, "tx_packets": 0}),
        _entity("nic", left_nics[0], "disabled", relations={"node": left_node, "channel": left_channel}),
        _entity("nic", left_nics[1], "disabled", relations={"node": node_id, "channel": left_channel}),
        _entity("nic", right_nics[0], "disabled", relations={"node": node_id, "channel": right_channel}),
        _entity("nic", right_nics[1], "disabled", relations={"node": right_node, "channel": right_channel}),
        _entity(
            "channel",
            left_channel,
            "disabled",
            properties=_channel_properties("disabled"),
            relations={"connects": list(left_nics)},
        ),
        _entity(
            "channel",
            right_channel,
            "disabled",
            properties=_channel_properties("disabled"),
            relations={"connects": list(right_nics)},
        ),
        _entity(
            "data_flow",
            flow_id,
            "failed",
            properties={
                "demand_mbps": 10.0,
                "tx_packets": 1,
                "rx_packets": 0,
                "lost_packets": 1,
                "throughput_mbps": 0.0,
            },
            relations={
                "path_nodes": [left_node, node_id, right_node],
                "path_channels": [left_channel, right_channel],
            },
        ),
    ]


def _routing_failed_source_entities(node_id: str, flow_id: str) -> list[dict[str, object]]:
    peer_node = f"{node_id}P"
    channel_id = f"C{flow_id}"
    source_nic = f"{node_id}:IF1"
    peer_nic = f"{peer_node}:IF1"
    return [
        _entity(
            "node",
            node_id,
            "routing_failed",
            properties={"rx_packets": 0, "tx_packets": 0},
            relations={"routes": []},
        ),
        _entity(
            "node",
            peer_node,
            "unlabeled",
            properties={"rx_packets": 0, "tx_packets": 0},
            relations={
                "routes": [
                    {
                        "destination_nodes": [node_id],
                        "egress_interface": peer_nic,
                        "next_hop": node_id,
                    }
                ]
            },
        ),
        _entity("nic", source_nic, "normal", relations={"node": node_id, "channel": channel_id}),
        _entity("nic", peer_nic, "normal", relations={"node": peer_node, "channel": channel_id}),
        _entity(
            "channel",
            channel_id,
            "normal",
            properties=_channel_properties("normal"),
            relations={"connects": [source_nic, peer_nic]},
        ),
        _entity(
            "data_flow",
            flow_id,
            "failed",
            properties={
                "demand_mbps": 10.0,
                "tx_packets": 0,
                "rx_packets": 0,
                "lost_packets": 0,
                "throughput_mbps": 0.0,
            },
            relations={
                "source_node": node_id,
                "destination_node": peer_node,
                "path_nodes": [node_id],
                "path_channels": [],
            },
        ),
    ]


def _channel_failure_entities(flow_id: str) -> list[dict[str, object]]:
    return [
        _entity("node", "N0001", "normal", properties={"rx_packets": 0, "tx_packets": 1}),
        _entity("node", "N0002", "normal", properties={"rx_packets": 0, "tx_packets": 0}),
        _entity("node", "N0003", "normal", properties={"rx_packets": 1, "tx_packets": 1}),
        _entity("node", "N0004", "normal", properties={"rx_packets": 1, "tx_packets": 1}),
        _entity("nic", "N0001:IF1", "disabled", relations={"node": "N0001", "channel": "C0001"}),
        _entity("nic", "N0002:IF1", "disabled", relations={"node": "N0002", "channel": "C0001"}),
        _entity("nic", "N0001:IF2", "normal", relations={"node": "N0001", "channel": "C0002"}),
        _entity("nic", "N0003:IF1", "normal", relations={"node": "N0003", "channel": "C0002"}),
        _entity("nic", "N0002:IF2", "normal", relations={"node": "N0002", "channel": "C0003"}),
        _entity("nic", "N0004:IF1", "normal", relations={"node": "N0004", "channel": "C0003"}),
        _entity(
            "channel",
            "C0001",
            "disabled",
            properties=_channel_properties("disabled"),
            relations={"connects": ["N0001:IF1", "N0002:IF1"]},
        ),
        _entity(
            "channel",
            "C0002",
            "normal",
            properties=_channel_properties("normal"),
            relations={"connects": ["N0001:IF2", "N0003:IF1"]},
        ),
        _entity(
            "channel",
            "C0003",
            "normal",
            properties=_channel_properties("normal"),
            relations={"connects": ["N0002:IF2", "N0004:IF1"]},
        ),
        _entity(
            "data_flow",
            flow_id,
            "failed",
            properties={
                "demand_mbps": 10.0,
                "tx_packets": 1,
                "rx_packets": 0,
                "lost_packets": 1,
                "throughput_mbps": 0.0,
            },
            relations={
                "source_node": "N0001",
                "destination_node": "N0002",
                "path_nodes": ["N0001"],
                "path_channels": [],
            },
        ),
    ]


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

    assert len(templates) == 10
    for template in templates:
        for target_label in template.answer_values:
            generator.generate_candidate(empty_scene, template, target_label, random.Random(1))


def test_run_randomizes_scene_order_reproducibly_and_uses_entity_labels(tmp_path: Path) -> None:
    scenes_root = tmp_path / "scenes"
    _write_scene(
        scenes_root,
        "scene_001",
        [
            _entity("node", "N0001", "normal", properties={"rx_packets": 1, "tx_packets": 1}),
            _entity("data_flow", "F000001", "normal", relations={"path_nodes": ["N0001"]}),
        ],
    )
    _write_scene(
        scenes_root,
        "scene_002",
        _disabled_transit_node_entities("N0002", "F000002"),
    )
    _write_scene(
        scenes_root,
        "scene_003",
        _routing_failed_source_entities("N0003", "F000003"),
    )
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
        "What is the current status of node $node_id$ (normal, disabled, or routing_failed)? "
        "||| [normal, disabled, routing_failed]\n",
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
                "    questions_per_question: 3",
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
        ("normal", "scene_001"),
        ("disabled", "scene_002"),
        ("routing_failed", "scene_003"),
    ]
    assert output_rows[0]["question"] == (
        "What is the current status of node N0001 (normal, disabled, or routing_failed)?"
    )
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

    assert not (scene_without_twin / "analysis_questions.jsonl").exists()


def test_entity_status_candidates_are_limited_to_data_flow_scope() -> None:
    scene = SceneData(
        "scope",
        Path("scope/twin.jsonl"),
        [
            EntityRecord("node", "N0001", "normal", {"rx_packets": 1, "tx_packets": 1}, {}),
            EntityRecord("node", "N9999", "disabled", {}, {}),
            EntityRecord(
                "nic",
                "N0001:IF000001",
                "normal",
                {
                    "queue_size_packets": 100,
                    "queue_current_packets": 0,
                },
                {"node": "N0001", "channel": "C0001"},
            ),
            EntityRecord(
                "nic",
                "N0002:IF000001",
                "normal",
                {
                    "queue_size_packets": 100,
                    "queue_current_packets": 0,
                },
                {"node": "N0002", "channel": "C0001"},
            ),
            EntityRecord("nic", "N9999:IF000001", "disabled", {}, {"node": "N9999"}),
            EntityRecord(
                "channel",
                "C0001",
                "normal",
                _channel_properties("normal"),
                {"connects": ["N0001:IF000001", "N0002:IF000001"]},
            ),
            EntityRecord("channel", "C9999", "disabled", {}, {"connects": []}),
            EntityRecord(
                "data_flow",
                "F000001",
                "normal",
                {},
                {"path_nodes": ["N0001", "N0002"], "path_channels": ["C0001"]},
            ),
        ],
    )
    generator = AnalysisQuestionGenerator()

    for template, missing_label in (
        (
            _template(
                "node",
                "$node_id$",
                ("normal", "disabled", "routing_failed"),
                ("node_id",),
            ),
            "disabled",
        ),
        (
            _template(
                "channel",
                "$channel_id$",
                ("normal", "disabled", "degraded", "saturated"),
                ("channel_id",),
            ),
            "disabled",
        ),
        (
            _template(
                "nic",
                "$nic_id$",
                ("normal", "disabled", "saturated"),
                ("nic_id",),
            ),
            "disabled",
        ),
    ):
        assert generator.generate_candidate(scene, template, "normal", random.Random(1)) is not None
        assert generator.generate_candidate(scene, template, missing_label, random.Random(1)) is None


def test_scene_loads_entity_and_network_labels_from_separate_jsonl(tmp_path: Path) -> None:
    twin_file = _write_scene(
        tmp_path,
        "scene_001",
        [{"entity_type": "node", "entity_id": "N0001", "properties": {}, "relations": {}}],
    )
    twin_file.with_name("labels.jsonl").write_text(
        '{"label_type":"node_state","label":[{"entity_id":"N0001","label":"disabled"}]}\n'
        '{"label_type":"network_state","label":"degraded"}\n'
        '{"label_type":"data_flow_bandwidth_constraint","label":['
        '{"data_flow_id":"F000001","label":"traffic_congestion"}]}\n'
        '{"label_type":"data_flow_congestion_pattern","label":['
        '{"data_flow_id":"F000003","label":"multi_channel_saturation"}]}\n'
        '{"label_type":"channel_saturation_cause","label":['
        '{"channel_id":"C0002","label":"single_large_flow"}]}\n'
        '{"label_type":"data_flow_failure_cause","label":['
        '{"data_flow_id":"F000002","entity_id":"C0001"}]}\n'
        '{"label_type":"data_flow_failure_type","label":['
        '{"data_flow_id":"F000002","label":"channel_failure"}]}\n',
        encoding="utf-8",
    )

    scene = SceneData.from_jsonl(twin_file)

    assert scene.entity("node", "N0001").label == "disabled"
    assert scene.network_state == "degraded"
    assert scene.bandwidth_constraints == (("F000001", "traffic_congestion"),)
    assert scene.congestion_patterns == (("F000003", "multi_channel_saturation"),)
    assert scene.channel_saturation_causes == (("C0002", "single_large_flow"),)
    assert scene.flow_failure_causes == (("F000002", "C0001"),)
    assert scene.flow_failure_types == (("F000002", "channel_failure"),)


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
                    properties={
                        **_channel_properties(label, capacity),
                        "available_bandwidth_mbps": available,
                    },
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
                properties={"demand_mbps": 60.0},
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
    twin_file = _write_scene(tmp_path, "scene_001", entities)
    twin_file.with_name("labels.jsonl").write_text(
        '{"label_type":"data_flow_bandwidth_constraint","label":['
        '{"data_flow_id":"F000001","label":"traffic_congestion"},'
        '{"data_flow_id":"F000003","label":"insufficient_channel_capacity"},'
        '{"data_flow_id":"F000004","label":"both"}]}\n',
        encoding="utf-8",
    )
    scene = SceneData.from_jsonl(twin_file)
    generator = AnalysisQuestionGenerator()

    constraint_template = _template(
        "analysis_006",
        "Constraint for $data_flow_id$?",
        ("traffic_congestion", "insufficient_channel_capacity", "both"),
        ("data_flow_id",),
    )

    congested_flow = generator.generate_candidate(
        scene,
        constraint_template,
        "traffic_congestion",
        random.Random(1),
    )
    insufficient_flow = generator.generate_candidate(
        scene,
        constraint_template,
        "insufficient_channel_capacity",
        random.Random(1),
    )
    both_flow = generator.generate_candidate(
        scene,
        constraint_template,
        "both",
        random.Random(1),
    )
    assert congested_flow is not None and congested_flow.replacements == {"data_flow_id": "F000001"}
    assert insufficient_flow is not None and insufficient_flow.replacements == {"data_flow_id": "F000003"}
    assert both_flow is not None and both_flow.replacements == {"data_flow_id": "F000004"}


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
            "saturated",
            properties=_channel_properties("saturated"),
            relations={"connects": ["N0001:IF000001", "N0002:IF000001"]},
        ),
        _entity(
            "channel",
            "C0002",
            "saturated",
            properties=_channel_properties("saturated"),
            relations={"connects": ["N0002:IF000002", "N0003:IF000001"]},
        ),
        _entity("data_flow", "F000001", "normal", relations={"path_nodes": ["N0001", "N0002"]}),
        _entity("data_flow", "F000002", "normal", relations={"path_nodes": ["N0002", "N0003"]}),
    ]
    twin_file = _write_scene(tmp_path, "scene_001", entities)
    twin_file.with_name("labels.jsonl").write_text(
        '{"label_type":"bottleneck","label":['
        '{"data_flow_id":"F000001","channel_id":"C0001"},'
        '{"data_flow_id":"F000002","channel_id":"C0002"}]}\n',
        encoding="utf-8",
    )
    scene = SceneData.from_jsonl(twin_file)
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


def test_congestion_pattern_requires_matching_private_and_public_evidence() -> None:
    scene = SceneData(
        "patterns",
        Path("patterns/twin.jsonl"),
        [
            EntityRecord("channel", "C0001", "saturated", _channel_properties("saturated"), {}),
            EntityRecord("channel", "C0002", "normal", _channel_properties("normal"), {}),
            EntityRecord("channel", "C0003", "saturated", _channel_properties("saturated"), {}),
            EntityRecord(
                "data_flow",
                "F000001",
                "degraded",
                {},
                {"path_channels": ["C0001", "C0002"]},
            ),
            EntityRecord(
                "data_flow",
                "F000002",
                "degraded",
                {},
                {"path_channels": ["C0001", "C0003"]},
            ),
            EntityRecord(
                "data_flow",
                "F000003",
                "normal",
                {},
                {"path_channels": ["C0002"]},
            ),
        ],
        congestion_patterns=[
            ("F000001", "single_channel_bottleneck"),
            ("F000002", "multi_channel_saturation"),
            # A private label without matching public evidence must be ignored.
            ("F000003", "single_channel_bottleneck"),
        ],
    )
    template = _template(
        "congestion_pattern",
        "What is the congestion pattern for $data_flow_id$?",
        ("single_channel_bottleneck", "multi_channel_saturation"),
        ("data_flow_id",),
    )
    generator = AnalysisQuestionGenerator()

    single = generator.generate_candidate(
        scene, template, "single_channel_bottleneck", random.Random(1)
    )
    multiple = generator.generate_candidate(
        scene, template, "multi_channel_saturation", random.Random(1)
    )

    assert single is not None
    assert single.replacements == {"data_flow_id": "F000001"}
    assert single.label == "single_channel_bottleneck"
    assert multiple is not None
    assert multiple.replacements == {"data_flow_id": "F000002"}
    assert multiple.label == "multi_channel_saturation"


def test_channel_saturation_cause_compares_largest_flow_with_all_others() -> None:
    scene = SceneData(
        "saturation_causes",
        Path("saturation_causes/twin.jsonl"),
        [
            EntityRecord(
                "channel",
                "C0001",
                "saturated",
                _channel_properties("saturated"),
                {"carries": ["F000001", "F000002"]},
            ),
            EntityRecord(
                "channel",
                "C0002",
                "saturated",
                _channel_properties("saturated"),
                {"carries": ["F000003", "F000004", "F000005"]},
            ),
            EntityRecord(
                "channel",
                "C0003",
                "normal",
                _channel_properties("normal"),
                {"carries": ["F000006"]},
            ),
            EntityRecord("data_flow", "F000001", "normal", {"demand_mbps": 70.0}, {"path_channels": ["C0001"]}),
            EntityRecord("data_flow", "F000002", "normal", {"demand_mbps": 30.0}, {"path_channels": ["C0001"]}),
            EntityRecord("data_flow", "F000003", "normal", {"demand_mbps": 50.0}, {"path_channels": ["C0002"]}),
            EntityRecord("data_flow", "F000004", "normal", {"demand_mbps": 30.0}, {"path_channels": ["C0002"]}),
            EntityRecord("data_flow", "F000005", "normal", {"demand_mbps": 20.0}, {"path_channels": ["C0002"]}),
            EntityRecord("data_flow", "F000006", "normal", {"demand_mbps": 100.0}, {"path_channels": ["C0003"]}),
        ],
        channel_saturation_causes=[
            ("C0001", "single_large_flow"),
            ("C0002", "multiple_flow_aggregation"),
            # A non-saturated channel cannot be accepted from its private label alone.
            ("C0003", "single_large_flow"),
        ],
    )
    template = _template(
        "channel_saturation_cause",
        "What causes channel $channel_id$ to become saturated?",
        ("single_large_flow", "multiple_flow_aggregation"),
        ("channel_id",),
    )
    generator = AnalysisQuestionGenerator()

    single = generator.generate_candidate(scene, template, "single_large_flow", random.Random(1))
    aggregate = generator.generate_candidate(
        scene, template, "multiple_flow_aggregation", random.Random(1)
    )

    assert single is not None
    assert single.replacements == {"channel_id": "C0001"}
    assert single.label == "single_large_flow"
    assert aggregate is not None
    assert aggregate.replacements == {"channel_id": "C0002"}
    assert aggregate.label == "multiple_flow_aggregation"


def test_flow_failure_cause_requires_unique_public_evidence() -> None:
    def make_scene(with_healthy_side_channels: bool) -> SceneData:
        entities = [
            EntityRecord("node", "N0001", "normal", {"rx_packets": 0, "tx_packets": 0}, {}),
            EntityRecord("node", "N0002", "normal", {"rx_packets": 0, "tx_packets": 0}, {}),
            EntityRecord(
                "nic",
                "N0001:IF1",
                "disabled",
                {},
                {"node": "N0001", "channel": "C0001"},
            ),
            EntityRecord(
                "nic",
                "N0002:IF1",
                "disabled",
                {},
                {"node": "N0002", "channel": "C0001"},
            ),
            EntityRecord(
                "channel",
                "C0001",
                "disabled",
                _channel_properties("disabled"),
                {"connects": ["N0001:IF1", "N0002:IF1"]},
            ),
            EntityRecord(
                "data_flow",
                "F000001",
                "failed",
                {
                    "demand_mbps": 10.0,
                    "tx_packets": 1,
                    "rx_packets": 0,
                    "lost_packets": 1,
                    "throughput_mbps": 0.0,
                },
                {"path_nodes": ["N0001", "N0002"], "path_channels": ["C0001"]},
            ),
        ]
        if with_healthy_side_channels:
            for index, endpoint in enumerate(("N0001", "N0002"), start=2):
                side_node = f"N000{index + 1}"
                channel_id = f"C000{index}"
                endpoint_nic = f"{endpoint}:IF{index}"
                side_nic = f"{side_node}:IF1"
                entities.extend(
                    [
                        EntityRecord(
                            "node",
                            side_node,
                            "normal",
                            {"rx_packets": 1, "tx_packets": 1},
                            {},
                        ),
                        EntityRecord(
                            "nic",
                            endpoint_nic,
                            "normal",
                            {},
                            {"node": endpoint, "channel": channel_id},
                        ),
                        EntityRecord(
                            "nic",
                            side_nic,
                            "normal",
                            {},
                            {"node": side_node, "channel": channel_id},
                        ),
                        EntityRecord(
                            "channel",
                            channel_id,
                            "normal",
                            _channel_properties("normal"),
                            {"connects": [endpoint_nic, side_nic]},
                        ),
                    ]
                )
        return SceneData(
            "failure",
            Path("failure/twin.jsonl"),
            entities,
            flow_failure_causes=[("F000001", "C0001")],
        )

    template = _template(
        "failure",
        "Which entity causes $data_flow_id$ to fail?",
        ("entity_id",),
        ("data_flow_id",),
    )
    generator = AnalysisQuestionGenerator()

    supported = generator.generate_candidate(
        make_scene(True), template, "entity_id", random.Random(1)
    )
    ambiguous = generator.generate_candidate(
        make_scene(False), template, "entity_id", random.Random(1)
    )

    assert supported is not None and supported.label == "C0001"
    assert ambiguous is None


def test_routing_failure_is_publicly_inferable_and_can_cause_flow_failure(
    tmp_path: Path,
) -> None:
    twin_file = _write_scene(
        tmp_path,
        "routing_failure",
        _routing_failed_source_entities("N0001", "F000001"),
    )
    twin_file.with_name("labels.jsonl").write_text(
        '{"label_type":"data_flow_failure_cause","label":['
        '{"data_flow_id":"F000001","entity_id":"N0001"}]}\n',
        encoding="utf-8",
    )
    scene = SceneData.from_jsonl(twin_file)
    generator = AnalysisQuestionGenerator()
    node_template = _template(
        "node_state",
        "What is the state of $node_id$?",
        ("normal", "disabled", "routing_failed"),
        ("node_id",),
    )
    cause_template = _template(
        "failure_cause",
        "Which entity causes $data_flow_id$ to fail?",
        ("entity_id",),
        ("data_flow_id",),
    )

    node_candidate = generator.generate_candidate(
        scene, node_template, "routing_failed", random.Random(1)
    )
    cause_candidate = generator.generate_candidate(
        scene, cause_template, "entity_id", random.Random(1)
    )

    assert node_candidate is not None
    assert node_candidate.replacements == {"node_id": "N0001"}
    assert cause_candidate is not None
    assert cause_candidate.replacements == {"data_flow_id": "F000001"}
    assert cause_candidate.label == "N0001"


@pytest.mark.parametrize(
    ("scene_name", "entities", "entity_id", "failure_type"),
    [
        (
            "node_crash",
            _disabled_transit_node_entities("N0002", "F000001"),
            "N0002",
            "node_crash",
        ),
        (
            "channel_failure",
            _channel_failure_entities("F000001"),
            "C0001",
            "channel_failure",
        ),
        (
            "routing_failure",
            _routing_failed_source_entities("N0001", "F000001"),
            "N0001",
            "routing_failure",
        ),
    ],
)
def test_flow_failure_type_requires_matching_private_label_and_public_evidence(
    tmp_path: Path,
    scene_name: str,
    entities: list[dict[str, object]],
    entity_id: str,
    failure_type: str,
) -> None:
    twin_file = _write_scene(tmp_path, scene_name, entities)
    twin_file.with_name("labels.jsonl").write_text(
        '{"label_type":"data_flow_failure_cause","label":['
        f'{{"data_flow_id":"F000001","entity_id":"{entity_id}"}}]}}\n'
        '{"label_type":"data_flow_failure_type","label":['
        f'{{"data_flow_id":"F000001","label":"{failure_type}"}}]}}\n',
        encoding="utf-8",
    )
    scene = SceneData.from_jsonl(twin_file)
    template = _template(
        "failure_type",
        "What causes $data_flow_id$ to fail?",
        ("node_crash", "channel_failure", "routing_failure"),
        ("data_flow_id",),
    )

    candidate = AnalysisQuestionGenerator().generate_candidate(
        scene,
        template,
        failure_type,
        random.Random(1),
    )

    assert candidate is not None
    assert candidate.replacements == {"data_flow_id": "F000001"}
    assert candidate.label == failure_type
