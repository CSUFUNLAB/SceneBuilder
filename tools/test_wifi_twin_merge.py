"""Fast schema/evidence regression tests; no ns-3 simulation required."""
from pathlib import Path
import sys
import json
import random
import tempfile
import unittest
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from question_generator.scene import EntityRecord, SceneData
from question_generator.evidence import (
    _complete_flow_path, channel_directional_throughputs, infer_bandwidth_constraint,
    infer_bottleneck, infer_channel_state, infer_congestion_pattern,
    infer_nic_state, infer_wifi_association_state,
)
from question_generator.generators.analysis import AnalysisQuestionGenerator
from question_generator.templates import load_templates


def fixture(observed=True, include_observation=True, ap_operational=True):
    nodes = [EntityRecord('node', n, 'normal', {}, {}) for n in ('ap', 'sta1', 'sta2', 'idle')]
    nics = []
    for node in ('ap', 'sta1', 'sta2', 'idle'):
        props = {'interface_type': 'wifi', 'wifi_role': 'ap' if node == 'ap' else 'sta',
                 'operational': ap_operational if node == 'ap' else True}
        rels = {'node': node, 'channel': 'BSS1'}
        if node != 'ap':
            props['association_configured_state'] = 'enabled'
            if include_observation:
                props['association_observed'] = observed
            rels['ap_nic'] = 'ap:IF1'
        nics.append(EntityRecord('nic', node + ':IF1', 'normal', props, rels))
    channel = EntityRecord('channel', 'BSS1', 'normal',
                           {'medium_type': 'wifi', 'configured_state': 'enabled'},
                           {'ap_node': 'ap', 'ap_nic': 'ap:IF1', 'connects': [n.entity_id for n in nics]})
    flow = EntityRecord('data_flow', 'F1', 'normal', {'demand_mbps': 1},
                        {'source_node': 'sta1', 'destination_node': 'sta2',
                         'path_nodes': ['sta1', 'ap', 'sta2'], 'path_channels': ['BSS1', 'BSS1']})
    return nodes + nics + [channel, flow]


class WifiTwinMergeTests(unittest.TestCase):
    def scene(self, **kwargs):
        return SceneData('test', Path('twin.jsonl'), fixture(**kwargs),
                         nic_association_states={'sta1:IF1': 'associated', 'sta2:IF1': 'associated'})

    def test_shared_channel_repeated_for_two_hops(self):
        scene = self.scene()
        path = _complete_flow_path(scene, scene.entity('data_flow', 'F1'))
        self.assertIsNotNone(path)
        self.assertEqual([c.entity_id for c in path[1]], ['BSS1', 'BSS1'])
        self.assertFalse(scene.channel_supports_hop(scene.entity('channel', 'BSS1'), 'sta1', 'sta2'))

    def test_fallback_path_retains_repeated_channel(self):
        entities = fixture()
        flow = entities[-1]
        entities[-1] = replace(flow, relations={k: v for k, v in flow.relations.items() if k != 'path_channels'})
        scene = SceneData('test', Path('twin.jsonl'), entities)
        self.assertIsNotNone(_complete_flow_path(scene, entities[-1]))

    def test_shared_channel_does_not_pull_idle_sta_into_flow_scope(self):
        scene = self.scene()
        self.assertTrue(scene.entity_is_in_flow_scope('nic', 'sta1:IF1'))
        self.assertFalse(scene.entity_is_in_flow_scope('nic', 'idle:IF1'))

    def test_wireless_not_fixed_capacity_even_with_two_members(self):
        entities = fixture()
        c = entities[-2]
        entities[-2] = replace(c, properties={**c.properties, 'original_capacity_mbps': 54},
                               relations={**c.relations, 'connects': ['ap:IF1', 'sta1:IF1']})
        scene = SceneData('test', Path('twin.jsonl'), entities)
        self.assertIsNone(channel_directional_throughputs(scene, entities[-2]))
        for fn in (infer_bandwidth_constraint, infer_bottleneck, infer_congestion_pattern):
            self.assertIsNone(fn(scene, entities[-1]))

    def test_observation_required_and_distinct_from_interface_state(self):
        for observed, expected in [(True, 'associated'), (False, 'not_associated')]:
            scene = self.scene(observed=observed)
            nic = scene.entity('nic', 'sta1:IF1')
            self.assertEqual(infer_wifi_association_state(scene, nic), expected)
            self.assertEqual(infer_nic_state(scene, nic), 'normal')
        scene = self.scene(include_observation=False)
        self.assertIsNone(infer_wifi_association_state(scene, scene.entity('nic', 'sta1:IF1')))
        scene = self.scene(ap_operational=False)
        self.assertEqual(infer_wifi_association_state(scene, scene.entity('nic', 'sta1:IF1')), 'disabled')

    def test_label_namespaces_do_not_overwrite_nic_state(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            rows = [dict(entity_type=e.entity_type, entity_id=e.entity_id,
                         properties=e.properties, relations=e.relations) for e in fixture()]
            (p / 'twin.jsonl').write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
            labels = [{'label_type': 'nic_state', 'label': [{'entity_id': 'sta1:IF1', 'label': 'normal'}]},
                      {'label_type': 'nic_association_state', 'label': [{'entity_id': 'sta1:IF1', 'label': 'associated'}]}]
            for order in (labels, list(reversed(labels))):
                (p / 'labels.jsonl').write_text('\n'.join(json.dumps(r) for r in order), encoding='utf-8')
                scene = SceneData.from_jsonl(p / 'twin.jsonl')
                self.assertEqual(scene.entity('nic', 'sta1:IF1').label, 'normal')
                self.assertEqual(scene.nic_association_states['sta1:IF1'], 'associated')

    def test_questions_target_existing_channel_and_nic(self):
        scene = self.scene()
        templates = {t.template_id: t for t in load_templates(ROOT / 'question_generator/templates/analysis.yaml', 'analysis')}
        generator = AnalysisQuestionGenerator()
        for tid, label, entity_type, placeholder in [('TA0012', 'normal', 'channel', 'channel_id'),
                                                    ('TA0013', 'associated', 'nic', 'nic_id')]:
            q = generator.generate_candidate(scene, templates[tid], label, random.Random(1))
            self.assertIsNotNone(q)
            self.assertIsNotNone(scene.entity(entity_type, q.replacements[placeholder]))
        self.assertIsNone(generator.generate_candidate(scene, templates['TA0002'], 'normal', random.Random(1)))

    def test_bad_reference_not_assumed_associated(self):
        entities = fixture()
        sta = entities[5]
        entities[5] = replace(sta, relations={**sta.relations, 'ap_nic': 'missing'})
        scene = SceneData('test', Path('twin.jsonl'), entities)
        self.assertIsNone(infer_wifi_association_state(scene, scene.entity('nic', 'sta1:IF1')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
