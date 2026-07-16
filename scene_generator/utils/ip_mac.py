from __future__ import annotations

import ipaddress
from typing import Iterable

from ..rng import RandomManager


def _parse_networks(cidr: str, cidr_candidates: Iterable[str] | None) -> list[ipaddress._BaseNetwork]:
    raw_values = [str(item) for item in (cidr_candidates or [cidr])]
    if not raw_values:
        raw_values = [str(cidr)]

    networks: list[ipaddress._BaseNetwork] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        network = ipaddress.ip_network(raw_value, strict=False)
        text = str(network)
        if text in seen:
            continue
        seen.add(text)
        networks.append(network)

    if len({network.version for network in networks}) > 1:
        raise ValueError("ip_cidr and ip_cidr_candidates must use the same IP version")

    for index, left in enumerate(networks):
        for right in networks[index + 1 :]:
            if left.overlaps(right):
                raise ValueError(f"Overlapping IP CIDR candidates are not supported: {left} vs {right}")

    return networks


def _normalize_prefix_weights(
    channel_subnet_prefix: int,
    channel_subnet_prefix_probabilities: dict[int | str, float] | None,
) -> dict[int, float]:
    if not channel_subnet_prefix_probabilities:
        return {int(channel_subnet_prefix): 1.0}

    normalized: dict[int, float] = {}
    for raw_prefix, raw_weight in channel_subnet_prefix_probabilities.items():
        normalized[int(raw_prefix)] = float(raw_weight)
    return normalized


def _network_weights(
    networks: list[ipaddress._BaseNetwork],
    ip_cidr_probabilities: dict[str, float] | None,
) -> dict[str, float]:
    if not ip_cidr_probabilities:
        return {str(network): 1.0 for network in networks}

    return {str(network): float(ip_cidr_probabilities.get(str(network), 0.0)) for network in networks}


def _candidate_subnet(
    pool: ipaddress._BaseNetwork,
    prefix: int,
    index: int,
) -> ipaddress._BaseNetwork:
    subnet_size = 1 << (int(pool.max_prefixlen) - int(prefix))
    network_int = int(pool.network_address) + int(index) * subnet_size
    return ipaddress.ip_network((network_int, int(prefix)))


def _find_random_available_subnet(
    pool: ipaddress._BaseNetwork,
    prefix: int,
    used_subnets: list[ipaddress._BaseNetwork],
    rng: RandomManager,
) -> ipaddress._BaseNetwork | None:
    if int(prefix) <= int(pool.prefixlen):
        return None

    total_subnets = 1 << (int(prefix) - int(pool.prefixlen))
    if total_subnets <= 0:
        return None

    attempts = min(max(32, len(used_subnets) * 4), total_subnets)
    tried_indices: set[int] = set()

    for _ in range(attempts):
        candidate_index = rng.randint(0, total_subnets - 1)
        if candidate_index in tried_indices:
            continue
        tried_indices.add(candidate_index)
        candidate = _candidate_subnet(pool, prefix, candidate_index)
        if any(candidate.overlaps(existing) for existing in used_subnets):
            continue
        return candidate

    if total_subnets > 4096:
        return None

    start_index = rng.randint(0, total_subnets - 1)
    for offset in range(total_subnets):
        candidate_index = (start_index + offset) % total_subnets
        candidate = _candidate_subnet(pool, prefix, candidate_index)
        if any(candidate.overlaps(existing) for existing in used_subnets):
            continue
        return candidate

    return None


def generate_channel_interface_ips(
    cidr: str,
    channel_count: int,
    channel_subnet_prefix: int,
    rng: RandomManager,
    cidr_candidates: list[str] | None = None,
    ip_cidr_probabilities: dict[str, float] | None = None,
    channel_subnet_prefix_probabilities: dict[int | str, float] | None = None,
) -> tuple[list[tuple[str, str]], dict[str, int], dict[str, int]]:
    if channel_count < 0:
        raise ValueError("channel_count must be non-negative")
    if channel_count == 0:
        return [], {}, {}

    networks = _parse_networks(cidr, cidr_candidates)
    prefix_weights = _normalize_prefix_weights(channel_subnet_prefix, channel_subnet_prefix_probabilities)
    network_weights = _network_weights(networks, ip_cidr_probabilities)
    network_index = {str(network): network for network in networks}
    used_subnets: dict[str, list[ipaddress._BaseNetwork]] = {str(network): [] for network in networks}

    interface_pairs: list[tuple[str, str]] = []
    network_counts: dict[str, int] = {}
    prefix_counts: dict[str, int] = {}

    for _ in range(channel_count):
        prefixes = list(prefix_weights.keys())
        prefix_choice = int(rng.weighted_choice(prefixes, [prefix_weights[prefix] for prefix in prefixes]))

        pool_candidates = list(networks)
        chosen_subnet = None
        while pool_candidates:
            pool_names = [str(pool) for pool in pool_candidates]
            pool_weights = [network_weights.get(str(pool), 0.0) for pool in pool_candidates]
            if sum(pool_weights) > 0:
                chosen_pool_name = str(rng.weighted_choice(pool_names, pool_weights))
                chosen_pool = network_index[chosen_pool_name]
            else:
                chosen_pool = rng.choice(pool_candidates)

            subnet = _find_random_available_subnet(chosen_pool, prefix_choice, used_subnets[str(chosen_pool)], rng)
            if subnet is not None:
                chosen_subnet = subnet
                used_subnets[str(chosen_pool)].append(subnet)
                network_counts[str(chosen_pool)] = network_counts.get(str(chosen_pool), 0) + 1
                prefix_counts[str(prefix_choice)] = prefix_counts.get(str(prefix_choice), 0) + 1
                break

            pool_candidates = [pool for pool in pool_candidates if str(pool) != str(chosen_pool)]

        if chosen_subnet is None:
            raise ValueError("Unable to allocate enough non-overlapping channel subnets from configured IP pools")

        hosts = list(chosen_subnet.hosts())
        if len(hosts) < 2:
            raise ValueError(f"Subnet {chosen_subnet} does not provide two usable host addresses")

        interface_pairs.append(
            (
                f"{hosts[0]}/{chosen_subnet.prefixlen}",
                f"{hosts[1]}/{chosen_subnet.prefixlen}",
            )
        )

    return interface_pairs, dict(sorted(network_counts.items())), dict(sorted(prefix_counts.items()))


def _format_mac(parts: list[int]) -> str:
    return ":".join(f"{part:02x}" for part in parts)


def generate_unique_macs(
    count: int,
    rng: RandomManager,
    locally_administered: bool = True,
) -> list[str]:
    if count < 0:
        raise ValueError("count must be non-negative")

    macs: list[str] = []
    used: set[str] = set()

    while len(macs) < count:
        first = 0x02 if locally_administered else 0x00
        parts = [first] + [rng.randint(0, 255) for _ in range(5)]
        mac = _format_mac(parts)
        if mac in used:
            continue
        used.add(mac)
        macs.append(mac)

    return macs
