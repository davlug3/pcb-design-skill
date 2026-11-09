#!/usr/bin/env python3
"""Design-spec loading, validation, netlist import, and professional gates.

This module is intentionally dependency-free (Python 3.9+). It does not need
KiCad: it validates the *design intent* before any geometry is generated, so a
bad spec fails fast and with a useful message.

The canonical spec is described by ``schemas/design-spec.schema.json`` and the
board-class gates live in ``profiles/<profile>.json``.

CLI:
    python3 scripts/design_spec.py validate specs/led-blinky.json
    python3 scripts/design_spec.py explain  specs/led-blinky.json
    python3 scripts/design_spec.py netlist  board.net --spec overlay.json -o resolved.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "profiles"

DEFAULT_NETCLASS = "Default"
COPPER_LAYER_RE = re.compile(r"^(F\.Cu|B\.Cu|In\d+\.Cu)$")
REF_RE = re.compile(r"^[A-Za-z]{1,4}[0-9]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class Findings:
    """Errors and warnings with stable machine-readable codes."""

    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []

    def error(self, code: str, message: str) -> None:
        self.errors.append({"code": code, "message": message})

    def warning(self, code: str, message: str) -> None:
        self.warnings.append({"code": code, "message": message})

    @property
    def ok(self) -> bool:
        return not self.errors

    def merge(self, other: "Findings") -> "Findings":
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def load_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")


def load_profile(name: str, profiles_dir: str | Path | None = None) -> dict[str, Any]:
    directory = Path(profiles_dir) if profiles_dir else PROFILES_DIR
    path = directory / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"unknown profile {name!r}: {path} not found")
    return load_json(path)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def apply_defaults(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a new spec with documented defaults filled in.

    The input dict is not mutated.
    """
    import copy

    s = copy.deepcopy(spec)
    s.setdefault("version", 1)
    s.setdefault("title", s.get("name", ""))
    s.setdefault("revision", "A")
    s.setdefault("assumptions", [])

    board = s.setdefault("board", {})
    board.setdefault("thickness_mm", 1.6)
    board.setdefault("copper_oz", 1.0)
    board.setdefault("material", "FR4")
    board.setdefault("surface_finish", "HASL-LF")
    board.setdefault("mask_color", "green")
    board.setdefault("silkscreen_color", "white")
    board.setdefault("edge_radius_mm", 0.0)

    rules = s.setdefault("rules", {})
    for key, value in {
        "min_track_width_mm": 0.2,
        "min_clearance_mm": 0.2,
        "min_via_diameter_mm": 0.6,
        "min_via_drill_mm": 0.3,
        "min_hole_diameter_mm": 0.3,
        "min_annular_ring_mm": 0.15,
        "edge_clearance_mm": 0.5,
        "min_silk_width_mm": 0.15,
        "min_mask_sliver_mm": 0.1,
        "min_hole_to_hole_mm": 0.5,
    }.items():
        rules.setdefault(key, value)

    netclasses = s.setdefault("netclasses", {})
    default_class = netclasses.setdefault(DEFAULT_NETCLASS, {})
    default_class.setdefault("track_width_mm", rules["min_track_width_mm"])
    default_class.setdefault("clearance_mm", rules["min_clearance_mm"])
    default_class.setdefault("via_diameter_mm", rules["min_via_diameter_mm"])
    default_class.setdefault("via_drill_mm", rules["min_via_drill_mm"])
    for cls in netclasses.values():
        cls.setdefault("track_width_mm", default_class["track_width_mm"])
        cls.setdefault("clearance_mm", default_class["clearance_mm"])
        cls.setdefault("via_diameter_mm", default_class["via_diameter_mm"])
        cls.setdefault("via_drill_mm", default_class["via_drill_mm"])
        cls.setdefault("impedance_tolerance_pct", 10)
        cls.setdefault("diff_pair", False)
        cls.setdefault("length_match_mm", 0.0)

    for comp in s.setdefault("components", []):
        comp.setdefault("value", "")
        comp.setdefault("dnp", False)
        comp.setdefault("properties", {})
        comp.setdefault("pads", {})
        at = comp.get("at")
        if isinstance(at, dict):
            at.setdefault("rotation_deg", 0)
            at.setdefault("side", "front")

    placement = s.setdefault("placement", {})
    placement.setdefault("mode", "fixed")
    placement.setdefault("margin_mm", 2.0)
    placement.setdefault("grid_mm", 2.54)
    placement.setdefault("clearance_mm", 1.0)
    placement.setdefault("keepouts", [])

    routing = s.setdefault("routing", {})
    routing.setdefault("mode", "freerouting")
    routing.setdefault("max_passes", 100)
    routing.setdefault("threads", 4)
    routing.setdefault("timeout_s", 900)
    routing.setdefault("attempts", 3)
    routing.setdefault("optimize", True)

    s.setdefault("zones", [])
    s.setdefault("fabrication", {})
    s["fabrication"].setdefault("vendor", "generic")
    s["fabrication"].setdefault("capabilities", {})
    s.setdefault("signoff", {})
    s["signoff"].setdefault("completed", [])
    return s


def netclass_of(spec: dict[str, Any], net: dict[str, Any] | str) -> dict[str, Any]:
    name = net.get("class", DEFAULT_NETCLASS) if isinstance(net, dict) else DEFAULT_NETCLASS
    return spec.get("netclasses", {}).get(name, spec.get("netclasses", {}).get(DEFAULT_NETCLASS, {}))


def netclass_name_of(spec: dict[str, Any], net: dict[str, Any] | str) -> str:
    if isinstance(net, dict) and net.get("class"):
        return str(net["class"])
    return DEFAULT_NETCLASS


def copper_layers(spec: dict[str, Any]) -> list[str]:
    count = _int(spec.get("board", {}).get("layers"), 2)
    if count <= 1:
        return ["F.Cu"]
    if count == 2:
        return ["F.Cu", "B.Cu"]
    inner = [f"In{i}.Cu" for i in range(1, count - 1)]
    return ["F.Cu", *inner, "B.Cu"]


