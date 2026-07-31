from __future__ import annotations

import csv
from dataclasses import dataclass
from copy import deepcopy
import json
import math
from pathlib import Path
import random
import re
import shutil
from typing import Any

from .config import CategoryConfig, QuestionGeneratorConfig, load_config
from .evidence import channel_directional_throughputs
from .models import (
    EvolutionEventType,
    GeneratedQuestion,
    GenerationCount,
    QuestionTemplate,
)
from .runner import (
    CategoryRunResult,
    QuestionGenerationResult,
    export_question_template,
)
from .scene import EntityRecord, SceneData
from .templates import load_template_bundle


DERIVATION_KIND = "evolution"
ORIGINAL_SCENES_DIR_NAME = "origin"
EVOLUTION_SCENES_DIR_NAME = "evo"
SCENES_DIR_NAME = "scenes"
INPUT_DIR_NAME = "input"
NUMERIC_UNCHANGED_RELATIVE_TOLERANCE = 0.01
EXACT_NUMERIC_METRICS = frozenset({"lost_packets"})
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
class EvolutionQuestionRule:
    event_type_id: str
    target_entity_type: str
    comparison_kind: str
    metric_name: str | None = None


_QUESTION_RULES = {
    "TE0001": EvolutionQuestionRule(
        "node_failure", "data_flow", "numeric", "throughput_mbps"
    ),
    "TE0002": EvolutionQuestionRule(
        "node_failure", "data_flow", "numeric", "lost_packets"
    ),
    "TE0003": EvolutionQuestionRule(
        "node_recovery", "data_flow", "numeric", "throughput_mbps"
    ),
    "TE0004": EvolutionQuestionRule(
        "node_recovery", "data_flow", "numeric", "average_delay_ms"
    ),
    "TE0005": EvolutionQuestionRule(
        "channel_failure", "data_flow", "numeric", "throughput_mbps"
    ),
    "TE0006": EvolutionQuestionRule(
        "channel_failure", "data_flow", "numeric", "lost_packets"
    ),
    "TE0007": EvolutionQuestionRule(
        "channel_recovery", "data_flow", "numeric", "throughput_mbps"
    ),
    "TE0008": EvolutionQuestionRule(
        "channel_recovery", "data_flow", "numeric", "average_delay_ms"
    ),
    "TE0009": EvolutionQuestionRule(
        "nic_failure", "data_flow", "numeric", "throughput_mbps"
    ),
    "TE0010": EvolutionQuestionRule(
        "nic_failure", "data_flow", "numeric", "lost_packets"
    ),
    "TE0011": EvolutionQuestionRule(
        "nic_recovery", "data_flow", "numeric", "throughput_mbps"
    ),
    "TE0012": EvolutionQuestionRule(
        "nic_recovery", "data_flow", "numeric", "average_delay_ms"
    ),
    "TE0013": EvolutionQuestionRule(
        "flow_load_increase",
        "channel",
        "numeric",
        "maximum_directional_bandwidth_mbps",
    ),
    "TE0014": EvolutionQuestionRule(
        "flow_load_increase", "data_flow", "numeric", "average_delay_ms"
    ),
    "TE0015": EvolutionQuestionRule(
        "flow_load_decrease",
        "channel",
        "numeric",
        "maximum_directional_bandwidth_mbps",
    ),
    "TE0016": EvolutionQuestionRule(
        "flow_load_decrease", "data_flow", "numeric", "average_delay_ms"
    ),
    "TE0017": EvolutionQuestionRule("node_failure", "node", "status"),
    "TE0018": EvolutionQuestionRule("node_recovery", "node", "status"),
    "TE0023": EvolutionQuestionRule(
        "flow_load_increase", "channel", "saturation_outcome"
    ),
    "TE0024": EvolutionQuestionRule(
        "flow_load_decrease", "channel", "saturation_recovery"
    ),
    "TE0025": EvolutionQuestionRule(
        "flow_load_increase", "nic", "saturation_outcome"
    ),
    "TE0026": EvolutionQuestionRule(
        "flow_load_decrease", "nic", "saturation_recovery"
    ),
    "TE0027": EvolutionQuestionRule(
        "flow_addition", "data_flow", "after_status"
    ),
    "TE0028": EvolutionQuestionRule(
        "flow_addition",
        "channel",
        "numeric",
        "maximum_directional_bandwidth_mbps",
    ),
    "TE0029": EvolutionQuestionRule(
        "flow_addition", "channel", "saturation_outcome"
    ),
    "TE0030": EvolutionQuestionRule(
        "flow_addition", "nic", "saturation_outcome"
    ),
}


_EXPECTED_ANSWER_VALUES = {
    "TE0001": ("unchanged", "decrease"),
    "TE0017": ("unchanged", "disabled"),
    "TE0018": ("recovered", "unchanged"),
    "TE0023": ("saturated", "not_saturated"),
    "TE0024": ("recovered", "not_recovered"),
    "TE0025": ("saturated", "not_saturated"),
    "TE0026": ("recovered", "not_recovered"),
    "TE0027": ("normal", "unstable", "degraded", "failed"),
    "TE0029": ("saturated", "not_saturated"),
    "TE0030": ("saturated", "not_saturated"),
}


@dataclass(frozen=True)
class EvolutionSourceScene:
    scene_dir: Path
    nodes: tuple[dict[str, str], ...]
    channels: tuple[dict[str, str], ...]
    nics: tuple[dict[str, str], ...]
    traffic: tuple[dict[str, Any], ...]
    routing_matrix: tuple[tuple[int, ...], ...]
    physical_faults: tuple[dict[str, str], ...]
    public_nic_id_by_raw_id: dict[str, str]


