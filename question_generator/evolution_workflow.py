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
import time
from typing import Any, Callable

from .config import CategoryConfig, QuestionGeneratorConfig, load_config
from .evidence import infer_entity_state
from .models import (
    EvolutionEventType,
    GeneratedQuestion,
    GenerationCount,
    QuestionTemplate,
)
from .runner import (
    CategoryRunResult,
    QuestionGenerationResult,
    _target_counts,
    ensure_question_outputs_absent,
)
from .scene import EntityRecord, SceneData
from .templates import load_template_bundle


DERIVATION_KIND = "evolution"
ORIGINAL_SCENES_DIR_NAME = "origin"
EVOLUTION_SCENES_DIR_NAME = "evo"
SCENES_DIR_NAME = "scenes"
INPUT_DIR_NAME = "input"
THROUGHPUT_UNCHANGED_RELATIVE_TOLERANCE = 0.03
SCENE_NAME_PATTERN = re.compile(
    r"^(?P<prefix>.+)_id(?P<scene_id>[0-9]+)_(?P<suffix>.+)$"
)
SCENE_INPUT_FILES = (
    "nodes.csv",
    "channels.csv",
    "interfaces.csv",
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
    "TE0001": EvolutionQuestionRule("node_failure", "data_flow", "numeric", "throughput_mbps"),
    "TE0002": EvolutionQuestionRule("node_failure", "interface", "state_prediction"),
    "TE0003": EvolutionQuestionRule("node_failure", "channel", "state_prediction"),
    "TE0004": EvolutionQuestionRule("node_failure", "data_flow", "state_prediction"),
    "TE0005": EvolutionQuestionRule("interface_failure", "data_flow", "numeric", "throughput_mbps"),
    "TE0006": EvolutionQuestionRule("interface_failure", "interface", "state_prediction"),
    "TE0007": EvolutionQuestionRule("interface_failure", "channel", "state_prediction"),
    "TE0008": EvolutionQuestionRule("interface_failure", "data_flow", "state_prediction"),
    "TE0009": EvolutionQuestionRule("channel_failure", "data_flow", "numeric", "throughput_mbps"),
    "TE0010": EvolutionQuestionRule("channel_failure", "interface", "state_prediction"),
    "TE0011": EvolutionQuestionRule("channel_failure", "channel", "state_prediction"),
    "TE0012": EvolutionQuestionRule("channel_failure", "data_flow", "state_prediction"),
    "TE0013": EvolutionQuestionRule("flow_load_change", "data_flow", "numeric", "throughput_mbps"),
    "TE0014": EvolutionQuestionRule("flow_load_change", "interface", "state_prediction"),
    "TE0015": EvolutionQuestionRule("flow_load_change", "channel", "state_prediction"),
    "TE0016": EvolutionQuestionRule("flow_load_change", "data_flow", "state_prediction"),
}


_ENTITY_STATE_VALUES = {
    "channel": ("normal", "saturated"),
    "interface": ("normal", "saturated"),
    "data_flow": ("normal", "congested", "lossy", "failed"),
}


def _state_transition_answers(entity_type: str) -> tuple[str, ...]:
    states = _ENTITY_STATE_VALUES[entity_type]
    return tuple(
        f"{before}_to_{after}"
        for before in states
        for after in states
        if before != after
    ) + ("unchanged",)


