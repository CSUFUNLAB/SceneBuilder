from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


_PLACEHOLDER_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\$")
UNKNOWN_ANSWER_LABEL = "unknown"


EVOLUTION_EVENT_SEMANTICS = {
    "node_failure": ("node", "failure"),
    "node_recovery": ("node", "recovery"),
    "channel_failure": ("channel", "failure"),
    "channel_recovery": ("channel", "recovery"),
    "nic_failure": ("nic", "failure"),
    "nic_recovery": ("nic", "recovery"),
    "flow_load_change": ("data_flow", "load_change"),
    "flow_addition": ("data_flow", "addition"),
}


def evolution_event_semantics(event_type_id: str) -> tuple[str, str]:
    try:
        return EVOLUTION_EVENT_SEMANTICS[event_type_id]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported evolution event type: {event_type_id}"
        ) from exc


@dataclass(frozen=True)
class QuestionTemplate:
    template_id: str
    category: str
    question: str
    answer_type: str
    answer_values: tuple[str, ...]
    placeholders: tuple[str, ...]
    strategy: str | None = None
    answer_fields: tuple[str, ...] = ()
    answer_item_fields: tuple[str, ...] = ()
    unknown_answer: str | None = None

    @property
    def has_id_answer(self) -> bool:
        return self.answer_type in {"node_id", "channel_id", "entity_id"}

    def render(self, replacements: dict[str, str]) -> str:
        rendered = self.question
        for placeholder in self.placeholders:
            if placeholder not in replacements:
                raise ValueError(f"Missing ${placeholder}$ for {self.template_id}")
            rendered = rendered.replace(f"${placeholder}$", str(replacements[placeholder]))
        unresolved = _PLACEHOLDER_PATTERN.findall(rendered)
        if unresolved:
            raise ValueError(f"Unresolved placeholders for {self.template_id}: {unresolved}")
        return rendered


@dataclass(frozen=True)
class EvolutionEventType:
    event_type_id: str
    entity_type: str
    description: str

    @property
    def change(self) -> str:
        return evolution_event_semantics(self.event_type_id)[1]


@dataclass(frozen=True)
class OptimizationStrategy:
    strategy_id: str
    description: str


@dataclass(frozen=True)
class QuestionTemplateBundle:
    category: str
    templates: tuple[QuestionTemplate, ...]
    event_types: tuple[EvolutionEventType, ...] = ()
    optimization_strategies: tuple[OptimizationStrategy, ...] = ()


@dataclass(frozen=True)
class QuestionCandidate:
    replacements: dict[str, str]
    label: str
    evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class GeneratedQuestion:
    question_id: str
    question_type: str
    template_id: str
    question: str
    label: str
    scene_name: str
    original_scene_id: str | None = None
    evolved_scene_id: str | None = None
    evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "question_id": self.question_id,
            "question_type": self.question_type,
            "template_id": self.template_id,
            "question": self.question,
            "label": self.label,
            "scene_name": self.scene_name,
        }
        if self.original_scene_id is not None:
            value["original_scene_id"] = self.original_scene_id
        if self.evolved_scene_id is not None:
            value["evolved_scene_id"] = self.evolved_scene_id
        if self.evidence is not None:
            value["evidence"] = dict(self.evidence)
        return value


@dataclass(frozen=True)
class GenerationCount:
    template_id: str
    target_label: str
    requested: int
    generated: int

    @property
    def complete(self) -> bool:
        return self.generated == self.requested
