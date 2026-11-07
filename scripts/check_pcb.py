#!/usr/bin/env python3
"""Structural validator for KiCad .kicad_pcb files.

This is a fast, dependency-free pre-flight check. It catches the mistakes that
are easy to make when generating a board programmatically:

  * unbalanced s-expressions / unterminated strings
  * missing or inconsistent header / required sections
  * net ordinals referenced by pads, tracks, vias and zones that do not exist
  * net name mismatches between the net table and pads
  * unknown layer names, duplicate layers / UUIDs / references
  * zero-length tracks, legacy tokens for the declared file version
  * open or missing Edge.Cuts outline
  * dangling track endpoints (geometric connectivity, not just net labels)
  * zones that claim to be filled but have no fill polygons
  * footprints without attributes or courtyards

It is NOT a replacement for KiCad's own DRC. When KiCad is available run:

    kicad-cli pcb drc --severity-all --exit-code-violations board.kicad_pcb

Usage:
    python3 check_pcb.py board.kicad_pcb
    python3 check_pcb.py --strict board.kicad_pcb      # warnings fail too
    python3 check_pcb.py --no-connectivity board.kicad_pcb

Exit codes: 0 = clean, 1 = errors (or warnings with --strict), 2 = bad input.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# s-expression parsing
# --------------------------------------------------------------------------


class ParseError(Exception):
    pass


@dataclass
class Token:
    value: str
    quoted: bool
    line: int


@dataclass
class SList:
    line: int
    items: list = field(default_factory=list)


def parse(text: str) -> SList:
    """Parse the file into a list of nested SList/Token objects."""
    root = SList(1)
    stack = [root]
    line = 1
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        elif c == ";":
            # KiCad does not emit comments, but tolerate them.
            while i < n and text[i] != "\n":
                i += 1
        elif c == "(":
            node = SList(line)
            stack[-1].items.append(node)
            stack.append(node)
            i += 1
        elif c == ")":
            if len(stack) == 1:
                raise ParseError(f"unexpected ')' at line {line}")
            stack.pop()
            i += 1
        elif c == '"':
            start_line = line
            i += 1
            buf = []
            closed = False
            while i < n:
                ch = text[i]
                if ch == "\\":
                    if i + 1 < n:
                        buf.append(text[i + 1])
                        i += 2
                        continue
                if ch == '"':
                    i += 1
                    closed = True
                    break
                if ch == "\n":
                    line += 1
                buf.append(ch)
                i += 1
            if not closed:
                raise ParseError(f"unterminated string starting at line {start_line}")
            stack[-1].items.append(Token("".join(buf), True, start_line))
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in '()";':
                i += 1
            if i == start:  # defensive: never spin
                i += 1
            stack[-1].items.append(Token(text[start:i], False, line))
    if len(stack) != 1:
        raise ParseError(f"unclosed '(' starting at line {stack[-1].line}")
    return root


def head(node: SList):
    if node.items and isinstance(node.items[0], Token):
        return node.items[0].value
    return None


def subs(node: SList, name: str):
    return [c for c in node.items if isinstance(c, SList) and head(c) == name]


def sub(node: SList, name: str):
    for c in node.items:
        if isinstance(c, SList) and head(c) == name:
            return c
    return None


def atom(node: SList, index: int = 1):
    if len(node.items) > index and isinstance(node.items[index], Token):
        return node.items[index].value
    return None


def walk(node: SList):
    for item in node.items:
        if isinstance(item, SList):
            yield item
            yield from walk(item)


def fnum(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def xy_of(node: SList):
    """Return (x, y) for a token node such as (at X Y [R]) or (xy X Y)."""
    x = fnum(atom(node, 1))
    y = fnum(atom(node, 2))
    if x is None or y is None:
        return None
    return (x, y)


# --------------------------------------------------------------------------
# KiCad knowledge
# --------------------------------------------------------------------------

VERSIONS = {
    20211014: "6.0",
    20221018: "7.0",
    20240108: "8.0",
    20241229: "9.0",
}

# KiCad 9 changed the layer ordinal scheme. Copper is even, technical odd.
V9_LAYER_ORDINALS = {
    "F.Cu": 0,
    "F.Mask": 1,
    "B.Cu": 2,
    "B.Mask": 3,
    "F.SilkS": 5,
    "B.SilkS": 7,
    "F.Adhes": 9,
    "B.Adhes": 11,
    "F.Paste": 13,
    "B.Paste": 15,
    "Dwgs.User": 17,
    "Cmts.User": 19,
    "Eco1.User": 21,
    "Eco2.User": 23,
    "Edge.Cuts": 25,
    "Margin": 27,
    "B.CrtYd": 29,
    "F.CrtYd": 31,
    "B.Fab": 33,
    "F.Fab": 35,
}
for _i in range(1, 31):
    V9_LAYER_ORDINALS[f"In{_i}.Cu"] = 2 + 2 * _i
for _i in range(1, 10):
    V9_LAYER_ORDINALS[f"User.{_i}"] = 37 + 2 * _i

# KiCad 6/7/8 use the historical layer IDs.
LEGACY_LAYER_ORDINALS = {
    "F.Cu": 0,
    "B.Cu": 31,
    "B.Adhes": 32,
    "F.Adhes": 33,
    "B.Paste": 34,
    "F.Paste": 35,
    "B.SilkS": 36,
    "F.SilkS": 37,
    "B.Mask": 38,
    "F.Mask": 39,
    "Dwgs.User": 40,
    "Cmts.User": 41,
    "Eco1.User": 42,
    "Eco2.User": 43,
    "Edge.Cuts": 44,
    "Margin": 45,
    "B.CrtYd": 46,
    "F.CrtYd": 47,
    "B.Fab": 48,
    "F.Fab": 49,
}
for _i in range(1, 31):
    LEGACY_LAYER_ORDINALS[f"In{_i}.Cu"] = _i
for _i in range(1, 10):
    LEGACY_LAYER_ORDINALS[f"User.{_i}"] = 49 + _i

CANONICAL_LAYERS = set(V9_LAYER_ORDINALS) | set(LEGACY_LAYER_ORDINALS)
PAD_TYPES = {"thru_hole", "smd", "connect", "np_thru_hole"}
PAD_SHAPES = {"circle", "rect", "oval", "trapezoid", "roundrect", "custom"}
LAYER_TYPES = {"jumper", "mixed", "power", "signal", "user"}
TRACK_HEADS = {"segment", "via", "arc"}
GRAPHIC_HEADS = {
    "gr_line",
    "gr_arc",
    "gr_rect",
    "gr_circle",
    "gr_poly",
    "gr_curve",
    "gr_text",
    "gr_text_box",
}
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def expected_layer_ordinal(name: str, version):
    """Return the canonical ordinal for a layer in the file's KiCad dialect."""
    table = V9_LAYER_ORDINALS if (version or 0) >= 20241229 else LEGACY_LAYER_ORDINALS
    return table.get(name)


