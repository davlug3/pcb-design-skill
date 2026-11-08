#!/usr/bin/env python3
"""Tests for the LTspice/SPICE netlist importer."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import design_spec as ds  # noqa: E402


def parse(text: str, *, pin_map=None, node_aliases=None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "design.cir"
        path.write_text(text, encoding="utf-8")
        return ds.parse_ltspice_netlist(path, pin_map=pin_map, node_aliases=node_aliases)


def component(netlist: dict, ref: str) -> dict:
    return next(comp for comp in netlist["components"] if comp["ref"] == ref)


def net(netlist: dict, name: str) -> dict:
    return next(item for item in netlist["nets"] if item["name"] == name)


class LtspiceParseTests(unittest.TestCase):
    def test_two_terminal_parts_default_to_pads_one_and_two(self) -> None:
        netlist = parse(
            """
            * RC divider
            V1 N001 0 5
            R1 N001 N002 1k
            R2 N002 0 2k
            C1 N002 0 100n
            .end
            """
        )
        self.assertEqual([comp["ref"] for comp in netlist["components"]], ["R1", "R2", "C1"])
        self.assertEqual(component(netlist, "R1")["pads"], {"1": "N001", "2": "N002"})
        self.assertEqual(component(netlist, "R2")["pads"], {"1": "N002", "2": "GND"})
        self.assertEqual(component(netlist, "R1")["value"], "1k")
        self.assertEqual(netlist["excluded"], ["V1"])
        self.assertEqual(net(netlist, "N002")["nodes"], [
            {"ref": "C1", "pin": "1"},
            {"ref": "R1", "pin": "2"},
            {"ref": "R2", "pin": "1"},
        ])
        self.assertEqual(netlist["format"], "ltspice")

    def test_node_aliases_and_ground(self) -> None:
        netlist = parse(
            """
            R1 N001 N002 330
            D1 N002 0 RED
            """,
            node_aliases={"N001": "+5V", "N002": "LED_A", "0": "GND"},
        )
        self.assertEqual(component(netlist, "R1")["pads"], {"1": "+5V", "2": "LED_A"})
        self.assertEqual(component(netlist, "D1")["pads"], {"1": "LED_A", "2": "GND"})
        self.assertEqual(component(netlist, "D1")["value"], "RED")
        self.assertIn("+5V", [item["name"] for item in netlist["nets"]])

    def test_bjt_requires_a_pin_map(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse("Q1 N003 N002 0 2N3904\n.model 2N3904 NPN(IS=1e-14)")
        self.assertIn("no pin map", str(ctx.exception))
        self.assertIn("Q1", str(ctx.exception))

    def test_tolerant_mode_returns_unresolved_parts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "design.cir"
            path.write_text(
                """
                Q1 N003 N002 0 2N3904
                .model 2N3904 NPN(IS=1e-14)
                X1 A B C VCC 0 OPAMP
                """,
                encoding="utf-8",
            )
            netlist = ds.parse_ltspice_netlist(path, strict=False)
        self.assertEqual(
            [item["ref"] for item in netlist["unresolved"]],
            ["Q1", "X1"],
        )
        self.assertEqual(netlist["unresolved"][0]["prefix"], "Q")
        self.assertEqual(netlist["unresolved"][0]["nodes"], ["N003", "N002", "0"])
        self.assertEqual(netlist["unresolved"][1]["prefix"], "X")
        self.assertEqual(netlist["components"], [])

    def test_multi_pad_pin_map_for_a_tab(self) -> None:
        netlist = parse(
            "Q1 C B E 2N3904\n.model 2N3904 NPN",
            pin_map={"refs": {"Q1": {"c": ["2", "4"], "b": "1", "e": "3"}}},
        )
        self.assertEqual(
            component(netlist, "Q1")["pads"],
            {"1": "B", "2": "C", "3": "E", "4": "C"},
        )

    def test_multi_pad_pin_map_rejects_two_nets_on_one_pad(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse(
                "Q1 C B E 2N3904\n.model 2N3904 NPN",
                pin_map={"refs": {"Q1": {"c": ["2", "4"], "b": "2", "e": "3"}}},
            )
        self.assertIn("maps to both", str(ctx.exception))

    def test_bjt_pin_map(self) -> None:
        netlist = parse(
            "Q1 N003 N002 0 2N3904",
            pin_map={"refs": {"Q1": {"c": "2", "b": "1", "e": "3"}}},
        )
        self.assertEqual(component(netlist, "Q1")["pads"], {"2": "N003", "1": "N002", "3": "GND"})
        self.assertEqual(component(netlist, "Q1")["value"], "2N3904")

    def test_bjt_substrate_node_is_detected_from_the_model(self) -> None:
        netlist = parse(
            "Q1 N003 N002 0 NSUB 2N3904\n.model 2N3904 NPN(IS=1e-14)",
            pin_map={"refs": {"Q1": {"c": "2", "b": "1", "e": "3", "s": "4"}}},
        )
        self.assertEqual(
            component(netlist, "Q1")["pads"],
            {"2": "N003", "1": "N002", "3": "GND", "4": "NSUB"},
        )
        self.assertEqual(component(netlist, "Q1")["value"], "2N3904")

    def test_mosfet_pin_map_with_bulk(self) -> None:
        netlist = parse(
            "M1 D G S B NMOS",
            pin_map={"refs": {"M1": {"d": "1", "g": "2", "s": "3", "b": "4"}}},
        )
        self.assertEqual(
            component(netlist, "M1")["pads"],
            {"1": "D", "2": "G", "3": "S", "4": "B"},
        )

    def test_pin_names_are_case_insensitive(self) -> None:
        netlist = parse(
            "Q1 N003 N002 0 2N3904",
            pin_map={"refs": {"Q1": {"C": "2", "B": "1", "E": "3"}}},
        )
        self.assertEqual(component(netlist, "Q1")["pads"], {"2": "N003", "1": "N002", "3": "GND"})

    def test_pin_map_node_count_mismatch_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse(
                "M1 D G S NMOS\n.model NMOS NMOS",
                pin_map={"refs": {"M1": {"d": "1", "g": "2", "s": "3", "b": "4"}}},
            )
        self.assertIn("4 pins", str(ctx.exception))

    def test_subcircuit_port_names(self) -> None:
        netlist = parse(
            """
            .subckt OPAMP in+ in- out v+ v-
            R1 in+ in- 1k
            .ends
            X1 A B C VCC 0 OPAMP
            """,
            pin_map={"refs": {"X1": {"in+": "1", "in-": "2", "out": "3", "v+": "4", "v-": "5"}}},
        )
        self.assertEqual(
            component(netlist, "X1")["pads"],
            {"1": "A", "2": "B", "3": "C", "4": "VCC", "5": "GND"},
        )
        self.assertEqual(component(netlist, "X1")["value"], "OPAMP")
        # The R1 inside the .subckt is a definition, not a board part.
        self.assertNotIn("R1", [comp["ref"] for comp in netlist["components"]])

    def test_subcircuit_ordered_pad_list(self) -> None:
        netlist = parse(
            "X1 A B C VCC 0 OPAMP",
            pin_map={"refs": {"X1": ["1", "2", "3", "4", "5"]}},
        )
        self.assertEqual(
            component(netlist, "X1")["pads"],
            {"1": "A", "2": "B", "3": "C", "4": "VCC", "5": "GND"},
        )

    def test_simulation_sources_are_excluded_by_default(self) -> None:
        netlist = parse(
            """
            V1 IN 0 5
            I1 IN 0 1m
            B1 OUT 0 V=sin(0 1 1k)
            R1 IN OUT 1k
            """
        )
        self.assertEqual([comp["ref"] for comp in netlist["components"]], ["R1"])
        self.assertEqual(netlist["excluded"], ["V1", "I1", "B1"])

    def test_include_and_exclude_override_defaults(self) -> None:
        netlist = parse(
            """
            V1 IN 0 5
            R1 IN 0 1k
            """,
            pin_map={"include": ["V1"], "refs": {"V1": ["1", "2"]}},
        )
        self.assertEqual([comp["ref"] for comp in netlist["components"]], ["V1", "R1"])
        self.assertEqual(component(netlist, "V1")["pads"], {"1": "IN", "2": "GND"})

    def test_continuation_lines_and_inline_comments(self) -> None:
        netlist = parse(
            """
            R1 N001
            + N002 1k ; this is a comment
            """
        )
        self.assertEqual(component(netlist, "R1")["pads"], {"1": "N001", "2": "N002"})
        self.assertEqual(component(netlist, "R1")["value"], "1k")

    def test_invalid_net_alias_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse("R1 N001 0 1k", node_aliases={"N001": ""})
        self.assertIn("invalid net name", str(ctx.exception))

    def test_duplicate_reference_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse("R1 N001 0 1k\nR1 N002 0 1k")
        self.assertIn("duplicate component reference", str(ctx.exception))

    def test_utf16_netlist_is_decoded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "design.cir"
            path.write_bytes("R1 N001 0 1k\n".encode("utf-16"))
            netlist = ds.parse_ltspice_netlist(path)
        self.assertEqual(component(netlist, "R1")["pads"], {"1": "N001", "2": "GND"})

    def test_unknown_lines_are_warnings(self) -> None:
        netlist = parse("LTspice title\nR1 N001 0 1k\nZ9 what is this")
        self.assertTrue(any("unrecognised line" in warning for warning in netlist["warnings"]))


class LtspiceMergeTests(unittest.TestCase):
    def overlay(self) -> dict:
        spec = ds.apply_defaults(ds.load_json(ROOT / "specs" / "led-blinky.json"))
        # Keep the fixture small: merge_netlist treats overlay parts with pads
        # that are absent from the netlist as an error.
        spec["components"] = [comp for comp in spec["components"] if comp["ref"] == "R1"]
        return spec

    def test_merge_keeps_overlay_footprint(self) -> None:
        netlist = parse("R1 +5V LED_A 330")
        merged = ds.merge_netlist(self.overlay(), netlist)
        r1 = next(comp for comp in merged["components"] if comp["ref"] == "R1")
        self.assertEqual(r1["footprint"], "Resistor_SMD:R_0805_2012Metric")
        self.assertEqual(r1["pads"], {"1": "+5V", "2": "LED_A"})
        self.assertEqual(merged["import"]["format"], "ltspice")

    def test_merge_falls_back_to_overlay_pads_when_importer_has_none(self) -> None:
        netlist = {
            "components": [{"ref": "R1", "value": "330R", "footprint": "", "pads": {}}],
            "nets": [],
            "format": "ltspice",
            "source": "synthetic",
        }
        merged = ds.merge_netlist(self.overlay(), netlist)
        r1 = next(comp for comp in merged["components"] if comp["ref"] == "R1")
        self.assertEqual(r1["pads"], {"1": "+5V", "2": "LED_A"})

    def test_excluded_overlay_part_is_not_an_error(self) -> None:
        overlay = self.overlay()
        overlay["components"].append(
            {
                "ref": "V1",
                "value": "5V",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
                "pads": {"1": "+5V", "2": "GND"},
            }
        )
        netlist = parse("R1 +5V LED_A 330", pin_map={"exclude": ["V1"]})
        self.assertIn("V1", netlist["excluded"])
        merged = ds.merge_netlist(overlay, netlist)
        self.assertIn("V1", [comp["ref"] for comp in merged["components"]])

    def test_cli_writes_resolved_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            netlist_path = Path(tmp) / "design.cir"
            netlist_path.write_text("R1 +5V LED_A 330\n", encoding="utf-8")
            spec_path = Path(tmp) / "overlay.json"
            spec_path.write_text(json.dumps(self.overlay()), encoding="utf-8")
            output = Path(tmp) / "resolved.json"
            code = ds.main(
                [
                    "ltspice",
                    str(netlist_path),
                    "--spec",
                    str(spec_path),
                    "-o",
                    str(output),
                ]
            )
            self.assertEqual(code, 0)
            resolved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(resolved["import"]["format"], "ltspice")
            self.assertEqual(resolved["netlist_source"], str(netlist_path))


if __name__ == "__main__":
    unittest.main()
