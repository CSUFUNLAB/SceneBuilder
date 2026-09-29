from __future__ import annotations

import ipaddress
import math
from typing import Any

import networkx as nx

from ..rng import RandomManager
from ..utils.graph_utils import ordered_nodes
from ..utils.ip_mac import generate_unique_macs
from .nics import generate_queue_attributes, resolve_queue_policy_selection


WIFI_BSS_FIELDS = [
    "bss_id",
    "ap_node",
    "standard",
    "channel_number",
    "channel_width_mhz",
    "tx_power_dbm",
    "loss_exponent",
    "rate_manager",
    "state",
]

WIFI_INTERFACE_FIELDS = [
    "nic_id",
    "node",
    "interface_index",
    "bss_id",
    "wifi_role",
    "ip",
    "mac",
    "state",
    "device_type",
    "queue_layer",
    "queue_policy",
    "queue_size_packets",
]

WIFI_ASSOCIATION_FIELDS = [
    "association_id",
    "bss_id",
    "ap_node",
    "sta_node",
    "ap_nic_id",
    "sta_nic_id",
    "configured_state",
]

POSITION_FIELDS = [
    "node_id",
    "x_m",
    "y_m",
    "z_m",
    "mobility_model",
    "velocity_x_mps",
    "velocity_y_mps",
    "velocity_z_mps",
]


def _choose_weighted_mapping(
    values: dict[str, Any],
    rng: RandomManager,
    field_name: str,
) -> str:
    if not values:
        raise ValueError(f"{field_name} must not be empty")
    items = list(values)
    weights = [float(values[item]) for item in items]
    return str(rng.weighted_choice(items, weights))


def build_wired_graph(graph: nx.Graph, node_roles: dict[str, str]) -> nx.Graph:
    """Return the hybrid scene's wired backbone while retaining all nodes."""

    wired = graph.copy()
    for src, dst in list(wired.edges()):
        if "edge" in {str(node_roles.get(str(src), "")), str(node_roles.get(str(dst), ""))}:
            wired.remove_edge(src, dst)
    return wired


def _association_map(
    graph: nx.Graph,
    node_roles: dict[str, str],
) -> dict[str, str]:
    aps = [node for node in ordered_nodes(graph) if node_roles.get(node) == "aggregation"]
    stas = [node for node in ordered_nodes(graph) if node_roles.get(node) == "edge"]
    if stas and not aps:
        raise ValueError("hybrid mode requires at least one aggregation node to act as an AP")

    load = {ap: 0 for ap in aps}
    associations: dict[str, str] = {}
    for sta in stas:
        adjacent_aps = sorted(
            (str(neighbor) for neighbor in graph.neighbors(sta) if node_roles.get(str(neighbor)) == "aggregation"),
            key=lambda node: (load[node], node),
        )
        candidates = adjacent_aps or sorted(aps, key=lambda node: (load[node], node))
        if not candidates:
            raise ValueError(f"No AP candidate is available for STA {sta}")
        selected = candidates[0]
        associations[sta] = selected
        load[selected] += 1
    return associations


def _place_aps(
    aps: list[str],
    wifi_cfg: dict[str, Any],
    rng: RandomManager,
) -> dict[str, tuple[float, float, float]]:
    width = float(wifi_cfg.get("area_width_m", 200.0))
    height = float(wifi_cfg.get("area_height_m", 150.0))
    minimum = float(wifi_cfg.get("min_ap_distance_m", 25.0))
    z = float(wifi_cfg.get("ap_height_m", 2.5))
    positions: dict[str, tuple[float, float, float]] = {}

    margin_x = min(10.0, width / 10.0)
    margin_y = min(10.0, height / 10.0)
    for ap in aps:
        chosen: tuple[float, float, float] | None = None
        for _ in range(200):
            x = rng.uniform(margin_x, max(margin_x, width - margin_x))
            y = rng.uniform(margin_y, max(margin_y, height - margin_y))
            if all(math.hypot(x - px, y - py) >= minimum for px, py, _ in positions.values()):
                chosen = (x, y, z)
                break
        if chosen is None:
            index = len(positions)
            columns = max(1, int(math.ceil(math.sqrt(len(aps)))))
            rows = max(1, int(math.ceil(len(aps) / columns)))
            column = index % columns
            row = index // columns
            chosen = (
                width * (column + 1) / (columns + 1),
                height * (row + 1) / (rows + 1),
                z,
            )
        positions[ap] = chosen
    return positions