def validate_spec(spec: dict[str, Any], profile: dict[str, Any]) -> Findings:
    """Validate the resolved spec against its profile.

    This is semantic validation, not JSON-Schema validation. It catches the
    mistakes that cause bad geometry: wrong layer counts, undersized vias,
    undeclared nets, missing stackups, unreachable impedance targets and
    fabrication limits that are looser than the design rules.
    """
    f = Findings()
    spec = apply_defaults(spec)

    if spec.get("version") != 1:
        f.error("spec.version",
                "unsupported spec/handoff version; this build supports version 1")
    name = str(spec.get("name", ""))
    if not NAME_RE.match(name):
        f.error("spec.name", "name must be 1-64 chars of letters, digits, dot, underscore or dash")

    board = spec.get("board", {})
    width = _num(board.get("width_mm"))
    height = _num(board.get("height_mm"))
    layers = _int(board.get("layers"), 0)
    if width <= 0 or height <= 0:
        f.error("board.size", "board.width_mm and board.height_mm must be > 0")
    if layers < 1:
        f.error("board.layers", "board.layers must be >= 1")

    layer_range = profile.get("layers", {})
    if "min" in layer_range and layers < _int(layer_range["min"]):
        f.error("profile.layers", f"profile {profile.get('name')} requires at least {layer_range['min']} layers")
    if "max" in layer_range and layers > _int(layer_range["max"]):
        f.error("profile.layers", f"profile {profile.get('name')} allows at most {layer_range['max']} layers")

    thickness = _num(board.get("thickness_mm"))
    if thickness <= 0:
        f.error("board.thickness", "board.thickness_mm must be > 0")
    copper_oz = _num(board.get("copper_oz"), 1.0)
    if copper_oz <= 0:
        f.error("board.copper_oz", "board.copper_oz must be > 0")

    rules = spec.get("rules", {})
    for key in (
        "min_track_width_mm",
        "min_clearance_mm",
        "min_via_diameter_mm",
        "min_via_drill_mm",
        "min_hole_diameter_mm",
        "min_silk_width_mm",
    ):
        if _num(rules.get(key)) <= 0:
            f.error("rules." + key, f"rules.{key} must be > 0")
    for key in ("min_annular_ring_mm", "edge_clearance_mm", "min_mask_sliver_mm", "min_hole_to_hole_mm"):
        if _num(rules.get(key)) < 0:
            f.error("rules." + key, f"rules.{key} must be >= 0")

    via_d = _num(rules.get("min_via_diameter_mm"))
    via_drill = _num(rules.get("min_via_drill_mm"))
    annular = _num(rules.get("min_annular_ring_mm"))
    if via_drill >= via_d:
        f.error("rules.via", "min_via_drill_mm must be smaller than min_via_diameter_mm")
    elif (via_d - via_drill) / 2.0 + 1e-9 < annular:
        f.error(
            "rules.annular",
            f"min via ({via_d} mm) gives annular ring {(via_d - via_drill) / 2:.3f} mm, "
            f"below min_annular_ring_mm ({annular} mm)",
        )
    if _num(rules.get("min_hole_diameter_mm")) > via_drill:
        f.warning("rules.hole", "min_hole_diameter_mm is larger than the minimum via drill; check tooling")

    netclasses = spec.get("netclasses", {})
    if DEFAULT_NETCLASS not in netclasses:
        f.error("netclasses.default", f"netclasses must define {DEFAULT_NETCLASS!r}")
    for cls_name, cls in netclasses.items():
        if not re.match(r"^[A-Za-z0-9_. -]{1,48}$", cls_name):
            f.error("netclasses.name", f"netclass name {cls_name!r} contains unsupported characters")
        width_cls = _num(cls.get("track_width_mm"))
        clearance = _num(cls.get("clearance_mm"))
        if width_cls < _num(rules.get("min_track_width_mm")):
            f.error("netclasses.width", f"{cls_name}.track_width_mm is below the board minimum")
        if clearance < _num(rules.get("min_clearance_mm")):
            f.error("netclasses.clearance", f"{cls_name}.clearance_mm is below the board minimum")
        if width_cls <= 0 or clearance <= 0:
            f.error("netclasses.value", f"{cls_name} track width and clearance must be > 0")
        vd = _num(cls.get("via_diameter_mm"))
        vdr = _num(cls.get("via_drill_mm"))
        if vd > 0 and vdr > 0:
            if vdr >= vd:
                f.error("netclasses.via", f"{cls_name} via drill must be smaller than via diameter")
            elif (vd - vdr) / 2.0 + 1e-9 < _num(rules.get("min_annular_ring_mm")):
                f.error("netclasses.annular", f"{cls_name} via annular ring is below the board minimum")
        if cls.get("impedance_ohm") is not None:
            if _num(cls.get("impedance_ohm")) <= 0:
                f.error("netclasses.impedance", f"{cls_name}.impedance_ohm must be > 0")
            layer = cls.get("layer")
            if layer and layer not in copper_layers(spec):
                f.error("netclasses.layer", f"{cls_name}.layer {layer!r} is not a copper layer in this stackup")
            if cls.get("diff_pair"):
                if _num(cls.get("diff_pair_gap_mm")) <= 0:
                    f.error("netclasses.diff_gap", f"{cls_name} is a differential pair but has no diff_pair_gap_mm")
            if profile.get("gates", {}).get("impedance") not in ("computed", "required"):
                f.warning("netclasses.impedance", f"{cls_name} sets an impedance target but the profile does not verify impedance")

    nets = spec.get("nets", [])
    seen_nets: set[str] = set()
    for net in nets if isinstance(nets, list) else []:
        net_name = str(net.get("name", ""))
        if not net_name:
            f.error("nets.name", "net name must not be empty")
            continue
        if net_name in seen_nets:
            f.error("nets.duplicate", f"duplicate net {net_name!r}")
        seen_nets.add(net_name)
        cls = net.get("class", DEFAULT_NETCLASS)
        if cls not in netclasses:
            f.error("nets.class", f"net {net_name!r} references unknown class {cls!r}")
        if net.get("diff_pair") and net.get("diff_pair") not in seen_nets and net.get("diff_pair") not in {
            n.get("name") for n in nets if isinstance(n, dict)
        }:
            f.error("nets.diff", f"net {net_name!r} references missing diff_pair {net.get('diff_pair')!r}")
        voltage = _num(net.get("voltage_v"))
        if voltage > 30:
            gate = profile.get("gates", {}).get("safety")
            clearance = _num(netclass_of(spec, net).get("clearance_mm"))
            required = ipc2221_clearance_mm(voltage)
            if gate == "human":
                if clearance + 1e-9 < required:
                    f.error(
                        "safety.clearance",
                        f"net {net_name!r} at {voltage:g} V needs >= {required:.2f} mm "
                        f"clearance (IPC-2221 external uncoated approximation), has {clearance:.2f} mm",
                    )
            else:
                f.warning(
                    "safety.voltage",
                    f"net {net_name!r} at {voltage:g} V is above 30 V but the profile "
                    f"{profile.get('name')} does not require safety sign-off",
                )
        if _num(net.get("current_a")) > 0:
            required_w = ipc2152_width_mm(
                _num(net.get("current_a")),
                copper_oz=copper_oz,
                external=True,
                delta_t_c=10.0,
            )
            actual_w = _num(netclass_of(spec, net).get("track_width_mm"))
            if actual_w + 1e-9 < required_w:
                f.warning(
                    "thermal.width",
                    f"net {net_name!r} carries {_num(net.get('current_a')):g} A; "
                    f"IPC-2152 (10 C rise, external) suggests >= {required_w:.2f} mm, "
                    f"class width is {actual_w:.2f} mm",
                )

    components = spec.get("components", [])
    if not components:
        f.error("components.empty", "at least one component is required")
    refs: set[str] = set()
    for comp in components if isinstance(components, list) else []:
        ref = str(comp.get("ref", ""))
        if not ref:
            f.error("components.ref", "component ref must not be empty")
            continue
        if ref in refs:
            f.error("components.duplicate", f"duplicate reference {ref!r}")
        refs.add(ref)
        if not REF_RE.match(ref):
            f.warning("components.ref_format", f"reference {ref!r} does not look like a standard designator")
        footprint = str(comp.get("footprint", ""))
        if not footprint:
            f.error("components.footprint", f"{ref} has no footprint")
        elif ":" not in footprint and not footprint.endswith(".kicad_mod"):
            f.error("components.footprint", f"{ref} footprint must be 'Library:Footprint' or a .kicad_mod path")
        pads = comp.get("pads", {})
        if not isinstance(pads, dict):
            f.error("components.pads", f"{ref}.pads must be an object mapping pad number to net")
            pads = {}
        for pad, net_name in pads.items():
            if net_name and net_name not in seen_nets:
                # Nets may be omitted when they are only used by pads; derive
                # them in derive_nets(), but a typo should not silently pass.
                f.error("components.pad_net", f"{ref} pad {pad} references undeclared net {net_name!r}")
        at = comp.get("at")
        if at is not None:
            x = _num(at.get("x_mm"), math.nan)
            y = _num(at.get("y_mm"), math.nan)
            if math.isnan(x) or math.isnan(y):
                f.error("components.at", f"{ref}.at must include x_mm and y_mm")
            elif not (0 <= x <= width and 0 <= y <= height):
                f.error("components.at", f"{ref} is outside the board at ({x}, {y}) mm")
            if str(at.get("side", "front")) not in ("front", "back"):
                f.error("components.side", f"{ref}.at.side must be front or back")
        elif spec.get("placement", {}).get("mode") == "fixed":
            f.error("components.at", f"{ref} has no placement and placement.mode is fixed")
        for keepout in spec.get("placement", {}).get("keepouts", []):
            if at is None:
                continue
            kx = _num(keepout.get("x_mm"))
            ky = _num(keepout.get("y_mm"))
            kw = _num(keepout.get("width_mm"))
            kh = _num(keepout.get("height_mm"))
            x = _num(at.get("x_mm"), math.nan)
            y = _num(at.get("y_mm"), math.nan)
            if not math.isnan(x) and kx <= x <= kx + kw and ky <= y <= ky + kh:
                f.error("components.keepout", f"{ref} is inside keepout {keepout.get('reason', '')!r}")

    # Stackup and impedance.
    stackup = board.get("stackup")
    gate_stackup = profile.get("gates", {}).get("stackup")
    if gate_stackup == "required" and not stackup:
        f.error("stackup.required", f"profile {profile.get('name')} requires board.stackup")
    if stackup:
        f.merge(validate_stackup(spec, stackup, f=True))

    gate_impedance = profile.get("gates", {}).get("impedance")
    if gate_impedance in ("computed", "required"):
        if not stackup:
            f.error("impedance.stackup", "impedance verification requires board.stackup")
        else:
            for cls_name, cls in netclasses.items():
                if cls.get("impedance_ohm") is None:
                    continue
                results = impedance_for_netclass(spec, cls_name)
                if not results:
                    f.error("impedance.compute", f"could not compute impedance for {cls_name}")
                for result in results:
                    target = _num(cls.get("impedance_ohm"))
                    tol = _num(cls.get("impedance_tolerance_pct"), 10.0)
                    if result["z0_ohm"] < target * (1 - tol / 100) or result["z0_ohm"] > target * (1 + tol / 100):
                        f.error(
                            "impedance.target",
                            f"{cls_name} on {result['layer']}: computed {result['z0_ohm']:.1f} ohm, "
                            f"target {target:.1f} +/- {tol:.0f}% - adjust width/stackup",
                        )

    # Fabrication capability checks.
    caps = spec.get("fabrication", {}).get("capabilities", {})
    if caps:
        checks = [
            ("min_track_width_mm", "min_track_mm", "track width"),
            ("min_clearance_mm", "min_clearance_mm", "clearance"),
            ("min_via_diameter_mm", "min_via_diameter_mm", "via diameter"),
            ("min_via_drill_mm", "min_via_drill_mm", "via drill"),
            ("min_annular_ring_mm", "min_annular_ring_mm", "annular ring"),
            ("min_hole_to_hole_mm", "min_hole_to_hole_mm", "hole-to-hole spacing"),
        ]
        for rule_key, cap_key, label in checks:
            if cap_key in caps and _num(rules.get(rule_key)) + 1e-9 < _num(caps[cap_key]):
                f.error("fab." + rule_key, f"design {label} {rules.get(rule_key)} mm is below vendor minimum {caps[cap_key]} mm")
        if "max_width_mm" in caps and width > _num(caps["max_width_mm"]):
            f.error("fab.size", f"board width {width} mm exceeds vendor maximum {caps['max_width_mm']} mm")
        if "max_height_mm" in caps and height > _num(caps["max_height_mm"]):
            f.error("fab.size", f"board height {height} mm exceeds vendor maximum {caps['max_height_mm']} mm")
        if "max_layers" in caps and layers > _int(caps["max_layers"]):
            f.error("fab.layers", f"{layers} layers exceed vendor maximum {caps['max_layers']}")

    if spec.get("routing", {}).get("mode") not in ("freerouting", "none"):
        f.error("routing.mode", "routing.mode must be freerouting or none")
    if spec.get("placement", {}).get("mode") not in ("fixed", "auto"):
        f.error("placement.mode", "placement.mode must be fixed or auto")
    if not spec.get("assumptions"):
        f.warning("assumptions.empty", "no assumptions recorded; document power, current, environment and fab assumptions")

    return f