@dataclass(frozen=True)
class EvolutionPlan:
    event_scenario_id: str
    event_type: str
    original_scene_id: str
    evolved_scene_id: str
    original_scene_file: Path
    evolved_scene_file: Path
    evolved_input_dir: Path
    event: dict[str, Any]

    @property
    def evolved_scene_dir(self) -> Path:
        return self.evolved_input_dir


@dataclass(frozen=True)
class EvolutionPreparation:
    config: QuestionGeneratorConfig
    category: CategoryConfig
    scenes_root: Path
    source_scene_count: int
    plans: tuple[EvolutionPlan, ...]


def prepare_evolution_scenes(
    config_path: str | Path,
    *,
    scenes_root: str | Path | None = None,
) -> EvolutionPreparation:
    config = load_config(config_path)
    category = config.categories["evolution"]
    template_bundle = load_template_bundle(
        category.template_file,
        "evolution",
    )
    _validate_templates(
        list(template_bundle.templates),
        template_bundle.event_types,
    )
    root = (
        Path(scenes_root).expanduser().resolve()
        if scenes_root is not None
        else config.scenes_root
    )
    _clear_previous_evolution_outputs(root, category.output_file)

    original_input_root = (
        root / ORIGINAL_SCENES_DIR_NAME / INPUT_DIR_NAME
    )
    original_scenes_root = (
        root / ORIGINAL_SCENES_DIR_NAME / SCENES_DIR_NAME
    )
    evolution_input_root = (
        root / EVOLUTION_SCENES_DIR_NAME / INPUT_DIR_NAME
    )
    evolution_scenes_root = (
        root / EVOLUTION_SCENES_DIR_NAME / SCENES_DIR_NAME
    )
    source_directories = [
        path.parent
        for path in sorted(original_input_root.glob("*/metadata.json"))
        if not _is_evolution_scene(path.parent)
        and (
            original_scenes_root / f"{path.parent.name}.jsonl"
        ).is_file()
        and (path.parent / "labels.jsonl").is_file()
    ]
    sources = [_load_source_scene(path) for path in source_directories]
    if not sources:
        raise ValueError(
            "Evolution generation requires at least one origin scene with "
            "complete twin.jsonl and labels.jsonl outputs; run "
            "'python main.py twin origin' first"
        )

    rng = random.Random(config.seed)
    options = _evolution_options(category)
    scenes_per_event = int(options["scenes_per_event"])
    plans: list[EvolutionPlan] = []
    next_scene_numeric_id, scene_id_width = _next_scene_id(root)
    serial = 0
    for spec in template_bundle.event_types:
        event_type = spec.event_type_id
        used_source_scene_ids: set[str] = set()
        for _ in range(scenes_per_event):
            candidates = [
                source
                for source in sources
                if source.scene_dir.name not in used_source_scene_ids
            ]
            rng.shuffle(candidates)
            selected: tuple[EvolutionSourceScene, dict[str, Any]] | None = None
            for source in candidates:
                event = _select_event(source, spec, rng, options)
                if event is not None:
                    selected = source, event
                    break
            if selected is None:
                break
            source, event = selected
            used_source_scene_ids.add(source.scene_dir.name)
            serial += 1
            event_scenario_id = f"E{serial:08d}"
            evolved_scene_numeric_id = next_scene_numeric_id
            next_scene_numeric_id += 1
            evolved_scene_id = _evolved_scene_name(
                source.scene_dir.name,
                evolved_scene_numeric_id,
                scene_id_width,
                event_scenario_id,
            )
            evolved_scene_dir = evolution_input_root / evolved_scene_id
            if evolved_scene_dir.exists():
                raise ValueError(
                    f"Refusing to overwrite unrecognized scene directory: {evolved_scene_dir}"
                )
            try:
                _create_evolved_scene(
                    source,
                    evolved_scene_dir,
                    evolved_scene_id,
                    evolved_scene_numeric_id,
                    event_scenario_id,
                    event_type,
                    event,
                )
            except Exception:
                if evolved_scene_dir.is_dir():
                    shutil.rmtree(evolved_scene_dir)
                raise
            plans.append(
                EvolutionPlan(
                    event_scenario_id=event_scenario_id,
                    event_type=event_type,
                    original_scene_id=source.scene_dir.name,
                    evolved_scene_id=evolved_scene_id,
                    original_scene_file=(
                        original_scenes_root
                        / f"{source.scene_dir.name}.jsonl"
                    ),
                    evolved_scene_file=(
                        evolution_scenes_root
                        / f"{evolved_scene_id}.jsonl"
                    ),
                    evolved_input_dir=evolved_scene_dir,
                    event=event,
                )
            )

    return EvolutionPreparation(
        config=config,
        category=category,
        scenes_root=root,
        source_scene_count=len(sources),
        plans=tuple(plans),
    )


