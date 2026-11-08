#!/usr/bin/env python3
"""Detect which PCB pipeline backend can run in the current environment.

The pipeline needs different tools for different stages:

    validate  Python only
    build     pcbnew (KiCad 9 Python bindings)
    route     pcbnew + Java 25+ + freerouting-2.5.0.jar
    check     pcbnew + kicad-cli
    fab       kicad-cli

``design_pipeline.py --backend auto`` uses this module to run locally when the
required tools are present and to dispatch GitHub Actions otherwise. The probe
is read-only: it never imports pcbnew into the calling process, so a missing
KiCad installation cannot break spec validation or reporting.

CLI:
    python3 scripts/env_probe.py
    python3 scripts/env_probe.py --json
    python3 scripts/env_probe.py --spec specs/led-blinky.json --require ci
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

MIN_PYTHON = (3, 9)
MIN_KICAD = 9
MIN_JAVA = 25

# A stage is runnable only when every listed tool is present and supported.
STAGE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "validate": (),
    "build": ("pcbnew",),
    "route": ("pcbnew", "java", "freerouting"),
    "check": ("pcbnew", "kicad-cli"),
    "fab": ("kicad-cli",),
}
ALL_STAGES = ("build", "route", "check", "fab")


def _run(cmd: list[str], *, timeout: int = 20, cwd: Path | None = None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _text(proc: subprocess.CompletedProcess | None) -> str:
    if proc is None:
        return ""
    return ((proc.stdout or "") + (proc.stderr or "")).strip()


def _short(text: str, limit: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_java_major(text: str | None) -> int | None:
    """Return the Java feature version from ``java -version`` output."""
    if not text:
        return None
    match = re.search(r'version\s+"(\d+)(?:\.(\d+))?', text)
    if match:
        major = int(match.group(1))
        # Java 8 and older report "1.8.0_x"; the feature version is the second part.
        if major == 1 and match.group(2):
            return int(match.group(2))
        return major
    match = re.search(r"openjdk\s+(\d+)", text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_kicad_major(text: str | None) -> int | None:
    """Return the KiCad major version from ``kicad-cli version`` or pcbnew."""
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d+)\b", text)
    return int(match.group(1)) if match else None


def _tool(ok: bool, *, supported: bool | None = None, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "supported": ok if supported is None else supported, **extra}


def probe_python() -> dict[str, Any]:
    version = platform.python_version()
    return _tool(
        sys.version_info[:2] >= MIN_PYTHON,
        version=version,
        executable=sys.executable,
        detail=f"Python {version}",
    )


def probe_pcbnew() -> dict[str, Any]:
    proc = _run(
        [sys.executable, "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"],
        timeout=60,
    )
    if proc is None:
        return _tool(False, version=None, major=None, detail="pcbnew probe could not run")
    if proc.returncode != 0:
        lines = [line.strip() for line in _text(proc).splitlines() if line.strip()]
        detail = lines[-1] if lines else "pcbnew is not importable by this Python"
        return _tool(False, version=None, major=None, detail=_short(detail))
    version = (_text(proc).splitlines() or [""])[0].strip() or None
    major = parse_kicad_major(version)
    supported = major is not None and major >= MIN_KICAD
    detail = f"pcbnew {version}" if version else "pcbnew importable"
    if major is not None and not supported:
        detail += f" (KiCad {MIN_KICAD}+ required)"
    return _tool(True, supported=supported, version=version, major=major, detail=detail)


def probe_kicad_cli(command: str | None = None) -> dict[str, Any]:
    command = command or os.environ.get("KICAD_CLI") or "kicad-cli"
    path = command if os.path.isabs(command) else shutil.which(command)
    if path and not Path(path).is_file():
        path = None
    if not path:
        return _tool(False, version=None, major=None, path=None, detail=f"{command} not found on PATH")
    proc = _run([path, "version"], timeout=30)
    if proc is None or proc.returncode != 0:
        return _tool(
            False,
            version=None,
            major=None,
            path=path,
            detail=_short(_text(proc)) or f"{path} did not report a version",
        )
    version = (_text(proc).splitlines() or [""])[0].strip() or None
    major = parse_kicad_major(version)
    supported = major is not None and major >= MIN_KICAD
    detail = f"kicad-cli {version}" if version else "kicad-cli found"
    if major is not None and not supported:
        detail += f" (KiCad {MIN_KICAD}+ required)"
    return _tool(True, supported=supported, version=version, major=major, path=path, detail=detail)


def probe_java() -> dict[str, Any]:
    path = shutil.which("java")
    if not path and os.environ.get("JAVA_HOME"):
        candidate = Path(os.environ["JAVA_HOME"]) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.is_file():
            path = str(candidate)
    if not path:
        return _tool(False, version=None, major=None, path=None, detail="java not found on PATH")
    proc = _run([path, "-version"], timeout=30)
    text = _text(proc)
    major = parse_java_major(text)
    supported = major is not None and major >= MIN_JAVA
    version = (text.splitlines() or [""])[0].strip() or None
    detail = version or "java found"
    if major is not None and not supported:
        detail += f" (Java {MIN_JAVA}+ required for Freerouting 2.5.0)"
    return _tool(True, supported=supported, version=version, major=major, path=path, detail=detail)


def resolve_freerouting_jar(jar: str | Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if jar:
        candidates.append(Path(jar))
    env = os.environ.get("FREEROUTING_JAR")
    if env:
        candidates.append(Path(env))
    candidates.extend((SCRIPT_DIR / "freerouting.jar", Path.cwd() / "freerouting.jar"))
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate.resolve()
        except OSError:
            continue
    return None


def probe_freerouting(jar: str | Path | None = None) -> dict[str, Any]:
    path = resolve_freerouting_jar(jar)
    if path is None:
        return _tool(
            False,
            version=None,
            path=None,
            detail="freerouting jar not found (set FREEROUTING_JAR or download scripts/freerouting.jar)",
        )
    match = re.search(r"freerouting[-_](\d+\.\d+\.\d+)", path.name)
    version = match.group(1) if match else None
    detail = f"freerouting {version}" if version else f"freerouting jar {path.name}"
    return _tool(True, version=version, path=str(path), detail=detail)


def probe_gh() -> dict[str, Any]:
    path = shutil.which("gh")
    if not path:
        return _tool(False, authenticated=False, path=None, version=None, detail="gh CLI not found on PATH")
    version_proc = _run([path, "--version"], timeout=15)
    version = (_text(version_proc).splitlines() or [""])[0].strip() or None
    auth = _run([path, "auth", "status"], timeout=30)
    authenticated = auth is not None and auth.returncode == 0
    detail = version or "gh found"
    if not authenticated:
        detail += " (not authenticated: run `gh auth login`)"
    return _tool(True, authenticated=authenticated, path=path, version=version, detail=detail)


def probe_git(cwd: Path | None = None) -> dict[str, Any]:
    cwd = cwd or Path.cwd()
    proc = _run(["git", "rev-parse", "--show-toplevel"], timeout=15, cwd=cwd)
    root_text = _text(proc)
    root = Path(root_text).resolve() if proc is not None and proc.returncode == 0 and root_text else None
    branch = upstream = remote = None
    if root is not None:
        branch_proc = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=15, cwd=root)
        branch = _text(branch_proc) or None if branch_proc is not None and branch_proc.returncode == 0 else None
        upstream_proc = _run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            timeout=15,
            cwd=root,
        )
        upstream = _text(upstream_proc) or None if upstream_proc is not None and upstream_proc.returncode == 0 else None
        remote_proc = _run(["git", "remote", "get-url", "origin"], timeout=15, cwd=root)
        remote = _text(remote_proc) or None if remote_proc is not None and remote_proc.returncode == 0 else None
    return {
        "ok": root is not None,
        "root": str(root) if root else None,
        "branch": branch,
        "upstream": upstream,
        "remote": remote,
        "detail": f"{root} @ {branch}" if root and branch else "not a git repository",
    }


def repo_relative(root: Path, path: str | Path, cwd: Path | None = None) -> str | None:
    """Return ``path`` relative to ``root``, or None when it is outside."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (cwd or Path.cwd()) / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def ci_blockers(
    root: Path | str | None,
    spec: str | Path | None = None,
    *,
    workflow: str = "design.yml",
    cwd: Path | None = None,
    paths: list[str | Path] | None = None,
) -> list[str]:
    """Reasons the pushed tree cannot represent the local working copy in CI."""
    if root is None:
        return ["not inside a git repository"]
    root = Path(root)
    blockers: list[str] = []
    workflow_path = root / ".github" / "workflows" / workflow
    if not workflow_path.is_file():
        blockers.append(f"workflow missing: {workflow_path}")

    watched: list[str] = ["scripts", "profiles", "schemas", "tests", f".github/workflows/{workflow}"]
    for extra in paths or []:
        rel = repo_relative(root, extra, cwd)
        if rel is None:
            blockers.append(f"input is outside the repository: {extra}")
        elif not (root / rel).is_file():
            blockers.append(f"input not found: {rel}")
        elif rel not in watched:
            watched.insert(0, rel)
    if spec is not None:
        rel = repo_relative(root, spec, cwd)
        if rel is None:
            blockers.append(f"spec is outside the repository: {spec}")
        elif not (root / rel).is_file():
            blockers.append(f"spec not found: {rel}")
        elif rel not in watched:
            watched.insert(0, rel)

    status = _run(["git", "status", "--porcelain", "--", *watched], timeout=30, cwd=root)
    dirty = _text(status)
    if dirty:
        blockers.append(
            "uncommitted or untracked pipeline inputs:\n    "
            + "\n    ".join(dirty.splitlines()[:10])
        )

    upstream_proc = _run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        timeout=15,
        cwd=root,
    )
    upstream = _text(upstream_proc)
    if upstream_proc is None or upstream_proc.returncode != 0 or not upstream:
        blockers.append("current branch has no upstream (push it with `git push -u origin <branch>`)")
    else:
        ahead_proc = _run(["git", "rev-list", "--count", f"{upstream}..HEAD"], timeout=30, cwd=root)
        try:
            ahead = int(_text(ahead_proc) or "0")
        except ValueError:
            ahead = 0
        if ahead > 0:
            blockers.append(f"{ahead} commit(s) not pushed to {upstream}")

    return blockers


