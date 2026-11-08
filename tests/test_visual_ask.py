#!/usr/bin/env python3
"""Tests for the visual question payload and HTTP server."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import visual_ask  # noqa: E402

SMD_FOOTPRINT = """
(footprint "Test_0805"
  (version 20241229)
  (generator "pcbnew")
  (layer "F.Cu")
  (descr "Test 0805 resistor")
  (attr smd)
  (fp_line (start -1.7 -1.0) (end 1.7 -1.0) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start 1.7 -1.0) (end 1.7 1.0) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start 1.7 1.0) (end -1.7 1.0) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (fp_line (start -1.7 1.0) (end -1.7 -1.0) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
  (pad "1" smd roundrect (at -1 0) (size 1.2 1.4) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
  (pad "2" smd roundrect (at 1 0) (size 1.2 1.4) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
)
"""


def payload() -> dict:
    return {
        "title": "Choose the parts",
        "intro": "Test payload",
        "questions": [
            {
                "id": "R1",
                "type": "footprint",
                "prompt": "Which resistor?",
                "allow_custom": True,
                "options": [
                    {
                        "id": "Resistor_SMD:R_0805_2012Metric",
                        "footprint": "Resistor_SMD:R_0805_2012Metric",
                        "label": "0805",
                    }
                ],
            },
            {
                "id": "color",
                "type": "choice",
                "prompt": "Which color?",
                "options": [
                    {"id": "red", "label": "Red", "swatch": "#c0392b"},
                    {"id": "green", "label": "Green", "swatch": "#2e8b57"},
                ],
            },
            {
                "id": "silk",
                "type": "multi",
                "prompt": "Which silk marks?",
                "required": False,
                "options": [{"id": "refs", "label": "References"}, {"id": "pin1", "label": "Pin 1"}],
            },
            {"id": "notes", "type": "text", "prompt": "Notes?"},
            {"id": "holes", "type": "confirm", "prompt": "Mounting holes?", "required": False},
        ],
    }


class NormalizeTests(unittest.TestCase):
    def test_example_payload_is_valid(self) -> None:
        data = json.loads((ROOT / "examples" / "visual-ask-led.json").read_text(encoding="utf-8"))
        normalized = visual_ask.normalize_payload(data, enrich=False)
        self.assertEqual(len(normalized["questions"]), 6)
        self.assertEqual(normalized["questions"][2]["type"], "choice")

    def test_defaults_and_option_shorthand(self) -> None:
        data = {
            "title": "Test",
            "questions": [
                {
                    "id": "R1",
                    "type": "footprint",
                    "prompt": "Pick one",
                    "allow_custom": True,
                    "options": ["Resistor_SMD:R_0805_2012Metric"],
                },
                {"id": "notes", "type": "text", "prompt": "Notes?"},
            ],
        }
        normalized = visual_ask.normalize_payload(data, enrich=False)
        self.assertTrue(normalized["questions"][0]["required"])
        self.assertTrue(normalized["questions"][0]["allow_custom"])
        self.assertFalse(normalized["questions"][1]["required"])
        option = normalized["questions"][0]["options"][0]
        self.assertEqual(option["id"], "Resistor_SMD:R_0805_2012Metric")
        self.assertEqual(option["footprint"], "Resistor_SMD:R_0805_2012Metric")

    def test_duplicate_question_ids_rejected(self) -> None:
        data = payload()
        data["questions"][1]["id"] = "R1"
        with self.assertRaises(visual_ask.PayloadError):
            visual_ask.normalize_payload(data, enrich=False)

    def test_unknown_type_rejected(self) -> None:
        data = payload()
        data["questions"][0]["type"] = "telepathy"
        with self.assertRaises(visual_ask.PayloadError):
            visual_ask.normalize_payload(data, enrich=False)

    def test_svg_is_sanitized(self) -> None:
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><rect onload="x()" /></svg>'
        cleaned = visual_ask._clean_svg(svg)
        self.assertIsNotNone(cleaned)
        self.assertNotIn("<script", cleaned)
        self.assertNotIn("onload=", cleaned)

    def test_local_footprint_preview_is_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Test_0805.kicad_mod"
            path.write_text(SMD_FOOTPRINT, encoding="utf-8")
            data = payload()
            data["questions"][0]["options"][0] = {"id": "local", "footprint": str(path), "label": "Local"}
            normalized = visual_ask.normalize_payload(data, enrich=True, fetch=False)
        option = normalized["questions"][0]["options"][0]
        self.assertIn("preview_svg", option)
        self.assertEqual(option["meta"]["pads"], 2)
        self.assertTrue(option["meta"]["available"])


class ValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = visual_ask.normalize_payload(payload(), enrich=False)

    def test_required_answer_missing(self) -> None:
        _, errors = visual_ask.validate_answers(self.data, {"color": {"value": "red"}})
        self.assertTrue(any(error["question"] == "R1" for error in errors))

    def test_valid_answers(self) -> None:
        normalized, errors = visual_ask.validate_answers(
            self.data,
            {
                "R1": {"value": "Resistor_SMD:R_0805_2012Metric"},
                "color": {"value": "green"},
                "silk": {"value": ["refs", "pin1"]},
                "notes": {"value": "Keep it small."},
                "holes": {"value": True},
            },
        )
        self.assertEqual(errors, [])
        self.assertEqual(normalized["R1"]["label"], "0805")
        self.assertEqual(normalized["color"]["label"], "Green")
        self.assertEqual(normalized["silk"]["labels"], ["References", "Pin 1"])
        self.assertEqual(normalized["notes"]["value"], "Keep it small.")
        self.assertIs(normalized["holes"]["value"], True)

    def test_unknown_option_rejected(self) -> None:
        _, errors = visual_ask.validate_answers(
            self.data,
            {"R1": {"value": "nope"}, "color": {"value": "red"}},
        )
        self.assertTrue(any(error["question"] == "R1" for error in errors))

    def test_custom_footprint_allowed(self) -> None:
        normalized, errors = visual_ask.validate_answers(
            self.data,
            {
                "R1": {"value": "MyLib:MyPart", "custom": "MyLib:MyPart"},
                "color": {"value": "red"},
            },
        )
        self.assertEqual(errors, [])
        self.assertTrue(normalized["R1"]["custom"])
        self.assertEqual(normalized["R1"]["footprint"], "MyLib:MyPart")

    def test_custom_footprint_needs_library_syntax(self) -> None:
        _, errors = visual_ask.validate_answers(
            self.data,
            {"R1": {"value": "just-a-word", "custom": "just-a-word"}, "color": {"value": "red"}},
        )
        self.assertTrue(any(error["question"] == "R1" for error in errors))

    def test_multi_min_selections(self) -> None:
        data = visual_ask.normalize_payload(
            {
                "title": "T",
                "questions": [
                    {
                        "id": "pick",
                        "type": "multi",
                        "prompt": "Pick",
                        "min_selections": 2,
                        "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                    }
                ],
            },
            enrich=False,
        )
        _, errors = visual_ask.validate_answers(data, {"pick": {"value": ["a"]}})
        self.assertTrue(errors)
        data["questions"][0]["required"] = False
        _, errors = visual_ask.validate_answers(data, {"pick": {"value": ["a"]}})
        self.assertEqual(errors, [])

    def test_confirm_requires_boolean_when_required(self) -> None:
        data = visual_ask.normalize_payload(
            {
                "title": "T",
                "questions": [{"id": "ok", "type": "confirm", "prompt": "OK?"}],
            },
            enrich=False,
        )
        _, errors = visual_ask.validate_answers(data, {"ok": {"value": "yes"}})
        self.assertTrue(errors)


class SpecPayloadTests(unittest.TestCase):
    def test_infer_category(self) -> None:
        self.assertEqual(visual_ask.infer_category("Resistor_SMD:R_0805_2012Metric"), "resistor")
        self.assertEqual(visual_ask.infer_category("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"), "soic")
        self.assertEqual(visual_ask.infer_category("Package_TO_SOT_SMD:SOT-23"), "sot")
        self.assertIsNone(visual_ask.infer_category("Custom:Thing"))

    def test_spec_payload_offers_current_and_catalog(self) -> None:
        spec = json.loads((ROOT / "specs" / "led-blinky.json").read_text(encoding="utf-8"))
        data = visual_ask.build_spec_payload(spec)
        normalized = visual_ask.normalize_payload(data, enrich=False)
        by_id = {question["id"]: question for question in normalized["questions"]}
        self.assertIn("footprint_R1", by_id)
        self.assertEqual(by_id["footprint_R1"]["type"], "footprint")
        self.assertEqual(
            by_id["footprint_R1"]["options"][0]["id"],
            "Resistor_SMD:R_0805_2012Metric",
        )
        self.assertGreaterEqual(len(by_id["footprint_R1"]["options"]), 5)

    def test_only_missing_and_unknown_family_text(self) -> None:
        spec = {
            "name": "test",
            "components": [
                {"ref": "R1", "value": "1k", "footprint": "Resistor_SMD:R_0805_2012Metric"},
                {"ref": "X1", "value": "widget", "footprint": ""},
            ],
        }
        data = visual_ask.build_spec_payload(spec, only_missing=True)
        normalized = visual_ask.normalize_payload(data, enrich=False)
        self.assertEqual(len(normalized["questions"]), 1)
        self.assertEqual(normalized["questions"][0]["id"], "footprint_X1")
        self.assertEqual(normalized["questions"][0]["type"], "text")
        self.assertTrue(normalized["questions"][0]["required"])

    def test_only_missing_with_nothing_missing_rejected(self) -> None:
        spec = {
            "name": "test",
            "components": [{"ref": "R1", "footprint": "Resistor_SMD:R_0805_2012Metric"}],
        }
        with self.assertRaises(visual_ask.PayloadError):
            visual_ask.build_spec_payload(spec, only_missing=True)

    def test_categories_override_missing_footprints(self) -> None:
        spec = {
            "name": "spice-test",
            "components": [
                {"ref": "R1", "value": "1k", "footprint": ""},
                {"ref": "Q1", "value": "2N3904", "footprint": ""},
                {"ref": "X1", "value": "OPAMP", "footprint": ""},
            ],
        }
        data = visual_ask.build_spec_payload(
            spec,
            only_missing=True,
            categories={"R1": "resistor", "Q1": "sot", "X1": "ic"},
        )
        normalized = visual_ask.normalize_payload(data, enrich=False)
        by_id = {question["id"]: question for question in normalized["questions"]}
        self.assertEqual(by_id["footprint_R1"]["type"], "footprint")
        self.assertEqual(by_id["footprint_Q1"]["type"], "footprint")
        self.assertEqual(by_id["footprint_X1"]["type"], "footprint")
        self.assertEqual(
            by_id["footprint_Q1"]["options"][0]["id"],
            "Package_TO_SOT_SMD:SOT-23",
        )
        self.assertEqual(
            by_id["footprint_X1"]["options"][0]["id"],
            "Package_DIP:DIP-8_W7.62mm",
        )


class CatalogTests(unittest.TestCase):
    def test_catalog_payload_has_real_ids(self) -> None:
        data = visual_ask.build_catalog_payload("R1", "resistor")
        normalized = visual_ask.normalize_payload(data, enrich=False)
        question = normalized["questions"][0]
        self.assertEqual(question["type"], "footprint")
        self.assertGreaterEqual(len(question["options"]), 5)
        self.assertTrue(all(":" in option["id"] for option in question["options"]))

    def test_unknown_category_rejected(self) -> None:
        with self.assertRaises(visual_ask.PayloadError):
            visual_ask.build_catalog_payload("R1", "not-a-category")

    def test_spice_categories_have_real_ids(self) -> None:
        for category in ("inductor", "diode", "ic"):
            data = visual_ask.build_catalog_payload("X1", category)
            normalized = visual_ask.normalize_payload(data, enrich=False)
            options = normalized["questions"][0]["options"]
            self.assertTrue(options)
            self.assertTrue(all(":" in option["id"] for option in options))


class ServerTests(unittest.TestCase):
    def _request(self, url: str, method: str = "GET", body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(request, timeout=5)

    def test_payload_and_answer_flow(self) -> None:
        data = visual_ask.normalize_payload(payload(), enrich=False)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "answers.json"
            server = visual_ask.make_server(
                data,
                out_path=out,
                host="127.0.0.1",
                port=0,
                session_id="test-session",
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with self._request(f"{base}/api/payload") as response:
                    served = json.loads(response.read())
                self.assertEqual(served["session"], "test-session")
                self.assertEqual(served["questions"][0]["id"], "R1")

                with self._request(f"{base}/") as response:
                    html = response.read().decode("utf-8")
                self.assertIn("<title>PCB design questions</title>", html)
                for asset, marker in (("/styles.css", ":root"), ("/app.js", "addEventListener"), ("/favicon.svg", "<svg")):
                    with self._request(f"{base}{asset}") as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(marker, response.read().decode("utf-8"))

                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self._request(
                        f"{base}/api/answer",
                        method="POST",
                        body={"answers": {"color": {"value": "red"}}},
                    )
                self.assertEqual(caught.exception.code, 400)
                caught.exception.close()

                with self._request(
                    f"{base}/api/answer",
                    method="POST",
                    body={
                        "answers": {
                            "R1": {"value": "Resistor_SMD:R_0805_2012Metric"},
                            "color": {"value": "green"},
                            "silk": {"value": ["refs"]},
                            "notes": {"value": ""},
                            "holes": {"value": False},
                        }
                    },
                ) as response:
                    result = json.loads(response.read())
                self.assertTrue(result["ok"])
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                saved = json.loads(out.read_text(encoding="utf-8"))
                self.assertEqual(saved["session"], "test-session")
                self.assertEqual(saved["answers"]["color"]["value"], "green")
                self.assertEqual(saved["answers"]["silk"]["value"], ["refs"])
            finally:
                server.server_close()
                if thread.is_alive():
                    server.shutdown()
                    thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
