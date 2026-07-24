from __future__ import annotations

import math

from .scene import EntityRecord, SceneData


SATURATION_THRESHOLD = 0.95
DEGRADATION_EVIDENCE_THRESHOLD = 0.95
MIN_OFFERED_PACKET_SAMPLE = 10
FLOAT_TOLERANCE = 1e-9


def _number(properties: dict[str, object], name: str) -> float | None:
    value = properties.get(name)
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def infer_flow_state(flow: EntityRecord) -> str | None:
    tx_packets = _number(flow.properties, "tx_packets")
    rx_packets = _number(flow.properties, "rx_packets")
    lost_packets = _number(flow.properties, "lost_packets")
    throughput = _number(flow.properties, "throughput_mbps")
    demand = _number(flow.properties, "demand_mbps")
    if None in (tx_packets, rx_packets, lost_packets, throughput, demand):
        return None
    if tx_packets <= 0 or rx_packets <= 0:
        return "failed"
    if lost_packets > 0:
        return "unstable"
    if throughput < demand * 0.95:
        return "degraded"
    return "normal"


def _first_hop_offered_load(
    scene: SceneData,
    channel: EntityRecord,
) -> float | None:
    """Return the strongest directly sourced directional load on a channel.

    Only flows whose first hop is this channel are used. Their public transmit
    statistics establish that traffic was offered before any other network
    channel could have limited it.
    """

    directional_loads: dict[tuple[str, str], float] = {}
    directional_packets: dict[tuple[str, str], float] = {}
    for flow in scene.entities("data_flow"):
        path_channels = flow.relations.get("path_channels")
        path_nodes = flow.relations.get("path_nodes")
        if (
            not isinstance(path_channels, list)
            or not path_channels
            or str(path_channels[0]) != channel.entity_id
            or not isinstance(path_nodes, list)
            or len(path_nodes) != len(path_channels) + 1
        ):
            continue

        demand = _number(flow.properties, "demand_mbps")
        tx_packets = _number(flow.properties, "tx_packets")
        if demand is None or demand <= 0 or tx_packets is None or tx_packets <= 0:
            continue

        direction = (str(path_nodes[0]), str(path_nodes[1]))
        if not all(direction):
            continue
        directional_loads[direction] = directional_loads.get(direction, 0.0) + demand
        directional_packets[direction] = (
            directional_packets.get(direction, 0.0) + tx_packets
        )

    sampled_loads = [
        load
        for direction, load in directional_loads.items()
        if directional_packets.get(direction, 0.0) >= MIN_OFFERED_PACKET_SAMPLE
    ]
    return max(sampled_loads) if sampled_loads else None


def infer_channel_state(scene: SceneData, channel: EntityRecord) -> str | None:
    original_capacity = _number(channel.properties, "original_capacity_mbps")
    current_throughput = _number(channel.properties, "current_throughput_mbps")
    if (
        None in (original_capacity, current_throughput)
        or original_capacity <= 0
        or current_throughput < -FLOAT_TOLERANCE
        or current_throughput > original_capacity + FLOAT_TOLERANCE
    ):
        return None
    if current_throughput / original_capacity >= SATURATION_THRESHOLD:
        return "saturated"

    offered_load = _first_hop_offered_load(scene, channel)
    if offered_load is None:
        return None
    expected_throughput = min(original_capacity, offered_load)
    if current_throughput <= FLOAT_TOLERANCE:
        return "disabled"
    if (
        current_throughput
        < expected_throughput * DEGRADATION_EVIDENCE_THRESHOLD
    ):
        return "degraded"

    # A low observed rate is not enough to prove normality. The conservative
    # degraded rule requires directly sourced offered load, a sufficient packet
    # sample, and a throughput deficit, which excludes upstream channel causes.
    return None


def _incident_channels(scene: SceneData, node_id: str) -> list[EntityRecord]:
    return [
        channel
        for channel in scene.entities("channel")
        if node_id in scene.channel_endpoint_nodes(channel)
    ]


