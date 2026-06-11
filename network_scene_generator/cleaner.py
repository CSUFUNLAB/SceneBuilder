from __future__ import annotations

import shutil
from pathlib import Path

from .config import load_config

_SCENE_MARKER_FILES = {
    "metadata.json",
    "links.csv",
    "nodes.csv",
    "routing_matrix.csv",
    "nics.csv",
    "traffic.jsonl",
}


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
        if not _looks_like_scene_dir(child):
            continue
        shutil.rmtree(child)
        removed.append(child)

    return output_root, removed


def clean(config_path: str | Path) -> tuple[Path, list[Path]]:
    config = load_config(config_path)
    return clean_output_root(config.output_root)