def validate_stackup(spec: dict[str, Any], stackup: dict[str, Any], f: bool = False) -> Findings:
    findings = Findings()
    layers = stackup.get("layers", [])
    if not isinstance(layers, list) or not layers:
        findings.error("stackup.layers", "stackup.layers must be a non-empty list")
        return findings
    copper = [layer for layer in layers if layer.get("type") == "copper"]
    dielectrics = [layer for layer in layers if layer.get("type") == "dielectric"]
    if len(copper) != _int(spec.get("board", {}).get("layers")):
        findings.error(
            "stackup.copper",
            f"stackup has {len(copper)} copper layers, board.layers is {spec.get('board', {}).get('layers')}",
        )
    if not dielectrics:
        findings.error("stackup.dielectric", "stackup needs at least one dielectric layer")
    if copper and layers[0].get("type") != "copper":
        findings.error("stackup.order", "stackup must start with a copper layer")
    if copper and layers[-1].get("type") != "copper":
        findings.error("stackup.order", "stackup must end with a copper layer")
    total = 0.0
    for index, layer in enumerate(layers):
        thickness = _num(layer.get("thickness_mm"))
        if thickness <= 0:
            findings.error("stackup.thickness", f"stackup layer {index} must have thickness_mm > 0")
        total += thickness
        if layer.get("type") == "dielectric":
            if _num(layer.get("er")) <= 0:
                findings.error("stackup.er", f"dielectric layer {index} must have er > 0")
            if index > 0 and layers[index - 1].get("type") == "dielectric":
                findings.error("stackup.adjacent", f"dielectric layer {index} is adjacent to another dielectric")
    board_thickness = _num(spec.get("board", {}).get("thickness_mm"))
    if board_thickness > 0 and abs(total - board_thickness) > max(0.05, board_thickness * 0.1):
        findings.warning(
            "stackup.total",
            f"stackup thickness {total:.3f} mm differs from board thickness {board_thickness:.3f} mm",
        )
    if stackup.get("confirmed") is not True:
        findings.warning("stackup.confirmed", "stackup is not marked confirmed; confirm with the fabricator before sign-off")
    return findings


def _copper_thickness_mm(spec: dict[str, Any], layer_name: str | None = None) -> float:
    board = spec.get("board", {})
    stackup = board.get("stackup") or {}
    layers = stackup.get("layers", []) if isinstance(stackup, dict) else []
    if layer_name:
        for layer in layers:
            if layer.get("type") == "copper" and str(layer.get("name", "")).lower() == layer_name.lower():
                return _num(layer.get("thickness_mm"), _num(board.get("copper_oz"), 1.0) * 0.035)
    return _num(board.get("copper_oz"), 1.0) * 0.035


