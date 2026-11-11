#!/usr/bin/env python3
"""Tests for fail-closed PCB DFM inspection."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import pcb_check  # noqa: E402


class BrokenTrack:
    def GetWidth(self):
        raise RuntimeError("pcbnew width accessor failed")


class FakeBoard:
    def GetTracks(self):
        return [BrokenTrack()]

    def GetFootprints(self):
        return []


class FakePcbnew:
    @staticmethod
    def LoadBoard(_path):
        return FakeBoard()


class BoardDfmTests(unittest.TestCase):
    def test_drc_parser_counts_clearance_markers(self) -> None:
        report = """Found 3 DRC violations
[clearance]: Clearance violation (clearance 0.20 mm; actual 0.00 mm)
[clearance]: Clearance violation (clearance 0.20 mm; actual 0.10 mm)
[track_width]: Track width violation
"""
        parsed = pcb_check._parse_drc_report(report)
        self.assertEqual(parsed["violations"], 3)
        self.assertEqual(parsed["clearance_violations"], 2)

    def test_uninspectable_track_fails_dfm_instead_of_being_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            board_path = Path(tmp) / "board.kicad_pcb"
            board_path.write_text("(kicad_pcb)", encoding="utf-8")
            with patch.object(pcb_check.pcb_build, "require_pcbnew", return_value=FakePcbnew):
                result = pcb_check.board_dfm({}, board_path)

        self.assertFalse(result["ok"])
        self.assertTrue(any(item["code"] == "dfm.track_inspection" for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
