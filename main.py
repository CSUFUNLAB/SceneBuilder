from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import random
import re
import select
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Sequence

from question_generator.config import QUESTION_CATEGORIES
from question_generator.runner import (
    QuestionGenerationResult,
    clean_question_outputs,
    run as generate_questions,
)
from scene_generator.cleaner import clean
from scene_generator.config import load_config as load_scene_config
from scene_generator.runner import run as generate_scenes


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_NS3_ROOT = PROJECT_ROOT / "ns-3.44"
DEFAULT_SCENE_ROOT = PROJECT_ROOT / "generated_scenes"
DEFAULT_QUESTION_CONFIG = PROJECT_ROOT / "configs" / "question_generator.yaml"
TWIN_FILE_NAME = "twin.jsonl"
REQUIRED_BASE_SCENE_FILES = {
    "nodes.csv",
    "nics.csv",
    "routing_matrix.csv",
    "traffic.jsonl",
}
CHANNEL_SCENE_FILES = {"channels.csv", "links.csv"}
PROGRESS_RE = re.compile(
    r"NS3_PROGRESS sim_time=([0-9.eE+-]+) stop_time=([0-9.eE+-]+) events=([0-9]+)"
)
DISPLAY_REFRESH_INTERVAL = 5.0
LEGACY_RUNTIME_EVENTS_ENABLED = False
COMMANDS = ("generate", "twins", "questions", "clean")


class SceneBuilderArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        modes = ", ".join(COMMANDS)
        self.exit(
            2,
            f"error: {message}\n可用模式: {modes}\n"
            "运行 'python main.py --help' 查看完整帮助。\n",
        )


@dataclass(frozen=True)
class TwinGenerationResult:
    generated_files: tuple[Path, ...]
    failures: tuple[tuple[str, int], ...]

    @property
    def complete(self) -> bool:
        return not self.failures


def build_parser() -> argparse.ArgumentParser:
    parser = SceneBuilderArgumentParser(
        prog="SceneBuilder",
        description="Generate scenes, simulate digital twins with ns-3, and generate labeled questions.",
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{" + ",".join(COMMANDS) + "}",
    )

    generate_parser = subparsers.add_parser("generate", help="Generate network scenes")
    generate_parser.add_argument("-c", "--config", dest="scene_config", required=True)

    twins_parser = subparsers.add_parser("twins", help="Generate twins from existing scenes")
    twins_parser.add_argument(
        "scene",
        nargs="?",
        help="Relative path of one scene under generated_scenes. Omit it to process all scenes.",
    )
    _add_twin_arguments(twins_parser)

    questions_parser = subparsers.add_parser("questions", help="Generate questions from scene twins")
    question_action = questions_parser.add_mutually_exclusive_group(required=True)
    question_action.add_argument(
        "-t",
        "--type",
        dest="question_type",
        choices=QUESTION_CATEGORIES,
        help="Question type to generate.",
    )
    question_action.add_argument(
        "--clean",
        dest="clean_questions",
        action="store_true",
        help="Remove global and per-scene question JSONL files, then exit.",
    )
    questions_parser.add_argument(
        "-c",
        "--config",
        dest="question_config",
        default=str(DEFAULT_QUESTION_CONFIG),
        help="Question generator YAML config",
    )
    questions_parser.add_argument(
        "--scene-root",
        help="Override the scenes_root in the question configuration.",
    )

    clean_parser = subparsers.add_parser("clean", help="Remove generated scene directories")
    clean_parser.add_argument("-c", "--config", dest="scene_config", required=True)
    return parser


