"""Routing-metric feedback for optional placement refinement.

This is a deterministic geometry heuristic. It uses long routed nets to
suggest soft component proximity objectives; it is not a router, electrical
optimizer, or sign-off calculation.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def build_feedback_spec(
    spec: dict[str, Any],
    metrics: dict[str, Any],
    *,
    top_nets: int = 3,
    weight: float = 1.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Add soft proximity hints for component pairs on the longest routed nets."""
    if top_nets < 1:
        raise ValueError("top_nets must be at least 1")
    if weight <= 0:
        raise ValueError("weight must be greater than zero")
    lengths = metrics.get("track_length_by_net_mm") or {}
    ranked = sorted(
        ((str(net), float(length)) for net, length in lengths.items() if float(length) > 0),
        key=lambda item: (-item[1], item[0]),
    )[:top_nets]
    if not ranked:
        return deepcopy(spec), []

    net_refs: dict[str, list[str]] = {}
    for component in spec.get("components", []):
        ref = str(component.get("ref", ""))
        for net in set(str(value) for value in (component.get("pads") or {}).values() if value):
            net_refs.setdefault(net, []).append(ref)

    max_length = max(length for _, length in ranked)
    rules: list[dict[str, Any]] = []
    for net, length in ranked:
        refs = sorted(set(net_refs.get(net, [])))
        if len(refs) < 2:
            continue
        # A star avoids O(n^2) objectives on large multi-drop nets.
        for other in refs[1:]:
            rules.append({
                "refs": [refs[0], other],
                "weight": round(float(weight) * length / max_length, 6),
                "reason": f"routing feedback: {net} measured {length:.3f} mm",
            })

    result = deepcopy(spec)
    placement = result.setdefault("placement", {})
    existing = placement.setdefault("proximity", [])
    seen = {tuple(sorted(rule.get("refs", []))) for rule in existing}
    for rule in rules:
        key = tuple(sorted(rule["refs"]))
        if key not in seen:
            existing.append(rule)
            seen.add(key)
    return result, rules


def quality_key(metrics: dict[str, Any], critical_nets: list[str]) -> tuple[float, ...]:
    """Lexicographic objective: unrouted items, critical copper length, then vias."""
    unconnected = int(metrics.get("unconnected_items", -1))
    # Unknown connectivity must not be treated as better than a measured result.
    unconnected_score = float(unconnected if unconnected >= 0 else 10**9)
    by_net = metrics.get("track_length_by_net_mm") or {}
    critical_length = sum(float(by_net.get(net, 0.0)) for net in critical_nets)
    vias = float(metrics.get("via_count", 10**9))
    return (unconnected_score, critical_length, vias)
