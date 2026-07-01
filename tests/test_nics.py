import ipaddress

from scene_generator.generators.nics import generate_nics
from scene_generator.rng import RandomManager


class _Config:
    nics = {
        "queue_policy_mode": "mixed",
        "queue_policy_mode_probabilities": {},
        "queue_policy_candidates": ["FIFO"],
        "queue_policy_probabilities": {},
        "single_queue_policy": "FIFO",
        "single_queue_policy_probabilities": {},
        "queue_size_range_packets": [128, 128],
        "ip_cidr": "10.0.0.0/24",
        "ip_cidr_candidates": [],
        "link_subnet_prefix": 30,
        "link_subnet_prefix_probabilities": {},
        "state_probabilities": {"normal": 1.0},
    }


def test_generate_nics_creates_one_nic_per_channel_endpoint() -> None:
    channel_rows = [
        {"channel_id": "C0001", "src": "1", "dst": "2"},
        {"channel_id": "C0002", "src": "2", "dst": "3"},
    ]

    rows = generate_nics(channel_rows, _Config(), RandomManager(1))

    assert len(rows) == 4
    assert rows[0]["nic_id"] == "IF0001"
    assert rows[0]["node"] == "1"
    assert rows[0]["interface_index"] == 1
    assert rows[0]["channel_id"] == "C0001"
    assert rows[1]["node"] == "2"
    assert rows[1]["interface_index"] == 1
    assert rows[1]["channel_id"] == "C0001"
    assert rows[2]["node"] == "2"
    assert rows[2]["interface_index"] == 2
    assert rows[2]["channel_id"] == "C0002"
    assert rows[3]["node"] == "3"
    assert rows[3]["interface_index"] == 1
    assert rows[3]["channel_id"] == "C0002"
    assert rows[3]["nic_id"] == "IF0004"
    assert all(row["queue_policy"] == "FIFO" for row in rows)
    assert all(row["queue_size_packets"] == 128 for row in rows)
    assert all(row["state"] == "normal" for row in rows)
    nic1 = ipaddress.ip_interface(str(rows[0]["ip"]))
    nic2 = ipaddress.ip_interface(str(rows[1]["ip"]))
    nic3 = ipaddress.ip_interface(str(rows[2]["ip"]))
    nic4 = ipaddress.ip_interface(str(rows[3]["ip"]))
    assert nic1.ip in ipaddress.ip_network("10.0.0.0/24")
    assert nic3.ip in ipaddress.ip_network("10.0.0.0/24")
    assert nic1.network.prefixlen == 30
    assert nic3.network.prefixlen == 30
    assert nic1.network == nic2.network
    assert nic3.network == nic4.network
    assert nic1.network != nic3.network


class _SingleQueueConfig:
    nics = {
        "queue_policy_mode": "mixed",
        "queue_policy_mode_probabilities": {"single": 1.0},
        "queue_policy_candidates": ["FIFO", "RED"],
        "queue_policy_probabilities": {},
        "single_queue_policy": "FIFO",
        "single_queue_policy_probabilities": {"RED": 1.0},
        "queue_size_range_packets": [64, 64],
        "ip_cidr": "10.0.1.0/24",
        "ip_cidr_candidates": [],
        "link_subnet_prefix": 30,
        "link_subnet_prefix_probabilities": {},
        "state_probabilities": {"disabled": 1.0},
    }


def test_generate_nics_can_use_single_queue_mode() -> None:
    channel_rows = [{"channel_id": "C0001", "src": "1", "dst": "2"}]

    rows = generate_nics(channel_rows, _SingleQueueConfig(), RandomManager(2))

    assert len(rows) == 2
    assert all(row["queue_policy"] == "RED" for row in rows)
    assert all(row["state"] == "disabled" for row in rows)


class _RoleSizedConfig:
    nics = {
        "queue_policy_mode": "mixed",
        "queue_policy_mode_probabilities": {},
        "queue_policy_candidates": ["FIFO"],
        "queue_policy_probabilities": {},
        "single_queue_policy": "FIFO",
        "single_queue_policy_probabilities": {},
        "queue_size_range_packets": [64, 256],
        "ip_cidr": "10.0.2.0/24",
        "ip_cidr_candidates": [],
        "link_subnet_prefix": 30,
        "link_subnet_prefix_probabilities": {},
        "state_probabilities": {"normal": 1.0},
    }


