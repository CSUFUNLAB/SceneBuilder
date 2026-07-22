from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EntityRecord:
    entity_type: str
    entity_id: str
    label: str
    properties: dict[str, Any]
    relations: dict[str, Any]


class SceneData:
    def __init__(
        self,
        scene_name: str,
        source_file: Path,
        entities: list[EntityRecord],
        network_state: str = "",
        bottlenecks: list[tuple[str, str]] | None = None,
        congestion_patterns: list[tuple[str, str]] | None = None,
        channel_saturation_causes: list[tuple[str, str]] | None = None,
        bandwidth_constraints: list[tuple[str, str]] | None = None,
        flow_failure_causes: list[tuple[str, str]] | None = None,
        flow_failure_types: list[tuple[str, str]] | None = None,
    ) -> None:
        self.scene_name = scene_name
        self.source_file = source_file
        self.network_state = network_state
        self.bottlenecks = tuple(bottlenecks or [])
        self.congestion_patterns = tuple(congestion_patterns or [])
        self.channel_saturation_causes = tuple(channel_saturation_causes or [])
        self.bandwidth_constraints = tuple(bandwidth_constraints or [])
        self.flow_failure_causes = tuple(flow_failure_causes or [])
        self.flow_failure_types = tuple(flow_failure_types or [])
        self._entities_by_type: dict[str, list[EntityRecord]] = {}
        self._entities_by_key: dict[tuple[str, str], EntityRecord] = {}
        for entity in entities:
            key = (entity.entity_type, entity.entity_id)
            if key in self._entities_by_key:
                raise ValueError(f"Duplicate entity {entity.entity_type}:{entity.entity_id} in {source_file}")
            self._entities_by_key[key] = entity
            self._entities_by_type.setdefault(entity.entity_type, []).append(entity)

        for records in self._entities_by_type.values():
            records.sort(key=lambda item: item.entity_id)

        self._channel_nodes: dict[str, tuple[str, ...]] = {}
        self._channels_by_node_pair: dict[tuple[str, str], list[EntityRecord]] = {}
        for channel in self.entities("channel"):
            endpoint_nodes: list[str] = []
            for nic_id in channel.relations.get("connects", []):
                nic = self.entity("nic", str(nic_id))
                if nic is None:
                    continue
                node_id = str(nic.relations.get("node", ""))
                if node_id and node_id not in endpoint_nodes:
                    endpoint_nodes.append(node_id)
            self._channel_nodes[channel.entity_id] = tuple(endpoint_nodes)
            if len(endpoint_nodes) == 2:
                pair = tuple(sorted(endpoint_nodes))
                self._channels_by_node_pair.setdefault(pair, []).append(channel)

        for channels in self._channels_by_node_pair.values():
            channels.sort(key=lambda item: item.entity_id)

        self._flow_scope_ids: dict[str, set[str]] = {
            "node": set(),
            "nic": set(),
            "channel": set(),
            "data_flow": set(),
        }
        for flow in self.entities("data_flow"):
            self._flow_scope_ids["data_flow"].add(flow.entity_id)
            for relation_name in ("source_node", "destination_node"):
                node_id = str(flow.relations.get(relation_name, ""))
                if node_id:
                    self._flow_scope_ids["node"].add(node_id)
            for node_id in flow.relations.get("path_nodes", []):
                self._flow_scope_ids["node"].add(str(node_id))
            for channel in self.channels_on_flow_path(flow):
                self._flow_scope_ids["channel"].add(channel.entity_id)
                self._flow_scope_ids["node"].update(self.channel_endpoint_nodes(channel))
                for nic_id in channel.relations.get("connects", []):
                    self._flow_scope_ids["nic"].add(str(nic_id))

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "SceneData":
        source_file = Path(path)
        label_file = source_file.with_name(
            "labels.jsonl"
            if source_file.name == "twin.jsonl"
            else f"labels_{source_file.stem.removeprefix('twin_')}.jsonl"
        )
        entity_labels: dict[str, str] = {}
        network_state = ""
        bottlenecks: list[tuple[str, str]] = []
        congestion_patterns: list[tuple[str, str]] = []
        channel_saturation_causes: list[tuple[str, str]] = []
        bandwidth_constraints: list[tuple[str, str]] = []
        flow_failure_causes: list[tuple[str, str]] = []
        flow_failure_types: list[tuple[str, str]] = []
        if label_file.is_file():
            with label_file.open("r", encoding="utf-8-sig") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        label_row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Invalid JSON at {label_file}:{line_number}: {exc.msg}") from exc
                    label_type = str(label_row.get("label_type", ""))
                    if label_type == "state" or label_type in {
                        "node_state",
                        "nic_state",
                        "channel_state",
                        "data_flow_state",
                    }:
                        values = label_row.get("label", [])
                        if not isinstance(values, list):
                            raise ValueError(f"{label_file}:{line_number} state label must be a list")
                        for value in values:
                            if not isinstance(value, dict) or "entity_id" not in value or "label" not in value:
                                raise ValueError(f"{label_file}:{line_number} contains an invalid entity label")
                            entity_labels[str(value["entity_id"])] = str(value["label"])
                    elif label_type == "network_state":
                        network_state = str(label_row.get("label", ""))
                    elif label_type == "bottleneck":
                        values = label_row.get("label", [])
                        if not isinstance(values, list):
                            raise ValueError(f"{label_file}:{line_number} bottleneck label must be a list")
                        for value in values:
                            if (
                                not isinstance(value, dict)
                                or "data_flow_id" not in value
                                or "channel_id" not in value
                            ):
                                raise ValueError(f"{label_file}:{line_number} contains an invalid bottleneck label")
                            bottlenecks.append(
                                (str(value["data_flow_id"]), str(value["channel_id"]))
                            )
                    elif label_type == "data_flow_bandwidth_constraint":
                        values = label_row.get("label", [])
                        if not isinstance(values, list):
                            raise ValueError(
                                f"{label_file}:{line_number} bandwidth constraint label must be a list"
                            )
                        for value in values:
                            if (
                                not isinstance(value, dict)
                                or "data_flow_id" not in value
                                or "label" not in value
                            ):
                                raise ValueError(
                                    f"{label_file}:{line_number} contains an invalid bandwidth constraint label"
                                )
                            bandwidth_constraints.append(
                                (str(value["data_flow_id"]), str(value["label"]))
                            )
                    elif label_type == "data_flow_congestion_pattern":
                        values = label_row.get("label", [])
                        if not isinstance(values, list):
                            raise ValueError(
                                f"{label_file}:{line_number} congestion pattern label must be a list"
                            )
                        for value in values:
                            if (
                                not isinstance(value, dict)
                                or "data_flow_id" not in value
                                or "label" not in value
                            ):
                                raise ValueError(
                                    f"{label_file}:{line_number} contains an invalid congestion pattern label"
                                )
                            congestion_patterns.append(
                                (str(value["data_flow_id"]), str(value["label"]))
                            )
                    elif label_type == "data_flow_failure_cause":
                        values = label_row.get("label", [])
                        if not isinstance(values, list):
                            raise ValueError(
                                f"{label_file}:{line_number} flow failure cause label must be a list"
                            )
                        for value in values:
                            if (
                                not isinstance(value, dict)
                                or "data_flow_id" not in value
                                or "entity_id" not in value
                            ):
                                raise ValueError(
                                    f"{label_file}:{line_number} contains an invalid flow failure cause label"
                                )
                            flow_failure_causes.append(
                                (str(value["data_flow_id"]), str(value["entity_id"]))
                            )
                    elif label_type == "data_flow_failure_type":
                        values = label_row.get("label", [])
                        if not isinstance(values, list):
                            raise ValueError(
                                f"{label_file}:{line_number} flow failure type label must be a list"
                            )
                        for value in values:
                            if (
                                not isinstance(value, dict)
                                or "data_flow_id" not in value
                                or "label" not in value
                            ):
                                raise ValueError(
                                    f"{label_file}:{line_number} contains an invalid flow failure type label"
                                )
                            flow_failure_types.append(
                                (str(value["data_flow_id"]), str(value["label"]))
                            )
                    elif label_type == "channel_saturation_cause":
                        values = label_row.get("label", [])
                        if not isinstance(values, list):
                            raise ValueError(
                                f"{label_file}:{line_number} channel saturation cause label must be a list"
                            )
                        for value in values:
                            if (
                                not isinstance(value, dict)
                                or "channel_id" not in value
                                or "label" not in value
                            ):
                                raise ValueError(
                                    f"{label_file}:{line_number} contains an invalid channel saturation cause label"
                                )
                            channel_saturation_causes.append(
                                (str(value["channel_id"]), str(value["label"]))
                            )
        entities: list[EntityRecord] = []
        with source_file.open("r", encoding="utf-8-sig") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON at {source_file}:{line_number}: {exc.msg}") from exc
                if not isinstance(raw, dict):
                    raise ValueError(f"{source_file}:{line_number} must contain a JSON object")
                entity_type = str(raw.get("entity_type", ""))
                entity_id = str(raw.get("entity_id", ""))
                if not entity_type or not entity_id:
                    raise ValueError(f"{source_file}:{line_number} is missing entity_type or entity_id")
                properties = raw.get("properties", {})
                relations = raw.get("relations", {})
                if not isinstance(properties, dict) or not isinstance(relations, dict):
                    raise ValueError(f"{source_file}:{line_number} properties and relations must be mappings")
                entities.append(
                    EntityRecord(
                        entity_type=entity_type,
                        entity_id=entity_id,
                        label=entity_labels.get(entity_id, str(raw.get("label", ""))),
                        properties=dict(properties),
                        relations=dict(relations),
                    )
                )
        scene_name = source_file.parent.name
        return cls(
            scene_name,
            source_file,
            entities,
            network_state=network_state,
            bottlenecks=bottlenecks,
            congestion_patterns=congestion_patterns,
            channel_saturation_causes=channel_saturation_causes,
            bandwidth_constraints=bandwidth_constraints,
            flow_failure_causes=flow_failure_causes,
            flow_failure_types=flow_failure_types,
        )

    def entities(self, entity_type: str) -> list[EntityRecord]:
        return list(self._entities_by_type.get(entity_type, []))

    def entity(self, entity_type: str, entity_id: str) -> EntityRecord | None:
        return self._entities_by_key.get((entity_type, entity_id))

    def entity_is_in_flow_scope(self, entity_type: str, entity_id: str) -> bool:
        return entity_id in self._flow_scope_ids.get(entity_type, set())

    def channel_endpoint_nodes(self, channel: EntityRecord) -> tuple[str, ...]:
        return self._channel_nodes.get(channel.entity_id, ())

    def channels_on_flow_path(self, flow: EntityRecord) -> list[EntityRecord]:
        explicit_channel_ids = flow.relations.get("path_channels")
        if isinstance(explicit_channel_ids, list):
            return [
                channel
                for channel_id in explicit_channel_ids
                if (channel := self.entity("channel", str(channel_id))) is not None
            ]

        path_nodes = [str(node) for node in flow.relations.get("path_nodes", [])]
        channels: list[EntityRecord] = []
        seen: set[str] = set()
        for src, dst in zip(path_nodes, path_nodes[1:]):
            pair = tuple(sorted((src, dst)))
            for channel in self._channels_by_node_pair.get(pair, []):
                if channel.entity_id in seen:
                    continue
                seen.add(channel.entity_id)
                channels.append(channel)
        return channels

    def flow_direction_on_channel(
        self,
        flow: EntityRecord,
        channel: EntityRecord,
    ) -> tuple[str, str] | None:
        endpoints = set(self.channel_endpoint_nodes(channel))
        if len(endpoints) != 2:
            return None
        path_nodes = [str(node) for node in flow.relations.get("path_nodes", [])]
        for src, dst in zip(path_nodes, path_nodes[1:]):
            if {src, dst} == endpoints:
                return src, dst
        return None


def discover_scene_files(root: str | Path) -> list[Path]:
    scene_root = Path(root)
    if not scene_root.is_dir():
        raise ValueError(f"scenes_root is not a directory: {scene_root}")

    files = list(scene_root.glob("*/twin.jsonl"))
    if (scene_root / "twin.jsonl").is_file():
        files.append(scene_root / "twin.jsonl")
    files.sort(key=lambda path: (path.parent.name, str(path)))
    if not files:
        raise ValueError(f"No scene twin.jsonl files found under {scene_root}")
    return files