def generate_wifi_scene(
    graph: nx.Graph,
    config: Any,
    rng: RandomManager,
    node_roles: dict[str, str],
    wired_nics: list[dict[str, Any]],
    queue_selection: dict[str, Any] | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Generate a deterministic infrastructure Wi-Fi access layer."""

    wifi_cfg = dict(config.wifi)
    aps = [node for node in ordered_nodes(graph) if node_roles.get(node) == "aggregation"]
    associations_by_sta = _association_map(graph, node_roles)
    stas = list(associations_by_sta.keys())
    channels = [int(value) for value in wifi_cfg.get("channel_numbers", [1, 6, 11])]
    standard_probabilities = wifi_cfg.get("standard_probabilities")
    if isinstance(standard_probabilities, dict) and standard_probabilities:
        standard = _choose_weighted_mapping(
            standard_probabilities,
            rng.fork("standard"),
            "wifi.standard_probabilities",
        )
    else:
        standard = str(wifi_cfg.get("standard", "802.11g"))
    channel_width_mhz = int(wifi_cfg.get("channel_width_mhz", 20))
    tx_power_candidates = [
        float(value) for value in wifi_cfg.get("tx_power_candidates_dbm", [18.0])
    ]
    loss_exponent = float(
        rng.fork("loss_exponent").choice(
            [float(value) for value in wifi_cfg.get("loss_exponent_candidates", [3.0])]
        )
    )
    rate_manager = str(wifi_cfg.get("rate_manager", "IdealWifiManager"))

    channel_plan: list[int] = []
    while len(channel_plan) < len(aps):
        channel_plan.extend(channels)
    channel_plan = rng.fork("channel_plan").shuffled(channel_plan[: len(aps)])

    base_network = ipaddress.ip_network(str(wifi_cfg.get("ip_cidr", "198.18.0.0/15")), strict=False)
    subnet_prefix = int(wifi_cfg.get("subnet_prefix", 24))
    subnets = base_network.subnets(new_prefix=subnet_prefix)

    interface_counts: dict[str, int] = {}
    highest_nic_id = 0
    highest_channel_id = 0
    for nic in wired_nics:
        node = str(nic["node"])
        interface_counts[node] = max(interface_counts.get(node, 0), int(nic["interface_index"]))
        text = str(nic["nic_id"])
        if text.startswith("IF") and text[2:].isdigit():
            highest_nic_id = max(highest_nic_id, int(text[2:]))
        channel_id = str(nic["channel_id"])
        if not (channel_id.startswith("C") and channel_id[1:].isdigit()):
            raise ValueError(f"Generated wired channel ID must be C followed by digits: {channel_id}")
        highest_channel_id = max(highest_channel_id, int(channel_id[1:]))

    macs = iter(generate_unique_macs(len(aps) + len(stas), rng, locally_administered=True))
    bss_rows: list[dict[str, Any]] = []
    interface_rows: list[dict[str, Any]] = []
    association_rows: list[dict[str, Any]] = []
    interface_by_node: dict[str, dict[str, Any]] = {}

    sta_by_ap: dict[str, list[str]] = {ap: [] for ap in aps}
    for sta, ap in associations_by_sta.items():
        sta_by_ap[ap].append(sta)

    next_nic_id = highest_nic_id + 1
    for ap_index, ap in enumerate(aps, start=1):
        subnet = next(subnets)
        hosts = subnet.hosts()
        ap_address = next(hosts)
        bss_id = f"C{highest_channel_id + ap_index:04d}"
        interface_counts[ap] = interface_counts.get(ap, 0) + 1
        ap_nic = {
            "nic_id": f"IF{next_nic_id:04d}",
            "node": ap,
            "interface_index": interface_counts[ap],
            "bss_id": bss_id,
            "wifi_role": "ap",
            "ip": f"{ap_address}/{subnet.prefixlen}",
            "mac": next(macs),
            "state": "normal",
        }
        next_nic_id += 1
        interface_rows.append(ap_nic)
        interface_by_node[ap] = ap_nic
        bss_rows.append(
            {
                "bss_id": bss_id,
                "ap_node": ap,
                "standard": standard,
                "channel_number": channel_plan[ap_index - 1],
                "channel_width_mhz": channel_width_mhz,
                # Keep the old seed namespace so an ID-only change does not change power.
                # This internal RNG token is not an exported channel ID.
                "tx_power_dbm": rng.fork(f"tx_power:BSS{ap_index:04d}").choice(tx_power_candidates),
                "loss_exponent": loss_exponent,
                "rate_manager": rate_manager,
                "state": "normal",
            }
        )

        for sta in sorted(sta_by_ap[ap]):
            interface_counts[sta] = interface_counts.get(sta, 0) + 1
            sta_nic = {
                "nic_id": f"IF{next_nic_id:04d}",
                "node": sta,
                "interface_index": interface_counts[sta],
                "bss_id": bss_id,
                "wifi_role": "sta",
                "ip": f"{next(hosts)}/{subnet.prefixlen}",
                "mac": next(macs),
                "state": "normal",
            }
            next_nic_id += 1
            interface_rows.append(sta_nic)
            interface_by_node[sta] = sta_nic
            association_rows.append(
                {
                    "association_id": f"A{len(association_rows) + 1:04d}",
                    "bss_id": bss_id,
                    "ap_node": ap,
                    "sta_node": sta,
                    "ap_nic_id": ap_nic["nic_id"],
                    "sta_nic_id": sta_nic["nic_id"],
                    "configured_state": "enabled",
                }
            )

    placement_rng = rng.fork("placement")
    ap_positions = _place_aps(aps, wifi_cfg, placement_rng)
    positions: dict[str, tuple[float, float, float]] = dict(ap_positions)
    min_distance, max_distance = [float(value) for value in wifi_cfg.get("sta_distance_range_m", [5.0, 30.0])]
    width = float(wifi_cfg.get("area_width_m", 200.0))
    height = float(wifi_cfg.get("area_height_m", 150.0))
    sta_height = float(wifi_cfg.get("sta_height_m", 1.0))
    for sta, ap in associations_by_sta.items():
        ap_x, ap_y, _ = ap_positions[ap]
        radius = placement_rng.uniform(min_distance, max_distance)
        angle = placement_rng.uniform(0.0, 2.0 * math.pi)
        x = min(width, max(0.0, ap_x + radius * math.cos(angle)))
        y = min(height, max(0.0, ap_y + radius * math.sin(angle)))
        positions[sta] = (x, y, sta_height)

    for node in ordered_nodes(graph):
        if node not in positions:
            index = len(positions)
            positions[node] = (5.0 + index * 2.0, 5.0, 1.5)

    moving_ratio = float(wifi_cfg.get("moving_sta_ratio", 0.0))
    moving_count = min(len(stas), max(0, int(round(len(stas) * moving_ratio))))
    moving_stas = set(rng.fork("moving_stas").sample(stas, moving_count))
    min_speed, max_speed = [
        float(value) for value in wifi_cfg.get("sta_speed_range_mps", [0.5, 1.0])
    ]
    motion_rng = rng.fork("motion")
    position_rows: list[dict[str, Any]] = []
    for node in ordered_nodes(graph):
        velocity_x = 0.0
        velocity_y = 0.0
        mobility_model = "constant"
        if node in moving_stas:
            speed = motion_rng.uniform(min_speed, max_speed)
            angle = motion_rng.uniform(0.0, 2.0 * math.pi)
            velocity_x = speed * math.cos(angle)
            velocity_y = speed * math.sin(angle)
            mobility_model = "constant_velocity"
        position_rows.append(
            {
                "node_id": node,
                "x_m": round(positions[node][0], 6),
                "y_m": round(positions[node][1], 6),
                "z_m": round(positions[node][2], 6),
                "mobility_model": mobility_model,
                "velocity_x_mps": round(velocity_x, 6),
                "velocity_y_mps": round(velocity_y, 6),
                "velocity_z_mps": 0.0,
            }
        )

    queue_rng = rng.fork("queues")
    queue_selection = queue_selection or resolve_queue_policy_selection(config.nics, queue_rng)
    for interface in interface_rows:
        interface.update(generate_queue_attributes(
            config.nics, queue_rng, queue_selection, node_roles.get(interface["node"], "")))
        interface["device_type"] = "wifi"

    wifi_metadata = {
        "standard": standard,
        "standard_probabilities": dict(standard_probabilities or {}),
        "channel_numbers": channel_plan,
        "channel_width_mhz": channel_width_mhz,
        "tx_power_candidates_dbm": tx_power_candidates,
        "loss_exponent": loss_exponent,
        "rate_manager": rate_manager,
        "moving_sta_ratio": moving_ratio,
        "moving_sta_count": moving_count,
        "sta_speed_range_mps": [min_speed, max_speed],
    }

    return bss_rows, interface_rows, association_rows, position_rows, wifi_metadata
