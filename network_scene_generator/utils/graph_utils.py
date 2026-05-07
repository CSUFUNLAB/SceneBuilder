from __future__ import annotations

from itertools import product

import networkx as nx


def normalize_graph_nodes(graph: nx.Graph) -> nx.Graph:
    normalized = nx.DiGraph() if graph.is_directed() else nx.Graph()
    for node, attrs in graph.nodes(data=True):
        normalized.add_node(str(node), **dict(attrs))
    for src, dst, attrs in graph.edges(data=True):
        normalized.add_edge(str(src), str(dst), **dict(attrs))
    return normalized


def as_working_graph(graph: nx.Graph, treat_as_undirected: bool = True) -> nx.Graph:
    normalized = normalize_graph_nodes(graph)
    if not treat_as_undirected:
        return normalized

    undirected = nx.Graph()
    undirected.add_nodes_from(normalized.nodes(data=True))
    for src, dst, attrs in normalized.edges(data=True):
        a, b = sorted((str(src), str(dst)))
        if undirected.has_edge(a, b):
            continue
        undirected.add_edge(a, b, **dict(attrs))
    return undirected


def ordered_nodes(graph: nx.Graph) -> list[str]:
    nodes = [str(node) for node in graph.nodes()]
    if nodes and all(node.isdigit() for node in nodes):
        return [str(value) for value in sorted(int(node) for node in nodes)]
    return sorted(nodes)


def ordered_pairs(nodes: list[str], include_self: bool = True) -> list[tuple[str, str]]:
    pairs = list(product(nodes, nodes))
    if include_self:
        return pairs
    return [(src, dst) for src, dst in pairs if src != dst]


def is_reachable(graph: nx.Graph, src: str, dst: str) -> bool:
    try:
        return nx.has_path(graph, src, dst)
    except nx.NetworkXError:
        return False
