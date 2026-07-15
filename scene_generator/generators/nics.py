from __future__ import annotations

from typing import Any

from ..rng import RandomManager
from ..utils.ip_mac import generate_channel_interface_ips, generate_unique_macs
from ..utils.selection import weighted_pick

NIC_FIELDS = ["nic_id", "node", "interface_index", "channel_id", "ip", "mac", "queue_policy", "queue_size_packets", "state"]


def resolve_queue_policy_selection(nics_cfg: dict[str, Any], rng: RandomManager) -> dict[str, Any]:
    mode_probabilities = dict(nics_cfg.get("queue_policy_mode_probabilities", {}))
    if mode_probabilities:
        selected_mode = weighted_pick(mode_probabilities, "mixed", rng)
    else:
        selected_mode = str(nics_cfg.get("queue_policy_mode", "mixed"))

    single_queue_policy_probabilities = dict(nics_cfg.get("single_queue_policy_probabilities", {}))
    if single_queue_policy_probabilities:
        selected_single_queue_policy = weighted_pick(
            single_queue_policy_probabilities,
            str(nics_cfg.get("single_queue_policy", "FIFO")),
            rng,
        )
    else:
        selected_single_queue_policy = str(nics_cfg.get("single_queue_policy", "FIFO"))

    if selected_mode == "single":
        return {
            "selected_mode": selected_mode,
            "active_rule": {
                "queue_policy": selected_single_queue_policy,
            },
        }

    queue_policy_probabilities = dict(nics_cfg.get("queue_policy_probabilities", {}))
    if queue_policy_probabilities:
        return {
            "selected_mode": selected_mode,
            "active_rule": {
                "queue_policy_probabilities": queue_policy_probabilities,
            },
        }

    return {
        "selected_mode": selected_mode,
        "active_rule": {
            "queue_policy_candidates": [str(item) for item in nics_cfg.get("queue_policy_candidates", [])],
        },
    }


def _choose_queue_policy(
    nics_cfg: dict[str, Any],
    rng: RandomManager,
    selection: dict[str, Any],
) -> str:
    mode = str(selection.get("selected_mode", "mixed"))
    active_rule = dict(selection.get("active_rule", {}))

    if mode == "single":
        return str(active_rule.get("queue_policy", "FIFO"))

    probabilities = dict(active_rule.get("queue_policy_probabilities", {}))
    if probabilities:
        return weighted_pick(probabilities, "FIFO", rng)

    queue_candidates = [str(item) for item in active_rule.get("queue_policy_candidates", ["FIFO", "RED", "CoDel", "FqCoDel"])]
    return str(rng.choice(queue_candidates))


def _resolve_queue_size_by_role(
    nics_cfg: dict[str, Any],
    node_roles: dict[str, str],
    rng: RandomManager,
    selection: dict[str, Any],
) -> dict[str, int]:
    queue_size_range = nics_cfg.get("queue_size_range_packets", [128, 2048])
    q_low, q_high = int(queue_size_range[0]), int(queue_size_range[1])

    role_queue_sizes: dict[str, int] = {}
    for role in sorted({str(role) for role in node_roles.values()}):
        role_queue_sizes[role] = rng.randint(q_low, q_high)

    selection.setdefault("active_rule", {})
    selection["active_rule"]["queue_size_packets_by_role"] = dict(role_queue_sizes)
    return role_queue_sizes


def generate_nics(
    channel_rows: list[dict[str, Any]],
    config: Any,
    rng: RandomManager,
    selection: dict[str, Any] | None = None,
    node_roles: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    nics_cfg = config.nics
    queue_selection = selection or resolve_queue_policy_selection(nics_cfg, rng)

    ip_cidr = str(nics_cfg.get("ip_cidr", "10.0.0.0/8"))
    ip_cidr_candidates = [str(item) for item in nics_cfg.get("ip_cidr_candidates", [])]
    ip_cidr_probabilities = {str(key): float(value) for key, value in dict(nics_cfg.get("ip_cidr_probabilities", {})).items()}
    link_subnet_prefix = int(nics_cfg.get("link_subnet_prefix", 30))
    link_subnet_prefix_probabilities = {
        int(key): float(value)
        for key, value in dict(nics_cfg.get("link_subnet_prefix_probabilities", {})).items()
    }

    queue_size_range = nics_cfg.get("queue_size_range_packets", [128, 2048])
    q_low, q_high = int(queue_size_range[0]), int(queue_size_range[1])
    role_queue_sizes = _resolve_queue_size_by_role(nics_cfg, node_roles or {}, rng, queue_selection) if node_roles else {}

    nic_count = len(channel_rows) * 2
    channel_interface_ips, ip_cidr_counts, channel_subnet_prefix_counts = generate_channel_interface_ips(
        ip_cidr,
        len(channel_rows),
        link_subnet_prefix,
        rng,
        cidr_candidates=ip_cidr_candidates,
        ip_cidr_probabilities=ip_cidr_probabilities,
        channel_subnet_prefix_probabilities=link_subnet_prefix_probabilities,
    )
    macs = generate_unique_macs(nic_count, rng)
    queue_selection.setdefault("active_rule", {})
    queue_selection["active_rule"]["ip_assignment"] = "per_channel_random_subnet"
    queue_selection["active_rule"]["ip_cidr_counts"] = dict(ip_cidr_counts)
    queue_selection["active_rule"]["channel_subnet_prefix_counts"] = dict(channel_subnet_prefix_counts)

    rows: list[dict[str, Any]] = []
    nic_idx = 1
    interface_counts_by_node: dict[str, int] = {}

    for channel_index, channel in enumerate(channel_rows):
        channel_id = str(channel["channel_id"])
        left_ip, right_ip = channel_interface_ips[channel_index]
        for node, ip in ((str(channel["src"]), left_ip), (str(channel["dst"]), right_ip)):
            interface_counts_by_node[node] = int(interface_counts_by_node.get(node, 0)) + 1
            node_role = str(node_roles.get(node, "")) if node_roles else ""
            if node_role and node_role in role_queue_sizes:
                queue_size_packets = role_queue_sizes[node_role]
            else:
                queue_size_packets = rng.randint(q_low, q_high)
            rows.append(
                {
                    "nic_id": f"IF{nic_idx:04d}",
                    "node": node,
                    "interface_index": interface_counts_by_node[node],
                    "channel_id": channel_id,
                    "ip": ip,
                    "mac": macs[nic_idx - 1],
                    "queue_policy": _choose_queue_policy(nics_cfg, rng, queue_selection),
                    "queue_size_packets": queue_size_packets,
                    "state": "normal",
                }
            )
            nic_idx += 1

    return rows
