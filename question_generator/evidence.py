from __future__ import annotations

import math

from .scene import EntityRecord, SceneData


SATURATION_THRESHOLD = 0.95
NORMAL_EVIDENCE_MIN_RATIO = 0.70
NORMAL_EVIDENCE_MAX_RATIO = 0.90
DEGRADATION_EVIDENCE_THRESHOLD = 0.95
MIN_OFFERED_PACKET_SAMPLE = 10
FLOAT_TOLERANCE = 1e-9
_CACHE_MISS = object()


def _number(properties: dict[str, object], name: str) -> float | None:
    value = properties.get(name)
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def channel_directional_throughputs(
    scene: SceneData,
    channel: EntityRecord,
) -> tuple[float, float] | None:
    """Reconstruct both directional throughputs from carries and flow paths."""

    endpoint_nodes = scene.channel_endpoint_nodes(channel)
    if len(endpoint_nodes) != 2 or endpoint_nodes[0] == endpoint_nodes[1]:
        return None
    forward = (endpoint_nodes[0], endpoint_nodes[1])
    reverse = (endpoint_nodes[1], endpoint_nodes[0])
    totals = {forward: 0.0, reverse: 0.0}

    expected_directions: dict[str, tuple[str, str]] = {}
    for flow in scene.entities("data_flow"):
        path = _complete_flow_path(scene, flow)
        if path is None:
            return None
        path_nodes, path_channels = path
        matching_positions = [
            index
            for index, path_channel in enumerate(path_channels)
            if path_channel.entity_id == channel.entity_id
        ]
        if not matching_positions:
            continue
        if len(matching_positions) != 1:
            return None
        path_index = matching_positions[0]
        direction = (path_nodes[path_index], path_nodes[path_index + 1])
        if direction not in totals:
            return None
        expected_directions[flow.entity_id] = direction

    raw_carries = channel.relations.get("carries")
    if not isinstance(raw_carries, list):
        return None
    observed_flow_ids: set[str] = set()
    for item in raw_carries:
        if not isinstance(item, dict):
            return None
        flow_id = str(item.get("data_flow_id", ""))
        bandwidth = _number(item, "bandwidth_mbps")
        if (
            not flow_id
            or flow_id in observed_flow_ids
            or flow_id not in expected_directions
            or bandwidth is None
            or bandwidth < -FLOAT_TOLERANCE
        ):
            return None
        observed_flow_ids.add(flow_id)
        totals[expected_directions[flow_id]] += max(0.0, bandwidth)

    if observed_flow_ids != set(expected_directions):
        return None
    return totals[forward], totals[reverse]


def _maximum_channel_throughput(
    scene: SceneData,
    channel: EntityRecord,
) -> float | None:
    throughputs = channel_directional_throughputs(scene, channel)
    return max(throughputs) if throughputs is not None else None


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


def _channel_directional_measurements(
    scene: SceneData,
    channel: EntityRecord,
) -> tuple[tuple[float, float], ...] | None:
    """Return sampled (offered, delivered) rates for both channel directions.

    A point-to-point channel exposes exactly two NICs.  For each direction, the
    sender NIC's local IPv4 transmit counters measure traffic offered to this
    channel, while the peer NIC's receive counters measure traffic delivered by
    it.  This remains direct evidence when the channel is an intermediate hop.
    """

    connected = channel.relations.get("connects")
    if not isinstance(connected, list) or len(connected) != 2:
        return None
    nic_ids = [str(nic_id) for nic_id in connected]
    if not all(nic_ids) or nic_ids[0] == nic_ids[1]:
        return None

    nics = [scene.entity("nic", nic_id) for nic_id in nic_ids]
    if any(nic is None for nic in nics):
        return None
    if any(
        str(nic.relations.get("channel", "")) != channel.entity_id
        for nic in nics
        if nic is not None
    ):
        return None

    measurements: list[tuple[float, float]] = []
    for sender, receiver in ((nics[0], nics[1]), (nics[1], nics[0])):
        if sender is None or receiver is None:
            continue
        offered = _number(sender.properties, "tx_rate_mbps")
        delivered = _number(receiver.properties, "rx_rate_mbps")
        offered_packets = _number(sender.properties, "tx_packets")
        if (
            offered is None
            or delivered is None
            or offered_packets is None
            or offered <= FLOAT_TOLERANCE
            or delivered < -FLOAT_TOLERANCE
            or offered_packets < MIN_OFFERED_PACKET_SAMPLE
        ):
            continue
        measurements.append((offered, max(0.0, delivered)))

    return tuple(measurements) if measurements else None


