from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml

from .models import (
    EVOLUTION_EVENT_SEMANTICS,
    EvolutionEventType,
    OptimizationStrategy,
    QuestionTemplate,
    QuestionTemplateBundle,
)


SCHEMA_VERSION = 1
_PLACEHOLDER_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\$")
_ID_ANSWER_TYPES = {"node_id", "channel_id", "entity_id"}
_OPTIMIZATION_ANSWER_TYPES = {
    "routing_adjustment": "node_id",
    "channel_expansion": "channel_id",
    "fault_repair": "entity_id",
}
_TEMPLATE_ID_PREFIXES = {
    "analysis": "TA",
    "evolution": "TE",
    "optimization": "TO",
}
_REQUIRED_OPTIMIZATION_STRATEGY_IDS = frozenset(
    {
        "routing_adjustment",
        "channel_expansion",
        "fault_repair",
    }
)
_INDETERMINATE_ANSWER_LABELS = frozenset({"unknown", "unkown"})


def _mapping(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be a mapping")
    return dict(value)


def _list(value: object, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{location} must be a list")
    return list(value)


def _identifier(value: object, location: str) -> str:
    result = str(value or "").strip()
    if not result or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", result) is None:
        raise ValueError(f"{location} must be a non-empty identifier")
    return result


def _text(value: object, location: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{location} must be non-empty")
    return result


def _reject_unknown(
    value: dict[str, Any],
    allowed: set[str],
    location: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{location} has unsupported fields: {unknown}")


def _load_evolution_events(
    raw_events: object,
    template_path: Path,
    category: str,
) -> tuple[EvolutionEventType, ...]:
    if category != "evolution":
        if raw_events not in (None, []):
            raise ValueError(
                f"{template_path}: only evolution templates may define events"
            )
        return ()

    raw_list = _list(raw_events, f"{template_path}:events")
    if not raw_list:
        raise ValueError(f"{template_path}:events must not be empty")
    events: list[EvolutionEventType] = []
    seen_ids: set[str] = set()
    for index, raw_event in enumerate(raw_list, start=1):
        location = f"{template_path}:events[{index}]"
        event = _mapping(raw_event, location)
        _reject_unknown(event, {"id", "entity_type", "description"}, location)
        event_id = _identifier(event.get("id"), f"{location}.id")
        if event_id in seen_ids:
            raise ValueError(f"{location}.id duplicates {event_id}")
        if event_id not in EVOLUTION_EVENT_SEMANTICS:
            raise ValueError(f"{location}.id has unsupported evolution event: {event_id}")
        entity_type = _identifier(event.get("entity_type"), f"{location}.entity_type")
        expected_type, _ = EVOLUTION_EVENT_SEMANTICS[event_id]
        if entity_type != expected_type:
            raise ValueError(
                f"{location}.entity_type must be {expected_type} for {event_id}"
            )
        description = _text(event.get("description"), f"{location}.description")
        events.append(EvolutionEventType(event_id, entity_type, description))
        seen_ids.add(event_id)
    return tuple(events)


def _load_optimization_strategies(
    raw_strategies: object,
    template_path: Path,
    category: str,
) -> tuple[OptimizationStrategy, ...]:
    if category != "optimization":
        if raw_strategies not in (None, []):
            raise ValueError(
                f"{template_path}: only optimization templates may define "
                "strategies"
            )
        return ()

    strategies: list[OptimizationStrategy] = []
    seen_ids: set[str] = set()
    for index, raw_strategy in enumerate(
        _list(raw_strategies, f"{template_path}:strategies"),
        start=1,
    ):
        location = f"{template_path}:strategies[{index}]"
        strategy = _mapping(raw_strategy, location)
        _reject_unknown(
            strategy,
            {"id", "description"},
            location,
        )
        strategy_id = _identifier(strategy.get("id"), f"{location}.id")
        if strategy_id in seen_ids:
            raise ValueError(f"{location}.id duplicates {strategy_id}")
        seen_ids.add(strategy_id)
        description = _text(
            strategy.get("description"),
            f"{location}.description",
        )
        strategies.append(
            OptimizationStrategy(
                strategy_id=strategy_id,
                description=description,
            )
        )

    actual_ids = {strategy.strategy_id for strategy in strategies}
    if actual_ids != _REQUIRED_OPTIMIZATION_STRATEGY_IDS:
        raise ValueError(
            f"{template_path}: optimization strategies must be exactly "
            f"{sorted(_REQUIRED_OPTIMIZATION_STRATEGY_IDS)}"
        )
    return tuple(strategies)


def _validate_optimization_strategy_coverage(
    strategies: tuple[OptimizationStrategy, ...],
    templates: tuple[QuestionTemplate, ...],
    template_path: Path,
) -> None:
    strategy_ids = {strategy.strategy_id for strategy in strategies}
    unknown = sorted(
        {
            str(template.strategy)
            for template in templates
            if template.strategy not in strategy_ids
        }
    )
    if unknown:
        raise ValueError(
            f"{template_path}: optimization templates reference unknown "
            f"strategies: {unknown}"
        )
    for template in templates:
        expected = _OPTIMIZATION_ANSWER_TYPES[template.strategy]
        if template.answer_type != expected:
            raise ValueError(
                f"{template_path}: {template.template_id} answer must be "
                f"[{expected}] for {template.strategy}"
            )


def _load_answer(
    raw_answer: object,
    location: str,
) -> tuple[str, tuple[str, ...]]:
    raw_values = _list(raw_answer, location)
    if not raw_values or any(
        not isinstance(value, str) or not value.strip() for value in raw_values
    ):
        raise ValueError(f"{location} must be a non-empty list of non-empty strings")
    values = tuple(value.strip() for value in raw_values)
    normalized = [value.casefold() for value in values]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{location} values must be unique")
    if any(value.casefold() in _INDETERMINATE_ANSWER_LABELS for value in values):
        raise ValueError(
            f"{location} cannot contain an indeterminate answer label"
        )
    if _ID_ANSWER_TYPES.intersection(values):
        if len(values) != 1:
            raise ValueError(f"{location} must contain exactly one ID type or only labels")
        return values[0], values
    return "enum", values


def _load_templates(
    raw_templates: object,
    template_path: Path,
    category: str,
) -> tuple[QuestionTemplate, ...]:
    templates: list[QuestionTemplate] = []
    seen_ids: set[str] = set()
    base_fields = {"id", "question", "answer"}
    if category == "optimization":
        base_fields.add("strategy")

    for index, raw_template in enumerate(
        _list(raw_templates, f"{template_path}:templates"),
        start=1,
    ):
        location = f"{template_path}:templates[{index}]"
        item = _mapping(raw_template, location)
        _reject_unknown(item, base_fields, location)
        template_id = _identifier(item.get("id"), f"{location}.id")
        if template_id in seen_ids:
            raise ValueError(f"{location}.id duplicates {template_id}")
        seen_ids.add(template_id)
        template_id_pattern = (
            rf"{re.escape(_TEMPLATE_ID_PREFIXES[category])}[0-9]{{4}}"
        )
        if re.fullmatch(template_id_pattern, template_id) is None:
            raise ValueError(
                f"{location}.id must use "
                f"{_TEMPLATE_ID_PREFIXES[category]} followed by four digits"
            )
        question = _text(item.get("question"), f"{location}.question")
        strategy = (
            _identifier(item.get("strategy"), f"{location}.strategy")
            if category == "optimization"
            else None
        )
        placeholders = tuple(
            dict.fromkeys(_PLACEHOLDER_PATTERN.findall(question))
        )
        if not placeholders:
            raise ValueError(f"{location}.question needs at least one placeholder")
        answer_type, answer_values = _load_answer(
            item.get("answer"),
            f"{location}.answer",
        )

        templates.append(
            QuestionTemplate(
                template_id=template_id,
                category=category,
                question=question,
                answer_type=answer_type,
                answer_values=answer_values,
                placeholders=placeholders,
                strategy=strategy,
            )
        )
    return tuple(templates)


def load_template_bundle(
    path: str | Path,
    category: str,
) -> QuestionTemplateBundle:
    template_path = Path(path)
    try:
        with template_path.open("r", encoding="utf-8-sig") as handle:
            raw = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid question-template YAML in {template_path}: {exc}") from exc
    root = _mapping(raw, str(template_path))
    _reject_unknown(
        root,
        {
            "schema_version",
            "question_type",
            "events",
            "strategies",
            "templates",
        },
        str(template_path),
    )
    if root.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{template_path}: unsupported schema_version "
            f"{root.get('schema_version')}"
        )
    question_type = _identifier(
        root.get("question_type"),
        f"{template_path}:question_type",
    )
    if question_type != category:
        raise ValueError(
            f"{template_path}: question_type is {question_type}, expected {category}"
        )
    # The template selects the interventions; the built-in catalog defines
    # their supported physical semantics, not which events should be generated.
    event_types = _load_evolution_events(
        root.get("events"), template_path, category,
    )
    optimization_strategies = _load_optimization_strategies(
        root.get("strategies"),
        template_path,
        category,
    )
    templates = _load_templates(
        root.get("templates", []),
        template_path,
        category,
    )
    if category == "optimization":
        _validate_optimization_strategy_coverage(
            optimization_strategies,
            templates,
            template_path,
        )
    return QuestionTemplateBundle(
        category=category,
        templates=templates,
        event_types=event_types,
        optimization_strategies=optimization_strategies,
    )


def load_templates(path: str | Path, category: str) -> list[QuestionTemplate]:
    return list(load_template_bundle(path, category).templates)
