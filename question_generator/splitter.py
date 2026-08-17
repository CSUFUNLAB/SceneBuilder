from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import random
import shutil
import tempfile
from typing import Any

import yaml

from question_generator.config import (
    QUESTION_CATEGORIES,
    QuestionGeneratorConfig,
    load_config as load_question_config,
)


TWIN_FILE_NAME = "twin.jsonl"
SCENES_DIR_NAME = "scenes"
QUESTION_FILE_NAMES = {
    task_type: f"{task_type}_questions.jsonl"
    for task_type in QUESTION_CATEGORIES
}
SOURCE_SCENE_GROUPS = {
    "analysis": "origin",
    "evolution": "origin",
    "optimization": "opt",
}


@dataclass(frozen=True)
class DatasetSplitConfig:
    question_config: Path
    train_output_root: Path
    test_output_root: Path
    seed: int


@dataclass(frozen=True)
class TaskSplitResult:
    task_type: str
    source_questions: int
    train_questions: int
    test_questions: int
    train_scenes: int
    test_scenes: int
    templates: tuple[str, ...]


@dataclass(frozen=True)
class DatasetSplitResult:
    train_output_root: Path
    test_output_root: Path
    train_ratio: float
    tasks: tuple[TaskSplitResult, ...]


def load_dataset_split_config(
    config_path: str | Path,
) -> DatasetSplitConfig:
    path = Path(config_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(
            f"Dataset split config does not exist: {path}"
        )
    with path.open("r", encoding="utf-8-sig") as input_file:
        raw = yaml.safe_load(input_file) or {}
    if not isinstance(raw, dict):
        raise ValueError("Dataset split config must be a mapping.")

    seed = raw.get("seed", 42)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer.")

    config = DatasetSplitConfig(
        question_config=_resolve_config_path(
            raw.get("question_config", "question_generator.yaml"),
            path.parent,
            "question_config",
        ),
        train_output_root=_resolve_config_path(
            raw.get("train_output_root"),
            path.parent,
            "train_output_root",
        ),
        test_output_root=_resolve_config_path(
            raw.get("test_output_root"),
            path.parent,
            "test_output_root",
        ),
        seed=seed,
    )
    _validate_output_roots(config)
    return config


def split_generated_dataset(
    config_path: str | Path,
    *,
    train_ratio: float,
) -> DatasetSplitResult:
    config = load_dataset_split_config(config_path)
    effective_ratio = _validate_ratio(train_ratio)
    question_config = load_question_config(config.question_config)
    _validate_outputs_outside_sources(config, question_config)
    _ensure_output_roots_empty(config)

    train_stage = _create_stage(config.train_output_root)
    test_stage = _create_stage(config.test_output_root)
    try:
        task_results = _build_split(
            question_config,
            train_stage,
            test_stage,
            train_ratio=effective_ratio,
            seed=config.seed,
        )
        _replace_output_root(train_stage, config.train_output_root)
        train_stage = None
        _replace_output_root(test_stage, config.test_output_root)
        test_stage = None
    finally:
        for stage in (train_stage, test_stage):
            if stage is not None and stage.exists():
                shutil.rmtree(stage)

    return DatasetSplitResult(
        train_output_root=config.train_output_root,
        test_output_root=config.test_output_root,
        train_ratio=effective_ratio,
        tasks=task_results,
    )


def _build_split(
    question_config: QuestionGeneratorConfig,
    train_root: Path,
    test_root: Path,
    *,
    train_ratio: float,
    seed: int,
) -> tuple[TaskSplitResult, ...]:
    results: list[TaskSplitResult] = []
    for task_offset, task_type in enumerate(QUESTION_CATEGORIES):
        source_path = question_config.categories[task_type].output_file
        records = (
            _load_question_records(source_path, task_type=task_type)
            if source_path.is_file()
            else []
        )
        train_records, test_records = _split_by_template(
            records,
            train_ratio=train_ratio,
            seed=seed + task_offset,
        )
        train_scenes = _write_task_dataset(
            train_root,
            task_type,
            train_records,
            question_config=question_config,
        )
        test_scenes = _write_task_dataset(
            test_root,
            task_type,
            test_records,
            question_config=question_config,
        )
        results.append(
            TaskSplitResult(
                task_type=task_type,
                source_questions=len(records),
                train_questions=len(train_records),
                test_questions=len(test_records),
                train_scenes=train_scenes,
                test_scenes=test_scenes,
                templates=tuple(
                    sorted(
                        {
                            str(record["template_id"])
                            for record in records
                        }
                    )
                ),
            )
        )
    return tuple(results)


def _load_question_records(
    path: Path,
    *,
    task_type: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_question_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw_record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid question JSON in {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(raw_record, dict):
                raise ValueError(
                    f"Question record must be an object: {path}:{line_number}"
                )
            record = dict(raw_record)
            actual_task_type = str(
                record.get("question_type")
                or record.get("task_type")
                or ""
            ).strip()
            if actual_task_type != task_type:
                raise ValueError(
                    f"Question type {actual_task_type!r} in "
                    f"{path}:{line_number} does not match {task_type!r}."
                )
            template_id = str(record.get("template_id") or "").strip()
            question_id = str(record.get("question_id") or "").strip()
            scene_name = str(record.get("scene_name") or "").strip()
            if not template_id or not question_id or not scene_name:
                raise ValueError(
                    f"Question in {path}:{line_number} must contain "
                    "question_id, template_id, and scene_name."
                )
            if question_id in seen_question_ids:
                raise ValueError(
                    f"Duplicate question_id {question_id!r} in {path}."
                )
            seen_question_ids.add(question_id)
            records.append(record)
    return records


def _split_by_template(
    records: list[dict[str, Any]],
    *,
    train_ratio: float,
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    indices_by_template: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        template_id = str(record["template_id"])
        indices_by_template.setdefault(template_id, []).append(index)

    train_indices: set[int] = set()
    rng = random.Random(seed)
    for template_id in sorted(indices_by_template):
        indices = list(indices_by_template[template_id])
        rng.shuffle(indices)
        train_count = _train_count(len(indices), train_ratio)
        train_indices.update(indices[:train_count])

    train_records = [
        record
        for index, record in enumerate(records)
        if index in train_indices
    ]
    test_records = [
        record
        for index, record in enumerate(records)
        if index not in train_indices
    ]
    return train_records, test_records


def _train_count(question_count: int, train_ratio: float) -> int:
    if question_count <= 1:
        return question_count
    count = round(question_count * train_ratio)
    return min(question_count - 1, max(1, count))


def _write_task_dataset(
    output_root: Path,
    task_type: str,
    records: list[dict[str, Any]],
    *,
    question_config: QuestionGeneratorConfig,
) -> int:
    if not records:
        return 0

    task_root = output_root / task_type
    scenes_root = task_root / SCENES_DIR_NAME
    scenes_root.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        task_root / QUESTION_FILE_NAMES[task_type],
        records,
    )

    scene_ids = sorted(
        {
            _runtime_scene_id(record, task_type=task_type)
            for record in records
        }
    )
    source_group = SOURCE_SCENE_GROUPS[task_type]
    source_scenes_root = (
        question_config.scenes_root
        / source_group
        / SCENES_DIR_NAME
    )
    for scene_id in scene_ids:
        source_twin = source_scenes_root / f"{scene_id}.jsonl"
        if not source_twin.is_file():
            legacy_source_twin = (
                source_scenes_root / scene_id / TWIN_FILE_NAME
            )
            if legacy_source_twin.is_file():
                source_twin = legacy_source_twin
        if not source_twin.is_file():
            raise FileNotFoundError(
                f"Question dataset references a missing Twin: {source_twin}"
            )
        shutil.copy2(
            source_twin,
            scenes_root / f"{scene_id}.jsonl",
        )
    return len(scene_ids)


def _runtime_scene_id(
    record: dict[str, Any],
    *,
    task_type: str,
) -> str:
    if task_type == "evolution":
        original_scene_id = str(
            record.get("original_scene_id") or ""
        ).strip()
        if not original_scene_id:
            raise ValueError(
                "Evolution question is missing original_scene_id: "
                f"{record.get('question_id')}"
            )
        return original_scene_id
    scene_name = str(record.get("scene_name") or "").strip()
    if not scene_name:
        raise ValueError(
            f"Question is missing scene_name: {record.get('question_id')}"
        )
    return scene_name


def _write_jsonl(
    path: Path,
    records: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output_file:
        for record in records:
            output_file.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )


def _resolve_config_path(
    value: object,
    base_dir: Path,
    field_name: str,
) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required.")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _validate_ratio(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 < float(value) < 1
    ):
        raise ValueError("train ratio must be a number in (0, 1).")
    return float(value)


def _validate_output_roots(config: DatasetSplitConfig) -> None:
    train_root = config.train_output_root
    test_root = config.test_output_root
    if (
        train_root == test_root
        or train_root in test_root.parents
        or test_root in train_root.parents
    ):
        raise ValueError(
            "train_output_root and test_output_root must be separate, "
            "non-nested directories."
        )


def _validate_outputs_outside_sources(
    config: DatasetSplitConfig,
    question_config: QuestionGeneratorConfig,
) -> None:
    source_root = question_config.scenes_root
    for label, output_root in (
        ("train_output_root", config.train_output_root),
        ("test_output_root", config.test_output_root),
    ):
        if (
            output_root == source_root
            or output_root in source_root.parents
            or source_root in output_root.parents
        ):
            raise ValueError(
                f"{label} must not contain or be contained by the generated "
                f"scene source root: {source_root}"
            )


def _ensure_output_roots_empty(
    config: DatasetSplitConfig,
) -> None:
    for label, output_root in (
        ("train_output_root", config.train_output_root),
        ("test_output_root", config.test_output_root),
    ):
        if output_root.is_symlink():
            raise ValueError(
                f"{label} must not be a symbolic link: {output_root}"
            )
        if not output_root.exists():
            continue
        if not output_root.is_dir():
            raise ValueError(
                f"{label} must be a directory: {output_root}"
            )
        if next(output_root.iterdir(), None) is not None:
            raise ValueError(
                f"{label} is not empty: {output_root}; clear it explicitly "
                "before running split"
            )


def _create_stage(output_root: Path) -> Path:
    output_root.parent.mkdir(parents=True, exist_ok=True)
    return Path(
        tempfile.mkdtemp(
            prefix=f".{output_root.name}-split-",
            dir=output_root.parent,
        )
    )


def _replace_output_root(stage: Path, output_root: Path) -> None:
    if output_root.exists():
        if output_root.is_symlink() or not output_root.is_dir():
            raise ValueError(
                f"Dataset output root must be a directory: {output_root}"
            )
        if next(output_root.iterdir(), None) is not None:
            raise ValueError(
                f"Dataset output root became non-empty during split: "
                f"{output_root}"
            )
        output_root.rmdir()
    stage.replace(output_root)
