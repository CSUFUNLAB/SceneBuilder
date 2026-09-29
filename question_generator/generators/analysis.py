from __future__ import annotations

import random
from typing import Callable

from .base import QuestionCategoryGenerator
from ..models import (
    QuestionCandidate,
    QuestionTemplate,
)
from ..scene import SceneData


class AnalysisQuestionGenerator(QuestionCategoryGenerator):
    def __init__(self) -> None:
        self._handlers: dict[
            str,
            Callable[[SceneData, str, random.Random], QuestionCandidate | None],
        ] = {
            "TA0001": self._node_state,
            "TA0002": self._channel_state,
            "TA0003": self._nic_state,
            "TA0004": self._flow_state,
            "TA0005": self._flow_bandwidth_constraint,
            "TA0006": self._flow_congestion_pattern,
            "TA0007": self._channel_saturation_cause,
            "TA0008": self._flow_bottleneck,
            "TA0009": self._flow_failure_cause,
            "TA0010": self._channel_unavailability_cause,
            "TA0011": self._nic_unavailability_cause,
        }

    def generate_candidate(
        self,
        scene: SceneData,
        template: QuestionTemplate,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        handler = self._handlers.get(template.template_id)
        if handler is None:
            raise ValueError(
                f"No analysis generation logic for {template.template_id}"
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
            if scene.entity("data_flow", data_flow_id) is not None
            and scene.entity("channel", channel_id) is not None
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
            if scene.entity("data_flow", data_flow_id) is not None
            and (
                scene.entity("node", entity_id) is not None
                or scene.entity("channel", entity_id) is not None
            )
        ]
        if not candidates:
            return None
        data_flow_id, entity_id = rng.choice(candidates)
        return QuestionCandidate({"data_flow_id": data_flow_id}, entity_id)

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
            ):
                candidates.append(channel_id)
        if not candidates:
            return None
        return QuestionCandidate({"channel_id": rng.choice(candidates)}, target_label)

    @staticmethod
    def _channel_unavailability_cause(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        candidates = [
            channel_id
            for channel_id, label in scene.channel_unavailability_causes
            if label == target_label
            and scene.entity("channel", channel_id) is not None
            and scene.entity_is_in_flow_scope("channel", channel_id)
        ]
        if not candidates:
            return None
        return QuestionCandidate(
            {"channel_id": rng.choice(candidates)},
            target_label,
        )

    @staticmethod
    def _nic_unavailability_cause(
        scene: SceneData,
        target_label: str,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        candidates = [
            nic_id
            for nic_id, label in scene.nic_unavailability_causes
            if label == target_label
            and scene.entity("nic", nic_id) is not None
            and scene.entity_is_in_flow_scope("nic", nic_id)
        ]
        if not candidates:
            return None
        return QuestionCandidate(
            {"nic_id": rng.choice(candidates)},
            target_label,
        )
