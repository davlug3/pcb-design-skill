import unittest

from placement import optimize_placement


class PadAwarePlacementTests(unittest.TestCase):
    def test_net_affinity_uses_pad_offsets_when_available(self):
        entries = [
            ({
                "ref": "U1",
                "at": {"x_mm": 20, "y_mm": 20},
                "pads": {"1": "VCC"},
                "_placement_pads": {"VCC": [(4.0, 0.0)]},
            }, 2, 2),
            ({
                "ref": "C1",
                "pads": {"1": "VCC"},
                "_placement_pads": {"VCC": [(-1.0, 0.0)]},
            }, 2, 2),
        ]
        spec = {
            "board": {"width_mm": 60, "height_mm": 40},
            "placement": {
                "mode": "auto",
                "margin_mm": 2,
                "grid_mm": 1,
                "clearance_mm": 0,
                "fixed_refs": ["U1"],
            },
        }
        result = optimize_placement(entries, spec)
        # U1's VCC pad is at x=24; C1's pad is 1 mm left of its origin.
        # The best grid-aligned capacitor origin is therefore near x=25,
        # not directly beside the IC body origin at x=20.
        self.assertGreaterEqual(result["C1"][0], 24.0)
        self.assertLessEqual(result["C1"][0], 27.0)

    def test_legacy_entries_without_pad_geometry_still_work(self):
        entries = [
            ({"ref": "U1", "at": {"x_mm": 20, "y_mm": 20}, "pads": {"1": "VCC"}}, 2, 2),
            ({"ref": "C1", "pads": {"1": "VCC"}}, 2, 2),
        ]
        spec = {
            "board": {"width_mm": 40, "height_mm": 30},
            "placement": {"mode": "auto", "fixed_refs": ["U1"], "grid_mm": 1},
        }
        result = optimize_placement(entries, spec)
        self.assertIn("C1", result)


if __name__ == "__main__":
    unittest.main()
