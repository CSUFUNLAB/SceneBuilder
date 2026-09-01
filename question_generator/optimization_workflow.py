from __future__ import annotations

import csv
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
import re
import shutil
from typing import Any

from .config import CategoryConfig, QuestionGeneratorConfig, load_config
from .evidence import channel_directional_throughputs
from .models import GeneratedQuestion, GenerationCount, QuestionTemplate
from .runner import (
    CategoryRunResult,
    QuestionGenerationResult,
    ensure_question_outputs_absent,
)
from .scene import EntityRecord, SceneData
from .templates import load_template_bundle


DERIVATION_KIND = "optimization"
ORIGINAL_SCENES_DIR_NAME = "origin"
OPTIMIZATION_SCENES_DIR_NAME = "opt"
SCENES_DIR_NAME = "scenes"
INPUT_DIR_NAME = "input"
SCENE_NAME_PATTERN = re.compile(
    r"^(?P<prefix>.+)_id(?P<scene_id>[0-9]+)_(?P<suffix>.+)$"
)
SCENE_INPUT_FILES = (
    "nodes.csv",
    "channels.csv",
    "nics.csv",
    "routing_matrix.csv",
    "traffic.jsonl",
    "metadata.json",
)


@dataclass(frozen=True)
class OptimizationSourceScene:
    scene_dir: Path
    twin_file: Path
    label_file: Path
    nodes: tuple[dict[str, str], ...]
    node_fields: tuple[str, ...]
    channels: tuple[dict[str, str], ...]
    channel_fields: tuple[str, ...]
    nics: tuple[dict[str, str], ...]
    nic_fields: tuple[str, ...]
    traffic: tuple[dict[str, Any], ...]
    routing_matrix: tuple[tuple[int, ...], ...]
    twin: SceneData


@dataclass(frozen=True)
class CandidateSpecification:
    candidate_id: str
    action: dict[str, Any]
    routing_matrix: tuple[tuple[int, ...], ...] | None = None


@dataclass(frozen=True)
class ScenarioSpecification:
    template_id: str
    strategy: str
    replacements: dict[str, str]
    objective: dict[str, Any]
    candidates: tuple[CandidateSpecification, ...]


@dataclass(frozen=True)
class OptimizationCandidatePlan:
    candidate_id: str
    scene_id: str
    scene_dir: Path
    twin_file: Path
    action: dict[str, Any]


@dataclass(frozen=True)
class OptimizationPlan:
    optimization_scenario_id: str
    template_id: str
    strategy: str
    original_scene_id: str
    context_scene_id: str
    context_scene_dir: Path
    context_twin_file: Path
    replacements: dict[str, str]
    objective: dict[str, Any]
    candidates: tuple[OptimizationCandidatePlan, ...]


@dataclass(frozen=True)
class OptimizationPreparation:
    config: QuestionGeneratorConfig
    category: CategoryConfig
    scenes_root: Path
    source_scene_count: int
    plans: tuple[OptimizationPlan, ...]

    @property
    def evaluation_scene_dirs(self) -> tuple[Path, ...]:
        return tuple(
            candidate.scene_dir
            for plan in self.plans
            for candidate in plan.candidates
        )


@dataclass(frozen=True)
class NextHopEntry:
    next_hop_node_id: str
    channel_id: str
    interface_index: int


@dataclass(frozen=True)
class RepairFaultEntity:
    entity_type: str
    entity_id: str
    input_entity_id: str
    related_node_ids: tuple[str, ...]
    affected_flow_ids: tuple[str, ...]
    channel_id: str | None = None


_EXPECTED_TEMPLATE_CONTRACTS = {
    "TO0001": (
        "channel_expansion",
        {"candidate_channel_ids", "node_id", "target_capacity_mbps"},
    ),
    "TO0002": (
        "routing_adjustment",
        {
            "candidate_next_hop_ids",
            "routing_node_id",
            "destination_node_id",
        },
    ),
    "TO0003": (
        "channel_expansion",
        {"candidate_channel_ids", "node_id", "target_capacity_mbps"},
    ),
    "TO0004": (
        "channel_expansion",
        {"candidate_channel_ids", "node_id", "target_capacity_mbps"},
    ),
    "TO0005": (
        "routing_adjustment",
        {
            "candidate_next_hop_ids",
            "routing_node_id",
            "destination_node_id",
        },
    ),
    "TO0006": (
        "routing_adjustment",
        {
            "candidate_next_hop_ids",
            "routing_node_id",
            "destination_node_id",
        },
    ),
    "TO0007": (
        "fault_repair",
        {"candidate_fault_entity_ids"},
    ),
    "TO0008": (
        "fault_repair",
        {"candidate_fault_entity_ids"},
    ),
    "TO0009": (
        "fault_repair",
        {"candidate_fault_entity_ids"},
    ),
}

_TEMPLATE_OBJECTIVES = {
    "TO0001": "total_network_throughput_mbps",
    "TO0002": "destination_received_throughput_mbps",
    "TO0003": "network_packet_weighted_mean_delay_ms",
    "TO0004": "network_aggregate_packet_loss_rate",
    "TO0005": "destination_packet_weighted_mean_delay_ms",
    "TO0006": "destination_aggregate_packet_loss_rate",
    "TO0007": "total_network_throughput_mbps",
    "TO0008": "network_packet_weighted_mean_delay_ms",
    "TO0009": "network_aggregate_packet_loss_rate",
}

_SCENE_TEMPLATE_IDS = {"TO0001", "TO0002", "TO0007"}

_OBJECTIVE_OPTIONS = {
    "total_network_throughput_mbps": (
        "maximize",
        "throughput_improvement_tolerance_mbps",
        "winner_margin_mbps",
    ),
    "destination_received_throughput_mbps": (
        "maximize",
        "throughput_improvement_tolerance_mbps",
        "winner_margin_mbps",
    ),
    "network_packet_weighted_mean_delay_ms": (
        "minimize",
        "delay_improvement_tolerance_ms",
        "delay_winner_margin_ms",
    ),
    "destination_packet_weighted_mean_delay_ms": (
        "minimize",
        "delay_improvement_tolerance_ms",
        "delay_winner_margin_ms",
    ),
    "network_aggregate_packet_loss_rate": (
        "minimize",
        "packet_loss_rate_improvement_tolerance",
        "packet_loss_rate_winner_margin",
    ),
    "destination_aggregate_packet_loss_rate": (
        "minimize",
        "packet_loss_rate_improvement_tolerance",
        "packet_loss_rate_winner_margin",
    ),
}