def generate_evolution_questions(
    config_path: str | Path,
    *,
    scenes_root: str | Path | None = None,
) -> QuestionGenerationResult:
    config = load_config(config_path)
    category = config.categories["evolution"]
    root = (
        Path(scenes_root).expanduser().resolve()
        if scenes_root is not None
        else config.scenes_root
    )
    template_bundle = load_template_bundle(
        category.template_file,
        "evolution",
    )
    templates = list(template_bundle.templates)
    _validate_templates(templates, template_bundle.event_types)
    plans = _load_evolution_plans(root, template_bundle.event_types)
    if not plans:
        raise ValueError(
            "No completed evolution scene pairs are available; run "
            "'python main.py twin evo' first"
        )
    export_question_template(category)
    _clear_evolution_question_outputs(
        root / EVOLUTION_SCENES_DIR_NAME / INPUT_DIR_NAME,
        category.output_file,
    )

    questions: list[GeneratedQuestion] = []
    generated_by_target: dict[tuple[str, str], int] = {}
    rng = random.Random(config.seed)
    original_scene_cache: dict[Path, SceneData] = {}
    evolved_scene_cache: dict[Path, SceneData] = {}
    related_probability = float(
        _evolution_options(category)["related_target_probability"]
    )
    question_number = 1
    event_type_by_id = {
        event_type.event_type_id: event_type
        for event_type in template_bundle.event_types
    }
    for template in templates:
        rule = _question_rule(template)
        event_type = event_type_by_id[rule.event_type_id]
        matching_plans = [
            plan
            for plan in plans
            if plan.event_type == rule.event_type_id
        ]
        for target_label, requested in _target_label_counts(
            template,
            category.questions_per_question,
        ):
            candidates: list[tuple[EvolutionPlan, dict[str, str], bool]] = []
            for plan in matching_plans:
                try:
                    before = original_scene_cache.get(plan.original_scene_file)
                    if before is None:
                        before = SceneData.from_jsonl(plan.original_scene_file)
                        original_scene_cache[plan.original_scene_file] = before
                    after = evolved_scene_cache.get(plan.evolved_scene_file)
                    if after is None:
                        after = SceneData.from_jsonl(plan.evolved_scene_file)
                        evolved_scene_cache[plan.evolved_scene_file] = after
                    candidates.extend(
                        (
                            plan,
                            replacements,
                            related,
                        )
                        for replacements, related in _find_evidence_candidates(
                            before,
                            after,
                            template,
                            rule,
                            event_type,
                            plan.event,
                            target_label,
                        )
                    )
                except (OSError, ValueError):
                    continue
            related_candidates = [
                candidate for candidate in candidates if candidate[2]
            ]
            other_candidates = [
                candidate for candidate in candidates if not candidate[2]
            ]
            rng.shuffle(related_candidates)
            rng.shuffle(other_candidates)
            if rng.random() < related_probability:
                candidates = [*related_candidates, *other_candidates]
            else:
                candidates = [*other_candidates, *related_candidates]
            selected_candidates = candidates[:requested]
            for plan, replacements, _ in selected_candidates:
                question = GeneratedQuestion(
                    question_id=f"Q{question_number:08d}",
                    question_type="evolution",
                    template_id=template.template_id,
                    question=template.render(replacements),
                    label=target_label,
                    scene_name=plan.evolved_scene_id,
                    original_scene_id=plan.original_scene_id,
                    evolved_scene_id=plan.evolved_scene_id,
                )
                question_number += 1
                questions.append(question)
            generated_by_target[(template.template_id, target_label)] = len(
                selected_candidates
            )

    _write_questions(category.output_file, questions)
    counts = tuple(
        GenerationCount(
            template_id=template.template_id,
            target_label=target_label,
            requested=requested,
            generated=generated_by_target.get(
                (template.template_id, target_label),
                0,
            ),
        )
        for template in templates
        for target_label, requested in _target_label_counts(
            template,
            category.questions_per_question,
        )
    )
    category_result = CategoryRunResult(
        category="evolution",
        output_file=category.output_file,
        scene_output_files=(),
        generated_count=len(questions),
        counts=counts,
    )
    return QuestionGenerationResult(
        scene_count=len(plans),
        categories=(category_result,),
    )


def _validate_templates(
    templates: list[QuestionTemplate],
    event_types: tuple[EvolutionEventType, ...],
) -> None:
    event_type_by_id = {
        event_type.event_type_id: event_type
        for event_type in event_types
    }
    for template in templates:
        rule = _question_rule(template)
        event_type = event_type_by_id.get(rule.event_type_id)
        if event_type is None:
            raise ValueError(
                f"{template.template_id} references unknown event rule "
                f"{rule.event_type_id}"
            )
        expected_placeholders = template_placeholder_names(
            template,
            rule,
            event_type,
        )
        if set(template.placeholders) != expected_placeholders:
            raise ValueError(
                f"{template.template_id} placeholders must be "
                f"{sorted(expected_placeholders)}, got {list(template.placeholders)}"
            )
        expected_answers = _EXPECTED_ANSWER_VALUES.get(
            template.template_id
        )
        if (
            expected_answers is not None
            and template.answer_values != expected_answers
        ):
            raise ValueError(
                f"{template.template_id} answers must be "
                f"{list(expected_answers)}"
            )
        if (
            expected_answers is None
            and rule.comparison_kind == "numeric"
            and template.answer_values
            != ("increase", "unchanged", "decrease")
        ):
            raise ValueError(
                f"{template.template_id} numeric answers must be "
                "[increase, unchanged, decrease]"
            )


def _question_rule(template: QuestionTemplate) -> EvolutionQuestionRule:
    try:
        return _QUESTION_RULES[template.template_id]
    except KeyError as exc:
        raise ValueError(
            f"No evolution generation rule for {template.template_id}"
        ) from exc


