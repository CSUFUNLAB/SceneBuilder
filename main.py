from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
import select
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_NS3_ROOT = PROJECT_ROOT / "ns-3"
DEFAULT_SCENE_CONFIG = PROJECT_ROOT / "configs" / "example.yaml"
DEFAULT_QUESTION_CONFIG = PROJECT_ROOT / "configs" / "question_generator.yaml"
DEFAULT_DATASET_SPLIT_CONFIG = (
    PROJECT_ROOT / "configs" / "dataset_split.yaml"
)
TWIN_FILE_NAME = "twin.jsonl"
LABEL_FILE_NAME = "labels.jsonl"
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
QUESTION_CATEGORIES = ("analysis", "evolution", "optimization")
TWIN_TYPES = ("origin", "evo", "opt")
QUESTION_CATEGORY_BY_TWIN_TYPE = {
    "origin": "analysis",
    "evo": "evolution",
    "opt": "optimization",
}
TWIN_COMMAND_TYPE_BY_TYPE = {
    "origin": "origin",
    "evo": "evolution",
    "opt": "optimization",
}
TWIN_COMMAND_TYPES = tuple(TWIN_COMMAND_TYPE_BY_TYPE.values())
TWIN_TYPE_BY_COMMAND_TYPE = {
    command_type: twin_type
    for twin_type, command_type in TWIN_COMMAND_TYPE_BY_TYPE.items()
}
SCENES_DIR_NAME = "scenes"
INPUT_DIR_NAME = "input"
COMMANDS = ("initial", "scenes", "twin", "questions", "split", "clean")


def _load_project_dependencies() -> None:
    global QuestionGenerationResult
    global clean_question_outputs
    global ensure_question_outputs_absent
    global generate_evolution_questions
    global generate_questions
    global generate_scenes
    global load_question_config
    global load_scene_config
    global prepare_evolution_scenes
    global reset_output_root
    global split_generated_dataset

    from question_generator.config import load_config as load_question_config
    from question_generator.evolution_workflow import (
        generate_evolution_questions,
        prepare_evolution_scenes,
    )
    from question_generator.runner import (
        QuestionGenerationResult,
        clean_question_outputs,
        ensure_question_outputs_absent,
        run as generate_questions,
    )
    from question_generator.splitter import split_generated_dataset
    from scene_generator.cleaner import reset_output_root
    from scene_generator.config import load_config as load_scene_config
    from scene_generator.runner import run as generate_scenes


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


