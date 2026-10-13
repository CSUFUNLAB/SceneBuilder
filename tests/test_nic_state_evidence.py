from __future__ import annotations

from pathlib import Path
import random
import unittest

from question_generator.evidence import (
    _infer_unique_physical_fault,
    infer_nic_state,
)
from question_generator.generators.analysis import AnalysisQuestionGenerator
from question_generator.models import QuestionTemplate
from question_generator.scene import EntityRecord, SceneData


NIC_TEMPLATE = QuestionTemplate(
    template_id="TA0003",
    category="analysis",
    question="What is the current state of interface $nic_id$?",
    answer_type="enum",
    answer_values=("normal", "disabled", "saturated"),
    placeholders=("nic_id",),
)


def make_scene(
    *,
    hops=1,
    label="disabled",
    offered=0.0,
    offered_packets=0,
    delivered=0.0,
    flow_tx_packets=0,
    queue_current=0,
):
    nodes = [
        EntityRecord("node", f"N{i}", "", {"tx_packets": 0, "rx_packets": 0}, {})
        for i in range(hops + 1)
    ]
    nics = []
    channels = []
    for i in range(hops):
        nic_ids = [f"N{i}:IF1", f"N{i + 1}:IF2"]
        for side, nic_id in enumerate(nic_ids):
            nics.append(EntityRecord(
                "nic", nic_id, label,
                {
                    "tx_rate_mbps": offered if side == 0 else 0.0,
                    "tx_packets": offered_packets if side == 0 else 0,
                    "rx_rate_mbps": delivered if side == 1 else 0.0,
                    "rx_packets": 10 if side == 1 and delivered else 0,
                    "queue_size_packets": 100,
                    "queue_current_packets": queue_current,
                },
                {"node": f"N{i + side}", "channel": f"C{i}"},
            ))
        channels.append(EntityRecord(
            "channel", f"C{i}", "", {"original_capacity_mbps": 100.0},
            {
                "connects": nic_ids,
                "carries": [{"data_flow_id": "F1", "bandwidth_mbps": delivered}],
            },
        ))
    flow = EntityRecord(
        "data_flow", "F1", "",
        {
            "tx_packets": flow_tx_packets,
            "rx_packets": 10 if delivered else 0,
            "lost_packets": 0,
            "throughput_mbps": delivered,
            "demand_mbps": 10.0,
        },
        {
            "source_node": "N0", "destination_node": f"N{hops}",
            "path_nodes": [node.entity_id for node in nodes],
            "path_channels": [channel.entity_id for channel in channels],
        },
    )
    return SceneData("nic-evidence", Path("in-memory"), [*nodes, *nics, *channels, flow])


class NicStateEvidenceTests(unittest.TestCase):
    def candidate(self, scene, label="disabled"):
        return AnalysisQuestionGenerator().generate_candidate(
            scene, NIC_TEMPLATE, label, random.Random(0),
        )

    def test_all_zero_nics_do_not_establish_a_unique_path_fault(self):
        scene = make_scene()
        # This previously bypassed local traffic checks and admitted both NICs.
        self.assertIsNone(_infer_unique_physical_fault(scene))
        for nic in scene.entities("nic"):
            self.assertIsNone(infer_nic_state(scene, nic))
            self.assertEqual(nic.label, "disabled")
        self.assertIsNone(self.candidate(scene))

    def test_labels_alone_do_not_admit_ambiguous_zero_traffic_path(self):
        scene = make_scene(hops=2)
        self.assertIsNone(_infer_unique_physical_fault(scene))
        self.assertIsNone(self.candidate(scene))

    def test_source_flow_packets_do_not_replace_local_nic_send_evidence(self):
        scene = make_scene(flow_tx_packets=100)
        self.assertIsNone(_infer_unique_physical_fault(scene))
        self.assertIsNone(self.candidate(scene))

    def test_zero_rate_or_insufficient_packet_sample_is_rejected(self):
        for offered, packets in ((0, 100), (1, 0), (1, 9)):
            with self.subTest(offered=offered, packets=packets):
                scene = make_scene(offered=offered, offered_packets=packets, flow_tx_packets=100)
                self.assertIsNone(self.candidate(scene))

    def test_sampled_send_with_zero_peer_delivery_remains_eligible(self):
        scene = make_scene(offered=1, offered_packets=10, flow_tx_packets=10)
        candidate = self.candidate(scene)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.label, "disabled")
        self.assertIn(candidate.replacements["nic_id"], {"N0:IF1", "N1:IF2"})

    def test_missing_local_measurements_are_not_treated_as_zero(self):
        for field in ("tx_rate_mbps", "tx_packets", "rx_rate_mbps"):
            with self.subTest(field=field):
                scene = make_scene(offered=1, offered_packets=10, flow_tx_packets=10)
                for nic in scene.entities("nic"):
                    nic.properties.pop(field)
                self.assertIsNone(self.candidate(scene))

    def test_positive_traffic_and_queue_evidence_preserve_other_states(self):
        for label, queue in (("normal", 0), ("normal", 94), ("saturated", 95)):
            with self.subTest(label=label, queue=queue):
                scene = make_scene(
                    label=label, offered=1, offered_packets=10, delivered=1,
                    flow_tx_packets=10, queue_current=queue,
                )
                candidate = self.candidate(scene, label)
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate.label, label)

    def test_label_must_agree_with_observed_state(self):
        for label, queue in (("disabled", 0), ("normal", 95), ("saturated", 0)):
            with self.subTest(label=label, queue=queue):
                scene = make_scene(
                    label=label, offered=1, offered_packets=10, delivered=1,
                    flow_tx_packets=10, queue_current=queue,
                )
                self.assertIsNone(self.candidate(scene, label))

    def test_missing_queue_evidence_filters_normal_and_saturated_labels(self):
        for label in ("normal", "saturated"):
            with self.subTest(label=label):
                scene = make_scene(
                    label=label, offered=1, offered_packets=10, delivered=1,
                    flow_tx_packets=10,
                )
                for nic in scene.entities("nic"):
                    nic.properties.pop("queue_current_packets")
                self.assertIsNone(self.candidate(scene, label))


if __name__ == "__main__":
    unittest.main()