def _evolution_options(category: CategoryConfig) -> dict[str, object]:
    options: dict[str, object] = {
        "load_increase_multiplier_range": (1.2, 2.0),
        "load_decrease_multiplier_range": (0.2, 0.8),
        "flow_addition_demand_mbps_range": (1.0, 100.0),
        "related_target_probability": 0.8,
        "scenes_per_event": 3,
    }
    options.update(category.options)
    for name in (
        "load_increase_multiplier_range",
        "load_decrease_multiplier_range",
        "flow_addition_demand_mbps_range",
    ):
        raw_range = options[name]
        if (
            not isinstance(raw_range, (list, tuple))
            or len(raw_range) != 2
            or isinstance(raw_range[0], bool)
            or isinstance(raw_range[1], bool)
        ):
            raise ValueError(f"evolution options.{name} must contain two numbers")
        lower, upper = float(raw_range[0]), float(raw_range[1])
        if lower <= 0 or upper < lower:
            raise ValueError(f"evolution options.{name} is invalid")
        options[name] = (lower, upper)
    probability = float(options["related_target_probability"])
    if not 0 <= probability <= 1:
        raise ValueError("evolution options.related_target_probability must be in [0, 1]")
    options["related_target_probability"] = probability
    scenes_per_event = options["scenes_per_event"]
    if (
        isinstance(scenes_per_event, bool)
        or not isinstance(scenes_per_event, int)
        or scenes_per_event <= 0
    ):
        raise ValueError(
            "evolution options.scenes_per_event must be a positive integer"
        )
    return options


def _target_label_counts(
    template: QuestionTemplate,
    total_count: int,
) -> tuple[tuple[str, int], ...]:
    label_count = len(template.answer_values)
    if total_count % label_count != 0:
        raise ValueError(
            f"{template.template_id} requests {total_count} questions but has "
            f"{label_count} labels; questions_per_question must be divisible "
            "by the label count"
        )
    count_per_label = total_count // label_count
    return tuple(
        (target_label, count_per_label)
        for target_label in template.answer_values
    )


def _select_event(
    source: EvolutionSourceScene,
    spec: EvolutionEventType,
    rng: random.Random,
    options: dict[str, object],
) -> dict[str, Any] | None:
    if spec.entity_type == "data_flow" and spec.change == "addition":
        if source.physical_faults:
            return None
        new_flow = _choose_new_data_flow(source, rng, options)
        if new_flow is None:
            return None
        return {
            "entity_type": "data_flow",
            "entity_id": new_flow["flow_id"],
            "input_entity_id": new_flow["flow_id"],
            "change": "addition",
            "before_state": "absent",
            "source_node_id": new_flow["src"],
            "destination_node_id": new_flow["dst"],
            "demand_mbps": new_flow["demand_mbps"],
            "feature_model": new_flow["feature_model"],
        }

    event_entity = _choose_event_entity(source, spec, rng)
    if event_entity is None:
        return None
    public_event_id, raw_event_id, state = event_entity
    event: dict[str, Any] = {
        "entity_type": spec.entity_type,
        "entity_id": public_event_id,
        "input_entity_id": raw_event_id,
        "change": spec.change,
        "before_state": state,
    }

    if spec.entity_type == "data_flow":
        multiplier_range = options[
            "load_increase_multiplier_range"
            if spec.change == "load_increase"
            else "load_decrease_multiplier_range"
        ]
        lower, upper = multiplier_range
        event["multiplier"] = rng.uniform(float(lower), float(upper))

    return event


def _choose_new_data_flow(
    source: EvolutionSourceScene,
    rng: random.Random,
    options: dict[str, object],
) -> dict[str, object] | None:
    node_ids = [str(row["node_id"]) for row in source.nodes]
    if len(source.routing_matrix) != len(node_ids):
        return None
    existing_pairs = {
        (str(row.get("src", "")), str(row.get("dst", "")))
        for row in source.traffic
    }
    candidate_pairs = [
        (source_id, destination_id)
        for source_index, source_id in enumerate(node_ids)
        for destination_index, destination_id in enumerate(node_ids)
        if source_index != destination_index
        and len(source.routing_matrix[source_index]) == len(node_ids)
        and source.routing_matrix[source_index][destination_index] > 0
        and (source_id, destination_id) not in existing_pairs
    ]
    if not candidate_pairs:
        return None

    source_id, destination_id = rng.choice(candidate_pairs)
    lower, upper = options["flow_addition_demand_mbps_range"]
    demand_mbps = round(rng.uniform(float(lower), float(upper)), 6)
    return {
        "flow_id": _next_flow_id(source.traffic),
        "src": source_id,
        "dst": destination_id,
        "demand_mbps": demand_mbps,
        "feature_model": "cbr",
    }


def _next_flow_id(traffic: tuple[dict[str, Any], ...]) -> str:
    numeric_ids = [
        int(match.group(1))
        for row in traffic
        if (
            match := re.fullmatch(
                r"F([0-9]+)",
                str(row.get("flow_id", "")),
            )
        )
        is not None
    ]
    width = max(
        [6]
        + [
            len(str(row.get("flow_id", ""))) - 1
            for row in traffic
            if re.fullmatch(r"F[0-9]+", str(row.get("flow_id", "")))
        ]
    )
    next_numeric_id = max(numeric_ids, default=0) + 1
    existing_ids = {
        str(row.get("flow_id", ""))
        for row in traffic
    }
    while f"F{next_numeric_id:0{width}d}" in existing_ids:
        next_numeric_id += 1
    return f"F{next_numeric_id:0{width}d}"


def template_placeholder_names(
    template: QuestionTemplate,
    rule: EvolutionQuestionRule,
    event_type: EvolutionEventType,
) -> set[str]:
    names = {_event_placeholder(event_type.entity_type)}
    if (
        event_type.entity_type == "data_flow"
        and event_type.change == "addition"
    ):
        names.update(
            {
                "event_source_node_id",
                "event_destination_node_id",
                "event_demand_mbps",
            }
        )
    elif (
        event_type.entity_type == "data_flow"
        and event_type.change in {"load_increase", "load_decrease"}
    ):
        names.add("event_rate_multiplier")
    if not (
        event_type.entity_type == "data_flow"
        and rule.target_entity_type == "data_flow"
    ):
        names.add(_target_placeholder(rule.target_entity_type))
    return names


