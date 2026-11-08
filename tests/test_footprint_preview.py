#!/usr/bin/env python3
"""Tests for footprint preview rendering and resolution."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import footprint_preview as fp  # noqa: E402


SMD_FOOTPRINT = """
(footprint "Test_0805"
  (version 20241229)
  (generator "pcbnew")
  (layer "F.Cu")
  (descr "Test 0805 resistor")
  (tags "resistor test")
  (attr smd)
  (fp_line (start -1.7 -1.0) (end 1.7 -1.0) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start 1.7 -1.0) (end 1.7 1.0) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start 1.7 1.0) (end -1.7 1.0) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start -1.7 1.0) (end -1.7 -1.0) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start -0.5 -0.7) (end 0.5 -0.7) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
  (fp_line (start -0.5 0.7) (end 0.5 0.7) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))
  (pad "1" smd roundrect (at -1 0) (size 1.2 1.4) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
  (pad "2" smd roundrect (at 1 0) (size 1.2 1.4) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
)
"""

THT_FOOTPRINT = """
(module Test_THT (layer F.Cu) (tedit 5F68FEEE)
  (descr "Test through-hole part")
  (attr through_hole)
  (fp_circle (center 0 0) (end 1.0 0) (layer F.SilkS) (width 0.12))
  (pad 1 thru_hole circle (at -1 0) (size 2 2) (drill 1) (layers *.Cu *.Mask))
  (pad 2 thru_hole circle (at 1 0) (size 2 2) (drill 1) (layers *.Cu *.Mask))
)
"""


class ParseTests(unittest.TestCase):
    def test_smd_pads_and_graphics(self) -> None:
        parsed = fp.parse_footprint_text(SMD_FOOTPRINT)
        self.assertEqual(parsed.name, "Test_0805")
        self.assertEqual(parsed.pad_count, 2)
        self.assertFalse(parsed.through_hole)
        self.assertTrue(any(g.layer == "F.CrtYd" for g in parsed.graphics))
        self.assertTrue(any(g.layer == "F.SilkS" for g in parsed.graphics))
        self.assertEqual(parsed.pads[0].number, "1")
        self.assertEqual(parsed.pads[0].shape, "roundrect")

    def test_old_format_through_hole(self) -> None:
        parsed = fp.parse_footprint_text(THT_FOOTPRINT)
        self.assertEqual(parsed.pad_count, 2)
        self.assertTrue(parsed.through_hole)
        self.assertEqual(parsed.pads[0].drill, (1.0, 1.0))

    def test_bounds_exclude_preview_margin(self) -> None:
        parsed = fp.parse_footprint_text(SMD_FOOTPRINT)
        min_x, min_y, max_x, max_y = fp._bounds(parsed)
        self.assertAlmostEqual(max_x - min_x, 3.4, places=3)
        self.assertAlmostEqual(max_y - min_y, 2.0, places=3)


class RenderTests(unittest.TestCase):
    def test_svg_contains_pads_and_courtyard(self) -> None:
        parsed = fp.parse_footprint_text(SMD_FOOTPRINT)
        svg = fp.render_svg(parsed, label="Test 0805")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('class="pads"', svg)
        self.assertIn('class="courtyard"', svg)
        self.assertIn('class="pin1"', svg)
        self.assertIn("<title>Test 0805</title>", svg)

    def test_through_hole_drill_is_drawn(self) -> None:
        parsed = fp.parse_footprint_text(THT_FOOTPRINT)
        svg = fp.render_svg(parsed)
        self.assertIn("<circle", svg)
        self.assertIn(fp.HOLE, svg)

    def test_placeholder_is_an_svg(self) -> None:
        svg = fp.placeholder_svg("Library:Missing", "not installed")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("Library:Missing", svg)
        self.assertIn("not installed", svg)

    def test_preview_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Test_0805.kicad_mod"
            path.write_text(SMD_FOOTPRINT, encoding="utf-8")
            result = fp.preview(str(path), fetch=False)
        self.assertTrue(result["available"])
        self.assertEqual(result["pads"], 2)
        self.assertEqual(result["size_mm"], [3.4, 2.0])
        self.assertEqual(result["source"], "local file")
        self.assertTrue(result["svg"].startswith("<svg"))

    def test_preview_missing_file_returns_placeholder(self) -> None:
        result = fp.preview("Library:DoesNotExist", fetch=False)
        self.assertFalse(result["available"])
        self.assertIn("preview unavailable", result["svg"])


class FetchTests(unittest.TestCase):
    def test_fetch_urls_use_github_library(self) -> None:
        urls = fp.fetch_urls("Resistor_SMD", "R_0805_2012Metric")
        self.assertTrue(urls)
        self.assertIn("KiCad/kicad-footprints/master/Resistor_SMD.pretty/R_0805_2012Metric.kicad_mod", urls[0])

    def test_fetch_urls_reject_path_traversal(self) -> None:
        self.assertEqual(fp.fetch_urls("../evil", "x"), [])
        self.assertEqual(fp.fetch_urls("ok", "../../etc/passwd"), [])

    def test_common_catalog_is_available(self) -> None:
        self.assertIn("resistor", fp.COMMON_FOOTPRINTS)
        self.assertTrue(fp.COMMON_FOOTPRINTS["resistor"])
        self.assertTrue(all(":" in item_id for item_id, _ in fp.COMMON_FOOTPRINTS["resistor"]))


if __name__ == "__main__":
    unittest.main()
