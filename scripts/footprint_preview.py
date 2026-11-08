#!/usr/bin/env python3
"""Render KiCad footprints as small SVG previews for the visual asker.

The preview is generated from the footprint's real geometry (pads, silkscreen,
courtyard and drills), not from a generic package icon. Resolution order:

1. an explicit ``.kicad_mod`` path;
2. a local KiCad footprint library (``KICAD9_FOOTPRINT_DIR`` or
   ``/usr/share/kicad/footprints``);
3. the official KiCad footprint library on GitHub, cached locally.

The GitHub fallback makes the picker useful on Termux and other hosts without
KiCad installed. Use ``--no-fetch`` to keep the tool fully offline.

CLI:
    python3 scripts/footprint_preview.py "Resistor_SMD:R_0805_2012Metric"
    python3 scripts/footprint_preview.py "Resistor_SMD:R_0805_2012Metric" --svg preview.svg
    python3 scripts/footprint_preview.py --list-common resistor
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import check_pcb  # noqa: E402
import pcb_build  # noqa: E402

CACHE_DIR = Path(
    os.environ.get("PCB_FOOTPRINT_CACHE", Path.home() / ".cache" / "pcb-design-skill" / "footprints")
)
GITHUB_RAW = "https://raw.githubusercontent.com/KiCad/kicad-footprints/{ref}/{library}.pretty/{name}.kicad_mod"
GITHUB_REFS = ("master", "9.0", "8.0", "7.0")
SAFE_PART = re.compile(r"^[A-Za-z0-9_.+-]+$")

BOARD = "#0E3B2E"
BOARD_EDGE = "#1B5A45"
COPPER = "#D99A4E"
COPPER_EDGE = "#F2C879"
SILK = "#F7F3E8"
COURTYARD = "#7FD1FF"
HOLE = "#08120E"
MUTED = "#9DB8A9"


COMMON_FOOTPRINTS: dict[str, list[tuple[str, str]]] = {
    "resistor": [
        ("Resistor_SMD:R_0402_1005Metric", "0402 · 1.0 × 0.5 mm"),
        ("Resistor_SMD:R_0603_1608Metric", "0603 · 1.6 × 0.8 mm"),
        ("Resistor_SMD:R_0805_2012Metric", "0805 · 2.0 × 1.25 mm"),
        ("Resistor_SMD:R_1206_3216Metric", "1206 · 3.2 × 1.6 mm"),
        (
            "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",
            "Axial THT · 7.62 mm pitch",
        ),
    ],
    "capacitor": [
        ("Capacitor_SMD:C_0402_1005Metric", "0402 · 1.0 × 0.5 mm"),
        ("Capacitor_SMD:C_0603_1608Metric", "0603 · 1.6 × 0.8 mm"),
        ("Capacitor_SMD:C_0805_2012Metric", "0805 · 2.0 × 1.25 mm"),
        ("Capacitor_SMD:C_1206_3216Metric", "1206 · 3.2 × 1.6 mm"),
        ("Capacitor_THT:CP_Radial_D5.0mm_P2.00mm", "Radial electrolytic · 5 mm"),
    ],
    "led": [
        ("LED_SMD:LED_0603_1608Metric", "0603 SMD LED"),
        ("LED_SMD:LED_0805_2012Metric", "0805 SMD LED"),
        ("LED_SMD:LED_1206_3216Metric", "1206 SMD LED"),
        ("LED_THT:LED_D3.0mm", "3 mm THT LED"),
        ("LED_THT:LED_D5.0mm", "5 mm THT LED"),
    ],
    "header": [
        ("Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", "1×2 · 2.54 mm"),
        ("Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical", "1×3 · 2.54 mm"),
        ("Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical", "1×4 · 2.54 mm"),
        ("Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", "1×6 · 2.54 mm"),
        ("Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical", "2×5 · 2.54 mm"),
    ],
    "sot": [
        ("Package_TO_SOT_SMD:SOT-23", "SOT-23 · 3 pins"),
        ("Package_TO_SOT_SMD:SOT-23-5", "SOT-23-5 · 5 pins"),
        ("Package_TO_SOT_SMD:SOT-223", "SOT-223 · 4 pins + tab"),
    ],
    "soic": [
        ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8 · 1.27 mm"),
        ("Package_SO:SOIC-14_3.9x8.7mm_P1.27mm", "SOIC-14 · 1.27 mm"),
        ("Package_SO:SOIC-16_3.9x9.9mm_P1.27mm", "SOIC-16 · 1.27 mm"),
    ],
    "qfp": [
        ("Package_QFP:TQFP-32_7x7mm_P0.8mm", "TQFP-32 · 0.8 mm"),
        ("Package_QFP:TQFP-44_10x10mm_P0.8mm", "TQFP-44 · 0.8 mm"),
        ("Package_QFP:TQFP-48_7x7mm_P0.5mm", "TQFP-48 · 0.5 mm"),
    ],
    "qfn": [
        (
            "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm",
            "QFN-16 · 3 × 3 mm",
        ),
        (
            "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.1x3.1mm",
            "QFN-32 · 5 × 5 mm",
        ),
    ],
    "dip": [
        ("Package_DIP:DIP-8_W7.62mm", "DIP-8 · 7.62 mm"),
        ("Package_DIP:DIP-14_W7.62mm", "DIP-14 · 7.62 mm"),
        ("Package_DIP:DIP-16_W7.62mm", "DIP-16 · 7.62 mm"),
    ],
    "inductor": [
        ("Inductor_SMD:L_0805_2012Metric", "0805 · 2.0 × 1.2 mm"),
        ("Inductor_SMD:L_1206_3216Metric", "1206 · 3.2 × 1.6 mm"),
        ("Inductor_SMD:L_1210_3225Metric", "1210 · 3.2 × 2.5 mm"),
    ],
    "diode": [
        ("Diode_SMD:D_SOD-323", "SOD-323 · 1.7 × 1.25 mm"),
        ("Diode_SMD:D_SOD-123", "SOD-123 · 2.7 × 1.6 mm"),
        ("Diode_SMD:D_SMA", "SMA · 4.3 × 2.6 mm"),
        ("Diode_SMD:D_SMB", "SMB · 4.6 × 3.6 mm"),
        ("Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal", "DO-35 THT · 7.62 mm"),
    ],
    "ic": [
        ("Package_DIP:DIP-8_W7.62mm", "DIP-8 · 7.62 mm"),
        ("Package_DIP:DIP-14_W7.62mm", "DIP-14 · 7.62 mm"),
        ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-8 · 1.27 mm"),
        ("Package_SO:SOIC-14_3.9x8.7mm_P1.27mm", "SOIC-14 · 1.27 mm"),
        ("Package_QFP:TQFP-32_7x7mm_P0.8mm", "TQFP-32 · 0.8 mm"),
        (
            "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm",
            "QFN-16 · 3 × 3 mm",
        ),
        ("Package_TO_SOT_SMD:SOT-23-5", "SOT-23-5 · 5 pins"),
    ],
}


@dataclass
class Pad:
    number: str
    kind: str
    shape: str
    x: float
    y: float
    rot: float
    w: float
    h: float
    rratio: float = 0.25
    drill: tuple[float, float] | None = None
    layers: list[str] = field(default_factory=list)


@dataclass
class Graphic:
    kind: str
    layer: str
    points: list[tuple[float, float]]
    width: float = 0.12
    fill: bool = False
    radius: float = 0.0


@dataclass
class Footprint:
    name: str
    description: str
    tags: str
    attr: str
    pads: list[Pad]
    graphics: list[Graphic]

    @property
    def pad_count(self) -> int:
        return len(self.pads)

    @property
    def through_hole(self) -> bool:
        return self.attr == "through_hole" or any(p.drill for p in self.pads)


def _unwrap(node: Any) -> Any:
    if isinstance(node, check_pcb.SList) and len(node.items) == 1 and isinstance(node.items[0], check_pcb.SList):
        return node.items[0]
    return node


def _sub(node: Any, name: str) -> Any:
    return check_pcb.sub(node, name) if isinstance(node, check_pcb.SList) else None


def _subs(node: Any, name: str) -> list[Any]:
    return check_pcb.subs(node, name) if isinstance(node, check_pcb.SList) else []


def _atom(node: Any, index: int = 1) -> str | None:
    return check_pcb.atom(node, index) if isinstance(node, check_pcb.SList) else None


def _num(value: str | None, default: float = 0.0) -> float:
    number = check_pcb.fnum(value)
    return default if number is None else float(number)


def _point(node: Any) -> tuple[float, float] | None:
    return check_pcb.xy_of(node) if isinstance(node, check_pcb.SList) else None


def _width(node: Any) -> float:
    stroke = _sub(node, "stroke")
    if stroke is not None:
        return _num(_atom(_sub(stroke, "width")), 0.12)
    return _num(_atom(_sub(node, "width")), 0.12)


def _filled(node: Any) -> bool:
    fill = _sub(node, "fill")
    if fill is None:
        return False
    value = _atom(fill, 1)
    if value in ("solid", "yes", "true"):
        return True
    if value == "none":
        return False
    # KiCad 9 uses (fill (type solid)).
    fill_type = _sub(fill, "type")
    return (_atom(fill_type, 1) or "") in ("solid", "yes", "true")


def _layer(node: Any) -> str:
    layer = _sub(node, "layer")
    return _atom(layer, 1) or "F.SilkS"


def _parse_graphic(node: Any, kind: str) -> Graphic | None:
    layer = _layer(node)
    width = _width(node)
    fill = _filled(node)
    if kind == "line":
        start, end = _point(_sub(node, "start")), _point(_sub(node, "end"))
        if not start or not end:
            return None
        return Graphic("line", layer, [start, end], width, fill)
    if kind == "rect":
        start, end = _point(_sub(node, "start")), _point(_sub(node, "end"))
        if not start or not end:
            return None
        return Graphic("rect", layer, [start, end], width, fill)
    if kind == "circle":
        center, end = _point(_sub(node, "center")), _point(_sub(node, "end"))
        if not center or not end:
            return None
        radius = math.hypot(end[0] - center[0], end[1] - center[1])
        return Graphic("circle", layer, [center], width, fill, radius)
    if kind == "arc":
        start = _point(_sub(node, "start"))
        mid = _point(_sub(node, "mid"))
        end = _point(_sub(node, "end"))
        if start and mid and end:
            return Graphic("arc", layer, [start, mid, end], width, fill)
        return None
    if kind == "poly":
        pts = _sub(node, "pts")
        points = [_point(item) for item in _subs(pts, "xy")] if pts is not None else []
        points = [point for point in points if point]
        if len(points) >= 2:
            return Graphic("poly", layer, points, width, fill)
    return None


def _parse_pad(node: Any) -> Pad | None:
    number = _atom(node, 1) or ""
    kind = _atom(node, 2) or "smd"
    shape = _atom(node, 3) or "rect"
    at = _sub(node, "at")
    if at is None:
        return None
    x = _num(_atom(at, 1))
    y = _num(_atom(at, 2))
    rot = _num(_atom(at, 3), 0.0)
    size = _sub(node, "size")
    w = _num(_atom(size, 1), 1.0)
    h = _num(_atom(size, 2), w)
    rratio = _num(_atom(_sub(node, "roundrect_rratio")), 0.25)
    drill: tuple[float, float] | None = None
    drill_node = _sub(node, "drill")
    if drill_node is not None:
        if _atom(drill_node, 1) == "oval":
            dw = _num(_atom(drill_node, 2), 0.0)
            dh = _num(_atom(drill_node, 3), dw)
            drill = (dw, dh)
        else:
            d = _num(_atom(drill_node, 1), 0.0)
            if d > 0:
                drill = (d, d)
    layers_node = _sub(node, "layers")
    layers = [
        str(item.value)
        for item in (layers_node.items[1:] if layers_node is not None else [])
        if isinstance(item, check_pcb.Token)
    ]
    return Pad(number, kind, shape, x, y, rot, w, h, rratio, drill, layers)


def parse_footprint_text(text: str) -> Footprint:
    root = _unwrap(check_pcb.parse(text))
    name = _atom(root, 1) or "footprint"
    description = _atom(_sub(root, "descr"), 1) or ""
    tags = _atom(_sub(root, "tags"), 1) or ""
    attr = _atom(_sub(root, "attr"), 1) or ""
    pads = [pad for pad in (_parse_pad(node) for node in _subs(root, "pad")) if pad]
    graphics: list[Graphic] = []
    for node in root.items if isinstance(root, check_pcb.SList) else []:
        if not isinstance(node, check_pcb.SList):
            continue
        head = check_pcb.head(node)
        if head in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly"):
            graphic = _parse_graphic(node, head.removeprefix("fp_"))
            if graphic is not None:
                graphics.append(graphic)
    return Footprint(name, description, tags, attr, pads, graphics)


def _pad_corners(pad: Pad) -> list[tuple[float, float]]:
    angle = math.radians(-pad.rot)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    corners = []
    for dx, dy in ((-pad.w / 2, -pad.h / 2), (pad.w / 2, -pad.h / 2), (pad.w / 2, pad.h / 2), (-pad.w / 2, pad.h / 2)):
        corners.append((pad.x + dx * cos_a - dy * sin_a, pad.y + dx * sin_a + dy * cos_a))
    return corners


def _bounds(footprint: Footprint) -> tuple[float, float, float, float]:
    points: list[tuple[float, float]] = []
    for pad in footprint.pads:
        points.extend(_pad_corners(pad))
    for graphic in footprint.graphics:
        if graphic.kind == "circle":
            cx, cy = graphic.points[0]
            r = graphic.radius
            points.extend(((cx - r, cy - r), (cx + r, cy + r)))
        else:
            points.extend(graphic.points)
    if not points:
        points = [(-2.0, -2.0), (2.0, 2.0)]
    min_x = min(x for x, _ in points)
    max_x = max(x for x, _ in points)
    min_y = min(y for _, y in points)
    max_y = max(y for _, y in points)
    return (min_x, min_y, max_x, max_y)


def _f(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _rect_svg(
    x: float,
    y: float,
    w: float,
    h: float,
    rx: float,
    fill: str,
    stroke: str | None,
    extra: str = "",
    stroke_width: float = 0.025,
) -> str:
    stroke_attr = f' stroke="{stroke}" stroke-width="{_f(stroke_width)}"' if stroke else ""
    return (
        f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" rx="{_f(rx)}" '
        f'fill="{fill}"{stroke_attr} {extra}/>'
    )


def _arc_points(start: tuple[float, float], mid: tuple[float, float], end: tuple[float, float]) -> list[tuple[float, float]]:
    ax, ay = start
    bx, by = mid
    cx, cy = end
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return [start, mid, end]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    radius = math.hypot(ax - ux, ay - uy)
    start_angle = math.atan2(ay - uy, ax - ux)
    mid_angle = math.atan2(by - uy, bx - ux)
    end_angle = math.atan2(cy - uy, cx - ux)
    delta = (end_angle - start_angle) % (2 * math.pi)
    mid_delta = (mid_angle - start_angle) % (2 * math.pi)
    if mid_delta > delta:
        delta -= 2 * math.pi
    steps = 24
    return [
        (ux + radius * math.cos(start_angle + delta * i / steps), uy + radius * math.sin(start_angle + delta * i / steps))
        for i in range(steps + 1)
    ]


def _graphic_svg(graphic: Graphic) -> str:
    if graphic.layer.startswith("F.CrtYd") or graphic.layer.startswith("B.CrtYd"):
        stroke, opacity, dash = COURTYARD, 0.62, ' stroke-dasharray="0.32 0.24"'
    elif graphic.layer.endswith("Fab"):
        stroke, opacity, dash = SILK, 0.28, ""
    else:
        stroke, opacity, dash = SILK, 1.0, ""
    fill = stroke if graphic.fill else "none"
    common = (
        f'stroke="{stroke}" stroke-opacity="{opacity}" stroke-width="{_f(graphic.width)}" '
        f'stroke-linecap="round" stroke-linejoin="round" fill="{fill}"{dash}'
    )
    if graphic.kind == "line":
        (x1, y1), (x2, y2) = graphic.points
        return f'<line x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}" {common}/>'
    if graphic.kind == "rect":
        (x1, y1), (x2, y2) = graphic.points
        return _rect_svg(
            min(x1, x2),
            min(y1, y2),
            abs(x2 - x1),
            abs(y2 - y1),
            0,
            fill,
            stroke,
            f'stroke-opacity="{opacity}"{dash}',
            graphic.width,
        )
    if graphic.kind == "circle":
        cx, cy = graphic.points[0]
        return f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(graphic.radius)}" {common}/>'
    if graphic.kind == "arc":
        points = _arc_points(*graphic.points)
        path = " ".join(f'{"M" if i == 0 else "L"} {_f(x)} {_f(y)}' for i, (x, y) in enumerate(points))
        return f'<path d="{path}" {common}/>'
    if graphic.kind == "poly":
        points = " ".join(f"{_f(x)},{_f(y)}" for x, y in graphic.points)
        return f'<polygon points="{points}" {common}/>'
    return ""


def _pad_svg(pad: Pad) -> str:
    transform = f' transform="rotate({_f(-pad.rot)} {_f(pad.x)} {_f(pad.y)})"' if pad.rot else ""
    x = pad.x - pad.w / 2
    y = pad.y - pad.h / 2
    rx = 0.0
    if pad.shape == "roundrect":
        rx = min(pad.w, pad.h) * max(0.0, min(0.5, pad.rratio))
    elif pad.shape == "oval":
        rx = min(pad.w, pad.h) / 2
    if pad.kind == "np_thru_hole":
        copper = ""
    elif pad.shape == "circle":
        copper = f'<circle cx="{_f(pad.x)}" cy="{_f(pad.y)}" r="{_f(min(pad.w, pad.h) / 2)}" fill="{COPPER}" stroke="{COPPER_EDGE}" stroke-width="0.025"{transform}/>'
    else:
        copper = _rect_svg(x, y, pad.w, pad.h, rx, COPPER, COPPER_EDGE, transform)
    hole = ""
    if pad.drill:
        dw, dh = pad.drill
        if abs(dw - dh) < 1e-6:
            hole = f'<circle cx="{_f(pad.x)}" cy="{_f(pad.y)}" r="{_f(dw / 2)}" fill="{HOLE}"{transform}/>'
        else:
            hole = (
                f'<ellipse cx="{_f(pad.x)}" cy="{_f(pad.y)}" rx="{_f(dw / 2)}" ry="{_f(dh / 2)}" '
                f'fill="{HOLE}"{transform}/>'
            )
    return copper + hole


def _pin1_svg(footprint: Footprint) -> str:
    pad = next((p for p in footprint.pads if p.number == "1"), None)
    if pad is None:
        return ""
    r = max(pad.w, pad.h) / 2 + 0.13
    return f'<circle cx="{_f(pad.x)}" cy="{_f(pad.y)}" r="{_f(r)}" fill="none" stroke="{SILK}" stroke-width="0.055" stroke-opacity="0.9"/>'


def render_svg(footprint: Footprint, *, label: str | None = None, error: str | None = None) -> str:
    min_x, min_y, max_x, max_y = _bounds(footprint)
    margin = 0.35
    min_x -= margin
    min_y -= margin
    max_x += margin
    max_y += margin
    width, height = max_x - min_x, max_y - min_y
    uid = hashlib.sha1(f"{footprint.name}:{min_x}:{min_y}:{width}:{height}".encode("utf-8")).hexdigest()[:10]
    title = escape(label or footprint.name)
    title_attr = escape(label or footprint.name, {'"': "&quot;"})
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_f(min_x)} {_f(min_y)} {_f(width)} {_f(height)}" '
        f'role="img" aria-label="{title_attr}" preserveAspectRatio="xMidYMid meet" class="fp-svg">',
        f"<title>{title}</title>",
        "<defs>",
        f'<pattern id="grid-{uid}" width="1" height="1" patternUnits="userSpaceOnUse">',
        f'<path d="M 1 0 L 0 0 0 1" fill="none" stroke="{SILK}" stroke-opacity="0.055" stroke-width="0.02"/>',
        "</pattern>",
        "</defs>",
        f'<rect x="{_f(min_x)}" y="{_f(min_y)}" width="{_f(width)}" height="{_f(height)}" fill="{BOARD}"/>',
        f'<rect x="{_f(min_x)}" y="{_f(min_y)}" width="{_f(width)}" height="{_f(height)}" fill="url(#grid-{uid})"/>',
        f'<rect x="{_f(min_x)}" y="{_f(min_y)}" width="{_f(width)}" height="{_f(height)}" fill="none" stroke="{BOARD_EDGE}" stroke-width="0.08"/>',
    ]
    courtyard = [g for g in footprint.graphics if g.layer.startswith(("F.CrtYd", "B.CrtYd"))]
    silk = [g for g in footprint.graphics if not g.layer.startswith(("F.CrtYd", "B.CrtYd"))]
    if courtyard:
        parts.append('<g class="courtyard">' + "".join(_graphic_svg(g) for g in courtyard) + "</g>")
    if silk:
        parts.append('<g class="silk">' + "".join(_graphic_svg(g) for g in silk) + "</g>")
    parts.append('<g class="pads">' + "".join(_pad_svg(p) for p in footprint.pads) + "</g>")
    parts.append('<g class="pin1">' + _pin1_svg(footprint) + "</g>")
    if error:
        parts.append(
            f'<text x="{_f(min_x + width / 2)}" y="{_f(min_y + height - 0.5)}" text-anchor="middle" '
            f'font-family="monospace" font-size="0.7" fill="{MUTED}">{escape(error[:80])}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def placeholder_svg(footprint: str, reason: str | None = None) -> str:
    name = escape(footprint)
    name_attr = escape(footprint, {'"': "&quot;"})
    note = escape((reason or "footprint geometry not available")[:70])
    uid = hashlib.sha1(f"placeholder:{footprint}".encode("utf-8")).hexdigest()[:10]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 16" role="img" '
        f'aria-label="{name_attr} preview unavailable" preserveAspectRatio="xMidYMid meet" class="fp-svg">'
        f"<title>{name}</title>"
        f"<defs><pattern id=\"grid-{uid}\" width=\"1\" height=\"1\" patternUnits=\"userSpaceOnUse\">"
        f'<path d="M 1 0 L 0 0 0 1" fill="none" stroke="{SILK}" stroke-opacity="0.06" stroke-width="0.02"/></pattern></defs>'
        f'<rect width="24" height="16" fill="{BOARD}"/>'
        f'<rect width="24" height="16" fill="url(#grid-{uid})"/>'
        f'<rect x="0.6" y="0.6" width="22.8" height="14.8" fill="none" stroke="{BOARD_EDGE}" stroke-width="0.08"/>'
        f'<rect x="7" y="5" width="10" height="6" fill="none" stroke="{SILK}" stroke-opacity="0.55" stroke-width="0.12" stroke-dasharray="0.5 0.35"/>'
        f'<text x="12" y="12.7" text-anchor="middle" font-family="monospace" font-size="0.9" fill="{MUTED}">{note}</text>'
        f'<text x="12" y="14.2" text-anchor="middle" font-family="monospace" font-size="0.7" fill="{SILK}" fill-opacity="0.75">{name}</text>'
        "</svg>"
    )


def _safe_part(value: str) -> bool:
    return bool(SAFE_PART.match(value))


def fetch_urls(library: str, name: str, refs: tuple[str, ...] = GITHUB_REFS) -> list[str]:
    if not _safe_part(library) or not _safe_part(name):
        return []
    return [GITHUB_RAW.format(ref=ref, library=library, name=name) for ref in refs]


def fetch_footprint(
    library: str,
    name: str,
    *,
    refs: tuple[str, ...] = GITHUB_REFS,
    timeout: int = 20,
    cache_dir: Path | None = None,
) -> Path | None:
    cache = Path(cache_dir) if cache_dir else CACHE_DIR
    cached = cache / f"{library}.pretty" / f"{name}.kicad_mod"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached
    for url in fetch_urls(library, name, refs):
        request = urllib.request.Request(url, headers={"User-Agent": "pcb-design-skill/footprint-preview"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue  # try the next library tag
            break  # auth/network/server error: stop early
        except (urllib.error.URLError, TimeoutError, OSError):
            break  # offline or DNS failure: do not wait on every tag
        if not text.lstrip().startswith("("):
            continue
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(text, encoding="utf-8")
        return cached
    return None


def resolve_footprint(
    footprint: str,
    *,
    footprints_dir: Path | None = None,
    fetch: bool = True,
    cache_dir: Path | None = None,
) -> tuple[Path, str]:
    if footprint.endswith(".kicad_mod"):
        path = Path(footprint).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"footprint file not found: {path}")
        return path, "local file"
    try:
        library_dir, name = pcb_build.footprint_paths(footprint, footprints_dir)
        path = Path(library_dir) / f"{name}.kicad_mod"
        if path.is_file():
            return path, "KiCad library"
    except (FileNotFoundError, ValueError):
        pass
    if ":" in footprint:
        library, name = footprint.split(":", 1)
        if fetch:
            path = fetch_footprint(library, name, cache_dir=cache_dir)
            if path is not None:
                return path, "KiCad library (fetched)"
    raise FileNotFoundError(
        f"footprint not found: {footprint}. Install KiCad's footprint libraries, set "
        "KICAD9_FOOTPRINT_DIR, use a .kicad_mod path, or allow the GitHub fetch."
    )


def preview(
    footprint: str,
    *,
    footprints_dir: Path | None = None,
    fetch: bool = True,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    try:
        path, source = resolve_footprint(
            footprint, footprints_dir=footprints_dir, fetch=fetch, cache_dir=cache_dir
        )
        parsed = parse_footprint_text(path.read_text(encoding="utf-8"))
        min_x, min_y, max_x, max_y = _bounds(parsed)
        label = parsed.name.replace("_", " ")
        return {
            "available": True,
            "footprint": footprint,
            "label": label,
            "description": parsed.description,
            "tags": parsed.tags,
            "pads": parsed.pad_count,
            "through_hole": parsed.through_hole,
            "size_mm": [round(max_x - min_x, 2), round(max_y - min_y, 2)],
            "source": source,
            "path": str(path),
            "svg": render_svg(parsed, label=label),
            "error": None,
        }
    except Exception as exc:  # preview must never break the picker
        return {
            "available": False,
            "footprint": footprint,
            "label": footprint.split(":")[-1].replace("_", " "),
            "description": "",
            "tags": "",
            "pads": 0,
            "through_hole": False,
            "size_mm": None,
            "source": None,
            "path": None,
            "svg": placeholder_svg(footprint, str(exc)),
            "error": str(exc),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a KiCad footprint as an SVG preview.")
    parser.add_argument("footprint", nargs="?", help="Library:Footprint or path/to/file.kicad_mod")
    parser.add_argument("--footprints-dir", help="KiCad footprints root (contains *.pretty)")
    parser.add_argument("--cache-dir", help="footprint download cache")
    parser.add_argument("--no-fetch", action="store_true", help="never fetch from the KiCad GitHub library")
    parser.add_argument("--svg", help="write the SVG here ('-' for stdout)")
    parser.add_argument("--json", action="store_true", help="print metadata and SVG as JSON")
    parser.add_argument("--list-common", metavar="CATEGORY", help="list built-in common footprints")
    args = parser.parse_args(argv)

    if args.list_common:
        if args.list_common == "all":
            print(json.dumps({k: [{"id": i, "label": l} for i, l in v] for k, v in COMMON_FOOTPRINTS.items()}, indent=2))
            return 0
        items = COMMON_FOOTPRINTS.get(args.list_common)
        if items is None:
            print(f"unknown category {args.list_common!r}; choose from {', '.join(sorted(COMMON_FOOTPRINTS))}", file=sys.stderr)
            return 2
        print(json.dumps([{"id": i, "label": l} for i, l in items], indent=2))
        return 0

    if not args.footprint:
        parser.error("a footprint is required unless --list-common is used")

    result = preview(
        args.footprint,
        footprints_dir=Path(args.footprints_dir) if args.footprints_dir else None,
        fetch=not args.no_fetch,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
    )
    if args.svg:
        if args.svg == "-":
            print(result["svg"])
        else:
            Path(args.svg).write_text(result["svg"], encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2))
    elif not args.svg:
        print(f"{result['label']}  ({result['footprint']})")
        if result["size_mm"]:
            print(f"  size: {result['size_mm'][0]} × {result['size_mm'][1]} mm")
        print(f"  pads: {result['pads']}  through-hole: {result['through_hole']}")
        print(f"  source: {result['source'] or 'unavailable'}")
        if result["error"]:
            print(f"  error: {result['error']}")
    return 0 if result["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
