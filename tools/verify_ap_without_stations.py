"""Before/after ns-3 regression for APs with zero assigned stations.

Use --phase check on a fresh checkout. For before/after comparisons, run
--phase before, apply/build the fix, then --phase after in the same NEW output
directory. Only test configurations and outputs are written there.
"""
from pathlib import Path
import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import traceback

import networkx as nx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scene_generator.runner import run as generate
from question_generator.runner import run as generate_questions
from question_generator.scene import SceneData
from check_scene_consistency import check


def read(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def hierarchy(count, aps, idle=0):
    graph = nx.Graph()
    for node in range(count):
        graph.add_node(str(node), role='core' if node < 2 else 'aggregation' if node < aps + 2 else 'edge')
    graph.add_edge('0', '1', role='backbone', bandwidth=40000)
    for ap in range(2, aps + 2):
        for core in ('0', '1'):
            graph.add_edge(core, str(ap), role='uplink', bandwidth=10000)
    for i, sta in enumerate(range(aps + 2, count)):
        graph.add_edge(str(2 + i % (aps - idle)), str(sta), role='access', bandwidth=1000)
    return graph


def cases():
    stable = [dict(name='example20', example=True),
              dict(name='example20_g', example=True, standard='802.11g'),
              dict(name='example20_n', example=True, standard='802.11n'),
              dict(name='example20_csv', example=True, format='legacy_csv'),
              dict(name='wired_csv', example=True, mode='wired', format='legacy_csv'),
              dict(name='wired_jsonl', example=True, mode='wired')]
    stable += [dict(name=f'nodes_{n}', nodes=n, aps=a) for n, a in ((10, 2), (30, 5), (50, 8))]
    boundaries = [dict(name=f'idle_{s}_{p}', nodes=12, aps=4, idle=1, standard=s, policy=p)
                  for s in ('802.11g', '802.11n') for p in ('FIFO', 'RED', 'CoDel', 'FqCoDel')]
    boundaries += [dict(name=f'multiple_idle_{seed}', nodes=12, aps=4, idle=3, seed=seed)
                   for seed in (2026092901, 2026092902, 2026092903)]
    boundaries += [dict(name=f'all_idle_{s}', nodes=6, aps=4, idle=4, standard=s)
                   for s in ('802.11g', '802.11n')]
    boundaries += [dict(name=f'idle_csv_{s}', nodes=12, aps=4, idle=1, standard=s, format='legacy_csv')
                   for s in ('802.11g', '802.11n')]
    boundaries += [dict(name='idle_stationary', nodes=12, aps=4, idle=1, moving=0.0),
                   dict(name='idle_all_stations_moving', nodes=12, aps=4, idle=1, moving=1.0)]
    return stable, boundaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--phase', required=True, choices=('before', 'after', 'check'))
    parser.add_argument('--ns3-root', type=Path, default=ROOT / 'ns-3')
    args = parser.parse_args()
    if os.name == 'nt':
        parser.error('Run using the Linux/WSL Python environment.')
    out = args.output.resolve()
    ns3_root = args.ns3_root.resolve()
    report = out / (args.phase + '_summary.json')
    if report.exists():
        raise FileExistsError(report)
    out.mkdir(parents=True, exist_ok=True)
    base = yaml.safe_load((ROOT / 'configs/wifi_v2_normal_example.yaml').read_text(encoding='utf-8'))
    stable, boundaries = cases()
    specs = stable if args.phase == 'before' else stable + boundaries
    results = []
    for spec in specs:
        print('RUN ' + args.phase + ' ' + spec['name'], flush=True)
        case = out / 'cases' / spec['name']
        phase = case / args.phase
        phase.mkdir(parents=True, exist_ok=False)
        result = dict(case=spec['name'], passed=False)
        try:
            cfg = copy.deepcopy(base)
            cfg.update(seed=spec.get('seed', 2026092901), output_root=str(phase / 'generated'),
                       network_mode=spec.get('mode', 'hybrid'), scene_format=spec.get('format', 'unified_jsonl'))
            if spec.get('example'):
                source = ROOT / 'examples/topologies'
                filename = 'custom_20node.gml'
                node_count, ap_count = 20, 4
            else:
                source = case / 'source'
                source.mkdir(exist_ok=True)
                filename = 'topology.gml'
                node_count, ap_count = spec['nodes'], spec['aps']
                if not (source / filename).exists():
                    nx.write_gml(hierarchy(node_count, ap_count, spec.get('idle', 0)), source / filename)
            cfg['max_topology_nodes'] = node_count
            cfg['topology_sources'] = [dict(name=spec['name'], type='topologyzoo', enabled=True,
                                           root_dir=str(source), glob_patterns=[filename])]
            if 'standard' in spec:
                cfg['wifi']['standard_probabilities'] = {spec['standard']: 1.0}
            if 'policy' in spec:
                cfg['nics'].update(queue_policy_mode='single', single_queue_policy=spec['policy'])
            if 'moving' in spec:
                cfg['wifi']['moving_sta_ratio'] = spec['moving']
            config_path = case / 'config.yaml'
            config_path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
            scene = generate(config_path)[0]
            twin = phase / 'twin/twin.jsonl'
            proc = subprocess.run([str(ns3_root / 'build/scratch/ns3.48-TwinGenerate-debug'),
                                   '--scene=' + str(scene), '--result=' + str(twin),
                                   '--progressInterval=0', '--wallProgressInterval=0'],
                                  capture_output=True, timeout=180,
                                  env={**os.environ, 'NS_LOG': 'NetworkSceneHelper=level_info'})
            (phase / 'ns3.log').write_bytes(proc.stdout + proc.stderr)
            if proc.returncode:
                raise RuntimeError((proc.stdout + proc.stderr).decode(errors='replace')[-2500:])
            shutil.copyfile(twin.with_name('labels.jsonl'), scene / 'labels.jsonl')
            question_path = phase / 'analysis_questions.jsonl'
            question_cfg = phase / 'questions.yaml'
            question_cfg.write_text(yaml.safe_dump(dict(seed=cfg['seed'], scenes_root=str(twin.parent),
                categories={'analysis': dict(enabled=True, questions_per_question=60,
                    template_file=str(ROOT / 'question_generator/templates/analysis.yaml'),
                    output_file=str(question_path))})), encoding='utf-8')
            generate_questions(question_cfg, scene_files=[twin], question_type='analysis')
            if cfg['scene_format'] == 'unified_jsonl':
                result.update(check(scene, twin, question_path))
            rows = read(twin)
            by_kind = {k: {r['entity_id']: r for r in rows if r['entity_type'] == k}
                       for k in ('node', 'nic', 'channel', 'data_flow')}
            assert len(rows) == len({r['entity_id'] for r in rows})
            assert len(by_kind['node']) == node_count
            labels = {r['label_type']: {v['entity_id']: v['label'] for v in r['label']} for r in read(twin.with_name('labels.jsonl'))}
            for kind in by_kind:
                assert set(labels[kind + '_state']) == set(by_kind[kind])
            nics, channels = by_kind['nic'], by_kind['channel']
            wifi = [r for r in channels.values() if r['properties']['medium_type'] == 'wifi']
            stas = [r for r in nics.values() if r['properties'].get('wifi_role') == 'sta']
            expected_sta = node_count - 2 - ap_count if cfg['network_mode'] == 'hybrid' else 0
            assert len(stas) == expected_sta
            assert len(wifi) == (ap_count if cfg['network_mode'] == 'hybrid' else 0)
            states = SceneData.from_jsonl(twin).nic_association_states
            assert set(states) == {r['entity_id'] for r in stas}
            assert all(s == 'associated' for s in states.values())
            empty = []
            for channel in wifi:
                rel = channel['relations']
                ap = nics[rel['ap_nic']]
                assert ap['properties']['wifi_role'] == 'ap' and ap['properties']['operational']
                assert ap['relations']['node'] == rel['ap_node']
                members = {r['entity_id'] for r in nics.values() if r['relations']['channel'] == channel['entity_id']}
                assert set(rel['connects']) == members and rel['ap_nic'] in members
                if len(members) == 1:
                    empty.append(channel['entity_id'])
                    assert labels['channel_state'][channel['entity_id']] == 'normal'
                    assert labels['nic_state'][rel['ap_nic']] == 'normal'
            assert len(empty) == spec.get('idle', 0)
            policies = {'FIFO': 'ns3::PfifoFastQueueDisc', 'RED': 'ns3::RedQueueDisc',
                        'CoDel': 'ns3::CoDelQueueDisc', 'FqCoDel': 'ns3::FqCoDelQueueDisc'}
            for nic in nics.values():
                assert nic['properties']['queue_disc_type'] == policies[nic['properties']['queue_policy']]
            observations = re.findall(r'Wi-Fi interface (\S+) channel (\S+) configured SSID=\[([^\]]*)\]',
                                      (proc.stdout + proc.stderr).decode(errors='replace'))
            assert len(observations) == len(stas) + len(wifi)
            assert all(channel == ssid for _, channel, ssid in observations)
            qrows = read(question_path)
            kinds = {'TA0001': 'node_state', 'TA0002': 'channel_state', 'TA0003': 'nic_state',
                     'TA0004': 'data_flow_state', 'TA0012': 'channel_state', 'TA0013': 'nic_association_state'}
            for q in qrows:
                if q['template_id'] in kinds:
                    eid = re.findall(r'(?<!\w)(?:N\d+:IF\d+|N\d+|C\d+|F\d+)(?!\w)', q['question'])
                    assert len(eid) == 1 and labels[kinds[q['template_id']]][eid[0]] == q['label'], q
            if not stas:
                assert not any(q['template_id'] == 'TA0013' for q in qrows)
            flows = list(by_kind['data_flow'].values())
            assert flows and all(f['properties']['rx_packets'] > 0 for f in flows)
            lost = sum(f['properties']['lost_packets'] for f in flows)
            assert lost == 0, lost
            result.update(nodes=node_count, flows=len(flows), questions=len(qrows), idle_aps=len(empty),
                          associated_stations=len(stas), lost_packets=lost,
                          scene=str(scene), runtime_ssid_checks=len(observations))
            if args.phase == 'after' and spec in stable:
                before = case / 'before'
                previous_scene = next((before / 'generated/origin/input').iterdir())
                for file in scene.iterdir():
                    if file.suffix in ('.jsonl', '.csv'):
                        assert file.read_bytes() == (previous_scene / file.name).read_bytes(), file.name
                for relative in ('twin/twin.jsonl', 'twin/labels.jsonl', 'analysis_questions.jsonl'):
                    assert (phase / relative).read_bytes() == (before / relative).read_bytes(), relative
                result['byte_identical_to_before'] = True
            result['passed'] = True
        except Exception as exc:
            result.update(error=str(exc), traceback=traceback.format_exc())
        results.append(result)
        report.write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)
    print('REPORT ' + str(report), flush=True)
    if not all(r['passed'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
