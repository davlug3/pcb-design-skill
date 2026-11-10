"""Deterministic, connectivity-aware PCB component placement.

Uses footprint bounding boxes, net connectivity, board bounds, clearances and
keepouts. It is a heuristic: it does not replace electrical/mechanical review,
routing, thermal analysis, or KiCad DRC.
"""
from __future__ import annotations

from itertools import product
from math import hypot
from typing import Any


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float], clearance: float) -> bool:
    return not (a[2] + clearance <= b[0] or b[2] + clearance <= a[0]
                or a[3] + clearance <= b[1] or b[3] + clearance <= a[1])


def _nets(comp: dict[str, Any]) -> set[str]:
    return {str(net) for net in (comp.get("pads") or {}).values() if net and str(net) not in {"", "unconnected"}}


def optimize_placement(
    entries: list[tuple[dict[str, Any], float, float]],
    spec: dict[str, Any],
) -> dict[str, tuple[float, float, float]]:
    """Return ref -> (x_mm, y_mm, rotation_deg), respecting hard constraints."""
    board = spec["board"]
    width, height = float(board["width_mm"]), float(board["height_mm"])
    cfg = spec.get("placement", {})
    margin = float(cfg.get("margin_mm", 2.0))
    clearance = float(cfg.get("clearance_mm", 1.0))
    step = max(float(cfg.get("grid_mm", 2.54)), 1.0)
    keepouts = [
        (float(k["x_mm"]), float(k["y_mm"]),
         float(k["x_mm"]) + float(k["width_mm"]),
         float(k["y_mm"]) + float(k["height_mm"]))
        for k in cfg.get("keepouts", [])
    ]
    fixed_refs = set(cfg.get("fixed_refs", []))
    by_ref = {c["ref"]: (c, max(float(w), 0.1), max(float(h), 0.1)) for c, w, h in entries}
    if len(by_ref) != len(entries):
        raise ValueError("component references must be unique for automatic placement")

    positions: dict[str, tuple[float, float, float]] = {}
    rects: dict[str, tuple[float, float, float, float]] = {}
    nets = {ref: _nets(c) for ref, (c, _, _) in by_ref.items()}
    pad_offsets = {
        ref: {
            str(net): [(float(point[0]), float(point[1])) for point in points]
            for net, points in (comp.get("_placement_pads") or {}).items()
        }
        for ref, (comp, _, _) in by_ref.items()
    }
    proximity = cfg.get("proximity", [])
    for rule in proximity:
        refs = rule.get("refs", [])
        if len(refs) != 2 or refs[0] == refs[1]:
            raise ValueError("each placement.proximity rule requires two distinct component refs")
        unknown = [ref for ref in refs if ref not in by_ref]
        if unknown:
            raise ValueError(f"placement.proximity references unknown component(s): {', '.join(unknown)}")
        if float(rule.get("weight", 1.0)) <= 0:
            raise ValueError("placement.proximity weight must be greater than zero")

    net_degree: dict[str, int] = {}
    for component_nets in nets.values():
        for net in component_nets:
            net_degree[net] = net_degree.get(net, 0) + 1

    def rectangle(ref: str, x: float, y: float):
        _, w, h = by_ref[ref]
        return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)

    def valid(ref: str, x: float, y: float, ignore: str | None = None) -> bool:
        _, w, h = by_ref[ref]
        if x - w / 2 < margin or y - h / 2 < margin or x + w / 2 > width - margin or y + h / 2 > height - margin:
            return False
        rect = rectangle(ref, x, y)
        if any(_overlap(rect, k, 0.0) for k in keepouts):
            return False
        return not any(other != ignore and _overlap(rect, r, clearance) for other, r in rects.items())

    # Explicitly anchored parts are hard constraints, never silently moved.
    for ref in sorted(fixed_refs):
        if ref not in by_ref:
            raise ValueError(f"placement.fixed_refs contains unknown component {ref}")
        comp, _, _ = by_ref[ref]
        at = comp.get("at")
        if not at or "x_mm" not in at or "y_mm" not in at:
            raise ValueError(f"fixed component {ref} requires at.x_mm and at.y_mm")
        x, y = float(at["x_mm"]), float(at["y_mm"])
        if not valid(ref, x, y):
            raise ValueError(f"fixed component {ref} violates board bounds, keepouts, or clearance")
        rot = float(at.get("rotation_deg", 0))
        positions[ref] = (x, y, rot)
        rects[ref] = rectangle(ref, x, y)

    def candidate_axis(limit: float, size: float):
        lo, hi = margin + size / 2, limit - margin - size / 2
        if lo > hi:
            return []
        count = int((hi - lo) // step)
        values = [lo + i * step for i in range(count + 1)]
        if not values or hi - values[-1] > step * 0.35:
            values.append(hi)
        return values

    def candidates(ref: str):
        _, w, h = by_ref[ref]
        xs, ys = candidate_axis(width, w), candidate_axis(height, h)
        return product(xs, ys)

    def score(ref: str, x: float, y: float, current: dict[str, tuple[float, float, float]]) -> float:
        own = nets[ref]
        cost = 0.0
        for other, (ox, oy, _) in current.items():
            if other == ref:
                continue
            shared = own & nets[other]
            for net in shared:
                # Use actual transformed pad locations when both footprints expose
                # pad geometry. Fall back to component origins for legacy callers.
                own_pads = pad_offsets[ref].get(net, [])
                other_pads = pad_offsets[other].get(net, [])
                if own_pads and other_pads:
                    distance = min(
                        abs((x + px) - (ox + opx)) + abs((y + py) - (oy + opy))
                        for px, py in own_pads
                        for opx, opy in other_pads
                    )
                else:
                    distance = abs(x - ox) + abs(y - oy)
                # Multi-drop nets are discounted; this remains a placement
                # heuristic and does not model trace routing or return paths.
                cost += distance / max(net_degree.get(net, 2) - 1, 1)
        # Explicit component-level proximity hints are soft objectives. They
        # guide placement without pretending to know pad-level electrical paths.
        for rule in proximity:
            a, b = rule["refs"]
            other = b if a == ref else a if b == ref else None
            if other in current:
                ox, oy, _ = current[other]
                weight = float(rule.get("weight", 1.0))
                cost += weight * (abs(x - ox) + abs(y - oy))
        prefix = ref[:1].upper()
        if prefix in {"J", "X", "P"}:
            edge = min(x - margin, width - margin - x, y - margin, height - margin - y)
            cost += 8.0 * edge
        # Gentle compactness term breaks ties without dominating connectivity.
        cost += 0.02 * hypot(x - width / 2, y - height / 2)
        return cost

    # Place mechanically constrained connectors first, then highest connectivity.
    pending = [r for r in by_ref if r not in positions]
    pending.sort(key=lambda r: (
        0 if r[:1].upper() in {"J", "X", "P"} else 1,
        -len(nets[r]),
        -(by_ref[r][1] * by_ref[r][2]),
        r,
    ))
    for ref in pending:
        comp, _, _ = by_ref[ref]
        at = comp.get("at") or {}
        rotation = float(at.get("rotation_deg", 0))
        best = None
        for x, y in candidates(ref):
            if not valid(ref, x, y):
                continue
            value = score(ref, x, y, positions)
            key = (value, y, x)
            if best is None or key < best[0]:
                best = (key, float(x), float(y))
        if best is None:
            raise RuntimeError(f"auto-placement could not find room for {ref}")
        _, x, y = best
        positions[ref] = (x, y, rotation)
        rects[ref] = rectangle(ref, x, y)

    # Coordinate-descent refinement reduces total estimated net length while
    # preserving all hard geometry constraints and fixed anchors.
    movable = [r for r in pending if r not in fixed_refs]
    for _ in range(2):
        for ref in movable:
            old = positions[ref]
            rects.pop(ref)
            best = (score(ref, old[0], old[1], positions), old[0], old[1])
            for x, y in candidates(ref):
                if not valid(ref, x, y):
                    continue
                value = score(ref, float(x), float(y), positions)
                candidate = (value, float(y), float(x))
                if candidate < (best[0], best[2], best[1]):
                    best = (value, float(x), float(y))
            positions[ref] = (best[1], best[2], old[2])
            rects[ref] = rectangle(ref, best[1], best[2])

    return positions
