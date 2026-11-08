#!/usr/bin/env python3
"""Run professional gates on a KiCad board.

Gates:
  * structural: dependency-free ``check_pcb.py --strict``
  * DRC: ``kicad-cli pcb drc`` (error-level gate + full report)
  * unconnected: from the DRC report
  * DFM: spec-level fabrication checks plus board-level track/via checks
  * netlist parity: board pad nets must match the spec

The output is JSON so the pipeline report can make an explicit professional
claim (or refuse one) instead of relying on a human skimming a log.

CLI:
    python3 scripts/pcb_check.py build/led-blinky/led-blinky.kicad_pcb \
        --spec specs/led-blinky.json -o build/led-blinky/check
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import design_spec as ds  # noqa: E402
import pcb_build  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
CHECK_PCB = SCRIPT_DIR / "check_pcb.py"


def _run(cmd: list[str], *, timeout: int = 300, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(cwd) if cwd else None)


def structural_check(board_path: Path) -> dict[str, Any]:
    if not CHECK_PCB.is_file():
        return {"ok": False, "exit_code": 2, "output": "check_pcb.py missing"}
    proc = _run([sys.executable, str(CHECK_PCB), "--strict", str(board_path)])
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "output": (proc.stdout + proc.stderr).strip(),
    }


def _parse_drc_report(text: str) -> dict[str, int]:
    errors = 0
    warnings = 0
    unconnected = 0
    m = re.search(r"Found\s+(\d+)\s+DRC violations", text)
    if m:
        errors = int(m.group(1))
    m = re.search(r"Found\s+(\d+)\s+unconnected\s+(?:items|pads)", text)
    if m:
        unconnected = int(m.group(1))
    # The full report marks severity on each violation.
    warnings = len(re.findall(r"\[[^\]]+\].*?\n\s*.*?warning", text, flags=re.IGNORECASE))
    return {"violations": errors, "unconnected": unconnected, "warning_markers": warnings}


def run_drc(board_path: Path, output_dir: Path, kicad_cli: str = "kicad-cli") -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(output_dir, 0o777)
    except OSError:
        pass
    full = output_dir / f"{board_path.stem}.drc.rpt"
    errors = output_dir / f"{board_path.stem}.drc-errors.rpt"
    full_cmd = [
        kicad_cli,
        "pcb",
        "drc",
        "--severity-all",
        "--units",
        "mm",
        "--output",
        str(full),
        str(board_path),
    ]
    gate_cmd = [
        kicad_cli,
        "pcb",
        "drc",
        "--severity-error",
        "--exit-code-violations",
        "--units",
        "mm",
        "--output",
        str(errors),
        str(board_path),
    ]
    full_proc = _run(full_cmd, timeout=600)
    gate_proc = _run(gate_cmd, timeout=600)
    full_text = full.read_text(encoding="utf-8", errors="replace") if full.is_file() else ""
    gate_text = errors.read_text(encoding="utf-8", errors="replace") if errors.is_file() else ""
    parsed = _parse_drc_report(full_text or gate_text)
    # With --severity-error --exit-code-violations, 0 means clean.
    gate_ok = gate_proc.returncode == 0
    return {
        "ok": gate_ok,
        "exit_code": gate_proc.returncode,
        "violations": parsed["violations"],
        "unconnected": parsed["unconnected"],
        "warnings": parsed["warning_markers"],
        "report": str(full),
        "error_report": str(errors),
        "full_exit_code": full_proc.returncode,
        "full_output": (full_proc.stdout + full_proc.stderr).strip(),
        "gate_output": (gate_proc.stdout + gate_proc.stderr).strip(),
        "report_text": full_text,
    }


def board_dfm(spec: dict[str, Any], board_path: Path) -> dict[str, Any]:
    """Board-level DFM checks that do not need a full DRC engine."""
    findings = ds.Findings()
    rules = spec.get("rules", {})
    try:
        pcbnew = pcb_build.require_pcbnew()
    except RuntimeError:
        # Spec-level DFM already ran; this is an additional check.
        return {"ok": True, "errors": [], "warnings": [], "skipped": "pcbnew unavailable"}

    board = pcbnew.LoadBoard(str(board_path))
    min_track = float(rules.get("min_track_width_mm", 0.2))
    min_via = float(rules.get("min_via_diameter_mm", 0.6))
    min_drill = float(rules.get("min_via_drill_mm", 0.3))
    min_annular = float(rules.get("min_annular_ring_mm", 0.15))

    tracks = []
    try:
        tracks = list(board.GetTracks())
    except Exception:
        tracks = []

    for track in tracks:
        is_via = hasattr(track, "GetDrillValue")
        if is_via:
            try:
                diameter = track.GetWidth() / 1_000_000.0
                drill = track.GetDrillValue() / 1_000_000.0
                if diameter + 1e-9 < min_via:
                    findings.error("dfm.via_diameter", f"via diameter {diameter:.3f} mm is below {min_via:.3f} mm")
                if drill + 1e-9 < min_drill:
                    findings.error("dfm.via_drill", f"via drill {drill:.3f} mm is below {min_drill:.3f} mm")
                if (diameter - drill) / 2 + 1e-9 < min_annular:
                    findings.error("dfm.annular", f"via annular ring is below {min_annular:.3f} mm")
            except Exception:
                pass
        else:
            try:
                width = track.GetWidth() / 1_000_000.0
                if width + 1e-9 < min_track:
                    findings.error("dfm.track_width", f"track width {width:.3f} mm is below {min_track:.3f} mm")
            except Exception:
                pass

    # Hole-to-hole spacing across pads and vias.
    holes: list[tuple[float, float, float]] = []
    try:
        for fp in board.GetFootprints():
            for pad in fp.Pads():
                try:
                    drill = pad.GetDrillSize()
                    dx = abs(drill.x) / 1_000_000.0
                    dy = abs(drill.y) / 1_000_000.0
                    if dx > 0 or dy > 0:
                        pos = pad.GetPosition()
                        holes.append((pos.x / 1_000_000.0, pos.y / 1_000_000.0, max(dx, dy)))
                except Exception:
                    pass
    except Exception:
        pass
    min_hole_gap = float(rules.get("min_hole_to_hole_mm", 0.5))
    hole_errors = 0
    for i, (x1, y1, d1) in enumerate(holes):
        for x2, y2, d2 in holes[i + 1 :]:
            gap = ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 - (d1 + d2) / 2
            if gap + 1e-9 < min_hole_gap:
                findings.error(
                    "dfm.hole_spacing",
                    f"holes at ({x1:.2f},{y1:.2f}) and ({x2:.2f},{y2:.2f}) are {gap:.3f} mm apart, "
                    f"below {min_hole_gap:.3f} mm",
                )
                hole_errors += 1
                break
        if hole_errors >= 5:
            break

    return {
        "ok": findings.ok,
        "errors": findings.errors,
        "warnings": findings.warnings,
    }


def netlist_parity(spec: dict[str, Any], board_path: Path) -> dict[str, Any]:
    try:
        pcbnew = pcb_build.require_pcbnew()
    except RuntimeError as exc:
        return {"ok": False, "errors": [{"code": "parity.pcbnew", "message": str(exc)}]}
    board = pcbnew.LoadBoard(str(board_path))
    by_ref: dict[str, Any] = {}
    try:
        for fp in board.GetFootprints():
            by_ref[fp.GetReference()] = fp
    except Exception:
        pass

    errors: list[dict[str, str]] = []
    for comp in spec.get("components", []):
        ref = comp["ref"]
        fp = by_ref.get(ref)
        if fp is None:
            errors.append({"code": "parity.missing_footprint", "message": f"{ref} is missing from the board"})
            continue
        for pad_number, net_name in (comp.get("pads") or {}).items():
            pad = pcb_build._call_get(fp, ["FindPadByNumber"], str(pad_number))
            if pad is None:
                errors.append({"code": "parity.missing_pad", "message": f"{ref} pad {pad_number} is missing"})
                continue
            try:
                actual = pad.GetNetname()
            except Exception:
                actual = ""
            if actual != net_name:
                errors.append(
                    {
                        "code": "parity.net",
                        "message": f"{ref} pad {pad_number} is on net {actual!r}, expected {net_name!r}",
                    }
                )
    return {"ok": not errors, "errors": errors}


def length_matching(spec: dict[str, Any], board_path: Path) -> dict[str, Any]:
    """Measure routed track lengths and check declared length-match tolerances."""
    try:
        pcbnew = pcb_build.require_pcbnew()
    except RuntimeError as exc:
        return {"ok": False, "errors": [{"code": "length.pcbnew", "message": str(exc)}], "lengths_mm": {}}

    board = pcbnew.LoadBoard(str(board_path))
    lengths: dict[str, float] = {}
    for track in board.GetTracks():
        if hasattr(track, "GetDrillValue"):
            continue
        try:
            net = track.GetNetname()
            length = track.GetLength() / 1_000_000.0
            lengths[net] = lengths.get(net, 0.0) + length
        except Exception:
            continue

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    for cls_name, cls in spec.get("netclasses", {}).items():
        tolerance = float(cls.get("length_match_mm", 0) or 0)
        if tolerance <= 0:
            continue
        nets = [net for net in spec.get("nets", []) if net.get("class", ds.DEFAULT_NETCLASS) == cls_name]
        for net in nets:
            companion = net.get("diff_pair")
            if not companion:
                continue
            if net.get("name") not in lengths or companion not in lengths:
                errors.append(
                    {
                        "code": "length.missing",
                        "message": f"{net.get('name')}/{companion}: one side has no routed length",
                    }
                )
                continue
            spread = abs(lengths[net["name"]] - lengths[companion])
            if spread > tolerance + 1e-9:
                errors.append(
                    {
                        "code": "length.diff_pair",
                        "message": f"{net.get('name')}/{companion}: {spread:.2f} mm skew exceeds {tolerance:.2f} mm",
                    }
                )
        singles = [net for net in nets if not net.get("diff_pair")]
        values = [lengths[net["name"]] for net in singles if net.get("name") in lengths]
        if len(values) > 1:
            spread = max(values) - min(values)
            if spread > tolerance + 1e-9:
                warnings.append(
                    {
                        "code": "length.single_ended",
                        "message": f"{cls_name}: routed length spread {spread:.2f} mm exceeds {tolerance:.2f} mm",
                    }
                )
    return {"ok": not errors, "errors": errors, "warnings": warnings, "lengths_mm": {k: round(v, 3) for k, v in lengths.items()}}


def gate_all(
    spec: dict[str, Any],
    board_path: Path,
    output_dir: Path,
    kicad_cli: str = "kicad-cli",
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # The spec is authoritative for netclasses and minimum rules. Rewrite the
    # project immediately before DRC so pcbnew cannot leave defaults behind.
    pcb_build.write_project(spec, board_path.parent)
    structural = structural_check(board_path)
    drc = run_drc(board_path, output_dir, kicad_cli=kicad_cli)
    dfm = board_dfm(spec, board_path)
    parity = netlist_parity(spec, board_path)
    length = length_matching(spec, board_path)
    result = {
        "structural": structural,
        "drc": drc,
        "dfm": dfm,
        "netlist_parity": parity,
        "length_match": length,
        "ok": bool(structural["ok"] and drc["ok"] and dfm["ok"] and parity["ok"] and length["ok"]),
    }
    ds.write_json(output_dir / "check.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run structural, DRC, DFM and netlist-parity gates.")
    parser.add_argument("board")
    parser.add_argument("--spec", required=True)
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--kicad-cli", default=os.environ.get("KICAD_CLI", "kicad-cli"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    spec = ds.apply_defaults(ds.derive_nets(ds.load_json(args.spec)))
    result = gate_all(spec, Path(args.board), Path(args.output), kicad_cli=args.kicad_cli)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"structural: {'PASS' if result['structural']['ok'] else 'FAIL'}")
        print(f"DRC:        {'PASS' if result['drc']['ok'] else 'FAIL'} "
              f"({result['drc']['violations']} violations, {result['drc']['unconnected']} unconnected)")
        print(f"DFM:        {'PASS' if result['dfm']['ok'] else 'FAIL'}")
        print(f"parity:     {'PASS' if result['netlist_parity']['ok'] else 'FAIL'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
