#!/usr/bin/env python3
"""Tests for builder helpers that do not require pcbnew."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import design_spec as ds  # noqa: E402
import pcb_build  # noqa: E402
import pcb_fab  # noqa: E402


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


class FabOutputTests(unittest.TestCase):
    def _fake_cli(self, directory: Path) -> Path:
        path = directory / "kicad-cli"
        path.write_text(
            f"#!{sys.executable}\n"
            + textwrap.dedent(
                """\
                import os
                import pathlib
                import sys

                args = sys.argv[1:]
                sub = args[2] if len(args) > 2 else ""
                out = args[args.index("-o") + 1] if "-o" in args else None
                if sub == "gerbers":
                    target = pathlib.Path(out)
                    target.mkdir(parents=True, exist_ok=True)
                    (target / "fake.gtl").write_text("gerber")
                elif sub == "drill":
                    target = pathlib.Path(out)
                    target.mkdir(parents=True, exist_ok=True)
                    (target / "fake.drl").write_text("drill")
                elif sub in ("pos", "step"):
                    pathlib.Path(out).write_text(sub)
                elif sub in ("ipcd356", "ipc356"):
                    if os.environ.get("FAKE_FAIL_IPC") == "1":
                        sys.exit(1)
                    pathlib.Path(out).write_text("ipc")
                else:
                    sys.exit(2)
                """
            )
        )
        path.chmod(0o755)
        return path

    def test_profile_requires_all_package_outputs(self) -> None:
        self.assertEqual(
            pcb_fab.required_fab_outputs(spec()),
            {"gerbers", "drill", "pos", "step", "ipc356"},
        )
        self.assertEqual(
            pcb_fab.required_fab_outputs(spec(), ["gerbers", "ipcd356"]),
            {"gerbers", "ipc356"},
        )

    def test_gerber_layers_follow_copper_count(self) -> None:
        two = pcb_fab.gerber_layers(spec())
        self.assertEqual(two[:2], ["F.Cu", "B.Cu"])
        self.assertNotIn("In1.Cu", two)
        self.assertIn("Edge.Cuts", two)
        self.assertIn("F.SilkS", two)
        four_spec = spec()
        four_spec["board"]["layers"] = 4
        four = pcb_fab.gerber_layers(four_spec)
        self.assertEqual(four[:4], ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])
        six_spec = spec()
        six_spec["board"]["layers"] = 6
        self.assertEqual(pcb_fab.gerber_layers(six_spec)[:6], ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"])

    def test_export_package_and_ipc356(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = root / "board.kicad_pcb"
            board.write_text("(kicad_pcb)")
            fake = self._fake_cli(root)
            result = pcb_fab.export_fab(spec(), board, root / "fab", kicad_cli=str(fake))
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["missing_required"], [])
            self.assertTrue(Path(result["ipc356"]).is_file())
            self.assertTrue(Path(result["archive"]).is_file())
            with zipfile.ZipFile(result["archive"]) as zf:
                names = set(zf.namelist())
            self.assertIn("fab.json", names)
            self.assertIn("gerbers/fake.gtl", names)
            self.assertIn("drill/fake.drl", names)

    def test_missing_required_ipc356_fails_the_fab_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = root / "board.kicad_pcb"
            board.write_text("(kicad_pcb)")
            fake = self._fake_cli(root)
            old = os.environ.get("FAKE_FAIL_IPC")
            os.environ["FAKE_FAIL_IPC"] = "1"
            try:
                result = pcb_fab.export_fab(spec(), board, root / "fab", kicad_cli=str(fake))
            finally:
                if old is None:
                    os.environ.pop("FAKE_FAIL_IPC", None)
                else:
                    os.environ["FAKE_FAIL_IPC"] = old
            self.assertFalse(result["ok"])
            self.assertEqual(result["missing_required"], ["ipc356"])
            ipc_step = next(item for item in result["steps"] if item["step"] == "ipc356")
            self.assertTrue(ipc_step["required"])
            self.assertFalse(ipc_step["ok"])


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
