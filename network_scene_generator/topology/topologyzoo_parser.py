from __future__ import annotations

from pathlib import Path

import networkx as nx

from .base import TopologyParseError

_BW_KEYS = ("bandwidth", "Bandwidth", "capacity", "Capacity", "bw", "BW")
_LAT_KEYS = ("latitude", "Latitude", "lat", "Lat", "LAT", "y", "Y")
_LON_KEYS = ("longitude", "Longitude", "lon", "Lon", "LON", "lng", "Lng", "x", "X")


def _to_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_location(attrs: dict[str, object]) -> tuple[float | None, float | None]:
    lat = None
    lon = None

    for key in _LAT_KEYS:
        if key in attrs:
            lat = _to_float(attrs[key])
            if lat is not None:
                break

    for key in _LON_KEYS:
        if key in attrs:
            lon = _to_float(attrs[key])
            if lon is not None:
                break

    return lat, lon


def _extract_original_node_name(attrs: dict[str, object]) -> str | None:
    value = attrs.get("label")
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def _read_gml_with_stable_ids(path: Path) -> nx.Graph:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    has_multigraph = any(line.strip().lower().startswith("multigraph") for line in lines)

    if not has_multigraph:
        for index, line in enumerate(lines):
            if line.strip().lower().startswith("graph"):
                lines.insert(index + 1, "  multigraph 1")
                break

    return nx.parse_gml(lines, label=None)


def _as_simple_graph(graph: nx.Graph) -> nx.Graph:
    simple = nx.Graph() if not graph.is_directed() else nx.DiGraph()
    for node, attrs in graph.nodes(data=True):
        node_attrs = dict(attrs)
        lat, lon = _extract_location(node_attrs)
        original_name = _extract_original_node_name(node_attrs)
        if original_name is not None:
            node_attrs["source_original_node_name"] = original_name
        if lat is not None:
            node_attrs["source_latitude"] = lat
        if lon is not None:
            node_attrs["source_longitude"] = lon
        simple.add_node(str(node), **node_attrs)

    for src, dst, attrs in graph.edges(data=True):
        src_s, dst_s = str(src), str(dst)
        if simple.has_edge(src_s, dst_s):
            continue

        edge_attrs = dict(attrs)
        for key in _BW_KEYS:
            if key in edge_attrs:
                try:
                    edge_attrs["source_bandwidth_mbps"] = float(edge_attrs[key])
                except (TypeError, ValueError):
                    pass
                break

        simple.add_edge(src_s, dst_s, **edge_attrs)

    return simple


def parse_topologyzoo(path: Path) -> nx.Graph:
    ext = path.suffix.lower()
    try:
        if ext == ".gml":
            graph = _read_gml_with_stable_ids(path)
        else:
            raise TopologyParseError(f"Unsupported TopologyZoo file extension: {path}")
    except Exception as exc:
        raise TopologyParseError(f"Failed to parse TopologyZoo file {path}: {exc}") from exc

    simple = _as_simple_graph(graph)
    if simple.number_of_nodes() == 0 or simple.number_of_edges() == 0:
        raise TopologyParseError(f"TopologyZoo graph is empty: {path}")
    return simple
