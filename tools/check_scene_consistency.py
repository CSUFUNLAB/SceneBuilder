"""Read-only checks for C-prefixed channels and scene/Twin/question references."""
from pathlib import Path
import argparse
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scene_generator.scene_schema import read_jsonl, validate_scene_directory

ID_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:N[0-9]+:IF[0-9]+|IF[0-9]+|C[0-9]+|BSS[0-9]+|N[0-9]+|F[0-9]+)(?![A-Za-z0-9_])")


def check(scene, twin=None, questions=None):
    summary = validate_scene_directory(scene)
    if twin is None:
        return summary
    records = read_jsonl(twin)
    kinds = {kind: {} for kind in ('node', 'nic', 'channel', 'data_flow')}
    for row in records:
        kind, entity_id = row['entity_type'], row['entity_id']
        if kind not in kinds or entity_id in kinds[kind]:
            raise ValueError(f'Unexpected or duplicate Twin entity: {kind}:{entity_id}')
        kinds[kind][entity_id] = row
    all_ids = {eid for entities in kinds.values() for eid in entities}
    if len(all_ids) != len(records):
        raise ValueError('Duplicate ID across Twin entity types')
    for kind, filename, field in [('node', 'nodes.jsonl', 'node_id'),
                                  ('channel', 'channels.jsonl', 'channel_id'),
                                  ('data_flow', 'traffic.jsonl', 'flow_id')]:
        if set(kinds[kind]) != {r[field] for r in read_jsonl(scene / filename)}:
            raise ValueError(f'Scene/Twin {kind} IDs differ')
    source_nics = read_jsonl(scene / 'nics.jsonl')
    if len(source_nics) != len(kinds['nic']):
        raise ValueError('Scene/Twin NIC counts differ')
    wired_fields = None
    for nic in source_nics:
        # Preserve the original scene-ID to node-scoped Twin-ID convention.
        eid = f"{nic['node']}:IF{nic['interface_index']:06d}"
        output = kinds['nic'][eid]
        p, rel = output['properties'], output['relations']
        if rel['node'] != nic['node'] or rel['channel'] != nic['channel_id']:
            raise ValueError(f'Incorrect NIC references: {eid}')
        for key in ('interface_type', 'device_type', 'queue_policy', 'queue_layer', 'queue_size_packets'):
            if p[key] != nic[key]:
                raise ValueError(f'Incorrect NIC property {eid}.{key}')
        if nic['interface_type'] == 'wired':
            wired_fields = wired_fields or tuple(p)
            if tuple(p) != wired_fields:
                raise ValueError('Wired Twin field orders differ')
    for row in kinds['nic'].values():
        props, rel = row['properties'], row['relations']
        if wired_fields and tuple(props)[:len(wired_fields)] != wired_fields:
            raise ValueError('Wired/WiFi common Twin field orders differ')
        channel = kinds['channel'][rel['channel']]
        if channel['properties']['medium_type'] != props['interface_type']:
            raise ValueError('NIC/channel types disagree')
        if row['entity_id'] not in channel['relations']['connects']:
            raise ValueError('Channel/NIC reciprocal reference missing')
        if 'ap_nic' in rel and kinds['nic'][rel['ap_nic']]['relations']['channel'] != rel['channel']:
            raise ValueError('STA/AP channel mismatch')
    for row in kinds['channel'].values():
        if 'ssid' in row['properties']:
            raise ValueError('Twin must not export the derived SSID')
        if not re.fullmatch(r'C[0-9]{4,}', row['entity_id']):
            raise ValueError('Twin contains a non-C channel ID')
        for nid in row['relations']['connects']:
            if kinds['nic'][nid]['relations']['channel'] != row['entity_id']:
                raise ValueError('Unknown or mismatched channel member')
    for row in kinds['data_flow'].values():
        rel = row['relations']
        if any(n not in kinds['node'] for n in rel['path_nodes']):
            raise ValueError('Unknown node in flow path')
        if len(rel['path_channels']) != max(0, len(rel['path_nodes']) - 1):
            raise ValueError('Incorrect number of path channels')
        if any(c not in kinds['channel'] for c in rel['path_channels']):
            raise ValueError('Unknown channel in flow path')
    labels = read_jsonl(twin.with_name('labels.jsonl'))
    by_label = {r['label_type']: r['label'] for r in labels}
    for label, kind in [('node_state', 'node'), ('nic_state', 'nic'), ('channel_state', 'channel'),
                        ('data_flow_state', 'data_flow')]:
        entries = by_label[label]
        if len(entries) != len(kinds[kind]) or {r['entity_id'] for r in entries} != set(kinds[kind]):
            raise ValueError(f'Incomplete or duplicate {label} labels')
    for name, content in [('Twin', records), ('labels', labels)]:
        unknown = set(ID_TOKEN.findall(json.dumps(content))) - all_ids
        if unknown:
            raise ValueError(f'{name} references unknown IDs: {sorted(unknown)}')
    if questions is not None:
        rows = read_jsonl(questions)
        unknown = set(ID_TOKEN.findall(json.dumps(rows))) - all_ids
        if unknown:
            raise ValueError(f'Questions reference unknown IDs: {sorted(unknown)}')
        if len({r['question_id'] for r in rows}) != len(rows):
            raise ValueError('Duplicate question ID')
        summary['questions'] = len(rows)
    scene_labels = scene / 'labels.jsonl'
    if scene_labels.is_file() and read_jsonl(scene_labels) != labels:
        raise ValueError('Scene labels and Twin labels differ')
    summary['channel_ids'] = list(kinds['channel'])
    summary['references_valid'] = True
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, required=True)
    parser.add_argument('--twin', type=Path)
    parser.add_argument('--questions', type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.scene, args.twin, args.questions), indent=2))


if __name__ == '__main__':
    main()