@dataclass(frozen=True)
class Ns3InitializationResult:
    archive: Path
    source_prefix: PurePosixPath
    destination: Path
    extracted_entries: int
    skipped_entries: int


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

    initial_parser = subparsers.add_parser(
        "initial",
        help="Extract an ns-3 source archive into the project",
    )
    initial_parser.add_argument(
        "archive",
        nargs="?",
        help=(
            "Path to an ns-3 source archive, for example "
            "./ns-allinone-3.48.tar.bz2. Prompts when omitted."
        ),
    )

    scenes_parser = subparsers.add_parser("scenes", help="Generate network scenes")
    scenes_parser.add_argument("-c", "--config", dest="scene_config", required=True)

    twin_parser = subparsers.add_parser(
        "twin",
        help="Generate origin, evolution, or optimization Twins",
    )
    twin_parser.add_argument(
        "-t",
        "--type",
        dest="twin_type",
        required=True,
        choices=TWIN_COMMAND_TYPES,
        help="Twin type to generate.",
    )
    twin_parser.add_argument(
        "-c",
        "--config",
        dest="question_config",
        default=str(DEFAULT_QUESTION_CONFIG),
        help="Question generator YAML config",
    )
    twin_parser.add_argument(
        "--scene-root",
        help="Override the scenes_root in the question configuration.",
    )
    _add_twin_arguments(twin_parser)

    questions_parser = subparsers.add_parser(
        "questions",
        help="Generate questions from completed Twins",
    )
    questions_parser.add_argument(
        "-t",
        "--type",
        dest="question_type",
        required=True,
        choices=QUESTION_CATEGORIES,
        help="Question type to generate.",
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
    split_parser = subparsers.add_parser(
        "split",
        help=(
            "Split generated questions by template into task-specific "
            "training and test datasets"
        ),
    )
    split_parser.add_argument(
        "-c",
        "--config",
        dest="dataset_split_config",
        default=str(DEFAULT_DATASET_SPLIT_CONFIG),
        help="Dataset split YAML config",
    )
    split_parser.add_argument(
        "-r",
        "--train-ratio",
        type=float,
        required=True,
        help="Required training-question ratio in the open interval (0, 1).",
    )
    clean_parser = subparsers.add_parser(
        "clean",
        help="Explicitly clean all outputs, Twins, or questions",
    )
    clean_parser.add_argument(
        "-o",
        "--object",
        dest="clean_object",
        choices=("scenes", "twin", "questions"),
        help=(
            "Choose scenes, twin, or questions as the cleanup object. "
            "Omit to clean all generated outputs."
        ),
    )
    clean_parser.add_argument(
        "-t",
        "--type",
        dest="clean_type",
        choices=tuple(
            dict.fromkeys((*TWIN_COMMAND_TYPES, *QUESTION_CATEGORIES))
        ),
        help=(
            "For '-o twin', select origin/evolution/optimization; for "
            "'-o questions', select analysis/evolution/optimization. "
            "Omit to clean every type of the selected object."
        ),
    )
    clean_parser.add_argument(
        "-c",
        "--config",
        dest="clean_config",
        help=(
            "Scene config for full/scenes clean, or question config when "
            "cleaning Twins or questions."
        ),
    )
    clean_parser.add_argument(
        "--scene-root",
        help="Override scenes_root when cleaning Twins or questions.",
    )
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
        help=(
            "Absolute simulation stop time in seconds. 0 uses the application "
            "start time plus each scene's metadata duration."
        ),
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


def _archive_member_path(member: tarfile.TarInfo) -> PurePosixPath:
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe path in ns-3 archive: {member.name}")
    return path


def _find_ns3_source_prefix(
    members: Sequence[tarfile.TarInfo],
) -> tuple[PurePosixPath, PurePosixPath]:
    members_by_path: dict[PurePosixPath, list[tarfile.TarInfo]] = {}
    for member in members:
        path = _archive_member_path(member)
        members_by_path.setdefault(path, []).append(member)

    candidates: list[tuple[PurePosixPath, PurePosixPath]] = []
    for path, path_members in members_by_path.items():
        if path.name != "ns3" or not any(member.isfile() for member in path_members):
            continue
        prefix = path.parent
        if (
            prefix / "CMakeLists.txt" in members_by_path
            and prefix / "VERSION" in members_by_path
        ):
            candidates.append((prefix, path))

    if not candidates:
        raise ValueError(
            "The archive does not contain an ns-3 source root with ns3, "
            "CMakeLists.txt, and VERSION"
        )
    if len(candidates) > 1:
        roots = ", ".join(str(prefix) for prefix, _ in candidates)
        raise ValueError(f"The archive contains multiple ns-3 source roots: {roots}")
    return candidates[0]


def _validate_relative_symlink(
    member_path: PurePosixPath,
    link_name: str,
) -> None:
    link_path = PurePosixPath(link_name)
    if link_path.is_absolute():
        raise ValueError(
            f"Unsafe absolute symbolic link in ns-3 archive: "
            f"{member_path} -> {link_name}"
        )

    resolved_parts: list[str] = list(member_path.parent.parts)
    for part in link_path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not resolved_parts:
                raise ValueError(
                    f"Symbolic link escapes the ns-3 destination: "
                    f"{member_path} -> {link_name}"
                )
            resolved_parts.pop()
        else:
            resolved_parts.append(part)


def _ensure_safe_parent(destination: Path, target: Path) -> None:
    relative_parent = target.parent.relative_to(destination)
    current = destination
    for part in relative_parent.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(
                f"Refusing to extract through a symbolic-link directory: {current}"
            )
        if current.exists():
            if not current.is_dir():
                raise NotADirectoryError(
                    f"Archive output parent is not a directory: {current}"
                )
            continue
        current.mkdir()


def _extract_ns3_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    relative_path: PurePosixPath,
    destination: Path,
) -> bool:
    target = destination.joinpath(*relative_path.parts)
    if target.exists() or target.is_symlink():
        return False

    _ensure_safe_parent(destination, target)
    safe_mode = member.mode & 0o755

    if member.isdir():
        target.mkdir()
        target.chmod(safe_mode or 0o755)
        return True

    if member.issym():
        os.symlink(member.linkname, target)
        return True

    source = archive.extractfile(member)
    if source is None:
        raise ValueError(f"Cannot read archive member: {member.name}")
    try:
        with source, target.open("xb") as output:
            shutil.copyfileobj(source, output)
        target.chmod(safe_mode or 0o644)
        os.utime(target, (member.mtime, member.mtime))
    except BaseException:
        if target.exists() and not target.is_symlink():
            target.unlink()
        raise
    return True