def _complete_path_channels(
    scene: SceneData,
    flow: EntityRecord,
) -> list[EntityRecord] | None:
    explicit_channel_ids = flow.relations.get("path_channels")
    if explicit_channel_ids is not None:
        if not isinstance(explicit_channel_ids, list) or not explicit_channel_ids:
            return None
        channel_ids = [str(channel_id) for channel_id in explicit_channel_ids]
        if any(not channel_id for channel_id in channel_ids):
            return None
        if len(set(channel_ids)) != len(channel_ids):
            return None
        channels = scene.channels_on_flow_path(flow)
        if [channel.entity_id for channel in channels] != channel_ids:
            return None
        return channels

    path_nodes = [str(node_id) for node_id in flow.relations.get("path_nodes", [])]
    if len(path_nodes) < 2:
        return None
    discovered = scene.channels_on_flow_path(flow)
    ordered: list[EntityRecord] = []
    for source, destination in zip(path_nodes, path_nodes[1:]):
        pair = {source, destination}
        matches = [
            channel
            for channel in discovered
            if set(scene.channel_endpoint_nodes(channel)) == pair
        ]
        if len(matches) != 1:
            return None
        ordered.append(matches[0])
    return ordered


def _topologically_reachable_nodes(
    scene: SceneData,
    source_node_id: str,
) -> set[str] | None:
    nodes = {node.entity_id for node in scene.entities("node")}
    if source_node_id not in nodes:
        return None
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for channel in scene.entities("channel"):
        endpoints = scene.channel_endpoint_nodes(channel)
        if len(endpoints) != 2 or any(endpoint not in nodes for endpoint in endpoints):
            return None
        channel_state = infer_channel_state(scene, channel)
        if channel_state is None:
            return None
        if channel_state == "disabled":
            continue
        left, right = endpoints
        adjacency[left].add(right)
        adjacency[right].add(left)

    visited = {source_node_id}
    pending = [source_node_id]
    while pending:
        current = pending.pop()
        for neighbor in adjacency[current]:
            if neighbor in visited:
                continue
            visited.add(neighbor)
            pending.append(neighbor)
    visited.remove(source_node_id)
    return visited


def _missing_route_destinations(
    scene: SceneData,
    node: EntityRecord,
) -> set[str] | None:
    if "routes" not in node.relations:
        return None
    routes = node.relations.get("routes")
    if not isinstance(routes, list):
        return None
    expected = _topologically_reachable_nodes(scene, node.entity_id)
    if expected is None:
        return None

    advertised: set[str] = set()
    for route in routes:
        if not isinstance(route, dict):
            return None
        destinations = route.get("destination_nodes")
        if not isinstance(destinations, list):
            return None
        destination_ids = {str(destination_id) for destination_id in destinations}
        if any(not destination_id for destination_id in destination_ids):
            return None
        advertised.update(destination_ids)
    if not advertised.issubset(expected):
        return None
    return expected - advertised


def infer_node_state(scene: SceneData, node: EntityRecord) -> str | None:
    missing_routes = _missing_route_destinations(scene, node)
    if missing_routes:
        return "routing_failed"

    rx_packets = _number(node.properties, "rx_packets")
    tx_packets = _number(node.properties, "tx_packets")
    if rx_packets is not None and tx_packets is not None and rx_packets + tx_packets > 0:
        return "normal"

    incident_channels = _incident_channels(scene, node.entity_id)
    channel_states = [
        infer_channel_state(scene, channel) for channel in incident_channels
    ]
    if any(state in {"normal", "degraded", "saturated"} for state in channel_states):
        return "normal"
    if (
        len(incident_channels) >= 2
        and all(state == "disabled" for state in channel_states)
    ):
        return "disabled"
    return None


def infer_nic_state(scene: SceneData, nic: EntityRecord) -> str | None:
    channel_id = str(nic.relations.get("channel", ""))
    channel = scene.entity("channel", channel_id)
    current_throughput = (
        _number(channel.properties, "current_throughput_mbps")
        if channel is not None
        else None
    )
    if current_throughput is None or current_throughput <= FLOAT_TOLERANCE:
        return None

    queue_size = _number(nic.properties, "queue_size_packets")
    queue_current = _number(nic.properties, "queue_current_packets")
    if None in (queue_size, queue_current) or queue_size <= 0:
        return None
    if queue_current / queue_size >= SATURATION_THRESHOLD:
        return "saturated"
    return "normal"


def infer_entity_state(scene: SceneData, entity: EntityRecord) -> str | None:
    if entity.entity_type == "node":
        return infer_node_state(scene, entity)
    if entity.entity_type == "channel":
        return infer_channel_state(scene, entity)
    if entity.entity_type == "nic":
        return infer_nic_state(scene, entity)
    if entity.entity_type == "data_flow":
        return infer_flow_state(entity)
    return None


def infer_bandwidth_constraint(scene: SceneData, flow: EntityRecord) -> str | None:
    # The effective channel capacity is intentionally not exposed by the Twin.
    # Therefore insufficient capacity (and consequently the three-way answer)
    # cannot be established from public evidence.
    return None


