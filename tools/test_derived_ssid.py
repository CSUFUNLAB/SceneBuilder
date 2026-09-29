"""Tests for channel-derived SSIDs without adding an input/output field."""
from pathlib import Path
import copy
import csv
import json
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scene_generator.config import load_config
from scene_generator.runner import run
from scene_generator.scene_schema import read_jsonl, validate_scene_directory


class DerivedSsidTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.base = yaml.safe_load((ROOT / 'configs/wifi_v2_normal_example.yaml').read_text(encoding='utf-8'))
        for source in self.base['topology_sources']:
            topology = (ROOT / 'configs' / source['root_dir']).resolve()
            if not (topology / 'custom_20node.gml').is_file():
                topology = ROOT.parent / 'source'
            source['root_dir'] = str(topology)

    def tearDown(self):
        self.temp.cleanup()

    def generate(self, name, config=None):
        config = copy.deepcopy(config or self.base)
        config['output_root'] = str(self.folder / name)
        path = self.folder / (name + '.yaml')
        path.write_text(yaml.safe_dump(config), encoding='utf-8')
        return run(path)[0], load_config(path)

    def test_unified_has_no_ssid_or_prefix(self):
        scene, config = self.generate('jsonl')
        validate_scene_directory(scene)
        self.assertNotIn('ssid_prefix', config.wifi)
        for file in scene.iterdir():
            if file.suffix in {'.json', '.jsonl'}:
                content = file.read_text(encoding='utf-8')
                self.assertNotIn('"ssid"', content)
                self.assertNotIn('"ssid_prefix"', content)

    def test_legacy_csv_has_no_ssid_column(self):
        config = copy.deepcopy(self.base)
        config['scene_format'] = 'legacy_csv'
        scene, _ = self.generate('csv', config)
        with (scene / 'wifi_bss.csv').open(encoding='utf-8-sig', newline='') as handle:
            rows = csv.DictReader(handle)
            self.assertNotIn('ssid', rows.fieldnames)
            self.assertEqual(len(list(rows)), 4)

    def test_old_prefix_ignored_without_changing_generation(self):
        first, _ = self.generate('without')
        config = copy.deepcopy(self.base)
        config['wifi']['ssid_prefix'] = 'obsolete-name'
        second, loaded = self.generate('with', config)
        self.assertNotIn('ssid_prefix', loaded.wifi)
        for name in ('nodes.jsonl', 'nics.jsonl', 'channels.jsonl', 'routes.jsonl', 'traffic.jsonl'):
            self.assertEqual(read_jsonl(first / name), read_jsonl(second / name))

    def test_new_scene_contract_rejects_separate_ssid(self):
        scene, _ = self.generate('unexpected')
        rows = read_jsonl(scene / 'channels.jsonl')
        next(r for r in rows if r['medium_type'] == 'wifi')['ssid'] = 'not-needed'
        (scene / 'channels.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'SSID is derived'):
            validate_scene_directory(scene)


if __name__ == '__main__':
    unittest.main()
