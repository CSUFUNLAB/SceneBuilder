from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scene_generator.config import SceneConfig

ALLOWED_SOURCE_TYPES = {"brite", "topologyzoo"}
ALLOWED_LINK_MODES = {"pure_random", "role_based_random"}
ALLOWED_QUEUE_POLICIES = {"FIFO", "RED", "CoDel", "FqCoDel"}
ALLOWED_QUEUE_POLICY_MODES = {"mixed", "single"}
ALLOWED_NODE_ASSIGNMENT_MODES = {"topology_role", "random", "fixed"}
ALLOWED_TM_MODES = {"uniform", "exponential", "gravity", "spike"}
ALLOWED_FLOW_MODELS = {"poisson", "on_off", "cbr"}
ALLOWED_FLOW_SELECTION_MODES = {"mixed", "single"}
ALLOWED_NODE_STATES = {"normal", "disabled"}
ALLOWED_NIC_STATES = {"normal", "disabled"}
ALLOWED_LINK_STATES = {"normal", "degraded", "disabled"}
ALLOWED_EVENT_ENTITY_TYPES = {"node", "channel", "nic", "data_flow"}
ALLOWED_EVENT_TYPES = {
    "node": {"fault", "recovery"},
    "channel": {"fault", "recovery"},
    "nic": {"fault", "recovery"},
    "data_flow": {"increase", "decrease"},
}


def _ensure_probability(value: float, name: str) -> None:
    if not (0.0 <= float(value) <= 1.0):
        raise ValueError(f"{name} must be in [0, 1], got {value}")


def _ensure_range(value: list[float] | tuple[float, float], name: str) -> None:
    if len(value) != 2:
        raise ValueError(f"{name} must contain exactly two values")
    if float(value[0]) > float(value[1]):
        raise ValueError(f"{name} min cannot be greater than max")


def _ensure_state_probabilities(value: object, allowed_states: set[str], name: str) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{name} must be a non-empty mapping")

    unknown_states = set(str(state) for state in value.keys()) - allowed_states
    if unknown_states:
        raise ValueError(f"Unsupported {name} states: {sorted(unknown_states)}")

    weights = [float(weight) for weight in value.values()]
    if any(weight < 0 for weight in weights):
        raise ValueError(f"{name} weights must be non-negative")
    if sum(weights) <= 0:
        raise ValueError(f"{name} must include positive weights")


def _ensure_positive_range(value: object, name: str) -> None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must be [min, max]")
    low = float(value[0])
    high = float(value[1])
    if low <= 0 or high <= 0:
        raise ValueError(f"{name} values must be > 0")
    if low > high:
        raise ValueError(f"{name} min cannot be greater than max")


