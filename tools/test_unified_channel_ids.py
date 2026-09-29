"""Channel-ID and common-field regression tests; no ns-3 required."""
from pathlib import Path
import copy
import json
import sys
import tempfile
import unittest
import networkx as nx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scene_generator.runner import run as generate
from scene_generator.generators.wifi import generate_wifi_scene
from scene_generator.rng import RandomManager
from scene_generator.config import load_config
from scene_generator.scene_schema import NIC_COMMON_FIELDS, CHANNEL_COMMON_FIELDS, read_jsonl, validate_scene_records


class UnifiedChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = yaml.safe_load((ROOT / 'configs/wifi_v2_normal_example.yaml').read_text(encoding='utf-8'))
        for source in base['topology_sources']:
            topo_dir = (ROOT / 'configs' / source['root_dir']).resolve()
            if not (topo_dir / 'custom_20node.gml').is_file():
                topo_dir = ROOT.parent / 'source'
            source['root_dir'] = str(topo_dir)
        base['output_root'] = str(Path(cls.temp.name) / 'generated')
        config = Path(cls.temp.name) / 'config.yaml'
        config.write_text(yaml.safe_dump(base), encoding='utf-8')
        cls.config = load_config(config)
        cls.scene = generate(config)[0]
        cls.records = [read_jsonl(cls.scene / name) for name in (
            'nodes.jsonl', 'nics.jsonl', 'channels.jsonl', 'routes.jsonl', 'traffic.jsonl')]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_c_ids_unique_and_continue_after_wired(self):
        channels = self.records[2]
        self.assertEqual([c['channel_id'] for c in channels], [f'C{i:04d}' for i in range(1, 16)])
        self.assertEqual(sum(c['medium_type'] == 'wifi' for c in channels), 4)
        validate_scene_records(*self.records)

    def test_common_field_order(self):
        for nic in self.records[1]:
            self.assertEqual(tuple(nic)[:len(NIC_COMMON_FIELDS)], NIC_COMMON_FIELDS)
            self.assertEqual(list(nic).index('interface_type'), 3)
            if nic['interface_type'] == 'wifi':
                self.assertEqual(list(nic)[-1], 'wifi_role')
        for channel in self.records[2]:
            self.assertEqual(tuple(channel)[:3], CHANNEL_COMMON_FIELDS)

    def test_sparse_wired_ids_use_max_not_count(self):
        graph = nx.Graph([('0', '1')])
        wired = [{'node': '0', 'interface_index': 1, 'nic_id': 'IF0001', 'channel_id': 'C0042'}]
        bss, nics, associations, *_ = generate_wifi_scene(
            graph, self.config, RandomManager(42), {'0': 'aggregation', '1': 'edge'}, wired)
        self.assertEqual(bss[0]['bss_id'], 'C0043')
        self.assertTrue(all(n['bss_id'] == 'C0043' for n in nics))
        self.assertEqual(associations[0]['bss_id'], 'C0043')

    def test_no_wired_channels_and_large_id(self):
        graph = nx.Graph([('0', '1')])
        roles = {'0': 'aggregation', '1': 'edge'}
        bss, *_ = generate_wifi_scene(graph, self.config, RandomManager(42), roles, [])
        self.assertEqual(bss[0]['bss_id'], 'C0001')
        wired = [{'node': '0', 'interface_index': 1, 'nic_id': 'IF0001', 'channel_id': 'C9999'}]
        bss, *_ = generate_wifi_scene(graph, self.config, RandomManager(42), roles, wired)
        self.assertEqual(bss[0]['bss_id'], 'C10000')

    def test_id_rename_does_not_change_power_sampling(self):
        graph = nx.Graph([('0', '1')])
        seed = 42
        bss, *_ = generate_wifi_scene(graph, self.config, RandomManager(seed), {'0': 'aggregation', '1': 'edge'}, [])
        expected = RandomManager(seed).fork('tx_power:BSS0001').choice(self.config.wifi['tx_power_candidates_dbm'])
        self.assertEqual(bss[0]['tx_power_dbm'], expected)

    def test_wrong_references_types_and_order_rejected(self):
        def missing_channel(rows):
            rows[1][0]['channel_id'] = 'C9999'
        def queue_as_text(rows):
            rows[1][0]['queue_size_packets'] = '256'
        def bad_order(rows):
            row = rows[1][0]
            row['interface_type'] = row.pop('interface_type')
        def duplicate_channel(rows):
            rows[2].append(copy.deepcopy(rows[2][0]))
        def bad_route(rows):
            rows[3][0]['egress_interface'] = 999
        def legacy_id(rows):
            rows[2][-1]['channel_id'] = 'BSS0004'
        for mutate in (missing_channel, queue_as_text, bad_order, duplicate_channel, bad_route, legacy_id):
            with self.subTest(mutate=mutate.__name__):
                rows = copy.deepcopy(self.records)
                mutate(rows)
                with self.assertRaises(ValueError):
                    validate_scene_records(*rows)

    def test_duplicate_json_keys_rejected(self):
        path = Path(self.temp.name) / 'bad_record.jsonl'
        path.write_text('{"channel_id":"C0001","channel_id":"C0002"}\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            read_jsonl(path)


if __name__ == '__main__':
    unittest.main()