def test_generate_nics_uses_same_queue_size_for_same_node_role() -> None:
    channel_rows = [
        {"channel_id": "C0001", "src": "1", "dst": "3"},
        {"channel_id": "C0002", "src": "2", "dst": "3"},
    ]
    node_roles = {
        "1": "edge",
        "2": "edge",
        "3": "core",
    }
    selection = {
        "selected_mode": "mixed",
        "active_rule": {
            "queue_policy_candidates": ["FIFO"],
        },
    }

    rows = generate_nics(channel_rows, _RoleSizedConfig(), RandomManager(3), selection=selection, node_roles=node_roles)

    queue_sizes_by_node = {str(row["node"]): int(row["queue_size_packets"]) for row in rows}
    assert queue_sizes_by_node["1"] == queue_sizes_by_node["2"]
    assert rows[1]["queue_size_packets"] == rows[3]["queue_size_packets"]
    assert selection["active_rule"]["queue_size_packets_by_role"]["edge"] == queue_sizes_by_node["1"]
    assert selection["active_rule"]["queue_size_packets_by_role"]["core"] == queue_sizes_by_node["3"]


def test_generate_nics_records_per_channel_subnet_metadata() -> None:
    channel_rows = [{"channel_id": "C0001", "src": "1", "dst": "2"}]
    selection = {
        "selected_mode": "mixed",
        "active_rule": {
            "queue_policy_candidates": ["FIFO"],
        },
    }

    rows = generate_nics(channel_rows, _Config(), RandomManager(4), selection=selection)

    assert len(rows) == 2
    assert selection["active_rule"]["ip_assignment"] == "per_channel_random_subnet"
    assert selection["active_rule"]["ip_cidr_counts"] == {"10.0.0.0/24": 1}
    assert selection["active_rule"]["channel_subnet_prefix_counts"] == {"30": 1}
    assert ipaddress.ip_interface(str(rows[0]["ip"])).network.prefixlen == 30


class _RandomSubnetConfig:
    nics = {
        "queue_policy_mode": "mixed",
        "queue_policy_mode_probabilities": {},
        "queue_policy_candidates": ["FIFO"],
        "queue_policy_probabilities": {},
        "single_queue_policy": "FIFO",
        "single_queue_policy_probabilities": {},
        "queue_size_range_packets": [64, 64],
        "ip_cidr": "10.0.0.0/8",
        "ip_cidr_candidates": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        "ip_cidr_probabilities": {
            "172.16.0.0/12": 1.0,
        },
        "link_subnet_prefix": 30,
        "link_subnet_prefix_probabilities": {
            29: 1.0,
        },
        "state_probabilities": {"disabled": 1.0},
    }


def test_generate_nics_can_use_non_default_ip_pool_and_prefix() -> None:
    channel_rows = [
        {"channel_id": "C0001", "src": "1", "dst": "2"},
        {"channel_id": "C0002", "src": "2", "dst": "3"},
    ]
    selection = {
        "selected_mode": "mixed",
        "active_rule": {
            "queue_policy_candidates": ["FIFO"],
        },
    }

    rows = generate_nics(channel_rows, _RandomSubnetConfig(), RandomManager(5), selection=selection)

    assert len(rows) == 4
    pool = ipaddress.ip_network("172.16.0.0/12")
    assert all(ipaddress.ip_interface(str(row["ip"])).ip in pool for row in rows)
    assert all(row["state"] == "disabled" for row in rows)
    assert ipaddress.ip_interface(str(rows[0]["ip"])).network.prefixlen == 29
    assert ipaddress.ip_interface(str(rows[2]["ip"])).network.prefixlen == 29
    assert selection["active_rule"]["ip_cidr_counts"] == {"172.16.0.0/12": 2}
    assert selection["active_rule"]["channel_subnet_prefix_counts"] == {"29": 2}