def validate_scene_config(config: "SceneConfig") -> None:
    if int(config.num_scenes) <= 0:
        raise ValueError("num_scenes must be a positive integer")
    if float(config.scene_duration) <= 0:
        raise ValueError("scene_duration must be > 0")

    if not config.topology_sources:
        raise ValueError("At least one topology source is required")

    if sum(float(source.weight) for source in config.topology_sources) <= 0:
        raise ValueError("At least one topology source weight must be positive")

    for source in config.topology_sources:
        if source.type not in ALLOWED_SOURCE_TYPES:
            raise ValueError(f"Unsupported topology source type: {source.type}")
        if source.weight < 0:
            raise ValueError(f"Topology source weight must be >= 0 for {source.name}")
        if not source.root_dirs:
            raise ValueError(f"Topology source root_dirs cannot be empty for {source.name}")
        if source.type == "topologyzoo":
            graphml_patterns = [pattern for pattern in source.glob_patterns if "graphml" in str(pattern).lower()]
            if graphml_patterns:
                raise ValueError(
                    f"TopologyZoo graphml support has been removed; update glob_patterns for {source.name}: {graphml_patterns}"
                )

    link_mode = str(config.link_generation.get("mode", ""))
    if link_mode not in ALLOWED_LINK_MODES:
        raise ValueError(f"Unsupported link_generation.mode: {link_mode}")
    _ensure_state_probabilities(
        config.link_generation.get("state_probabilities", {}),
        ALLOWED_LINK_STATES,
        "link_generation.state_probabilities",
    )
    if link_mode == "role_based_random":
        derived_role_cfg = config.link_generation.get("role_based_random", {}).get("derived_link_role", {})
        _ensure_probability(
            float(derived_role_cfg.get("aggregation_aggregation_lateral_probability", 0.7)),
            "link_generation.role_based_random.derived_link_role.aggregation_aggregation_lateral_probability",
        )
        _ensure_probability(
            float(derived_role_cfg.get("core_edge_uplink_probability", 0.8)),
            "link_generation.role_based_random.derived_link_role.core_edge_uplink_probability",
        )

    queue_policy_mode = str(config.nics.get("queue_policy_mode", "mixed"))
    if queue_policy_mode not in ALLOWED_QUEUE_POLICY_MODES:
        raise ValueError(f"Unsupported nics.queue_policy_mode: {queue_policy_mode}")
    _ensure_state_probabilities(
        config.nics.get("state_probabilities", {}),
        ALLOWED_NIC_STATES,
        "nics.state_probabilities",
    )
    queue_policy_mode_probabilities = config.nics.get("queue_policy_mode_probabilities", {})
    unknown_queue_modes = set(queue_policy_mode_probabilities.keys()) - ALLOWED_QUEUE_POLICY_MODES
    if unknown_queue_modes:
        raise ValueError(f"Unsupported nics.queue_policy_mode_probabilities keys: {sorted(unknown_queue_modes)}")
    if queue_policy_mode_probabilities and sum(float(v) for v in queue_policy_mode_probabilities.values()) <= 0:
        raise ValueError("nics.queue_policy_mode_probabilities must include positive weights")
    queue_candidates = list(config.nics.get("queue_policy_candidates", []))
    unknown_queues = set(queue_candidates) - ALLOWED_QUEUE_POLICIES
    if unknown_queues:
        raise ValueError(f"Unsupported queue policy candidates: {sorted(unknown_queues)}")
    single_queue_policy = str(config.nics.get("single_queue_policy", "FIFO"))
    if single_queue_policy not in ALLOWED_QUEUE_POLICIES:
        raise ValueError(f"Unsupported nics.single_queue_policy: {single_queue_policy}")
    queue_probabilities = config.nics.get("queue_policy_probabilities", {})
    unknown_prob_queues = set(queue_probabilities.keys()) - ALLOWED_QUEUE_POLICIES
    if unknown_prob_queues:
        raise ValueError(f"Unsupported nics.queue_policy_probabilities keys: {sorted(unknown_prob_queues)}")
    single_queue_probabilities = config.nics.get("single_queue_policy_probabilities", {})
    unknown_single_queues = set(single_queue_probabilities.keys()) - ALLOWED_QUEUE_POLICIES
    if unknown_single_queues:
        raise ValueError(f"Unsupported nics.single_queue_policy_probabilities keys: {sorted(unknown_single_queues)}")
    if single_queue_probabilities and sum(float(v) for v in single_queue_probabilities.values()) <= 0:
        raise ValueError("nics.single_queue_policy_probabilities must include positive weights")
    if queue_policy_mode == "mixed":
        if not queue_candidates and not queue_probabilities:
            raise ValueError("nics mixed mode requires queue_policy_candidates or queue_policy_probabilities")
        if queue_probabilities and sum(float(v) for v in queue_probabilities.values()) <= 0:
            raise ValueError("nics.queue_policy_probabilities must include positive weights")
    _ensure_range(config.nics.get("queue_size_range_packets", [0, 0]), "nics.queue_size_range_packets")
    try:
        nic_network = ipaddress.ip_network(str(config.nics.get("ip_cidr", "10.0.0.0/8")), strict=False)
    except ValueError as exc:
        raise ValueError(f"Unsupported nics.ip_cidr: {config.nics.get('ip_cidr')}") from exc
    cidr_candidates_raw = [str(item) for item in config.nics.get("ip_cidr_candidates", [])]
    nic_networks = [nic_network]
    if cidr_candidates_raw:
        nic_networks = []
        for raw_cidr in cidr_candidates_raw:
            try:
                nic_networks.append(ipaddress.ip_network(raw_cidr, strict=False))
            except ValueError as exc:
                raise ValueError(f"Unsupported nics.ip_cidr_candidates value: {raw_cidr}") from exc
    if len({network.version for network in nic_networks}) > 1:
        raise ValueError("nics.ip_cidr and nics.ip_cidr_candidates must use the same IP version")
    for index, left in enumerate(nic_networks):
        for right in nic_networks[index + 1 :]:
            if left.overlaps(right):
                raise ValueError(f"nics.ip_cidr_candidates cannot overlap: {left} vs {right}")
    ip_cidr_probabilities = config.nics.get("ip_cidr_probabilities", {})
    unknown_ip_cidrs = set(ip_cidr_probabilities.keys()) - {str(network) for network in nic_networks}
    if unknown_ip_cidrs:
        raise ValueError(f"Unsupported nics.ip_cidr_probabilities keys: {sorted(unknown_ip_cidrs)}")
    if ip_cidr_probabilities and sum(float(v) for v in ip_cidr_probabilities.values()) <= 0:
        raise ValueError("nics.ip_cidr_probabilities must include positive weights")

    link_subnet_prefix_probabilities = config.nics.get("link_subnet_prefix_probabilities", {})
    prefix_candidates = [int(config.nics.get("link_subnet_prefix", 30))]
    if link_subnet_prefix_probabilities:
        prefix_candidates = [int(prefix) for prefix in link_subnet_prefix_probabilities.keys()]
        if sum(float(v) for v in link_subnet_prefix_probabilities.values()) <= 0:
            raise ValueError("nics.link_subnet_prefix_probabilities must include positive weights")

    for prefix in prefix_candidates:
        if prefix > int(nic_network.max_prefixlen):
            raise ValueError("nics link subnet prefix exceeds address width")
        if not any(int(prefix) > int(network.prefixlen) for network in nic_networks):
            raise ValueError("Each nics link subnet prefix must be larger than at least one base IP CIDR prefix")

        sample_network = next(
            (network for network in nic_networks if int(prefix) > int(network.prefixlen)),
            None,
        )
        if sample_network is None:
            raise ValueError("Unable to validate nics link subnet prefix against base IP CIDRs")
        sample_subnet = next(sample_network.subnets(new_prefix=int(prefix)))
        if len(list(sample_subnet.hosts())) < 2:
            raise ValueError("nics link subnet prefix must leave at least two usable host addresses per link")

    node_types = list(config.nodes.get("type_candidates", []))
    if not node_types:
        raise ValueError("nodes.type_candidates cannot be empty")
    assignment_mode = str(config.nodes.get("assignment_mode", "topology_role"))
    if assignment_mode not in ALLOWED_NODE_ASSIGNMENT_MODES:
        raise ValueError(f"Unsupported nodes.assignment_mode: {assignment_mode}")
    _ensure_state_probabilities(
        config.nodes.get("state_probabilities", {}),
        ALLOWED_NODE_STATES,
        "nodes.state_probabilities",
    )

    _ensure_range(config.routing.get("weight_range", [0.0, 0.0]), "routing.weight_range")

    tm_mode_probabilities = config.traffic_matrix.get("mode_probabilities", {})
    unknown_tm_modes = set(tm_mode_probabilities.keys()) - ALLOWED_TM_MODES
    if unknown_tm_modes:
        raise ValueError(f"Unsupported traffic_matrix.mode_probabilities keys: {sorted(unknown_tm_modes)}")
    if tm_mode_probabilities:
        if sum(float(v) for v in tm_mode_probabilities.values()) <= 0:
            raise ValueError("traffic_matrix.mode_probabilities must include positive weights")
    else:
        tm_mode = str(config.traffic_matrix.get("mode", ""))
        if tm_mode not in ALLOWED_TM_MODES:
            raise ValueError(f"Unsupported traffic_matrix.mode: {tm_mode}")
    flow_count_range = config.traffic_matrix.get("flow_count_range")
    if flow_count_range is not None:
        if not isinstance(flow_count_range, (list, tuple)) or len(flow_count_range) != 2:
            raise ValueError("traffic_matrix.flow_count_range must be [min, max]")
        try:
            min_ratio = float(flow_count_range[0])
            max_ratio = float(flow_count_range[1])
        except (TypeError, ValueError):
            raise ValueError("traffic_matrix.flow_count_range values must be ratios between 0 and 1") from None
        if min_ratio < 0.0 or max_ratio < 0.0 or min_ratio > 1.0 or max_ratio > 1.0:
            raise ValueError("traffic_matrix.flow_count_range values must be ratios between 0 and 1")
        if min_ratio > max_ratio:
            raise ValueError("traffic_matrix.flow_count_range min cannot be greater than max")

    flow_selection_mode = str(config.flow_feature.get("selection_mode", "mixed"))
    if flow_selection_mode not in ALLOWED_FLOW_SELECTION_MODES:
        raise ValueError(f"Unsupported flow_feature.selection_mode: {flow_selection_mode}")
    selection_mode_probabilities = config.flow_feature.get("selection_mode_probabilities", {})
    unknown_selection_modes = set(selection_mode_probabilities.keys()) - ALLOWED_FLOW_SELECTION_MODES
    if unknown_selection_modes:
        raise ValueError(f"Unsupported flow_feature.selection_mode_probabilities keys: {sorted(unknown_selection_modes)}")
    if selection_mode_probabilities and sum(float(v) for v in selection_mode_probabilities.values()) <= 0:
        raise ValueError("flow_feature.selection_mode_probabilities must include positive weights")
    single_model = str(config.flow_feature.get("single_model", "poisson"))
    if single_model not in ALLOWED_FLOW_MODELS:
        raise ValueError(f"Unsupported flow_feature.single_model: {single_model}")
    single_model_probabilities = config.flow_feature.get("single_model_probabilities", {})
    unknown_single_models = set(single_model_probabilities.keys()) - ALLOWED_FLOW_MODELS
    if unknown_single_models:
        raise ValueError(f"Unsupported flow_feature.single_model_probabilities keys: {sorted(unknown_single_models)}")
    if single_model_probabilities and sum(float(v) for v in single_model_probabilities.values()) <= 0:
        raise ValueError("flow_feature.single_model_probabilities must include positive weights")
    mode_probs = config.flow_feature.get("mode_probabilities", {})
    unknown_models = set(mode_probs.keys()) - ALLOWED_FLOW_MODELS
    if unknown_models:
        raise ValueError(f"Unsupported flow_feature model: {sorted(unknown_models)}")
    if flow_selection_mode == "mixed":
        if not mode_probs:
            raise ValueError("flow_feature.mode_probabilities cannot be empty in mixed mode")
        if sum(float(v) for v in mode_probs.values()) <= 0:
            raise ValueError("flow_feature.mode_probabilities must include positive weights")

    events_cfg = getattr(config, "events", {})
    if not isinstance(events_cfg, dict):
        raise ValueError("events must be a mapping")
    event_count = int(events_cfg.get("count", 0))
    if event_count < 0:
        raise ValueError("events.count must be >= 0")
    event_type_probabilities = events_cfg.get("event_type_probabilities", {})
    if not isinstance(event_type_probabilities, dict):
        raise ValueError("events.event_type_probabilities must be a mapping")
    unknown_event_entities = set(str(key) for key in event_type_probabilities.keys()) - ALLOWED_EVENT_ENTITY_TYPES
    if unknown_event_entities:
        raise ValueError(f"Unsupported events.event_type_probabilities keys: {sorted(unknown_event_entities)}")
    for entity_type, probabilities in event_type_probabilities.items():
        entity_type = str(entity_type)
        if not isinstance(probabilities, dict) or not probabilities:
            raise ValueError(f"events.event_type_probabilities.{entity_type} must be a non-empty mapping")
        unknown_event_types = set(str(key) for key in probabilities.keys()) - ALLOWED_EVENT_TYPES[entity_type]
        if unknown_event_types:
            raise ValueError(
                f"Unsupported events.event_type_probabilities.{entity_type} keys: {sorted(unknown_event_types)}"
            )
        if any(float(weight) < 0 for weight in probabilities.values()):
            raise ValueError(f"events.event_type_probabilities.{entity_type} weights must be non-negative")
        if sum(float(weight) for weight in probabilities.values()) <= 0:
            raise ValueError(f"events.event_type_probabilities.{entity_type} must include positive weights")

    data_flow_events = events_cfg.get("data_flow", {})
    if not isinstance(data_flow_events, dict):
        raise ValueError("events.data_flow must be a mapping")
    _ensure_positive_range(
        data_flow_events.get("increase_multiplier_range", [1.2, 2.0]),
        "events.data_flow.increase_multiplier_range",
    )
    _ensure_positive_range(
        data_flow_events.get("decrease_multiplier_range", [0.2, 0.8]),
        "events.data_flow.decrease_multiplier_range",
    )