def _choose_event_entity(
    source: EvolutionSourceScene,
    spec: EvolutionEventType,
    rng: random.Random,
) -> tuple[str, str, str] | None:
    if spec.change in {"failure", "load_increase", "load_decrease"}:
        if source.physical_faults:
            return None
    elif spec.change == "recovery":
        matching = [
            fault
            for fault in source.physical_faults
            if fault["entity_type"] == spec.entity_type
        ]
        if len(source.physical_faults) != 1 or len(matching) != 1:
            return None
        fault = matching[0]
        return fault["entity_id"], fault["input_entity_id"], fault["state"]

    if spec.entity_type == "node":
        candidates = [
            (str(row["node_id"]), str(row["node_id"]), str(row.get("state", "normal")))
            for row in source.nodes
            if str(row.get("state", "normal")) == "normal"
        ]
    elif spec.entity_type == "channel":
        candidates = [
            (
                str(row["channel_id"]),
                str(row["channel_id"]),
                str(row.get("state", "normal")),
            )
            for row in source.channels
            if str(row.get("state", "normal")) == "normal"
        ]
    elif spec.entity_type == "nic":
        candidates = [
            (
                source.public_nic_id_by_raw_id[str(row["nic_id"])],
                str(row["nic_id"]),
                str(row.get("state", "normal")),
            )
            for row in source.nics
            if str(row.get("state", "normal")) == "normal"
            and str(row["nic_id"]) in source.public_nic_id_by_raw_id
        ]
    else:
        candidates = [
            (str(row["flow_id"]), str(row["flow_id"]), "normal")
            for row in source.traffic
            if float(row.get("demand_mbps", 0.0)) > 0
        ]
    return rng.choice(candidates) if candidates else None


def _flow_touches_entity(
    scene: SceneData,
    flow: EntityRecord,
    entity_type: str,
    entity_id: str,
) -> bool:
    if entity_type == "data_flow":
        return flow.entity_id == entity_id
    if entity_type == "node":
        return entity_id in {
            str(node_id)
            for node_id in flow.relations.get("path_nodes", [])
        }
    if entity_type == "channel":
        return entity_id in {
            str(channel_id)
            for channel_id in flow.relations.get("path_channels", [])
        }
    nic = scene.entity("nic", entity_id)
    return nic is not None and _flow_touches_entity(
        scene,
        flow,
        "channel",
        str(nic.relations.get("channel", "")),
    )


def _event_placeholder(entity_type: str) -> str:
    return {
        "node": "event_node_id",
        "channel": "event_channel_id",
        "nic": "event_nic_id",
        "data_flow": "event_data_flow_id",
    }[entity_type]


def _target_placeholder(entity_type: str) -> str:
    return {
        "node": "target_node_id",
        "channel": "target_channel_id",
        "nic": "target_nic_id",
        "data_flow": "target_data_flow_id",
    }[entity_type]


def _load_source_scene(scene_dir: Path) -> EvolutionSourceScene:
    nodes, _ = _read_csv(scene_dir / "nodes.csv")
    channels, _ = _read_csv(scene_dir / "channels.csv")
    nics, _ = _read_csv(scene_dir / "nics.csv")
    traffic = _read_jsonl(scene_dir / "traffic.jsonl")
    routing_matrix = _read_routing_matrix(
        scene_dir / "routing_matrix.csv",
        len(nodes),
    )

    public_by_raw: dict[str, str] = {}
    for row in nics:
        raw_id = str(row["nic_id"])
        node_id = str(row["node"])
        interface_index = int(float(row["interface_index"]))
        public_id = f"{node_id}:IF{interface_index:06d}"
        public_by_raw[raw_id] = public_id

    faults: list[dict[str, str]] = []
    faults.extend(
        {
            "entity_type": "node",
            "entity_id": str(row["node_id"]),
            "input_entity_id": str(row["node_id"]),
            "state": str(row.get("state", "normal")),
        }
        for row in nodes
        if str(row.get("state", "normal")) == "disabled"
    )
    faults.extend(
        {
            "entity_type": "channel",
            "entity_id": str(row["channel_id"]),
            "input_entity_id": str(row["channel_id"]),
            "state": str(row.get("state", "normal")),
        }
        for row in channels
        if str(row.get("state", "normal")) in {"disabled", "degraded"}
    )
    faults.extend(
        {
            "entity_type": "nic",
            "entity_id": public_by_raw.get(str(row["nic_id"]), ""),
            "input_entity_id": str(row["nic_id"]),
            "state": str(row.get("state", "normal")),
        }
        for row in nics
        if str(row.get("state", "normal")) == "disabled"
        and public_by_raw.get(str(row["nic_id"]), "")
    )
    return EvolutionSourceScene(
        scene_dir=scene_dir,
        nodes=tuple(nodes),
        channels=tuple(channels),
        nics=tuple(nics),
        traffic=tuple(traffic),
        routing_matrix=routing_matrix,
        physical_faults=tuple(faults),
        public_nic_id_by_raw_id=public_by_raw,
    )


