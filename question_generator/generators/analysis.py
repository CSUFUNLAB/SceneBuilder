from __future__ import annotations

import random
from typing import Callable

from .base import QuestionCategoryGenerator
from ..evidence import (
    infer_bandwidth_constraint,
    infer_bottleneck,
    infer_channel_saturation_cause,
    infer_congestion_pattern,
    infer_entity_state,
    infer_flow_failure_cause,
    infer_flow_failure_type,
)
from ..models import QuestionCandidate, QuestionTemplate
from ..scene import SceneData


class AnalysisQuestionGenerator(QuestionCategoryGenerator):
    def __init__(self) -> None:
        self._handlers: dict[
            tuple[tuple[str, ...], tuple[str, ...]],
            Callable[[SceneData, str, random.Random], QuestionCandidate | None],
        ] = {
            (("node_id",), ("normal", "disabled", "routing_failed")): self._node_state,
            (("channel_id",), ("normal", "disabled", "degraded", "saturated")): self._channel_state,
            (("nic_id",), ("normal", "disabled", "saturated")): self._nic_state,
            (("data_flow_id",), ("normal", "unstable", "degraded", "failed")): self._flow_state,
            (
                ("data_flow_id",),
                ("traffic_congestion", "insufficient_channel_capacity", "both"),
            ): self._flow_bandwidth_constraint,
            (
                ("data_flow_id",),
                ("single_channel_bottleneck", "multi_channel_saturation"),
            ): self._flow_congestion_pattern,
            (
                ("channel_id",),
                ("single_large_flow", "multiple_flow_aggregation"),
            ): self._channel_saturation_cause,
            (("data_flow_id",), ("channel_id",)): self._flow_bottleneck,
            (("data_flow_id",), ("entity_id",)): self._flow_failure_cause,
            (
                ("data_flow_id",),
                ("node_crash", "channel_failure", "routing_failure"),
            ): self._flow_failure_type,
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
            and scene.entity_is_in_flow_scope(entity_type, entity.entity_id)
            and infer_entity_state(scene, entity) == target_label
        ]
        if not candidates:
            return None
        entity = rng.choice(candidates)
        return QuestionCandidate({placeholder: entity.entity_id}, target_label)

    def _node_state(self, scene: SceneData, target_label: str, rng: random.Random) -> QuestionCandidate | None:
        return self._choose_entity_by_label(scene, "node", "node_id", target_label, rng)

    def _channel_state(self, scene: SceneData, target_label: str, rng: random.Random) -> QuestionCandidate | None:
        return self._choose_entity_by_label(scene, "channel", "channel_id", target_label, rng)

    def _nic_state(self, scene: SceneData, target_label: str, rng: random.Random) -> QuestionCandidate | None:
        return self._choose_entity_by_label(scene, "nic", "nic_id", target_label, rng)

    def _flow_state(self, scene: SceneData, target_label: str, rng: random.Random) -> QuestionCandidate | None:
        return self._choose_entity_by_label(scene, "data_flow", "data_flow_id", target_label, rng)

    @staticmethod
    def _flow_bandwidth_constraint(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        candidates: list[str] = []
        for data_flow_id, label in scene.bandwidth_constraints:
            flow = scene.entity("data_flow", data_flow_id)
            if (
                label == target_label
                and flow is not None
                and infer_bandwidth_constraint(scene, flow) == target_label
            ):
                candidates.append(data_flow_id)
        if not candidates:
            return None
        return QuestionCandidate({"data_flow_id": rng.choice(candidates)}, target_label)

    @staticmethod
    def _flow_bottleneck(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        if target_label != "channel_id":
            return None

        candidates = [
            (data_flow_id, channel_id)
            for data_flow_id, channel_id in scene.bottlenecks
            if (flow := scene.entity("data_flow", data_flow_id)) is not None
            and infer_bottleneck(scene, flow) == channel_id
        ]
        if not candidates:
            return None
        data_flow_id, channel_id = rng.choice(candidates)
        return QuestionCandidate({"data_flow_id": data_flow_id}, channel_id)

    @staticmethod
    def _flow_congestion_pattern(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        candidates: list[str] = []
        for data_flow_id, label in scene.congestion_patterns:
            flow = scene.entity("data_flow", data_flow_id)
            if (
                label == target_label
                and flow is not None
                and infer_congestion_pattern(scene, flow) == target_label
            ):
                candidates.append(data_flow_id)
        if not candidates:
            return None
        return QuestionCandidate({"data_flow_id": rng.choice(candidates)}, target_label)

    @staticmethod
    def _flow_failure_cause(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        if target_label != "entity_id":
            return None
        candidates = [
            (data_flow_id, entity_id)
            for data_flow_id, entity_id in scene.flow_failure_causes
            if (flow := scene.entity("data_flow", data_flow_id)) is not None
            and infer_flow_failure_cause(scene, flow) == entity_id
        ]
        if not candidates:
            return None
        data_flow_id, entity_id = rng.choice(candidates)
        return QuestionCandidate({"data_flow_id": data_flow_id}, entity_id)

    @staticmethod
    def _flow_failure_type(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        candidates: list[str] = []
        for data_flow_id, label in scene.flow_failure_types:
            flow = scene.entity("data_flow", data_flow_id)
            if (
                label == target_label
                and flow is not None
                and infer_flow_failure_type(scene, flow) == target_label
            ):
                candidates.append(data_flow_id)
        if not candidates:
            return None
        return QuestionCandidate({"data_flow_id": rng.choice(candidates)}, target_label)

    @staticmethod
    def _channel_saturation_cause(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        candidates: list[str] = []
        for channel_id, label in scene.channel_saturation_causes:
            channel = scene.entity("channel", channel_id)
            if (
                label == target_label
                and channel is not None
                and infer_channel_saturation_cause(scene, channel) == target_label
            ):
                candidates.append(channel_id)
        if not candidates:
            return None
        return QuestionCandidate({"channel_id": rng.choice(candidates)}, target_label)
