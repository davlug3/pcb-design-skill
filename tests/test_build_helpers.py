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
import pcb_route  # noqa: E402


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


class AutomaticPlacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = {
            "board": {"width_mm": 30, "height_mm": 20},
            "placement": {"mode": "auto", "margin_mm": 2, "clearance_mm": 1, "grid_mm": 2.54},
        }

    def test_net_connected_components_are_placed_close_and_deterministically(self) -> None:
        entries = [
            ({"ref": "U1", "pads": {"1": "SIGNAL", "2": "GND"}}, 4.0, 4.0),
            ({"ref": "R1", "pads": {"1": "SIGNAL"}}, 2.0, 2.0),
            ({"ref": "C1", "pads": {"1": "GND"}}, 2.0, 2.0),
            ({"ref": "R9", "pads": {"1": "UNRELATED"}}, 2.0, 2.0),
        ]
        first = pcb_build.placement_engine.optimize_placement(entries, self.spec)
        second = pcb_build.placement_engine.optimize_placement(entries, self.spec)
        self.assertEqual(first, second)
        signal_distance = abs(first["U1"][0] - first["R1"][0]) + abs(first["U1"][1] - first["R1"][1])
        unrelated_distance = abs(first["U1"][0] - first["R9"][0]) + abs(first["U1"][1] - first["R9"][1])
        self.assertLess(signal_distance, unrelated_distance)

    def test_fixed_reference_keeps_explicit_coordinates(self) -> None:
        self.spec["placement"]["fixed_refs"] = ["J1"]
        entries = [
            ({"ref": "J1", "at": {"x_mm": 5, "y_mm": 5}, "pads": {"1": "VCC"}}, 3.0, 3.0),
            ({"ref": "R1", "pads": {"1": "VCC"}}, 2.0, 2.0),
        ]
        result = pcb_build.placement_engine.optimize_placement(entries, self.spec)
        self.assertEqual(result["J1"], (5.0, 5.0, 0.0))

    def test_fixed_reference_rejects_missing_coordinates(self) -> None:
        self.spec["placement"]["fixed_refs"] = ["J1"]
        entries = [({"ref": "J1", "pads": {}}, 3.0, 3.0)]
        with self.assertRaisesRegex(ValueError, "requires at.x_mm"):
            pcb_build.placement_engine.optimize_placement(entries, self.spec)

    def test_keepout_is_respected(self) -> None:
        self.spec["placement"]["keepouts"] = [
            {"x_mm": 2, "y_mm": 2, "width_mm": 10, "height_mm": 10}
        ]
        entries = [({"ref": "R1", "pads": {}}, 3.0, 3.0)]
        result = pcb_build.placement_engine.optimize_placement(entries, self.spec)
        x, y, _ = result["R1"]
        self.assertFalse(2 <= x <= 12 and 2 <= y <= 12)

    def test_placement_fails_when_board_is_too_small(self) -> None:
        self.spec["board"] = {"width_mm": 5, "height_mm": 5}
        entries = [({"ref": "U1", "pads": {}}, 4.0, 4.0)]
        with self.assertRaisesRegex(RuntimeError, "could not find room"):
            pcb_build.placement_engine.optimize_placement(entries, self.spec)


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


class FreeroutingCommandTests(unittest.TestCase):
    def test_canonical_freerouting_25_flags(self) -> None:
        cmd = pcb_route.freerouting_command(
            "/opt/jre25/bin/java",
            Path("freerouting.jar"),
            Path("board.dsn"),
            Path("board.ses"),
            passes=150,
            threads=4,
            optimize=True,
        )
        self.assertIn("--router.autorouter.max_passes=150", cmd)
        self.assertIn("--router.optimizer.max_threads=4", cmd)
        self.assertIn("--router.optimizer.enabled=true", cmd)
        self.assertIn("--gui.enabled=false", cmd)
        self.assertNotIn("-mp", cmd)


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
