#!/usr/bin/env python3
"""Tests for the pipeline's SPICE hand-off behavior."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import design_pipeline  # noqa: E402
import design_spec as ds  # noqa: E402
import spice_flow  # noqa: E402


def minimal_overlay(*, complete: bool = False) -> dict:
    overlay = {
        "version": 1,
        "name": "spice-test",
        "profile": "2layer-low-speed",
        "board": {"width_mm": 40, "height_mm": 30, "layers": 2},
        "rules": {},
        "nets": [{"name": "IN"}, {"name": "GND"}],
        "components": [],
        "placement": {"mode": "fixed"},
    }
    if complete:
        overlay["components"] = [
            {
                "ref": "R1",
                "value": "1k",
                "footprint": "Resistor_SMD:R_0805_2012Metric",
                "at": {"x_mm": 10, "y_mm": 10},
                "pads": {"1": "IN", "2": "GND"},
            }
        ]
    return overlay


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_netlist(tmp: str, text: str = "R1 IN 0 1k\n") -> Path:
    path = Path(tmp) / "design.cir"
    path.write_text(text, encoding="utf-8")
    return path


class SpiceCiBlockerTests(unittest.TestCase):
    def test_reports_missing_footprint_and_placement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "overlay.json"
            write_json(spec_path, minimal_overlay())
            netlist = write_netlist(tmp)
            args = argparse.Namespace(
                spec=str(spec_path),
                ltspice=str(netlist),
                pin_map=None,
                node_aliases=None,
                ask=None,
            )
            message = design_pipeline.spice_ci_blocker(args)
        self.assertIsNotNone(message)
        self.assertIn("footprint for R1", message)
        self.assertIn("placement for R1", message)
        self.assertIn("CI cannot ask", message)

    def test_complete_overlay_is_not_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "overlay.json"
            write_json(spec_path, minimal_overlay(complete=True))
            netlist = write_netlist(tmp)
            args = argparse.Namespace(
                spec=str(spec_path),
                ltspice=str(netlist),
                pin_map=None,
                node_aliases=None,
                ask=None,
            )
            self.assertIsNone(design_pipeline.spice_ci_blocker(args))

    def test_no_ask_disables_the_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "overlay.json"
            write_json(spec_path, minimal_overlay())
            netlist = write_netlist(tmp)
            args = argparse.Namespace(
                spec=str(spec_path),
                ltspice=str(netlist),
                pin_map=None,
                node_aliases=None,
                ask=False,
            )
            self.assertIsNone(design_pipeline.spice_ci_blocker(args))


class ResolveSpecTests(unittest.TestCase):
    def test_visual_answers_are_written_as_reusable_sidecars(self) -> None:
        spec = ds.apply_defaults(minimal_overlay(complete=True))
        fake = spice_flow.SpiceImport(
            spec=spec,
            pin_map={"refs": {"Q1": {"c": "2", "b": "1", "e": "3"}}},
            node_aliases={"N001": "+5V"},
            warnings=[],
            asked=["pin maps", "node aliases"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "overlay.json"
            write_json(spec_path, minimal_overlay(complete=True))
            netlist = write_netlist(tmp)
            args = argparse.Namespace(
                spec=str(spec_path),
                ltspice=str(netlist),
                pin_map=None,
                node_aliases=None,
                ask=None,
                ask_mobile=False,
                ask_host="127.0.0.1",
                ask_port=8765,
                ask_timeout=10,
                ask_open=False,
                ask_no_fetch=True,
                footprints_dir=None,
                netlist=None,
                schematic=None,
            )
            output_dir = Path(tmp) / "build"
            with mock.patch.object(
                design_pipeline.spice_flow, "resolve_ltspice", return_value=fake
            ), contextlib.redirect_stderr(io.StringIO()):
                resolved, profile = design_pipeline.resolve_spec(args, output_dir)
            ask_dir = output_dir / "spice-test" / "ask"
            pin_map = json.loads((ask_dir / "pinmap.resolved.json").read_text())
            aliases = json.loads((ask_dir / "aliases.resolved.json").read_text())
        self.assertEqual(resolved["name"], "spice-test")
        self.assertEqual(profile["name"], "2layer-low-speed")
        self.assertEqual(pin_map["refs"]["Q1"]["c"], "2")
        self.assertEqual(aliases["N001"], "+5V")


if __name__ == "__main__":
    unittest.main()