def impedance_for_netclass(spec: dict[str, Any], cls_name: str) -> list[dict[str, Any]]:
    """Compute microstrip/stripline impedance for each signal layer.

    These are closed-form approximations (IPC-2141 / Hammerstad-style). They
    are useful for design-space checks, not a substitute for a field solver or
    the fabricator's impedance coupon.
    """
    board = spec.get("board", {})
    stackup = board.get("stackup") or {}
    layers = stackup.get("layers", []) if isinstance(stackup, dict) else []
    cls = spec.get("netclasses", {}).get(cls_name, {})
    width = _num(cls.get("track_width_mm"))
    gap = _num(cls.get("diff_pair_gap_mm"))
    if width <= 0 or not layers:
        return []

    # Build the ordered copper/dielectric physical stack.
    entries: list[dict[str, Any]] = [layer for layer in layers if layer.get("type") in ("copper", "dielectric")]
    copper_indices = [i for i, layer in enumerate(entries) if layer.get("type") == "copper"]
    results: list[dict[str, Any]] = []
    for pos, index in enumerate(copper_indices):
        layer_name = str(entries[index].get("name", f"Cu{pos + 1}"))
        if cls.get("layer") and str(cls["layer"]).lower() != layer_name.lower():
            continue
        t_mm = _num(entries[index].get("thickness_mm"), _copper_thickness_mm(spec, layer_name))
        # Dielectric below and above this copper layer.
        below = entries[index + 1] if index + 1 < len(entries) and entries[index + 1].get("type") == "dielectric" else None
        above = entries[index - 1] if index - 1 >= 0 and entries[index - 1].get("type") == "dielectric" else None
        if pos == 0 and below:
            # Microstrip: one dielectric to the next copper.
            er = _num(below.get("er"), 4.5)
            h = _num(below.get("thickness_mm"))
            z0 = microstrip_z0(width, h, t_mm, er, spec)
            kind = "microstrip"
        elif pos == len(copper_indices) - 1 and above:
            er = _num(above.get("er"), 4.5)
            h = _num(above.get("thickness_mm"))
            z0 = microstrip_z0(width, h, t_mm, er, spec)
            kind = "microstrip"
        else:
            # Stripline: total dielectric spacing between the two reference planes.
            h = 0.0
            er_values = []
            i = index - 1
            while i >= 0 and entries[i].get("type") == "dielectric":
                h += _num(entries[i].get("thickness_mm"))
                er_values.append(_num(entries[i].get("er"), 4.5))
                i -= 1
            i = index + 1
            while i < len(entries) and entries[i].get("type") == "dielectric":
                h += _num(entries[i].get("thickness_mm"))
                er_values.append(_num(entries[i].get("er"), 4.5))
                i += 1
            if h <= 0:
                continue
            er = sum(er_values) / len(er_values) if er_values else 4.5
            z0 = stripline_z0(width, h, t_mm, er)
            kind = "stripline"
        if z0 <= 0:
            continue
        result = {
            "layer": layer_name,
            "kind": kind,
            "width_mm": round(width, 4),
            "z0_ohm": round(z0, 2),
            "target_ohm": _num(cls.get("impedance_ohm")),
        }
        if cls.get("diff_pair") and gap > 0:
            result["zdiff_ohm"] = round(differential_z0(z0, gap, h_for_result(entries, index)), 2)
            result["gap_mm"] = gap
        results.append(result)
    return results


def h_for_result(entries: list[dict[str, Any]], index: int) -> float:
    for direction in (1, -1):
        i = index + direction
        while 0 <= i < len(entries) and entries[i].get("type") == "dielectric":
            h = _num(entries[i].get("thickness_mm"))
            if h > 0:
                return h
            i += direction
    return 0.1


def microstrip_z0(width_mm: float, h_mm: float, t_mm: float, er: float, spec: dict[str, Any]) -> float:
    if width_mm <= 0 or h_mm <= 0:
        return 0.0
    w = width_mm * 39.3700787
    h = h_mm * 39.3700787
    t = t_mm * 39.3700787
    u = w / h
    if u <= 0:
        return 0.0
    er_eff = (er + 1) / 2 + (er - 1) / 2 / math.sqrt(1 + 12 / u)
    if u < 1:
        z0 = 60 / math.sqrt(er_eff) * math.log(8 / u + u / 4)
    else:
        z0 = 120 * math.pi / (math.sqrt(er_eff) * (u + 1.393 + 0.667 * math.log(u + 1.444)))
    # Finite-thickness correction.
    if t > 0:
        z0 *= 1 - 0.02 * min(t / h, 0.5)
    return z0


def stripline_z0(width_mm: float, h_mm: float, t_mm: float, er: float) -> float:
    if width_mm <= 0 or h_mm <= 0:
        return 0.0
    w = width_mm * 39.3700787
    h = h_mm * 39.3700787
    t = t_mm * 39.3700787
    b = 2 * h + t
    if w <= 0:
        return 0.0
    return 60 / math.sqrt(er) * math.log(4 * b / (0.67 * math.pi * w * (1 + t / w)))


def differential_z0(z0: float, gap_mm: float, h_mm: float) -> float:
    if z0 <= 0 or gap_mm <= 0 or h_mm <= 0:
        return 0.0
    s = gap_mm / h_mm
    return 2 * z0 * (1 - 0.48 * math.exp(-0.96 * s))


def ipc2152_width_mm(current_a: float, copper_oz: float = 1.0, external: bool = True, delta_t_c: float = 10.0) -> float:
    """Approximate IPC-2152 conductor width for a current and temperature rise.

    Returns the required width in mm for a single conductor. This is a
    conservative design aid, not a thermal simulation.
    """
    if current_a <= 0:
        return 0.0
    k = 0.048 if external else 0.024
    delta = max(delta_t_c, 1.0)
    area_mil2 = (current_a / (k * delta ** 0.44)) ** (1 / 0.725)
    thickness_mil = max(copper_oz, 0.01) * 1.378
    width_mil = area_mil2 / thickness_mil
    return width_mil / 39.3700787


def ipc2221_clearance_mm(voltage_v: float) -> float:
    """IPC-2221 external uncoated clearance approximation.

    This is a coarse table used only for the high-voltage gate. Real designs
    must use the applicable standard, pollution degree, altitude and coating.
    """
    if voltage_v <= 30:
        return 0.1
    if voltage_v <= 150:
        return 0.6
    if voltage_v <= 300:
        return 1.25
    if voltage_v <= 500:
        return 2.5
    return 2.5 + 0.005 * (voltage_v - 500)


def derive_nets(spec: dict[str, Any]) -> dict[str, Any]:
    """Create net declarations for pad nets that are not declared explicitly."""
    import copy

    s = copy.deepcopy(spec)
    nets = s.setdefault("nets", [])
    existing = {net.get("name") for net in nets if isinstance(net, dict)}
    for comp in s.get("components", []):
        for net_name in (comp.get("pads") or {}).values():
            if net_name and net_name not in existing:
                nets.append({"name": net_name})
                existing.add(net_name)
    return s


