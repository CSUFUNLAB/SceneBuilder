from __future__ import annotations

from pathlib import Path

import networkx as nx

from .base import TopologyParseError


def _parse_float(token: str) -> float | None:
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


def _is_int(token: str) -> bool:
    try:
        int(token)
        return True
    except (TypeError, ValueError):
        return False


def parse_brite(path: Path) -> nx.Graph:
    graph = nx.Graph()
    section: str | None = None

    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        lower = line.lower()
        if lower.startswith("nodes"):
            section = "nodes"
            continue
        if lower.startswith("edges"):
            section = "edges"
            continue

        parts = line.split()
        if not parts:
            continue

        if section == "nodes":
            node_id = str(parts[0])
            attrs: dict[str, float] = {}
            if len(parts) >= 3:
                x = _parse_float(parts[1])
                y = _parse_float(parts[2])
                if x is not None:
                    attrs["source_longitude"] = x
                if y is not None:
                    attrs["source_latitude"] = y
            graph.add_node(node_id, **attrs)
            continue

        if section == "edges":
            if len(parts) < 2:
                continue

            if _is_int(parts[0]) and len(parts) >= 3:
                src, dst = parts[1], parts[2]
                bw = _parse_float(parts[5]) if len(parts) >= 6 else None
            else:
                src, dst = parts[0], parts[1]
                bw = _parse_float(parts[2]) if len(parts) >= 3 else None

            attrs: dict[str, float] = {}
            if bw is not None and bw > 0:
                attrs["source_bandwidth_mbps"] = float(bw)
            graph.add_edge(str(src), str(dst), **attrs)

    if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
        raise TopologyParseError(f"Failed to parse BRITE topology: {path}")

    return graph
