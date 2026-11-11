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
    offsets = {
        ref: (
            float((comp.get("_placement_offset") or (0.0, 0.0))[0]),
            float((comp.get("_placement_offset") or (0.0, 0.0))[1]),
        )
        for ref, (comp, _, _) in by_ref.items()
    }

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
    pad_positions = {
        ref: {
            str(pad): (float(point[0]), float(point[1]))
            for pad, point in (comp.get("_placement_pad_numbers") or {}).items()
        }
        for ref, (comp, _, _) in by_ref.items()
    }
    proximity = cfg.get("proximity", [])
    pad_proximity = cfg.get("pad_proximity", [])
    for rule in pad_proximity:
        pair = rule.get("pads", [])
        if len(pair) != 2:
            raise ValueError("each placement.pad_proximity rule requires exactly two pad endpoints")
        for endpoint in pair:
            if not isinstance(endpoint, dict) or endpoint.get("ref") not in by_ref:
                raise ValueError("placement.pad_proximity references an unknown component")
            ref, pad = endpoint["ref"], str(endpoint.get("pad", ""))
            if not pad or pad not in pad_positions[ref]:
                raise ValueError(f"placement.pad_proximity references unknown pad {ref}:{pad}")
        if float(rule.get("weight", 1.0)) <= 0:
            raise ValueError("placement.pad_proximity weight must be greater than zero")
    power_loops = cfg.get("power_loops", [])
    for rule in power_loops:
        for leg_name in ("supply", "return"):
            pair = rule.get(leg_name, [])
            if len(pair) != 2:
                raise ValueError(f"each placement.power_loops rule requires exactly two {leg_name} pad endpoints")
            for endpoint in pair:
                if not isinstance(endpoint, dict) or endpoint.get("ref") not in by_ref:
                    raise ValueError(f"placement.power_loops {leg_name} leg references an unknown component")
                ref, pad = endpoint["ref"], str(endpoint.get("pad", ""))
                if not pad or pad not in pad_positions[ref]:
                    raise ValueError(f"placement.power_loops references unknown pad {ref}:{pad}")
        if rule["supply"][0] == rule["supply"][1] or rule["return"][0] == rule["return"][1]:
            raise ValueError("placement.power_loops legs must connect two distinct pads")
        if float(rule.get("weight", 1.0)) <= 0:
            raise ValueError("placement.power_loops weight must be greater than zero")
    pad_paths = cfg.get("pad_paths", [])
    for rule in pad_paths:
        endpoints = rule.get("pads", [])
        if len(endpoints) < 2:
            raise ValueError("each placement.pad_paths rule requires at least two ordered pad endpoints")
        if rule.get("closed", False) and len(endpoints) < 3:
            raise ValueError("closed placement.pad_paths rules require at least three pad endpoints")
        for endpoint in endpoints:
            if not isinstance(endpoint, dict) or endpoint.get("ref") not in by_ref:
                raise ValueError("placement.pad_paths references an unknown component")
            ref, pad = endpoint["ref"], str(endpoint.get("pad", ""))
            if not pad or pad not in pad_positions[ref]:
                raise ValueError(f"placement.pad_paths references unknown pad {ref}:{pad}")
        if float(rule.get("weight", 1.0)) <= 0:
            raise ValueError("placement.pad_paths weight must be greater than zero")
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
        cx, cy = offsets[ref]
        return (x + cx - w / 2, y + cy - h / 2, x + cx + w / 2, y + cy + h / 2)

    def valid(ref: str, x: float, y: float, ignore: str | None = None) -> bool:
        _, w, h = by_ref[ref]
        cx, cy = offsets[ref]
        if (x + cx - w / 2 < margin or y + cy - h / 2 < margin
                or x + cx + w / 2 > width - margin or y + cy + h / 2 > height - margin):
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
            _, w, h = by_ref[ref]
            cx, cy = offsets[ref]
            rect = rectangle(ref, x, y)
            conflicts = [
                (other, r) for other, r in rects.items()
                if _overlap(rect, r, clearance)
            ]
            keep = [k for k in keepouts if _overlap(rect, k, 0.0)]
            raise ValueError(
                f"fixed component {ref} violates board bounds, keepouts, or clearance: "
                f"origin=({x},{y}) size=({w:.3f},{h:.3f}) offset=({cx:.3f},{cy:.3f}) "
                f"rect={tuple(round(v, 3) for v in rect)} "
                f"bounds=({margin},{margin},{width - margin},{height - margin}) "
                f"conflicts={[(o, tuple(round(v, 3) for v in r)) for o, r in conflicts]} "
                f"keepouts={keep}"
            )
        rot = float(at.get("rotation_deg", 0))
        positions[ref] = (x, y, rot)
        rects[ref] = rectangle(ref, x, y)

    def candidate_axis(limit: float, size: float, offset: float):
        lo, hi = margin - offset + size / 2, limit - margin - offset - size / 2
        if lo > hi:
            return []
        count = int((hi - lo) // step)
        values = [lo + i * step for i in range(count + 1)]
        if not values or hi - values[-1] > step * 0.35:
            values.append(hi)
        return values

    def candidates(ref: str):
        _, w, h = by_ref[ref]
        cx, cy = offsets[ref]
        xs, ys = candidate_axis(width, w, cx), candidate_axis(height, h, cy)
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
        # Explicit pad-to-pad objectives target known electrical pins (for
        # example, a particular IC supply pad and its bypass-capacitor pad).
        for rule in pad_proximity:
            left, right = rule["pads"]
            if left["ref"] == ref and right["ref"] in current:
                own_ref, own_pad, other_ref, other_pad = left["ref"], str(left["pad"]), right["ref"], str(right["pad"])
            elif right["ref"] == ref and left["ref"] in current:
                own_ref, own_pad, other_ref, other_pad = right["ref"], str(right["pad"]), left["ref"], str(left["pad"])
            else:
                continue
            ox, oy, _ = current[other_ref]
            px, py = pad_positions[own_ref][own_pad]
            opx, opy = pad_positions[other_ref][other_pad]
            distance = abs((x + px) - (ox + opx)) + abs((y + py) - (oy + opy))
            cost += float(rule.get("weight", 1.0)) * distance
        # Score both geometric legs of a local bypass power loop.
        for rule in power_loops:
            weight = float(rule.get("weight", 1.0))
            for leg_name in ("supply", "return"):
                left, right = rule[leg_name]
                if left["ref"] == ref and right["ref"] in current:
                    own_endpoint, other_endpoint = left, right
                elif right["ref"] == ref and left["ref"] in current:
                    own_endpoint, other_endpoint = right, left
                else:
                    continue
                other_ref = other_endpoint["ref"]
                ox, oy, _ = current[other_ref]
                px, py = pad_positions[ref][str(own_endpoint["pad"])]
                opx, opy = pad_positions[other_ref][str(other_endpoint["pad"])]
                cost += weight * (abs((x + px) - (ox + opx)) + abs((y + py) - (oy + opy)))
        # Ordered critical pad paths support datasheet-derived switching
        # loops, crystal networks, and RF matching chains. Closed paths add
        # the final-to-first segment. This is geometric distance only.
        for rule in pad_paths:
            endpoints = rule["pads"]
            segments = list(zip(endpoints, endpoints[1:]))
            if rule.get("closed", False):
                segments.append((endpoints[-1], endpoints[0]))
            for left, right in segments:
                if left["ref"] == ref and right["ref"] in current:
                    own_endpoint, other_endpoint = left, right
                elif right["ref"] == ref and left["ref"] in current:
                    own_endpoint, other_endpoint = right, left
                else:
                    continue
                other_ref = other_endpoint["ref"]
                ox, oy, _ = current[other_ref]
                px, py = pad_positions[ref][str(own_endpoint["pad"])]
                opx, opy = pad_positions[other_ref][str(other_endpoint["pad"])]
                cost += float(rule.get("weight", 1.0)) * (
                    abs((x + px) - (ox + opx)) + abs((y + py) - (oy + opy))
                )
        # Explicit component-level proximity hints remain soft objectives.
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
