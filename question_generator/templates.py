from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml

from .models import (
    EvolutionEventType,
    QuestionTemplate,
    QuestionTemplateBundle,
)


SCHEMA_VERSION = 1
_PLACEHOLDER_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\$")
_ANSWER_TYPES = {"enum", "channel_id", "entity_id"}
_EVENT_ENTITY_TYPES = {"node", "channel", "nic", "data_flow"}
_EVENT_CHANGES = {
    "failure",
    "recovery",
    "load_increase",
    "load_decrease",
    "addition",
}
_TEMPLATE_ID_PREFIXES = {
    "analysis": "TA",
    "evolution": "TE",
    "optimization": "TO",
}


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


def _load_event_types(
    raw_events: object,
    template_path: Path,
    category: str,
) -> tuple[EvolutionEventType, ...]:
    if category != "evolution":
        if raw_events not in (None, []):
            raise ValueError(f"{template_path}: only evolution templates may define events")
        return ()

    events: list[EvolutionEventType] = []
    seen_ids: set[str] = set()
    for index, raw_event in enumerate(
        _list(raw_events, f"{template_path}:events"),
        start=1,
    ):
        location = f"{template_path}:events[{index}]"
        event = _mapping(raw_event, location)
        _reject_unknown(
            event,
            {"id", "entity_type", "change", "description"},
            location,
        )
        event_type_id = _identifier(event.get("id"), f"{location}.id")
        if event_type_id in seen_ids:
            raise ValueError(f"{location}.id duplicates {event_type_id}")
        seen_ids.add(event_type_id)
        entity_type = _identifier(
            event.get("entity_type"),
            f"{location}.entity_type",
        )
        if entity_type not in _EVENT_ENTITY_TYPES:
            raise ValueError(
                f"{location}.entity_type must be one of "
                f"{sorted(_EVENT_ENTITY_TYPES)}"
            )
        change = _identifier(event.get("change"), f"{location}.change")
        description = _text(
            event.get("description"),
            f"{location}.description",
        )
        if change not in _EVENT_CHANGES:
            raise ValueError(
                f"{location}.change must be one of {sorted(_EVENT_CHANGES)}"
            )
        if entity_type == "data_flow" and change not in {
            "load_increase",
            "load_decrease",
            "addition",
        }:
            raise ValueError(
                f"{location}: data_flow events must change load or add a flow"
            )
        if entity_type != "data_flow" and change not in {
            "failure",
            "recovery",
        }:
            raise ValueError(
                f"{location}: physical-entity events must be failure or recovery"
            )
        events.append(
            EvolutionEventType(
                event_type_id,
                entity_type,
                change,
                description,
            )
        )
    if not events:
        raise ValueError(f"{template_path}: evolution templates require events")
    return tuple(events)


def _load_answer(
    raw_answer: object,
    location: str,
) -> tuple[str, tuple[str, ...]]:
    answer = _mapping(raw_answer, location)
    _reject_unknown(answer, {"type", "values"}, location)
    answer_type = _identifier(answer.get("type"), f"{location}.type")
    if answer_type not in _ANSWER_TYPES:
        raise ValueError(
            f"{location}.type must be one of {sorted(_ANSWER_TYPES)}"
        )
    raw_values = answer.get("values", [])
    values = tuple(
        _text(value, f"{location}.values")
        for value in _list(raw_values, f"{location}.values")
    )
    if len(values) != len(set(values)):
        raise ValueError(f"{location}.values must be unique")
    if answer_type == "enum" and not values:
        raise ValueError(f"{location}: enum answers require values")
    if answer_type != "enum" and values:
        raise ValueError(f"{location}: ID answers cannot define values")
    return (
        answer_type,
        values if answer_type == "enum" else (answer_type,),
    )


def _load_templates(
    raw_templates: object,
    template_path: Path,
    category: str,
) -> tuple[QuestionTemplate, ...]:
    templates: list[QuestionTemplate] = []
    seen_ids: set[str] = set()
    base_fields = {"id", "question", "answer"}

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
        {"schema_version", "question_type", "events", "templates"},
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
    event_types = _load_event_types(
        root.get("events"),
        template_path,
        category,
    )
    templates = _load_templates(
        root.get("templates", []),
        template_path,
        category,
    )
    return QuestionTemplateBundle(
        category=category,
        templates=templates,
        event_types=event_types,
    )


def load_templates(path: str | Path, category: str) -> list[QuestionTemplate]:
    return list(load_template_bundle(path, category).templates)
