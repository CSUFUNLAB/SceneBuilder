from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import random

from .config import CategoryConfig, QuestionGeneratorConfig, load_config
from .generators import (
    AnalysisQuestionGenerator,
    EvolutionQuestionGenerator,
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


def _generator_for(category: str) -> QuestionCategoryGenerator:
    if category == "analysis":
        return AnalysisQuestionGenerator()
    if category == "evolution":
        return EvolutionQuestionGenerator()
    if category == "optimization":
        return OptimizationQuestionGenerator()
    raise ValueError(f"Unsupported question category: {category}")


def _target_counts(template: QuestionTemplate, total_count: int) -> list[tuple[str, int]]:
    if template.has_id_answer:
        return [(template.answer_values[0], total_count)]

    label_count = len(template.answer_values)
    if total_count % label_count != 0:
        raise ValueError(
            f"{template.template_id} requests {total_count} questions but has {label_count} labels; "
            "questions_per_question must be divisible by the label count"
        )
    count_per_label = total_count // label_count
    return [(label, count_per_label) for label in template.answer_values]


def _write_questions(path: Path, questions: list[GeneratedQuestion]) -> None:
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
            for scene_file in scene_files:
                scene = SceneData.from_jsonl(scene_file)
                candidate = generator.generate_candidate(scene, template, target_label, rng)
                if candidate is None:
                    continue

                questions.append(
                    GeneratedQuestion(
                        question_id=f"Q{question_number:08d}",
                        question_type=category_config.name,
                        template_id=template.template_id,
                        question=template.render(candidate.replacements),
                        label=candidate.label,
                        scene_name=scene.scene_name,
                    )
                )
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
            generated_count=len(questions),
            counts=tuple(counts),
        ),
        question_number,
    )


def run(
    config_path: str | Path,
    *,
    scenes_root: str | Path | None = None,
) -> QuestionGenerationResult:
    config: QuestionGeneratorConfig = load_config(config_path)
    root = Path(scenes_root).expanduser().resolve() if scenes_root is not None else config.scenes_root
    scene_files = discover_scene_files(root)
    rng = random.Random(config.seed)
    category_results: list[CategoryRunResult] = []
    question_number = 1

    for category_config in config.categories.values():
        if not category_config.enabled:
            continue
        result, question_number = _generate_category(
            category_config,
            scene_files,
            rng,
            question_number,
        )
        category_results.append(result)

    return QuestionGenerationResult(
        scene_count=len(scene_files),
        categories=tuple(category_results),
    )