def infer_channel_state(scene: SceneData, channel: EntityRecord) -> str | None:
    original_capacity = _number(channel.properties, "original_capacity_mbps")
    current_throughput = _maximum_channel_throughput(scene, channel)
    if (
        None in (original_capacity, current_throughput)
        or original_capacity <= 0
        or current_throughput < -FLOAT_TOLERANCE
        or current_throughput > original_capacity + FLOAT_TOLERANCE
    ):
        return None

    physical_fault_id = _infer_unique_physical_fault(scene)
    if physical_fault_id == channel.entity_id:
        return "disabled"
    failed_node = scene.entity("node", physical_fault_id or "")
    if (
        failed_node is not None
        and failed_node.entity_id in scene.channel_endpoint_nodes(channel)
    ):
        return "disabled"

    throughput_ratio = current_throughput / original_capacity
    if throughput_ratio >= SATURATION_THRESHOLD:
        return "saturated"
    if (
        NORMAL_EVIDENCE_MIN_RATIO - FLOAT_TOLERANCE
        <= throughput_ratio
        <= NORMAL_EVIDENCE_MAX_RATIO + FLOAT_TOLERANCE
    ):
        # Configured degradation multipliers are at most 0.5, so a channel
        # carrying 70%-90% of its original capacity cannot be degraded,
        # disabled, or saturated.
        return "normal"

    measurements = _channel_directional_measurements(scene, channel)
    if measurements is None:
        return None
    if any(delivered <= FLOAT_TOLERANCE for _, delivered in measurements):
        return "disabled"
    if any(
        delivered
        < min(original_capacity, offered) * DEGRADATION_EVIDENCE_THRESHOLD
        for offered, delivered in measurements
    ):
        return "degraded"

    # Outside the explicit 70%-90% normal-evidence band, a low observed rate is
    # not enough to prove normality. The degraded rule requires a sufficient
    # sender-side packet sample and an observed per-hop delivery deficit.
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


def _complete_flow_path(
    scene: SceneData,
    flow: EntityRecord,
) -> tuple[list[str], list[EntityRecord]] | None:
    raw_path_nodes = flow.relations.get("path_nodes")
    if not isinstance(raw_path_nodes, list):
        return None
    path_node_ids = [str(node_id) for node_id in raw_path_nodes]
    if (
        len(path_node_ids) < 2
        or any(not node_id for node_id in path_node_ids)
        or len(set(path_node_ids)) != len(path_node_ids)
    ):
        return None
    source_node_id = str(flow.relations.get("source_node", ""))
    destination_node_id = str(flow.relations.get("destination_node", ""))
    if (
        path_node_ids[0] != source_node_id
        or path_node_ids[-1] != destination_node_id
        or any(scene.entity("node", node_id) is None for node_id in path_node_ids)
    ):
        return None

    channels = _complete_path_channels(scene, flow)
    if channels is None or len(channels) + 1 != len(path_node_ids):
        return None
    for index, channel in enumerate(channels):
        if set(scene.channel_endpoint_nodes(channel)) != {
            path_node_ids[index],
            path_node_ids[index + 1],
        }:
            return None
    return path_node_ids, channels


def _infer_unique_physical_fault(scene: SceneData) -> str | None:
    cached = scene._evidence_cache.get("unique_physical_fault", _CACHE_MISS)
    if cached is not _CACHE_MISS:
        return cached if isinstance(cached, str) else None

    result = _compute_unique_physical_fault(scene)
    scene._evidence_cache["unique_physical_fault"] = result
    return result


def _compute_unique_physical_fault(scene: SceneData) -> str | None:
    """Infer one physical fault from complete static paths and flow outcomes."""

    flow_paths: dict[str, tuple[list[str], list[EntityRecord]]] = {}
    failed_flow_ids: set[str] = set()
    for flow in scene.entities("data_flow"):
        state = infer_flow_state(flow)
        path = _complete_flow_path(scene, flow)
        if state is None or path is None:
            return None
        flow_paths[flow.entity_id] = path
        if state == "failed":
            failed_flow_ids.add(flow.entity_id)
    if not failed_flow_ids:
        return None

    possible_causes: set[str] = set()
    for channel in scene.entities("channel"):
        original_capacity = _number(
            channel.properties,
            "original_capacity_mbps",
        )
        current_throughput = _maximum_channel_throughput(scene, channel)
        if (
            original_capacity is None
            or original_capacity <= 0
            or current_throughput is None
            or abs(current_throughput) > FLOAT_TOLERANCE
            or len(scene.channel_endpoint_nodes(channel)) != 2
        ):
            continue
        predicted_failed = {
            flow_id
            for flow_id, (_, channels) in flow_paths.items()
            if any(
                path_channel.entity_id == channel.entity_id
                for path_channel in channels
            )
        }
        if predicted_failed == failed_flow_ids:
            possible_causes.add(channel.entity_id)

    for node in scene.entities("node"):
        rx_packets = _number(node.properties, "rx_packets")
        tx_packets = _number(node.properties, "tx_packets")
        incident_channels = _incident_channels(scene, node.entity_id)
        if (
            rx_packets is None
            or tx_packets is None
            or abs(rx_packets) > FLOAT_TOLERANCE
            or abs(tx_packets) > FLOAT_TOLERANCE
            or len(incident_channels) < 2
        ):
            continue
        incident_throughputs = [
            _maximum_channel_throughput(scene, channel)
            for channel in incident_channels
        ]
        if (
            any(value is None for value in incident_throughputs)
            or any(
                abs(float(value)) > FLOAT_TOLERANCE
                for value in incident_throughputs
                if value is not None
            )
        ):
            continue
        predicted_failed = {
            flow_id
            for flow_id, (node_ids, _) in flow_paths.items()
            if node.entity_id in node_ids
        }
        if predicted_failed == failed_flow_ids:
            possible_causes.add(node.entity_id)

    return next(iter(possible_causes)) if len(possible_causes) == 1 else None


