from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import random
from typing import Sequence

from .config import QUESTION_CATEGORIES, CategoryConfig, QuestionGeneratorConfig, load_config
from .generators import (
    AnalysisQuestionGenerator,
    OptimizationQuestionGenerator,
)
from .generators.base import QuestionCategoryGenerator
from .models import GeneratedQuestion, GenerationCount, QuestionTemplate
from .scene import SceneData, discover_scene_files
from .templates import load_templates


@dataclass(frozen=True)
class CategoryRunResult:
    category: str
    output_file: Path
    scene_output_files: tuple[Path, ...]
    generated_count: int
    counts: tuple[GenerationCount, ...]

    @property
    def complete(self) -> bool:
        return all(count.complete for count in self.counts)


@dataclass(frozen=True)
class QuestionGenerationResult:
    scene_count: int
    categories: tuple[CategoryRunResult, ...]

    @property
    def complete(self) -> bool:
        return all(category.complete for category in self.categories)


@dataclass(frozen=True)
class QuestionCleanupResult:
    scenes_root: Path
    scene_count: int
    removed_files: tuple[Path, ...]


def _scene_question_file_names() -> tuple[str, ...]:
    return tuple(f"{category}_questions.jsonl" for category in QUESTION_CATEGORIES)


def _clear_scene_question_files(
    scene_directories: list[Path] | tuple[Path, ...],
    categories: tuple[str, ...] = QUESTION_CATEGORIES,
) -> list[Path]:
    removed: list[Path] = []
    file_names = tuple(f"{category}_questions.jsonl" for category in categories)
    for scene_directory in scene_directories:
        for file_name in file_names:
            path = scene_directory / file_name
            if path.is_file():
                path.unlink()
                removed.append(path)
    return removed


def _discover_scene_directories_for_cleanup(root: Path) -> list[Path]:
    if not root.is_dir():
        raise ValueError(f"scenes_root is not a directory: {root}")

    known_files = _scene_question_file_names()
    if (root / "metadata.json").is_file() or (root / "twin.jsonl").is_file() or any(
        (root / file_name).is_file() for file_name in known_files
    ):
        return [root]

    scene_directories: set[Path] = set()
    for file_name in ("metadata.json", "twin.jsonl", *known_files):
        scene_directories.update(path.parent for path in root.rglob(file_name))
    return sorted(scene_directories, key=lambda path: str(path.relative_to(root)))


def clean_question_outputs(
    config_path: str | Path,
    *,
    scenes_root: str | Path | None = None,
    categories: Sequence[str] | None = None,
) -> QuestionCleanupResult:
    config = load_config(config_path)
    selected_categories = (
        QUESTION_CATEGORIES
        if categories is None
        else tuple(dict.fromkeys(categories))
    )
    invalid_category = next(
        (
            category
            for category in selected_categories
            if category not in QUESTION_CATEGORIES
        ),
        None,
    )
    if invalid_category is not None:
        raise ValueError(
            f"Unknown question category for cleanup: {invalid_category}"
        )
    root = Path(scenes_root).expanduser().resolve() if scenes_root is not None else config.scenes_root
    scene_directories = (
        _discover_scene_directories_for_cleanup(root)
        if root.is_dir()
        else []
    )
    removed = _clear_scene_question_files(
        scene_directories,
        selected_categories,
    )

    for category_name in selected_categories:
        category = config.categories[category_name]
        output_file = category.output_file
        if output_file.is_file():
            output_file.unlink()
            removed.append(output_file)

    return QuestionCleanupResult(
        scenes_root=root,
        scene_count=len(scene_directories),
        removed_files=tuple(removed),
    )


def ensure_question_outputs_absent(
    category_config: CategoryConfig,
) -> None:
    artifacts = [category_config.output_file]
    group_root = category_config.output_file.parent
    if group_root.is_dir():
        artifacts.extend(
            group_root.rglob(
                f"{category_config.name}_questions.jsonl"
            )
        )
    existing = next(
        (
            path
            for path in dict.fromkeys(artifacts)
            if path.is_file() or path.is_symlink()
        ),
        None,
    )
    if existing is not None:
        raise ValueError(
            f"Question output already exists: {existing}; run "
            f"'python main.py clean -o questions -t {category_config.name}' first"
        )


def _generator_for(category: str) -> QuestionCategoryGenerator:
    if category == "analysis":
        return AnalysisQuestionGenerator()
    if category == "evolution":
        raise ValueError(
            "Evolution questions create paired scenes and Twins; "
            "run them through 'python main.py questions -t evolution'"
        )
    if category == "optimization":
        return OptimizationQuestionGenerator()
    raise ValueError(f"Unsupported question category: {category}")


