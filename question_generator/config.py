from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import yaml


QUESTION_CATEGORIES = ("analysis", "evolution", "optimization")
QUESTION_DATASET_DIRS = {
    "analysis": "origin",
    "evolution": "evo",
    "optimization": "opt",
}


@dataclass(frozen=True)
class CategoryConfig:
    name: str
    enabled: bool
    questions_per_template: int
    template_file: Path
    output_file: Path
    options: dict[str, Any]


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


def _load_category(
    name: str,
    raw: object,
    base_dir: Path,
    count: int,
    configured_scenes_root: Path,
    scenes_root: Path,
) -> CategoryConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"categories.{name} must be a mapping")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"categories.{name}.enabled must be true or false")

    options = raw.get("options", {})
    if not isinstance(options, dict):
        raise ValueError(f"categories.{name}.options must be a mapping")

    output_file = _resolve_path(
        raw.get(
            "output_file",
            configured_scenes_root
            / QUESTION_DATASET_DIRS[name]
            / f"{name}_questions.jsonl",
        ),
        base_dir,
        f"categories.{name}.output_file",
    )
    if scenes_root != configured_scenes_root:
        try:
            relative_output = output_file.relative_to(configured_scenes_root)
        except ValueError:
            pass
        else:
            output_file = scenes_root / relative_output

    return CategoryConfig(
        name=name,
        enabled=enabled,
        questions_per_template=count,
        template_file=_resolve_path(
            raw.get("template_file", f"templates/{name}.yaml"),
            base_dir,
            f"categories.{name}.template_file",
        ),
        output_file=output_file,
        options=dict(options),
    )


def load_config(
    config_path: str | Path,
    *,
    scenes_root: str | Path | None = None,
) -> QuestionGeneratorConfig:
    path = Path(config_path).expanduser().resolve()
    with path.open("r", encoding="utf-8-sig") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("Question generator config must be a mapping")

    seed = raw.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    count = raw.get("questions_per_template", 20)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("questions_per_template must be a positive integer")

    configured_scenes_root = _resolve_path(
        raw.get("scenes_root"), path.parent, "scenes_root"
    )
    effective_scenes_root = (
        Path(scenes_root).expanduser().resolve()
        if scenes_root is not None
        else configured_scenes_root
    )

    categories_raw = raw.get("categories")
    if not isinstance(categories_raw, dict):
        raise ValueError("categories must be a mapping")

    unknown_categories = set(str(key) for key in categories_raw) - set(QUESTION_CATEGORIES)
    if unknown_categories:
        raise ValueError(f"Unsupported question categories: {sorted(unknown_categories)}")

    categories = {
        name: _load_category(
            name,
            categories_raw.get(name, {}),
            path.parent,
            count,
            configured_scenes_root,
            effective_scenes_root,
        )
        for name in QUESTION_CATEGORIES
    }
    if not any(category.enabled for category in categories.values()):
        raise ValueError("At least one question category must be enabled")

    return QuestionGeneratorConfig(
        seed=seed,
        scenes_root=effective_scenes_root,
        categories=categories,
    )
