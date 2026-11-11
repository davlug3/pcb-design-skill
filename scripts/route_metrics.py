"""Quantitative routing metrics for a KiCad PCB.

These are geometry/connectivity measurements, not electrical or SI sign-off.
Track lengths come from KiCad's PCB_TRACK.GetLength() internal-unit result.
"""
from __future__ import annotations
from typing import Any

IU_PER_MM = 1_000_000.0

def _layer_name(board: Any, layer: Any) -> str:
    try:
        return str(board.GetLayerName(layer))
    except Exception:
        return str(layer)

def collect_board_metrics(board: Any) -> dict[str, Any]:
    """Measure routed copper and connectivity from an already-loaded pcbnew board."""
    tracks = list(board.GetTracks())
    track_length = 0.0
    track_segments = 0
    via_count = 0
    layer_lengths: dict[str, float] = {}
    net_lengths: dict[str, float] = {}
    length_methods: set[str] = set()
    for item in tracks:
        if callable(getattr(item, "GetDrillValue", None)):
            via_count += 1
            continue
        track_segments += 1
        net = ""
        try:
            net = str(item.GetNetname() or "")
        except Exception:
            pass
        try:
            length_mm = float(item.GetLength()) / IU_PER_MM
            length_methods.add("pcbnew.GetLength")
        except Exception:
            try:
                start, end = item.GetStart(), item.GetEnd()
                dx = float(start.x - end.x) / IU_PER_MM
                dy = float(start.y - end.y) / IU_PER_MM
                length_mm = (dx * dx + dy * dy) ** 0.5
                length_methods.add("endpoint_chord_fallback")
            except Exception as exc:
                raise RuntimeError(f"could not measure a PCB track segment: {exc}") from exc
        track_length += length_mm
        if net:
            net_lengths[net] = net_lengths.get(net, 0.0) + length_mm
        try:
            layer = _layer_name(board, item.GetLayer())
            layer_lengths[layer] = layer_lengths.get(layer, 0.0) + length_mm
        except Exception:
            layer_lengths["unknown"] = layer_lengths.get("unknown", 0.0) + length_mm
    try:
        board.BuildConnectivity()
        connectivity = board.GetConnectivity()
        try:
            unconnected = int(connectivity.GetUnconnectedCount(False))
        except Exception:
            unconnected = int(connectivity.GetUnconnectedCount())
    except Exception:
        unconnected = -1
    return {
        "track_length_mm": round(track_length, 3),
        "track_segment_count": track_segments,
        "via_count": via_count,
        "via_layer_transition_count": via_count,
        "unconnected_items": unconnected,
        "clearance_violations": None,
        "track_length_by_layer_mm": {k: round(v, 3) for k, v in sorted(layer_lengths.items())},
        "track_length_by_net_mm": {k: round(v, 3) for k, v in sorted(net_lengths.items())},
        "length_measurement_methods": sorted(length_methods),
        "notes": [
            "KiCad PCB track lengths are geometric copper-segment lengths; zone current paths are not included.",
            "Via-layer transition count is a via-count proxy and does not model detailed layer-stack topology.",
            "Clearance violations are populated only after the KiCad DRC report is available.",
            "These metrics do not establish impedance, return-path quality, current capacity, thermal, EMC, or safety compliance.",
        ],
    }