def prepare_optimization_scenes(
    config_path: str | Path,
    *,
    scenes_root: str | Path | None = None,
) -> OptimizationPreparation:
    config = load_config(config_path)
    category = config.categories["optimization"]
    template_bundle = load_template_bundle(
        category.template_file,
        "optimization",
    )
    templates = list(template_bundle.templates)
    _validate_templates(templates)
    root = (
        Path(scenes_root).expanduser().resolve()
        if scenes_root is not None
        else config.scenes_root
    )
    if not root.is_dir():
        raise ValueError(f"scenes_root is not a directory: {root}")
    _ensure_optimization_outputs_absent(root, category)

    original_input_root = root / ORIGINAL_SCENES_DIR_NAME / INPUT_DIR_NAME
    original_twin_root = root / ORIGINAL_SCENES_DIR_NAME / SCENES_DIR_NAME
    optimization_input_root = (
        root / OPTIMIZATION_SCENES_DIR_NAME / INPUT_DIR_NAME
    )
    optimization_twin_root = (
        root / OPTIMIZATION_SCENES_DIR_NAME / SCENES_DIR_NAME
    )
    source_directories = [
        metadata_file.parent
        for metadata_file in sorted(original_input_root.glob("*/metadata.json"))
        if (original_twin_root / f"{metadata_file.parent.name}.jsonl").is_file()
        and (metadata_file.parent / "labels.jsonl").is_file()
    ]
    sources: list[OptimizationSourceScene] = []
    for scene_dir in source_directories:
        try:
            source = _load_source_scene(
                scene_dir,
                original_twin_root / f"{scene_dir.name}.jsonl",
            )
        except (OSError, ValueError):
            continue
        sources.append(source)
    if not sources:
        raise ValueError(
            "Optimization generation requires at least one origin scene with "
            "complete Twin and labels outputs; run "
            "'python main.py twin -t origin' first"
        )

    optimization_input_root.mkdir(parents=True, exist_ok=True)
    optimization_twin_root.mkdir(parents=True, exist_ok=True)
    options = _optimization_options(category)
    rng = random.Random(config.seed)
    next_scene_id, scene_id_width = _next_scene_id(root)
    scenario_serial = 0
    plans: list[OptimizationPlan] = []
    scenarios_per_template = int(options["scenarios_per_template"])

    for template in (
        template
        for template in templates
        if template.template_id in _SCENE_TEMPLATE_IDS
    ):
        scenario_target = (
            int(options["fault_repair_scenarios"])
            if template.strategy == "fault_repair"
            else scenarios_per_template
        )
        used_source_ids: set[str] = set()
        for _ in range(scenario_target):
            candidate_sources = [
                source
                for source in sources
                if source.scene_dir.name not in used_source_ids
            ]
            rng.shuffle(candidate_sources)
            selected: tuple[
                OptimizationSourceScene,
                ScenarioSpecification,
            ] | None = None
            for source in candidate_sources:
                specification = _build_scenario_specification(
                    source,
                    template,
                    rng,
                    options,
                )
                if specification is not None:
                    selected = source, specification
                    break
            if selected is None:
                break

            source, specification = selected
            used_source_ids.add(source.scene_dir.name)
            scenario_serial += 1
            optimization_scenario_id = f"O{scenario_serial:08d}"
            scene_count = 1 + len(specification.candidates)
            scene_ids = list(range(next_scene_id, next_scene_id + scene_count))
            next_scene_id += scene_count
            plan = _create_optimization_scenario(
                source,
                specification,
                optimization_scenario_id,
                scene_ids,
                scene_id_width,
                optimization_input_root,
                optimization_twin_root,
            )
            plans.append(plan)

    return OptimizationPreparation(
        config=config,
        category=category,
        scenes_root=root,
        source_scene_count=len(sources),
        plans=tuple(plans),
    )


def generate_optimization_questions(
    config_path: str | Path,
    *,
    scenes_root: str | Path | None = None,
) -> QuestionGenerationResult:
    config = load_config(config_path)
    category = config.categories["optimization"]
    root = (
        Path(scenes_root).expanduser().resolve()
        if scenes_root is not None
        else config.scenes_root
    )
    ensure_question_outputs_absent(category)
    template_bundle = load_template_bundle(
        category.template_file,
        "optimization",
    )
    templates = list(template_bundle.templates)
    _validate_templates(templates)
    plans = _load_optimization_plans(root)
    if not plans:
        raise ValueError(
            "No completed optimization candidate groups are available; run "
            "'python main.py twin -t optimization' first"
        )
    options = _optimization_options(category)
    rng = random.Random(config.seed)
    questions: list[GeneratedQuestion] = []
    counts: list[GenerationCount] = []
    question_number = 1
    scene_cache: dict[Path, SceneData] = {}
    for template in templates:
        requested = category.questions_per_question
        matching_plans = [
            plan for plan in plans if plan.strategy == template.strategy
        ]
        rng.shuffle(matching_plans)
        generated = 0
        for plan in matching_plans:
            try:
                candidate_scenes = {
                    candidate.candidate_id: _cached_scene(
                        scene_cache,
                        candidate.twin_file,
                    )
                    for candidate in plan.candidates
                }
                objective_metric = _TEMPLATE_OBJECTIVES[
                    template.template_id
                ]
                if plan.strategy == "fault_repair":
                    winner = _select_unique_repair_winner(
                        plan,
                        candidate_scenes,
                        options,
                        objective_metric=objective_metric,
                    )
                else:
                    context = _cached_scene(
                        scene_cache,
                        plan.context_twin_file,
                    )
                    winner = _select_unique_winner(
                        plan,
                        context,
                        candidate_scenes,
                        options,
                        objective_metric=objective_metric,
                    )
            except (OSError, ValueError):
                continue
            if winner is None:
                continue
            questions.append(
                GeneratedQuestion(
                    question_id=f"Q{question_number:08d}",
                    question_type="optimization",
                    template_id=template.template_id,
                    question=template.render(plan.replacements),
                    label=winner,
                    scene_name=plan.context_scene_id,
                )
            )
            question_number += 1
            generated += 1
            if generated == requested:
                break
        counts.append(
            GenerationCount(
                template_id=template.template_id,
                target_label="",
                requested=requested,
                generated=generated,
            )
        )

    _write_questions(category.output_file, questions)
    category_result = CategoryRunResult(
        category="optimization",
        output_file=category.output_file,
        scene_output_files=(),
        generated_count=len(questions),
        counts=tuple(counts),
    )
    return QuestionGenerationResult(
        scene_count=len(plans),
        categories=(category_result,),
    )


def _validate_templates(templates: list[QuestionTemplate]) -> None:
    actual_ids = {template.template_id for template in templates}
    expected_ids = set(_EXPECTED_TEMPLATE_CONTRACTS)
    if actual_ids != expected_ids:
        raise ValueError(
            "Optimization templates must be exactly "
            f"{sorted(expected_ids)}, got {sorted(actual_ids)}"
        )
    for template in templates:
        expected_strategy, expected_placeholders = (
            _EXPECTED_TEMPLATE_CONTRACTS[template.template_id]
        )
        if template.strategy != expected_strategy:
            raise ValueError(
                f"{template.template_id} strategy must be {expected_strategy}"
            )
        if set(template.placeholders) != expected_placeholders:
            raise ValueError(
                f"{template.template_id} placeholders must be "
                f"{sorted(expected_placeholders)}, got "
                f"{list(template.placeholders)}"
            )


