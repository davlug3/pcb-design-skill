#!/usr/bin/env python3
"""Graphical interaction UI for pcb-design-skill (MVP, additive).

Adds artifact/spec review sessions using the shared design-interact UI.
Existing `scripts/visual_ask.py` remains unchanged for footprint/visual
question workflows.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SHARED_SCRIPTS = SKILL_DIR.parent / "_shared" / "design-interact" / "scripts"
sys.path.insert(0, str(SHARED_SCRIPTS))

import design_interact as di  # noqa: E402


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _find_one(directory: Path, names: list[str]) -> Path | None:
    for n in names:
        p = directory / n
        if p.is_file():
            return p
    for pat in ["design-report.json", "*report*.json", "*.spec.json", "*.kicad_pcb"]:
        for p in directory.glob(pat):
            if p.is_file() and p.name in names:
                return p
    return None


def build_review_payload(*, artifact_dir: Path, spec_path: Path | None = None, title: str | None = None) -> dict:
    blocks = []
    report_json = _find_one(artifact_dir, ["design-report.json", "report.json"])
    report_md = _find_one(artifact_dir, ["design-report.md", "report.md"])
    spec_file = spec_path or _find_one(artifact_dir, [p.name for p in artifact_dir.glob("*.spec.json")] or []) or _find_one(artifact_dir, [p.name for p in artifact_dir.glob("*.json") if "report" not in p.name])

    if report_json and report_json.is_file():
        try:
            report = _load_json(report_json)
            summary = {}
            for k in ["name", "profile", "board", "professional_claim", "status", "backend", "gates", "signoff", "errors", "warnings", "summary"]:
                if k in report:
                    v = report[k]
                    summary[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
            if summary:
                blocks.append({"type": "preview", "title": "Design report summary", "format": "keyvalue", "content": summary})
            blocks.append({"type": "preview", "title": "Design report (JSON)", "format": "json", "path": str(report_json)})
        except Exception as exc:
            blocks.append({"type": "preview", "title": "Design report (JSON)", "format": "text", "path": str(report_json), "description": f"Could not parse summary: {exc}"})
    if report_md and report_md.is_file():
        blocks.append({"type": "preview", "title": "Design report (Markdown)", "format": "markdown", "path": str(report_md)})
    if spec_file and spec_file.is_file() and (not report_json or spec_file != report_json):
        try:
            spec = _load_json(spec_file)
            if isinstance(spec, dict):
                kv = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                      for k, v in spec.items() if k in ["name", "title", "profile", "board", "rules", "nets", "components", "assumptions", "notes"]}
                if "components" in spec and isinstance(spec["components"], list):
                    kv["components_count"] = len(spec["components"])
                blocks.append({"type": "preview", "title": f"Spec: {spec_file.name}", "format": "keyvalue", "content": kv})
                blocks.append({"type": "preview", "title": f"Spec JSON: {spec_file.name}", "format": "json", "path": str(spec_file)})
        except Exception:
            blocks.append({"type": "preview", "title": f"Spec: {spec_file.name}", "format": "text", "path": str(spec_file)})

    files = []
    for pat in ["*.kicad_pcb", "*.kicad_dru", "*.kicad_pro", "*.net", "*.dsn", "*.ses", "*.zip", "*.pos", "*.csv", "*.gbr", "*.drl", "*.ipc", "*.step", "*.stp", "fabrication*"]:
        for p in sorted(artifact_dir.rglob(pat)):
            if p.is_file() and p not in {report_json, report_md, spec_file}:
                rel = str(p.relative_to(artifact_dir))
                if rel not in [f.get("path") for f in files]:
                    files.append({"path": rel})
    if files:
        blocks.append({"type": "preview", "title": "Artifacts (downloads)", "format": "files", "paths": [f["path"] for f in files[:120]],
                       "description": "MVP preview shows summaries/downloads. Open PCB/CAM files in KiCad or your CAM tool for graphical inspection."})
    if not blocks:
        blocks.append({"type": "preview", "title": "No recognized PCB artifacts found", "format": "text",
                       "content": f"Looked in: {artifact_dir}\nProvide a directory containing design-report.json/md, resolved spec, and/or fabrication outputs."})

    blocks.append({"type": "preview", "title": "Footprint/choice questions", "format": "markdown", "content":
                   "For footprint selection and structured visual questions, use the existing `scripts/visual_ask.py` workflow (unchanged). This session is for artifact/spec review feedback."})
    blocks.append({"type": "review", "id": "pcb_review", "required": True,
                   "prompt": "Review PCB artifacts/spec.",
                   "help": "Check gates, assumptions, sign-offs, manufacturability claims, and requested geometry/rule changes. The pipeline's report determines professional/fab claims.",
                   "comment_label": "Feedback / requested changes / sign-off notes"})
    return {
        "title": title or f"PCB artifact review: {artifact_dir.name}",
        "intro": "MVP review for PCB outputs: report/spec summaries and downloadable artifacts. Graphical board rendering is not included; use KiCad for visual board inspection.",
        "skill": "pcb-design-skill",
        "session_kind": "artifact-review",
        "artifacts_dir": str(artifact_dir.resolve()),
        "submit_label": "Send review",
        "blocks": blocks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Graphical interaction UI for pcb-design-skill (additive).")
    sub = parser.add_subparsers(dest="command", required=True)
    rev = sub.add_parser("review-artifacts", help="review PCB report/spec/artifacts (MVP)")
    rev.add_argument("--dir", required=True, help="artifact/report directory")
    rev.add_argument("--spec", help="optional spec/report JSON path")
    rev.add_argument("--out", required=True, help="answer JSON path")
    rev.add_argument("--title")
    rev.add_argument("--host", default="127.0.0.1")
    rev.add_argument("--mobile", action="store_true")
    rev.add_argument("--port", type=int, default=8773)
    rev.add_argument("--timeout", type=float, default=1800)
    rev.add_argument("--detach", action="store_true")
    rev.add_argument("--open", action="store_true", dest="open_browser")
    rev.add_argument("--json", action="store_true", dest="print_json")
    args = parser.parse_args(argv)

    artifact_dir = Path(args.dir).expanduser().resolve()
    spec_path = Path(args.spec).expanduser().resolve() if args.spec else None
    out_path = Path(args.out).expanduser().resolve()
    url_file = out_path.with_suffix(".url")
    status_file = out_path.with_suffix(".status.json")
    log_file = out_path.with_suffix(".log")
    host = "0.0.0.0" if args.mobile else args.host
    payload = di.normalize_payload(build_review_payload(artifact_dir=artifact_dir, spec_path=spec_path, title=args.title), artifacts_dir=artifact_dir)

    if args.detach:
        return di._detach(payload=payload, out_path=out_path, host=host, port=args.port, timeout=args.timeout,
                          url_file=url_file, status_file=status_file, log_file=log_file,
                          artifacts_dir=str(artifact_dir), open_browser=args.open_browser, verbose=False)
    try:
        server = di.make_server(payload, out_path=out_path, host=host, port=args.port, status_path=status_file,
                                artifacts_dir=artifact_dir)
    except OSError as exc:
        print(f"could not start interaction server: {exc}", file=sys.stderr)
        return 1
    return di.run_server(server, timeout=args.timeout, url_file=url_file, open_browser=args.open_browser, print_json=args.print_json)


if __name__ == "__main__":
    raise SystemExit(main())
