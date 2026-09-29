"""Fast tests for common NIC types/queues and evidence compatibility."""
from pathlib import Path
from dataclasses import replace
import sys
import copy
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scene_generator.config import load_config
from scene_generator.generators.nics import generate_queue_attributes
from scene_generator.rng import RandomManager
from scene_generator.runner import run as generate
from question_generator.evidence import infer_nic_state, infer_wifi_association_state
from question_generator.scene import SceneData
from test_wifi_twin_merge import fixture


class NicCommonModelTests(unittest.TestCase):
    def test_common_policy_and_role_size(self):
        for policy in ('FIFO', 'RED', 'CoDel', 'FqCoDel'):
            selection = {'selected_mode': 'single', 'active_rule': {
                'queue_policy': policy, 'queue_size_packets_by_role': {'edge': 320}}}
            for _device in ('point_to_point', 'wifi'):
                attrs = generate_queue_attributes({}, RandomManager(42), selection, 'edge')
                self.assertEqual(attrs, {'queue_policy': policy, 'queue_size_packets': 320,
                                         'queue_layer': 'traffic_control'})

    def test_queue_rng_does_not_move_parent_stream(self):
        parent, baseline = RandomManager(42), RandomManager(42)
        parent.fork('queues').random()
        self.assertEqual(parent.random(), baseline.random())

    def test_invalid_config_rejected(self):
        base = yaml.safe_load((ROOT / 'configs/wifi_v2_normal_example.yaml').read_text(encoding='utf-8'))
        for key, value in [('wired_device_type', 'ethernet'), ('queue_size_range_packets', [0, 0]),
                           ('queue_size_range_packets', [1.5, 2.5]), ('queue_size_range_packets', [True, True]),
                           ('queue_size_range_packets', [1, 1000001])]:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as temp:
                config = {**base, 'nics': {**base['nics'], key: value}}
                path = Path(temp) / 'config.yaml'
                path.write_text(yaml.safe_dump(config), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_old_config_defaults_to_point_to_point(self):
        with tempfile.TemporaryDirectory() as temp:
            base = yaml.safe_load((ROOT / 'configs/wifi_v2_normal_example.yaml').read_text(encoding='utf-8'))
            base['nics'].pop('wired_device_type')
            path = Path(temp) / 'config.yaml'
            path.write_text(yaml.safe_dump(base), encoding='utf-8')
            self.assertEqual(load_config(path).nics['wired_device_type'], 'point_to_point')

    def test_wifi_queue_saturation_not_association_failure(self):
        for current, expected in [(0, 'normal'), (320, 'saturated')]:
            rows = fixture()
            index = next(i for i, r in enumerate(rows) if r.entity_id == 'sta1:IF1')
            rows[index] = replace(rows[index], properties={**rows[index].properties,
                'queue_layer': 'traffic_control', 'queue_size_packets': 320, 'queue_current_packets': current})
            scene = SceneData('test', Path('unused.jsonl'), rows)
            self.assertEqual(infer_nic_state(scene, rows[index]), expected)
            self.assertEqual(infer_wifi_association_state(scene, rows[index]), 'associated')

    def test_mixed_rules_produce_per_nic_policies_reproducibly(self):
        import json
        def read(path):
            return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        with tempfile.TemporaryDirectory() as temp:
            base = yaml.safe_load((ROOT / 'configs/wifi_v2_normal_example.yaml').read_text(encoding='utf-8'))
            for source in base['topology_sources']:
                topology_dir = (ROOT / 'configs' / source['root_dir']).resolve()
                if not (topology_dir / 'custom_20node.gml').is_file():
                    topology_dir = ROOT.parent / 'source'
                source['root_dir'] = str(topology_dir)
            scenes = []
            for index in range(2):
                config = copy.deepcopy(base)
                config['output_root'] = str(Path(temp) / f'generated_{index}')
                config['nics'].update(queue_policy_mode='mixed', queue_size_range_packets=[160, 160])
                path = Path(temp) / f'config_{index}.yaml'
                path.write_text(yaml.safe_dump(config), encoding='utf-8')
                scenes.append(generate(path)[0])
            nics = [read(s / 'nics.jsonl') for s in scenes]
            self.assertEqual(nics[0], nics[1])
            for kind in ('wired', 'wifi'):
                policies = {n['queue_policy'] for n in nics[0] if n['interface_type'] == kind}
                self.assertEqual(policies, {'FIFO', 'RED', 'CoDel', 'FqCoDel'})
            for row in nics[1]:
                self.assertEqual(row['queue_size_packets'], 160)
            for name in ('nodes.jsonl', 'channels.jsonl', 'routes.jsonl', 'traffic.jsonl'):
                self.assertEqual(read(scenes[0] / name), read(scenes[1] / name))


if __name__ == '__main__':
    unittest.main()