def stage_status(tools: dict[str, Any], stage: str) -> dict[str, Any]:
    missing = [
        name
        for name in STAGE_REQUIREMENTS.get(stage, ())
        if not tools.get(name, {}).get("supported", False)
    ]
    return {"ok": not missing, "missing": missing}


def missing_for_stages(probe: dict[str, Any], stages: list[str] | tuple[str, ...] | None = None) -> list[str]:
    stages = tuple(stages or ALL_STAGES)
    missing: list[str] = []
    for stage in stages:
        for name in probe.get("stages", {}).get(stage, {}).get("missing", []):
            if name not in missing:
                missing.append(name)
    return missing


def stages_ready(probe: dict[str, Any], stages: list[str] | tuple[str, ...] | None = None) -> bool:
    return not missing_for_stages(probe, stages)


def recommended_backend(probe: dict[str, Any], stages: list[str] | tuple[str, ...] | None = None) -> str:
    if stages_ready(probe, stages):
        return "local"
    if probe.get("ci", {}).get("usable"):
        return "ci"
    return "none"


def probe(
    *,
    jar: str | Path | None = None,
    kicad_cli: str | None = None,
    spec: str | Path | None = None,
    paths: list[str | Path] | None = None,
    workflow: str = "design.yml",
    cwd: Path | None = None,
) -> dict[str, Any]:
    cwd = cwd or Path.cwd()
    tools = {
        "python": probe_python(),
        "pcbnew": probe_pcbnew(),
        "kicad-cli": probe_kicad_cli(kicad_cli),
        "java": probe_java(),
        "freerouting": probe_freerouting(jar),
        "gh": probe_gh(),
    }
    stages = {name: stage_status(tools, name) for name in STAGE_REQUIREMENTS}
    git = probe_git(cwd)
    root = Path(git["root"]) if git.get("root") else None
    blockers = (
        ci_blockers(root, spec, workflow=workflow, cwd=cwd, paths=paths)
        if root is not None
        else ["not inside a git repository"]
    )
    workflow_present = bool(root and (root / ".github" / "workflows" / workflow).is_file())
    ci_usable = bool(
        tools["gh"].get("ok")
        and tools["gh"].get("authenticated")
        and git.get("ok")
        and git.get("branch")
        and git.get("branch") != "HEAD"
        and workflow_present
        and not blockers
    )
    return {
        "ok": stages_ready({"stages": stages}),
        "python": tools["python"],
        "tools": {key: value for key, value in tools.items() if key != "python"},
        "stages": stages,
        "local_ready": all(stages[name]["ok"] for name in ALL_STAGES),
        "missing": missing_for_stages({"stages": stages}),
        "ci": {
            "usable": ci_usable,
            "workflow": workflow,
            "workflow_present": workflow_present,
            "blockers": blockers,
            "git": git,
            "gh": tools["gh"],
        },
        "recommended_backend": recommended_backend({"stages": stages, "ci": {"usable": ci_usable}}),
    }


