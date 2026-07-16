from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml


QUESTION_CATEGORIES = ("analysis", "evolution", "optimization")


@dataclass(frozen=True)
class CategoryConfig:
    name: str
    enabled: bool
    questions_per_question: int
    template_file: Path
    output_file: Path


@dataclass(frozen=True)
class QuestionGeneratorConfig:
    seed: int
    scenes_root: Path
    categories: dict[str, CategoryConfig]


def _resolve_path(value: object, base_dir: Path, name: str) -> Path:
    if value in (None, ""):
        raise ValueError(f"{name} is required")
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _load_category(name: str, raw: object, base_dir: Path) -> CategoryConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"categories.{name} must be a mapping")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"categories.{name}.enabled must be true or false")

    count = raw.get("questions_per_question", 0)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"categories.{name}.questions_per_question must be a non-negative integer")
    if enabled and count == 0:
        raise ValueError(f"categories.{name}.questions_per_question must be positive when enabled")

    return CategoryConfig(
        name=name,
        enabled=enabled,
        questions_per_question=count,
        template_file=_resolve_path(
            raw.get("template_file", f"templates/{name}.txt"),
            base_dir,
            f"categories.{name}.template_file",
        ),
        output_file=_resolve_path(
            raw.get("output_file", f"output/{name}_questions.jsonl"),
            base_dir,
            f"categories.{name}.output_file",
        ),
    )


def load_config(config_path: str | Path) -> QuestionGeneratorConfig:
    path = Path(config_path).expanduser().resolve()
    with path.open("r", encoding="utf-8-sig") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("Question generator config must be a mapping")

    seed = raw.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    categories_raw = raw.get("categories")
    if not isinstance(categories_raw, dict):
        raise ValueError("categories must be a mapping")

    unknown_categories = set(str(key) for key in categories_raw) - set(QUESTION_CATEGORIES)
    if unknown_categories:
        raise ValueError(f"Unsupported question categories: {sorted(unknown_categories)}")

    categories = {
        name: _load_category(name, categories_raw.get(name, {}), path.parent)
        for name in QUESTION_CATEGORIES
    }
    if not any(category.enabled for category in categories.values()):
        raise ValueError("At least one question category must be enabled")

    scenes_root = _resolve_path(raw.get("scenes_root"), path.parent, "scenes_root")
    return QuestionGeneratorConfig(
        seed=seed,
        scenes_root=scenes_root,
        categories=categories,
    )
