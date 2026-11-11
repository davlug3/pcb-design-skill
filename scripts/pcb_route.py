#!/usr/bin/env python3
"""Route a KiCad board with Freerouting via Specctra DSN/SES.

This stage needs:
  * pcbnew (KiCad 9 Python bindings) for DSN export / SES import
  * Java 25+ and a Freerouting 2.5.0 jar for the autorouter

It is intentionally a thin, auditable bridge. Freerouting is a mature
autorouter, but it is not an impedance or EMC sign-off tool; the pipeline
always follows routing with KiCad DRC and the profile gates.

CLI:
    python3 scripts/pcb_route.py board.kicad_pcb --spec specs/led-blinky.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import design_spec as ds  # noqa: E402
import pcb_build  # noqa: E402
import route_metrics  # noqa: E402
import routing_feedback  # noqa: E402


def require_pcbnew():
    return pcb_build.require_pcbnew()


def export_dsn(board_path: Path, dsn_path: Path) -> None:
    pcbnew = require_pcbnew()
    board = pcbnew.LoadBoard(str(board_path))
    # Route with GND as a normal net. A poured plane exported to Specctra makes
    # Freerouting skip that net, which can leave isolated copper islands. The
    # pours are re-added and filled after the SES import, and the GND vias the
    # router places also stitch the top and bottom pours together.
    for index in range(board.GetAreaCount() - 1, -1, -1):
        board.Remove(board.GetArea(index))
    ok = pcbnew.ExportSpecctraDSN(board, str(dsn_path))
    if ok is False or not dsn_path.is_file():
        raise RuntimeError(f"pcbnew.ExportSpecctraDSN failed for {board_path}")


def inset_dsn_boundary(dsn_path: Path, inset_mm: float) -> None:
    """Pull the Specctra routing boundary inside the board edge.

    Freerouting only sees the board outline, not the KiCad edge-clearance rule,
    so it can route copper flush to the edge and fail DRC. Insetting the
    boundary makes the router keep its tracks clear of the edge. The clamp is
    exact for the rectangular outlines this pipeline generates; the pours are
    re-added from the spec after import, so they still reach the real edge.
    """
    text = dsn_path.read_text(encoding="utf-8")
    match = re.search(r"\(boundary\s*\(path\s+pcb\s+\d+\s+([^)]+)\)\s*\)", text)
    if not match:
        return
    values = [float(value) for value in match.group(1).split()]
    xs = values[0::2]
    ys = values[1::2]
    if not xs or not ys:
        return
    inset = float(inset_mm) * 1000.0  # DSN coordinates are micrometres
    x_min, x_max = min(xs) + inset, max(xs) - inset
    y_min, y_max = min(ys) + inset, max(ys) - inset
    points = []
    for x, y in zip(xs, ys):
        points.append(f"{min(max(x, x_min), x_max):g} {min(max(y, y_min), y_max):g}")
    text = text[:match.start(1)] + "  ".join(points) + text[match.end(1):]
    dsn_path.write_text(text, encoding="utf-8")


def import_ses(board_path: Path, ses_path: Path) -> None:
    pcbnew = require_pcbnew()
    board = pcbnew.LoadBoard(str(board_path))
    imported = None
    try:
        imported = pcbnew.ImportSpecctraSES(board, str(ses_path))
    except TypeError:
        # Older/newer SWIG signatures differ. The one-argument form imports
        # into an internally loaded board; the two-argument form is the one
        # that returns a usable board in current KiCad.
        imported = pcbnew.ImportSpecctraSES(str(ses_path))
    if imported is False:
        raise RuntimeError(f"pcbnew.ImportSpecctraSES failed for {ses_path}")
    # Some builds return a new board object; save that one if it is a BOARD.
    target = imported if imported is not None and hasattr(imported, "Save") else board
    target.BuildConnectivity()
    pcbnew.SaveBoard(str(board_path), target)


def unconnected_count(board_path: Path) -> int:
    pcbnew = require_pcbnew()
    board = pcbnew.LoadBoard(str(board_path))
    board.BuildConnectivity()
    connectivity = board.GetConnectivity()
    try:
        return int(connectivity.GetUnconnectedCount(False))
    except Exception:
        try:
            return int(connectivity.GetUnconnectedCount())
        except Exception:
            return -1


def track_count(board_path: Path) -> int:
    pcbnew = require_pcbnew()
    board = pcbnew.LoadBoard(str(board_path))
    try:
        return int(board.GetTracks().size())
    except Exception:
        try:
            return len(list(board.GetTracks()))
        except Exception:
            return 0


def clamp_ses_widths(ses_path: Path, min_width_mm: float) -> int:
    """Raise any routed track narrower than the board minimum to that minimum.

    Freerouting can emit neckdowns below the netclass minimum (observed
    0.1874 mm against a 0.2 mm board minimum), even while reporting a clean
    run. The DRC gate rejects those, so clamp before import. The clamp widens
    by the neckdown amount only; a genuine clearance conflict still fails the
    DRC gate.
    """
    text = ses_path.read_text(encoding="utf-8")
    match = re.search(r"\(resolution\s+um\s+(\d+)\)", text)
    units_per_mm = int(match.group(1)) * 1000 if match else 10000
    minimum = int(round(float(min_width_mm) * units_per_mm))
    changed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        width = int(match.group(2))
        if width < minimum:
            changed += 1
            return f"{match.group(1)}{minimum}{match.group(3)}"
        return match.group(0)

    text = re.sub(r"(\(path\s+[^\s()]+\s+)(\d+)(\s)", replace, text)
    if changed:
        ses_path.write_text(text, encoding="utf-8")
    return changed


def fill_zones(board_path: Path) -> None:
    pcbnew = require_pcbnew()
    board = pcbnew.LoadBoard(str(board_path))
    zones = board.Zones()
    if zones is None or len(zones) == 0:
        return
    pcbnew.ZONE_FILLER(board).Fill(zones)
    pcbnew.SaveBoard(str(board_path), board)


def find_java() -> str:
    explicit = os.environ.get("JAVA")
    if explicit:
        if shutil.which(explicit) or Path(explicit).is_file():
            return explicit
        raise RuntimeError(f"JAVA={explicit} is not executable")
    java = shutil.which("java")
    if java:
        return java
    raise RuntimeError(
        "Java not found. Install Java 25+ (Termux: pkg install openjdk-25; "
        "Debian: install a current JDK) or run routing in CI where Java is available."
    )


def freerouting_command(
    java: str,
    jar: Path,
    dsn: Path,
    ses: Path,
    *,
    passes: int,
    threads: int,
    optimize: bool,
) -> list[str]:
    """Build a pinned Freerouting 2.5 command using canonical setting names."""
    return [
        java,
        "-jar",
        str(jar),
        "-de",
        str(dsn),
        "-do",
        str(ses),
        f"--router.autorouter.max_passes={passes}",
        f"--router.optimizer.max_threads={threads}",
        f"--router.optimizer.enabled={'true' if optimize else 'false'}",
        "--gui.enabled=false",
    ]


def run_freerouting(
    jar: Path,
    dsn: Path,
    ses: Path,
    *,
    passes: int,
    threads: int,
    timeout_s: int,
    optimize: bool,
    log_path: Path,
) -> dict[str, Any]:
    if not jar.is_file():
        raise RuntimeError(f"Freerouting jar not found: {jar}")
    java = find_java()
    cmd = freerouting_command(
        java,
        jar,
        dsn,
        ses,
        passes=passes,
        threads=threads,
        optimize=optimize,
    )
    started = time.time()
    with open(log_path, "w", encoding="utf-8") as log:
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        try:
            proc = subprocess.run(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
                check=False,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = -9
            log.write(f"\nTIMEOUT after {timeout_s}s\n")
    elapsed = time.time() - started
    if not ses.is_file() or ses.stat().st_size == 0:
        raise RuntimeError(
            f"Freerouting produced no SES file (exit {code}, {elapsed:.1f}s). See {log_path}"
        )
    return {
        "exit_code": code,
        "elapsed_s": round(elapsed, 1),
        "jar": str(jar),
        "log": str(log_path),
        "ses_bytes": ses.stat().st_size,
    }


def route(
    spec: dict[str, Any],
    board_path: Path,
    output_dir: Path,
    *,
    freerouting_jar: Path,
    attempts: int | None = None,
    allow_feedback: bool = False,
) -> dict[str, Any]:
    routing = spec.get("routing", {})
    if routing.get("mode", "freerouting") == "none":
        return {"skipped": True, "reason": "routing.mode is none"}
    attempts = attempts or int(routing.get("attempts", 3))
    passes = int(routing.get("max_passes", 100))
    threads = int(routing.get("threads", 4))
    timeout_s = int(routing.get("timeout_s", 900))
    optimize = bool(routing.get("optimize", True))
    output_dir.mkdir(parents=True, exist_ok=True)
    # Ensure the source project carries the spec netclasses, then copy it into
    # each attempt directory so DSN export sees them.
    try:
        pcb_build.write_project(spec, board_path.parent)
    except Exception:
        pass
    project_src = board_path.parent / f"{board_path.stem}.kicad_pro"

    best_path: Path | None = None
    best_score = (10**9, 10**9)
    results: list[dict[str, Any]] = []
    for attempt in range(1, attempts + 1):
        attempt_dir = output_dir / f"route-attempt-{attempt}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        candidate = attempt_dir / board_path.name
        shutil.copy2(board_path, candidate)
        if project_src.is_file():
            shutil.copy2(project_src, attempt_dir / project_src.name)
        dsn = attempt_dir / f"{spec['name']}.dsn"
        ses = attempt_dir / f"{spec['name']}.ses"
        log = attempt_dir / f"freerouting-{attempt}.log"
        export_dsn(candidate, dsn)
        inset_dsn_boundary(
            dsn, float(spec.get("rules", {}).get("edge_clearance_mm", 0.5)) + 0.1
        )
        info = run_freerouting(
            freerouting_jar,
            dsn,
            ses,
            passes=passes,
            threads=threads,
            timeout_s=timeout_s,
            optimize=optimize,
            log_path=log,
        )
        min_width = float(spec.get("rules", {}).get("min_track_width_mm", 0.2))
        info["width_clamps"] = clamp_ses_widths(ses, min_width)
        import_ses(candidate, ses)
        fill_zones(candidate)
        unconnected = unconnected_count(candidate)
        tracks = track_count(candidate)
        info.update({"attempt": attempt, "unconnected": unconnected, "tracks": tracks, "board": str(candidate)})
        results.append(info)
        score = (unconnected if unconnected >= 0 else 10**8, tracks)
        if best_path is None or score < best_score:
            best_path = candidate
            best_score = score
        if unconnected == 0:
            break

    if best_path is None:
        raise RuntimeError("routing did not produce a candidate board")
    shutil.copy2(best_path, board_path)
    fill_zones(board_path)
    # pcbnew rewrites generator metadata and the project on every save. Stamp
    # the final board and restore the spec rules before DRC reads them.
    try:
        pcb_build.stamp_generator(board_path, "2.0")
    except OSError:
        pass
    try:
        pcb_build.write_project(spec, output_dir)
    except Exception:
        pass
    metrics = route_metrics.collect_board_metrics(require_pcbnew().LoadBoard(str(board_path)))
    selected_attempt = best_path.parent.name
    feedback_report: dict[str, Any] = {"enabled": False, "rounds": []}
    feedback_rounds = int(routing.get("feedback_rounds", 0))
    if allow_feedback and feedback_rounds and spec.get("placement", {}).get("mode") == "auto":
        feedback_report["enabled"] = True
        top_nets = int(routing.get("feedback_top_nets", 3))
        feedback_weight = float(routing.get("feedback_weight", 1.0))
        for round_index in range(1, feedback_rounds + 1):
            feedback_spec, rules = routing_feedback.build_feedback_spec(
                spec, metrics, top_nets=top_nets, weight=feedback_weight
            )
            critical_nets = sorted(
                (str(net) for net, length in (metrics.get("track_length_by_net_mm") or {}).items()
                 if float(length) > 0),
                key=lambda net: (-float(metrics["track_length_by_net_mm"][net]), net),
            )[:top_nets]
            if not rules or not critical_nets:
                feedback_report["rounds"].append({
                    "round": round_index, "accepted": False,
                    "reason": "no eligible routed multi-component nets",
                })
                break
            try:
                feedback_spec["routing"]["feedback_rounds"] = 0
                candidate_dir = output_dir / f"feedback-round-{round_index}"
                candidate_board = pcb_build.build(feedback_spec, candidate_dir)
                candidate_result = route(
                    feedback_spec, candidate_board, candidate_dir,
                    freerouting_jar=freerouting_jar, attempts=attempts,
                    allow_feedback=False,
                )
            except Exception as exc:
                feedback_report["rounds"].append({
                    "round": round_index, "accepted": False,
                    "reason": f"candidate build/route failed: {exc}",
                })
                break
            candidate_metrics = candidate_result["metrics"]
            before = routing_feedback.quality_key(metrics, critical_nets)
            after = routing_feedback.quality_key(candidate_metrics, critical_nets)
            accepted = after < before
            feedback_report["rounds"].append({
                "round": round_index,
                "critical_nets": critical_nets,
                "generated_objectives": rules,
                "before": {"unconnected_items": metrics.get("unconnected_items"), "critical_net_length_mm": before[1], "via_count": metrics.get("via_count")},
                "after": {"unconnected_items": candidate_metrics.get("unconnected_items"), "critical_net_length_mm": after[1], "via_count": candidate_metrics.get("via_count")},
                "accepted": accepted,
                "candidate_board": candidate_result.get("board"),
            })
            if not accepted:
                break
            shutil.copy2(candidate_board, board_path)
            metrics = candidate_metrics
            selected_attempt = candidate_result.get("selected_attempt", selected_attempt)
            results.extend(candidate_result.get("attempts", []))
            best_score = (
                int(metrics.get("unconnected_items", best_score[0])),
                int(metrics.get("track_segment_count", best_score[1])),
            )
    metrics_path = output_dir / "route-metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    feedback_path = output_dir / "route-feedback.json"
    feedback_path.write_text(json.dumps(feedback_report, indent=2) + "\n", encoding="utf-8")
    return {
        "attempts": results,
        "selected_attempt": selected_attempt,
        "unconnected": metrics.get("unconnected_items", best_score[0]),
        "tracks": metrics.get("track_segment_count", best_score[1]),
        "board": str(board_path),
        "metrics": metrics,
        "metrics_report": str(metrics_path),
        "feedback": feedback_report,
        "feedback_report": str(feedback_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route a KiCad board with Freerouting.")
    parser.add_argument("board")
    parser.add_argument("--spec", required=True)
    parser.add_argument("-o", "--output", help="routing work directory (default: board directory)")
    parser.add_argument("--jar", default=os.environ.get("FREEROUTING_JAR", ""))
    parser.add_argument("--attempts", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    spec = ds.apply_defaults(ds.derive_nets(ds.load_json(args.spec)))
    board_path = Path(args.board)
    output_dir = Path(args.output) if args.output else board_path.parent
    jar_value = args.jar or str(Path(__file__).resolve().parent / "freerouting.jar")
    jar = Path(jar_value)
    if not jar.is_file():
        print(
            f"ERROR: Freerouting jar not found at {jar}. Download it with:\n"
            f"  curl -fL -o {jar} "
            "https://github.com/freerouting/freerouting/releases/download/v2.5.0/freerouting-2.5.0.jar",
            file=sys.stderr,
        )
        return 1
    try:
        result = route(spec, board_path, output_dir, freerouting_jar=jar, attempts=args.attempts)
    except Exception as exc:
        print(f"ROUTE FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Routed {board_path}: {result.get('tracks')} tracks, {result.get('unconnected')} unconnected")
    return 0 if result.get("unconnected") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
