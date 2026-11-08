#!/usr/bin/env python3
"""Export fabrication data from a validated KiCad board.

Outputs a deterministic package:
  gerbers/              copper, mask, silkscreen, edge cuts
  drill/                Excellon drill files
  <name>-pos.csv        component position file
  <name>.step           board STEP (best effort)
  <name>-ipc356.net     IPC-D-356 netlist (best effort)
  fabrication.zip       all of the above
  fabrication-README.md assumptions and stackup

CLI:
    python3 scripts/pcb_fab.py build/led-blinky/led-blinky.kicad_pcb \
        --spec specs/led-blinky.json -o build/led-blinky/fab
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import design_spec as ds  # noqa: E402
import pcb_build  # noqa: E402


def _run(cmd: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _step(
    name: str,
    cmd: list[str],
    output: Path,
    log: list[dict[str, Any]],
    *,
    required: bool = False,
    check: Callable[[], bool] | None = None,
) -> bool:
    proc = _run(cmd)
    ok = proc.returncode == 0 and output.exists()
    if ok and check is not None:
        ok = check()
    log.append(
        {
            "step": name,
            "ok": ok,
            "required": required,
            "exit_code": proc.returncode,
            "output": (proc.stdout + proc.stderr).strip()[-4000:],
        }
    )
    return ok


def required_fab_outputs(spec: dict[str, Any], override: list[str] | None = None) -> set[str]:
    """Return the fabrication outputs the profile requires.

    The profile is the contract: every listed output is a hard gate. Aliases
    are normalized so KiCad 9's ``ipcd356`` and the older ``ipc356`` name map
    to the same package item.
    """
    if override is not None:
        outputs = list(override)
    else:
        outputs = []
        profile_name = spec.get("profile")
        if profile_name:
            try:
                outputs = list(ds.load_profile(str(profile_name)).get("fab_outputs", []))
            except Exception:
                outputs = []
        if not outputs:
            outputs = ["gerbers", "drill"]
    return {"ipc356" if item in ("ipc356", "ipcd356") else item for item in outputs}


def gerber_layers(spec: dict[str, Any]) -> list[str]:
    """Layers that belong in a fabrication package, not every empty user layer."""
    count = int(spec.get("board", {}).get("layers", 2))
    copper = ["F.Cu"] + [f"In{i}.Cu" for i in range(1, max(1, count - 1))] + ["B.Cu"]
    return copper + [
        "F.Paste",
        "B.Paste",
        "F.SilkS",
        "B.SilkS",
        "F.Mask",
        "B.Mask",
        "Edge.Cuts",
    ]


def _ipc356_step(
    kicad_cli: str,
    board_path: Path,
    output: Path,
    log: list[dict[str, Any]],
    *,
    required: bool,
) -> bool:
    """Export IPC-D-356 across KiCad 9 (ipcd356) and older (ipc356) CLIs."""
    attempts: list[dict[str, Any]] = []
    last_code = 1
    for subcommand in ("ipcd356", "ipc356"):
        cmd = [kicad_cli, "pcb", "export", subcommand, "-o", str(output), str(board_path)]
        proc = _run(cmd)
        last_code = proc.returncode
        attempts.append(
            {
                "command": subcommand,
                "exit_code": proc.returncode,
                "output": (proc.stdout + proc.stderr).strip(),
            }
        )
        if proc.returncode == 0 and output.exists():
            log.append(
                {
                    "step": "ipc356",
                    "ok": True,
                    "required": required,
                    "exit_code": 0,
                    "command": subcommand,
                    "output": attempts[-1]["output"][-4000:],
                }
            )
            return True
    log.append(
        {
            "step": "ipc356",
            "ok": False,
            "required": required,
            "exit_code": last_code,
            "command": "ipcd356/ipc356",
            "output": "\n".join(
                f"{item['command']}: {item['output']}" for item in attempts
            )[-4000:],
        }
    )
    return False


def export_fab(
    spec: dict[str, Any],
    board_path: Path,
    output_dir: Path,
    kicad_cli: str = "kicad-cli",
    required_outputs: list[str] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # kicad-cli reads the project for netclass-aware plotting; restore it first.
    pcb_build.write_project(spec, board_path.parent)
    gerbers = output_dir / "gerbers"
    drill = output_dir / "drill"
    for directory in (gerbers, drill):
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True)
    log: list[dict[str, Any]] = []
    name = spec["name"]
    required = required_fab_outputs(spec, required_outputs)

    pos = output_dir / f"{name}-pos.csv"
    step = output_dir / f"{name}.step"
    ipc = output_dir / f"{name}-ipc356.net"
    for stale in (pos, step, ipc):
        if stale.exists():
            stale.unlink()

    layer_list = ",".join(gerber_layers(spec))
    _step(
        "gerbers",
        [
            kicad_cli,
            "pcb",
            "export",
            "gerbers",
            "-o",
            str(gerbers) + os.sep,
            "--layers",
            layer_list,
            str(board_path),
        ],
        gerbers,
        log,
        required="gerbers" in required,
        check=lambda: any(gerbers.iterdir()),
    )
    _step(
        "drill",
        [
            kicad_cli,
            "pcb",
            "export",
            "drill",
            "-o",
            str(drill) + os.sep,
            str(board_path),
        ],
        drill,
        log,
        required="drill" in required,
        check=lambda: any(drill.iterdir()),
    )
    _step(
        "pos",
        [
            kicad_cli,
            "pcb",
            "export",
            "pos",
            "-o",
            str(pos),
            "--format",
            "csv",
            "--units",
            "mm",
            "--side",
            "both",
            str(board_path),
        ],
        pos,
        log,
        required="pos" in required,
    )
    _step(
        "step",
        [kicad_cli, "pcb", "export", "step", "-o", str(step), str(board_path)],
        step,
        log,
        required="step" in required,
    )
    _ipc356_step(kicad_cli, board_path, ipc, log, required="ipc356" in required)

    readme = output_dir / "fabrication-README.md"
    readme.write_text(fabrication_readme(spec, log), encoding="utf-8")

    gerber_files = [p for p in gerbers.iterdir() if p.is_file()]
    drill_files = [p for p in drill.iterdir() if p.is_file()]
    produced = {
        "gerbers": bool(gerber_files),
        "drill": bool(drill_files),
        "pos": pos.exists(),
        "step": step.exists(),
        "ipc356": ipc.exists(),
    }
    missing = sorted(item for item in required if not produced.get(item, False))
    required_ok = all(item["ok"] for item in log if item["required"])
    archive = output_dir / f"{name}-fabrication.zip"
    result = {
        "output_dir": str(output_dir),
        "archive": str(archive),
        "gerbers": str(gerbers),
        "drill": str(drill),
        "gerber_files": sorted(p.name for p in gerber_files),
        "drill_files": sorted(p.name for p in drill_files),
        "pos": str(pos) if pos.exists() else None,
        "step": str(step) if step.exists() else None,
        "ipc356": str(ipc) if ipc.exists() else None,
        "readme": str(readme),
        "required_outputs": sorted(required),
        "missing_required": missing,
        "steps": log,
        "ok": bool(required_ok and not missing),
    }
    # Include the manifest in the package itself.
    ds.write_json(output_dir / "fab.json", result)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(output_dir.rglob("*")):
            if path == archive or path.is_dir():
                continue
            zf.write(path, path.relative_to(output_dir))
    return result


def fabrication_readme(spec: dict[str, Any], log: list[dict[str, Any]]) -> str:
    board = spec.get("board", {})
    rules = spec.get("rules", {})
    stackup = board.get("stackup")
    lines = [
        f"# {spec.get('name')} rev {spec.get('revision', 'A')} - fabrication package",
        "",
        "Generated by `pcb-design-skill`. This package is only release-ready when the",
        "design report says `professional_claim: true`.",
        "",
        "## Board",
        "",
        f"- Size: {board.get('width_mm')} x {board.get('height_mm')} mm",
        f"- Layers: {board.get('layers')}",
        f"- Thickness: {board.get('thickness_mm')} mm",
        f"- Copper: {board.get('copper_oz')} oz",
        f"- Material: {board.get('material', 'FR4')}",
        f"- Surface finish: {board.get('surface_finish', 'HASL-LF')}",
        f"- Mask / silkscreen: {board.get('mask_color', 'green')} / {board.get('silkscreen_color', 'white')}",
        "",
        "## Rules",
        "",
        f"- Minimum track: {rules.get('min_track_width_mm')} mm",
        f"- Minimum clearance: {rules.get('min_clearance_mm')} mm",
        f"- Minimum via: {rules.get('min_via_diameter_mm')} mm / drill {rules.get('min_via_drill_mm')} mm",
        f"- Minimum annular ring: {rules.get('min_annular_ring_mm')} mm",
        f"- Copper-to-edge: {rules.get('edge_clearance_mm')} mm",
        "",
    ]
    if stackup:
        lines.extend(["## Stackup", "", "| Layer | Type | Thickness (mm) | Material | Er |", "|---|---|---:|---|---:|"])
        for layer in stackup.get("layers", []):
            lines.append(
                f"| {layer.get('name', '')} | {layer.get('type')} | {layer.get('thickness_mm')} | "
                f"{layer.get('material', '')} | {layer.get('er', '')} |"
            )
        lines.extend(
            [
                "",
                f"Stackup source: {stackup.get('source', 'not stated')}",
                f"Fabricator confirmed: {'yes' if stackup.get('confirmed') else 'no - confirm before release'}",
                "",
            ]
        )
    lines.extend(
        [
            "## Export log",
            "",
            "| Step | Required | Status |",
            "|---|---|---|",
        ]
    )
    for item in log:
        lines.append(f"| {item['step']} | {'yes' if item['required'] else 'no'} | {'PASS' if item['ok'] else 'FAIL'} |")
    lines.extend(
        [
            "",
            "## Assumptions and limits",
            "",
        ]
    )
    for assumption in spec.get("assumptions", []):
        lines.append(f"- {assumption}")
    lines.extend(
        [
            "- DRC and DFM gates were run by the pipeline; see the design report for the exact results.",
            "- Gerbers are limited to copper, paste, mask, silkscreen and edge cuts.",
            "- STEP contains board geometry; component 3D models are included only when the KiCad 3D library is installed.",
            "- Impedance, EMC, thermal and safety claims require the sign-offs listed in the profile.",
            "- Refill copper zones in KiCad if you edit the board after export.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Gerbers, drill, position and STEP files.")
    parser.add_argument("board")
    parser.add_argument("--spec", required=True)
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--kicad-cli", default=os.environ.get("KICAD_CLI", "kicad-cli"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    spec = ds.apply_defaults(ds.load_json(args.spec))
    try:
        result = export_fab(spec, Path(args.board), Path(args.output), kicad_cli=args.kicad_cli)
    except Exception as exc:
        print(f"FAB FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Fabrication package: {result['archive']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
