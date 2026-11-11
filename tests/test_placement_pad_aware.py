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

    def test_explicit_pad_proximity_targets_named_pins(self):
        entries = [
            ({
                "ref": "U1", "at": {"x_mm": 20, "y_mm": 20},
                "pads": {"1": "VCC"},
                "_placement_pad_numbers": {"1": (4.0, 0.0)},
            }, 2, 2),
            ({
                "ref": "C1", "pads": {"1": "VCC"},
                "_placement_pad_numbers": {"1": (-1.0, 0.0)},
            }, 2, 2),
        ]
        spec = {
            "board": {"width_mm": 60, "height_mm": 40},
            "placement": {
                "mode": "auto", "margin_mm": 2, "grid_mm": 1,
                "clearance_mm": 0, "fixed_refs": ["U1"],
                "pad_proximity": [{
                    "pads": [{"ref": "U1", "pad": "1"}, {"ref": "C1", "pad": "1"}],
                    "weight": 10,
                    "reason": "datasheet-identified supply bypass pair",
                }],
            },
        }
        result = optimize_placement(entries, spec)
        # Align C1 pad 1 (1 mm left of its origin) with U1 pad 1 (x=24).
        self.assertEqual(result["C1"][0], 25.0)

    def test_pad_proximity_rejects_unknown_pad(self):
        entries = [
            ({"ref": "U1", "_placement_pad_numbers": {"1": (0, 0)}}, 2, 2),
            ({"ref": "C1", "_placement_pad_numbers": {"1": (0, 0)}}, 2, 2),
        ]
        spec = {
            "board": {"width_mm": 40, "height_mm": 30},
            "placement": {"mode": "auto", "pad_proximity": [{
                "pads": [{"ref": "U1", "pad": "9"}, {"ref": "C1", "pad": "1"}]
            }]},
        }
        with self.assertRaisesRegex(ValueError, "unknown pad"):
            optimize_placement(entries, spec)


    def test_power_loop_scores_supply_and_return_legs(self):
        entries = [
            ({"ref": "U1", "at": {"x_mm": 20, "y_mm": 20}, "pads": {"1": "VCC", "2": "GND"},
              "_placement_pad_numbers": {"1": (4.0, 0.0), "2": (4.0, 2.0)}}, 2, 2),
            ({"ref": "C1", "pads": {"1": "VCC", "2": "GND"},
              "_placement_pad_numbers": {"1": (-1.0, 0.0), "2": (-1.0, 2.0)}}, 2, 2),
        ]
        spec = {"board": {"width_mm": 60, "height_mm": 40}, "placement": {
            "mode": "auto", "margin_mm": 2, "grid_mm": 1, "clearance_mm": 0,
            "fixed_refs": ["U1"], "power_loops": [{
                "supply": [{"ref": "U1", "pad": "1"}, {"ref": "C1", "pad": "1"}],
                "return": [{"ref": "C1", "pad": "2"}, {"ref": "U1", "pad": "2"}],
                "weight": 10, "reason": "local bypass supply and ground return",
            }],
        }}
        result = optimize_placement(entries, spec)
        self.assertEqual(result["C1"][:2], (25.0, 20.0))

    def test_power_loop_rejects_unknown_return_pad(self):
        entries = [
            ({"ref": "U1", "_placement_pad_numbers": {"1": (0, 0), "2": (0, 1)}}, 2, 2),
            ({"ref": "C1", "_placement_pad_numbers": {"1": (0, 0), "2": (0, 1)}}, 2, 2),
        ]
        spec = {"board": {"width_mm": 40, "height_mm": 30}, "placement": {
            "mode": "auto", "power_loops": [{
                "supply": [{"ref": "U1", "pad": "1"}, {"ref": "C1", "pad": "1"}],
                "return": [{"ref": "C1", "pad": "9"}, {"ref": "U1", "pad": "2"}],
            }],
        }}
        with self.assertRaisesRegex(ValueError, "unknown pad"):
            optimize_placement(entries, spec)


    def test_ordered_pad_path_keeps_adjacent_critical_pads_close(self):
        entries = [
            ({"ref": "U1", "at": {"x_mm": 10, "y_mm": 10},
              "_placement_pad_numbers": {"1": (0, 0)}}, 2, 2),
            ({"ref": "C1", "_placement_pad_numbers": {"1": (0, 0)}}, 2, 2),
            ({"ref": "D1", "_placement_pad_numbers": {"1": (0, 0)}}, 2, 2),
        ]
        spec = {
            "board": {"width_mm": 30, "height_mm": 20},
            "placement": {
                "mode": "auto", "margin_mm": 1, "grid_mm": 1,
                "clearance_mm": 0, "fixed_refs": ["U1"],
                "pad_paths": [{
                    "kind": "switching_hot_loop",
                    "pads": [
                        {"ref": "U1", "pad": "1"},
                        {"ref": "C1", "pad": "1"},
                        {"ref": "D1", "pad": "1"},
                    ],
                    "weight": 10,
                    "reason": "datasheet switching-current path",
                }],
            },
        }
        result = optimize_placement(entries, spec)
        c1_d1_distance = (
            abs(result["C1"][0] - result["D1"][0])
            + abs(result["C1"][1] - result["D1"][1])
        )
        self.assertEqual(c1_d1_distance, 2.0)

    def test_pad_path_rejects_unknown_pad(self):
        entries = [
            ({"ref": "U1", "_placement_pad_numbers": {"1": (0, 0)}}, 2, 2),
            ({"ref": "C1", "_placement_pad_numbers": {"1": (0, 0)}}, 2, 2),
        ]
        spec = {
            "board": {"width_mm": 30, "height_mm": 20},
            "placement": {"mode": "auto", "pad_paths": [{
                "kind": "crystal_network",
                "pads": [{"ref": "U1", "pad": "1"}, {"ref": "C1", "pad": "7"}],
            }]},
        }
        with self.assertRaisesRegex(ValueError, "unknown pad"):
            optimize_placement(entries, spec)

    def test_bbox_offset_is_used_for_fixed_components(self):
        # A 10 mm-wide part whose bbox centre is +8 mm from its origin spans
        # [x+3, x+13]. On a 40 mm board with a 1 mm margin, x=2 is valid
        # (5..15) but x=30 is not (33..43). A centred model would get both
        # cases backwards near the left edge.
        good = [({"ref": "J1", "at": {"x_mm": 2.0, "y_mm": 10.0},
                  "_placement_offset": (8.0, 0.0)}, 10.0, 10.0)]
        spec = {
            "board": {"width_mm": 40, "height_mm": 20},
            "placement": {"mode": "auto", "margin_mm": 1.0, "grid_mm": 1.0,
                          "clearance_mm": 0.5, "fixed_refs": ["J1"]},
        }
        self.assertEqual(optimize_placement(good, spec)["J1"], (2.0, 10.0, 0.0))

        bad = [({"ref": "J1", "at": {"x_mm": 30.0, "y_mm": 10.0},
                 "_placement_offset": (8.0, 0.0)}, 10.0, 10.0)]
        with self.assertRaisesRegex(ValueError, "violates board bounds"):
            optimize_placement(bad, spec)

    def test_bbox_offset_extends_auto_placement_range_to_the_edge(self):
        entries = [({"ref": "J1", "_placement_offset": (8.0, 0.0)}, 10.0, 10.0)]
        spec = {
            "board": {"width_mm": 40, "height_mm": 20},
            "placement": {"mode": "auto", "margin_mm": 1.0, "grid_mm": 1.0,
                          "clearance_mm": 0.5},
        }
        x, y, _ = optimize_placement(entries, spec)["J1"]
        self.assertGreaterEqual(x + 8.0 - 5.0, 1.0 - 1e-9)
        self.assertLessEqual(x + 8.0 + 5.0, 39.0 + 1e-9)
        self.assertGreaterEqual(y - 5.0, 1.0 - 1e-9)
        self.assertLessEqual(y + 5.0, 19.0 + 1e-9)


if __name__ == "__main__":
    unittest.main()