@dataclass
class Issue:
    severity: str  # "error" | "warning"
    line: int
    message: str

    def __str__(self):
        return f"{self.severity.upper():7} line {self.line:>6}: {self.message}"


class Checker:
    def __init__(self, text: str, path: str):
        self.path = path
        self.issues: list[Issue] = []
        self.root = parse(text)
        self.declared_layers: dict[str, int] = {}
        self.nets: dict[int, str] = {}
        self.net_lines: dict[int, int] = {}
        self.uuids: dict[str, int] = {}
        self.version = None
        self.connectivity = True
        self.segments: list[dict] = []
        self.vias: list[dict] = []
        self.pads: list[dict] = []
        self.zones: list[dict] = []
        self.footprints: list = []
        self._refs: set[str] = set()

    # -- reporting ---------------------------------------------------------
    def error(self, line, msg):
        self.issues.append(Issue("error", line, msg))

    def warn(self, line, msg):
        self.issues.append(Issue("warning", line, msg))

    # -- top level ---------------------------------------------------------
    def run(self, connectivity=True):
        self.connectivity = connectivity
        top = self.root
        if len(top.items) != 1 or not isinstance(top.items[0], SList):
            self.error(top.line, "file must contain exactly one top-level s-expression")
            return self.issues
        board = top.items[0]
        if head(board) != "kicad_pcb":
            self.error(board.line, f"top-level token is '{head(board)}', expected 'kicad_pcb'")
            return self.issues

        self.check_header(board)
        self.check_layers(board)
        self.check_sections(board)
        self.check_nets(board)
        self.check_uuid_uniqueness(board)
        self.collect_footprints(board)
        self.collect_tracks(board)
        self.collect_zones(board)
        self.check_pads()
        self.check_zones()
        self.check_outline(board)
        self.check_legacy_tokens(board)
        if self.connectivity:
            self.check_dangling()
        return self.issues

    def check_header(self, board):
        version_node = sub(board, "version")
        if version_node is None:
            self.error(board.line, "missing (version ...) in header")
        else:
            raw = atom(version_node, 1)
            try:
                self.version = int(raw or "")
            except ValueError:
                self.error(version_node.line, f"version '{raw}' is not an integer date stamp")
            else:
                if self.version not in VERSIONS:
                    self.warn(
                        version_node.line,
                        f"unrecognized version {self.version}; known: "
                        + ", ".join(f"{k} ({v})" for k, v in sorted(VERSIONS.items())),
                    )

        gen = sub(board, "generator")
        if gen is None:
            self.error(board.line, "missing (generator ...) in header")
        elif atom(gen, 1) == "pcbnew":
            self.warn(
                gen.line,
                "generator is 'pcbnew'; third-party generators should use their own identifier",
            )

        if self.version and self.version >= 20240108 and sub(board, "generator_version") is None:
            self.warn(board.line, "KiCad 8/9 files normally include (generator_version \"x.y\")")

    def check_sections(self, board):
        for name in ("general", "paper", "layers", "setup"):
            if sub(board, name) is None:
                self.error(board.line, f"missing required section ({name} ...)")
        general = sub(board, "general")
        if general is not None:
            thickness = sub(general, "thickness")
            if thickness is None:
                self.error(general.line, "missing (thickness ...) in general section")
            else:
                value = fnum(atom(thickness, 1))
                if value is None or value <= 0:
                    self.error(thickness.line, "board thickness must be a positive number in mm")
                elif not (0.4 <= value <= 6.0):
                    self.warn(thickness.line, f"board thickness {value} mm is unusual (0.6-2.0 mm typical)")

    def check_layers(self, board):
        layers = sub(board, "layers")
        if layers is None:
            return
        seen_names = set()
        seen_ordinals = set()
        has_copper = False
        for entry in layers.items:
            if not isinstance(entry, SList):
                continue
            if len(entry.items) < 3:
                self.error(entry.line, "layer entry must be (ORDINAL \"NAME\" TYPE [\"USER_NAME\"])")
                continue
            ordinal = fnum(atom(entry, 0))
            name = atom(entry, 1)
            ltype = atom(entry, 2)
            if ordinal is None:
                self.error(entry.line, f"layer ordinal '{atom(entry, 0)}' is not an integer")
            if not name:
                self.error(entry.line, "layer name is empty")
                continue
            if ltype not in LAYER_TYPES:
                self.error(entry.line, f"layer '{name}' has invalid type '{ltype}'")
            if name in seen_names:
                self.error(entry.line, f"duplicate layer '{name}'")
            if ordinal is not None and int(ordinal) in seen_ordinals:
                self.error(entry.line, f"duplicate layer ordinal {int(ordinal)}")
            seen_names.add(name)
            if ordinal is not None:
                seen_ordinals.add(int(ordinal))
            self.declared_layers[name] = int(ordinal) if ordinal is not None else -1
            if name.endswith(".Cu"):
                has_copper = True
            expected = expected_layer_ordinal(name, self.version)
            if expected is not None and ordinal is not None and int(ordinal) != expected:
                scheme = "KiCad 9" if (self.version or 0) >= 20241229 else "KiCad 6/7/8"
                self.warn(
                    entry.line,
                    f"layer '{name}' uses ordinal {int(ordinal)}, expected {expected} for {scheme}",
                )
            if name not in CANONICAL_LAYERS and not name.startswith("In"):
                self.warn(entry.line, f"non-canonical layer name '{name}'")
        if not has_copper:
            self.error(layers.line, "layers section has no copper layers")
        for required in ("F.Cu", "B.Cu", "Edge.Cuts"):
            if required not in self.declared_layers:
                self.error(layers.line, f"required layer '{required}' is not declared")

    def check_nets(self, board):
        for net in subs(board, "net"):
            ordinal_raw = atom(net, 1)
            name = atom(net, 2)
            try:
                ordinal = int(ordinal_raw or "")
            except ValueError:
                self.error(net.line, f"net ordinal '{ordinal_raw}' is not an integer")
                continue
            if ordinal in self.nets:
                self.error(net.line, f"duplicate net ordinal {ordinal}")
            self.nets[ordinal] = name if name is not None else ""
            self.net_lines[ordinal] = net.line
            if ordinal == 0 and name not in ("", None):
                self.warn(net.line, "net 0 is conventionally the empty net with an empty name")
        if not self.nets:
            self.error(board.line, "no (net ...) entries; the nets section is required")
        if 0 not in self.nets:
            self.warn(board.line, "no (net 0 \"\") entry; KiCad normally writes one")
        seen_names = {}
        for ordinal, name in self.nets.items():
            if name and name in seen_names:
                self.warn(
                    self.net_lines[ordinal],
                    f"net name '{name}' is also used by net {seen_names[name]}",
                )
            elif name:
                seen_names[name] = ordinal

    def check_uuid_uniqueness(self, board):
        def visit(node):
            for item in node.items:
                if not isinstance(item, SList):
                    continue
                h = head(item)
                if h == "dimension":
                    # A dimension reuses its own UUID for its internal gr_text.
                    record(item)
                    continue
                if h in ("uuid", "tstamp", "id"):
                    record(item)
                else:
                    visit(item)

        def record(node):
            value = atom(node, 1)
            if not value:
                return
            if value in self.uuids:
                self.error(node.line, f"duplicate UUID '{value}' (first seen line {self.uuids[value]})")
            else:
                self.uuids[value] = node.line
            if not UUID_RE.match(value):
                self.warn(node.line, f"'{value}' does not look like a UUIDv4 string")

        visit(board)

    # -- footprints / pads -------------------------------------------------
    def collect_footprints(self, board):
        for fp in subs(board, "footprint"):
            self.footprints.append(fp)
            line = fp.line
            if sub(fp, "layer") is None:
                self.error(line, "footprint has no (layer ...)")
            if sub(fp, "at") is None:
                self.error(line, "footprint has no (at X Y [ROT])")
            if sub(fp, "uuid") is None and (self.version or 0) >= 20240108:
                self.warn(line, "footprint has no (uuid ...)")

            attr = sub(fp, "attr")
            board_only = False
            if attr is None:
                self.warn(line, "footprint has no (attr smd|through_hole); assembly/DRC classification may be wrong")
            else:
                kinds = {atom(attr, i) for i in range(1, len(attr.items)) if isinstance(attr.items[i], Token)}
                board_only = "board_only" in kinds
                mechanical = bool(kinds & {"exclude_from_pos_files", "exclude_from_bom"})
                if not kinds & {"smd", "through_hole"} and not board_only and not mechanical:
                    self.warn(line, "footprint (attr ...) does not declare smd or through_hole")

            # Reference: KiCad 8/9 field syntax, or legacy fp_text.
            ref = None
            for prop in subs(fp, "property"):
                if atom(prop, 1) == "Reference":
                    ref = atom(prop, 2)
                    break
            if ref is None:
                for text in subs(fp, "fp_text"):
                    if atom(text, 1) == "reference":
                        ref = atom(text, 2)
                        break
            if ref is None:
                self.warn(line, "footprint has no Reference field")
            elif ref in self._refs:
                self.error(line, f"duplicate reference designator '{ref}'")
            else:
                self._refs.add(ref)

            has_courtyard = False
            for child in walk(fp):
                h = head(child)
                if h and h.startswith("fp_"):
                    layer = sub(child, "layer")
                    if layer is not None and atom(layer, 1) in ("F.CrtYd", "B.CrtYd"):
                        has_courtyard = True
                        break
            if not has_courtyard:
                self.warn(line, f"footprint '{ref or '?'}' has no courtyard graphics (F.CrtYd/B.CrtYd)")

            fp_at = sub(fp, "at")
            fp_xy = xy_of(fp_at) if fp_at else None
            fp_rot = fnum(atom(fp_at, 3)) if fp_at and len(fp_at.items) > 3 else 0.0
            if fp_xy is None or fp_rot is None:
                continue
            for pad in subs(fp, "pad"):
                self.pads.append(self.parse_pad(pad, fp_xy, fp_rot, ref, board_only))

    def parse_pad(self, pad, fp_xy, fp_rot, ref, board_only=False):
        number = atom(pad, 1)
        ptype = atom(pad, 2)
        shape = atom(pad, 3)
        at = sub(pad, "at")
        size = sub(pad, "size")
        layers = sub(pad, "layers")
        net = sub(pad, "net")
        local = xy_of(at) if at else None
        pad_rot = fnum(atom(at, 3)) if at and len(at.items) > 3 else 0.0
        dims = xy_of(size) if size else None
        if local is None or dims is None:
            self.error(pad.line, f"pad {number or '?'} is missing (at ...) or (size ...)")
            return None
        total_rot = (fp_rot or 0.0) + (pad_rot or 0.0)
        gx = fp_xy[0] + local[0] * math.cos(math.radians(fp_rot or 0.0)) - local[1] * math.sin(math.radians(fp_rot or 0.0))
        gy = fp_xy[1] + local[0] * math.sin(math.radians(fp_rot or 0.0)) + local[1] * math.cos(math.radians(fp_rot or 0.0))

        if ptype not in PAD_TYPES:
            self.error(pad.line, f"pad {number or '?'} has invalid type '{ptype}'")
        if shape not in PAD_SHAPES:
            self.error(pad.line, f"pad {number or '?'} has invalid shape '{shape}'")
        if layers is None:
            self.error(pad.line, f"pad {number or '?'} has no (layers ...)")
            layer_names = []
        else:
            layer_names = [atom(layers, i) for i in range(1, len(layers.items)) if isinstance(layers.items[i], Token)]
            for name in layer_names:
                if name.startswith("*."):
                    continue
                if name not in self.declared_layers:
                    self.error(pad.line, f"pad layer '{name}' is not declared in the layers section")
        drill = sub(pad, "drill")
        if ptype in ("thru_hole", "np_thru_hole") and drill is None:
            self.error(pad.line, f"pad {number or '?'} is {ptype} but has no (drill ...)")
        if ptype == "smd" and drill is not None:
            self.warn(pad.line, f"SMD pad {number or '?'} has a drill")
        if ptype == "smd" and layer_names and not any(n.endswith(".Cu") or n.startswith("*.Cu") for n in layer_names):
            self.error(pad.line, f"SMD pad {number or '?'} is not on a copper layer")

        net_ord = None
        net_name = None
        if net is not None:
            try:
                net_ord = int(atom(net, 1) or "")
            except ValueError:
                self.error(net.line, f"pad {number or '?'} net ordinal is not an integer")
            net_name = atom(net, 2)
        return {
            "line": pad.line,
            "number": number,
            "type": ptype,
            "number_empty": number in ("", None),
            "x": gx,
            "y": gy,
            "w": dims[0],
            "h": dims[1],
            "rot": total_rot,
            "layers": layer_names,
            "net": net_ord,
            "net_name": net_name,
            "ref": ref,
            "has_drill": drill is not None,
            "board_only": board_only,
        }

    def check_pads(self):
        for p in self.pads:
            if p is None:
                continue
            line = p["line"]
            if p["net"] is None:
                numbered = (p["number"] or "").isdigit() and p["number"] != "0"
                if p["type"] != "np_thru_hole" and numbered and not p["board_only"]:
                    self.warn(
                        line,
                        f"pad {p['number']} of {p['ref']} has no net; confirm it is mechanical",
                    )
                continue
            if p["net"] not in self.nets:
                self.error(line, f"pad {p['number']} of {p['ref']} references undeclared net {p['net']}")
            elif p["net_name"] is not None and self.nets[p["net"]] and p["net_name"] != self.nets[p["net"]]:
                self.error(
                    line,
                    f"pad {p['number']} net name '{p['net_name']}' does not match net {p['net']} "
                    f"name '{self.nets[p['net']]}'",
                )

    # -- tracks ------------------------------------------------------------
    def collect_tracks(self, board):
        for node in walk(board):
            h = head(node)
            if h == "segment":
                seg = self.parse_segment(node)
                if seg:
                    self.segments.append(seg)
            elif h == "via":
                via = self.parse_via(node)
                if via:
                    self.vias.append(via)
            elif h == "arc":
                self.parse_arc(node)

    def check_net_and_layer(self, node, net, layer):
        if net is not None:
            if net not in self.nets:
                self.error(node.line, f"{head(node)} references undeclared net {net}")
            elif self.nets[net] == "":
                self.warn(node.line, f"{head(node)} is on the empty net (net 0)")
        if layer is not None and layer not in self.declared_layers:
            self.error(node.line, f"{head(node)} uses undeclared layer '{layer}'")
        elif layer is not None and not (layer.endswith(".Cu") or layer.startswith("In")):
            self.warn(node.line, f"{head(node)} is on non-copper layer '{layer}'")

    def parse_segment(self, node):
        start = sub(node, "start")
        end = sub(node, "end")
        width = sub(node, "width")
        layer = sub(node, "layer")
        net = sub(node, "net")
        if start is None or end is None:
            self.error(node.line, "segment is missing (start ...) or (end ...)")
            return None
        p1, p2 = xy_of(start), xy_of(end)
        if p1 is None or p2 is None:
            self.error(node.line, "segment has invalid coordinates")
            return None
        w = fnum(atom(width, 1)) if width else None
        if w is None or w <= 0:
            self.error(node.line, "segment width must be a positive number")
        net_ord = None
        try:
            net_ord = int(atom(net, 1) or "")
        except (ValueError, TypeError):
            if net is not None:
                self.error(net.line, "segment net ordinal is not an integer")
        layer_name = atom(layer, 1) if layer else None
        self.check_net_and_layer(node, net_ord, layer_name)
        if abs(p1[0] - p2[0]) < 1e-9 and abs(p1[1] - p2[1]) < 1e-9:
            self.warn(node.line, "zero-length segment (KiCad's track cleanup would remove it)")
        return {"line": node.line, "a": p1, "b": p2, "layer": layer_name, "net": net_ord}

    def parse_via(self, node):
        at = sub(node, "at")
        size = sub(node, "size")
        drill = sub(node, "drill")
        layers = sub(node, "layers")
        net = sub(node, "net")
        p = xy_of(at) if at else None
        if p is None:
            self.error(node.line, "via is missing (at ...)")
            return None
        s = fnum(atom(size, 1)) if size else None
        d = fnum(atom(drill, 1)) if drill else None
        if s is None or s <= 0:
            self.error(node.line, "via size must be a positive number")
        if d is None or d <= 0:
            self.error(node.line, "via drill must be a positive number")
        elif s is not None and d >= s:
            self.error(node.line, f"via drill {d} mm is not smaller than size {s} mm")
        elif s is not None and (s - d) / 2.0 < 0.05:
            self.warn(node.line, f"via annular ring {(s - d) / 2.0:.3f} mm is very small")
        layer_names = [atom(layers, i) for i in range(1, len(layers.items))] if layers else []
        for name in layer_names:
            if name not in self.declared_layers:
                self.error(node.line, f"via layer '{name}' is not declared")
        net_ord = None
        try:
            net_ord = int(atom(net, 1) or "")
        except (ValueError, TypeError):
            if net is not None:
                self.error(net.line, "via net ordinal is not an integer")
        if net_ord is not None and net_ord not in self.nets:
            self.error(node.line, f"via references undeclared net {net_ord}")
        return {"line": node.line, "x": p[0], "y": p[1], "size": s or 0.0, "layers": layer_names, "net": net_ord}

    def parse_arc(self, node):
        for key in ("start", "mid", "end"):
            if sub(node, key) is None:
                self.error(node.line, f"arc is missing ({key} ...)")
                return
        layer = sub(node, "layer")
        net = sub(node, "net")
        width = sub(node, "width")
        w = fnum(atom(width, 1)) if width else None
        if w is None or w <= 0:
            self.error(node.line, "arc width must be a positive number")
        net_ord = None
        try:
            net_ord = int(atom(net, 1) or "")
        except (ValueError, TypeError):
            if net is not None:
                self.error(net.line, "arc net ordinal is not an integer")
        self.check_net_and_layer(node, net_ord, atom(layer, 1) if layer else None)

    # -- zones -------------------------------------------------------------
    def collect_zones(self, board):
        for zone in subs(board, "zone"):
            net = sub(zone, "net")
            net_name = sub(zone, "net_name")
            layer = sub(zone, "layer")
            polygon = sub(zone, "polygon")
            net_ord = None
            try:
                net_ord = int(atom(net, 1) or "")
            except (ValueError, TypeError):
                if net is not None:
                    self.error(net.line, "zone net ordinal is not an integer")
            layer_name = atom(layer, 1) if layer else None
            if polygon is None:
                self.error(zone.line, "zone has no outline (polygon (pts ...))")
            else:
                pts = sub(polygon, "pts")
                if pts is None or len(subs(pts, "xy")) < 3:
                    self.error(polygon.line, "zone polygon needs at least 3 (xy ...) points")
            keepout = sub(zone, "keepout") is not None
            if keepout:
                if net_ord not in (0, None):
                    self.error(zone.line, "keepout zone should use net 0")
                if (atom(net_name, 1) or "") != "":
                    self.error(net_name.line, "keepout zone net_name should be empty")
            else:
                if net_ord is not None and net_ord not in self.nets:
                    self.error(zone.line, f"zone references undeclared net {net_ord}")
                elif net_ord is not None and (atom(net_name, 1) or "") != self.nets.get(net_ord, ""):
                    self.error(
                        zone.line,
                        f"zone net_name '{atom(net_name, 1)}' does not match net {net_ord} "
                        f"name '{self.nets.get(net_ord, '')}'",
                    )
            if layer_name is not None and layer_name not in self.declared_layers:
                self.error(zone.line, f"zone layer '{layer_name}' is not declared")
            self.zones.append(
                {
                    "line": zone.line,
                    "net": net_ord,
                    "layer": layer_name,
                    "filled": sub(zone, "filled_polygon") is not None or sub(zone, "fill_segments") is not None,
                    "fill": sub(zone, "fill"),
                    "keepout": keepout,
                }
            )

    def check_zones(self):
        for z in self.zones:
            if z["keepout"]:
                continue
            fill = z["fill"]
            wants_fill = False
            if fill is not None and any(isinstance(i, Token) and i.value == "yes" for i in fill.items):
                wants_fill = True
            if wants_fill and not z["filled"]:
                self.warn(
                    z["line"],
                    "zone is marked (fill yes) but has no filled_polygon; KiCad must refill it (Edit > Fill All Zones)",
                )

    # -- board outline -----------------------------------------------------
    def check_outline(self, board):
        shapes = []
        for node in walk(board):
            if head(node) not in GRAPHIC_HEADS:
                continue
            layer = sub(node, "layer")
            if layer is None or atom(layer, 1) != "Edge.Cuts":
                continue
            shapes.append(node)
        if not shapes:
            self.error(board.line, "no Edge.Cuts geometry; board outline is missing")
            return

        closed_shapes = {"gr_rect", "gr_circle", "gr_poly"}
        if any(head(s) in closed_shapes for s in shapes):
            # A rectangle/circle/polygon is intrinsically closed. Mixed outlines are
            # legal (e.g. rectangle plus a cutout), so do not require line closure.
            return

        # Endpoints are merged with a small tolerance: real boards contain
        # sub-micron numeric gaps from arcs and file conversions.
        tol = 0.001
        clusters = []
        degree = []
        edges = 0

        def cluster_id(point):
            for idx, (cx, cy) in enumerate(clusters):
                if abs(point[0] - cx) <= tol and abs(point[1] - cy) <= tol:
                    return idx
            clusters.append(point)
            degree.append(0)
            return len(clusters) - 1

        for shape in shapes:
            h = head(shape)
            if h in ("gr_line", "gr_arc"):
                start, end = sub(shape, "start"), sub(shape, "end")
                if start is None or end is None:
                    continue
                p1, p2 = xy_of(start), xy_of(end)
                if p1 is None or p2 is None:
                    continue
                degree[cluster_id(p1)] += 1
                degree[cluster_id(p2)] += 1
                edges += 1
        if edges == 0:
            self.error(shapes[0].line, "Edge.Cuts has no usable line/arc/rect outline")
            return
        dead_ends = [clusters[i] for i, d in enumerate(degree) if d % 2 == 1]
        if dead_ends:
            self.error(
                shapes[0].line,
                f"Edge.Cuts outline is open: {len(dead_ends)} unconnected endpoint(s), "
                f"first at {dead_ends[0][0]} {dead_ends[0][1]} mm",
            )

    # -- legacy / version-specific tokens ---------------------------------
    def check_legacy_tokens(self, board):
        if not self.version:
            return
        for node in walk(board):
            if head(node) == "tstamp" and self.version >= 20240108:
                self.warn(
                    node.line,
                    "(tstamp ...) is legacy for KiCad 8+; KiCad 8/9 write (uuid ...)",
                )
        # Only board-level graphics; pad custom primitives legitimately use (width ...).
        for node in board.items:
            if not isinstance(node, SList) or head(node) not in GRAPHIC_HEADS:
                continue
            if sub(node, "width") is not None and self.version >= 20221018:
                self.warn(
                    node.line,
                    f"{head(node)} uses legacy (width ...); KiCad 7+ writes (stroke (width ...) (type ...))",
                )
            if head(node) == "gr_text" and sub(node, "effects") is None:
                self.warn(node.line, "gr_text has no (effects (font (size ...)))")

    # -- geometric connectivity -------------------------------------------
    def check_dangling(self):
        if len(self.segments) > 8000:
            self.warn(0, "more than 8000 segments; skipping dangling-track analysis")
            return
        pad_points = []
        for p in self.pads:
            if p and p["net"] not in (None, 0):
                pad_points.append(p)
        # Nets/layers that already have a zone are considered connected enough.
        zone_nets = {(z["net"], z["layer"]) for z in self.zones if not z["keepout"]}

        for seg in self.segments:
            if seg["net"] in (None, 0):
                continue
            for label, point in (("start", seg["a"]), ("end", seg["b"])):
                if self.point_connected(point, seg, pad_points, zone_nets):
                    continue
                self.warn(
                    seg["line"],
                    f"segment {label} ({point[0]:g}, {point[1]:g}) does not touch a pad, via or "
                    f"track of net {seg['net']} on {seg['layer']} (dangling)",
                )

    def point_connected(self, point, seg, pad_points, zone_nets):
        tol = 0.002

        def near(a, b):
            return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

        # Same-net, same-layer tracks: endpoint or T-junction.
        for other in self.segments:
            if other is seg or other["net"] != seg["net"] or other["layer"] != seg["layer"]:
                continue
            if near(point, other["a"]) or near(point, other["b"]):
                return True
            if point_on_segment(point, other["a"], other["b"], tol):
                return True

        # Vias.
        for via in self.vias:
            if via["net"] != seg["net"]:
                continue
            if via["layers"] and seg["layer"] not in via["layers"]:
                continue
            if math.hypot(point[0] - via["x"], point[1] - via["y"]) <= via["size"] / 2.0 + tol:
                return True
            if near(point, (via["x"], via["y"])):
                return True

        # Pads.
        for p in pad_points:
            if p["net"] != seg["net"]:
                continue
            if not pad_on_layer(p, seg["layer"]):
                continue
            if point_in_pad(point, p, tol):
                return True

        # Copper pour on this net/layer can make the connection.
        if (seg["net"], seg["layer"]) in zone_nets:
            return True
        return False