def infer_node_state(scene: SceneData, node: EntityRecord) -> str | None:
    physical_fault_id = _infer_unique_physical_fault(scene)
    if physical_fault_id is not None:
        return (
            "disabled"
            if physical_fault_id == node.entity_id
            else "normal"
        )

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
    return None


def infer_nic_state(scene: SceneData, nic: EntityRecord) -> str | None:
    channel_id = str(nic.relations.get("channel", ""))
    channel = scene.entity("channel", channel_id)
    if channel is None:
        return None

    channel_state = infer_channel_state(scene, channel)
    if channel_state == "disabled":
        # NIC state is operational, not a physical-root label. An interface
        # attached to a disabled channel is itself unavailable regardless of
        # whether the channel, an endpoint node, or one NIC caused the fault.
        return "disabled"

    current_throughput = _maximum_channel_throughput(scene, channel)
    if current_throughput is None or current_throughput <= FLOAT_TOLERANCE:
        return None

    queue_size = _number(nic.properties, "queue_size_packets")
    queue_current = _number(nic.properties, "queue_current_packets")
    if None in (queue_size, queue_current) or queue_size <= 0:
        return None
    if queue_current / queue_size >= SATURATION_THRESHOLD:
        return "saturated"
    return "normal"


def infer_channel_unavailability_cause(
    scene: SceneData,
    channel: EntityRecord,
) -> str | None:
    if infer_channel_state(scene, channel) != "disabled":
        return None
    physical_fault_id = _infer_unique_physical_fault(scene)
    if physical_fault_id == channel.entity_id:
        return "channel_or_interface_fault"
    failed_node = scene.entity("node", physical_fault_id or "")
    if (
        failed_node is not None
        and failed_node.entity_id in scene.channel_endpoint_nodes(channel)
    ):
        return "connected_node_fault"
    return None


def infer_nic_unavailability_cause(
    scene: SceneData,
    nic: EntityRecord,
) -> str | None:
    if infer_nic_state(scene, nic) != "disabled":
        return None
    channel = scene.entity(
        "channel",
        str(nic.relations.get("channel", "")),
    )
    if channel is None:
        return None
    return infer_channel_unavailability_cause(scene, channel)


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
    channels = _complete_path_channels(scene, flow)
    demand = _number(flow.properties, "demand_mbps")
    if channels is None or demand is None or demand <= 0:
        return None

    saturated_capacities: list[float] = []
    for channel in channels:
        original_capacity = _number(
            channel.properties,
            "original_capacity_mbps",
        )
        current_throughput = _maximum_channel_throughput(scene, channel)
        if (
            None in (original_capacity, current_throughput)
            or original_capacity <= 0
            or current_throughput < -FLOAT_TOLERANCE
            or current_throughput > original_capacity + FLOAT_TOLERANCE
        ):
            return None

        if current_throughput / original_capacity >= SATURATION_THRESHOLD:
            saturated_capacities.append(original_capacity)

    if not saturated_capacities:
        return None
    bottleneck_capacity = min(saturated_capacities)
    if bottleneck_capacity < demand:
        return "insufficient_channel_capacity"
    return "traffic_congestion"


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

    raw_carried_flows = channel.relations.get("carries")
    if not isinstance(raw_carried_flows, list) or not raw_carried_flows:
        return None
    carried_flow_ids: list[str] = []
    for raw_carried_flow in raw_carried_flows:
        if isinstance(raw_carried_flow, dict):
            flow_id = str(raw_carried_flow.get("data_flow_id", ""))
            bandwidth = _number(raw_carried_flow, "bandwidth_mbps")
            if not flow_id or bandwidth is None or bandwidth < -FLOAT_TOLERANCE:
                return None
            carried_flow_ids.append(flow_id)
        else:
            # Keep existing Twin files readable until they are regenerated.
            carried_flow_ids.append(str(raw_carried_flow))
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
    if channels is None or len(channels) < 2:
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


def infer_flow_failure_cause(scene: SceneData, flow: EntityRecord) -> str | None:
    if infer_flow_state(flow) != "failed":
        return None
    path = _complete_flow_path(scene, flow)
    physical_fault_id = _infer_unique_physical_fault(scene)
    if path is None or physical_fault_id is None:
        return None
    path_node_ids, path_channels = path
    if len(path_channels) < 2:
        return None
    if physical_fault_id in path_node_ids:
        return physical_fault_id
    if any(
        channel.entity_id == physical_fault_id
        for channel in path_channels
    ):
        return physical_fault_id
    return None
