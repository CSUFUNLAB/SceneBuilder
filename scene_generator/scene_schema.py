"""Shared output order and checks for the unified JSONL scene contract.

IDs remain scene-local. Validation only checks the generated scene files.
Legacy CSV/BSS inputs are still accepted by the separate ns-3 input reader.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

NIC_COMMON_FIELDS = (
    "nic_id", "node", "interface_index", "interface_type", "device_type", "channel_id",
    "ip", "mac", "queue_policy", "queue_size_packets", "queue_layer", "state",
)
CHANNEL_COMMON_FIELDS = ("channel_id", "medium_type", "state")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _index(rows: list[dict[str, Any]], key: str, pattern: str) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        value = row.get(key)
        _require(isinstance(value, str) and re.fullmatch(pattern, value) is not None,
                 f"Invalid {key}: {value!r}")
        _require(value not in result, f"Duplicate {key}: {value}")
        result[value] = row
    return result


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def validate_scene_records(nodes, nics, channels, routes, traffic) -> dict[str, int]:
    """Check generated field order/types, IDs, and references without changing records."""
    node_by_id = _index(nodes, "node_id", r"N[0-9]{4,}")
    nic_by_id = _index(nics, "nic_id", r"IF[0-9]{4,}")
    channel_by_id = _index(channels, "channel_id", r"C[0-9]{4,}")
    _index(traffic, "flow_id", r"F[0-9]{6,}")
    interfaces = set()
    members = {cid: [] for cid in channel_by_id}
    for row in nodes:
        _require(row.get("state") in {"normal", "disabled"}, "Invalid node state")
        _require(isinstance(row.get("role"), str), "Node role must be a string")
        for key in ("x_m", "y_m", "z_m", "velocity_x_mps", "velocity_y_mps", "velocity_z_mps"):
            if key in row:
                _require(_number(row[key]), f"{key} must be a finite number")
    for row in nics:
        nid = row["nic_id"]
        kind = row.get("interface_type")
        _require(kind in {"wired", "wifi"}, f"Invalid interface_type for {nid}")
        expected_keys = (*NIC_COMMON_FIELDS, *(("wifi_role",) if kind == "wifi" else ()))
        _require(tuple(row) == expected_keys, f"Incorrect NIC fields/order: {nid}")
        for key in NIC_COMMON_FIELDS:
            if key not in {"interface_index", "queue_size_packets"}:
                _require(isinstance(row[key], str) and bool(row[key]), f"{nid}.{key} must be a nonempty string")
        _require(row["device_type"] == ("wifi" if kind == "wifi" else "point_to_point"), f"Invalid device_type for {nid}")
        _require(type(row["interface_index"]) is int and row["interface_index"] >= 1, f"Invalid interface_index for {nid}")
        _require(type(row["queue_size_packets"]) is int and 1 <= row["queue_size_packets"] <= 1000000,
                 f"Invalid queue_size_packets for {nid}")
        _require(row["queue_policy"] in {"FIFO", "RED", "CoDel", "FqCoDel"}, f"Invalid queue policy for {nid}")
        _require(row["queue_layer"] == "traffic_control", f"Invalid queue layer for {nid}")
        _require(row["state"] in {"normal", "disabled"}, f"Invalid state for {nid}")
        _require(row["node"] in node_by_id, f"Unknown node for {nid}")
        _require(row["channel_id"] in channel_by_id, f"Unknown channel for {nid}")
        _require(channel_by_id[row["channel_id"]].get("medium_type") == kind, f"NIC/channel type mismatch: {nid}")
        local_key = (row["node"], row["interface_index"])
        _require(local_key not in interfaces, f"Duplicate node/interface_index: {local_key}")
        interfaces.add(local_key)
        members[row["channel_id"]].append(nid)
        if kind == "wifi":
            _require(row["wifi_role"] in {"ap", "sta"}, f"Invalid wifi_role for {nid}")
    for row in channels:
        cid = row["channel_id"]
        _require(tuple(row)[:len(CHANNEL_COMMON_FIELDS)] == CHANNEL_COMMON_FIELDS, f"Incorrect channel field order: {cid}")
        kind = row.get("medium_type")
        _require(kind in {"wired", "wifi"}, f"Invalid medium_type: {cid}")
        _require(row.get("state") in ({"normal", "disabled", "degraded"} if kind == "wired" else {"normal", "disabled"}),
                 f"Invalid channel state: {cid}")
        if kind == "wired":
            _require(row.get("src") in node_by_id and row.get("dst") in node_by_id, f"Unknown endpoint: {cid}")
            _require(row["src"] != row["dst"], f"Identical wired endpoints: {cid}")
            _require(len(members[cid]) == 2 and {nic_by_id[n]["node"] for n in members[cid]} == {row["src"], row["dst"]},
                     f"Wired membership mismatch: {cid}")
            for key in ("bandwidth_mbps", "capacity_multiplier"):
                _require(_number(row.get(key)) and row[key] >= 0, f"Invalid {key}: {cid}")
        else:
            _require("ssid" not in row, f"SSID is derived from channel_id, not a scene field: {cid}")
            _require(len(cid.encode("utf-8")) <= 32, f"WiFi channel_id exceeds 32-byte SSID limit: {cid}")
            ap = row.get("ap_nic_id")
            stas = row.get("sta_nic_ids")
            _require(isinstance(ap, str) and ap in nic_by_id, f"Unknown AP NIC: {cid}")
            _require(isinstance(stas, list) and all(isinstance(n, str) for n in stas), f"sta_nic_ids must be a string array: {cid}")
            _require(len(stas) == len(set(stas)) and ap not in stas, f"Invalid STA membership: {cid}")
            _require(set(members[cid]) == {ap, *stas}, f"WiFi membership mismatch: {cid}")
            _require(nic_by_id[ap].get("wifi_role") == "ap", f"Invalid AP role: {cid}")
            _require(all(nic_by_id[n].get("wifi_role") == "sta" for n in stas), f"Invalid STA role: {cid}")
            for key in ("standard", "rate_manager"):
                _require(isinstance(row.get(key), str) and bool(row[key]), f"{key} must be a string: {cid}")
            for key in ("channel_number", "channel_width_mhz"):
                _require(type(row.get(key)) is int and row[key] > 0, f"{key} must be a positive integer: {cid}")
            for key in ("tx_power_dbm", "loss_exponent"):
                _require(_number(row.get(key)), f"{key} must be a finite number: {cid}")
    route_keys = set()
    for row in routes:
        _require(all(isinstance(row.get(k), str) and row[k] in node_by_id for k in ("src", "dst", "next_hop")),
                 "Unknown route node")
        key = (row["src"], row["dst"])
        _require(key not in route_keys, f"Duplicate route: {key}")
        route_keys.add(key)
        _require(type(row.get("egress_interface")) is int and (row["src"], row["egress_interface"]) in interfaces,
                 f"Unknown route egress interface: {key}")
    for row in traffic:
        _require(all(isinstance(row.get(k), str) and row[k] in node_by_id for k in ("src", "dst")), "Unknown flow endpoint")
        _require(_number(row.get("demand_mbps")) and row["demand_mbps"] >= 0, "Invalid demand_mbps")
    return {"nodes": len(nodes), "nics": len(nics), "channels": len(channels), "routes": len(routes), "flows": len(traffic)}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL without silently accepting duplicate fields."""
    return [json.loads(line, object_pairs_hook=_unique_object)
            for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def validate_scene_directory(directory: Path) -> dict[str, int]:
    return validate_scene_records(*(read_jsonl(directory / name) for name in (
        "nodes.jsonl", "nics.jsonl", "channels.jsonl", "routes.jsonl", "traffic.jsonl")))