def _target_counts(template: QuestionTemplate, total_count: int) -> list[tuple[str, int]]:
    unknown_answer = template.unknown_answer
    if not template.answer_values:
        return (
            [("", total_count)]
            if unknown_answer is None
            else [(unknown_answer, total_count)]
        )
    if template.has_id_answer:
        targets = [template.answer_values[0]]
        if unknown_answer is not None:
            targets.append(unknown_answer)
    else:
        targets = list(template.answer_values)
        if unknown_answer is not None:
            targets.append(unknown_answer)

    label_count = len(targets)
    if total_count % label_count != 0:
        raise ValueError(
            f"{template.template_id} requests {total_count} questions but has {label_count} labels; "
            "questions_per_question must be divisible by the label count"
        )
    count_per_label = total_count // label_count
    return [(label, count_per_label) for label in targets]


def _write_questions(path: Path, questions: list[GeneratedQuestion]) -> None:
    if not questions:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
        for question in questions:
            handle.write(json.dumps(question.to_dict(), ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    temporary_path.replace(path)


def _generate_category(
    category_config: CategoryConfig,
    scene_files: list[Path],
    rng: random.Random,
    next_question_number: int,
) -> tuple[CategoryRunResult, int]:
    templates = load_templates(category_config.template_file, category_config.name)
    if not templates:
        raise ValueError(f"Enabled category {category_config.name} has no question templates")

    generator = _generator_for(category_config.name)
    questions: list[GeneratedQuestion] = []
    counts: list[GenerationCount] = []
    question_number = next_question_number

    for template in templates:
        for target_label, requested_count in _target_counts(
            template,
            category_config.questions_per_question,
        ):
            generated_count = 0
            candidate_scene_files = list(scene_files)
            rng.shuffle(candidate_scene_files)
            for scene_file in candidate_scene_files:
                scene = SceneData.from_jsonl(scene_file)
                candidate = generator.generate_candidate(scene, template, target_label, rng)
                if candidate is None:
                    continue

                question = GeneratedQuestion(
                    question_id=f"Q{question_number:08d}",
                    question_type=category_config.name,
                    template_id=template.template_id,
                    question=template.render(candidate.replacements),
                    label=candidate.label,
                    scene_name=scene.scene_name,
                    evidence=candidate.evidence,
                )
                questions.append(question)
                question_number += 1
                generated_count += 1
                if generated_count == requested_count:
                    break

            counts.append(
                GenerationCount(
                    template_id=template.template_id,
                    target_label=target_label,
                    requested=requested_count,
                    generated=generated_count,
                )
            )

    _write_questions(category_config.output_file, questions)
    return (
        CategoryRunResult(
            category=category_config.name,
            output_file=category_config.output_file,
            scene_output_files=(),
            generated_count=len(questions),
            counts=tuple(counts),
        ),
        question_number,
    )


def run(
    config_path: str | Path,
    *,
    scenes_root: str | Path | None = None,
    scene_files: Sequence[str | Path] | None = None,
    question_type: str | None = None,
) -> QuestionGenerationResult:
    config: QuestionGeneratorConfig = load_config(config_path)
    root = Path(scenes_root).expanduser().resolve() if scenes_root is not None else config.scenes_root

    if question_type is not None:
        if question_type not in QUESTION_CATEGORIES:
            raise ValueError(
                f"Unsupported question type {question_type!r}; choose from {', '.join(QUESTION_CATEGORIES)}"
            )
        category_configs = [config.categories[question_type]]
    else:
        category_configs = [category for category in config.categories.values() if category.enabled]

    for category_config in category_configs:
        ensure_question_outputs_absent(category_config)
    if scene_files is None:
        selected_scene_files = discover_scene_files(root)
    else:
        selected_scene_files = list(
            dict.fromkeys(
                Path(scene_file).expanduser().resolve()
                for scene_file in scene_files
            )
        )
        missing_scene_file = next(
            (
                scene_file
                for scene_file in selected_scene_files
                if not scene_file.is_file()
            ),
            None,
        )
        if missing_scene_file is not None:
            raise ValueError(
                f"Scene twin file does not exist: {missing_scene_file}"
            )
        if not selected_scene_files:
            raise ValueError("No completed scene twin files were provided")
    rng = random.Random(config.seed)
    category_results: list[CategoryRunResult] = []
    question_number = 1

    for category_config in category_configs:
        result, question_number = _generate_category(
            category_config,
            selected_scene_files,
            rng,
            question_number,
        )
        category_results.append(result)

    return QuestionGenerationResult(
        scene_count=len(selected_scene_files),
        categories=tuple(category_results),
    )
