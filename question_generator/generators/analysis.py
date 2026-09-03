from __future__ import annotations

import random
from typing import Callable

from ..evidence import (
    infer_bandwidth_constraint,
    infer_bottleneck,
    infer_channel_saturation_cause,
    infer_channel_unavailability_cause,
    infer_congestion_pattern,
    infer_entity_state,
    infer_flow_failure_cause,
    infer_nic_unavailability_cause,
)
from .base import QuestionCategoryGenerator
from ..models import (
    UNKNOWN_ANSWER_LABEL,
    QuestionCandidate,
    QuestionTemplate,
)
from ..scene import EntityRecord, SceneData


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
        if target_label == template.unknown_answer:
            return self._unknown_candidate(scene, template, rng)
        handler = self._handlers.get(template.template_id)
        if handler is None:
            raise ValueError(
                f"No analysis generation logic for {template.template_id}"
            )
        return handler(scene, target_label, rng)

    @classmethod
    def _unknown_candidate(
        cls,
        scene: SceneData,
        template: QuestionTemplate,
        rng: random.Random,
    ) -> QuestionCandidate | None:
        if template.unknown_answer != UNKNOWN_ANSWER_LABEL:
            return None

        state_specs = {
            "TA0001": ("node", "node_id"),
            "TA0002": ("channel", "channel_id"),
            "TA0003": ("nic", "nic_id"),
            "TA0004": ("data_flow", "data_flow_id"),
        }
        if template.template_id in state_specs:
            entity_type, placeholder = state_specs[template.template_id]
            return cls._choose_unknown_entity(
                scene,
                entity_type,
                placeholder,
                lambda entity: infer_entity_state(scene, entity),
                "state_not_derivable_from_observable_properties",
                (
                    "entity properties, relations, and operational evidence "
                    "sufficient to reproduce one supported state"
                ),
                rng,
            )

        specs: dict[
            str,
            tuple[
                str,
                str,
                Callable[[EntityRecord], str | None],
                str,
                str,
                Callable[[EntityRecord], bool] | None,
            ],
        ] = {
            "TA0005": (
                "data_flow",
                "data_flow_id",
                lambda flow: infer_bandwidth_constraint(scene, flow),
                "bandwidth_constraint_not_uniquely_derivable",
                "a complete path, channel capacities, and directional loads",
                lambda flow: cls._has_saturated_path_channel(scene, flow),
            ),
            "TA0006": (
                "data_flow",
                "data_flow_id",
                lambda flow: infer_congestion_pattern(scene, flow),
                "congestion_pattern_not_uniquely_derivable",
                "a complete path and an evidence-backed state for every path channel",
                lambda flow: cls._has_saturated_path_channel(scene, flow),
            ),
            "TA0007": (
                "channel",
                "channel_id",
                lambda channel: infer_channel_saturation_cause(scene, channel),
                "channel_saturation_cause_not_uniquely_derivable",
                "a saturated channel and complete carried-flow demands",
                lambda channel: channel.label == "saturated",
            ),
            "TA0008": (
                "data_flow",
                "data_flow_id",
                lambda flow: infer_bottleneck(scene, flow),
                "bottleneck_channel_not_uniquely_derivable",
                "a complete multi-channel path with exactly one saturated channel",
                lambda flow: (
                    len(scene.channels_on_flow_path(flow)) >= 2
                    and cls._has_saturated_path_channel(scene, flow)
                ),
            ),
            "TA0009": (
                "data_flow",
                "data_flow_id",
                lambda flow: infer_flow_failure_cause(scene, flow),
                "flow_failure_cause_not_uniquely_derivable",
                "a failed flow, complete path, and one uniquely supported physical fault",
                lambda flow: flow.label == "failed",
            ),
            "TA0010": (
                "channel",
                "channel_id",
                lambda channel: infer_channel_unavailability_cause(scene, channel),
                "channel_unavailability_cause_not_uniquely_derivable",
                "a disabled channel and one uniquely supported physical fault domain",
                lambda channel: channel.label == "disabled",
            ),
            "TA0011": (
                "nic",
                "nic_id",
                lambda nic: infer_nic_unavailability_cause(scene, nic),
                "nic_unavailability_cause_not_uniquely_derivable",
                "a disabled interface, its attached channel, and one supported fault domain",
                lambda nic: nic.label == "disabled",
            ),
        }
        spec = specs.get(template.template_id)
        if spec is None:
            return None
        (
            entity_type,
            placeholder,
            evaluator,
            reason,
            required_evidence,
            predicate,
        ) = spec
        return cls._choose_unknown_entity(
            scene,
            entity_type,
            placeholder,
            evaluator,
            reason,
            required_evidence,
            rng,
            predicate=predicate,
        )

    @staticmethod
    def _has_saturated_path_channel(
        scene: SceneData,
        flow: EntityRecord,
    ) -> bool:
        return any(
            channel.label == "saturated"
            for channel in scene.channels_on_flow_path(flow)
        )

    @staticmethod
    def _choose_unknown_entity(
        scene: SceneData,
        entity_type: str,
        placeholder: str,
        evaluator: Callable[[EntityRecord], str | None],
        reason: str,
        required_evidence: str,
        rng: random.Random,
        *,
        predicate: Callable[[EntityRecord], bool] | None = None,
    ) -> QuestionCandidate | None:
        candidates = [
            entity
            for entity in scene.entities(entity_type)
            if scene.entity_is_in_flow_scope(entity_type, entity.entity_id)
            and (predicate is None or predicate(entity))
            and evaluator(entity) is None
        ]
        if not candidates:
            return None
        entity = rng.choice(candidates)
        return QuestionCandidate(
            {placeholder: entity.entity_id},
            UNKNOWN_ANSWER_LABEL,
            evidence={
                "status": "insufficient",
                "reason": reason,
                "required_evidence": required_evidence,
                "target_entity_type": entity_type,
                "target_entity_id": entity.entity_id,
                "scene_name": scene.scene_name,
            },
        )

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