def _create_evolved_scene(
    source: EvolutionSourceScene,
    destination: Path,
    evolved_scene_id: str,
    evolved_scene_numeric_id: int,
    event_scenario_id: str,
    event_type: str,
    event: dict[str, Any],
) -> None:
    destination.mkdir(parents=True)
    for file_name in SCENE_INPUT_FILES:
        source_file = source.scene_dir / file_name
        if not source_file.is_file():
            raise ValueError(f"Original scene is missing {source_file}")
        shutil.copy2(source_file, destination / file_name)

    nodes, node_fields = _read_csv(destination / "nodes.csv")
    channels, channel_fields = _read_csv(destination / "channels.csv")
    nics, nic_fields = _read_csv(destination / "nics.csv")
    traffic = _read_jsonl(destination / "traffic.jsonl")
    entity_type = str(event["entity_type"])
    raw_id = str(event["input_entity_id"])
    change = str(event["change"])

    if entity_type == "node":
        row = _find_row(nodes, "node_id", raw_id)
        row["state"] = "disabled" if change == "failure" else "normal"
        event["after_state"] = row["state"]
    elif entity_type == "channel":
        row = _find_row(channels, "channel_id", raw_id)
        row["state"] = "disabled" if change == "failure" else "normal"
        row["capacity_multiplier"] = "1.0"
        event["after_state"] = row["state"]
    elif entity_type == "nic":
        row = _find_row(nics, "nic_id", raw_id)
        row["state"] = "disabled" if change == "failure" else "normal"
        event["after_state"] = row["state"]
    else:
        if change == "addition":
            if any(
                str(row.get("flow_id", "")) == raw_id
                for row in traffic
            ):
                raise ValueError(f"Data flow already exists: {raw_id}")
            traffic.append(
                {
                    "flow_id": raw_id,
                    "src": str(event["source_node_id"]),
                    "dst": str(event["destination_node_id"]),
                    "demand_mbps": float(event["demand_mbps"]),
                    "feature_model": str(event["feature_model"]),
                }
            )
            event["after_state"] = "present"
        else:
            row = _find_row(traffic, "flow_id", raw_id)
            before_demand = float(row["demand_mbps"])
            after_demand = before_demand * float(event["multiplier"])
            row["demand_mbps"] = round(after_demand, 6)
            event["before_demand_mbps"] = before_demand
            event["after_demand_mbps"] = float(row["demand_mbps"])

    _write_csv(destination / "nodes.csv", node_fields, nodes)
    _write_csv(destination / "channels.csv", channel_fields, channels)
    _write_csv(destination / "nics.csv", nic_fields, nics)
    _write_jsonl(destination / "traffic.jsonl", traffic)
    _update_evolution_metadata(
        destination / "metadata.json",
        source.scene_dir.name,
        evolved_scene_id,
        evolved_scene_numeric_id,
        event_scenario_id,
        event_type,
        event,
        nodes,
        channels,
        nics,
        traffic,
    )


def _update_evolution_metadata(
    path: Path,
    original_scene_id: str,
    evolved_scene_id: str,
    evolved_scene_numeric_id: int,
    event_scenario_id: str,
    event_type: str,
    event: dict[str, Any],
    nodes: list[dict[str, str]],
    channels: list[dict[str, str]],
    nics: list[dict[str, str]],
    traffic: list[dict[str, Any]],
) -> None:
    with path.open("r", encoding="utf-8-sig") as handle:
        metadata = json.load(handle)
    metadata["scene_name"] = evolved_scene_id
    metadata["scene_id"] = evolved_scene_numeric_id
    metadata["derivation"] = {
        "kind": DERIVATION_KIND,
        "original_scene_id": original_scene_id,
        "evolved_scene_id": evolved_scene_id,
        "event_scenario_id": event_scenario_id,
        "event_type": event_type,
        "event": deepcopy(event),
    }
    physical_faults: list[dict[str, object]] = []
    physical_faults.extend(
        {
            "entity_type": "node",
            "entity_id": str(row["node_id"]),
            "state": "disabled",
        }
        for row in nodes
        if str(row.get("state", "normal")) == "disabled"
    )
    physical_faults.extend(
        {
            "entity_type": "channel",
            "entity_id": str(row["channel_id"]),
            "state": str(row.get("state", "normal")),
            **(
                {"capacity_multiplier": float(row.get("capacity_multiplier", 1.0))}
                if str(row.get("state", "normal")) == "degraded"
                else {}
            ),
        }
        for row in channels
        if str(row.get("state", "normal")) in {"disabled", "degraded"}
    )
    physical_faults.extend(
        {
            "entity_type": "nic",
            "entity_id": str(row["nic_id"]),
            "state": "disabled",
        }
        for row in nics
        if str(row.get("state", "normal")) == "disabled"
    )
    fault_generation = metadata.setdefault("generation", {}).setdefault(
        "fault_generation",
        {},
    )
    fault_generation["selected_scenario"] = (
        "normal" if not physical_faults else "single" if len(physical_faults) == 1 else "multiple"
    )
    fault_generation["fault_count"] = len(physical_faults)
    fault_generation["faulted_entities"] = physical_faults
    summary = metadata.setdefault("summary", {})
    summary["fault_scenario"] = fault_generation["selected_scenario"]
    summary["fault_count"] = len(physical_faults)
    summary["flow_count"] = len(traffic)
    _write_json(path, metadata)


