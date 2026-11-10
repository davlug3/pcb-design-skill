import unittest

from placement import optimize_placement


class PlacementProximityTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            ({"ref": "U1", "at": {"x_mm": 10, "y_mm": 10}, "pads": {}}, 2, 2),
            ({"ref": "C1", "pads": {}}, 2, 2),
            ({"ref": "R1", "pads": {}}, 2, 2),
        ]
        self.base = {
            "board": {"width_mm": 60, "height_mm": 40},
            "placement": {
                "mode": "auto",
                "margin_mm": 2,
                "grid_mm": 2,
                "clearance_mm": 1,
                "fixed_refs": ["U1"],
            },
        }

    def test_proximity_hint_moves_pair_closer(self):
        baseline = optimize_placement(self.entries, self.base)
        hinted_spec = {
            **self.base,
            "placement": {
                **self.base["placement"],
                "proximity": [{"refs": ["C1", "U1"], "weight": 5.0}],
            },
        }
        hinted = optimize_placement(self.entries, hinted_spec)
        base_distance = abs(baseline["C1"][0] - baseline["U1"][0]) + abs(baseline["C1"][1] - baseline["U1"][1])
        hinted_distance = abs(hinted["C1"][0] - hinted["U1"][0]) + abs(hinted["C1"][1] - hinted["U1"][1])
        self.assertLess(hinted_distance, base_distance)

    def test_unknown_proximity_reference_is_rejected(self):
        spec = {
            **self.base,
            "placement": {
                **self.base["placement"],
                "proximity": [{"refs": ["C1", "U99"]}],
            },
        }
        with self.assertRaisesRegex(ValueError, "unknown component"):
            optimize_placement(self.entries, spec)

    def test_proximity_does_not_move_fixed_anchor(self):
        spec = {
            **self.base,
            "placement": {
                **self.base["placement"],
                "proximity": [{"refs": ["C1", "U1"], "weight": 10.0}],
            },
        }
        result = optimize_placement(self.entries, spec)
        self.assertEqual(result["U1"], (10.0, 10.0, 0.0))


if __name__ == "__main__":
    unittest.main()
