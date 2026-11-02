from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from ..config import SceneConfig
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


def select_topology(config: SceneConfig) -> SelectedTopology:
    topology = config.topology
    if not topology.file.exists():
        raise FileNotFoundError(f"Topology file does not exist: {topology.file}")
    if topology.file.is_dir():
        raise IsADirectoryError(f"topology.file must refer to a single file, not a directory: {topology.file}")
    if not topology.file.is_file():
        raise ValueError(f"topology.file must refer to a regular file: {topology.file}")
    return SelectedTopology(
        source_name=topology.name,
        source_type=topology.type,
        file_path=topology.file,
    )


def load_topology(selected: SelectedTopology) -> nx.Graph:
    parser = _PARSERS.get(selected.source_type)
    if parser is None:
        raise TopologyParseError(f"No parser registered for source type: {selected.source_type}")
    return parser(selected.file_path)