def initialize_ns3(
    archive_value: str | Path | None,
    destination: str | Path = DEFAULT_NS3_ROOT,
) -> Ns3InitializationResult:
    destination_path = Path(destination).expanduser()
    if destination_path.is_symlink():
        raise ValueError(
            f"Refusing to initialize a symbolic-link ns-3 directory: "
            f"{destination_path}"
        )
    destination_path = destination_path.resolve()
    launcher = destination_path / "ns3"
    if launcher.exists() or launcher.is_symlink():
        raise ValueError(
            f"ns-3 has already been initialized: {launcher}; "
            "the initial command cannot be run again"
        )

    raw_archive = str(archive_value).strip() if archive_value is not None else ""
    if not raw_archive:
        try:
            raw_archive = input("请输入 ns-3 压缩包路径: ").strip()
        except EOFError as exc:
            raise ValueError("No ns-3 archive path was provided") from exc
    if not raw_archive:
        raise ValueError("ns-3 archive path cannot be empty")

    archive_path = Path(raw_archive).expanduser().resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"ns-3 archive does not exist: {archive_path}")

    try:
        archive = tarfile.open(archive_path, mode="r:*")
    except tarfile.TarError as exc:
        raise ValueError(f"Invalid or unsupported ns-3 archive: {archive_path}") from exc

    with archive:
        members = archive.getmembers()
        source_prefix, launcher_member_path = _find_ns3_source_prefix(members)
        selected: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
        for member in members:
            member_path = _archive_member_path(member)
            try:
                relative_path = member_path.relative_to(source_prefix)
            except ValueError:
                continue
            if relative_path == PurePosixPath("."):
                continue
            if not (
                member.isdir()
                or member.isfile()
                or member.islnk()
                or member.issym()
            ):
                raise ValueError(
                    f"Unsupported special file in ns-3 archive: {member.name}"
                )
            if member.issym():
                _validate_relative_symlink(relative_path, member.linkname)
            selected.append((member, relative_path))

        if not selected:
            raise ValueError("The ns-3 source root in the archive is empty")

        relative_launcher = launcher_member_path.relative_to(source_prefix)
        selected.sort(key=lambda item: item[1] == relative_launcher)
        destination_path.mkdir(parents=True, exist_ok=True)
        extracted_entries = 0
        skipped_entries = 0
        for member, relative_path in selected:
            if _extract_ns3_member(
                archive,
                member,
                relative_path,
                destination_path,
            ):
                extracted_entries += 1
            else:
                skipped_entries += 1

    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise ValueError(
            f"Initialization did not create an executable ns-3 launcher: {launcher}"
        )
    return Ns3InitializationResult(
        archive=archive_path,
        source_prefix=source_prefix,
        destination=destination_path,
        extracted_entries=extracted_entries,
        skipped_entries=skipped_entries,
    )


def _require_ns3_initialized(destination: str | Path | None = None) -> None:
    ns3_root = Path(
        destination if destination is not None else DEFAULT_NS3_ROOT
    ).expanduser().resolve()
    launcher = ns3_root / "ns3"
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise ValueError(
            "ns-3 尚未初始化，请先运行: "
            "python main.py initial <ns-3压缩包路径>"
        )


def _resolve_project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


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
    scenes = sorted(
        {
            metadata_file.parent
            for metadata_file in scene_root.rglob("metadata.json")
            if is_scene_dir(metadata_file.parent)
        },
        key=lambda path: str(path.relative_to(scene_root)),
    )
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
    twin_output_root: Path,
    event_work_root: Path,
    event_groups: int,
    events_per_group: int,
    event_seed: int,
    event_list: str,
    dry_run: bool,
) -> list[tuple[int, Path | None, Path]]:
    jobs: list[tuple[int, Path | None, Path]] = [
        (0, None, twin_output_root / f"{scene.name}.jsonl")
    ]
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
        jobs.append(
            (
                group_id,
                event_file,
                twin_output_root / f"{scene.name}_{group_id}.jsonl",
            )
        )
    return jobs