def _optimization_options(category: CategoryConfig) -> dict[str, object]:
    options: dict[str, object] = {
        "channel_expansion_capacity_candidates_mbps": (
            10000.0,
            20000.0,
            40000.0,
            100000.0,
        ),
        "channel_expansion_max_candidates": 4,
        "routing_next_hop_max_candidates": 4,
        "routing_max_destination_flows": 6,
        "scenarios_per_template": category.questions_per_question,
        "fault_repair_scenarios": max(
            category.questions_per_question,
            category.questions_per_question * 5,
        ),
        "throughput_improvement_tolerance_mbps": 0.01,
        "winner_margin_mbps": 0.01,
        "delay_improvement_tolerance_ms": 0.01,
        "delay_winner_margin_ms": 0.01,
        "delay_packet_loss_rate_tolerance": 0.001,
        "packet_loss_rate_improvement_tolerance": 0.001,
        "packet_loss_rate_winner_margin": 0.001,
    }
    options.update(category.options)
    raw_capacities = options[
        "channel_expansion_capacity_candidates_mbps"
    ]
    if not isinstance(raw_capacities, (list, tuple)) or not raw_capacities:
        raise ValueError(
            "optimization options."
            "channel_expansion_capacity_candidates_mbps must be a "
            "non-empty list"
        )
    capacities: list[float] = []
    for raw_value in raw_capacities:
        value = _finite_number(raw_value)
        if value is None or value <= 0:
            raise ValueError(
                "optimization capacity candidates must be positive numbers"
            )
        capacities.append(value)
    if len(capacities) != len(set(capacities)):
        raise ValueError(
            "optimization capacity candidates must be unique"
        )
    options["channel_expansion_capacity_candidates_mbps"] = tuple(
        sorted(capacities)
    )

    for name, minimum in (
        ("channel_expansion_max_candidates", 2),
        ("routing_next_hop_max_candidates", 2),
        ("routing_max_destination_flows", 2),
        ("scenarios_per_template", 1),
        ("fault_repair_scenarios", 1),
    ):
        value = options[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(
                f"optimization options.{name} must be an integer >= {minimum}"
            )

    for name in (
        "throughput_improvement_tolerance_mbps",
        "winner_margin_mbps",
        "delay_improvement_tolerance_ms",
        "delay_winner_margin_ms",
        "delay_packet_loss_rate_tolerance",
        "packet_loss_rate_improvement_tolerance",
        "packet_loss_rate_winner_margin",
    ):
        value = _finite_number(options[name])
        if value is None or value < 0:
            raise ValueError(
                f"optimization options.{name} must be a non-negative number"
            )
        options[name] = value
    for name in (
        "delay_packet_loss_rate_tolerance",
        "packet_loss_rate_improvement_tolerance",
        "packet_loss_rate_winner_margin",
    ):
        if float(options[name]) > 1:
            raise ValueError(
                f"optimization options.{name} must not exceed 1"
            )
    return options


def _load_source_scene(
    scene_dir: Path,
    twin_file: Path,
) -> OptimizationSourceScene:
    nodes, node_fields = _read_csv(scene_dir / "nodes.csv")
    channels, channel_fields = _read_csv(scene_dir / "channels.csv")
    nics, nic_fields = _read_csv(scene_dir / "nics.csv")
    traffic = _read_jsonl(scene_dir / "traffic.jsonl")
    routing_matrix = _read_routing_matrix(
        scene_dir / "routing_matrix.csv",
        len(nodes),
    )
    if not nodes or not channels or not traffic:
        raise ValueError(f"Optimization source scene is empty: {scene_dir}")
    return OptimizationSourceScene(
        scene_dir=scene_dir,
        twin_file=twin_file,
        label_file=scene_dir / "labels.jsonl",
        nodes=tuple(nodes),
        node_fields=tuple(node_fields),
        channels=tuple(channels),
        channel_fields=tuple(channel_fields),
        nics=tuple(nics),
        nic_fields=tuple(nic_fields),
        traffic=tuple(traffic),
        routing_matrix=routing_matrix,
        twin=SceneData.from_jsonl(twin_file),
    )


def _operational_topology(
    source: OptimizationSourceScene,
) -> tuple[set[str], set[str]]:
    """Return nodes and channels that can carry candidate traffic.

    A background fault does not invalidate an optimization source scene.  It
    only makes the affected local resources unavailable as action candidates
    or routing hops.  Degraded channels remain operational because their
    configured capacity can still be expanded and routes can still use them.
    """

    operational_nodes = {
        str(row["node_id"])
        for row in source.nodes
        if str(row.get("state", "normal")) == "normal"
    }
    nics_by_channel: dict[str, list[dict[str, str]]] = {}
    for row in source.nics:
        nics_by_channel.setdefault(str(row["channel_id"]), []).append(row)

    operational_channels: set[str] = set()
    for row in source.channels:
        channel_id = str(row["channel_id"])
        endpoint_nodes = {str(row["src"]), str(row["dst"])}
        attached_nics = nics_by_channel.get(channel_id, [])
        if (
            str(row.get("state", "normal")) not in {"normal", "degraded"}
            or not endpoint_nodes.issubset(operational_nodes)
            or {str(nic["node"]) for nic in attached_nics}
            != endpoint_nodes
            or any(
                str(nic.get("state", "normal")) != "normal"
                for nic in attached_nics
            )
        ):
            continue
        operational_channels.add(channel_id)
    return operational_nodes, operational_channels


def _build_scenario_specification(
    source: OptimizationSourceScene,
    template: QuestionTemplate,
    rng: random.Random,
    options: dict[str, object],
) -> ScenarioSpecification | None:
    objective_metric = _TEMPLATE_OBJECTIVES.get(template.template_id)
    if objective_metric is None:
        raise ValueError(
            f"Unsupported optimization template: {template.template_id}"
        )
    if template.strategy == "channel_expansion":
        return _build_channel_expansion_specification(
            source,
            rng,
            options,
            template_id=template.template_id,
            objective_metric=objective_metric,
        )
    if template.strategy == "routing_adjustment":
        return _build_routing_adjustment_specification(
            source,
            rng,
            options,
            template_id=template.template_id,
            objective_metric=objective_metric,
        )
    if template.strategy == "fault_repair":
        return _build_fault_repair_specification(
            source,
            rng,
            options,
            template_id=template.template_id,
            objective_metric=objective_metric,
        )
    raise ValueError(f"Unsupported optimization template: {template.template_id}")


def _build_channel_expansion_specification(
    source: OptimizationSourceScene,
    rng: random.Random,
    options: dict[str, object],
    *,
    template_id: str = "TO0001",
    objective_metric: str = "total_network_throughput_mbps",
) -> ScenarioSpecification | None:
    operational_nodes, operational_channels = _operational_topology(source)
    channel_by_id = {
        str(row["channel_id"]): row for row in source.channels
    }
    incident_channels: dict[str, list[str]] = {}
    for channel_id, row in channel_by_id.items():
        if channel_id not in operational_channels:
            continue
        for node_id in (str(row["src"]), str(row["dst"])):
            if node_id in operational_nodes:
                incident_channels.setdefault(node_id, []).append(channel_id)

    flow_ids_by_channel: dict[str, set[str]] = {
        channel_id: set() for channel_id in channel_by_id
    }
    for flow in source.twin.entities("data_flow"):
        for channel_id in flow.relations.get("path_channels", []):
            flow_ids_by_channel.setdefault(str(channel_id), set()).add(
                flow.entity_id
            )

    max_candidates = int(options["channel_expansion_max_candidates"])
    anchor_nodes = list(incident_channels)
    rng.shuffle(anchor_nodes)
    anchor_nodes.sort(
        key=lambda node_id: max(
            (
                _channel_load_ratio(source.twin, channel_id)
                for channel_id in incident_channels[node_id]
            ),
            default=0.0,
        ),
        reverse=True,
    )
    for node_id in anchor_nodes:
        candidate_ids = [
            channel_id
            for channel_id in incident_channels[node_id]
            if flow_ids_by_channel.get(channel_id)
        ]
        if len(candidate_ids) < 2:
            continue
        candidate_ids.sort(
            key=lambda channel_id: (
                _channel_load_ratio(source.twin, channel_id),
                channel_id,
            ),
            reverse=True,
        )
        candidate_ids = candidate_ids[:max_candidates]
        if len(
            {
                frozenset(flow_ids_by_channel[channel_id])
                for channel_id in candidate_ids
            }
        ) < 2:
            continue

        current_capacities = [
            float(channel_by_id[channel_id]["bandwidth_mbps"])
            for channel_id in candidate_ids
        ]
        eligible_targets = [
            float(value)
            for value in options[
                "channel_expansion_capacity_candidates_mbps"
            ]
            if float(value) > max(current_capacities) + 1e-9
        ]
        if not eligible_targets:
            continue
        target_capacity = rng.choice(eligible_targets)
        candidates = tuple(
            CandidateSpecification(
                candidate_id=channel_id,
                action={
                    "kind": "channel_expansion",
                    "channel_id": channel_id,
                    "before_capacity_mbps": float(
                        channel_by_id[channel_id]["bandwidth_mbps"]
                    ),
                    "target_capacity_mbps": target_capacity,
                },
            )
            for channel_id in candidate_ids
        )
        return ScenarioSpecification(
            template_id=template_id,
            strategy="channel_expansion",
            replacements={
                "candidate_channel_ids": _english_list(candidate_ids),
                "node_id": node_id,
                "target_capacity_mbps": _format_number(target_capacity),
            },
            objective={
                "metric": objective_metric,
                "flow_ids": [
                    flow.entity_id
                    for flow in source.twin.entities("data_flow")
                ],
            },
            candidates=candidates,
        )
    return None


def _build_fault_repair_specification(
    source: OptimizationSourceScene,
    rng: random.Random,
    options: dict[str, object],
    *,
    template_id: str = "TO0007",
    objective_metric: str = "total_network_throughput_mbps",
) -> ScenarioSpecification | None:
    del options
    if not _source_is_fault_free(source):
        return None

    repairable_entities = _repair_fault_entities(source)
    eligible_pairs = [
        (left, right)
        for left_index, left in enumerate(repairable_entities)
        for right in repairable_entities[left_index + 1 :]
        if not _fault_entities_share_domain(left, right)
        and left.affected_flow_ids != right.affected_flow_ids
    ]
    if not eligible_pairs:
        return None
    left, right = rng.choice(eligible_pairs)
    selected_entities = [left, right]
    rng.shuffle(selected_entities)
    fault_payloads = {
        entity.entity_id: _fault_payload(entity)
        for entity in selected_entities
    }
    candidates: list[CandidateSpecification] = []
    for repair_entity, remaining_entity in (
        (selected_entities[0], selected_entities[1]),
        (selected_entities[1], selected_entities[0]),
    ):
        candidates.append(
            CandidateSpecification(
                candidate_id=repair_entity.entity_id,
                action={
                    "kind": "fault_repair",
                    "repair_fault": deepcopy(
                        fault_payloads[repair_entity.entity_id]
                    ),
                    "remaining_fault": deepcopy(
                        fault_payloads[remaining_entity.entity_id]
                    ),
                },
            )
        )
    flow_ids = [
        flow.entity_id for flow in source.twin.entities("data_flow")
    ]
    if not flow_ids:
        return None
    return ScenarioSpecification(
        template_id=template_id,
        strategy="fault_repair",
        replacements={
            "candidate_fault_entity_ids": _english_list(
                [entity.entity_id for entity in selected_entities]
            ),
        },
        objective={
            "metric": objective_metric,
            "flow_ids": flow_ids,
            "fault_entity_ids": [
                entity.entity_id for entity in selected_entities
            ],
        },
        candidates=tuple(candidates),
    )


def _source_is_fault_free(source: OptimizationSourceScene) -> bool:
    return (
        all(str(row.get("state", "normal")) == "normal" for row in source.nodes)
        and all(
            str(row.get("state", "normal")) == "normal"
            for row in source.channels
        )
        and all(
            str(row.get("state", "normal")) == "normal"
            for row in source.nics
        )
    )


def _repair_fault_entities(
    source: OptimizationSourceScene,
) -> list[RepairFaultEntity]:
    channel_endpoints = {
        str(row["channel_id"]): (str(row["src"]), str(row["dst"]))
        for row in source.channels
    }
    flows = source.twin.entities("data_flow")
    flow_ids_by_node: dict[str, set[str]] = {}
    flow_ids_by_channel: dict[str, set[str]] = {}
    for flow in flows:
        for node_id in flow.relations.get("path_nodes", []):
            flow_ids_by_node.setdefault(str(node_id), set()).add(
                flow.entity_id
            )
        for channel_id in flow.relations.get("path_channels", []):
            flow_ids_by_channel.setdefault(str(channel_id), set()).add(
                flow.entity_id
            )
    result: list[RepairFaultEntity] = []
    for row in source.nodes:
        node_id = str(row["node_id"])
        affected_flow_ids = tuple(sorted(flow_ids_by_node.get(node_id, set())))
        if affected_flow_ids:
            result.append(
                RepairFaultEntity(
                    entity_type="node",
                    entity_id=node_id,
                    input_entity_id=node_id,
                    related_node_ids=(node_id,),
                    affected_flow_ids=affected_flow_ids,
                )
            )
    for row in source.channels:
        channel_id = str(row["channel_id"])
        affected_flow_ids = tuple(
            sorted(flow_ids_by_channel.get(channel_id, set()))
        )
        if affected_flow_ids:
            result.append(
                RepairFaultEntity(
                    entity_type="channel",
                    entity_id=channel_id,
                    input_entity_id=channel_id,
                    related_node_ids=channel_endpoints[channel_id],
                    affected_flow_ids=affected_flow_ids,
                    channel_id=channel_id,
                )
            )
    for row in source.nics:
        node_id = str(row["node"])
        channel_id = str(row["channel_id"])
        interface_index = int(float(row["interface_index"]))
        public_id = f"{node_id}:IF{interface_index:06d}"
        affected_flow_ids = tuple(
            sorted(flow_ids_by_channel.get(channel_id, set()))
        )
        if not affected_flow_ids:
            continue
        result.append(
            RepairFaultEntity(
                entity_type="nic",
                entity_id=public_id,
                input_entity_id=str(row["nic_id"]),
                related_node_ids=channel_endpoints.get(
                    channel_id,
                    (node_id,),
                ),
                affected_flow_ids=affected_flow_ids,
                channel_id=channel_id,
            )
        )
    result.sort(
        key=lambda entity: (
            entity.entity_type,
            entity.entity_id,
        )
    )
    return result


def _fault_entities_share_domain(
    left: RepairFaultEntity,
    right: RepairFaultEntity,
) -> bool:
    if (
        left.channel_id is not None
        and left.channel_id == right.channel_id
    ):
        return True
    if left.entity_type == "node" and left.entity_id in right.related_node_ids:
        return True
    if right.entity_type == "node" and right.entity_id in left.related_node_ids:
        return True
    return False


def _fault_payload(
    entity: RepairFaultEntity,
) -> dict[str, object]:
    return {
        "entity_type": entity.entity_type,
        "entity_id": entity.entity_id,
        "input_entity_id": entity.input_entity_id,
        "state": "disabled",
    }


def _build_routing_adjustment_specification(
    source: OptimizationSourceScene,
    rng: random.Random,
    options: dict[str, object],
    *,
    template_id: str = "TO0002",
    objective_metric: str = "destination_received_throughput_mbps",
) -> ScenarioSpecification | None:
    operational_nodes, _ = _operational_topology(source)
    flows_by_destination: dict[str, list[EntityRecord]] = {}
    for flow in source.twin.entities("data_flow"):
        destination_node = str(
            flow.relations.get("destination_node", "")
        )
        if destination_node:
            flows_by_destination.setdefault(destination_node, []).append(flow)

    max_destination_flows = int(options["routing_max_destination_flows"])
    destinations = [
        destination_node
        for destination_node, flows in flows_by_destination.items()
        if destination_node in operational_nodes
        and 2 <= len(flows) <= max_destination_flows
        and len(
            {
                str(flow.relations.get("source_node", ""))
                for flow in flows
            }
        )
        >= 2
    ]
    rng.shuffle(destinations)
    for destination_node in destinations:
        destination_flows = flows_by_destination[destination_node]
        routing_nodes = {
            str(node_id)
            for flow in destination_flows
            for node_id in flow.relations.get("path_nodes", [])[:-1]
            if str(node_id) in operational_nodes
        }
        affected_flows_by_node = {
            routing_node: sorted(
                flow.entity_id
                for flow in destination_flows
                if routing_node
                in {
                    str(node_id)
                    for node_id in flow.relations.get("path_nodes", [])[:-1]
                }
            )
            for routing_node in routing_nodes
        }
        routing_nodes = list(routing_nodes)
        rng.shuffle(routing_nodes)
        routing_nodes.sort(
            key=lambda node_id: len(affected_flows_by_node[node_id]),
            reverse=True,
        )
        for routing_node in routing_nodes:
            candidates = _generate_next_hop_candidates(
                source,
                routing_node,
                destination_node,
                affected_flows_by_node[routing_node],
                int(options["routing_next_hop_max_candidates"]),
                rng,
            )
            if len(candidates) < 2:
                continue
            before_peak_load = max(
                float(
                    candidate.action.get(
                        "before_path_max_load_ratio",
                        0.0,
                    )
                )
                for candidate in candidates
            )
            best_candidate_peak_load = min(
                float(
                    candidate.action.get(
                        "candidate_path_max_load_ratio",
                        math.inf,
                    )
                )
                for candidate in candidates
            )
            before_path_fault_affected = any(
                bool(
                    candidate.action.get(
                        "before_path_fault_affected",
                        False,
                    )
                )
                for candidate in candidates
            )
            if not before_path_fault_affected and (
                before_peak_load < 0.95
                or best_candidate_peak_load
                >= before_peak_load - 1e-9
            ):
                continue
            return ScenarioSpecification(
                template_id=template_id,
                strategy="routing_adjustment",
                replacements={
                    "candidate_next_hop_ids": _english_list(
                        [candidate.candidate_id for candidate in candidates]
                    ),
                    "routing_node_id": routing_node,
                    "destination_node_id": destination_node,
                },
                objective={
                    "metric": objective_metric,
                    "destination_node_id": destination_node,
                    "routing_node_id": routing_node,
                    "flow_ids": sorted(
                        flow.entity_id for flow in destination_flows
                    ),
                    "affected_flow_ids": affected_flows_by_node[
                        routing_node
                    ],
                },
                candidates=tuple(candidates),
            )
    return None


def _generate_next_hop_candidates(
    source: OptimizationSourceScene,
    routing_node: str,
    destination_node: str,
    affected_flow_ids: list[str],
    max_candidates: int,
    rng: random.Random,
) -> list[CandidateSpecification]:
    operational_nodes, operational_channels = _operational_topology(source)
    if (
        routing_node not in operational_nodes
        or destination_node not in operational_nodes
    ):
        return []
    node_ids = [str(row["node_id"]) for row in source.nodes]
    node_index = {node_id: index for index, node_id in enumerate(node_ids)}
    routing_index = node_index.get(routing_node)
    destination_index = node_index.get(destination_node)
    if routing_index is None or destination_index is None:
        return []
    entries_by_node, entry_by_interface = _routing_entries(source)
    current_interface = source.routing_matrix[routing_index][
        destination_index
    ]
    current_entry = entry_by_interface.get(
        (routing_node, current_interface)
    )
    if current_entry is None:
        return []
    current_traced = _trace_routing_path(
        source.routing_matrix,
        node_index,
        entry_by_interface,
        routing_node,
        destination_node,
        current_entry,
    )
    if current_traced is None:
        return []
    current_path_nodes, current_path_entries = current_traced
    current_path_operational = _routing_path_is_operational(
        current_path_nodes,
        current_path_entries,
        operational_nodes,
        operational_channels,
    )
    channel_states = {
        str(row["channel_id"]): str(row.get("state", "normal"))
        for row in source.channels
    }
    current_path_fault_affected = (
        not current_path_operational
        or any(
            channel_states.get(entry.channel_id, "disabled") != "normal"
            for entry in current_path_entries
        )
    )
    current_path_peak_load = max(
        (
            _channel_load_ratio(source.twin, entry.channel_id)
            for entry in current_path_entries
        ),
        default=0.0,
    )

    entries_by_next_hop: dict[str, list[NextHopEntry]] = {}
    for entry in entries_by_node.get(routing_node, []):
        entries_by_next_hop.setdefault(
            entry.next_hop_node_id,
            [],
        ).append(entry)

    feasible: list[
        tuple[NextHopEntry, list[str], list[NextHopEntry]]
    ] = []
    for next_hop_node_id, entries in entries_by_next_hop.items():
        # A next-hop node alone is ambiguous when parallel channels exist.
        if (
            len(entries) != 1
            or next_hop_node_id == current_entry.next_hop_node_id
        ):
            continue
        entry = entries[0]
        traced = _trace_routing_path(
            source.routing_matrix,
            node_index,
            entry_by_interface,
            routing_node,
            destination_node,
            entry,
        )
        if traced is None:
            continue
        path_nodes, path_entries = traced
        if not _routing_path_is_operational(
            path_nodes,
            path_entries,
            operational_nodes,
            operational_channels,
        ):
            continue
        feasible.append((entry, path_nodes, path_entries))

    rng.shuffle(feasible)
    feasible.sort(
        key=lambda candidate: (
            max(
                (
                    _channel_load_ratio(source.twin, entry.channel_id)
                    for entry in candidate[2]
                ),
                default=0.0,
            ),
            len(candidate[1]),
        )
    )
    result: list[CandidateSpecification] = []
    for entry, path_nodes, path_entries in feasible[:max_candidates]:
        candidate_path_peak_load = max(
            (
                _channel_load_ratio(source.twin, path_entry.channel_id)
                for path_entry in path_entries
            ),
            default=0.0,
        )
        matrix = [list(row) for row in source.routing_matrix]
        matrix[routing_index][destination_index] = entry.interface_index
        result.append(
            CandidateSpecification(
                candidate_id=entry.next_hop_node_id,
                action={
                    "kind": "routing_adjustment",
                    "routing_node_id": routing_node,
                    "destination_node_id": destination_node,
                    "before_next_hop_node_id": (
                        current_entry.next_hop_node_id
                    ),
                    "before_channel_id": current_entry.channel_id,
                    "before_interface_index": (
                        current_entry.interface_index
                    ),
                    "before_path_nodes": current_path_nodes,
                    "before_path_channel_ids": [
                        path_entry.channel_id
                        for path_entry in current_path_entries
                    ],
                    "before_path_operational": current_path_operational,
                    "before_path_fault_affected": (
                        current_path_fault_affected
                    ),
                    "before_path_max_load_ratio": current_path_peak_load,
                    "candidate_next_hop_node_id": (
                        entry.next_hop_node_id
                    ),
                    "candidate_channel_id": entry.channel_id,
                    "candidate_interface_index": entry.interface_index,
                    "resulting_path_nodes": path_nodes,
                    "resulting_path_channel_ids": [
                        path_entry.channel_id for path_entry in path_entries
                    ],
                    "resulting_path_operational": True,
                    "candidate_path_max_load_ratio": (
                        candidate_path_peak_load
                    ),
                    "affected_flow_ids": list(affected_flow_ids),
                },
                routing_matrix=tuple(tuple(row) for row in matrix),
            )
        )
    return result


def _routing_path_is_operational(
    path_nodes: list[str],
    path_entries: list[NextHopEntry],
    operational_nodes: set[str],
    operational_channels: set[str],
) -> bool:
    return (
        bool(path_nodes)
        and len(path_entries) + 1 == len(path_nodes)
        and all(node_id in operational_nodes for node_id in path_nodes)
        and all(
            entry.channel_id in operational_channels
            for entry in path_entries
        )
    )


def _routing_entries(
    source: OptimizationSourceScene,
) -> tuple[
    dict[str, list[NextHopEntry]],
    dict[tuple[str, int], NextHopEntry],
]:
    channel_endpoints = {
        str(row["channel_id"]): (str(row["src"]), str(row["dst"]))
        for row in source.channels
    }
    entries_by_node: dict[str, list[NextHopEntry]] = {}
    entry_by_interface: dict[tuple[str, int], NextHopEntry] = {}
    for row in source.nics:
        node_id = str(row["node"])
        channel_id = str(row["channel_id"])
        endpoints = channel_endpoints.get(channel_id)
        if endpoints is None or node_id not in endpoints:
            continue
        next_hop_node_id = (
            endpoints[1] if node_id == endpoints[0] else endpoints[0]
        )
        interface_index = int(float(row["interface_index"]))
        entry = NextHopEntry(
            next_hop_node_id=next_hop_node_id,
            channel_id=channel_id,
            interface_index=interface_index,
        )
        entries_by_node.setdefault(node_id, []).append(entry)
        entry_by_interface[(node_id, interface_index)] = entry
    for entries in entries_by_node.values():
        entries.sort(
            key=lambda entry: (
                entry.next_hop_node_id,
                entry.channel_id,
                entry.interface_index,
            )
        )
    return entries_by_node, entry_by_interface


def _trace_routing_path(
    routing_matrix: tuple[tuple[int, ...], ...],
    node_index: dict[str, int],
    entry_by_interface: dict[tuple[str, int], NextHopEntry],
    routing_node: str,
    destination_node: str,
    candidate_entry: NextHopEntry,
) -> tuple[list[str], list[NextHopEntry]] | None:
    destination_index = node_index.get(destination_node)
    if destination_index is None:
        return None
    path_nodes = [routing_node]
    path_entries: list[NextHopEntry] = []
    seen = {routing_node}
    current = routing_node
    while current != destination_node:
        current_index = node_index.get(current)
        if current_index is None:
            return None
        if current == routing_node:
            entry = candidate_entry
        else:
            interface_index = routing_matrix[current_index][
                destination_index
            ]
            entry = entry_by_interface.get((current, interface_index))
            if entry is None:
                return None
        path_entries.append(entry)
        current = entry.next_hop_node_id
        if current in seen:
            return None
        seen.add(current)
        path_nodes.append(current)
        if len(path_nodes) > len(node_index):
            return None
    return path_nodes, path_entries


def _create_optimization_scenario(
    source: OptimizationSourceScene,
    specification: ScenarioSpecification,
    optimization_scenario_id: str,
    scene_numeric_ids: list[int],
    scene_id_width: int,
    optimization_input_root: Path,
    optimization_twin_root: Path,
) -> OptimizationPlan:
    if len(scene_numeric_ids) != 1 + len(specification.candidates):
        raise ValueError("Optimization scene ID allocation is inconsistent")
    context_scene_id = _derived_scene_name(
        source.scene_dir.name,
        scene_numeric_ids[0],
        scene_id_width,
        optimization_scenario_id,
        "base",
    )
    candidate_scene_ids = [
        _derived_scene_name(
            source.scene_dir.name,
            numeric_id,
            scene_id_width,
            optimization_scenario_id,
            f"c{index:02d}",
        )
        for index, numeric_id in enumerate(scene_numeric_ids[1:], start=1)
    ]
    context_scene_dir = optimization_input_root / context_scene_id
    context_twin_file = optimization_twin_root / f"{context_scene_id}.jsonl"
    created_directories: list[Path] = []
    created_files: list[Path] = []
    try:
        _copy_scene_input(source.scene_dir, context_scene_dir)
        created_directories.append(context_scene_dir)
        candidate_records = [
            {
                "candidate_id": candidate.candidate_id,
                "candidate_scene_id": candidate_scene_id,
                "action": deepcopy(candidate.action),
            }
            for candidate, candidate_scene_id in zip(
                specification.candidates,
                candidate_scene_ids,
            )
        ]
        _update_optimization_metadata(
            context_scene_dir / "metadata.json",
            scene_id=context_scene_id,
            scene_numeric_id=scene_numeric_ids[0],
            derivation={
                "kind": DERIVATION_KIND,
                "role": "context",
                "optimization_scenario_id": optimization_scenario_id,
                "template_id": specification.template_id,
                "strategy": specification.strategy,
                "original_scene_id": source.scene_dir.name,
                "context_scene_id": context_scene_id,
                "question_replacements": dict(specification.replacements),
                "objective": deepcopy(specification.objective),
                "candidates": candidate_records,
            },
        )
        shutil.copy2(source.label_file, context_scene_dir / "labels.jsonl")
        shutil.copy2(source.twin_file, context_twin_file)
        created_files.append(context_twin_file)

        candidate_plans: list[OptimizationCandidatePlan] = []
        for index, (
            candidate,
            candidate_scene_id,
            candidate_numeric_id,
        ) in enumerate(
            zip(
                specification.candidates,
                candidate_scene_ids,
                scene_numeric_ids[1:],
            ),
            start=1,
        ):
            candidate_scene_dir = optimization_input_root / candidate_scene_id
            _copy_scene_input(source.scene_dir, candidate_scene_dir)
            created_directories.append(candidate_scene_dir)
            if candidate.action.get("kind") == "channel_expansion":
                channels, channel_fields = _read_csv(
                    candidate_scene_dir / "channels.csv"
                )
                channel = _find_row(
                    channels,
                    "channel_id",
                    str(candidate.action["channel_id"]),
                )
                channel["bandwidth_mbps"] = _format_number(
                    float(candidate.action["target_capacity_mbps"])
                )
                _write_csv(
                    candidate_scene_dir / "channels.csv",
                    channel_fields,
                    channels,
                )
            elif candidate.action.get("kind") == "routing_adjustment":
                if candidate.routing_matrix is None:
                    raise ValueError(
                        "Routing candidate is missing its routing matrix"
                    )
                _write_routing_matrix(
                    candidate_scene_dir / "routing_matrix.csv",
                    candidate.routing_matrix,
                )
            elif candidate.action.get("kind") == "fault_repair":
                _apply_fault_repair_candidate(
                    candidate_scene_dir,
                    candidate.action,
                )
            else:
                raise ValueError(
                    f"Unknown optimization action: {candidate.action}"
                )
            _update_optimization_metadata(
                candidate_scene_dir / "metadata.json",
                scene_id=candidate_scene_id,
                scene_numeric_id=candidate_numeric_id,
                derivation={
                    "kind": DERIVATION_KIND,
                    "role": "candidate",
                    "optimization_scenario_id": optimization_scenario_id,
                    "template_id": specification.template_id,
                    "strategy": specification.strategy,
                    "original_scene_id": source.scene_dir.name,
                    "context_scene_id": context_scene_id,
                    "candidate_id": candidate.candidate_id,
                    "candidate_index": index,
                    "action": deepcopy(candidate.action),
                    "objective": deepcopy(specification.objective),
                },
            )
            candidate_twin_file = (
                optimization_twin_root / f"{candidate_scene_id}.jsonl"
            )
            candidate_plans.append(
                OptimizationCandidatePlan(
                    candidate_id=candidate.candidate_id,
                    scene_id=candidate_scene_id,
                    scene_dir=candidate_scene_dir,
                    twin_file=candidate_twin_file,
                    action=deepcopy(candidate.action),
                )
            )

        return OptimizationPlan(
            optimization_scenario_id=optimization_scenario_id,
            template_id=specification.template_id,
            strategy=specification.strategy,
            original_scene_id=source.scene_dir.name,
            context_scene_id=context_scene_id,
            context_scene_dir=context_scene_dir,
            context_twin_file=context_twin_file,
            replacements=dict(specification.replacements),
            objective=deepcopy(specification.objective),
            candidates=tuple(candidate_plans),
        )
    except Exception:
        for file_path in reversed(created_files):
            if file_path.is_file() or file_path.is_symlink():
                file_path.unlink()
        for directory in reversed(created_directories):
            if directory.is_dir():
                shutil.rmtree(directory)
        raise


def _apply_fault_repair_candidate(
    scene_dir: Path,
    action: dict[str, Any],
) -> None:
    remaining_fault = action.get("remaining_fault")
    if not isinstance(remaining_fault, dict):
        raise ValueError(
            "Fault-repair candidate is missing remaining_fault"
        )
    entity_type = str(remaining_fault.get("entity_type", ""))
    input_entity_id = str(
        remaining_fault.get("input_entity_id", "")
    )
    state = str(remaining_fault.get("state", ""))
    if entity_type == "node":
        if state != "disabled":
            raise ValueError("Node repair candidates require a disabled fault")
        rows, fields = _read_csv(scene_dir / "nodes.csv")
        row = _find_row(rows, "node_id", input_entity_id)
        row["state"] = state
        _write_csv(scene_dir / "nodes.csv", fields, rows)
    elif entity_type == "channel":
        if state not in {"disabled", "degraded"}:
            raise ValueError(
                "Channel repair candidates require a disabled or degraded "
                "fault"
            )
        rows, fields = _read_csv(scene_dir / "channels.csv")
        row = _find_row(rows, "channel_id", input_entity_id)
        row["state"] = state
        row["capacity_multiplier"] = _format_number(
            float(remaining_fault.get("capacity_multiplier", 1.0))
        )
        _write_csv(scene_dir / "channels.csv", fields, rows)
    elif entity_type == "nic":
        if state != "disabled":
            raise ValueError("NIC repair candidates require a disabled fault")
        rows, fields = _read_csv(scene_dir / "nics.csv")
        row = _find_row(rows, "nic_id", input_entity_id)
        row["state"] = state
        _write_csv(scene_dir / "nics.csv", fields, rows)
    else:
        raise ValueError(
            f"Unsupported fault-repair entity type: {entity_type}"
        )
    _update_fault_repair_metadata(
        scene_dir / "metadata.json",
        remaining_fault,
    )


def _update_fault_repair_metadata(
    path: Path,
    remaining_fault: dict[str, object],
) -> None:
    with path.open("r", encoding="utf-8-sig") as handle:
        metadata = json.load(handle)
    if not isinstance(metadata, dict):
        raise ValueError(f"Metadata must be an object: {path}")
    fault_entry: dict[str, object] = {
        "entity_type": str(remaining_fault["entity_type"]),
        "entity_id": str(remaining_fault["input_entity_id"]),
        "state": str(remaining_fault["state"]),
    }
    if "capacity_multiplier" in remaining_fault:
        fault_entry["capacity_multiplier"] = float(
            remaining_fault["capacity_multiplier"]
        )
    fault_generation = metadata.setdefault("generation", {}).setdefault(
        "fault_generation",
        {},
    )
    fault_generation["selected_scenario"] = "single"
    fault_generation["fault_count"] = 1
    fault_generation["faulted_entities"] = [fault_entry]
    summary = metadata.setdefault("summary", {})
    summary["fault_scenario"] = "single"
    summary["fault_count"] = 1
    _write_json(path, metadata)


def _copy_scene_input(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise ValueError(
            f"Refusing to overwrite optimization scene: {destination}"
        )
    destination.mkdir(parents=True)
    for file_name in SCENE_INPUT_FILES:
        source_file = source / file_name
        if not source_file.is_file():
            raise ValueError(f"Original scene is missing {source_file}")
        shutil.copy2(source_file, destination / file_name)


def _update_optimization_metadata(
    path: Path,
    *,
    scene_id: str,
    scene_numeric_id: int,
    derivation: dict[str, Any],
) -> None:
    with path.open("r", encoding="utf-8-sig") as handle:
        metadata = json.load(handle)
    if not isinstance(metadata, dict):
        raise ValueError(f"Metadata must be an object: {path}")
    metadata["scene_name"] = scene_id
    metadata["scene_id"] = scene_numeric_id
    metadata["derivation"] = derivation
    _write_json(path, metadata)


def _load_optimization_plans(root: Path) -> list[OptimizationPlan]:
    input_root = root / OPTIMIZATION_SCENES_DIR_NAME / INPUT_DIR_NAME
    twin_root = root / OPTIMIZATION_SCENES_DIR_NAME / SCENES_DIR_NAME
    plans: list[OptimizationPlan] = []
    for metadata_file in sorted(input_root.glob("*/metadata.json")):
        with metadata_file.open("r", encoding="utf-8-sig") as handle:
            metadata = json.load(handle)
        if not isinstance(metadata, dict):
            continue
        derivation = metadata.get("derivation")
        if (
            not isinstance(derivation, dict)
            or derivation.get("kind") != DERIVATION_KIND
            or derivation.get("role") != "context"
        ):
            continue
        template_id = str(derivation.get("template_id", ""))
        contract = _EXPECTED_TEMPLATE_CONTRACTS.get(template_id)
        if contract is None:
            continue
        strategy = str(derivation.get("strategy", ""))
        if strategy != contract[0]:
            continue
        context_scene_id = str(
            derivation.get("context_scene_id", metadata_file.parent.name)
        )
        if context_scene_id != metadata_file.parent.name:
            continue
        replacements = derivation.get("question_replacements")
        objective = derivation.get("objective")
        raw_candidates = derivation.get("candidates")
        if (
            not isinstance(replacements, dict)
            or set(str(key) for key in replacements) != contract[1]
            or not isinstance(objective, dict)
            or not isinstance(raw_candidates, list)
            or len(raw_candidates) < 2
        ):
            continue
        candidates: list[OptimizationCandidatePlan] = []
        valid = True
        seen_candidate_ids: set[str] = set()
        for raw_candidate in raw_candidates:
            if not isinstance(raw_candidate, dict):
                valid = False
                break
            candidate_id = str(raw_candidate.get("candidate_id", ""))
            candidate_scene_id = str(
                raw_candidate.get("candidate_scene_id", "")
            )
            action = raw_candidate.get("action")
            if (
                not candidate_id
                or candidate_id in seen_candidate_ids
                or not candidate_scene_id
                or not isinstance(action, dict)
            ):
                valid = False
                break
            seen_candidate_ids.add(candidate_id)
            candidates.append(
                OptimizationCandidatePlan(
                    candidate_id=candidate_id,
                    scene_id=candidate_scene_id,
                    scene_dir=input_root / candidate_scene_id,
                    twin_file=twin_root / f"{candidate_scene_id}.jsonl",
                    action=dict(action),
                )
            )
        context_twin_file = twin_root / f"{context_scene_id}.jsonl"
        if (
            not valid
            or not context_twin_file.is_file()
            or not (metadata_file.parent / "labels.jsonl").is_file()
            or any(
                not candidate.twin_file.is_file()
                or not (candidate.scene_dir / "labels.jsonl").is_file()
                for candidate in candidates
            )
        ):
            continue
        plans.append(
            OptimizationPlan(
                optimization_scenario_id=str(
                    derivation.get("optimization_scenario_id", "")
                ),
                template_id=template_id,
                strategy=strategy,
                original_scene_id=str(
                    derivation.get("original_scene_id", "")
                ),
                context_scene_id=context_scene_id,
                context_scene_dir=metadata_file.parent,
                context_twin_file=context_twin_file,
                replacements={
                    str(key): str(value)
                    for key, value in replacements.items()
                },
                objective=dict(objective),
                candidates=tuple(candidates),
            )
        )
    return plans


def _select_unique_winner(
    plan: OptimizationPlan,
    context: SceneData,
    candidates: dict[str, SceneData],
    options: dict[str, object],
    *,
    objective_metric: str | None = None,
) -> str | None:
    flow_ids = plan.objective.get("flow_ids")
    if not isinstance(flow_ids, list) or not flow_ids:
        return None
    normalized_flow_ids = [str(flow_id) for flow_id in flow_ids]
    metric = (
        str(objective_metric)
        if objective_metric is not None
        else str(plan.objective.get("metric", ""))
    )
    objective_options = _OBJECTIVE_OPTIONS.get(metric)
    if objective_options is None:
        return None
    direction, improvement_option, margin_option = objective_options
    baseline = _objective_value(context, normalized_flow_ids, metric)
    if baseline is None:
        return None
    baseline_loss_rate: float | None = None
    if "mean_delay_ms" in metric:
        baseline_loss_rate = _aggregate_packet_loss_rate(
            context,
            normalized_flow_ids,
        )
        if baseline_loss_rate is None:
            return None
    values: list[tuple[str, float]] = []
    for candidate in plan.candidates:
        candidate_scene = candidates.get(candidate.candidate_id)
        if candidate_scene is None:
            return None
        value = _objective_value(
            candidate_scene,
            normalized_flow_ids,
            metric,
        )
        if value is None:
            return None
        if baseline_loss_rate is not None:
            candidate_loss_rate = _aggregate_packet_loss_rate(
                candidate_scene,
                normalized_flow_ids,
            )
            if (
                candidate_loss_rate is None
                or candidate_loss_rate
                > baseline_loss_rate
                + float(options["delay_packet_loss_rate_tolerance"])
            ):
                return None
        values.append((candidate.candidate_id, value))
    if len(values) < 2:
        return None
    values.sort(
        key=(
            (lambda item: (-item[1], item[0]))
            if direction == "maximize"
            else (lambda item: (item[1], item[0]))
        )
    )
    best_id, best_value = values[0]
    second_value = values[1][1]
    improvement_tolerance = float(options[improvement_option])
    winner_margin = float(options[margin_option])
    if direction == "maximize":
        if best_value <= baseline + improvement_tolerance:
            return None
        if best_value <= second_value + winner_margin:
            return None
    else:
        if best_value >= baseline - improvement_tolerance:
            return None
        if best_value >= second_value - winner_margin:
            return None
    return best_id


def _select_unique_repair_winner(
    plan: OptimizationPlan,
    candidates: dict[str, SceneData],
    options: dict[str, object],
    *,
    objective_metric: str,
) -> str | None:
    if len(plan.candidates) != 2:
        return None
    flow_ids = plan.objective.get("flow_ids")
    if not isinstance(flow_ids, list) or not flow_ids:
        return None
    normalized_flow_ids = [str(flow_id) for flow_id in flow_ids]
    objective_options = _OBJECTIVE_OPTIONS.get(objective_metric)
    if objective_options is None:
        return None
    direction, _, margin_option = objective_options
    values: list[tuple[str, float]] = []
    loss_rates: dict[str, float] = {}
    for candidate in plan.candidates:
        candidate_scene = candidates.get(candidate.candidate_id)
        if candidate_scene is None:
            return None
        value = _objective_value(
            candidate_scene,
            normalized_flow_ids,
            objective_metric,
        )
        if value is None:
            return None
        values.append((candidate.candidate_id, value))
        if "mean_delay_ms" in objective_metric:
            loss_rate = _aggregate_packet_loss_rate(
                candidate_scene,
                normalized_flow_ids,
            )
            if loss_rate is None:
                return None
            loss_rates[candidate.candidate_id] = loss_rate
    values.sort(
        key=(
            (lambda item: (-item[1], item[0]))
            if direction == "maximize"
            else (lambda item: (item[1], item[0]))
        )
    )
    best_id, best_value = values[0]
    second_id, second_value = values[1]
    winner_margin = float(options[margin_option])
    if direction == "maximize":
        if best_value <= second_value + winner_margin:
            return None
    elif best_value >= second_value - winner_margin:
        return None
    if (
        "mean_delay_ms" in objective_metric
        and loss_rates[best_id]
        > loss_rates[second_id]
        + float(options["delay_packet_loss_rate_tolerance"])
    ):
        return None
    return best_id


def _objective_value(
    scene: SceneData,
    flow_ids: list[str],
    metric: str,
) -> float | None:
    if metric in {
        "total_network_throughput_mbps",
        "destination_received_throughput_mbps",
    }:
        return _sum_flow_throughput(scene, flow_ids)
    if metric in {
        "network_packet_weighted_mean_delay_ms",
        "destination_packet_weighted_mean_delay_ms",
    }:
        return _packet_weighted_mean_delay(scene, flow_ids)
    if metric in {
        "network_aggregate_packet_loss_rate",
        "destination_aggregate_packet_loss_rate",
    }:
        return _aggregate_packet_loss_rate(scene, flow_ids)
    return None


def _sum_flow_throughput(
    scene: SceneData,
    flow_ids: list[str],
) -> float | None:
    total = 0.0
    for flow_id in flow_ids:
        flow = scene.entity("data_flow", flow_id)
        if flow is None:
            return None
        throughput = _finite_number(flow.properties.get("throughput_mbps"))
        if throughput is None or throughput < 0:
            return None
        total += throughput
    return total


def _packet_weighted_mean_delay(
    scene: SceneData,
    flow_ids: list[str],
) -> float | None:
    weighted_delay = 0.0
    received_packets = 0.0
    for flow_id in flow_ids:
        flow = scene.entity("data_flow", flow_id)
        if flow is None:
            return None
        delay = _finite_number(flow.properties.get("average_delay_ms"))
        flow_received_packets = _finite_number(
            flow.properties.get("rx_packets")
        )
        if (
            flow_received_packets is None
            or flow_received_packets < 0
        ):
            return None
        # A failed flow contributes no received packets to a packet-weighted
        # delay.  Its impact is still represented by the aggregate packet-loss
        # constraint used by the delay templates.
        if flow_received_packets == 0:
            continue
        if delay is None or delay < 0:
            return None
        weighted_delay += delay * flow_received_packets
        received_packets += flow_received_packets
    if received_packets <= 0:
        return None
    return weighted_delay / received_packets


def _aggregate_packet_loss_rate(
    scene: SceneData,
    flow_ids: list[str],
) -> float | None:
    transmitted_packets = 0.0
    lost_packets = 0.0
    for flow_id in flow_ids:
        flow = scene.entity("data_flow", flow_id)
        if flow is None:
            return None
        flow_transmitted_packets = _finite_number(
            flow.properties.get("tx_packets")
        )
        flow_lost_packets = _finite_number(
            flow.properties.get("lost_packets")
        )
        if (
            flow_transmitted_packets is None
            or flow_transmitted_packets < 0
            or flow_lost_packets is None
            or flow_lost_packets < 0
            or flow_lost_packets > flow_transmitted_packets
        ):
            return None
        if flow_transmitted_packets == 0:
            continue
        transmitted_packets += flow_transmitted_packets
        lost_packets += flow_lost_packets
    if transmitted_packets <= 0:
        return None
    return lost_packets / transmitted_packets


def _cached_scene(
    cache: dict[Path, SceneData],
    path: Path,
) -> SceneData:
    scene = cache.get(path)
    if scene is None:
        scene = SceneData.from_jsonl(path)
        cache[path] = scene
    return scene


def _channel_load_ratio(scene: SceneData, channel_id: str) -> float:
    channel = scene.entity("channel", channel_id)
    if channel is None:
        return 0.0
    capacity = _finite_number(
        channel.properties.get("original_capacity_mbps")
    )
    throughputs = channel_directional_throughputs(scene, channel)
    if capacity is None or capacity <= 0 or throughputs is None:
        return 0.0
    return max(throughputs) / capacity


def _ensure_optimization_outputs_absent(
    root: Path,
    category: CategoryConfig,
) -> None:
    input_root = root / OPTIMIZATION_SCENES_DIR_NAME / INPUT_DIR_NAME
    twin_root = root / OPTIMIZATION_SCENES_DIR_NAME / SCENES_DIR_NAME
    candidates = [category.output_file]
    for output_root in (input_root, twin_root):
        if output_root.is_dir():
            candidates.extend(output_root.iterdir())
        elif output_root.exists() or output_root.is_symlink():
            candidates.append(output_root)
    existing = next(
        (
            path
            for path in candidates
            if path.exists() or path.is_symlink()
        ),
        None,
    )
    if existing is not None:
        raise ValueError(
            f"Optimization output already exists: {existing}; run "
            "'python main.py clean -o twin -t optimization' first"
        )


def _next_scene_id(root: Path) -> tuple[int, int]:
    scene_ids: list[int] = []
    widths: list[int] = []
    for metadata_file in root.rglob("metadata.json"):
        match = SCENE_NAME_PATTERN.fullmatch(metadata_file.parent.name)
        if match is None:
            continue
        token = match.group("scene_id")
        scene_ids.append(int(token))
        widths.append(len(token))
    return max(scene_ids, default=0) + 1, max([4, *widths])


def _derived_scene_name(
    original_scene_name: str,
    scene_numeric_id: int,
    scene_id_width: int,
    optimization_scenario_id: str,
    role: str,
) -> str:
    match = SCENE_NAME_PATTERN.fullmatch(original_scene_name)
    if match is None:
        raise ValueError(
            "Original scene name does not follow "
            "'<prefix>_id<number>_<suffix>': "
            f"{original_scene_name}"
        )
    return (
        f"{match.group('prefix')}_"
        f"id{scene_numeric_id:0{scene_id_width}d}_"
        f"{match.group('suffix')}_opt_"
        f"{optimization_scenario_id}_{role}"
    )


def _english_list(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return ", ".join(values[:-1]) + f", and {values[-1]}"


def _format_number(value: float) -> str:
    if math.isclose(value, round(value), rel_tol=0.0, abs_tol=1e-9):
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file has no header: {path}")
        return [dict(row) for row in reader], list(reader.fieldnames)


def _write_csv(
    path: Path,
    field_names: list[str],
    rows: list[dict[str, object]],
) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=field_names,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _read_routing_matrix(
    path: Path,
    node_count: int,
) -> tuple[tuple[int, ...], ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        raw_rows = [row for row in csv.reader(handle)]
    if len(raw_rows) != node_count or any(
        len(row) != node_count for row in raw_rows
    ):
        raise ValueError(
            f"{path} must be a square {node_count} x {node_count} matrix"
        )
    try:
        return tuple(
            tuple(int(value) for value in row) for row in raw_rows
        )
    except ValueError as exc:
        raise ValueError(f"{path} contains a non-integer value") from exc


def _write_routing_matrix(
    path: Path,
    rows: tuple[tuple[int, ...], ...],
) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(rows)
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON at {path}:{line_number}: {exc.msg}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(
                    f"{path}:{line_number} must contain a JSON object"
                )
            values.append(value)
    return values


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def _write_questions(path: Path, questions: list[GeneratedQuestion]) -> None:
    if not questions:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for question in questions:
            handle.write(
                json.dumps(
                    question.to_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            handle.write("\n")
    temporary.replace(path)


def _find_row(
    rows: list[dict[str, Any]],
    id_field: str,
    entity_id: str,
) -> dict[str, Any]:
    matches = [
        row for row in rows if str(row.get(id_field, "")) == entity_id
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {id_field}={entity_id}")
    return matches[0]
