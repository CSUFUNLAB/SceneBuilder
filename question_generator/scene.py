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
        bottlenecks: list[tuple[str, str]] | None = None,
        congestion_patterns: list[tuple[str, str]] | None = None,
        channel_saturation_causes: list[tuple[str, str]] | None = None,
        bandwidth_constraints: list[tuple[str, str]] | None = None,
        flow_failure_causes: list[tuple[str, str]] | None = None,
        channel_unavailability_causes: list[tuple[str, str]] | None = None,
        nic_unavailability_causes: list[tuple[str, str]] | None = None,
        nic_association_states: dict[str, str] | None = None,
    ) -> None:
        self.scene_name = scene_name
        self.source_file = source_file
        self.bottlenecks = tuple(bottlenecks or [])
        self.congestion_patterns = tuple(congestion_patterns or [])
        self.channel_saturation_causes = tuple(channel_saturation_causes or [])
        self.bandwidth_constraints = tuple(bandwidth_constraints or [])
        self.flow_failure_causes = tuple(flow_failure_causes or [])
        self.channel_unavailability_causes = tuple(
            channel_unavailability_causes or []
        )
        self.nic_unavailability_causes = tuple(
            nic_unavailability_causes or []
        )
        # Association labels must never overwrite the NIC's operational label.
        self.nic_association_states = dict(nic_association_states or {})
        self._evidence_cache: dict[str, object] = {}
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
            if channel.properties.get("medium_type") == "wifi":
                ap_node = str(channel.relations.get("ap_node", ""))
                for sta_node in endpoint_nodes:
                    if ap_node in endpoint_nodes and sta_node != ap_node:
                        pair = tuple(sorted((ap_node, sta_node)))
                        self._channels_by_node_pair.setdefault(pair, []).append(channel)
            elif len(endpoint_nodes) == 2:
                pair = tuple(sorted(endpoint_nodes))
                self._channels_by_node_pair.setdefault(pair, []).append(channel)

        for channels in self._channels_by_node_pair.values():
            channels.sort(key=lambda item: item.entity_id)

        self._flow_scope_ids: dict[str, set[str]] = {
            "node": set(),
            "nic": set(),
            "channel": set(),
            "data_flow": set(),
            "wifi_bss": set(),
            "wifi_association": set(),
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
                path_nodes = set(flow.relations.get("path_nodes", []))
                wireless = channel.properties.get("medium_type") == "wifi"
                for nic_id in channel.relations.get("connects", []):
                    nic = self.entity("nic", str(nic_id))
                    if nic and (not wireless or nic.relations.get("node") in path_nodes):
                        self._flow_scope_ids["nic"].add(str(nic_id))
                        self._flow_scope_ids["node"].add(str(nic.relations.get("node")))
            for resource_id in flow.relations.get("path_resources", []):
                bss = self.entity("wifi_bss", str(resource_id))
                if bss is None:
                    continue
                self._flow_scope_ids["wifi_bss"].add(bss.entity_id)
                for nic_id in bss.relations.get("interfaces", []):
                    self._flow_scope_ids["nic"].add(str(nic_id))
                for association_id in bss.relations.get("associations", []):
                    self._flow_scope_ids["wifi_association"].add(
                        str(association_id)
                    )

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "SceneData":
        source_file = Path(path)
        if (
            source_file.parent.name == "scenes"
            and source_file.name != "twin.jsonl"
        ):
            scene_name = source_file.stem
            label_file = (
                source_file.parent.parent
                / "input"
                / scene_name
                / "labels.jsonl"
            )
        else:
            scene_name = source_file.parent.name
            label_file = source_file.with_name(
                "labels.jsonl"
                if source_file.name == "twin.jsonl"
                else f"labels_{source_file.stem.removeprefix('twin_')}.jsonl"
            )
        entity_labels: dict[str, str] = {}
        nic_association_states: dict[str, str] = {}
        bottlenecks: list[tuple[str, str]] = []
        congestion_patterns: list[tuple[str, str]] = []
        channel_saturation_causes: list[tuple[str, str]] = []
        bandwidth_constraints: list[tuple[str, str]] = []
        flow_failure_causes: list[tuple[str, str]] = []
        channel_unavailability_causes: list[tuple[str, str]] = []
        nic_unavailability_causes: list[tuple[str, str]] = []
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
                    if label_type == "nic_association_state":
                        values = label_row.get("label", [])
                        if not isinstance(values, list):
                            raise ValueError(f"{label_file}:{line_number} association label must be a list")
                        for value in values:
                            if not isinstance(value, dict) or "entity_id" not in value or "label" not in value:
                                raise ValueError(f"{label_file}:{line_number} invalid association label")
                            nic_association_states[str(value["entity_id"])] = str(value["label"])
                    elif label_type == "state" or label_type in {
                        "node_state",
                        "nic_state",
                        "channel_state",
                        "wifi_bss_state",
                        "wifi_association_state",
                        "data_flow_state",
                    }:
                        values = label_row.get("label", [])
                        if not isinstance(values, list):
                            raise ValueError(f"{label_file}:{line_number} state label must be a list")
                        for value in values:
                            if not isinstance(value, dict) or "entity_id" not in value or "label" not in value:
                                raise ValueError(f"{label_file}:{line_number} contains an invalid entity label")
                            entity_labels[str(value["entity_id"])] = str(value["label"])
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
                    elif label_type in {
                        "channel_unavailability_cause",
                        "nic_unavailability_cause",
                    }:
                        values = label_row.get("label", [])
                        if not isinstance(values, list):
                            raise ValueError(
                                f"{label_file}:{line_number} unavailability "
                                "cause label must be a list"
                            )
                        target = (
                            channel_unavailability_causes
                            if label_type == "channel_unavailability_cause"
                            else nic_unavailability_causes
                        )
                        for value in values:
                            if (
                                not isinstance(value, dict)
                                or "entity_id" not in value
                                or "label" not in value
                            ):
                                raise ValueError(
                                    f"{label_file}:{line_number} contains an "
                                    "invalid unavailability cause label"
                                )
                            target.append(
                                (
                                    str(value["entity_id"]),
                                    str(value["label"]),
                                )
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
        return cls(
            scene_name,
            source_file,
            entities,
            bottlenecks=bottlenecks,
            congestion_patterns=congestion_patterns,
            channel_saturation_causes=channel_saturation_causes,
            bandwidth_constraints=bandwidth_constraints,
            flow_failure_causes=flow_failure_causes,
            channel_unavailability_causes=channel_unavailability_causes,
            nic_unavailability_causes=nic_unavailability_causes,
            nic_association_states=nic_association_states,
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
        for src, dst in zip(path_nodes, path_nodes[1:]):
            pair = tuple(sorted((src, dst)))
            for channel in self._channels_by_node_pair.get(pair, []):
                channels.append(channel)
        return channels

    def channel_supports_hop(self, channel: EntityRecord, src: str, dst: str) -> bool:
        endpoints = set(self.channel_endpoint_nodes(channel))
        if src == dst or not {src, dst}.issubset(endpoints):
            return False
        if channel.properties.get("medium_type") == "wifi":
            return channel.relations.get("ap_node") in {src, dst}
        return endpoints == {src, dst}

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

    files = list(scene_root.rglob("twin.jsonl"))
    files.extend(
        path
        for path in scene_root.rglob("scenes/*.jsonl")
        if not path.stem.endswith("_labels")
    )
    if scene_root.name == "scenes":
        files.extend(
            path
            for path in scene_root.glob("*.jsonl")
            if not path.stem.endswith("_labels")
        )
    files = list(dict.fromkeys(files))
    files.sort(key=lambda path: str(path.relative_to(scene_root)))
    if not files:
        raise ValueError(f"No scene Twin files found under {scene_root}")
    return files