def render(probe_result: dict[str, Any], stages: list[str] | tuple[str, ...] | None = None) -> str:
    stages = tuple(stages or ALL_STAGES)
    lines = ["Environment probe", ""]
    python = probe_result.get("python", {})
    lines.append(f"  python       {python.get('detail', 'unknown')}")
    for name in ("pcbnew", "kicad-cli", "java", "freerouting", "gh"):
        tool = probe_result.get("tools", {}).get(name, {})
        mark = "ok" if tool.get("supported", tool.get("ok")) else "MISSING"
        lines.append(f"  {name:<12} {mark:<8} {tool.get('detail', '')}")
    lines.extend(["", "Stages", ""])
    for name in ("build", "route", "check", "fab"):
        status = probe_result.get("stages", {}).get(name, {})
        if status.get("ok"):
            lines.append(f"  {name:<8} ready")
        else:
            lines.append(f"  {name:<8} missing: {', '.join(status.get('missing', [])) or 'unknown'}")
    ci = probe_result.get("ci", {})
    git = ci.get("git", {})
    lines.extend(["", "Backends", ""])
    local_ready = all(probe_result.get("stages", {}).get(name, {}).get("ok") for name in stages)
    lines.append(f"  local   {'READY' if local_ready else 'NOT READY'}")
    if ci.get("usable"):
        lines.append(f"  ci      READY ({git.get('detail', '')})")
    else:
        lines.append("  ci      NOT READY")
        for blocker in ci.get("blockers", []):
            lines.append(f"          - {blocker}")
    lines.extend(["", f"Recommended backend: {recommended_backend(probe_result, stages)}"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect the PCB pipeline backend for this environment.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--spec", help="design spec to check for CI readiness")
    parser.add_argument("--freerouting-jar", help="explicit freerouting jar path")
    parser.add_argument("--kicad-cli", help="explicit kicad-cli command")
    parser.add_argument("--workflow", default="design.yml", help="GitHub Actions workflow file name")
    parser.add_argument(
        "--stage",
        action="append",
        choices=sorted(STAGE_REQUIREMENTS),
        help="stage to check; repeatable (default: build, route, check, fab)",
    )
    parser.add_argument(
        "--require",
        choices=("auto", "local", "ci"),
        help="exit non-zero unless the selected backend is available",
    )
    args = parser.parse_args(argv)

    stages = args.stage or list(ALL_STAGES)
    result = probe(
        jar=args.freerouting_jar,
        kicad_cli=args.kicad_cli,
        spec=args.spec,
        workflow=args.workflow,
    )
    backend = recommended_backend(result, stages)

    if args.json:
        payload = dict(result)
        payload["checked_stages"] = stages
        payload["recommended_backend"] = backend
        print(json.dumps(payload, indent=2))
    else:
        print(render(result, stages))

    if args.require:
        if args.require == "auto":
            return 0 if backend != "none" else 1
        if args.require == "local":
            return 0 if stages_ready(result, stages) else 1
        return 0 if result.get("ci", {}).get("usable") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
