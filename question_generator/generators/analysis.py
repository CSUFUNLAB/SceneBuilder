from __future__ import annotations

import math
import random
from typing import Callable

from .base import QuestionCategoryGenerator
from ..models import QuestionCandidate, QuestionTemplate
from ..scene import EntityRecord, SceneData


_SATURATION_THRESHOLD = 0.95


class AnalysisQuestionGenerator(QuestionCategoryGenerator):
    def __init__(self) -> None:
        self._handlers: dict[
            tuple[tuple[str, ...], tuple[str, ...]],
            Callable[[SceneData, str, random.Random], QuestionCandidate | None],
        ] = {
            ((), ("normal", "congested", "faulty")): self._network_state,
            (("node_id",), ("normal", "disabled")): self._node_state,
            (("channel_id",), ("normal", "disabled", "degraded", "saturated")): self._channel_state,
            (("nic_id",), ("normal", "disabled", "tx_failed", "rx_failed", "saturated")): self._nic_state,
            (("data_flow_id",), ("normal", "degraded", "failed")): self._flow_state,
            (("data_flow_id",), ("channel_saturation", "channel_degradation")): self._flow_degradation_cause,
            (("data_flow_id",), ("channel_id",)): self._flow_bottleneck,
            (("channel_id",), ("channel_fault", "endpoint_node_fault")): self._channel_unavailability_cause,
            (("channel_id",), ("single_large_flow", "multiple_flow_aggregation")): self._channel_saturation_cause,
        }

    def generate_candidate(
        self,
        scene: SceneData,
        template: QuestionTemplate,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        handler = self._handlers.get((template.placeholders, template.answer_values))
        if handler is None:
            raise ValueError(
                f"No analysis generation logic for {template.template_id} with "
                f"placeholders={template.placeholders} answers={template.answer_values}"
            )
        return handler(scene, target_label, rng)

    @staticmethod
    def _choose_entity_by_label(
        scene: SceneData,
        entity_type: str,
        placeholder: str,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        candidates = [
            entity
            for entity in scene.entities(entity_type)
            if entity.label == target_label
        ]
        if not candidates:
            return None
        entity = rng.choice(candidates)
        return QuestionCandidate({placeholder: entity.entity_id}, target_label)

    @staticmethod
    def _network_state(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        del rng
        faulty_labels = {
            "node": {"disabled"},
            "channel": {"disabled", "degraded"},
            "nic": {"disabled", "tx_failed", "rx_failed"},
            "data_flow": {"failed"},
        }
        congested_labels = {
            "channel": {"saturated"},
            "nic": {"saturated"},
            "data_flow": {"degraded"},
        }

        is_faulty = any(
            entity.label in labels
            for entity_type, labels in faulty_labels.items()
            for entity in scene.entities(entity_type)
        )
        is_congested = any(
            entity.label in labels
            for entity_type, labels in congested_labels.items()
            for entity in scene.entities(entity_type)
        )
        actual_label = "faulty" if is_faulty else "congested" if is_congested else "normal"
        if actual_label != target_label:
            return None
        return QuestionCandidate({}, actual_label)

    def _node_state(self, scene: SceneData, target_label: str, rng: random.Random) -> QuestionCandidate | None:
        return self._choose_entity_by_label(scene, "node", "node_id", target_label, rng)

    def _channel_state(self, scene: SceneData, target_label: str, rng: random.Random) -> QuestionCandidate | None:
        return self._choose_entity_by_label(scene, "channel", "channel_id", target_label, rng)

    def _nic_state(self, scene: SceneData, target_label: str, rng: random.Random) -> QuestionCandidate | None:
        return self._choose_entity_by_label(scene, "nic", "nic_id", target_label, rng)

    def _flow_state(self, scene: SceneData, target_label: str, rng: random.Random) -> QuestionCandidate | None:
        return self._choose_entity_by_label(scene, "data_flow", "data_flow_id", target_label, rng)

    @staticmethod
    def _flow_degradation_cause(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        candidates: list[EntityRecord] = []
        for flow in scene.entities("data_flow"):
            if flow.label != "degraded":
                continue
            path_labels = {channel.label for channel in scene.channels_on_flow_path(flow)}
            has_saturation = "saturated" in path_labels
            has_degradation = "degraded" in path_labels
            cause = None
            if has_saturation and not has_degradation:
                cause = "channel_saturation"
            elif has_degradation and not has_saturation:
                cause = "channel_degradation"
            if cause == target_label:
                candidates.append(flow)

        if not candidates:
            return None
        flow = rng.choice(candidates)
        return QuestionCandidate({"data_flow_id": flow.entity_id}, target_label)

    @staticmethod
    def _flow_bottleneck(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        if target_label != "channel_id":
            return None

        candidates: list[tuple[EntityRecord, EntityRecord]] = []
        for flow in scene.entities("data_flow"):
            path_channels = scene.channels_on_flow_path(flow)
            available_values: list[tuple[EntityRecord, float]] = []
            for channel in path_channels:
                available = _as_float(channel.properties.get("available_bandwidth_mbps"))
                if available is not None:
                    available_values.append((channel, available))
            if not available_values:
                continue

            minimum = min(value for _, value in available_values)
            bottlenecks = [
                channel
                for channel, value in available_values
                if math.isclose(value, minimum, rel_tol=1e-9, abs_tol=1e-9)
            ]
            if len(bottlenecks) == 1:
                candidates.append((flow, bottlenecks[0]))

        if not candidates:
            return None
        flow, channel = rng.choice(candidates)
        return QuestionCandidate({"data_flow_id": flow.entity_id}, channel.entity_id)

    @staticmethod
    def _channel_unavailability_cause(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        candidates: list[EntityRecord] = []
        for channel in scene.entities("channel"):
            if channel.label != "disabled":
                continue
            endpoint_nodes = scene.channel_endpoint_nodes(channel)
            if len(endpoint_nodes) != 2:
                continue
            endpoint_fault = any(
                (node := scene.entity("node", node_id)) is not None and node.label == "disabled"
                for node_id in endpoint_nodes
            )
            cause = "endpoint_node_fault" if endpoint_fault else "channel_fault"
            if cause == target_label:
                candidates.append(channel)

        if not candidates:
            return None
        channel = rng.choice(candidates)
        return QuestionCandidate({"channel_id": channel.entity_id}, target_label)

    @staticmethod
    def _channel_saturation_cause(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        candidates: list[EntityRecord] = []
        flows = scene.entities("data_flow")
        for channel in scene.entities("channel"):
            if channel.label != "saturated":
                continue
            capacity = _as_float(channel.properties.get("capacity_mbps"))
            if capacity is None or capacity <= 0:
                continue

            demands_by_direction: dict[tuple[str, str], list[float]] = {}
            for flow in flows:
                direction = scene.flow_direction_on_channel(flow, channel)
                demand = _as_float(flow.properties.get("demand_mbps"))
                if direction is None or demand is None or demand <= 0:
                    continue
                demands_by_direction.setdefault(direction, []).append(demand)

            threshold = capacity * _SATURATION_THRESHOLD
            causes: set[str] = set()
            for demands in demands_by_direction.values():
                if any(demand >= threshold for demand in demands):
                    causes.add("single_large_flow")
                elif len(demands) >= 2 and sum(demands) >= threshold:
                    causes.add("multiple_flow_aggregation")

            if causes == {target_label}:
                candidates.append(channel)

        if not candidates:
            return None
        channel = rng.choice(candidates)
        return QuestionCandidate({"channel_id": channel.entity_id}, target_label)


def _as_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
