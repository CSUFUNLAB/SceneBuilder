from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import random
import tempfile
import unittest

from question_generator.config import CategoryConfig
from question_generator.evidence import (
    _infer_unique_physical_fault,
    infer_channel_state,
    infer_flow_state,
    infer_node_state,
)
from question_generator.generators.analysis import AnalysisQuestionGenerator
from question_generator.runner import _generate_category
from question_generator.scene import EntityRecord, SceneData
from question_generator.templates import load_templates
from test_nic_state_evidence import make_scene


def copy_scene(scene, *, labels=None, extra=(), **causes):
    labels = labels or {}
    entities = [
        replace(entity, label=labels.get(entity.entity_id, entity.label))
        for kind in ("node", "nic", "channel", "data_flow")
        for entity in scene.entities(kind)
    ]
    return SceneData("evidence-test", Path("in-memory"), [*entities, *extra], **causes)


def loaded_scene(*, two_saturated=False, multiple_flows=False):
    scene = make_scene(
        hops=2, label="normal", offered=80, offered_packets=100,
        delivered=80, flow_tx_packets=100,
    )
    for node in scene.entities("node"):
        node.properties.update(tx_packets=100, rx_packets=10)
    scene.entity("data_flow", "F1").properties["demand_mbps"] = 80
    scene.entity("channel", "C0").properties["original_capacity_mbps"] = 80
    if two_saturated:
        scene.entity("channel", "C1").properties["original_capacity_mbps"] = 80
    extra = []
    if multiple_flows:
        flow = scene.entity("data_flow", "F1")
        flow.properties.update(demand_mbps=40, throughput_mbps=40)
        extra.append(replace(flow, entity_id="F2", label="normal", properties=dict(flow.properties)))
        for channel in scene.entities("channel"):
            channel.relations["carries"] = [
                {"data_flow_id": flow_id, "bandwidth_mbps": 40}
                for flow_id in ("F1", "F2")
            ]
    labels = {"N0": "normal", "N1": "normal", "N2": "normal", "F1": "normal",
              "C0": "saturated", "C1": "saturated" if two_saturated else "normal"}
    return copy_scene(
        scene, labels=labels, extra=extra,
        bandwidth_constraints=[("F1", "traffic_congestion")],
        congestion_patterns=[("F1", "multi_channel_saturation" if two_saturated else "single_channel_bottleneck")],
        bottlenecks=[] if two_saturated else [("F1", "C0")],
        channel_saturation_causes=[("C0", "multiple_flow_aggregation" if multiple_flows else "single_large_flow")],
    )


def failed_channel_scene():
    # F1 reaches N1 over C0 but cannot cross C1. F2 attempts the reverse
    # direction on C1. Both endpoint nodes have their own activity evidence.
    scene = make_scene(
        hops=2, label="normal", offered=1, offered_packets=100,
        delivered=1, flow_tx_packets=100,
    )
    for node in scene.entities("node"):
        node.properties.update(tx_packets=100, rx_packets=0)
    scene.entity("node", "N1").properties.update(tx_packets=0, rx_packets=10)
    scene.entity("nic", "N1:IF1").properties.update(tx_rate_mbps=0, tx_packets=0)
    scene.entity("nic", "N2:IF2").properties.update(
        rx_rate_mbps=0, rx_packets=0, tx_rate_mbps=1, tx_packets=100,
    )
    flow = scene.entity("data_flow", "F1")
    flow.properties.update(rx_packets=0, lost_packets=100, throughput_mbps=0)
    reverse_flow = EntityRecord(
        "data_flow", "F2", "failed", dict(flow.properties),
        {"source_node": "N2", "destination_node": "N1",
         "path_nodes": ["N2", "N1"], "path_channels": ["C1"]},
    )
    scene.entity("channel", "C1").relations["carries"] = [
        {"data_flow_id": "F1", "bandwidth_mbps": 0},
        {"data_flow_id": "F2", "bandwidth_mbps": 1},
    ]
    return copy_scene(
        scene, extra=[reverse_flow],
        labels={"N0": "normal", "N1": "normal", "N2": "normal",
                "C1": "disabled", "N1:IF1": "disabled", "N2:IF2": "disabled", "F1": "failed"},
        flow_failure_causes=[("F1", "C1")],
        channel_unavailability_causes=[("C1", "channel_or_interface_fault")],
        nic_unavailability_causes=[("N2:IF2", "channel_or_interface_fault")],
    )