def infer_congestion_pattern(scene: SceneData, flow: EntityRecord) -> str | None:
    channels = _complete_path_channels(scene, flow)
    if channels is None:
        return None
    states = [infer_channel_state(scene, channel) for channel in channels]
    if any(state not in {"normal", "saturated"} for state in states):
        return None
    saturated_count = sum(state == "saturated" for state in states)
    if saturated_count == 1:
        return "single_channel_bottleneck"
    if saturated_count >= 2:
        return "multi_channel_saturation"
    return None


def infer_channel_saturation_cause(
    scene: SceneData,
    channel: EntityRecord,
) -> str | None:
    if infer_channel_state(scene, channel) != "saturated":
        return None

    raw_carried_flow_ids = channel.relations.get("carries")
    if not isinstance(raw_carried_flow_ids, list) or not raw_carried_flow_ids:
        return None
    carried_flow_ids = [str(flow_id) for flow_id in raw_carried_flow_ids]
    if any(not flow_id for flow_id in carried_flow_ids):
        return None
    if len(set(carried_flow_ids)) != len(carried_flow_ids):
        return None

    flows_on_path: set[str] = set()
    for flow in scene.entities("data_flow"):
        path_channels = _complete_path_channels(scene, flow)
        if path_channels is None:
            explicit_ids = flow.relations.get("path_channels")
            if isinstance(explicit_ids, list) and channel.entity_id in {
                str(channel_id) for channel_id in explicit_ids
            }:
                return None
            continue
        if any(path_channel.entity_id == channel.entity_id for path_channel in path_channels):
            flows_on_path.add(flow.entity_id)
    if flows_on_path != set(carried_flow_ids):
        return None

    demands: list[float] = []
    for flow_id in carried_flow_ids:
        flow = scene.entity("data_flow", flow_id)
        if flow is None:
            return None
        demand = _number(flow.properties, "demand_mbps")
        if demand is None or demand <= 0:
            return None
        demands.append(demand)

    largest_demand = max(demands)
    other_demand = sum(demands) - largest_demand
    if largest_demand > other_demand:
        return "single_large_flow"
    return "multiple_flow_aggregation"


def infer_bottleneck(scene: SceneData, flow: EntityRecord) -> str | None:
    channels = _complete_path_channels(scene, flow)
    if channels is None:
        return None
    states = [infer_channel_state(scene, channel) for channel in channels]
    if any(state not in {"normal", "saturated"} for state in states):
        return None
    saturated = [
        channel.entity_id
        for channel, state in zip(channels, states)
        if state == "saturated"
    ]
    return saturated[0] if len(saturated) == 1 else None


def _is_reachable_with_restored_channels(
    scene: SceneData,
    source_node_id: str,
    destination_node_id: str,
    restored_channel_ids: set[str] | None = None,
) -> bool | None:
    nodes = {node.entity_id for node in scene.entities("node")}
    if source_node_id not in nodes or destination_node_id not in nodes:
        return None
    restored = restored_channel_ids or set()
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for channel in scene.entities("channel"):
        endpoints = scene.channel_endpoint_nodes(channel)
        if len(endpoints) != 2 or any(endpoint not in nodes for endpoint in endpoints):
            return None
        state = infer_channel_state(scene, channel)
        if state is None:
            return None
        if state == "disabled" and channel.entity_id not in restored:
            continue
        left, right = endpoints
        adjacency[left].add(right)
        adjacency[right].add(left)

    visited = {source_node_id}
    pending = [source_node_id]
    while pending:
        current = pending.pop()
        if current == destination_node_id:
            return True
        for neighbor in adjacency[current]:
            if neighbor not in visited:
                visited.add(neighbor)
                pending.append(neighbor)
    return False