def pad_on_layer(pad, layer):
    if not layer:
        return False
    if any(name.startswith("*.Cu") for name in pad["layers"]):
        return layer.endswith(".Cu") or layer.startswith("In")
    return layer in pad["layers"]


def point_in_pad(point, pad, tol):
    # Transform the point into pad-local coordinates.
    dx = point[0] - pad["x"]
    dy = point[1] - pad["y"]
    a = math.radians(-pad["rot"])
    lx = dx * math.cos(a) - dy * math.sin(a)
    ly = dx * math.sin(a) + dy * math.cos(a)
    return abs(lx) <= pad["w"] / 2.0 + tol and abs(ly) <= pad["h"] / 2.0 + tol


def point_on_segment(p, a, b, tol):
    ax, ay = a
    bx, by = b
    px, py = p
    vx, vy = bx - ax, by - ay
    length2 = vx * vx + vy * vy
    if length2 == 0:
        return math.hypot(px - ax, py - ay) <= tol
    t = ((px - ax) * vx + (py - ay) * vy) / length2
    if t < -0.001 or t > 1.001:
        return False
    cx, cy = ax + t * vx, ay + t * vy
    return math.hypot(px - cx, py - cy) <= tol


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description="Structural pre-flight checks for a KiCad .kicad_pcb file")
    ap.add_argument("board", help="path to the .kicad_pcb file")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    ap.add_argument("--no-connectivity", action="store_true", help="skip dangling-track analysis")
    args = ap.parse_args(argv)

    try:
        with open(args.board, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print(f"error: cannot read {args.board}: {exc}", file=sys.stderr)
        return 2

    try:
        checker = Checker(text, args.board)
        issues = checker.run(connectivity=not args.no_connectivity)
    except ParseError as exc:
        print(f"{args.board}: PARSE ERROR: {exc}", file=sys.stderr)
        return 2

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    for issue in sorted(issues, key=lambda i: (i.severity != "error", i.line, i.message)):
        print(issue)

    version = VERSIONS.get(checker.version, str(checker.version) if checker.version else "unknown")
    print(
        f"\n{args.board}: KiCad {version}, {len(checker.nets)} nets, "
        f"{len(checker.footprints)} footprints, {len(checker.segments)} segments, "
        f"{len(checker.vias)} vias, {len(checker.zones)} zones"
    )
    print(f"{len(errors)} error(s), {len(warnings)} warning(s)")

    if errors or (args.strict and warnings):
        print(
            "\nThis tool only checks structure and geometry. Run KiCad's own DRC for clearance, "
            "silk, mask and electrical rules:\n"
            "  kicad-cli pcb drc --severity-all --exit-code-violations <board>",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