def silent_hub_scene(branches):
    entities = [EntityRecord("node", "hub", "disabled", {"rx_packets": 0, "tx_packets": 0}, {})]
    for index in range(branches):
        node_id, channel_id, flow_id = f"S{index}", f"C{index}", f"F{index}"
        sender_id, receiver_id = f"{node_id}:IF1", f"hub:IF{index}"
        entities.append(EntityRecord("node", node_id, "normal", {"rx_packets": 0, "tx_packets": 100}, {}))
        for nic_id, owner, packets in ((sender_id, node_id, 100), (receiver_id, "hub", 0)):
            entities.append(EntityRecord(
                "nic", nic_id, "disabled",
                {"tx_rate_mbps": 1 if packets else 0, "tx_packets": packets,
                 "rx_rate_mbps": 0, "rx_packets": 0},
                {"node": owner, "channel": channel_id},
            ))
        entities.append(EntityRecord(
            "channel", channel_id, "disabled", {"original_capacity_mbps": 100},
            {"connects": [sender_id, receiver_id],
             "carries": [{"data_flow_id": flow_id, "bandwidth_mbps": 1}]},
        ))
        entities.append(EntityRecord(
            "data_flow", flow_id, "failed",
            {"tx_packets": 100, "rx_packets": 0, "lost_packets": 100,
             "throughput_mbps": 0, "demand_mbps": 1},
            {"source_node": node_id, "destination_node": "hub",
             "path_nodes": [node_id, "hub"], "path_channels": [channel_id]},
        ))
    return SceneData(
        "silent-hub", Path("in-memory"), entities,
        channel_unavailability_causes=[("C0", "connected_node_fault")],
        nic_unavailability_causes=[("S0:IF1", "connected_node_fault")],
    )


class AnalysisEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "question_generator/templates/analysis.yaml"
        cls.templates = {template.template_id: template for template in load_templates(path, "analysis")}

    def candidate(self, scene, template_id, label):
        return AnalysisQuestionGenerator().generate_candidate(
            scene, self.templates[template_id], label, random.Random(0),
        )

    def test_every_analysis_template_accepts_supported_evidence(self):
        loaded = loaded_scene()
        failed = failed_channel_scene()
        cases = [
            ("TA0001", loaded, "normal", "normal"),
            ("TA0002", loaded, "saturated", "saturated"),
            ("TA0003", loaded, "normal", "normal"),
            ("TA0004", loaded, "normal", "normal"),
            ("TA0005", loaded, "traffic_congestion", "traffic_congestion"),
            ("TA0006", loaded, "single_channel_bottleneck", "single_channel_bottleneck"),
            ("TA0007", loaded, "single_large_flow", "single_large_flow"),
            ("TA0008", loaded, "channel_id", "C0"),
            ("TA0009", failed, "entity_id", "C1"),
            ("TA0010", failed, "channel_or_interface_fault", "channel_or_interface_fault"),
            ("TA0011", failed, "channel_or_interface_fault", "channel_or_interface_fault"),
        ]
        for template_id, scene, target, answer in cases:
            with self.subTest(template=template_id):
                candidate = self.candidate(scene, template_id, target)
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate.label, answer)

    def test_every_analysis_template_rejects_labels_without_properties(self):
        scene = copy_scene(
            make_scene(hops=2), labels={"N0": "disabled", "C0": "disabled", "F1": "failed"},
            bandwidth_constraints=[("F1", "traffic_congestion")],
            congestion_patterns=[("F1", "single_channel_bottleneck")],
            channel_saturation_causes=[("C0", "single_large_flow")],
            bottlenecks=[("F1", "C0")], flow_failure_causes=[("F1", "C0")],
            channel_unavailability_causes=[("C0", "channel_or_interface_fault")],
            nic_unavailability_causes=[("N0:IF1", "channel_or_interface_fault")],
        )
        for kind in ("node", "nic", "channel", "data_flow"):
            for entity in scene.entities(kind):
                entity.properties.clear()
        targets = ["disabled", "disabled", "disabled", "failed", "traffic_congestion",
                   "single_channel_bottleneck", "single_large_flow", "channel_id", "entity_id",
                   "channel_or_interface_fault", "channel_or_interface_fault"]
        for number, target in enumerate(targets, 1):
            with self.subTest(template=number):
                self.assertIsNone(self.candidate(scene, f"TA{number:04}", target))

    def test_mismatched_labels_do_not_override_observations(self):
        scene = loaded_scene()
        scene = copy_scene(
            scene, labels={"N0": "disabled", "C0": "degraded", "F1": "failed"},
            bottlenecks=[("F1", "C1")],
            bandwidth_constraints=[("F1", "insufficient_channel_capacity")],
            congestion_patterns=[("F1", "multi_channel_saturation")],
            channel_saturation_causes=[("C0", "multiple_flow_aggregation")],
        )
        for template_id, target in [
            ("TA0001", "disabled"), ("TA0002", "degraded"), ("TA0004", "failed"),
            ("TA0005", "insufficient_channel_capacity"), ("TA0006", "multi_channel_saturation"),
            ("TA0007", "multiple_flow_aggregation"), ("TA0008", "channel_id"),
        ]:
            with self.subTest(template=template_id):
                self.assertIsNone(self.candidate(scene, template_id, target))

    def test_all_zero_physical_entities_do_not_establish_faults(self):
        for hops in (1, 2):
            for flow_tx in (0, 100):
                with self.subTest(hops=hops, flow_tx=flow_tx):
                    scene = copy_scene(
                        make_scene(hops=hops, flow_tx_packets=flow_tx),
                        labels={"N0": "disabled", "C0": "disabled"},
                        flow_failure_causes=[("F1", "C0")],
                        channel_unavailability_causes=[("C0", "channel_or_interface_fault")],
                        nic_unavailability_causes=[("N0:IF1", "channel_or_interface_fault")],
                    )
                    self.assertIsNone(_infer_unique_physical_fault(scene))
                    for tid, label in [("TA0001", "disabled"), ("TA0002", "disabled"),
                                       ("TA0003", "disabled"), ("TA0009", "entity_id"),
                                       ("TA0010", "channel_or_interface_fault"),
                                       ("TA0011", "channel_or_interface_fault")]:
                        self.assertIsNone(self.candidate(scene, tid, label))

    def test_silent_leaf_node_remains_an_alternative_cause(self):
        scene = make_scene(offered=1, offered_packets=100, flow_tx_packets=100)
        scene.entity("node", "N0").properties["tx_packets"] = 100
        self.assertEqual(infer_channel_state(scene, scene.entity("channel", "C0")), "disabled")
        self.assertIsNone(_infer_unique_physical_fault(scene))
        self.assertIsNone(infer_node_state(scene, scene.entity("node", "N1")))

    def test_fault_elsewhere_does_not_prove_an_idle_node_normal(self):
        scene = copy_scene(failed_channel_scene(), extra=[
            EntityRecord("node", "idle", "normal", {"rx_packets": 0, "tx_packets": 0}, {}),
        ])
        self.assertEqual(_infer_unique_physical_fault(scene), "C1")
        self.assertIsNone(infer_node_state(scene, scene.entity("node", "idle")))

    def test_two_channel_faults_prevent_a_unique_node_diagnosis(self):
        scene = silent_hub_scene(2)
        self.assertIsNone(_infer_unique_physical_fault(scene))
        for template, target in [("TA0001", "disabled"), ("TA0010", "connected_node_fault"),
                                 ("TA0011", "connected_node_fault")]:
            self.assertIsNone(self.candidate(scene, template, target))

    def test_three_independent_outages_support_node_fault_with_at_most_two_faults(self):
        scene = silent_hub_scene(3)
        self.assertEqual(_infer_unique_physical_fault(scene), "hub")
        for template, target in [("TA0001", "disabled"), ("TA0010", "connected_node_fault"),
                                 ("TA0011", "connected_node_fault")]:
            self.assertIsNotNone(self.candidate(scene, template, target))

    def test_missing_alternative_cause_observations_prevent_unique_diagnosis(self):
        for kind, entity_id, field in [("channel", "C0", "original_capacity_mbps"),
                                       ("node", "S0", "tx_packets"),
                                       ("nic", "S0:IF1", "rx_rate_mbps")]:
            with self.subTest(kind=kind, field=field):
                scene = silent_hub_scene(3)
                scene.entity(kind, entity_id).properties.pop(field)
                self.assertIsNone(_infer_unique_physical_fault(scene))

    def test_degradation_requires_sampled_local_delivery_deficit(self):
        for delivered, packets, expected in [(59, 100, "degraded"), (60, 100, None),
                                              (20, 9, None), (10, 100, None)]:
            offered = 10 if delivered == 10 else 100
            scene = copy_scene(make_scene(
                offered=offered, offered_packets=packets,
                delivered=delivered, flow_tx_packets=100,
            ), labels={"C0": "degraded"})
            with self.subTest(delivered=delivered, packets=packets):
                candidate = self.candidate(scene, "TA0002", "degraded")
                self.assertEqual(None if candidate is None else candidate.label, expected)

    def test_one_way_zero_does_not_override_reverse_delivery(self):
        scene = make_scene(offered=1, offered_packets=100, flow_tx_packets=100)
        scene.entity("nic", "N0:IF1").properties.update(rx_rate_mbps=1, rx_packets=10)
        self.assertIsNone(infer_channel_state(scene, scene.entity("channel", "C0")))
        self.assertIsNone(self.candidate(scene, "TA0003", "disabled"))

    def test_multiple_saturated_channels_do_not_have_a_unique_bottleneck(self):
        scene = loaded_scene(two_saturated=True)
        self.assertIsNotNone(self.candidate(scene, "TA0006", "multi_channel_saturation"))
        scene.bottlenecks = (("F1", "C0"),)
        self.assertIsNone(self.candidate(scene, "TA0008", "channel_id"))

    def test_multiple_flow_aggregation_remains_supported(self):
        scene = loaded_scene(multiple_flows=True)
        self.assertIsNotNone(self.candidate(scene, "TA0007", "multiple_flow_aggregation"))

    def test_capacity_below_demand_remains_supported(self):
        scene = loaded_scene()
        scene.entity("data_flow", "F1").properties["demand_mbps"] = 100
        scene.bandwidth_constraints = (("F1", "insufficient_channel_capacity"),)
        self.assertIsNotNone(self.candidate(scene, "TA0005", "insufficient_channel_capacity"))

    def test_flow_status_still_distinguishes_outcome_from_physical_cause(self):
        cases = [
            ("failed", {"tx_packets": 0, "rx_packets": 0, "lost_packets": 0, "throughput_mbps": 0}),
            ("unstable", {"lost_packets": 1}),
            ("degraded", {"throughput_mbps": 70}),
            ("normal", {}),
        ]
        for label, updates in cases:
            with self.subTest(label=label):
                scene = loaded_scene()
                scene.entity("data_flow", "F1").properties.update(updates)
                scene = copy_scene(scene, labels={"F1": label})
                self.assertIsNotNone(self.candidate(scene, "TA0004", label))

    def test_invalid_packet_counters_are_not_failure_evidence(self):
        for updates in ({"tx_packets": -1}, {"rx_packets": 101}, {"lost_packets": 100},
                        {"rx_packets": 0, "throughput_mbps": 1}):
            with self.subTest(updates=updates):
                scene = loaded_scene()
                flow = scene.entity("data_flow", "F1")
                flow.properties.update(updates)
                self.assertIsNone(infer_flow_state(flow))

    def test_invalid_queue_occupancy_is_filtered(self):
        for queue in (-1, 101):
            scene = loaded_scene()
            for nic in scene.entities("nic"):
                nic.properties["queue_current_packets"] = queue
            self.assertIsNone(self.candidate(scene, "TA0003", "normal"))

    def test_question_pipeline_keeps_supported_sample_and_skips_all_zero_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for name, scene in [("supported", loaded_scene()), ("all-zero", make_scene(hops=2))]:
                folder = root / name
                folder.mkdir()
                rows, labels = [], []
                for kind in ("node", "nic", "channel", "data_flow"):
                    for entity in scene.entities(kind):
                        rows.append({"entity_type": kind, "entity_id": entity.entity_id,
                                     "properties": entity.properties, "relations": entity.relations})
                        labels.append({"entity_id": entity.entity_id,
                                       "label": "disabled" if name == "all-zero" else entity.label})
                path = folder / "twin.jsonl"
                path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                (folder / "labels.jsonl").write_text(
                    json.dumps({"label_type": "state", "label": labels}) + "\n", encoding="utf-8",
                )
                paths.append(path)
            config = CategoryConfig(
                name="analysis", enabled=True, questions_per_question=12,
                template_file=Path(__file__).resolve().parents[1] / "question_generator/templates/analysis.yaml",
                output_file=root / "questions.jsonl", options={},
            )
            result, _ = _generate_category(config, paths, random.Random(0), 1)
            questions = [json.loads(line) for line in config.output_file.read_text().splitlines()]
            self.assertGreater(result.generated_count, 0)
            self.assertEqual({question["scene_name"] for question in questions}, {"supported"})
            self.assertTrue(all(question["label"] != "unknown" for question in questions))


if __name__ == "__main__":
    unittest.main()
