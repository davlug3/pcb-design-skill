#!/usr/bin/env python3
"""Tests for environment probing and backend selection."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ci_dispatch  # noqa: E402
import design_pipeline  # noqa: E402
import env_probe  # noqa: E402


def make_probe(
    *,
    pcbnew: bool = False,
    kicad_cli: bool = False,
    java: bool = False,
    freerouting: bool = False,
    ci: bool = False,
) -> dict:
    tools = {
        "pcbnew": {"ok": pcbnew, "supported": pcbnew},
        "kicad-cli": {"ok": kicad_cli, "supported": kicad_cli},
        "java": {"ok": java, "supported": java},
        "freerouting": {"ok": freerouting, "supported": freerouting},
    }
    stages = {name: env_probe.stage_status(tools, name) for name in env_probe.STAGE_REQUIREMENTS}
    return {
        "stages": stages,
        "ci": {"usable": ci},
        "missing": env_probe.missing_for_stages({"stages": stages}),
    }


def pipeline_args(**overrides) -> argparse.Namespace:
    base = {
        "spec": str(ROOT / "specs" / "led-blinky.json"),
        "netlist": None,
        "schematic": None,
        "board": None,
        "output": None,
        "kicad_cli": "kicad-cli",
        "footprints_dir": None,
        "freerouting_jar": "",
        "skip_build": False,
        "skip_route": False,
        "skip_check": False,
        "skip_fab": False,
        "allow_unverified": False,
        "backend": "auto",
        "ci_repo": None,
        "ci_workflow": "design.yml",
        "ci_ref": None,
        "ci_watch": False,
        "ci_download": None,
        "ci_timeout": 3600,
        "json": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class VersionParsingTests(unittest.TestCase):
    def test_java_versions(self) -> None:
        self.assertEqual(env_probe.parse_java_major('openjdk version "25" 2025-09-16'), 25)
        self.assertEqual(env_probe.parse_java_major('openjdk version "25.0.1" 2025-10-21'), 25)
        self.assertEqual(env_probe.parse_java_major('openjdk version "17.0.10" 2024-01-16'), 17)
        self.assertEqual(env_probe.parse_java_major('java version "1.8.0_402"'), 8)
        self.assertIsNone(env_probe.parse_java_major("not java"))

    def test_kicad_versions(self) -> None:
        self.assertEqual(env_probe.parse_kicad_major("9.0.2"), 9)
        self.assertEqual(env_probe.parse_kicad_major("(9, 0, 2)"), 9)
        self.assertEqual(env_probe.parse_kicad_major("8.0.7"), 8)
        self.assertIsNone(env_probe.parse_kicad_major(""))


class StageTests(unittest.TestCase):
    def test_build_needs_only_pcbnew(self) -> None:
        probe = make_probe(pcbnew=True)
        self.assertTrue(probe["stages"]["build"]["ok"])
        self.assertFalse(probe["stages"]["route"]["ok"])

    def test_route_needs_java_and_jar(self) -> None:
        probe = make_probe(pcbnew=True, kicad_cli=True)
        self.assertEqual(probe["stages"]["route"]["missing"], ["java", "freerouting"])

    def test_check_needs_pcbnew_and_kicad_cli(self) -> None:
        probe = make_probe(pcbnew=True)
        self.assertEqual(probe["stages"]["check"]["missing"], ["kicad-cli"])

    def test_missing_tools_are_deduplicated(self) -> None:
        probe = make_probe()
        missing = env_probe.missing_for_stages(probe, ["build", "route", "check", "fab"])
        self.assertEqual(missing, ["pcbnew", "java", "freerouting", "kicad-cli"])

    def test_recommended_backend(self) -> None:
        self.assertEqual(env_probe.recommended_backend(make_probe(pcbnew=True, kicad_cli=True, java=True, freerouting=True)), "local")
        self.assertEqual(env_probe.recommended_backend(make_probe(ci=True)), "ci")
        self.assertEqual(env_probe.recommended_backend(make_probe()), "none")


class BackendSelectionTests(unittest.TestCase):
    def test_auto_prefers_local_when_ready(self) -> None:
        probe = make_probe(pcbnew=True, kicad_cli=True, java=True, freerouting=True, ci=True)
        with mock.patch.object(design_pipeline.env_probe, "probe", return_value=probe):
            backend, _ = design_pipeline.resolve_backend(pipeline_args(), ["build", "route", "check", "fab"])
        self.assertEqual(backend, "local")

    def test_auto_falls_back_to_ci(self) -> None:
        probe = make_probe(ci=True)
        with mock.patch.object(design_pipeline.env_probe, "probe", return_value=probe):
            backend, _ = design_pipeline.resolve_backend(pipeline_args(), ["build", "route", "check", "fab"])
        self.assertEqual(backend, "ci")

    def test_auto_without_ci_stays_local(self) -> None:
        probe = make_probe()
        with mock.patch.object(design_pipeline.env_probe, "probe", return_value=probe):
            backend, _ = design_pipeline.resolve_backend(pipeline_args(), ["build"])
        self.assertEqual(backend, "local")

    def test_explicit_backend_wins(self) -> None:
        probe = make_probe(pcbnew=True, kicad_cli=True, java=True, freerouting=True)
        with mock.patch.object(design_pipeline.env_probe, "probe", return_value=probe):
            backend, _ = design_pipeline.resolve_backend(pipeline_args(backend="ci"), ["build"])
        self.assertEqual(backend, "ci")

    def test_github_actions_never_redispatches(self) -> None:
        probe = make_probe(ci=True)
        with mock.patch.object(design_pipeline.env_probe, "probe", return_value=probe), mock.patch.dict(
            os.environ, {"GITHUB_ACTIONS": "true"}
        ):
            backend, _ = design_pipeline.resolve_backend(pipeline_args(), ["build"])
        self.assertEqual(backend, "local")

    def test_requested_stages_respect_skips_and_board(self) -> None:
        self.assertEqual(
            design_pipeline.requested_stages(pipeline_args()),
            ["build", "route", "check", "fab"],
        )
        self.assertEqual(
            design_pipeline.requested_stages(pipeline_args(skip_route=True, skip_fab=True)),
            ["build", "check"],
        )
        self.assertEqual(
            design_pipeline.requested_stages(pipeline_args(board="existing.kicad_pcb")),
            ["route", "check", "fab"],
        )


class DispatchTests(unittest.TestCase):
    def test_workflow_inputs_skip_empty_and_encode_booleans(self) -> None:
        args = ci_dispatch.workflow_inputs(
            {"spec": "specs/board.json", "skip_build": True, "schematic": "", "netlist": None}
        )
        self.assertEqual(
            args,
            ["-f", "spec=specs/board.json", "-F", "skip_build=true"],
        )

    def test_repo_relative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "specs").mkdir()
            (root / "specs" / "board.json").write_text("{}", encoding="utf-8")
            self.assertEqual(env_probe.repo_relative(root, "specs/board.json", root), "specs/board.json")
            self.assertIsNone(env_probe.repo_relative(root, "/etc/passwd", root))


class CiBlockerTests(unittest.TestCase):
    """CI must not run a revision that differs from the local tree."""

    def setUp(self) -> None:
        import shutil
        import subprocess

        if shutil.which("git") is None:
            self.skipTest("git not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.git = ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test"]
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        (self.root / "specs").mkdir()
        (self.root / "specs" / "board.json").write_text("{}", encoding="utf-8")
        subprocess.run([*self.git, "add", "."], cwd=self.root, check=True)
        subprocess.run([*self.git, "commit", "-qm", "init"], cwd=self.root, check=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_workflow_and_upstream_block(self) -> None:
        blockers = "\n".join(env_probe.ci_blockers(self.root, "specs/board.json", cwd=self.root))
        self.assertIn("workflow missing", blockers)
        self.assertIn("no upstream", blockers)

    def test_dirty_spec_blocks(self) -> None:
        workflow = self.root / ".github" / "workflows" / "design.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: test\n", encoding="utf-8")
        blockers = "\n".join(env_probe.ci_blockers(self.root, "specs/board.json", cwd=self.root))
        self.assertIn("uncommitted or untracked", blockers)

    def test_extra_input_is_checked(self) -> None:
        blockers = "\n".join(
            env_probe.ci_blockers(
                self.root,
                "specs/board.json",
                cwd=self.root,
                paths=["design.kicad_sch"],
            )
        )
        self.assertIn("input not found: design.kicad_sch", blockers)


if __name__ == "__main__":
    unittest.main()
