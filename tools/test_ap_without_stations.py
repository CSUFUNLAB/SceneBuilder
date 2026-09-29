"""AP channel references must not depend on the existence of a station."""
from pathlib import Path
import copy
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scene_generator.runner import _build_unified_channel_rows


def bss(cid='C0001', node='N0001'):
    return dict(bss_id=cid, ap_node=node, standard='802.11g', channel_number=1,
                channel_width_mhz=20, tx_power_dbm=18, loss_exponent=2.2,
                rate_manager='IdealWifiManager', state='normal')


def ap(cid='C0001', nid='IF0001', node='N0001'):
    return dict(bss_id=cid, nic_id=nid, node=node, wifi_role='ap')


class ApWithoutStationsTests(unittest.TestCase):
    def test_idle_ap_keeps_real_nic_and_empty_sta_list(self):
        row, = _build_unified_channel_rows([], [bss()], [], [ap()])
        self.assertEqual(row['ap_nic_id'], 'IF0001')
        self.assertEqual(row['sta_nic_ids'], [])
        self.assertEqual(row['state'], 'normal')
        self.assertEqual(tuple(row)[:3], ('channel_id', 'medium_type', 'state'))
        self.assertNotIn('ssid', row)

    def test_active_ap_retains_existing_output(self):
        association = dict(bss_id='C0001', ap_nic_id='IF0001', sta_nic_id='IF0002')
        row, = _build_unified_channel_rows([], [bss()], [association], [ap()])
        self.assertEqual(row, dict(channel_id='C0001', medium_type='wifi', state='normal',
            standard='802.11g', channel_number=1, channel_width_mhz=20, tx_power_dbm=18,
            loss_exponent=2.2, rate_manager='IdealWifiManager', ap_nic_id='IF0001', sta_nic_ids=['IF0002']))

    def test_idle_and_active_aps_are_matched_by_channel_not_order(self):
        stations = [dict(bss_id='C0002', ap_nic_id='IF0002', sta_nic_id='IF0003')]
        interfaces = [dict(bss_id='C0002', nic_id='IF0003', node='N0003', wifi_role='sta'),
                      ap('C0002', 'IF0002', 'N0002'), ap()]
        rows = _build_unified_channel_rows([], [bss(), bss('C0002', 'N0002')], stations, interfaces)
        self.assertEqual([r['ap_nic_id'] for r in rows], ['IF0001', 'IF0002'])
        self.assertEqual([r['sta_nic_ids'] for r in rows], [[], ['IF0003']])

    def test_all_aps_can_be_idle(self):
        rows = _build_unified_channel_rows([], [bss(), bss('C0002', 'N0002')], [],
                                           [ap(), ap('C0002', 'IF0002', 'N0002')])
        self.assertEqual([r['ap_nic_id'] for r in rows], ['IF0001', 'IF0002'])
        self.assertTrue(all(r['sta_nic_ids'] == [] for r in rows))

    def test_wired_conversion_is_unchanged(self):
        wired = dict(channel_id='C0001', state='normal', src='N0001', dst='N0002',
                     channel_type='uplink', bandwidth_mbps=10000, capacity_multiplier=1.0)
        original = copy.deepcopy(wired)
        row, = _build_unified_channel_rows([wired], [], [], [])
        self.assertEqual(row, {**{k: v for k, v in wired.items() if k != 'channel_type'},
                               'channel_role': 'uplink', 'medium_type': 'wired'})
        self.assertEqual(wired, original)

    def test_missing_ap_still_rejected_even_with_association(self):
        assoc = dict(bss_id='C0001', ap_nic_id='IF0001', sta_nic_id='IF0002')
        with self.assertRaisesRegex(ValueError, 'Missing Wi-Fi AP interface'):
            _build_unified_channel_rows([], [bss()], [assoc], [])

    def test_duplicate_ap_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Multiple Wi-Fi AP interfaces'):
            _build_unified_channel_rows([], [bss()], [], [ap(), ap(nid='IF0002')])

    def test_wrong_ap_node_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Wi-Fi AP node mismatch'):
            _build_unified_channel_rows([], [bss()], [], [ap(node='N0002')])

    def test_input_rows_not_mutated(self):
        inputs = ([], [bss()], [], [ap()])
        original = copy.deepcopy(inputs)
        _build_unified_channel_rows(*inputs)
        self.assertEqual(inputs, original)


if __name__ == '__main__':
    unittest.main(verbosity=2)
