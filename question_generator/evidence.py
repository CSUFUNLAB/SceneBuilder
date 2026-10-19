from __future__ import annotations

import math

from .scene import EntityRecord, SceneData


SATURATION_THRESHOLD = 0.95
DEGRADATION_EVIDENCE_THRESHOLD = 0.60
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
    values = (tx_packets, rx_packets, lost_packets, throughput, demand)
    if any(value is None or value < 0 for value in values):
        return None
    if (
        demand <= 0
        or rx_packets + lost_packets > tx_packets
        or (rx_packets == 0 and throughput > FLOAT_TOLERANCE)
    ):
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


def _channel_receive_rates(
    scene: SceneData,
    channel: EntityRecord,
) -> tuple[float, float] | None:
    """Check both endpoints, including the direction without a send sample."""
    connected = channel.relations.get("connects")
    if not isinstance(connected, list) or len(connected) != 2:
        return None
    if str(connected[0]) == str(connected[1]):
        return None
    rates: list[float] = []
    for nic_id in connected:
        nic = scene.entity("nic", str(nic_id))
        if nic is None or nic.relations.get("channel") != channel.entity_id:
            return None
        rate = _number(nic.properties, "rx_rate_mbps")
        if rate is None or rate < 0:
            return None
        rates.append(rate)
    return rates[0], rates[1]


def _channel_has_observed_outage(scene: SceneData, channel: EntityRecord) -> bool:
    rates = _channel_receive_rates(scene, channel)
    return (
        rates is not None
        and max(rates) <= FLOAT_TOLERANCE
        and _channel_directional_measurements(scene, channel) is not None
    )


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

    # Path overlap and zero throughput do not establish a local outage. There
    # must be a sampled send attempt, no peer delivery, and no delivery in the
    # reverse direction contradicting a static channel outage.
    if _channel_has_observed_outage(scene, channel):
        return "disabled"

    receive_rates = _channel_receive_rates(scene, channel)
    if receive_rates is None or max(receive_rates) <= FLOAT_TOLERANCE:
        return None

    throughput_ratio = current_throughput / original_capacity
    if throughput_ratio >= SATURATION_THRESHOLD:
        return "saturated"
    measurements = _channel_directional_measurements(scene, channel)
    if measurements is None:
        return None
    if any(delivered <= FLOAT_TOLERANCE for _, delivered in measurements):
        return None
    if any(
        delivered
        < min(original_capacity, offered) * DEGRADATION_EVIDENCE_THRESHOLD
        for offered, delivered in measurements
    ):
        return "degraded"

    # Normal describes service under the current load, not unchanged physical
    # capacity. A capacity reduction that still serves this load is normal.
    return "normal"


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


def infer_node_state(scene: SceneData, node: EntityRecord) -> str | None:
    """Infer availability, including isolation by failures of all adjacent links.

    Transmit attempts alone do not establish connectivity. Missing or idle
    links cannot establish an outage, and incomplete adjacency cannot establish
    that *all* links are unavailable. No physical root cause is implied.
    """
    raw_interfaces = node.relations.get("interfaces")
    if not isinstance(raw_interfaces, list) or not raw_interfaces:
        return None
    interface_ids = {str(nic_id) for nic_id in raw_interfaces}
    if len(interface_ids) != len(raw_interfaces) or interface_ids != {
        nic.entity_id
        for nic in scene.entities("nic")
        if nic.relations.get("node") == node.entity_id
    }:
        return None

    channels: dict[str, EntityRecord] = {}
    for nic_id in interface_ids:
        nic = scene.entity("nic", nic_id)
        if nic is None:
            return None
        channel = scene.entity("channel", str(nic.relations.get("channel", "")))
        if (
            channel is None
            or not isinstance(channel.relations.get("connects"), list)
            or nic_id not in channel.relations["connects"]
            or len(scene.channel_endpoint_nodes(channel)) != 2
        ):
            return None
        channels[channel.entity_id] = channel

    rates = [_channel_receive_rates(scene, channel) for channel in channels.values()]
    if any(pair is not None and max(pair) > FLOAT_TOLERANCE for pair in rates):
        return "normal"
    if all(_channel_has_observed_outage(scene, channel) for channel in channels.values()):
        return "disabled"
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
        # infer_channel_state already requires a sampled local send attempt.
        return "disabled"

    current_throughput = _maximum_channel_throughput(scene, channel)
    if current_throughput is None or current_throughput <= FLOAT_TOLERANCE:
        return None
    receive_rates = _channel_receive_rates(scene, channel)
    if receive_rates is None or max(receive_rates) <= FLOAT_TOLERANCE:
        return None

    queue_size = _number(nic.properties, "queue_size_packets")
    queue_current = _number(nic.properties, "queue_current_packets")
    if (
        None in (queue_size, queue_current)
        or queue_size <= 0
        or not 0 <= queue_current <= queue_size
    ):
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


def infer_flow_failure_locations(
    scene: SceneData,
    flow: EntityRecord,
) -> tuple[str, ...] | None:
    """Locate observed interruptions on this flow's declared path.

    An isolated node represents its unavailable incident links; those links are
    not reported again. Unsampled downstream silence is not an interruption
    observation. Multiple locations are allowed, independent of how many faults
    the scene generator injected or what happens on unrelated paths.
    """
    if (
        infer_flow_state(flow) != "failed"
        or _number(flow.properties, "tx_packets") < MIN_OFFERED_PACKET_SAMPLE
    ):
        return None
    path = _complete_flow_path(scene, flow)
    if path is None:
        return None
    path_node_ids, path_channels = path
    if len(path_channels) < 2:
        return None
    isolated_nodes = {
        node_id
        for node_id in path_node_ids
        if (node := scene.entity("node", node_id)) is not None
        and infer_node_state(scene, node) == "disabled"
    }
    interrupted_channels = {
        channel.entity_id
        for channel in path_channels
        if not isolated_nodes.intersection(scene.channel_endpoint_nodes(channel))
        and _channel_has_observed_outage(scene, channel)
    }
    return tuple(sorted(isolated_nodes | interrupted_channels))


def infer_flow_failure_cause(scene: SceneData, flow: EntityRecord) -> str | None:
    # The current dataset has a single-ID answer contract. This is an admission
    # check on observed locations, not an assumption of one fault in the scene.
    locations = infer_flow_failure_locations(scene, flow)
    return locations[0] if locations is not None and len(locations) == 1 else None
