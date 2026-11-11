#!/usr/bin/env python3
"""Professional automatic PCB design pipeline.

One command from a design spec to a validated fabrication package:

    validate -> build -> route (Freerouting) -> DRC/DFM -> export -> report

The pipeline refuses a "professional" claim when a hard gate fails or a
required human sign-off is missing. That is intentional: automatic routing is
not a substitute for RF, EMC, thermal, safety or flex sign-off.

Examples:
    # Automatic backend: local when KiCad/Java/Freerouting are present,
    # otherwise dispatch GitHub Actions (requires gh + a pushed branch)
    python3 scripts/design_pipeline.py all specs/led-blinky.json

    # Force a backend. CI dispatch waits for the run and returns its gate result;
    # pass --no-ci-watch to return immediately after dispatch.
    python3 scripts/design_pipeline.py all specs/led-blinky.json --backend local
    python3 scripts/design_pipeline.py all specs/led-blinky.json --backend ci

    # Validate the intent only, no KiCad required
    python3 scripts/design_pipeline.py validate specs/led-blinky.json

    # Route an existing board, then run the gates
    python3 scripts/design_pipeline.py all specs/led-blinky.json \
        --board existing.kicad_pcb --skip-build

    # Import a KiCad schematic as the electrical source of truth
    python3 scripts/design_pipeline.py all specs/led-blinky.json \
        --schematic design.kicad_sch

    # Import an LTspice/SPICE netlist. Missing pin maps, footprints, placement
    # and node aliases are asked for in the visual picker. Use --no-ask in CI.
    python3 scripts/design_pipeline.py all specs/led-blinky.json \
        --ltspice design.cir --pin-map pinmap.json --node-aliases aliases.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import ci_dispatch  # noqa: E402
import design_spec as ds  # noqa: E402
import env_probe  # noqa: E402
import pcb_build  # noqa: E402
import pcb_check  # noqa: E402
import pcb_fab  # noqa: E402
import pcb_route  # noqa: E402
import spice_flow  # noqa: E402


def resolve_spec(args: argparse.Namespace, output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    overlay = ds.apply_defaults(ds.load_json(args.spec))
    ltspice = getattr(args, "ltspice", None)
    if ltspice:
        pin_map = ds.load_json(args.pin_map) if getattr(args, "pin_map", None) else None
        node_aliases = ds.load_json(args.node_aliases) if getattr(args, "node_aliases", None) else None
        ask = getattr(args, "ask", None)
        ask = True if ask is None else bool(ask)
        host = "0.0.0.0" if getattr(args, "ask_mobile", False) else getattr(args, "ask_host", "127.0.0.1")
        ask_dir = output_dir / overlay["name"] / "ask"
        imported = spice_flow.resolve_ltspice(
            overlay,
            ltspice,
            pin_map=pin_map,
            node_aliases=node_aliases,
            ask=ask,
            ask_dir=ask_dir,
            host=host,
            port=getattr(args, "ask_port", 8765),
            timeout=getattr(args, "ask_timeout", 1800),
            open_browser=getattr(args, "ask_open", False),
            fetch=not getattr(args, "ask_no_fetch", False),
            footprints_dir=Path(args.footprints_dir) if getattr(args, "footprints_dir", None) else None,
        )
        spec = imported.spec
        spec["netlist_source"] = str(ltspice)
        for warning in imported.warnings:
            print(f"WARN  [ltspice] {warning}", file=sys.stderr)
        if imported.asked:
            ask_dir.mkdir(parents=True, exist_ok=True)
            ds.write_json(ask_dir / "pinmap.resolved.json", imported.pin_map)
            ds.write_json(ask_dir / "aliases.resolved.json", imported.node_aliases)
            print(f"[ask] answered: {', '.join(imported.asked)}", file=sys.stderr)
            print(f"[ask] reusable pin map: {ask_dir / 'pinmap.resolved.json'}", file=sys.stderr)
            print(f"[ask] reusable aliases: {ask_dir / 'aliases.resolved.json'}", file=sys.stderr)
    elif args.netlist or args.schematic:
        if args.schematic:
            netlist_path = output_dir / f"{overlay['name']}.net"
            ds.export_netlist_from_schematic(args.schematic, netlist_path, kicad_cli=args.kicad_cli)
        else:
            netlist_path = Path(args.netlist)
        netlist = ds.parse_kicad_netlist(netlist_path)
        spec = ds.merge_netlist(overlay, netlist)
        spec["netlist_source"] = str(netlist_path)
    else:
        spec = overlay
    spec = ds.apply_defaults(ds.derive_nets(spec))
    profile = ds.load_profile(str(spec["profile"]))
    return spec, profile


def requested_stages(args: argparse.Namespace) -> list[str]:
    """Stages this invocation actually needs, based on flags and routing mode."""
    raw = ds.apply_defaults(ds.load_json(args.spec)) if args.spec else {}
    routing_mode = raw.get("routing", {}).get("mode", "freerouting")
    stages: list[str] = []
    if not args.skip_build and not args.board:
        stages.append("build")
    if not args.skip_route and routing_mode != "none":
        stages.append("route")
    if not args.skip_check:
        stages.append("check")
    if not args.skip_fab:
        stages.append("fab")
    return stages


def resolve_backend(args: argparse.Namespace, stages: list[str]) -> tuple[str, dict[str, Any]]:
    """Choose local or CI execution for this environment.

    ``auto`` prefers local when every requested stage has its tools, then CI
    when the repository is pushed and ``gh`` is authenticated. Inside GitHub
    Actions it always stays local so a CI run can never re-dispatch itself.
    """
    probe = env_probe.probe(
        jar=args.freerouting_jar or None,
        kicad_cli=args.kicad_cli,
        spec=args.spec,
        paths=[
            path
            for path in (
                args.schematic,
                args.netlist,
                getattr(args, "ltspice", None),
                getattr(args, "pin_map", None),
                getattr(args, "node_aliases", None),
                args.board,
            )
            if path
        ],
        workflow=args.ci_workflow,
    )
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "local", probe
    if args.backend == "local":
        return "local", probe
    if args.backend == "ci":
        return "ci", probe
    if env_probe.stages_ready(probe, stages):
        return "local", probe
    if probe.get("ci", {}).get("usable"):
        return "ci", probe
    return "local", probe


def announce_backend(backend: str, probe: dict[str, Any], stages: list[str]) -> None:
    missing = env_probe.missing_for_stages(probe, stages)
    if backend == "ci":
        git = probe.get("ci", {}).get("git", {})
        print(
            f"[backend] ci - local pipeline missing: {', '.join(missing) or 'unknown'}; "
            f"dispatching {git.get('detail', 'GitHub Actions')}"
        )
        return
    if missing:
        print(f"[backend] local - missing for requested stages: {', '.join(missing)}")
        ci = probe.get("ci", {})
        if ci.get("usable"):
            print("          use --backend ci to run these stages in GitHub Actions")
        else:
            print("[backend] ci not available:")
            blockers = ci.get("blockers", [])
            if blockers:
                for blocker in blockers:
                    print(f"          - {blocker}")
            else:
                detail = ci.get("gh", {}).get("detail") or "gh and a pushed branch are required"
                print(f"          - {detail}")
    else:
        print("[backend] local - all required tools present")


def backend_extra(backend: str, probe: dict[str, Any], stages: list[str]) -> dict[str, Any]:
    return {
        "selected": backend,
        "environment": "ci" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
        "missing": env_probe.missing_for_stages(probe, stages),
        "local_ready": env_probe.stages_ready(probe, stages),
        "ci_usable": probe.get("ci", {}).get("usable", False),
    }


def dispatch_ci(args: argparse.Namespace) -> int:
    if args.footprints_dir:
        print("NOTE: --footprints-dir is local-only; CI uses the container's KiCad libraries", file=sys.stderr)
    if args.output:
        print("NOTE: -o is local-only; CI writes build/<spec-name>/ and artifacts are downloaded separately", file=sys.stderr)
    if args.freerouting_jar:
        print("NOTE: --freerouting-jar is local-only; CI downloads the pinned Freerouting jar", file=sys.stderr)
    if args.kicad_cli and args.kicad_cli != "kicad-cli":
        print("NOTE: --kicad-cli is local-only; CI uses the container's kicad-cli", file=sys.stderr)

    inputs: dict[str, Any] = {}
    for key in ("schematic", "netlist", "ltspice", "pin_map", "node_aliases", "board"):
        value = getattr(args, key, None)
        if value:
            inputs[key] = value
    for key in ("skip_build", "skip_route", "skip_check", "skip_fab", "allow_unverified"):
        if getattr(args, key):
            inputs[key] = True

    if args.ci_download and not args.ci_watch:
        print("--ci-download requires waiting for the CI run; remove --no-ci-watch", file=sys.stderr)
        return 2

    try:
        result = ci_dispatch.dispatch(
            args.spec,
            repo=args.ci_repo,
            workflow=args.ci_workflow,
            ref=args.ci_ref,
            inputs=inputs,
            watch=args.ci_watch or bool(args.ci_download),
            download_dir=args.ci_download,
            timeout=args.ci_timeout,
        )
    except ci_dispatch.CiError as exc:
        print(f"CI DISPATCH FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"[ci] {result['url']}")
    if result["downloaded"]:
        print(f"[ci] artifacts: {result['downloaded']}")
    if result["conclusion"]:
        print(f"[ci] conclusion: {result['conclusion']}")
    return int(result["exit_code"])


def stackup_gate(spec: dict[str, Any], profile: dict[str, Any]) -> int:
    gate = profile.get("gates", {}).get("stackup")
    if gate in (None, "off"):
        return 0
    stackup = spec.get("board", {}).get("stackup")
    if not stackup:
        return 1
    findings = ds.validate_stackup(spec, stackup)
    return 0 if findings.ok else max(1, len(findings.errors))


def impedance_gate(spec: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    failures = 0
    for cls_name, cls in spec.get("netclasses", {}).items():
        if not cls.get("impedance_ohm"):
            continue
        computed = ds.impedance_for_netclass(spec, cls_name)
        target = float(cls["impedance_ohm"])
        tolerance = float(cls.get("impedance_tolerance_pct", 10))
        for item in computed:
            item = dict(item)
            item["netclass"] = cls_name
            item["pass"] = target * (1 - tolerance / 100) <= item["z0_ohm"] <= target * (1 + tolerance / 100)
            if not item["pass"]:
                failures += 1
            results.append(item)
    return failures, results


def report_markdown(report: dict[str, Any], extra: dict[str, Any]) -> str:
    status = "PROFESSIONAL - RELEASE GATES PASSED" if report["professional_claim"] else "NOT RELEASED - GATES INCOMPLETE"
    lines = [
        f"# {report['name']} rev {report.get('revision', 'A')}",
        "",
        f"**Status: {status}**",
        "",
        f"- Profile: `{report['profile']}` ({report['automation']})",
        f"- Backend: `{extra.get('backend', {}).get('selected', 'local')}` ({extra.get('backend', {}).get('environment', 'local')})",
        f"- Failures: {', '.join(report['failures']) if report['failures'] else 'none'}",
        f"- Required human sign-off: {', '.join(report['required_signoff']) or 'none'}",
        f"- Completed sign-off: {', '.join(report['completed_signoff']) or 'none'}",
        "",
        "## Automated gates",
        "",
        "| Gate | Result |",
        "|---|---|",
    ]
    for key, value in report.get("automated", {}).items():
        if isinstance(value, dict):
            value = json.dumps(value, sort_keys=True)
        lines.append(f"| {key} | {value} |")
    if extra.get("check"):
        drc = extra["check"].get("drc", {})
        lines.extend(
            [
                "",
                "## DRC",
                "",
                f"- Errors: {drc.get('violations', 'unknown')}",
                f"- Unconnected: {drc.get('unconnected', 'unknown')}",
                f"- Report: `{drc.get('report', '')}`",
            ]
        )
    if extra.get("routing", {}).get("metrics"):
        metrics = extra["routing"]["metrics"]
        lines.extend([
            "",
            "## Routing quality metrics",
            "",
            f"- Track copper length: {metrics.get('track_length_mm')} mm",
            f"- Track segments: {metrics.get('track_segment_count')}",
            f"- Vias: {metrics.get('via_count')}",
            f"- Via layer-transition locations: {metrics.get('via_layer_transition_count')}",
            f"- Unconnected items: {metrics.get('unconnected_items')}",
            f"- Clearance violations: {metrics.get('clearance_violations', 'not measured')}",
            f"- Metrics JSON: `{extra['routing'].get('metrics_report', '')}`",
            "",
            "These are routing measurements, not electrical, thermal, SI/PI, EMC, or safety sign-off.",
        ])
    if extra.get("impedance"):
        lines.extend(["", "## Impedance", "", "| Netclass | Layer | Kind | Width (mm) | Z0 (ohm) | Target (ohm) | Pass |", "|---|---|---|---:|---:|---:|---|"])
        for item in extra["impedance"]:
            lines.append(
                f"| {item.get('netclass')} | {item.get('layer')} | {item.get('kind')} | {item.get('width_mm')} | "
                f"{item.get('z0_ohm')} | {item.get('target_ohm')} | {'yes' if item.get('pass') else 'NO'} |"
            )
    if extra.get("length_match"):
        lines.extend(["", "## Routed lengths", ""])
        for net, length in sorted(extra["length_match"].get("lengths_mm", {}).items()):
            lines.append(f"- {net}: {length} mm")
    if report.get("findings", {}).get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for item in report["findings"]["warnings"]:
            lines.append(f"- `{item['code']}` {item['message']}")
    if report.get("findings", {}).get("errors"):
        lines.extend(["", "## Errors", ""])
        for item in report["findings"]["errors"]:
            lines.append(f"- `{item['code']}` {item['message']}")
    if report.get("assumptions"):
        lines.extend(["", "## Assumptions", ""])
        for item in report["assumptions"]:
            lines.append(f"- {item}")
    if extra.get("fab"):
        fab = extra["fab"]
        lines.extend(["", "## Fabrication package", "", f"- Archive: `{fab.get('archive')}`"])
        for label, key in (
            ("Gerbers", "gerbers"),
            ("Drill", "drill"),
            ("Position", "pos"),
            ("STEP", "step"),
            ("IPC-D-356", "ipc356"),
        ):
            if fab.get(key):
                lines.append(f"- {label}: `{fab[key]}`")
        if fab.get("required_outputs"):
            lines.append(f"- Required outputs: {', '.join(fab['required_outputs'])}")
        if fab.get("missing_required"):
            lines.append(f"- Missing required outputs: {', '.join(fab['missing_required'])}")
    lines.extend(["", "---", "", report.get("disclaimer", ""), ""])
    return "\n".join(lines)


def write_report(report: dict[str, Any], extra: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    report_dir = output_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "design-report.json"
    md_path = report_dir / "design-report.md"
    payload = dict(report)
    payload["artifacts"] = extra
    ds.write_json(json_path, payload)
    md = report_markdown(report, extra)
    md_path.write_text(md, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")
    return json_path, md_path


def cmd_validate(args: argparse.Namespace) -> int:
    spec, profile = resolve_spec(args, Path(args.output) if args.output else ROOT / "build")
    findings = ds.validate_spec(spec, profile)
    for item in findings.errors:
        print(f"ERROR [{item['code']}] {item['message']}", file=sys.stderr)
    for item in findings.warnings:
        print(f"WARN  [{item['code']}] {item['message']}", file=sys.stderr)
    print(f"{len(findings.errors)} error(s), {len(findings.warnings)} warning(s)")
    return 0 if findings.ok else 1


def spice_ci_blocker(args: argparse.Namespace) -> str | None:
    """Return a message when CI would need an interactive SPICE answer."""
    ltspice = getattr(args, "ltspice", None)
    if not ltspice:
        return None
    if getattr(args, "ask", None) is False:
        return None
    try:
        overlay = ds.apply_defaults(ds.load_json(args.spec))
        pin_map = ds.load_json(args.pin_map) if getattr(args, "pin_map", None) else None
        node_aliases = ds.load_json(args.node_aliases) if getattr(args, "node_aliases", None) else None
        missing = spice_flow.missing_inputs(
            overlay, ltspice, pin_map=pin_map, node_aliases=node_aliases
        )
    except (ValueError, OSError) as exc:
        return f"could not inspect the SPICE import: {exc}"
    if not missing:
        return None
    return (
        "CI cannot ask visual questions for a SPICE import.\n"
        "Missing:\n"
        + "\n".join(f"  - {item}" for item in missing)
        + "\nRun locally with --ltspice to answer the questions, then either "
        "use build/<name>/<name>.spec.json as a self-contained spec, or pass "
        "the reusable pin map/aliases from build/<name>/ask/ with the original "
        "overlay."
    )


def cmd_all(args: argparse.Namespace) -> int:
    stages_needed = requested_stages(args)
    backend, probe = resolve_backend(args, stages_needed)
    announce_backend(backend, probe, stages_needed)
    if backend == "ci":
        blocker = spice_ci_blocker(args)
        if blocker:
            print(blocker, file=sys.stderr)
            return 2
        return dispatch_ci(args)

    base_output = Path(args.output) if args.output else ROOT / "build"
    started = time.time()
    spec, profile = resolve_spec(args, base_output)
    output_dir = base_output if args.output else base_output / spec["name"]
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_spec_path = output_dir / f"{spec['name']}.spec.json"
    ds.write_json(resolved_spec_path, spec)

    findings = ds.validate_spec(spec, profile)
    for item in findings.errors:
        print(f"ERROR [{item['code']}] {item['message']}", file=sys.stderr)
    if not findings.ok:
        report = ds.gate_report(spec, profile, findings, {})
        extra = {
            "spec": str(resolved_spec_path),
            "findings_only": True,
            "backend": backend_extra(backend, probe, stages_needed),
        }
        write_report(report, extra, output_dir)
        return 1

    board_path = Path(args.board) if args.board else output_dir / f"{spec['name']}.kicad_pcb"
    stages: dict[str, Any] = {}
    extra: dict[str, Any] = {
        "spec": str(resolved_spec_path),
        "backend": backend_extra(backend, probe, stages_needed),
    }

    if not args.board and not args.skip_build:
        print(f"[build] {board_path}")
        try:
            built = pcb_build.build(
                spec,
                output_dir,
                Path(args.footprints_dir) if args.footprints_dir else None,
            )
        except Exception as exc:
            print(f"BUILD FAILED: {exc}", file=sys.stderr)
            report = ds.gate_report(spec, profile, findings, {"structural": "build_failed"})
            write_report(report, extra, output_dir)
            return 1
        board_path = Path(built)
        extra["board"] = str(board_path)
        extra["project"] = str(output_dir / f"{spec['name']}.kicad_pro")
    else:
        extra["board"] = str(board_path)

    if not board_path.is_file():
        print(f"BOARD NOT FOUND: {board_path}", file=sys.stderr)
        return 1

    if not args.skip_route and spec.get("routing", {}).get("mode", "freerouting") != "none":
        jar = Path(args.freerouting_jar or os.environ.get("FREEROUTING_JAR", SCRIPT_DIR / "freerouting.jar"))
        if not jar.is_file():
            message = (
                f"Freerouting jar not found: {jar}. Download v2.5.0 with:\n"
                f"  curl -fL -o {jar} https://github.com/freerouting/freerouting/releases/download/v2.5.0/freerouting-2.5.0.jar"
            )
            print("ROUTE SKIPPED: " + message, file=sys.stderr)
            if not args.allow_unverified:
                stages["routing"] = "missing"
        else:
            print(f"[route] {board_path}")
            try:
                route_result = pcb_route.route(
                    spec, board_path, output_dir, freerouting_jar=jar,
                    allow_feedback=not bool(args.board) and not args.skip_build,
                )
                extra["routing"] = route_result
                stages["unconnected"] = route_result.get("unconnected", 0)
            except Exception as exc:
                print(f"ROUTE FAILED: {exc}", file=sys.stderr)
                stages["routing"] = "failed"
    else:
        stages["unconnected"] = "not_routed"

    if not args.skip_check:
        print(f"[check] {board_path}")
        check = pcb_check.gate_all(spec, board_path, output_dir / "check", kicad_cli=args.kicad_cli)
        check["drc"].pop("report_text", None)
        extra["check"] = check
        stages["structural"] = 0 if check["structural"]["ok"] else 1
        stages["drc"] = 0 if check["drc"]["ok"] else max(1, int(check["drc"].get("violations", 1)))
        stages["unconnected"] = int(check["drc"].get("unconnected", 0))
        stages["dfm"] = 0 if check["dfm"]["ok"] else max(1, len(check["dfm"].get("errors", [])))
        stages["netlist_parity"] = 0 if check["netlist_parity"]["ok"] else max(1, len(check["netlist_parity"].get("errors", [])))
        stages["length_match"] = 0 if check["length_match"]["ok"] else max(1, len(check["length_match"].get("errors", [])))
        extra["length_match"] = check["length_match"]
        if extra.get("routing", {}).get("metrics"):
            metrics = extra["routing"]["metrics"]
            metrics["clearance_violations"] = check["drc"].get("clearance_violations")
            metrics["drc_violations"] = check["drc"].get("violations")
            metrics["drc_ok"] = check["drc"].get("ok")
            metrics_path = extra["routing"].get("metrics_report")
            if metrics_path:
                ds.write_json(Path(metrics_path), metrics)

    stages["stackup"] = stackup_gate(spec, profile)
    impedance_failures, impedance_results = impedance_gate(spec)
    stages["impedance"] = impedance_failures
    extra["impedance"] = impedance_results
    stages["thermal"] = "reported"
    stages["emc"] = "not_verified"

    if not args.skip_fab:
        print(f"[fab] {board_path}")
        try:
            fab = pcb_fab.export_fab(spec, board_path, output_dir / "fab", kicad_cli=args.kicad_cli)
            extra["fab"] = fab
            stages["fab"] = 0 if fab["ok"] else 1
        except Exception as exc:
            print(f"FAB FAILED: {exc}", file=sys.stderr)
            stages["fab"] = 1

    report = ds.gate_report(spec, profile, findings, stages)
    report["elapsed_s"] = round(time.time() - started, 1)
    report["stages"] = stages
    json_path, md_path = write_report(report, extra, output_dir)
    print(f"[report] {md_path}")
    if report["professional_claim"]:
        print("PROFESSIONAL CLAIM: PASS")
        return 0
    print("PROFESSIONAL CLAIM: FAIL - " + ", ".join(report["failures"]))
    return 0 if args.allow_unverified else 1


def cmd_stage(args: argparse.Namespace) -> int:
    stages_needed = [args.command]
    backend, probe = resolve_backend(args, stages_needed)
    if backend == "ci":
        print(
            "CI runs the full pipeline and cannot see local stage files. "
            f"Use: python3 scripts/design_pipeline.py all {args.spec} --backend ci",
            file=sys.stderr,
        )
        return 1
    announce_backend(backend, probe, stages_needed)
    spec, profile = resolve_spec(args, Path(args.output) if args.output else ROOT / "build")
    findings = ds.validate_spec(spec, profile)
    if not findings.ok:
        for item in findings.errors:
            print(f"ERROR [{item['code']}] {item['message']}", file=sys.stderr)
        return 1
    output_dir = Path(args.output) if args.output else ROOT / "build" / spec["name"]
    if args.command == "build":
        print(pcb_build.build(spec, output_dir, Path(args.footprints_dir) if args.footprints_dir else None))
        return 0
    board = Path(args.board) if args.board else output_dir / f"{spec['name']}.kicad_pcb"
    if args.command == "route":
        jar = Path(args.freerouting_jar or os.environ.get("FREEROUTING_JAR", SCRIPT_DIR / "freerouting.jar"))
        result = pcb_route.route(spec, board, output_dir, freerouting_jar=jar)
        print(json.dumps(result, indent=2) if args.json else f"routed: {result.get('tracks')} tracks")
        return 0 if result.get("unconnected") == 0 else 1
    if args.command == "check":
        result = pcb_check.gate_all(spec, board, output_dir / "check", kicad_cli=args.kicad_cli)
        print(json.dumps(result, indent=2) if args.json else f"check ok={result['ok']}")
        return 0 if result["ok"] else 1
    if args.command == "fab":
        result = pcb_fab.export_fab(spec, board, output_dir / "fab", kicad_cli=args.kicad_cli)
        print(result["archive"])
        return 0 if result["ok"] else 1
    return 2


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("spec", help="design spec JSON")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--netlist", help="KiCad .net netlist to merge")
    source.add_argument("--schematic", help="KiCad .kicad_sch to export a netlist from")
    source.add_argument("--ltspice", help="LTspice/SPICE .net or .cir netlist to import")
    parser.add_argument("--pin-map", help="JSON pin map for LTspice multi-pin parts")
    parser.add_argument("--node-aliases", help="JSON map of SPICE node names to PCB net names")
    parser.add_argument(
        "--ask",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "ask visual questions when a SPICE import needs pin maps, footprints "
            "or placement (default: on)"
        ),
    )
    parser.add_argument("--ask-host", default="127.0.0.1", help="visual question bind address")
    parser.add_argument("--ask-port", type=int, default=8765, help="visual question port (0 picks a free port)")
    parser.add_argument(
        "--ask-timeout",
        type=float,
        default=1800,
        help="seconds to wait for visual answers",
    )
    parser.add_argument(
        "--ask-mobile",
        action="store_true",
        help="bind the visual question page to the LAN so a phone can open it",
    )
    parser.add_argument(
        "--ask-open",
        action="store_true",
        help="open the visual question page in the local browser",
    )
    parser.add_argument(
        "--ask-no-fetch",
        action="store_true",
        help="do not fetch footprint previews from the KiCad GitHub library",
    )
    parser.add_argument("--board", help="existing .kicad_pcb; skips build")
    parser.add_argument("-o", "--output", help="output directory")
    parser.add_argument("--kicad-cli", default=os.environ.get("KICAD_CLI", "kicad-cli"))
    parser.add_argument("--footprints-dir", help="KiCad footprints root")
    parser.add_argument("--freerouting-jar", default=os.environ.get("FREEROUTING_JAR", ""))
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-route", action="store_true")
    parser.add_argument("--skip-check", action="store_true")
    parser.add_argument("--skip-fab", action="store_true")
    parser.add_argument("--allow-unverified", action="store_true", help="exit 0 even without a professional claim")
    parser.add_argument(
        "--backend",
        choices=("auto", "local", "ci"),
        default=os.environ.get("PCB_BACKEND", "auto"),
        help="where to run build/route/check/fab: auto detects local tools, then falls back to GitHub Actions",
    )
    parser.add_argument("--ci-repo", help="owner/name for CI dispatch (default: current git remote)")
    parser.add_argument("--ci-workflow", default=os.environ.get("PCB_CI_WORKFLOW", "design.yml"))
    parser.add_argument("--ci-ref", help="branch for CI dispatch (default: current branch)")
    parser.add_argument(
        "--ci-watch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="wait for the CI run and return its gate result (default; --no-ci-watch returns after dispatch)",
    )
    parser.add_argument("--ci-download", help="download CI artifacts here (implies watching)")
    parser.add_argument("--ci-timeout", type=int, default=3600, help="CI watch timeout in seconds")
    parser.add_argument("--json", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Professional automatic PCB design pipeline.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "build", "route", "check", "fab", "all"):
        p = sub.add_parser(name)
        add_common(p)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return cmd_validate(args)
        if args.command == "all":
            return cmd_all(args)
        return cmd_stage(args)
    except spice_flow.SpiceInputError as exc:
        print(f"SPICE INPUT NEEDED:\n{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