@dataclass(frozen=True)
class EvolutionSourceScene:
    scene_dir: Path
    nodes: tuple[dict[str, str], ...]
    channels: tuple[dict[str, str], ...]
    interfaces: tuple[dict[str, str], ...]
    traffic: tuple[dict[str, Any], ...]
    routing_matrix: tuple[tuple[int, ...], ...]
    physical_faults: tuple[dict[str, str], ...]
    public_interface_id_by_raw_id: dict[str, str]
    saturated_flow_ids: tuple[str, ...]
    normal_flow_ids: tuple[str, ...]


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
    progress: Callable[[str], None] | None = None,
) -> EvolutionPreparation:
    started_at = time.monotonic()
    last_report_at = started_at

    def report(message: str, *, force: bool = True) -> None:
        nonlocal last_report_at
        if progress is None:
            return
        now = time.monotonic()
        if force or now - last_report_at >= 5.0:
            progress(
                f"[evolution prepare] {message} | elapsed={now - started_at:.1f}s"
            )
            last_report_at = now

    report("Loading configuration and question templates")
    config = load_config(config_path, scenes_root=scenes_root)
    category = config.categories["evolution"]
    template_bundle = load_template_bundle(
        category.template_file,
        "evolution",
    )
    _validate_templates(
        list(template_bundle.templates),
        template_bundle.event_types,
    )
    root = config.scenes_root
    if not root.is_dir():
        raise ValueError(f"scenes_root is not a directory: {root}")
    _ensure_evolution_outputs_absent(root, category)

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
    report(f"Scanning origin scenes: {original_input_root}")
    metadata_files = sorted(original_input_root.glob("*/metadata.json"))
    source_directories: list[Path] = []
    for index, metadata_file in enumerate(metadata_files, start=1):
        path = metadata_file.parent
        report(
            f"Scanning origin scenes: {index}/{len(metadata_files)} | {path.name}",
            force=False,
        )
        if (
            not _is_evolution_scene(path)
            and (original_scenes_root / f"{path.name}.jsonl").is_file()
            and (path / "labels.jsonl").is_file()
        ):
            source_directories.append(path)
    report(
        f"Origin scenes scanned: {len(metadata_files)} | "
        f"complete={len(source_directories)} | "
        f"skipped={len(metadata_files) - len(source_directories)}"
    )
    sources: list[EvolutionSourceScene] = []
    report(f"Loading source scenes: 0/{len(source_directories)}")
    for index, path in enumerate(source_directories, start=1):
        report(
            f"Loading source scenes: {index - 1}/{len(source_directories)} complete | "
            f"reading {path.name}",
            force=index == 1,
        )
        source = _load_source_scene(
            path,
            original_scenes_root / f"{path.name}.jsonl",
        )
        sources.append(source)
    report(f"Loaded source scenes: {len(sources)}/{len(source_directories)}")
    if not sources:
        raise ValueError(
            "Evolution generation requires at least one origin scene with "
            "complete twin.jsonl and labels.jsonl outputs; run "
            "'python main.py twin -t origin' first"
        )
    evolution_input_root.mkdir(parents=True, exist_ok=True)
    evolution_scenes_root.mkdir(parents=True, exist_ok=True)

    rng = random.Random(config.seed)
    options = _evolution_options(category)
    scenes_per_event = int(options["scenes_per_event"])
    plans: list[EvolutionPlan] = []
    report(
        f"Preparing derived inputs: {len(template_bundle.event_types)} change types "
        f"x up to {scenes_per_event} scenes | allocating scene IDs"
    )
    next_scene_numeric_id, scene_id_width = _next_scene_id(root)
    serial = 0
    for spec in template_bundle.event_types:
        event_type = spec.event_type_id
        event_plan_start = len(plans)
        report(f"Preparing {event_type}: 0/{scenes_per_event}")
        used_source_scene_ids: set[str] = set()
        flow_classes: list[str | None] = [None] * scenes_per_event
        if (
            spec.entity_type == "data_flow"
            and spec.change == "load_change"
        ):
            saturated_count = round(
                scenes_per_event
                * float(options["load_change_saturated_flow_ratio"])
            )
            flow_classes = [
                *("saturated" for _ in range(saturated_count)),
                *(
                    "normal"
                    for _ in range(scenes_per_event - saturated_count)
                ),
            ]
            rng.shuffle(flow_classes)
        for flow_class in flow_classes:
            report(
                f"Preparing {event_type}: {len(plans) - event_plan_start}/{scenes_per_event} "
                f"| selecting source"
                + (f" ({flow_class})" if flow_class is not None else ""),
                force=False,
            )
            candidates = [
                source
                for source in sources
                if source.scene_dir.name not in used_source_scene_ids
            ]
            rng.shuffle(candidates)
            selected: tuple[EvolutionSourceScene, dict[str, Any]] | None = None
            for source in candidates:
                report(
                    f"Preparing {event_type}: {len(plans) - event_plan_start}/{scenes_per_event} "
                    f"| checking {source.scene_dir.name}",
                    force=False,
                )
                event = _select_event(
                    source,
                    spec,
                    rng,
                    options,
                    flow_class=flow_class,
                )
                if event is not None:
                    selected = source, event
                    break
            if selected is None:
                report(
                    f"No eligible source remains for {event_type}"
                    + (f" ({flow_class})" if flow_class is not None else "")
                    + f" | prepared={len(plans) - event_plan_start}/{scenes_per_event}"
                )
                if flow_class is not None:
                    raise ValueError(
                        "Unable to generate the requested number of "
                        f"{event_type} scenes for {flow_class} flows"
                    )
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
            report(
                f"Preparing {event_type}: {len(plans) - event_plan_start}/{scenes_per_event} complete "
                f"| writing {evolved_scene_id}",
                force=False,
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
        report(
            f"Prepared {event_type}: {len(plans) - event_plan_start}/{scenes_per_event} "
            f"| total={len(plans)}"
        )

    report(f"Preparation complete: {len(plans)} scene(s)")
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
    progress: Callable[[str], None] | None = None,
) -> QuestionGenerationResult:
    config = load_config(config_path, scenes_root=scenes_root)
    category = config.categories["evolution"]
    root = config.scenes_root
    ensure_question_outputs_absent(category)
    template_bundle = load_template_bundle(
        category.template_file,
        "evolution",
    )
    templates = list(template_bundle.templates)
    _validate_templates(templates, template_bundle.event_types)
    plans = _load_evolution_plans(
        root, template_bundle.event_types, progress=progress
    )
    if not plans:
        raise ValueError(
            "No completed evolution scene pairs are available; run "
            "'python main.py twin -t evolution' first"
        )
    questions: list[GeneratedQuestion] = []
    generated_by_target: dict[tuple[str, str], int] = {}
    rng = random.Random(config.seed)
    original_scene_cache: dict[Path, SceneData] = {}
    evolved_scene_cache: dict[Path, SceneData] = {}
    evolution_options = _evolution_options(category)
    related_probability = float(
        evolution_options["related_target_probability"]
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
        for target_label, requested in _target_counts(
            template,
            category.questions_per_template,
        ):
            shuffled_plans = list(matching_plans)
            rng.shuffle(shuffled_plans)
            selected_candidates: list[
                tuple[
                    EvolutionPlan,
                    dict[str, str],
                    bool,
                    str,
                    dict[str, Any] | None,
                ]
            ] = []
            for plan in shuffled_plans:
                try:
                    before = original_scene_cache.get(plan.original_scene_file)
                    if before is None:
                        before = SceneData.from_jsonl(plan.original_scene_file)
                        original_scene_cache[plan.original_scene_file] = before
                    after = evolved_scene_cache.get(plan.evolved_scene_file)
                    if after is None:
                        after = SceneData.from_jsonl(plan.evolved_scene_file)
                        evolved_scene_cache[plan.evolved_scene_file] = after
                    scene_candidates = _find_evidence_candidates(
                        before,
                        after,
                        template,
                        rule,
                        event_type,
                        plan.event,
                        target_label,
                    )
                except (OSError, ValueError):
                    continue

                related_candidates = [
                    candidate
                    for candidate in scene_candidates
                    if candidate[1]
                ]
                other_candidates = [
                    candidate
                    for candidate in scene_candidates
                    if not candidate[1]
                ]
                prefer_related = rng.random() < related_probability
                preferred = (
                    related_candidates
                    if prefer_related
                    else other_candidates
                )
                fallback = (
                    other_candidates
                    if prefer_related
                    else related_candidates
                )
                available = preferred or fallback
                if not available:
                    continue
                replacements, related, answer_label, evidence = rng.choice(
                    available
                )
                selected_candidates.append(
                    (plan, replacements, related, answer_label, evidence)
                )
                if len(selected_candidates) == requested:
                    break

            for (
                plan,
                replacements,
                _,
                answer_label,
                evidence,
            ) in selected_candidates:
                question = GeneratedQuestion(
                    question_id=f"Q{question_number:08d}",
                    question_type="evolution",
                    template_id=template.template_id,
                    question=template.render(replacements),
                    label=answer_label,
                    scene_name=plan.evolved_scene_id,
                    original_scene_id=plan.original_scene_id,
                    evolved_scene_id=plan.evolved_scene_id,
                    evidence=evidence,
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
        for target_label, requested in _target_counts(
            template,
            category.questions_per_template,
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
        expected_answers = (
            ("increase", "unchanged", "decrease")
            if rule.comparison_kind == "numeric"
            else _state_transition_answers(rule.target_entity_type)
        )
        if template.answer_values != expected_answers:
            raise ValueError(
                f"{template.template_id} answers must be {list(expected_answers)}"
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
        "load_change_multiplier_range": (0.0, 3.0),
        "load_change_saturated_flow_ratio": 0.5,
        "related_target_probability": 0.8,
        "scenes_per_event": 3,
    }
    options.update(category.options)
    raw_range = options["load_change_multiplier_range"]
    if (
        not isinstance(raw_range, (list, tuple))
        or len(raw_range) != 2
        or any(isinstance(value, bool) for value in raw_range)
    ):
        raise ValueError(
            "evolution options.load_change_multiplier_range must contain two numbers"
        )
    lower, upper = (_finite_number(value) for value in raw_range)
    if lower is None or upper is None or not 0 <= lower <= upper or lower == upper == 1:
        raise ValueError(
            "evolution options.load_change_multiplier_range must allow "
            "a non-negative demand change"
        )
    options["load_change_multiplier_range"] = (lower, upper)
    for name in ("related_target_probability", "load_change_saturated_flow_ratio"):
        probability = _finite_number(options[name])
        if probability is None or not 0 <= probability <= 1:
            raise ValueError(f"evolution options.{name} must be in [0, 1]")
        options[name] = probability
    scenes_per_event = options["scenes_per_event"]
    if (
        isinstance(scenes_per_event, bool)
        or not isinstance(scenes_per_event, int)
        or scenes_per_event <= 0
    ):
        raise ValueError("evolution options.scenes_per_event must be a positive integer")
    return options


def _select_event(
    source: EvolutionSourceScene,
    spec: EvolutionEventType,
    rng: random.Random,
    options: dict[str, object],
    *,
    flow_class: str | None = None,
) -> dict[str, Any] | None:
    event_entity = _choose_event_entity(
        source,
        spec,
        rng,
        flow_class=flow_class,
    )
    if event_entity is None:
        return None
    public_event_id, raw_event_id, state = event_entity
    event: dict[str, Any] = {
        "entity_type": spec.entity_type,
        "entity_id": public_event_id,
        "input_entity_id": raw_event_id,
        "before_state": state,
    }

    if spec.entity_type == "data_flow":
        multiplier_range = options["load_change_multiplier_range"]
        lower, upper = multiplier_range
        event["multiplier"] = rng.uniform(float(lower), float(upper))
        row = next(
            row for row in source.traffic if str(row["flow_id"]) == raw_event_id
        )
        before_demand = float(row["demand_mbps"])
        after_demand = round(before_demand * event["multiplier"], 6)
        if not _demand_changed(before_demand, after_demand):
            return None
        if flow_class is not None:
            event["source_flow_class"] = flow_class

    return event


def _change_placeholder(
    rule: EvolutionQuestionRule,
    event_type: EvolutionEventType,
) -> str:
    name = _entity_placeholder(event_type.entity_type)
    # Two objects of the same type need separate slots, even when their IDs match.
    return f"changed_{name}" if event_type.entity_type == rule.target_entity_type else name


def template_placeholder_names(
    template: QuestionTemplate,
    rule: EvolutionQuestionRule,
    event_type: EvolutionEventType,
) -> set[str]:
    names = {
        _change_placeholder(rule, event_type),
        _entity_placeholder(rule.target_entity_type),
    }
    if event_type.entity_type == "data_flow":
        names.add("demand_mbps")
    return names


def _choose_event_entity(
    source: EvolutionSourceScene,
    spec: EvolutionEventType,
    rng: random.Random,
    *,
    flow_class: str | None = None,
) -> tuple[str, str, str] | None:
    if source.physical_faults:
        return None

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
    elif spec.entity_type == "interface":
        candidates = [
            (
                source.public_interface_id_by_raw_id[str(row["interface_id"])],
                str(row["interface_id"]),
                str(row.get("state", "normal")),
            )
            for row in source.interfaces
            if str(row.get("state", "normal")) == "normal"
            and str(row["interface_id"]) in source.public_interface_id_by_raw_id
        ]
    else:
        allowed_flow_ids = (
            set(source.saturated_flow_ids)
            if flow_class == "saturated"
            else set(source.normal_flow_ids)
            if flow_class == "normal"
            else None
        )
        candidates = [
            (str(row["flow_id"]), str(row["flow_id"]), "normal")
            for row in source.traffic
            if float(row.get("demand_mbps", 0.0)) > 0
            and (
                allowed_flow_ids is None
                or str(row["flow_id"]) in allowed_flow_ids
            )
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
    interface = scene.entity("interface", entity_id)
    return interface is not None and any(
        _flow_touches_entity(scene, flow, "channel", channel_id)
        for channel_id in interface.relations["channel"]
    )


def _entity_placeholder(entity_type: str) -> str:
    return {
        "node": "node_id",
        "channel": "channel_id",
        "interface": "interface_id",
        "data_flow": "data_flow_id",
    }[entity_type]


def _load_source_scene(
    scene_dir: Path,
    twin_file: Path,
) -> EvolutionSourceScene:
    nodes, _ = _read_csv(scene_dir / "nodes.csv")
    channels, _ = _read_csv(scene_dir / "channels.csv")
    interfaces, _ = _read_csv(scene_dir / "interfaces.csv")
    traffic = _read_jsonl(scene_dir / "traffic.jsonl")
    routing_matrix = _read_routing_matrix(
        scene_dir / "routing_matrix.csv",
        len(nodes),
    )
    twin = SceneData.from_jsonl(twin_file)
    saturated_flow_ids: list[str] = []
    normal_flow_ids: list[str] = []
    for flow in twin.entities("data_flow"):
        path_channels = [
            twin.entity("channel", str(channel_id))
            for channel_id in flow.relations.get("path_channels", [])
        ]
        path_interfaces = [
            twin.entity("interface", str(interface_id))
            for channel in path_channels
            if channel is not None
            for interface_id in channel.relations.get("connects", [])
        ]
        is_saturated = any(
            entity is not None and entity.label == "saturated"
            for entity in (*path_channels, *path_interfaces)
        )
        if is_saturated:
            saturated_flow_ids.append(flow.entity_id)
        elif flow.label == "normal":
            normal_flow_ids.append(flow.entity_id)

    public_by_raw: dict[str, str] = {}
    for row in interfaces:
        raw_id = str(row["interface_id"])
        node_id = str(row["node"])
        interface_index = int(float(row["interface_index"]))
        public_id = f"{node_id}IF{interface_index:06d}"
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
            "entity_type": "interface",
            "entity_id": public_by_raw.get(str(row["interface_id"]), ""),
            "input_entity_id": str(row["interface_id"]),
            "state": str(row.get("state", "normal")),
        }
        for row in interfaces
        if str(row.get("state", "normal")) == "disabled"
        and public_by_raw.get(str(row["interface_id"]), "")
    )
    return EvolutionSourceScene(
        scene_dir=scene_dir,
        nodes=tuple(nodes),
        channels=tuple(channels),
        interfaces=tuple(interfaces),
        traffic=tuple(traffic),
        routing_matrix=routing_matrix,
        physical_faults=tuple(faults),
        public_interface_id_by_raw_id=public_by_raw,
        saturated_flow_ids=tuple(saturated_flow_ids),
        normal_flow_ids=tuple(normal_flow_ids),
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
    interfaces, interface_fields = _read_csv(destination / "interfaces.csv")
    traffic = _read_jsonl(destination / "traffic.jsonl")
    entity_type = str(event["entity_type"])
    raw_id = str(event["input_entity_id"])

    if entity_type == "node":
        row = _find_row(nodes, "node_id", raw_id)
        row["state"] = "disabled"
        event["after_state"] = row["state"]
    elif entity_type == "channel":
        row = _find_row(channels, "channel_id", raw_id)
        row["state"] = "disabled"
        row["capacity_multiplier"] = "1.0"
        event["after_state"] = row["state"]
    elif entity_type == "interface":
        row = _find_row(interfaces, "interface_id", raw_id)
        row["state"] = "disabled"
        event["after_state"] = row["state"]
    else:
        row = _find_row(traffic, "flow_id", raw_id)
        before_demand = float(row["demand_mbps"])
        after_demand = round(before_demand * float(event["multiplier"]), 6)
        if not _demand_changed(before_demand, after_demand):
            raise ValueError("flow_load_change must set a different non-negative flow demand")
        row["demand_mbps"] = after_demand
        event["before_demand_mbps"] = before_demand
        event["after_demand_mbps"] = after_demand

    _write_csv(destination / "nodes.csv", node_fields, nodes)
    _write_csv(destination / "channels.csv", channel_fields, channels)
    _write_csv(destination / "interfaces.csv", interface_fields, interfaces)
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
        interfaces,
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
    interfaces: list[dict[str, str]],
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
            "entity_type": "interface",
            "entity_id": str(row["interface_id"]),
            "state": "disabled",
        }
        for row in interfaces
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
) -> list[tuple[dict[str, str], bool, str, dict[str, Any] | None]]:
    event_entity_type = event_type.entity_type
    target_entity_type = rule.target_entity_type
    event_id = str(event["entity_id"])
    target_ids = [
        entity.entity_id
        for entity in before.entities(target_entity_type)
        if after.entity(target_entity_type, entity.entity_id) is not None
        and before.entity_is_in_flow_scope(target_entity_type, entity.entity_id)
        and not (
            rule.comparison_kind == "numeric"
            and event_entity_type == target_entity_type
            and entity.entity_id == event_id
        )
    ]
    if rule.comparison_kind == "state_prediction" and not _event_has_state_evidence(
        before, event_type, event
    ):
        return []

    candidates = []
    for target_id in target_ids:
        answer_label = _evaluate_target(before, after, rule, target_id)
        if answer_label is None or answer_label not in template.answer_values:
            continue
        if answer_label != target_label:
            continue
        replacements = {
            _change_placeholder(rule, event_type): event_id,
            _entity_placeholder(target_entity_type): target_id,
        }
        if event_entity_type == "data_flow":
            replacements["demand_mbps"] = str(event["after_demand_mbps"])
        candidates.append((
            replacements,
            _target_is_related_to_event(
                before, event_entity_type, target_entity_type, event_id, target_id
            ),
            answer_label,
            None,
        ))
    return candidates


def _evidence_backed_entity_state(
    scene: SceneData,
    entity_type: str,
    entity_id: str,
) -> str | None:
    entity = scene.entity(entity_type, entity_id)
    if entity is None or not entity.label:
        return None
    inferred_state = infer_entity_state(scene, entity)
    return inferred_state if inferred_state == entity.label else None


def _event_has_state_evidence(
    before: SceneData,
    event_type: EvolutionEventType,
    event: dict[str, Any],
) -> bool:
    # The event is a stated physical intervention chosen from source inputs.
    # This check establishes the preceding state from public evidence. Traffic
    # silence alone cannot distinguish a node fault from failed interfaces.
    return (
        _evidence_backed_entity_state(
            before,
            event_type.entity_type,
            str(event.get("entity_id", "")),
        )
        is not None
    )


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
    if event_entity_type == target_entity_type == "data_flow":
        changed_flow = scene.entity("data_flow", event_id)
        target_flow = scene.entity("data_flow", target_id)
        if changed_flow is None or target_flow is None:
            return False
        target_channels = {
            channel.entity_id for channel in scene.channels_on_flow_path(target_flow)
        }
        for channel in scene.channels_on_flow_path(changed_flow):
            if channel.entity_id not in target_channels:
                continue
            direction = scene.flow_direction_on_channel(changed_flow, channel)
            if direction is not None and direction == scene.flow_direction_on_channel(
                target_flow, channel
            ):
                return True
        return False
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
    if before_entity is None or after_entity is None:
        return None
    if rule.comparison_kind == "state_prediction":
        before_state = _evidence_backed_entity_state(before, target_entity_type, target_id)
        after_state = _evidence_backed_entity_state(after, target_entity_type, target_id)
        allowed = _ENTITY_STATE_VALUES[target_entity_type]
        if before_state not in allowed or after_state not in allowed:
            return None
        return "unchanged" if before_state == after_state else f"{before_state}_to_{after_state}"

    before_value = _finite_number(before_entity.properties.get("throughput_mbps"))
    after_value = _finite_number(after_entity.properties.get("throughput_mbps"))
    if before_value is None or after_value is None or min(before_value, after_value) < 0:
        return None
    return _numeric_transition(before_value, after_value)


def _numeric_transition(before: float, after: float) -> str:
    # The inclusive +/-3% band is relative to the original throughput.
    # At a zero baseline, only zero remains unchanged.
    difference = abs(after - before)
    tolerance = before * THROUGHPUT_UNCHANGED_RELATIVE_TOLERANCE
    if difference <= tolerance or (
        tolerance > 0 and math.isclose(difference, tolerance, rel_tol=1e-12, abs_tol=0.0)
    ):
        return "unchanged"
    return "increase" if after > before else "decrease"


def _demand_changed(before: float, after: float) -> bool:
    if not (math.isfinite(before) and math.isfinite(after) and before > 0 and after >= 0):
        return False
    return after != before


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
    *,
    progress: Callable[[str], None] | None = None,
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
    incomplete_count = 0
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
            or evolved_scene_id != metadata_file.parent.name
        ):
            continue
        original_scene_file = (
            root / ORIGINAL_SCENES_DIR_NAME / SCENES_DIR_NAME
            / f"{original_scene_id}.jsonl"
        )
        evolved_scene_file = evolution_scenes_root / f"{evolved_scene_id}.jsonl"
        # Input-side labels are published only after successful Twin generation.
        # A Twin file alone may still be in progress or left by an interrupted run.
        required_outputs = (
            original_scene_file,
            root / ORIGINAL_SCENES_DIR_NAME / INPUT_DIR_NAME
            / original_scene_id / "labels.jsonl",
            evolved_scene_file,
            metadata_file.parent / "labels.jsonl",
        )
        if not all(path.is_file() for path in required_outputs):
            incomplete_count += 1
            continue
        if event_spec.entity_type == "data_flow":
            multiplier = _finite_number(event.get("multiplier"))
            before_demand = _finite_number(event.get("before_demand_mbps"))
            after_demand = _finite_number(event.get("after_demand_mbps"))
            demand_values_are_consistent = (
                multiplier is not None
                and before_demand is not None
                and after_demand is not None
                and math.isclose(
                    after_demand,
                    round(before_demand * multiplier, 6),
                    rel_tol=0.0,
                    abs_tol=1e-6,
                )
            )
            if (
                multiplier is None
                or multiplier < 0
                or before_demand is None
                or before_demand <= 0
                or after_demand is None
                or after_demand < 0
                or not demand_values_are_consistent
                or not _demand_changed(before_demand, after_demand)
            ):
                raise ValueError(
                    f"{metadata_file}: {event_type} requires a changed demand "
                    "and consistent before/after demand values; "
                    "clean and regenerate the evolution scenes"
                )
        plans.append(
            EvolutionPlan(
                event_scenario_id=event_scenario_id,
                event_type=event_type,
                original_scene_id=original_scene_id,
                evolved_scene_id=evolved_scene_id,
                original_scene_file=original_scene_file,
                evolved_scene_file=evolved_scene_file,
                evolved_input_dir=metadata_file.parent,
                event=dict(event),
            )
        )
    if progress is not None:
        progress(
            f"Evolution scene pairs: using {len(plans)} complete pair(s); "
            f"skipping {incomplete_count} incomplete pair(s)."
        )
    return plans


def _ensure_evolution_outputs_absent(
    root: Path,
    category: CategoryConfig,
) -> None:
    evolution_input_root = (
        root / EVOLUTION_SCENES_DIR_NAME / INPUT_DIR_NAME
    )
    evolution_scenes_root = (
        root / EVOLUTION_SCENES_DIR_NAME / SCENES_DIR_NAME
    )
    candidates = [category.output_file]
    for output_root in (evolution_input_root, evolution_scenes_root):
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
            f"Evolution output already exists: {existing}; run "
            "'python main.py clean -o twin -t evolution' first"
        )


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