def _gate_passed(value: Any) -> bool:
    """Strict gate predicate.

    ``1 == True`` in Python, so membership tests like ``value in (0, True)``
    silently accept a failure count of 1. Treat booleans as booleans, zero
    counts as pass, and everything else as failure.
    """
    if value is True:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)) and value == 0:
        return True
    return value == "pass"


def gate_report(
    spec: dict[str, Any],
    profile: dict[str, Any],
    findings: Findings,
    stage_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine spec findings, stage results and sign-off into a release report."""
    stage_results = stage_results or {}
    gates = profile.get("gates", {})
    signoff = spec.get("signoff", {})
    completed = set(signoff.get("completed", []))
    required_signoff = list(profile.get("human_signoff", []))

    automated: dict[str, Any] = {}
    automated["spec"] = {"ok": findings.ok, "errors": len(findings.errors), "warnings": len(findings.warnings)}
    for key in ("structural", "drc", "unconnected", "dfm", "netlist_parity", "stackup", "impedance", "length_match", "fab"):
        if key in stage_results:
            automated[key] = stage_results[key]
    automated["thermal"] = stage_results.get("thermal", "reported")
    automated["emc"] = stage_results.get("emc", "not_verified")

    failures: list[str] = []
    if not findings.ok:
        failures.append("spec")
    for key, gate in gates.items():
        if gate in ("off", "report", "human"):
            continue
        value = stage_results.get(key)
        if value is None:
            # A gate not exercised by this run is a failure when the profile
            # requires a hard pass or a computed check.
            if gate in ("error", "zero", "required", "computed"):
                failures.append(f"{key}:not_run")
            continue
        if gate in ("error", "zero", "required", "computed") and not _gate_passed(value):
            failures.append(f"{key}:{value}")
    missing_signoff = [item for item in required_signoff if item not in completed]
    if missing_signoff:
        failures.append("signoff:" + ",".join(missing_signoff))

    professional = not failures
    return {
        "name": spec.get("name"),
        "revision": spec.get("revision"),
        "profile": profile.get("name"),
        "automation": profile.get("automation", "gated"),
        "professional_claim": professional,
        "failures": failures,
        "required_signoff": required_signoff,
        "completed_signoff": sorted(completed),
        "missing_signoff": missing_signoff,
        "gates": gates,
        "automated": automated,
        "findings": findings.to_dict(),
        "assumptions": spec.get("assumptions", []),
        "notes": profile.get("notes", []),
        "disclaimer": (
            "A professional claim means the automated gates listed in this report passed and "
            "the required human sign-offs are recorded. It does not certify performance that "
            "the pipeline cannot measure (RF, EMC, thermal, safety, flex fatigue)."
        ),
    }


# ---------------------------------------------------------------------------
# KiCad netlist import
# ---------------------------------------------------------------------------

def _import_check_pcb():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import check_pcb  # type: ignore

    return check_pcb


def parse_kicad_netlist(path: str | Path) -> dict[str, Any]:
    """Parse a KiCad netlist (``.net``) into components and pad->net maps."""
    cp = _import_check_pcb()
    text = Path(path).read_text(encoding="utf-8")
    root = cp.parse(text)
    # parse() returns a wrapper SList containing the top-level expression(s).
    if len(root.items) == 1 and isinstance(root.items[0], cp.SList):
        root = root.items[0]
    if cp.head(root) != "export":
        raise ValueError(f"{path}: expected a KiCad netlist starting with (export ...)")

    components: dict[str, dict[str, Any]] = {}
    comps_node = cp.sub(root, "components")
    if comps_node is not None:
        for comp in cp.subs(comps_node, "comp"):
            ref = cp.atom(cp.sub(comp, "ref")) if cp.sub(comp, "ref") else ""
            if not ref:
                continue
            value = cp.atom(cp.sub(comp, "value")) if cp.sub(comp, "value") else ""
            footprint = cp.atom(cp.sub(comp, "footprint")) if cp.sub(comp, "footprint") else ""
            components[ref] = {
                "ref": ref,
                "value": value,
                "footprint": footprint,
                "pads": {},
            }

    nets: list[dict[str, Any]] = []
    nets_node = cp.sub(root, "nets")
    if nets_node is not None:
        for net in cp.subs(nets_node, "net"):
            name_node = cp.sub(net, "name")
            name = cp.atom(name_node) if name_node else ""
            if not name:
                continue
            nodes = []
            for node in cp.subs(net, "node"):
                ref = cp.atom(cp.sub(node, "ref")) if cp.sub(node, "ref") else ""
                pin = cp.atom(cp.sub(node, "pin")) if cp.sub(node, "pin") else ""
                if ref and pin:
                    nodes.append({"ref": ref, "pin": pin})
                    if ref in components:
                        components[ref]["pads"][pin] = name
            nets.append({"name": name, "nodes": nodes})

    return {
        "components": list(components.values()),
        "nets": nets,
        "source": str(path),
        "format": "kicad-netlist",
    }


def merge_netlist(overlay: dict[str, Any], netlist: dict[str, Any]) -> dict[str, Any]:
    """Merge a KiCad netlist into a spec overlay.

    The netlist is authoritative for components, footprints and pad->net
    connectivity. The overlay is authoritative for board mechanics, rules,
    netclasses, placement and routing.
    """
    import copy

    spec = copy.deepcopy(overlay)
    overlay_by_ref = {comp.get("ref"): comp for comp in spec.get("components", [])}
    netlist_refs = {comp["ref"] for comp in netlist.get("components", [])}
    excluded_refs = set(netlist.get("excluded", []))

    merged_components: list[dict[str, Any]] = []
    for comp in netlist.get("components", []):
        ref = comp["ref"]
        base = copy.deepcopy(overlay_by_ref.get(ref, {}))
        # A SPICE netlist has no pad names. When the importer cannot derive
        # them, keep pad->net assignments from the spec overlay instead of
        # silently dropping them.
        pads = comp.get("pads") or base.get("pads", {})
        base.update(
            {
                "ref": ref,
                "value": comp.get("value", base.get("value", "")),
                "footprint": comp.get("footprint") or base.get("footprint", ""),
                "pads": pads,
            }
        )
        merged_components.append(base)
    for ref, comp in overlay_by_ref.items():
        if ref not in netlist_refs:
            # Keep mechanical parts (mounting holes) that are intentionally
            # absent from the schematic, and parts the importer deliberately
            # excluded (for example simulation-only sources).
            if comp.get("pads") and ref not in excluded_refs:
                raise ValueError(f"overlay component {ref} has pads but is missing from the netlist")
            merged_components.append(copy.deepcopy(comp))
    spec["components"] = merged_components

    overlay_nets = {net.get("name"): net for net in spec.get("nets", []) if isinstance(net, dict)}
    merged_nets = []
    for net in netlist.get("nets", []):
        name = net["name"]
        merged = copy.deepcopy(overlay_nets.get(name, {"name": name}))
        merged["name"] = name
        merged.setdefault("class", DEFAULT_NETCLASS)
        merged_nets.append(merged)
    spec["nets"] = merged_nets
    spec["netlist_source"] = netlist.get("source")
    spec["import"] = {
        "format": netlist.get("format", "kicad-netlist"),
        "source": netlist.get("source"),
        "warnings": list(netlist.get("warnings", [])),
        "excluded": list(netlist.get("excluded", [])),
    }
    return spec


# ---------------------------------------------------------------------------
# LTspice / SPICE netlist import
# ---------------------------------------------------------------------------

LTSPICE_SIMULATION_PREFIXES = ("V", "I", "B", "E", "F", "G", "H", "K", "S", "W", "T", "O", "U", "A")
LTSPICE_SEMANTIC_PINS = {
    "Q": ("c", "b", "e", "s"),
    "M": ("d", "g", "s", "b"),
    "J": ("d", "g", "s"),
    "D": ("a", "k"),
}
LTSPICE_PIN_ALIASES = {
    "anode": "a",
    "cathode": "k",
    "collector": "c",
    "base": "b",
    "emitter": "e",
    "drain": "d",
    "gate": "g",
    "source": "s",
    "bulk": "b",
}
LTSPICE_KNOWN_PREFIXES = set("RCLDQMJXVIBEFGHKSWTOUA")


def _spice_logical_lines(text: str) -> list[str]:
    """Join continuation lines and drop comments/blank lines."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+"):
            if lines:
                lines[-1] = f"{lines[-1]} {line[1:].strip()}"
            continue
        # LTspice uses ';' for inline comments.
        if ";" in line:
            line = line.split(";", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def _spice_pin_map_entry(pin_map: dict[str, Any], ref: str, prefix: str) -> Any:
    refs = pin_map.get("refs") or {}
    defaults = pin_map.get("defaults") or {}
    if ref in refs:
        return refs[ref]
    if prefix in defaults:
        return defaults[prefix]
    if prefix.lower() in defaults:
        return defaults[prefix.lower()]
    return None


def _spice_normalise_mapping(prefix: str, mapping: Any) -> Any:
    if not isinstance(mapping, dict) or prefix == "X":
        return mapping
    normalised: dict[str, Any] = {}
    for key, value in mapping.items():
        key_str = str(key)
        lowered = key_str.lower()
        normalised[key_str if lowered.isdigit() else LTSPICE_PIN_ALIASES.get(lowered, lowered)] = value
    return normalised


def _spice_numeric_mapping(mapping: dict[str, Any]) -> list[str] | None:
    keys = [str(key) for key in mapping]
    if keys and all(key.isdigit() for key in keys):
        return [str(mapping[key]) for key in sorted(mapping, key=lambda key: int(str(key)))]
    return None


def _spice_pad_values(value: Any) -> list[str]:
    """Expand a pin-map value into one or more pad numbers."""
    if isinstance(value, (list, tuple)):
        pads = [str(pad).strip() for pad in value]
    else:
        pads = [str(value).strip()]
    if not pads or any(not pad for pad in pads):
        raise ValueError("pin map pad numbers must be non-empty")
    return pads


def _spice_assign_pad(pads: dict[str, str], pad: str, node: str, *, ref: str) -> None:
    previous = pads.get(pad)
    if previous is not None and previous != node:
        raise ValueError(
            f"{ref}: pad {pad} maps to both {previous!r} and {node!r}; use one pad per net"
        )
    pads[pad] = node


def _spice_apply_mapping(
    *,
    prefix: str,
    ref: str,
    nodes: list[str],
    mapping: Any,
    subckt_pins: list[str] | None = None,
) -> dict[str, str]:
    """Return pad number -> SPICE node for one element.

    A semantic pin (for example a SOT-223 collector or drain tab) may map to
    more than one pad by using a list in the pin map.
    """
    if mapping is None:
        if prefix in ("R", "C", "L", "D"):
            if len(nodes) != 2:
                raise ValueError(f"{ref}: expected 2 nodes for a {prefix} element, found {len(nodes)}")
            return {"1": nodes[0], "2": nodes[1]}
        pins = LTSPICE_SEMANTIC_PINS.get(prefix, ())
        hint = ", ".join(pins) if pins else "pads"
        example = (
            '{"c": "2", "b": "1", "e": "3"}'
            if prefix == "Q"
            else '{"d": "3", "g": "1", "s": "2"}'
            if prefix in ("M", "J")
            else '["1", "2", "3", "4"]'
        )
        raise ValueError(
            f"{ref}: no pin map for {len(nodes)}-node {prefix} element ({hint}). "
            f'Add {{"refs": {{"{ref}": {example}}}}} to the pin map.'
        )

    if isinstance(mapping, (list, tuple)):
        pads = [str(pad) for pad in mapping]
        if len(pads) != len(nodes):
            raise ValueError(f"{ref}: pin map has {len(pads)} pads but the netlist has {len(nodes)} nodes")
        result: dict[str, str] = {}
        for pad, node in zip(pads, nodes):
            _spice_assign_pad(result, pad, node, ref=ref)
        return result

    if not isinstance(mapping, dict):
        raise ValueError(f"{ref}: pin map entry must be a list or an object")

    numeric = _spice_numeric_mapping(mapping)
    if numeric is not None:
        if len(numeric) != len(nodes):
            raise ValueError(f"{ref}: pin map has {len(numeric)} pads but the netlist has {len(nodes)} nodes")
        result = {}
        for pad, node in zip(numeric, nodes):
            _spice_assign_pad(result, pad, node, ref=ref)
        return result

    # X instances: keys are subcircuit port names, order comes from .subckt.
    if prefix == "X":
        if subckt_pins is None:
            raise ValueError(
                f"{ref}: a port-name pin map needs an inline .subckt definition; "
                "use an ordered list of pad numbers instead"
            )
        if len(subckt_pins) != len(nodes):
            raise ValueError(
                f"{ref}: subcircuit has {len(subckt_pins)} pins but the netlist has {len(nodes)} nodes"
            )
        missing = [pin for pin in subckt_pins if pin not in mapping]
        if missing:
            raise ValueError(f"{ref}: pin map is missing subcircuit pin(s): {', '.join(missing)}")
        pads: dict[str, str] = {}
        for pin, node in zip(subckt_pins, nodes):
            for pad in _spice_pad_values(mapping[pin]):
                _spice_assign_pad(pads, pad, node, ref=ref)
        return pads

    order = LTSPICE_SEMANTIC_PINS.get(prefix)
    if order is None:
        raise ValueError(f"{ref}: unsupported pin map for {prefix} element")
    unknown = [key for key in mapping if key not in order]
    if unknown:
        raise ValueError(
            f"{ref}: unknown pin name(s) {', '.join(map(str, unknown))}; expected one of: {', '.join(order)}"
        )
    expected = [pin for pin in order if pin in mapping]
    if not expected:
        raise ValueError(f"{ref}: pin map keys must include one of: {', '.join(order)}")
    if len(expected) != len(nodes):
        raise ValueError(
            f"{ref}: pin map names {len(expected)} pins ({', '.join(expected)}) "
            f"but the netlist has {len(nodes)} nodes"
        )
    pads = {}
    for pin, node in zip(expected, nodes):
        for pad in _spice_pad_values(mapping[pin]):
            _spice_assign_pad(pads, pad, node, ref=ref)
    return pads


def parse_ltspice_netlist(
    path: str | Path,
    pin_map: dict[str, Any] | None = None,
    node_aliases: dict[str, str] | None = None,
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Parse an LTspice/SPICE netlist into the pipeline's netlist shape.

    LTspice netlists carry no footprints and no pad numbers. Two-terminal
    primitives default to pads 1/2; multi-pin primitives need an explicit
    ``pin_map``. ``node_aliases`` maps SPICE node names (for example ``N001``
    or ``0``) to meaningful PCB net names so netclasses and voltage rules
    still apply.

    Simulation-only elements (voltage/current sources, behavioral and
    controlled sources, coupling, switches, transmission lines) are excluded
    by default. Use ``pin_map.include`` to force one onto the board and
    ``pin_map.exclude`` to drop anything else.

    With ``strict=False``, elements that need a pin map are returned in the
    ``unresolved`` list instead of raising. This lets the pipeline ask the user
    for the missing pin maps and then parse again.
    """
    if pin_map is not None and not isinstance(pin_map, dict):
        raise ValueError("pin_map must be a JSON object")
    if node_aliases is not None and not isinstance(node_aliases, dict):
        raise ValueError("node_aliases must be a JSON object")
    pin_map = pin_map or {}
    for key in ("refs", "defaults"):
        if key in pin_map and not isinstance(pin_map[key], dict):
            raise ValueError(f"pin_map.{key} must be a JSON object")
    for key in ("include", "exclude"):
        if key in pin_map and not isinstance(pin_map[key], list):
            raise ValueError(f"pin_map.{key} must be a JSON array")
    aliases = {str(key): str(value) for key, value in (node_aliases or {}).items()}

    raw = Path(path).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        # Some LTspice exports are UTF-16 LE/BE.
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    lines = _spice_logical_lines(text)

    # Inline .model names let us find the model token without guessing whether
    # a BJT/MOSFET line has a fourth (substrate/bulk) node.
    models: set[str] = set()
    for line in lines:
        if line.lower().startswith(".model"):
            tokens = line.split()
            if len(tokens) >= 2:
                models.add(tokens[1].lower())

    subckt_stack: list[str] = []
    subckts: dict[str, list[str]] = {}
    components: dict[str, dict[str, Any]] = {}
    # Seed with user excludes so an overlay-only part (for example a connector
    # that is not simulated) is marked as intentionally absent.
    excluded: list[str] = [str(ref) for ref in pin_map.get("exclude", [])]
    warnings: list[str] = []
    unresolved: list[dict[str, Any]] = []
    first_line = True
    include_refs = {str(ref) for ref in pin_map.get("include", [])}
    exclude_refs = {str(ref) for ref in pin_map.get("exclude", [])}

    for line in lines:
        lowered = line.lower()

        if lowered.startswith(".subckt"):
            first_line = False
            tokens = line.split()
            if len(tokens) < 2:
                warnings.append(f"ignored malformed .subckt line: {line}")
                continue
            name = tokens[1]
            pins: list[str] = []
            for token in tokens[2:]:
                if "=" in token or token.lower() in ("params:", "param:"):
                    break
                pins.append(token)
            if not subckt_stack:
                subckts[name] = pins
            subckt_stack.append(name)
            continue

        if lowered.startswith(".ends"):
            first_line = False
            if subckt_stack:
                subckt_stack.pop()
            continue

        if lowered.startswith(".include") or lowered.startswith(".lib"):
            first_line = False
            warnings.append(f"external include not resolved: {line}")
            continue

        if line.startswith("."):
            first_line = False
            continue

        if subckt_stack:
            continue

        tokens = line.split()
        name = tokens[0]
        prefix = name[0].upper()
        is_first = first_line
        if prefix not in LTSPICE_KNOWN_PREFIXES:
            if first_line:
                # LTspice writes the schematic title as the first line.
                first_line = False
                continue
            warnings.append(f"ignored unrecognised line: {line}")
            continue
        first_line = False

        if name in exclude_refs:
            if name not in excluded:
                excluded.append(name)
            continue
        if prefix in LTSPICE_SIMULATION_PREFIXES and name not in include_refs:
            if name not in excluded:
                excluded.append(name)
            continue

        args = tokens[1:]
        mapping = _spice_normalise_mapping(prefix, _spice_pin_map_entry(pin_map, name, prefix))

        # Work out how many leading tokens are nodes.
        numeric_mapping = _spice_numeric_mapping(mapping) if isinstance(mapping, dict) else None
        if prefix == "X":
            if isinstance(mapping, (list, tuple)):
                node_count = len(mapping)
            elif numeric_mapping is not None:
                node_count = len(numeric_mapping)
            elif isinstance(mapping, dict):
                if not subckts:
                    raise ValueError(
                        f"{name}: a port-name pin map needs an inline .subckt definition"
                    )
                matches = [index for index, token in enumerate(args) if token in subckts]
                if len(matches) != 1:
                    raise ValueError(
                        f"{name}: cannot identify the subcircuit name; use an ordered pad list "
                        "in the pin map"
                    )
                node_count = matches[0]
            else:
                if not strict:
                    matches = [token for token in args if token in subckts]
                    subckt_name = matches[0] if len(matches) == 1 else None
                    unresolved.append(
                        {
                            "ref": name,
                            "prefix": "X",
                            "args": list(args),
                            "line": line,
                            "subckt_name": subckt_name,
                            "subckt_pins": subckts.get(subckt_name) if subckt_name else None,
                        }
                    )
                    continue
                raise ValueError(
                    f"{name}: no pin map for X (subcircuit) element. "
                    f'Add an ordered pad list, for example {{"refs": {{"{name}": ["1", "2", "3", "4"]}}}}.'
                )
            if len(args) <= node_count:
                if not is_first:
                    warnings.append(f"ignored malformed X instance (missing subcircuit name): {line}")
                continue
        elif isinstance(mapping, (list, tuple)):
            node_count = len(mapping)
        elif numeric_mapping is not None:
            node_count = len(numeric_mapping)
        elif prefix in ("R", "C", "L"):
            node_count = 2
        elif prefix == "D":
            model_index = next((index for index in range(2, len(args)) if args[index].lower() in models), None)
            node_count = model_index if model_index is not None else 2
        elif prefix in ("Q", "M", "J"):
            model_index = next((index for index in range(3, len(args)) if args[index].lower() in models), None)
            if model_index is not None:
                node_count = model_index
            else:
                bulk = "s" if prefix == "Q" else "b"
                node_count = 4 if isinstance(mapping, dict) and bulk in mapping else 3
        else:
            node_count = 2

        if len(args) < node_count:
            if not is_first:
                warnings.append(f"ignored malformed line: {line}")
            continue
        nodes = args[:node_count]
        value_token = args[node_count] if len(args) > node_count else ""

        subckt_name = None
        subckt_pins = None
        if prefix == "X":
            if isinstance(mapping, (list, tuple)) or numeric_mapping is not None:
                subckt_name = value_token
            else:
                matches = [token for token in args if token in subckts]
                subckt_name = matches[0] if matches else None
            if subckt_name in subckts:
                subckt_pins = subckts[subckt_name]

        # A multi-pin part with no pin map cannot be placed on a footprint yet.
        # In tolerant mode, hand it back to the caller so the pipeline can ask
        # the user for a pinout instead of failing the whole import.
        needs_pin_map = mapping is None and (
            prefix in ("Q", "M", "J")
            or (prefix == "D" and len(nodes) != 2)
        )
        if needs_pin_map:
            if not strict:
                unresolved.append(
                    {
                        "ref": name,
                        "prefix": prefix,
                        "nodes": list(nodes),
                        "value": value_token,
                        "line": line,
                    }
                )
                continue
            # Strict mode falls through to _spice_apply_mapping for the
            # detailed error message.

        pads = _spice_apply_mapping(
            prefix=prefix,
            ref=name,
            nodes=nodes,
            mapping=mapping,
            subckt_pins=subckt_pins,
        )

        if name in components:
            raise ValueError(f"duplicate component reference in netlist: {name}")

        pad_nets: dict[str, str] = {}
        for pad, node in pads.items():
            net_name = aliases.get(node, "GND" if node == "0" else node)
            if not net_name or any(char.isspace() for char in net_name):
                raise ValueError(
                    f"{name} pad {pad} maps to invalid net name {net_name!r}; "
                    "add a node_aliases entry"
                )
            pad_nets[str(pad)] = net_name

        components[name] = {
            "ref": name,
            "value": subckt_name if prefix == "X" and subckt_name else value_token,
            "footprint": "",
            "pads": pad_nets,
            "spice_prefix": prefix,
        }

    net_nodes: dict[str, list[dict[str, str]]] = {}
    for comp in components.values():
        for pad, net_name in comp["pads"].items():
            net_nodes.setdefault(net_name, []).append({"ref": comp["ref"], "pin": pad})

    def _node_key(node: dict[str, str]) -> tuple[str, int, str]:
        pin = node["pin"]
        return (node["ref"], int(pin) if pin.isdigit() else 10**9, pin)

    nets = [
        {"name": net_name, "nodes": sorted(nodes, key=_node_key)}
        for net_name, nodes in sorted(net_nodes.items())
    ]

    return {
        "components": list(components.values()),
        "nets": nets,
        "source": str(path),
        "format": "ltspice",
        "warnings": warnings,
        "excluded": excluded,
        "unresolved": unresolved,
        "subcircuits": subckts,
    }


def export_netlist_from_schematic(schematic: str | Path, output: str | Path, kicad_cli: str = "kicad-cli") -> Path:
    """Use kicad-cli to export a KiCad netlist from a schematic."""
    import subprocess

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [kicad_cli, "sch", "export", "netlist", "-o", str(out), str(schematic)]
    subprocess.run(cmd, check=True)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_spec(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = apply_defaults(derive_nets(load_json(path)))
    profile = load_profile(str(spec.get("profile", "")))
    return spec, profile


def _print_findings(findings: Findings, verbose: bool = True) -> None:
    for item in findings.errors:
        print(f"ERROR [{item['code']}] {item['message']}", file=sys.stderr)
    if verbose:
        for item in findings.warnings:
            print(f"WARN  [{item['code']}] {item['message']}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and resolve a PCB design spec.")
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate a spec against its profile")
    v.add_argument("spec")
    v.add_argument("--quiet", action="store_true", help="suppress warnings")
    v.add_argument("--json", action="store_true", help="print findings as JSON")

    e = sub.add_parser("explain", help="print resolved design intent and calculated limits")
    e.add_argument("spec")

    n = sub.add_parser("netlist", help="merge a KiCad netlist into a spec overlay")
    n.add_argument("netlist")
    n.add_argument("--spec", required=True, help="overlay spec with board/rules/placement")
    n.add_argument("-o", "--output", required=True)
    n.add_argument("--validate", action="store_true")

    s = sub.add_parser("schematic", help="export a netlist from a KiCad schematic, then merge it")
    s.add_argument("schematic")
    s.add_argument("--spec", required=True)
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--kicad-cli", default=os.environ.get("KICAD_CLI", "kicad-cli"))

    l = sub.add_parser(
        "ltspice",
        help="import an LTspice/SPICE netlist, then merge it",
        description=(
            "Import an LTspice/SPICE netlist. This low-level command is "
            "non-interactive; use design_pipeline.py --ltspice for visual "
            "pin-map, footprint and placement questions."
        ),
    )
    l.add_argument("netlist")
    l.add_argument("--spec", required=True, help="overlay spec with board/rules/placement")
    l.add_argument("-o", "--output", required=True)
    l.add_argument("--pin-map", help="JSON pin map for multi-pin parts")
    l.add_argument("--node-aliases", help="JSON map of SPICE node names to PCB net names")
    l.add_argument("--validate", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "validate":
        spec, profile = _load_spec(args.spec)
        findings = validate_spec(spec, profile)
        if args.json:
            print(json.dumps(findings.to_dict(), indent=2))
        else:
            _print_findings(findings, verbose=not args.quiet)
            print(f"{len(findings.errors)} error(s), {len(findings.warnings)} warning(s)")
        return 0 if findings.ok else 1

    if args.command == "explain":
        spec, profile = _load_spec(args.spec)
        findings = validate_spec(spec, profile)
        print(f"Design: {spec.get('name')} rev {spec.get('revision')}")
        print(f"Profile: {profile.get('name')} ({profile.get('automation')})")
        print(f"Board: {spec['board']['width_mm']} x {spec['board']['height_mm']} mm, {spec['board']['layers']} layers")
        print("Copper layers:", ", ".join(copper_layers(spec)))
        print("Components:", len(spec.get("components", [])))
        print("Nets:", ", ".join(net.get("name", "") for net in spec.get("nets", [])))
        print("Required sign-off:", ", ".join(profile.get("human_signoff", [])) or "none")
        for cls_name, cls in spec.get("netclasses", {}).items():
            print(
                f"  {cls_name}: {cls.get('track_width_mm')} mm / {cls.get('clearance_mm')} mm"
                + (f", {cls.get('impedance_ohm')} ohm" if cls.get("impedance_ohm") else "")
            )
            if cls.get("impedance_ohm"):
                for result in impedance_for_netclass(spec, cls_name):
                    print(
                        f"    {result['layer']} {result['kind']}: {result['z0_ohm']} ohm "
                        f"(target {result['target_ohm']} ohm)"
                    )
        for net in spec.get("nets", []):
            current = _num(net.get("current_a"))
            if current:
                print(
                    f"  current {net.get('name')}: {current} A -> "
                    f">= {ipc2152_width_mm(current, _num(spec['board'].get('copper_oz'), 1.0)):.3f} mm "
                    f"(IPC-2152, 10 C rise)"
                )
        _print_findings(findings)
        return 0 if findings.ok else 1

    if args.command in ("netlist", "schematic", "ltspice"):
        overlay = apply_defaults(load_json(args.spec))
        if args.command == "ltspice":
            pin_map = load_json(args.pin_map) if args.pin_map else None
            node_aliases = load_json(args.node_aliases) if args.node_aliases else None
            netlist = parse_ltspice_netlist(args.netlist, pin_map=pin_map, node_aliases=node_aliases)
            for warning in netlist.get("warnings", []):
                print(f"WARN  [ltspice] {warning}", file=sys.stderr)
        else:
            netlist_path = args.netlist if args.command == "netlist" else None
            if args.command == "schematic":
                netlist_path = str(Path(args.output).with_suffix(".net"))
                export_netlist_from_schematic(args.schematic, netlist_path, kicad_cli=args.kicad_cli)
            netlist = parse_kicad_netlist(netlist_path)
        merged = apply_defaults(derive_nets(merge_netlist(overlay, netlist)))
        profile = load_profile(str(merged.get("profile", "")))
        findings = validate_spec(merged, profile)
        _print_findings(findings)
        if not findings.ok and getattr(args, "validate", False):
            return 1
        write_json(args.output, merged)
        print(f"wrote {args.output}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
