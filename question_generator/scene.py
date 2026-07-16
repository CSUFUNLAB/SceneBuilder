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
    def __init__(self, scene_name: str, source_file: Path, entities: list[EntityRecord]) -> None:
        self.scene_name = scene_name
        self.source_file = source_file
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

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "SceneData":
        source_file = Path(path)
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
                        label=str(raw.get("label", "")),
                        properties=dict(properties),
                        relations=dict(relations),
                    )
                )
        scene_name = source_file.parent.parent.name
        return cls(scene_name, source_file, entities)

    def entities(self, entity_type: str) -> list[EntityRecord]:
        return list(self._entities_by_type.get(entity_type, []))

    def entity(self, entity_type: str, entity_id: str) -> EntityRecord | None:
        return self._entities_by_key.get((entity_type, entity_id))

    def channel_endpoint_nodes(self, channel: EntityRecord) -> tuple[str, ...]:
        return self._channel_nodes.get(channel.entity_id, ())

    def channels_on_flow_path(self, flow: EntityRecord) -> list[EntityRecord]:
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

    files = list(scene_root.glob("*/twin/0.jsonl"))
    if (scene_root / "twin" / "0.jsonl").is_file():
        files.append(scene_root / "twin" / "0.jsonl")
    files.sort(key=lambda path: (path.parent.parent.name, str(path)))
    if not files:
        raise ValueError(f"No scene twin/0.jsonl files found under {scene_root}")
    return files
