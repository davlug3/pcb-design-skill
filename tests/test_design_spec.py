#!/usr/bin/env python3
"""Unit tests for the dependency-free design-spec layer."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import design_spec as ds  # noqa: E402


def led_spec() -> dict:
    return ds.apply_defaults(ds.derive_nets(ds.load_json(ROOT / "specs" / "led-blinky.json")))


def led_profile() -> dict:
    return ds.load_profile("2layer-low-speed")


class SpecValidationTests(unittest.TestCase):
    def test_reference_spec_is_clean(self) -> None:
        findings = ds.validate_spec(led_spec(), led_profile())
        self.assertTrue(findings.ok, findings.to_dict())

    def test_via_annular_ring_is_rejected(self) -> None:
        spec = led_spec()
        spec["rules"]["min_via_diameter_mm"] = 0.6
        spec["rules"]["min_via_drill_mm"] = 0.5
        spec["rules"]["min_annular_ring_mm"] = 0.15
        findings = ds.validate_spec(spec, led_profile())
        self.assertFalse(findings.ok)
        self.assertTrue(any(item["code"] == "rules.annular" for item in findings.errors))

    def test_undeclared_pad_net_is_rejected(self) -> None:
        spec = led_spec()
        spec["components"][0]["pads"]["1"] = "TYPO_NET"
        findings = ds.validate_spec(spec, led_profile())
        self.assertFalse(findings.ok)
        self.assertTrue(any(item["code"] == "components.pad_net" for item in findings.errors))

    def test_fixed_placement_requires_coordinates(self) -> None:
        spec = led_spec()
        del spec["components"][0]["at"]
        findings = ds.validate_spec(spec, led_profile())
        self.assertFalse(findings.ok)
        self.assertTrue(any(item["code"] == "components.at" for item in findings.errors))

    def test_component_outside_board_is_rejected(self) -> None:
        spec = led_spec()
        spec["components"][0]["at"]["x_mm"] = 999
        findings = ds.validate_spec(spec, led_profile())
        self.assertFalse(findings.ok)
        self.assertTrue(any(item["code"] == "components.at" for item in findings.errors))

    def test_fab_capability_mismatch_is_rejected(self) -> None:
        spec = led_spec()
        spec["fabrication"]["capabilities"]["min_track_mm"] = 0.5
        findings = ds.validate_spec(spec, led_profile())
        self.assertFalse(findings.ok)
        self.assertTrue(any(item["code"] == "fab.min_track_width_mm" for item in findings.errors))

    def test_spec_keepout_zone_is_rejected_until_geometry_is_supported(self) -> None:
        spec = led_spec()
        spec["zones"][0]["keepout"] = True
        findings = ds.validate_spec(spec, led_profile())
        self.assertFalse(findings.ok)
        self.assertTrue(any(item["code"] == "zones.keepout_unsupported" for item in findings.errors))

    def test_4layer_profile_requires_stackup(self) -> None:
        spec = led_spec()
        spec["profile"] = "4layer-impedance"
        spec["board"]["layers"] = 4
        findings = ds.validate_spec(spec, ds.load_profile("4layer-impedance"))
        self.assertFalse(findings.ok)
        self.assertTrue(any(item["code"] == "stackup.required" for item in findings.errors))


class ImpedanceTests(unittest.TestCase):
    def test_microstrip_impedance_is_physical(self) -> None:
        z0 = ds.microstrip_z0(0.2, 0.2, 0.035, 4.2, {})
        self.assertGreater(z0, 50)
        self.assertLess(z0, 100)

    def test_wider_track_lowers_impedance(self) -> None:
        narrow = ds.microstrip_z0(0.2, 0.2, 0.035, 4.2, {})
        wide = ds.microstrip_z0(0.5, 0.2, 0.035, 4.2, {})
        self.assertLess(wide, narrow)

    def test_impedance_target_gate(self) -> None:
        spec = led_spec()
        spec["profile"] = "4layer-impedance"
        spec["board"]["layers"] = 4
        spec["board"]["stackup"] = {
            "source": "unit test",
            "confirmed": True,
            "layers": [
                {"type": "copper", "name": "F.Cu", "thickness_mm": 0.035},
                {"type": "dielectric", "name": "prepreg 1", "thickness_mm": 0.2, "er": 4.2, "loss_tangent": 0.02},
                {"type": "copper", "name": "In1.Cu", "thickness_mm": 0.035},
                {"type": "dielectric", "name": "core 1", "thickness_mm": 1.065, "er": 4.5, "loss_tangent": 0.02},
                {"type": "copper", "name": "In2.Cu", "thickness_mm": 0.035},
                {"type": "dielectric", "name": "prepreg 2", "thickness_mm": 0.2, "er": 4.2, "loss_tangent": 0.02},
                {"type": "copper", "name": "B.Cu", "thickness_mm": 0.035},
            ],
        }
        spec["netclasses"]["Default"]["layer"] = "F.Cu"
        spec["netclasses"]["Default"]["impedance_ohm"] = 50
        spec["netclasses"]["Default"]["impedance_tolerance_pct"] = 10
        spec["netclasses"]["Default"]["track_width_mm"] = 0.2
        findings = ds.validate_spec(spec, ds.load_profile("4layer-impedance"))
        self.assertTrue(any(item["code"] == "impedance.target" for item in findings.errors))


class NetlistTests(unittest.TestCase):
    def test_parse_and_merge(self) -> None:
        netlist_text = """(export (version "E")
  (components
    (comp (ref "J1") (value "5V IN") (footprint "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"))
    (comp (ref "R1") (value "330R") (footprint "Resistor_SMD:R_0805_2012Metric"))
    (comp (ref "D1") (value "RED") (footprint "LED_SMD:LED_0805_2012Metric"))
    (comp (ref "H1") (value "MOUNT") (footprint "MountingHole:MountingHole_2.5mm"))
  )
  (nets
    (net (code "1") (name "+5V") (node (ref "J1") (pin "1")) (node (ref "R1") (pin "1")))
    (net (code "2") (name "GND") (node (ref "J1") (pin "2")) (node (ref "D1") (pin "1")))
    (net (code "3") (name "LED_A") (node (ref "R1") (pin "2")) (node (ref "D1") (pin "2")))
  )
)
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.net"
            path.write_text(netlist_text, encoding="utf-8")
            netlist = ds.parse_kicad_netlist(path)
        self.assertEqual({comp["ref"] for comp in netlist["components"]}, {"J1", "R1", "D1", "H1"})
        self.assertEqual(netlist["components"][0]["pads"], {"1": "+5V", "2": "GND"})
        merged = ds.merge_netlist(led_spec(), netlist)
        self.assertEqual(merged["components"][0]["ref"], "J1")
        self.assertEqual(merged["components"][0]["pads"]["1"], "+5V")

    def test_missing_overlay_pads_are_rejected(self) -> None:
        netlist_text = """(export (version "E")
  (components
    (comp (ref "J1") (value "5V IN") (footprint "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"))
  )
  (nets
    (net (code "1") (name "+5V") (node (ref "J1") (pin "1")))
  )
)
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.net"
            path.write_text(netlist_text, encoding="utf-8")
            netlist = ds.parse_kicad_netlist(path)
        with self.assertRaises(ValueError):
            ds.merge_netlist(led_spec(), netlist)


class GateTests(unittest.TestCase):
    def test_reference_design_can_claim_professional(self) -> None:
        spec = led_spec()
        profile = led_profile()
        findings = ds.validate_spec(spec, profile)
        report = ds.gate_report(
            spec,
            profile,
            findings,
            {"structural": 0, "drc": 0, "unconnected": 0, "dfm": 0, "netlist_parity": 0, "stackup": 0, "length_match": 0, "fab": 0},
        )
        self.assertTrue(report["professional_claim"], report["failures"])

    def test_missing_signoff_blocks_professional_claim(self) -> None:
        spec = led_spec()
        spec["profile"] = "rf-mixed-signal"
        profile = ds.load_profile("rf-mixed-signal")
        findings = ds.validate_spec(spec, profile)
        report = ds.gate_report(
            spec,
            profile,
            findings,
            {"structural": 0, "drc": 0, "unconnected": 0, "dfm": 0, "netlist_parity": 0, "stackup": 0, "length_match": 0, "fab": 0},
        )
        self.assertFalse(report["professional_claim"])
        self.assertIn("rf", report["missing_signoff"])

    def test_completed_signoff_requires_reviewer_and_date(self) -> None:
        spec = led_spec()
        spec["profile"] = "rf-mixed-signal"
        profile = ds.load_profile("rf-mixed-signal")
        spec["signoff"]["completed"] = list(profile["human_signoff"])
        findings = ds.validate_spec(spec, profile)
        report = ds.gate_report(
            spec,
            profile,
            findings,
            {"structural": 0, "drc": 0, "unconnected": 0, "dfm": 0, "netlist_parity": 0, "stackup": 0, "length_match": 0, "fab": 0},
        )
        self.assertFalse(report["professional_claim"])
        self.assertEqual(set(report["missing_signoff_metadata"]), {"reviewer", "date"})

    def test_hard_gate_failure_blocks_professional_claim(self) -> None:
        spec = led_spec()
        profile = led_profile()
        findings = ds.validate_spec(spec, profile)
        report = ds.gate_report(
            spec,
            profile,
            findings,
            {"structural": 0, "drc": 2, "unconnected": 0, "dfm": 0, "netlist_parity": 0, "stackup": 0, "length_match": 0, "fab": 0},
        )
        self.assertFalse(report["professional_claim"])
        self.assertIn("drc:2", report["failures"])

    def test_structural_failure_count_one_is_not_boolean_true(self) -> None:
        spec = led_spec()
        profile = led_profile()
        findings = ds.validate_spec(spec, profile)
        report = ds.gate_report(
            spec,
            profile,
            findings,
            {"structural": 1, "drc": 0, "unconnected": 0, "dfm": 0, "netlist_parity": 0, "stackup": 0, "length_match": 0, "fab": 0},
        )
        self.assertFalse(report["professional_claim"])
        self.assertIn("structural:1", report["failures"])


class SizingTests(unittest.TestCase):
    def test_ipc2152_width_increases_with_current(self) -> None:
        low = ds.ipc2152_width_mm(0.5, 1.0)
        high = ds.ipc2152_width_mm(2.0, 1.0)
        self.assertLess(low, high)

    def test_ipc2221_clearance_table(self) -> None:
        self.assertLess(ds.ipc2221_clearance_mm(24), ds.ipc2221_clearance_mm(120))
        self.assertLess(ds.ipc2221_clearance_mm(120), ds.ipc2221_clearance_mm(400))


if __name__ == "__main__":
    unittest.main()