def infer_flow_failure_cause(scene: SceneData, flow: EntityRecord) -> str | None:
    if infer_flow_state(flow) != "failed":
        return None

    all_channels = scene.entities("channel")
    if any(len(scene.channel_endpoint_nodes(channel)) != 2 for channel in all_channels):
        return None
    channel_states = {
        channel.entity_id: infer_channel_state(scene, channel)
        for channel in all_channels
    }
    if not channel_states or any(state is None for state in channel_states.values()):
        return None
    raw_path_nodes = flow.relations.get("path_nodes")
    if not isinstance(raw_path_nodes, list) or not raw_path_nodes:
        return None
    ordered_path_node_ids = [str(node_id) for node_id in raw_path_nodes]
    path_node_ids = set(ordered_path_node_ids)
    if any(scene.entity("node", node_id) is None for node_id in path_node_ids):
        return None
    for node_id in path_node_ids:
        node = scene.entity("node", node_id)
        if node is None or None in (
            _number(node.properties, "rx_packets"),
            _number(node.properties, "tx_packets"),
        ):
            return None

    raw_path_channels = flow.relations.get("path_channels")
    if raw_path_channels == []:
        path_channels: list[EntityRecord] = []
    else:
        complete_path_channels = _complete_path_channels(scene, flow)
        if complete_path_channels is None:
            return None
        path_channels = complete_path_channels

    disabled_channels = {
        channel_id for channel_id, state in channel_states.items() if state == "disabled"
    }
    path_channel_ids = {channel.entity_id for channel in path_channels}
    possible_causes: set[str] = set()

    destination_node_id = str(flow.relations.get("destination_node", ""))
    last_path_node = scene.entity("node", ordered_path_node_ids[-1])
    if destination_node_id and last_path_node is not None:
        missing_destinations = _missing_route_destinations(scene, last_path_node)
        if (
            missing_destinations is not None
            and destination_node_id in missing_destinations
            and infer_node_state(scene, last_path_node) == "routing_failed"
        ):
            possible_causes.add(last_path_node.entity_id)

    # Under the configured single-fault model, one disabled channel can be
    # caused by that channel or one of its degree-one endpoint nodes.
    if len(disabled_channels) == 1:
        channel_id = next(iter(disabled_channels))
        if channel_id in path_channel_ids:
            possible_causes.add(channel_id)

    for node_id in path_node_ids:
        node = scene.entity("node", node_id)
        if node is None:
            continue
        incident_ids = {channel.entity_id for channel in _incident_channels(scene, node_id)}
        rx_packets = _number(node.properties, "rx_packets")
        tx_packets = _number(node.properties, "tx_packets")
        if (
            incident_ids
            and incident_ids == disabled_channels
            and rx_packets is not None
            and tx_packets is not None
            and rx_packets + tx_packets == 0
        ):
            possible_causes.add(node_id)

    source_node_id = str(flow.relations.get("source_node", ""))
    destination_node_id = str(flow.relations.get("destination_node", ""))
    if not source_node_id:
        source_node_id = ordered_path_node_ids[0]
    if source_node_id and destination_node_id:
        currently_reachable = _is_reachable_with_restored_channels(
            scene,
            source_node_id,
            destination_node_id,
        )
        if currently_reachable is False:
            disabled_node_candidates: dict[str, set[str]] = {}
            for node in scene.entities("node"):
                incident_ids = {
                    channel.entity_id
                    for channel in _incident_channels(scene, node.entity_id)
                }
                rx_packets = _number(node.properties, "rx_packets")
                tx_packets = _number(node.properties, "tx_packets")
                if (
                    len(incident_ids) >= 2
                    and incident_ids.issubset(disabled_channels)
                    and rx_packets is not None
                    and tx_packets is not None
                    and rx_packets + tx_packets == 0
                ):
                    disabled_node_candidates[node.entity_id] = incident_ids

            channels_at_disabled_nodes = {
                channel_id
                for incident_ids in disabled_node_candidates.values()
                for channel_id in incident_ids
            }
            for channel_id in disabled_channels - channels_at_disabled_nodes:
                if _is_reachable_with_restored_channels(
                    scene,
                    source_node_id,
                    destination_node_id,
                    {channel_id},
                ) is True:
                    possible_causes.add(channel_id)

            for node_id, incident_ids in disabled_node_candidates.items():
                if _is_reachable_with_restored_channels(
                    scene,
                    source_node_id,
                    destination_node_id,
                    incident_ids,
                ) is True:
                    possible_causes.add(node_id)

    return next(iter(possible_causes)) if len(possible_causes) == 1 else None


def infer_flow_failure_type(scene: SceneData, flow: EntityRecord) -> str | None:
    entity_id = infer_flow_failure_cause(scene, flow)
    if entity_id is None:
        return None

    channel = scene.entity("channel", entity_id)
    if channel is not None:
        return (
            "channel_failure"
            if infer_channel_state(scene, channel) == "disabled"
            else None
        )

    node = scene.entity("node", entity_id)
    if node is None:
        return None
    node_state = infer_node_state(scene, node)
    if node_state == "disabled":
        return "node_crash"
    if node_state == "routing_failed":
        return "routing_failure"
    return None
