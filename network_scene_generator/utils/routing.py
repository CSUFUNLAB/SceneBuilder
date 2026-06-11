from __future__ import annotations

def resolve_routed_path(
    src: str,
    dst: str,
    routing_map: dict[tuple[str, str], str],
) -> list[str] | None:
    if src == dst:
        return [src]

    path = [src]
    current = src
    visited = {src}

    while current != dst:
        next_hop = str(routing_map.get((current, dst), "-1"))
        if next_hop == "-1" or next_hop in visited:
            return None
        path.append(next_hop)
        current = next_hop
        visited.add(current)

    return path
def downstream_route_revisits_source(
    src: str,
    dst: str,
    candidate_next_hop: str,
    routing_map: dict[tuple[str, str], str],
) -> bool:
    downstream_path = resolve_routed_path(candidate_next_hop, dst, routing_map)
    if downstream_path is None:
        return False
    return src in downstream_path[:-1]