def _add_twin_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ns3-root",
        default=str(DEFAULT_NS3_ROOT),
        help="ns-3 source directory containing the ns3 launcher.",
    )
    parser.add_argument("--program", default="TwinGenerate", help="ns-3 scratch program name")
    parser.add_argument(
        "--stop-time",
        type=float,
        default=0.0,
        help="Simulation stop time in seconds. 0 uses each scene's metadata duration.",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=5.0,
        help="Progress report interval in simulated seconds. 0 disables ns-3 progress reports.",
    )
    parser.add_argument("--no-build", action="store_true", help="Skip the explicit ns-3 build step")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue simulating remaining scenes after a failed scene.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print ns-3 commands without running them")

    # Runtime events remain intentionally disabled, but the old arguments stay reserved.
    parser.add_argument("--event-groups", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--events-per-group", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--event-seed", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--event-list", default="events.jsonl", help=argparse.SUPPRESS)


def _resolve_project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _resolve_twin_scene_path(value: str | None) -> Path:
    scene_root = DEFAULT_SCENE_ROOT.resolve()
    if value is None:
        return scene_root

    relative_path = Path(value).expanduser()
    if relative_path.is_absolute():
        raise ValueError("twins scene path must be relative to generated_scenes")

    if relative_path.parts and relative_path.parts[0] == DEFAULT_SCENE_ROOT.name:
        candidate = (PROJECT_ROOT / relative_path).resolve()
    else:
        candidate = (scene_root / relative_path).resolve()

    try:
        candidate.relative_to(scene_root)
    except ValueError as exc:
        raise ValueError("twins scene path must stay inside generated_scenes") from exc
    return candidate


def resolve_event_sampling(event_groups: int, events_per_group: int) -> tuple[int, int]:
    if event_groups < 0:
        raise ValueError("--event-groups must be greater than or equal to 0")
    if events_per_group < 0:
        raise ValueError("--events-per-group must be greater than or equal to 0")
    if not LEGACY_RUNTIME_EVENTS_ENABLED and (event_groups > 0 or events_per_group > 0):
        raise ValueError(
            "Runtime event sampling is disabled; generate and simulate a separate paired scene instead."
        )
    if event_groups > 0 and events_per_group <= 0:
        raise ValueError("--events-per-group must be greater than 0 when --event-groups is set")
    return event_groups, events_per_group


def is_scene_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    names = {item.name for item in path.iterdir()}
    return REQUIRED_BASE_SCENE_FILES.issubset(names) and bool(CHANNEL_SCENE_FILES & names)


def discover_scenes(scene_root: Path) -> list[Path]:
    if is_scene_dir(scene_root):
        return [scene_root]
    if not scene_root.is_dir():
        raise ValueError(f"Scene directory does not exist: {scene_root}")
    scenes = sorted(path for path in scene_root.iterdir() if is_scene_dir(path))
    if not scenes:
        raise ValueError(f"No generated scenes found under: {scene_root}")
    return scenes


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def clear_dynamic_line(active: bool) -> None:
    if active and sys.stdout.isatty():
        print("\r\033[K", end="", flush=True)


def render_progress(
    scene_label: str | None,
    started_at: float,
    sim_time: float | None,
    stop_time: float | None,
    events: int | None,
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elapsed = format_duration(time.monotonic() - started_at)
    parts = [f"time={now}", f"elapsed={elapsed}"]
    if scene_label:
        parts.append(scene_label)
    if sim_time is not None and stop_time is not None:
        percent = 0.0 if stop_time <= 0.0 else min(100.0, sim_time / stop_time * 100.0)
        parts.append(f"sim={sim_time:.3f}/{stop_time:.3f}s {percent:5.1f}%")
    else:
        parts.append("sim=-")
    if events is not None:
        parts.append(f"sim_events={events}")
    if sys.stdout.isatty():
        print("\r\033[K" + " | ".join(parts), end="", flush=True)
    else:
        print(" | ".join(parts), flush=True)


def run_command(
    command: list[str],
    dry_run: bool,
    scene_label: str | None = None,
    cwd: Path | None = None,
) -> int:
    print("+ " + " ".join(shlex.quote(part) for part in command), flush=True)
    if dry_run:
        return 0

    started_at = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    dynamic_active = False
    sim_time: float | None = None
    stop_time: float | None = None
    events: int | None = None
    last_render_at = 0.0
    fd = process.stdout.fileno()

    while process.poll() is None:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            line = process.stdout.readline()
            if not line:
                continue
            match = PROGRESS_RE.search(line)
            if match:
                sim_time = float(match.group(1))
                stop_time = float(match.group(2))
                events = int(match.group(3))
                render_progress(scene_label, started_at, sim_time, stop_time, events)
                last_render_at = time.monotonic()
                dynamic_active = True
            else:
                clear_dynamic_line(dynamic_active)
                dynamic_active = False
                print(line, end="", flush=True)
        elif dynamic_active and sys.stdout.isatty():
            now = time.monotonic()
            if now - last_render_at >= DISPLAY_REFRESH_INTERVAL:
                render_progress(scene_label, started_at, sim_time, stop_time, events)
                last_render_at = now

    for line in process.stdout:
        match = PROGRESS_RE.search(line)
        if match:
            sim_time = float(match.group(1))
            stop_time = float(match.group(2))
            events = int(match.group(3))
            render_progress(scene_label, started_at, sim_time, stop_time, events)
            dynamic_active = True
        else:
            clear_dynamic_line(dynamic_active)
            dynamic_active = False
            print(line, end="", flush=True)
    clear_dynamic_line(dynamic_active)
    if scene_label and sys.stdout.isatty():
        render_progress(scene_label, started_at, sim_time, stop_time, events)
        print()
    return process.wait()


def event_list_path(scene: Path, event_list: str) -> Path:
    path = Path(event_list).expanduser()
    return path if path.is_absolute() else scene / path


def read_event_candidates(scene: Path, event_list: str) -> list[dict]:
    path = event_list_path(scene, event_list)
    if not path.exists():
        raise FileNotFoundError(f"Event list does not exist: {path}")

    events: list[dict] = []
    with path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Event row must be a JSON object: {path}:{line_number}")
            events.append(value)
    return events


def event_sort_key(event: dict) -> tuple[float, str]:
    try:
        event_time = float(event.get("time", 0.0))
    except (TypeError, ValueError):
        event_time = 0.0
    return event_time, str(event.get("event_id", ""))


def write_event_group(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        for event in sorted(events, key=event_sort_key):
            output_file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def build_twin_jobs(
    scene: Path,
    event_work_root: Path,
    event_groups: int,
    events_per_group: int,
    event_seed: int,
    event_list: str,
    dry_run: bool,
) -> list[tuple[int, Path | None, Path]]:
    jobs: list[tuple[int, Path | None, Path]] = [(0, None, scene / TWIN_FILE_NAME)]
    if event_groups == 0:
        return jobs
    if not LEGACY_RUNTIME_EVENTS_ENABLED:
        raise ValueError(
            "Runtime event sampling is disabled; generate and simulate a separate paired scene instead."
        )

    candidates = read_event_candidates(scene, event_list)
    if events_per_group > len(candidates):
        raise ValueError(
            f"Scene {scene.name} has only {len(candidates)} candidate event(s), "
            f"but --events-per-group={events_per_group}"
        )

    rng = random.Random(f"{event_seed}:{scene.name}")
    scene_event_work_dir = event_work_root / scene.name
    for group_id in range(1, event_groups + 1):
        sampled_events = rng.sample(candidates, events_per_group)
        event_file = scene_event_work_dir / f"{group_id}.jsonl"
        if not dry_run:
            write_event_group(event_file, sampled_events)
        jobs.append((group_id, event_file, scene / f"twin_{group_id}.jsonl"))
    return jobs


def clear_twin_outputs(scene_paths: Sequence[Path], dry_run: bool) -> tuple[Path, ...]:
    if dry_run:
        return ()

    removed: list[Path] = []
    for scene in scene_paths:
        twin_file = scene / TWIN_FILE_NAME
        if twin_file.is_file():
            twin_file.unlink()
            removed.append(twin_file)

        legacy_directory = scene / "twin"
        if not legacy_directory.is_dir():
            continue
        legacy_files = [legacy_directory / "0.jsonl"]
        legacy_files.extend(legacy_directory.glob("[1-9]*.jsonl"))
        legacy_files.extend(legacy_directory.glob("*_events.jsonl"))
        for legacy_file in dict.fromkeys(legacy_files):
            if legacy_file.is_file():
                legacy_file.unlink()
                removed.append(legacy_file)
        if not any(legacy_directory.iterdir()):
            legacy_directory.rmdir()

    return tuple(removed)


def ns3_run_argument(
    program: str,
    scene: Path,
    stop_time: float,
    progress_interval: float,
    event_file: Path | None,
    result_file: Path,
) -> str:
    parts = [program, f"--scene={scene}"]
    if event_file is not None:
        if not LEGACY_RUNTIME_EVENTS_ENABLED:
            raise ValueError("Runtime event files are disabled")
        parts.append(f"--events={event_file}")
    parts.append(f"--result={result_file}")
    if stop_time > 0.0:
        parts.append(f"--stopTime={stop_time}")
    parts.append(f"--progressInterval={progress_interval}")
    return " ".join(shlex.quote(part) for part in parts)


def run_twins(
    scene_root: str | Path,
    *,
    scenes: Sequence[Path] | None = None,
    ns3_root: str | Path = DEFAULT_NS3_ROOT,
    program: str = "TwinGenerate",
    stop_time: float = 0.0,
    progress_interval: float = 5.0,
    no_build: bool = False,
    continue_on_error: bool = False,
    dry_run: bool = False,
    event_groups: int = 0,
    events_per_group: int = 0,
    event_seed: int = 1,
    event_list: str = "events.jsonl",
) -> TwinGenerationResult:
    event_groups, events_per_group = resolve_event_sampling(event_groups, events_per_group)
    resolved_scene_root = Path(scene_root).expanduser().resolve()
    resolved_ns3_root = Path(ns3_root).expanduser().resolve()
    ns3_executable = resolved_ns3_root / "ns3"
    if not ns3_executable.is_file():
        raise ValueError(f"ns-3 launcher does not exist: {ns3_executable}")

    scene_paths = [Path(path).resolve() for path in scenes] if scenes is not None else discover_scenes(resolved_scene_root)
    invalid_scenes = [path for path in scene_paths if not is_scene_dir(path)]
    if invalid_scenes:
        raise ValueError(f"Invalid generated scene directory: {invalid_scenes[0]}")
    if not scene_paths:
        raise ValueError("No generated scenes were provided for twin generation")

    removed_twin_files = clear_twin_outputs(scene_paths, dry_run)
    if not dry_run:
        print(f"Removed {len(removed_twin_files)} existing twin file(s).", flush=True)

    if not no_build:
        rc = run_command(
            [str(ns3_executable), "build", program],
            dry_run,
            "build",
            cwd=resolved_ns3_root,
        )
        if rc != 0:
            return TwinGenerationResult((), (("build", rc),))

    failures: list[tuple[str, int]] = []
    generated_files: list[Path] = []
    total_jobs = len(scene_paths) * (event_groups + 1)
    completed_jobs = 0
    event_temp_context = (
        nullcontext(Path("/tmp/ns3-twin-events-dry-run"))
        if dry_run
        else tempfile.TemporaryDirectory(prefix="ns3-twin-events-")
    )
    with event_temp_context as event_temp_root:
        event_work_root = Path(event_temp_root)
        for scene in scene_paths:
            try:
                jobs = build_twin_jobs(
                    scene,
                    event_work_root,
                    event_groups,
                    events_per_group,
                    event_seed,
                    event_list,
                    dry_run,
                )
            except (FileNotFoundError, ValueError) as exc:
                print(str(exc), file=sys.stderr)
                failures.append((scene.name, 1))
                if not continue_on_error:
                    break
                continue

            for group_id, event_file, result_file in jobs:
                completed_jobs += 1
                scene_label = f"[{completed_jobs}/{total_jobs}] {scene.name} group={group_id}"
                print(f"{scene_label} generating", flush=True)
                rc = run_command(
                    [
                        str(ns3_executable),
                        "run",
                        ns3_run_argument(
                            program,
                            scene,
                            stop_time,
                            progress_interval,
                            event_file,
                            result_file,
                        ),
                    ],
                    dry_run,
                    scene_label,
                    cwd=resolved_ns3_root,
                )
                if rc == 0 and not dry_run and not result_file.is_file():
                    print(f"ns-3 did not create the expected twin file: {result_file}", file=sys.stderr)
                    rc = 1
                if rc != 0:
                    failures.append((f"{scene.name} group={group_id}", rc))
                    if not continue_on_error:
                        break
                else:
                    generated_files.append(result_file)
            if failures and not continue_on_error:
                break

    if failures:
        print("\nFailed twin jobs:", file=sys.stderr)
        for label, rc in failures:
            print(f"  {label} (exit code {rc})", file=sys.stderr)
    else:
        action = "Prepared" if dry_run else "Generated"
        print(f"\n{action} {len(generated_files)} twin file(s) inside scene directories.")

    return TwinGenerationResult(tuple(generated_files), tuple(failures))


def _print_question_result(result: QuestionGenerationResult) -> None:
    print(f"Twin scenes scanned: {result.scene_count}")
    for category in result.categories:
        print(f"{category.category}: {category.generated_count} questions -> {category.output_file}")
        print(f"{category.category}: distributed to {len(category.scene_output_files)} scene file(s)")
        for count in category.counts:
            if count.complete:
                continue
            print(
                f"available: {count.template_id} label={count.target_label} "
                f"target={count.requested} generated={count.generated}"
            )


def _run_twin_stage(args: argparse.Namespace, scene_root: Path, scenes: Sequence[Path] | None = None) -> TwinGenerationResult:
    return run_twins(
        scene_root,
        scenes=scenes,
        ns3_root=args.ns3_root,
        program=args.program,
        stop_time=args.stop_time,
        progress_interval=args.progress_interval,
        no_build=args.no_build,
        continue_on_error=args.continue_on_error,
        dry_run=args.dry_run,
        event_groups=args.event_groups,
        events_per_group=args.events_per_group,
        event_seed=args.event_seed,
        event_list=args.event_list,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "clean":
            output_root, removed = clean(args.scene_config)
            print(f"Removed {len(removed)} scene directories from {output_root}")
            return 0

        if args.command == "generate":
            scene_dirs = generate_scenes(args.scene_config)
            print(f"Generated {len(scene_dirs)} scene(s)")
            for scene_dir in scene_dirs:
                print(scene_dir)
            return 0

        if args.command == "twins":
            result = _run_twin_stage(args, _resolve_twin_scene_path(args.scene))
            return 0 if result.complete else 1

        if args.command == "questions":
            scenes_root = _resolve_project_path(args.scene_root) if args.scene_root else None
            if args.clean_questions:
                result = clean_question_outputs(args.question_config, scenes_root=scenes_root)
                print(
                    f"Removed {len(result.removed_files)} question file(s) "
                    f"from {result.scene_count} scene(s)"
                )
                return 0
            print(f"Question type: {args.question_type}")
            result = generate_questions(
                args.question_config,
                scenes_root=scenes_root,
                question_type=args.question_type,
            )
            _print_question_result(result)
            return 0
        raise AssertionError(f"Unhandled command: {args.command}")
    except (OSError, ValueError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