def generated_label_path_for_twin(twin_file: Path) -> Path:
    if twin_file.name == TWIN_FILE_NAME:
        return twin_file.with_name(LABEL_FILE_NAME)
    if twin_file.stem.startswith("twin_"):
        suffix = twin_file.stem.removeprefix("twin_")
        return twin_file.with_name(f"labels_{suffix}.jsonl")
    return twin_file.with_name(f"{twin_file.stem}_labels.jsonl")


# Kept for callers that used the former helper name.
label_path_for_twin = generated_label_path_for_twin


def existing_twin_outputs(
    scene_paths: Sequence[Path],
    twin_output_root: Path,
) -> tuple[Path, ...]:
    existing: list[Path] = []
    for scene in scene_paths:
        twin_file = twin_output_root / f"{scene.name}.jsonl"
        for output_file in (
            twin_file,
            generated_label_path_for_twin(twin_file),
            scene / LABEL_FILE_NAME,
            scene / TWIN_FILE_NAME,
        ):
            if output_file.is_file() or output_file.is_symlink():
                existing.append(output_file)
    return tuple(dict.fromkeys(existing))


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
    twin_output_root: str | Path | None = None,
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
    resolved_twin_output_root = (
        Path(twin_output_root).expanduser().resolve()
        if twin_output_root is not None
        else resolved_scene_root.parent / SCENES_DIR_NAME
        if resolved_scene_root.name == INPUT_DIR_NAME
        else resolved_scene_root
    )
    resolved_ns3_root = Path(ns3_root).expanduser().resolve()
    ns3_executable = resolved_ns3_root / "ns3"

    scene_paths = [Path(path).resolve() for path in scenes] if scenes is not None else discover_scenes(resolved_scene_root)
    invalid_scenes = [path for path in scene_paths if not is_scene_dir(path)]
    if invalid_scenes:
        raise ValueError(f"Invalid generated scene directory: {invalid_scenes[0]}")
    if not scene_paths:
        raise ValueError("No generated scenes were provided for twin generation")

    existing_outputs = existing_twin_outputs(
        scene_paths,
        resolved_twin_output_root,
    )
    if existing_outputs:
        raise ValueError(
            f"Twin output already exists: {existing_outputs[0]}; run "
            "'python main.py clean -o twin' first"
        )
    if not ns3_executable.is_file():
        raise ValueError(f"ns-3 launcher does not exist: {ns3_executable}")
    if not dry_run:
        resolved_twin_output_root.mkdir(parents=True, exist_ok=True)

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
                    resolved_twin_output_root,
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
                if rc == 0 and not dry_run:
                    generated_label_file = generated_label_path_for_twin(
                        result_file
                    )
                    expected_files = (result_file, generated_label_file)
                    missing_file = next((path for path in expected_files if not path.is_file()), None)
                    if missing_file is not None:
                        print(f"ns-3 did not create the expected output file: {missing_file}", file=sys.stderr)
                        rc = 1
                    elif generated_label_file != scene / LABEL_FILE_NAME:
                        shutil.move(
                            generated_label_file,
                            scene / LABEL_FILE_NAME,
                        )
                if rc != 0:
                    if not dry_run:
                        for partial_output in (
                            result_file,
                            generated_label_path_for_twin(result_file),
                        ):
                            if partial_output.is_file():
                                partial_output.unlink()
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
        print(
            f"\n{action} {len(generated_files)} twin file(s) in "
            f"{resolved_twin_output_root}."
        )

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