def _find_evidence_candidates(
    before: SceneData,
    after: SceneData,
    template: QuestionTemplate,
    rule: EvolutionQuestionRule,
    event_type: EvolutionEventType,
    event: dict[str, Any],
    target_label: str,
) -> list[tuple[dict[str, str], bool]]:
    event_entity_type = event_type.entity_type
    target_entity_type = rule.target_entity_type
    event_id = str(event["entity_id"])
    if (
        event_entity_type == "data_flow"
        and target_entity_type == "data_flow"
    ):
        target_ids = [event_id]
    else:
        scope_scene = (
            after
            if event_type.change == "addition"
            else before
        )
        target_ids = [
            entity.entity_id
            for entity in before.entities(target_entity_type)
            if after.entity(target_entity_type, entity.entity_id) is not None
            and scope_scene.entity_is_in_flow_scope(
                target_entity_type,
                entity.entity_id,
            )
        ]

    matching_target_ids = [
        target_id
        for target_id in target_ids
        if _evaluate_target(before, after, rule, target_id) == target_label
    ]
    candidates: list[tuple[dict[str, str], bool]] = []
    for target_id in matching_target_ids:
        replacements = {
            _event_placeholder(event_entity_type): event_id,
        }
        if event_type.change == "addition":
            replacements.update(
                {
                    "event_source_node_id": str(event["source_node_id"]),
                    "event_destination_node_id": str(
                        event["destination_node_id"]
                    ),
                    "event_demand_mbps": str(event["demand_mbps"]),
                }
            )
        elif (
            event_entity_type == "data_flow"
            and event_type.change
            in {"load_increase", "load_decrease"}
        ):
            replacements["event_rate_multiplier"] = str(
                event["multiplier"]
            )
        target_placeholder = _target_placeholder(target_entity_type)
        if target_placeholder in template_placeholder_names(
            template,
            rule,
            event_type,
        ):
            replacements[target_placeholder] = target_id
        candidates.append(
            (
                replacements,
                _target_is_related_to_event(
                    after if event_type.change == "addition" else before,
                    event_entity_type,
                    target_entity_type,
                    event_id,
                    target_id,
                ),
            )
        )
    return candidates


def _target_is_related_to_event(
    scene: SceneData,
    event_entity_type: str,
    target_entity_type: str,
    event_id: str,
    target_id: str,
) -> bool:
    if (
        event_entity_type == target_entity_type
        and event_id == target_id
    ):
        return True
    return any(
        _flow_touches_entity(
            scene,
            flow,
            event_entity_type,
            event_id,
        )
        and _flow_touches_entity(
            scene,
            flow,
            target_entity_type,
            target_id,
        )
        for flow in scene.entities("data_flow")
    )


def _evaluate_target(
    before: SceneData,
    after: SceneData,
    rule: EvolutionQuestionRule,
    target_id: str,
) -> str | None:
    target_entity_type = rule.target_entity_type
    before_entity = before.entity(target_entity_type, target_id)
    after_entity = after.entity(target_entity_type, target_id)
    if rule.comparison_kind == "after_status":
        if before_entity is not None or after_entity is None:
            return None
        after_state = after_entity.label
        if not after_state:
            return None
        return after_state
    if before_entity is None or after_entity is None:
        return None
    if rule.comparison_kind in {
        "status",
        "saturation_outcome",
        "saturation_recovery",
    }:
        before_state = before_entity.label
        after_state = after_entity.label
        if not before_state or not after_state:
            return None
        if rule.comparison_kind == "saturation_outcome":
            return (
                "saturated"
                if after_state == "saturated"
                else "not_saturated"
            )
        if rule.comparison_kind == "saturation_recovery":
            if before_state != "saturated":
                return None
            return (
                "recovered"
                if after_state != "saturated"
                else "not_recovered"
            )
        return _status_transition(before_state, after_state)

    if rule.metric_name == "maximum_directional_bandwidth_mbps":
        before_values = channel_directional_throughputs(before, before_entity)
        after_values = channel_directional_throughputs(after, after_entity)
        if before_values is None or after_values is None:
            return None
        before_value = max(before_values)
        after_value = max(after_values)
    else:
        before_value = _finite_number(
            before_entity.properties.get(rule.metric_name)
        )
        after_value = _finite_number(
            after_entity.properties.get(rule.metric_name)
        )
        if before_value is None or after_value is None:
            return None
    relative_tolerance = (
        0.0
        if rule.metric_name in EXACT_NUMERIC_METRICS
        else NUMERIC_UNCHANGED_RELATIVE_TOLERANCE
    )
    return _numeric_transition(
        before_value,
        after_value,
        relative_tolerance=relative_tolerance,
    )


def _numeric_transition(
    before: float,
    after: float,
    *,
    relative_tolerance: float = 0.0,
) -> str:
    unchanged_threshold = abs(before) * relative_tolerance
    if (
        abs(after - before) <= unchanged_threshold
        or math.isclose(before, after, rel_tol=0.0, abs_tol=1e-6)
    ):
        return "unchanged"
    return "increase" if after > before else "decrease"


def _status_transition(before: str, after: str) -> str | None:
    if not before or not after:
        return None
    if before == after:
        return "unchanged"
    if after == "normal" and before != "normal":
        return "recovered"
    return after


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


def _read_routing_matrix(
    path: Path,
    node_count: int,
) -> tuple[tuple[int, ...], ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        raw_rows = [row for row in csv.reader(handle)]
    if len(raw_rows) != node_count or any(
        len(row) != node_count
        for row in raw_rows
    ):
        raise ValueError(
            f"{path} must be a square {node_count} x {node_count} matrix"
        )
    try:
        return tuple(
            tuple(int(value) for value in row)
            for row in raw_rows
        )
    except ValueError as exc:
        raise ValueError(f"{path} contains a non-integer value") from exc


def _write_csv(
    path: Path,
    field_names: list[str],
    rows: list[dict[str, object]],
) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=field_names, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            values.append(value)
    return values


def _write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    temporary.replace(path)


def _write_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def _write_questions(path: Path, questions: list[GeneratedQuestion]) -> None:
    if not questions:
        if path.is_file():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(path, [question.to_dict() for question in questions])


def _find_row(
    rows: list[dict[str, Any]],
    id_field: str,
    entity_id: str,
) -> dict[str, Any]:
    matches = [row for row in rows if str(row.get(id_field, "")) == entity_id]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {id_field}={entity_id}")
    return matches[0]


def _is_evolution_scene(scene_dir: Path) -> bool:
    metadata_file = scene_dir / "metadata.json"
    if not metadata_file.is_file():
        return False
    try:
        with metadata_file.open("r", encoding="utf-8-sig") as handle:
            metadata = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(metadata, dict)
        and isinstance(metadata.get("derivation"), dict)
        and metadata["derivation"].get("kind") == DERIVATION_KIND
    )


