import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from routing_feedback import build_feedback_spec, quality_key


class RoutingFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            "components": [
                {"ref": "U1", "pads": {"1": "VCC", "2": "GND"}},
                {"ref": "C1", "pads": {"1": "VCC", "2": "GND"}},
                {"ref": "C2", "pads": {"1": "VCC"}},
                {"ref": "J1", "pads": {"1": "SIG"}},
            ],
            "placement": {"proximity": []},
        }
        self.metrics = {"track_length_by_net_mm": {"VCC": 40.0, "GND": 20.0, "SIG": 5.0}}

    def test_longest_nets_generate_deterministic_star_hints(self):
        updated, rules = build_feedback_spec(self.spec, self.metrics, top_nets=2, weight=4)
        self.assertEqual([r["refs"] for r in rules], [["C1", "C2"], ["C1", "U1"], ["C1", "U1"]])
        self.assertEqual(len(updated["placement"]["proximity"]), 2)
        self.assertEqual(self.spec["placement"]["proximity"], [])
        self.assertEqual(rules[0]["weight"], 4.0)
        self.assertEqual(rules[2]["weight"], 2.0)

    def test_no_metrics_means_no_feedback(self):
        updated, rules = build_feedback_spec(self.spec, {}, top_nets=2)
        self.assertEqual(rules, [])
        self.assertEqual(updated, self.spec)

    def test_invalid_controls_rejected(self):
        with self.assertRaises(ValueError):
            build_feedback_spec(self.spec, self.metrics, top_nets=0)
        with self.assertRaises(ValueError):
            build_feedback_spec(self.spec, self.metrics, weight=0)

    def test_quality_prefers_connectivity_then_critical_length_then_vias(self):
        baseline = {"unconnected_items": 1, "track_length_by_net_mm": {"VCC": 40}, "via_count": 2}
        better_connectivity = {"unconnected_items": 0, "track_length_by_net_mm": {"VCC": 50}, "via_count": 3}
        shorter = {"unconnected_items": 1, "track_length_by_net_mm": {"VCC": 35}, "via_count": 4}
        fewer_vias = {"unconnected_items": 1, "track_length_by_net_mm": {"VCC": 40}, "via_count": 1}
        self.assertLess(quality_key(better_connectivity, ["VCC"]), quality_key(baseline, ["VCC"]))
        self.assertLess(quality_key(shorter, ["VCC"]), quality_key(baseline, ["VCC"]))
        self.assertLess(quality_key(fewer_vias, ["VCC"]), quality_key(baseline, ["VCC"]))


if __name__ == "__main__":
    unittest.main()
