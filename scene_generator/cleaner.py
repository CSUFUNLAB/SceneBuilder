from __future__ import annotations

import shutil
from pathlib import Path

from .config import load_config

_SCENE_MARKER_FILES = {
    "metadata.json",
    "channels.csv",
    "links.csv",
    "nodes.csv",
    "routing_matrix.csv",
    "nics.csv",
    "traffic.jsonl",
}
_SCENE_GROUP_DIR_NAMES = {"origin", "evo", "opt"}
_SCENES_DIR_NAME = "scenes"
_INPUT_DIR_NAME = "input"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _looks_like_scene_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False

    child_names = {child.name for child in path.iterdir()}
    return "metadata.json" in child_names and any(name in child_names for name in _SCENE_MARKER_FILES - {"metadata.json"})


def clean_output_root(output_root: str | Path) -> tuple[Path, list[Path]]:
    output_root = Path(output_root)
    if not output_root.exists():
        return output_root, []

    removed: list[Path] = []
    for child in sorted(output_root.iterdir(), key=lambda p: p.name):
        if _looks_like_scene_dir(child):
            shutil.rmtree(child)
            removed.append(child)
            continue
        if child.name not in _SCENE_GROUP_DIR_NAMES or not child.is_dir():
            continue
        scene_roots = [child]
        nested_input_root = child / _INPUT_DIR_NAME
        if nested_input_root.is_dir():
            scene_roots.append(nested_input_root)
        nested_scenes_root = child / _SCENES_DIR_NAME
        if nested_scenes_root.is_dir():
            scene_roots.append(nested_scenes_root)
        for scene_root in scene_roots:
            for scene_dir in sorted(
                scene_root.iterdir(),
                key=lambda p: p.name,
            ):
                if not _looks_like_scene_dir(scene_dir):
                    continue
                shutil.rmtree(scene_dir)
                removed.append(scene_dir)
        if nested_scenes_root.is_dir():
            for twin_file in sorted(
                nested_scenes_root.glob("*.jsonl"),
                key=lambda path: path.name,
            ):
                if twin_file.is_file() or twin_file.is_symlink():
                    twin_file.unlink()
                    removed.append(twin_file)

    return output_root, removed


def reset_output_root(
    output_root: str | Path,
) -> tuple[Path, list[Path]]:
    """Remove every previous artifact before a fresh generation run."""

    raw_output_root = Path(output_root).expanduser()
    if raw_output_root.is_symlink():
        raise ValueError(
            f"Refusing to reset a symbolic-link output_root: "
            f"{raw_output_root}"
        )
    resolved_output_root = raw_output_root.resolve()
    protected_roots = {
        Path("/").resolve(),
        Path.home().resolve(),
        _PROJECT_ROOT,
    }
    if (
        resolved_output_root in protected_roots
        or _PROJECT_ROOT.is_relative_to(resolved_output_root)
    ):
        raise ValueError(
            "Refusing to reset an output_root that is the project root "
            f"or one of its parents: {resolved_output_root}"
        )
    if not resolved_output_root.exists():
        return resolved_output_root, []
    if not resolved_output_root.is_dir():
        raise NotADirectoryError(
            f"output_root is not a directory: {resolved_output_root}"
        )

    removed: list[Path] = []
    for child in sorted(
        resolved_output_root.iterdir(),
        key=lambda path: path.name,
    ):
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed.append(child)
    return resolved_output_root, removed


def clean(config_path: str | Path) -> tuple[Path, list[Path]]:
    config = load_config(config_path)
    return clean_output_root(config.output_root)
