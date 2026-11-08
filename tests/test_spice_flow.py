#!/usr/bin/env python3
"""Tests for the interactive LTspice completion flow."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import design_spec as ds  # noqa: E402
import spice_flow  # noqa: E402
import visual_ask  # noqa: E402


def overlay() -> dict:
    return ds.apply_defaults(
        {
            "version": 1,
            "name": "spice-test",
            "profile": "2layer-low-speed",
            "board": {"width_mm": 40, "height_mm": 30, "layers": 2},
            "rules": {},
            "nets": [
                {"name": "+5V", "class": "Power"},
                {"name": "GND", "class": "Power"},
                {"name": "BASE"},
            ],
            "components": [],
            "placement": {"mode": "fixed"},
        }
    )


def write_netlist(tmp: str, text: str) -> Path:
    path = Path(tmp) / "design.cir"
    path.write_text(text, encoding="utf-8")
    return path


def component(spec: dict, ref: str) -> dict:
    return next(comp for comp in spec["components"] if comp["ref"] == ref)


class SpiceFlowTests(unittest.TestCase):
    def test_asks_for_pin_map_aliases_footprints_and_placement(self) -> None:
        netlist_text = """
        V1 IN 0 5
        R1 IN N001 1k
        C1 N001 0 100n
        Q1 N001 N002 0 2N3904
        .model 2N3904 NPN(IS=1e-14)
        """
        asked_payloads: list[dict] = []

        def asker(payload: dict, out_path: Path) -> dict:
            asked_payloads.append(payload)
            answers: dict = {}
            for question in payload["questions"]:
                qid = question["id"]
                if qid == "pinmap_Q1":
                    answers[qid] = {"value": "sot23_bce"}
                elif qid == "node_N001":
                    answers[qid] = {"value": "net_0"}
                elif qid == "node_N002":
                    answers[qid] = {"value": "net_1"}
                elif qid == "footprint_R1":
                    answers[qid] = {
                        "value": "Resistor_SMD:R_0805_2012Metric",
                        "footprint": "Resistor_SMD:R_0805_2012Metric",
                    }
                elif qid == "footprint_C1":
                    answers[qid] = {
                        "value": "Capacitor_SMD:C_0805_2012Metric",
                        "footprint": "Capacitor_SMD:C_0805_2012Metric",
                    }
                elif qid == "footprint_Q1":
                    answers[qid] = {
                        "value": "Package_TO_SOT_SMD:SOT-23",
                        "footprint": "Package_TO_SOT_SMD:SOT-23",
                    }
                elif qid == "placement_mode":
                    answers[qid] = {"value": "auto"}
            return answers

        with tempfile.TemporaryDirectory() as tmp:
            path = write_netlist(tmp, netlist_text)
            result = spice_flow.resolve_ltspice(
                overlay(), path, asker=asker, ask_dir=Path(tmp) / "ask"
            )

        self.assertEqual(
            result.asked,
            ["pin maps", "node aliases", "footprints", "placement"],
        )
        self.assertEqual(len(asked_payloads), 3)
        self.assertEqual(result.spec["placement"]["mode"], "auto")
        self.assertEqual(
            component(result.spec, "R1")["footprint"],
            "Resistor_SMD:R_0805_2012Metric",
        )
        self.assertEqual(
            component(result.spec, "C1")["footprint"],
            "Capacitor_SMD:C_0805_2012Metric",
        )
        self.assertEqual(
            component(result.spec, "Q1")["footprint"],
            "Package_TO_SOT_SMD:SOT-23",
        )
        self.assertEqual(
            component(result.spec, "Q1")["pads"],
            {"1": "BASE", "2": "GND", "3": "+5V"},
        )
        self.assertEqual(component(result.spec, "R1")["pads"], {"1": "IN", "2": "+5V"})
        self.assertEqual(component(result.spec, "C1")["pads"], {"1": "+5V", "2": "GND"})
        self.assertEqual(
            [net["name"] for net in result.spec["nets"]],
            ["+5V", "BASE", "GND", "IN"],
        )

    def test_no_ask_reports_missing_pin_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_netlist(tmp, "Q1 C B 0 2N3904\n.model 2N3904 NPN\n")
            with self.assertRaises(spice_flow.SpiceInputError) as caught:
                spice_flow.resolve_ltspice(overlay(), path, ask=False)
        message = str(caught.exception)
        self.assertIn("pin maps", message)
        self.assertIn("Q1", message)

    def test_no_ask_reports_missing_footprints_and_placement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_netlist(tmp, "R1 IN 0 1k\n")
            with self.assertRaises(spice_flow.SpiceInputError) as caught:
                spice_flow.resolve_ltspice(overlay(), path, ask=False)
        message = str(caught.exception)
        self.assertIn("footprint for R1", message)
        self.assertIn("placement for R1", message)

    def test_complete_overlay_does_not_ask(self) -> None:
        def asker(payload: dict, out_path: Path) -> dict:
            raise AssertionError("asker should not be called")

        spec = overlay()
        spec["components"] = [
            {
                "ref": "R1",
                "value": "1k",
                "footprint": "Resistor_SMD:R_0805_2012Metric",
                "at": {"x_mm": 10, "y_mm": 10},
                "pads": {"1": "IN", "2": "GND"},
            }
        ]
        spec["nets"] = [{"name": "IN"}, {"name": "GND"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_netlist(tmp, "R1 IN 0 1k\n")
            result = spice_flow.resolve_ltspice(
                spec, path, ask=True, asker=asker, ask_dir=Path(tmp) / "ask"
            )
        self.assertEqual(result.asked, [])
        self.assertEqual(
            component(result.spec, "R1")["footprint"],
            "Resistor_SMD:R_0805_2012Metric",
        )

    def test_placement_only_question(self) -> None:
        spec = overlay()
        spec["components"] = [
            {
                "ref": "R1",
                "value": "1k",
                "footprint": "Resistor_SMD:R_0805_2012Metric",
            }
        ]
        spec["nets"] = [{"name": "IN"}, {"name": "GND"}]

        def asker(payload: dict, out_path: Path) -> dict:
            self.assertEqual(
                [question["id"] for question in payload["questions"]],
                ["placement_mode"],
            )
            return {"placement_mode": {"value": "auto"}}

        with tempfile.TemporaryDirectory() as tmp:
            path = write_netlist(tmp, "R1 IN 0 1k\n")
            result = spice_flow.resolve_ltspice(
                spec, path, asker=asker, ask_dir=Path(tmp) / "ask"
            )
        self.assertEqual(result.spec["placement"]["mode"], "auto")
        self.assertEqual(result.asked, ["placement"])

    def test_custom_pin_map_text(self) -> None:
        def asker(payload: dict, out_path: Path) -> dict:
            return {
                "pinmap_Q1": {"value": "custom"},
                "pinmap_Q1_custom": {"value": '{"c": "1", "b": "2", "e": "3"}'},
                "placement_mode": {"value": "auto"},
                "footprint_Q1": {
                    "value": "Package_TO_SOT_SMD:SOT-23",
                    "footprint": "Package_TO_SOT_SMD:SOT-23",
                },
            }

        spec = overlay()
        spec["nets"] = [{"name": "C"}, {"name": "B"}, {"name": "GND"}]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_netlist(tmp, "Q1 C B 0 2N3904\n.model 2N3904 NPN\n")
            result = spice_flow.resolve_ltspice(
                spec, path, asker=asker, ask_dir=Path(tmp) / "ask"
            )
        self.assertEqual(
            component(result.spec, "Q1")["pads"],
            {"1": "C", "2": "B", "3": "GND"},
        )

    def test_subcircuit_ordered_pads(self) -> None:
        def asker(payload: dict, out_path: Path) -> dict:
            answers: dict = {}
            for question in payload["questions"]:
                qid = question["id"]
                if qid == "pinmap_X1":
                    answers[qid] = {"value": "1, 2, 3, 4, 5"}
                elif qid == "footprint_X1":
                    answers[qid] = {
                        "value": "Package_DIP:DIP-8_W7.62mm",
                        "footprint": "Package_DIP:DIP-8_W7.62mm",
                    }
                elif qid == "placement_mode":
                    answers[qid] = {"value": "auto"}
            return answers

        spec = overlay()
        spec["nets"] = [
            {"name": "A"},
            {"name": "B"},
            {"name": "C"},
            {"name": "VCC"},
            {"name": "GND"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_netlist(
                tmp,
                """
                .subckt OPAMP in+ in- out v+ v-
                .ends
                X1 A B C VCC 0 OPAMP
                """,
            )
            result = spice_flow.resolve_ltspice(
                spec, path, asker=asker, ask_dir=Path(tmp) / "ask"
            )
        self.assertEqual(
            component(result.spec, "X1")["pads"],
            {"1": "A", "2": "B", "3": "C", "4": "VCC", "5": "GND"},
        )

    def test_merge_conflict_is_reported_as_spice_input(self) -> None:
        spec = overlay()
        spec["components"] = [
            {
                "ref": "J1",
                "value": "CONN",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
                "at": {"x_mm": 5, "y_mm": 5},
                "pads": {"1": "IN", "2": "GND"},
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_netlist(tmp, "R1 IN 0 1k\n")
            with self.assertRaises(spice_flow.SpiceInputError) as caught:
                spice_flow.resolve_ltspice(spec, path, ask=False)
        self.assertIn("J1", str(caught.exception))

    def test_missing_inputs_does_not_ask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_netlist(tmp, "Q1 C B 0 2N3904\n.model 2N3904 NPN\n")
            missing = spice_flow.missing_inputs(overlay(), path)
        self.assertEqual(missing, ["pin map for Q1 (3-node Q)"])

    def test_pinmap_payload_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_netlist(tmp, "Q1 C B 0 2N3904\n.model 2N3904 NPN\n")
            netlist = ds.parse_ltspice_netlist(path, strict=False)
        payload, option_maps = spice_flow._pinmap_payload(netlist["unresolved"])
        normalized = visual_ask.normalize_payload(payload, enrich=False)
        by_id = {question["id"]: question for question in normalized["questions"]}
        self.assertEqual(by_id["pinmap_Q1"]["type"], "choice")
        self.assertFalse(by_id["pinmap_Q1"]["required"])
        self.assertEqual(by_id["pinmap_Q1_custom"]["type"], "text")
        self.assertIn("sot23_bce", option_maps["pinmap_Q1"])

    def test_footprint_placement_payload_is_valid(self) -> None:
        spec = overlay()
        spec["components"] = [
            {"ref": "R1", "value": "1k", "footprint": ""},
            {"ref": "Q1", "value": "2N3904", "footprint": ""},
        ]
        payload, qid_to_ref = spice_flow._footprint_placement_payload(
            spec, {"R1": "R", "Q1": "Q"}, ["R1", "Q1"]
        )
        self.assertIsNotNone(payload)
        normalized = visual_ask.normalize_payload(payload, enrich=False)
        by_id = {question["id"]: question for question in normalized["questions"]}
        self.assertEqual(by_id["footprint_R1"]["type"], "footprint")
        self.assertEqual(by_id["footprint_Q1"]["type"], "footprint")
        self.assertEqual(by_id["placement_mode"]["type"], "choice")
        self.assertEqual(qid_to_ref["footprint_R1"], "R1")

    def test_category_inference(self) -> None:
        self.assertEqual(spice_flow._category_for("R", "1k"), "resistor")
        self.assertEqual(spice_flow._category_for("C", "100n"), "capacitor")
        self.assertEqual(spice_flow._category_for("L", "10u"), "inductor")
        self.assertEqual(spice_flow._category_for("D", "RED"), "led")
        self.assertEqual(spice_flow._category_for("D", "1N4148"), "diode")
        self.assertEqual(spice_flow._category_for("Q", "2N3904"), "sot")
        self.assertEqual(spice_flow._category_for("M", "NMOS"), "sot")
        self.assertEqual(spice_flow._category_for("X", "OPAMP"), "ic")
        self.assertIsNone(spice_flow._category_for("V", "5V"))


if __name__ == "__main__":
    unittest.main()