def _load_evolution_plans(
    root: Path,
    event_types: tuple[EvolutionEventType, ...],
) -> list[EvolutionPlan]:
    evolution_input_root = (
        root / EVOLUTION_SCENES_DIR_NAME / INPUT_DIR_NAME
    )
    evolution_scenes_root = (
        root / EVOLUTION_SCENES_DIR_NAME / SCENES_DIR_NAME
    )
    event_type_by_id = {
        event_type.event_type_id: event_type
        for event_type in event_types
    }
    plans: list[EvolutionPlan] = []
    for metadata_file in sorted(
        evolution_input_root.glob("*/metadata.json")
    ):
        with metadata_file.open("r", encoding="utf-8-sig") as handle:
            metadata = json.load(handle)
        derivation = metadata.get("derivation")
        if (
            not isinstance(derivation, dict)
            or derivation.get("kind") != DERIVATION_KIND
        ):
            continue
        event = derivation.get("event")
        if not isinstance(event, dict):
            continue
        original_scene_id = str(derivation.get("original_scene_id", ""))
        evolved_scene_id = str(
            derivation.get("evolved_scene_id", metadata_file.parent.name)
        )
        event_scenario_id = str(
            derivation.get("event_scenario_id", "")
        )
        event_type = str(derivation.get("event_type", ""))
        if (
            not original_scene_id
            or not evolved_scene_id
            or not event_scenario_id
            or event_type not in event_type_by_id
        ):
            continue
        event_spec = event_type_by_id[event_type]
        if (
            str(event.get("entity_type", ""))
            != event_spec.entity_type
            or str(event.get("change", "")) != event_spec.change
            or evolved_scene_id != metadata_file.parent.name
        ):
            continue
        plans.append(
            EvolutionPlan(
                event_scenario_id=event_scenario_id,
                event_type=event_type,
                original_scene_id=original_scene_id,
                evolved_scene_id=evolved_scene_id,
                original_scene_file=(
                    root
                    / ORIGINAL_SCENES_DIR_NAME
                    / SCENES_DIR_NAME
                    / f"{original_scene_id}.jsonl"
                ),
                evolved_scene_file=(
                    evolution_scenes_root / f"{evolved_scene_id}.jsonl"
                ),
                evolved_input_dir=metadata_file.parent,
                event=dict(event),
            )
        )
    return plans


def _clear_evolution_question_outputs(
    evolution_root: Path,
    global_output: Path,
) -> None:
    if global_output.is_file():
        global_output.unlink()
    if not evolution_root.is_dir():
        return
    for local_output in evolution_root.glob("*/evolution_questions.jsonl"):
        local_output.unlink()


def _clear_previous_evolution_outputs(root: Path, global_output: Path) -> None:
    if global_output.is_file():
        global_output.unlink()
    if not root.is_dir():
        raise ValueError(f"scenes_root is not a directory: {root}")
    original_input_root = (
        root / ORIGINAL_SCENES_DIR_NAME / INPUT_DIR_NAME
    )
    evolution_input_root = (
        root / EVOLUTION_SCENES_DIR_NAME / INPUT_DIR_NAME
    )
    evolution_scenes_root = (
        root / EVOLUTION_SCENES_DIR_NAME / SCENES_DIR_NAME
    )
    original_input_root.mkdir(parents=True, exist_ok=True)
    evolution_input_root.mkdir(parents=True, exist_ok=True)
    evolution_scenes_root.mkdir(parents=True, exist_ok=True)
    for scene_dir in list(evolution_input_root.iterdir()):
        if not scene_dir.is_dir():
            continue
        if _is_evolution_scene(scene_dir):
            _remove_evolution_scene(scene_dir)
    for twin_file in evolution_scenes_root.glob("*.jsonl"):
        twin_file.unlink()
    for local_output in original_input_root.glob(
        "*/evolution_questions.jsonl"
    ):
        local_output.unlink()


def _remove_evolution_scene(scene_dir: Path) -> None:
    if scene_dir.is_dir() and _is_evolution_scene(scene_dir):
        shutil.rmtree(scene_dir)


def _next_scene_id(root: Path) -> tuple[int, int]:
    scene_ids: list[int] = []
    widths: list[int] = []
    for metadata_file in root.rglob("metadata.json"):
        scene_dir = metadata_file.parent
        match = SCENE_NAME_PATTERN.fullmatch(scene_dir.name)
        if match is None:
            continue
        token = match.group("scene_id")
        scene_ids.append(int(token))
        widths.append(len(token))
    return (max(scene_ids, default=0) + 1, max([4, *widths]))


def _evolved_scene_name(
    original_scene_name: str,
    scene_numeric_id: int,
    scene_id_width: int,
    event_scenario_id: str,
) -> str:
    match = SCENE_NAME_PATTERN.fullmatch(original_scene_name)
    if match is None:
        raise ValueError(
            "Original scene name does not follow '<prefix>_id<number>_<suffix>': "
            f"{original_scene_name}"
        )
    return (
        f"{match.group('prefix')}_"
        f"id{scene_numeric_id:0{scene_id_width}d}_"
        f"{match.group('suffix')}_"
        f"evo_{event_scenario_id}"
    )
