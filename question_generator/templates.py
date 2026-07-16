from __future__ import annotations

from pathlib import Path
import re

from .models import QuestionTemplate


_PLACEHOLDER_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\$")


def load_templates(path: str | Path, category: str) -> list[QuestionTemplate]:
    template_path = Path(path)
    templates: list[QuestionTemplate] = []

    with template_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.count("|||") != 1:
                raise ValueError(f"{template_path}:{line_number} must contain exactly one '|||' separator")

            question, raw_answers = (part.strip() for part in line.split("|||", maxsplit=1))
            if not question:
                raise ValueError(f"{template_path}:{line_number} has an empty question")
            if not (raw_answers.startswith("[") and raw_answers.endswith("]")):
                raise ValueError(f"{template_path}:{line_number} answers must use [answer, ...] format")

            answer_values = tuple(
                value.strip()
                for value in raw_answers[1:-1].split(",")
                if value.strip()
            )
            if not answer_values:
                raise ValueError(f"{template_path}:{line_number} must declare at least one answer")

            template_index = len(templates) + 1
            placeholders = tuple(dict.fromkeys(_PLACEHOLDER_PATTERN.findall(question)))
            templates.append(
                QuestionTemplate(
                    template_id=f"{category}_{template_index:03d}",
                    category=category,
                    question=question,
                    answer_values=answer_values,
                    placeholders=placeholders,
                )
            )

    return templates