def _run_twin_stage(
    args: argparse.Namespace,
    scene_root: Path,
    scenes: Sequence[Path] | None = None,
    *,
    twin_output_root: Path | None = None,
) -> TwinGenerationResult:
    return run_twins(
        scene_root,
        twin_output_root=twin_output_root,
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


def _group_root(scenes_root: Path, group: str) -> Path:
    if (
        scenes_root.name in {SCENES_DIR_NAME, INPUT_DIR_NAME}
        and scenes_root.parent.name in TWIN_TYPES
    ):
        if scenes_root.parent.name != group:
            raise ValueError(
                f"scenes_root points to {scenes_root.parent.name}, not {group}"
            )
        return scenes_root.parent
    if scenes_root.name in TWIN_TYPES:
        if scenes_root.name != group:
            raise ValueError(
                f"scenes_root points to {scenes_root.name}, not {group}"
            )
        return scenes_root
    return scenes_root / group


def _group_input_root(scenes_root: Path, group: str) -> Path:
    return _group_root(scenes_root, group) / INPUT_DIR_NAME


def _group_scenes_root(scenes_root: Path, group: str) -> Path:
    return _group_root(scenes_root, group) / SCENES_DIR_NAME


def _prepare_group_layout(
    scenes_root: Path,
    group: str,
    *,
    dry_run: bool = False,
) -> tuple[Path, Path]:
    group_root = _group_root(scenes_root, group)
    input_root = group_root / INPUT_DIR_NAME
    twin_root = group_root / SCENES_DIR_NAME
    symbolic_root = next(
        (
            root
            for root in (group_root, input_root, twin_root)
            if root.is_symlink()
        ),
        None,
    )
    if symbolic_root is not None:
        raise ValueError(
            f"Refusing to use a symbolic-link scene root: {symbolic_root}"
        )
    if dry_run:
        if input_root.is_dir():
            return input_root, twin_root
        if twin_root.is_dir() and any(
            is_scene_dir(entry) for entry in twin_root.iterdir()
        ):
            return twin_root, twin_root
        return input_root, twin_root

    input_root.mkdir(parents=True, exist_ok=True)
    if twin_root.exists() and not twin_root.is_dir():
        raise NotADirectoryError(
            f"Twin output root is not a directory: {twin_root}"
        )

    migrated: list[Path] = []
    entries = (
        sorted(twin_root.iterdir(), key=lambda path: path.name)
        if twin_root.is_dir()
        else []
    )
    for entry in entries:
        if not entry.is_dir():
            continue
        if not is_scene_dir(entry):
            raise ValueError(
                "The scenes directory may contain only flat Twin files; "
                f"cannot migrate unrecognized directory: {entry}"
            )
        destination = input_root / entry.name
        if destination.exists():
            raise ValueError(
                f"Cannot migrate legacy scene because the input exists: "
                f"{destination}"
            )
        shutil.move(entry, destination)
        migrated.append(destination)
    if migrated:
        print(
            f"Migrated {len(migrated)} legacy {group} scene input "
            f"director{'y' if len(migrated) == 1 else 'ies'} to "
            f"{input_root}.",
            flush=True,
        )
    return input_root, twin_root


def _configured_scenes_root(
    question_config: str | Path,
    override: Path | None,
) -> Path:
    return (
        override
        if override is not None
        else load_question_config(question_config).scenes_root
    )


def _ensure_twin_outputs_absent_before_layout(
    scenes_root: Path,
    group: str,
) -> None:
    input_root = _group_input_root(scenes_root, group)
    twin_root = _group_scenes_root(scenes_root, group)
    candidates: list[Path] = []

    if twin_root.is_dir():
        candidates.extend(
            path
            for path in twin_root.iterdir()
            if path.is_file() or path.is_symlink()
        )
    for raw_root in (input_root, twin_root):
        if not raw_root.is_dir():
            continue
        for metadata_file in raw_root.rglob("metadata.json"):
            scene_dir = metadata_file.parent
            candidates.extend(
                (
                    scene_dir / TWIN_FILE_NAME,
                    scene_dir / LABEL_FILE_NAME,
                    scene_dir / "twin",
                )
            )
            candidates.extend(scene_dir.glob("twin_[0-9]*.jsonl"))
            candidates.extend(scene_dir.glob("labels_*.jsonl"))

    existing = next(
        (
            path
            for path in candidates
            if path.exists() or path.is_symlink()
        ),
        None,
    )
    if existing is not None:
        raise ValueError(
            f"Twin output already exists: {existing}; run "
            "'python main.py clean -o twin -t "
            f"{TWIN_COMMAND_TYPE_BY_TYPE[group]}' first"
        )


def _clean_twin_layer(
    question_config: str | Path,
    scenes_root: Path,
    twin_group: str | None = None,
) -> tuple[Path, ...]:
    if scenes_root.is_symlink():
        raise ValueError(
            f"Refusing to clean a symbolic-link scenes_root: {scenes_root}"
        )
    if twin_group is not None and twin_group not in TWIN_TYPES:
        raise ValueError(f"Unknown Twin group for cleanup: {twin_group}")
    selected_groups = TWIN_TYPES if twin_group is None else (twin_group,)
    layer_roots = tuple(
        root
        for group in selected_groups
        for root in (
            _group_input_root(scenes_root, group),
            _group_scenes_root(scenes_root, group),
        )
    )
    symbolic_root = next(
        (root for root in layer_roots if root.is_symlink()),
        None,
    )
    if symbolic_root is not None:
        raise ValueError(
            f"Refusing to clean a symbolic-link layer root: {symbolic_root}"
        )
    invalid_root = next(
        (
            root
            for root in layer_roots
            if root.exists() and not root.is_dir()
        ),
        None,
    )
    if invalid_root is not None:
        raise NotADirectoryError(
            f"Twin layer root is not a directory: {invalid_root}"
        )

    question_cleanup = clean_question_outputs(
        question_config,
        scenes_root=scenes_root,
        categories=tuple(
            QUESTION_CATEGORY_BY_TWIN_TYPE[group]
            for group in selected_groups
        ),
    )
    removed: list[Path] = list(question_cleanup.removed_files)

    for group in (
        group
        for group in selected_groups
        if group in {"origin", "opt"}
    ):
        input_root = _group_input_root(scenes_root, group)
        if input_root.is_dir():
            for metadata_file in sorted(input_root.rglob("metadata.json")):
                scene_dir = metadata_file.parent
                for pattern in (
                    LABEL_FILE_NAME,
                    TWIN_FILE_NAME,
                    "labels_*.jsonl",
                    "twin_[0-9]*.jsonl",
                ):
                    for artifact in sorted(scene_dir.glob(pattern)):
                        if artifact.is_file() or artifact.is_symlink():
                            artifact.unlink()
                            removed.append(artifact)
                legacy_twin_root = scene_dir / "twin"
                if legacy_twin_root.is_symlink():
                    legacy_twin_root.unlink()
                    removed.append(legacy_twin_root)
                elif legacy_twin_root.is_dir():
                    shutil.rmtree(legacy_twin_root)
                    removed.append(legacy_twin_root)

        twin_root = _group_scenes_root(scenes_root, group)
        if twin_root.is_dir():
            for artifact in sorted(
                twin_root.iterdir(),
                key=lambda path: path.name,
            ):
                if artifact.is_file() or artifact.is_symlink():
                    artifact.unlink()
                    removed.append(artifact)
                elif artifact.is_dir() and is_scene_dir(artifact):
                    for pattern in (
                        TWIN_FILE_NAME,
                        LABEL_FILE_NAME,
                        "labels_*.jsonl",
                        "twin_[0-9]*.jsonl",
                    ):
                        for legacy_output in artifact.glob(pattern):
                            if legacy_output.is_file():
                                legacy_output.unlink()
                                removed.append(legacy_output)
                    legacy_twin_root = artifact / "twin"
                    if legacy_twin_root.is_symlink():
                        legacy_twin_root.unlink()
                        removed.append(legacy_twin_root)
                    elif legacy_twin_root.is_dir():
                        shutil.rmtree(legacy_twin_root)
                        removed.append(legacy_twin_root)

    if "evo" in selected_groups:
        for evolution_root in (
            _group_input_root(scenes_root, "evo"),
            _group_scenes_root(scenes_root, "evo"),
        ):
            if not evolution_root.is_dir():
                continue
            for artifact in sorted(
                evolution_root.iterdir(),
                key=lambda path: path.name,
            ):
                if artifact.is_file() or artifact.is_symlink():
                    artifact.unlink()
                elif artifact.is_dir():
                    shutil.rmtree(artifact)
                else:
                    artifact.unlink()
                removed.append(artifact)

    return tuple(dict.fromkeys(removed))


def _require_completed_twins(
    scenes_root: Path,
    group: str,
    *,
    allow_partial: bool = False,
) -> tuple[Path, list[Path]]:
    command_type = TWIN_COMMAND_TYPE_BY_TYPE[group]
    input_root = _group_input_root(scenes_root, group)
    twin_root = _group_scenes_root(scenes_root, group)
    discovery_root = input_root
    if not discovery_root.is_dir():
        # Read-only compatibility for outputs generated before input/scenes
        # were separated.
        discovery_root = twin_root
    try:
        scenes = discover_scenes(discovery_root)
    except ValueError as exc:
        raise ValueError(
            f"No {command_type} scenes are available; run "
            "'python main.py twin -t "
            f"{command_type}' first"
        ) from exc
    complete = [
        scene
        for scene in scenes
        if _twin_file_for_scene(twin_root, scene).is_file()
        and (scene / LABEL_FILE_NAME).is_file()
    ]
    incomplete = [
        scene
        for scene in scenes
        if not _twin_file_for_scene(twin_root, scene).is_file()
        or not (scene / LABEL_FILE_NAME).is_file()
    ]
    if incomplete and not allow_partial:
        raise ValueError(
            f"{len(incomplete)} {command_type} scene(s) do not have complete Twin "
            "outputs; run 'python main.py twin -t "
            f"{command_type}' first"
        )
    if not complete:
        raise ValueError(
            f"No {command_type} scenes have complete Twin and labels.jsonl "
            "outputs; run 'python main.py twin -t "
            f"{command_type}' first"
        )
    if incomplete:
        print(
            f"Using {len(complete)} completed {command_type} scene(s); "
            f"skipping {len(incomplete)} incomplete scene(s).",
            flush=True,
        )
    return twin_root, complete


def _twin_file_for_scene(twin_root: Path, scene: Path) -> Path:
    flat_file = twin_root / f"{scene.name}.jsonl"
    if flat_file.is_file() or scene.parent.name == INPUT_DIR_NAME:
        return flat_file
    return scene / TWIN_FILE_NAME


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "initial":
            result = initialize_ns3(args.archive)
            print(f"Initialized ns-3 in {result.destination}")
            print(f"Archive: {result.archive}")
            print(f"Source root: {result.source_prefix}")
            print(
                f"Extracted {result.extracted_entries} archive entry/entries; "
                f"skipped {result.skipped_entries} existing entry/entries"
            )
            print("Next, configure and build ns-3:")
            print(f"  cd {result.destination}")
            print("  ./ns3 configure -d debug --enable-examples --disable-tests")
            print("  ./ns3 build TwinGenerate")
            return 0

        if args.command == "scenes":
            _require_ns3_initialized()
        elif args.command == "twin":
            _require_ns3_initialized(args.ns3_root)

        if args.command == "clean":
            if (
                args.clean_object == "scenes"
                and args.clean_type is not None
            ):
                raise ValueError(
                    "'clean -o scenes' does not accept '-t/--type'"
                )
            if (
                args.clean_object == "twin"
                and args.clean_type is not None
                and args.clean_type not in TWIN_COMMAND_TYPES
            ):
                raise ValueError(
                    "'clean -o twin -t' accepts only origin, evolution, "
                    "or optimization"
                )
            if (
                args.clean_object == "questions"
                and args.clean_type is not None
                and args.clean_type not in QUESTION_CATEGORIES
            ):
                raise ValueError(
                    "'clean -o questions -t' accepts only analysis, "
                    "evolution, or optimization"
                )
            if (
                args.clean_object is None
                and args.clean_type is not None
            ):
                raise ValueError(
                    "'-t/--type' requires '-o/--object twin' or "
                    "'-o/--object questions'"
                )

        _load_project_dependencies()

        if args.command == "clean":
            if args.clean_object in {None, "scenes"}:
                config_path = (
                    args.clean_config
                    if args.clean_config is not None
                    else DEFAULT_SCENE_CONFIG
                )
                scene_config = load_scene_config(config_path)
                output_root, removed = reset_output_root(
                    scene_config.output_root
                )
                print(
                    f"Removed {len(removed)} top-level generated output "
                    f"entr{'y' if len(removed) == 1 else 'ies'} from "
                    f"{output_root}"
                )
                return 0

            question_config = (
                args.clean_config
                if args.clean_config is not None
                else DEFAULT_QUESTION_CONFIG
            )
            scenes_root_override = (
                _resolve_project_path(args.scene_root)
                if args.scene_root
                else None
            )
            if args.clean_object == "questions":
                result = clean_question_outputs(
                    question_config,
                    scenes_root=scenes_root_override,
                    categories=(args.clean_type,)
                    if args.clean_type is not None
                    else None,
                )
                scope = (
                    f"{args.clean_type} "
                    if args.clean_type is not None
                    else ""
                )
                print(
                    f"Removed {len(result.removed_files)} {scope}question "
                    "artifact(s)"
                )
                return 0

            requested_twin_scope = args.clean_type
            requested_twin_group = (
                TWIN_TYPE_BY_COMMAND_TYPE[requested_twin_scope]
                if requested_twin_scope is not None
                else None
            )
            scenes_root = _configured_scenes_root(
                question_config,
                scenes_root_override,
            )
            removed = _clean_twin_layer(
                question_config,
                scenes_root,
                requested_twin_group,
            )
            scope = requested_twin_scope or "all"
            print(
                f"Removed {len(removed)} {scope} Twin-layer artifact(s)"
            )
            return 0

        if args.command == "scenes":
            scene_dirs = generate_scenes(args.scene_config)
            print(f"Generated {len(scene_dirs)} scene(s)")
            for scene_dir in scene_dirs:
                print(scene_dir)
            return 0

        if args.command == "split":
            result = split_generated_dataset(
                args.dataset_split_config,
                train_ratio=args.train_ratio,
            )
            print(
                f"Dataset split complete: train_ratio={result.train_ratio:g}"
            )
            print(f"Train output: {result.train_output_root}")
            print(f"Test output: {result.test_output_root}")
            for task in result.tasks:
                print(
                    f"{task.task_type}: "
                    f"{task.source_questions} source question(s), "
                    f"{task.train_questions} train / "
                    f"{task.test_questions} test, "
                    f"{task.train_scenes} train scene(s) / "
                    f"{task.test_scenes} test scene(s)"
                )
            return 0

        if args.command == "twin":
            scenes_root_override = (
                _resolve_project_path(args.scene_root)
                if args.scene_root
                else None
            )
            scenes_root = _configured_scenes_root(
                args.question_config,
                scenes_root_override,
            )
            twin_type = TWIN_TYPE_BY_COMMAND_TYPE[args.twin_type]
            print(f"Twin type: {args.twin_type}", flush=True)
            if twin_type == "origin":
                _ensure_twin_outputs_absent_before_layout(
                    scenes_root,
                    "origin",
                )
                origin_input_root, origin_scenes_root = (
                    _prepare_group_layout(
                        scenes_root,
                        "origin",
                        dry_run=args.dry_run,
                    )
                )

                result = _run_twin_stage(
                    args,
                    origin_input_root,
                    twin_output_root=origin_scenes_root,
                )
                return 0 if result.complete else 1
            if twin_type == "evo":
                if args.dry_run:
                    raise ValueError(
                        "Evolution Twin generation does not support --dry-run "
                        "because it creates derived scene directories"
                    )
                if args.stop_time != 0:
                    raise ValueError(
                        "Evolution Twin generation does not support --stop-time; "
                        "original and evolved Twins must use the same duration"
                )
                preparation = prepare_evolution_scenes(
                    args.question_config,
                    scenes_root=scenes_root,
                )
                if not preparation.plans:
                    raise ValueError(
                        "No evolution scenes could be generated from the "
                        "available origin scenes"
                    )
                result = _run_twin_stage(
                    args,
                    _group_input_root(scenes_root, "evo"),
                    [plan.evolved_scene_dir for plan in preparation.plans],
                    twin_output_root=_group_scenes_root(
                        scenes_root,
                        "evo",
                    ),
                )
                print(
                    f"Prepared {len(preparation.plans)} evolution scene(s)"
                )
                return 0 if result.complete else 1
            _ensure_twin_outputs_absent_before_layout(
                scenes_root,
                "opt",
            )
            optimization_input_root, optimization_scenes_root = (
                _prepare_group_layout(
                    scenes_root,
                    "opt",
                    dry_run=args.dry_run,
                )
            )
            result = _run_twin_stage(
                args,
                optimization_input_root,
                twin_output_root=optimization_scenes_root,
            )
            return 0 if result.complete else 1

        if args.command == "questions":
            scenes_root = _resolve_project_path(args.scene_root) if args.scene_root else None
            print(f"Question type: {args.question_type}", flush=True)
            configured_root = _configured_scenes_root(
                args.question_config,
                scenes_root,
            )
            question_config = load_question_config(
                args.question_config
            )
            ensure_question_outputs_absent(
                question_config.categories[args.question_type]
            )
            if args.question_type == "analysis":
                original_scenes_root, completed_scenes = (
                    _require_completed_twins(
                        configured_root,
                        "origin",
                        allow_partial=True,
                    )
                )
                result = generate_questions(
                    args.question_config,
                    scenes_root=original_scenes_root,
                    scene_files=[
                        _twin_file_for_scene(
                            original_scenes_root,
                            scene,
                        )
                        for scene in completed_scenes
                    ],
                    question_type="analysis",
                )
                _print_question_result(result)
                return 0
            if args.question_type == "evolution":
                _require_completed_twins(configured_root, "evo")
                result = generate_evolution_questions(
                    args.question_config,
                    scenes_root=configured_root,
                )
                _print_question_result(result)
                return 0
            optimization_root, completed_scenes = _require_completed_twins(
                configured_root,
                "opt",
            )
            result = generate_questions(
                args.question_config,
                scenes_root=optimization_root,
                scene_files=[
                    _twin_file_for_scene(
                        optimization_root,
                        scene,
                    )
                    for scene in completed_scenes
                ],
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
