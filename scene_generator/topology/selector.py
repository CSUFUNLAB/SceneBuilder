from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from ..config import SceneConfig, TopologySourceConfig
from .base import TopologyParseError
from .brite_parser import parse_brite
from .topologyzoo_parser import parse_topologyzoo


@dataclass(frozen=True)
class SelectedTopology:
    source_name: str
    source_type: str
    file_path: Path


_PARSERS = {
    "brite": parse_brite,
    "topologyzoo": parse_topologyzoo,
}


def _collect_candidates(source: TopologySourceConfig) -> list[Path]:
    candidates: set[Path] = set()
    for root in source.root_dirs:
        if not root.exists() or not root.is_dir():
            continue
        for pattern in source.glob_patterns:
            for file_path in root.glob(pattern):
                if file_path.is_file():
                    candidates.add(file_path.resolve())

    return sorted(candidates, key=lambda p: str(p))


def collect_topologies(config: SceneConfig) -> list[SelectedTopology]:
    selected_topologies: list[SelectedTopology] = []
    seen: set[tuple[str, Path]] = set()
    for source in config.topology_sources:
        if not source.enabled:
            continue
        candidates = _collect_candidates(source)
        if not candidates:
            raise FileNotFoundError(
                f"No candidate topology files found for source {source.name} under {source.root_dirs}"
            )
        for file_path in candidates:
            key = (source.type, file_path)
            if key in seen:
                continue
            seen.add(key)
            selected_topologies.append(
                SelectedTopology(
                    source_name=source.name,
                    source_type=source.type,
                    file_path=file_path,
                )
            )

    if not selected_topologies:
        raise FileNotFoundError("No topology files found in enabled topology sources")
    return selected_topologies


def load_topology(selected: SelectedTopology) -> nx.Graph:
    parser = _PARSERS.get(selected.source_type)
    if parser is None:
        raise TopologyParseError(f"No parser registered for source type: {selected.source_type}")
    return parser(selected.file_path)
