#!/usr/bin/env python3
"""Tests for builder helpers that do not require pcbnew."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import design_spec as ds  # noqa: E402
import pcb_build  # noqa: E402


def spec() -> dict:
    return ds.apply_defaults(ds.derive_nets(ds.load_json(ROOT / "specs" / "led-blinky.json")))


class StackupTests(unittest.TestCase):
    def test_two_layer_stackup_has_one_dielectric(self) -> None:
        block = pcb_build.stackup_block(spec())
        self.assertIn("(stackup", block)
        self.assertIn('(layer "F.Cu"', block)
        self.assertIn('(layer "B.Cu"', block)
        self.assertEqual(block.count('(layer "dielectric'), 1)

    def test_four_layer_stackup_has_three_dielectrics(self) -> None:
        s = spec()
        s["board"]["layers"] = 4
        s["board"]["stackup"] = pcb_build.default_stackup(s)
        block = pcb_build.stackup_block(s)
        self.assertEqual(block.count('(layer "dielectric'), 3)
        self.assertIn('"In1.Cu"', block)
        self.assertIn('"In2.Cu"', block)

    def test_replace_existing_stackup(self) -> None:
        block = pcb_build.stackup_block(spec())
        text = "(kicad_pcb (setup (stackup (layer \"F.Cu\" (type \"copper\"))) (pad_to_mask_clearance 0)))"
        out = pcb_build.replace_stackup(text, block)
        self.assertIn("(stackup", out)
        self.assertIn("(pad_to_mask_clearance 0)", out)
        # Only one stackup remains.
        self.assertEqual(out.count("(stackup"), 1)

    def test_insert_stackup_when_missing(self) -> None:
        block = pcb_build.stackup_block(spec())
        text = "(kicad_pcb (setup (pad_to_mask_clearance 0)))"
        out = pcb_build.replace_stackup(text, block)
        self.assertEqual(out.count("(stackup"), 1)
        self.assertTrue(out.endswith("))"))

    def test_patch_board_file_identifies_pipeline(self) -> None:
        text = (
            "(kicad_pcb (version 20241229) (generator \"pcbnew\") "
            "(generator_version \"9.0\") (general (thickness 1.6)) "
            "(setup (pad_to_mask_clearance 0)))"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "board.kicad_pcb"
            path.write_text(text)
            pcb_build.patch_board_file(path, spec())
            patched = path.read_text()
        self.assertIn('(generator "pcb-design-skill")', patched)
        self.assertIn('(generator_version "2.0")', patched)
        self.assertIn("(stackup", patched)


class ProjectTests(unittest.TestCase):
    def test_project_contains_netclasses_and_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pcb_build.write_project(spec(), Path(tmp))
            data = json.loads(path.read_text())
        classes = {item["name"] for item in data["net_settings"]["classes"]}
        self.assertEqual(classes, {"Default", "Power"})
        self.assertEqual(data["board"]["design_settings"]["rules"]["min_track_width"], 0.2)
        patterns = {(item["netclass"], item["pattern"]) for item in data["net_settings"]["netclass_patterns"]}
        self.assertIn(("Power", "+5V"), patterns)
        self.assertIn(("Power", "GND"), patterns)


class FootprintPathTests(unittest.TestCase):
    def test_library_id_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "Resistor_SMD.pretty"
            lib.mkdir()
            path, name = pcb_build.footprint_paths("Resistor_SMD:R_0805_2012Metric", Path(tmp))
            self.assertEqual(path, lib)
            self.assertEqual(name, "R_0805_2012Metric")

    def test_missing_library_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                pcb_build.footprint_paths("NoSuchLib:NoSuchPart", Path(tmp))


class DrcParserTests(unittest.TestCase):
    def test_unconnected_pads_are_parsed(self) -> None:
        import pcb_check
        parsed = pcb_check._parse_drc_report(
            "** Found 0 DRC violations **\n\n** Found 3 unconnected pads **\n"
        )
        self.assertEqual(parsed["violations"], 0)
        self.assertEqual(parsed["unconnected"], 3)


if __name__ == "__main__":
    unittest.main()
