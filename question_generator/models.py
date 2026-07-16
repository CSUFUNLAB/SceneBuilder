from __future__ import annotations

from dataclasses import dataclass
import re


_PLACEHOLDER_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\$")


@dataclass(frozen=True)
class QuestionTemplate:
    template_id: str
    category: str
    question: str
    answer_values: tuple[str, ...]
    placeholders: tuple[str, ...]

    @property
    def has_id_answer(self) -> bool:
        return len(self.answer_values) == 1 and self.answer_values[0].endswith("_id")

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
class QuestionCandidate:
    replacements: dict[str, str]
    label: str


@dataclass(frozen=True)
class GeneratedQuestion:
    question_id: str
    question_type: str
    template_id: str
    question: str
    label: str
    scene_name: str

    def to_dict(self) -> dict[str, str]:
        return {
            "question_id": self.question_id,
            "question_type": self.question_type,
            "template_id": self.template_id,
            "question": self.question,
            "label": self.label,
            "scene_name": self.scene_name,
        }


@dataclass(frozen=True)
class GenerationCount:
    template_id: str
    target_label: str
    requested: int
    generated: int

    @property
    def complete(self) -> bool:
        return self.generated == self.requested
