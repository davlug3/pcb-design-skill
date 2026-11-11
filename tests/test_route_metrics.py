"""Tests for KiCad routing-quality measurements."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import route_metrics  # noqa: E402

class Track:
    def __init__(self, length, layer, net):
        self.length, self.layer, self.net = length, layer, net
    def GetLength(self): return self.length
    def GetLayer(self): return self.layer
    def GetNetname(self): return self.net

class Via:
    def GetDrillValue(self): return 300_000

class Connectivity:
    def GetUnconnectedCount(self, *_args): return 2

class Board:
    def GetTracks(self): return [Track(2_500_000, 0, "GND"), Track(1_250_000, 31, "VCC"), Via()]
    def GetLayerName(self, layer): return {0: "F.Cu", 31: "B.Cu"}[layer]
    def BuildConnectivity(self): pass
    def GetConnectivity(self): return Connectivity()

class Point:
    def __init__(self, x, y): self.x, self.y = x, y

class FallbackTrack:
    def GetStart(self): return Point(0, 0)
    def GetEnd(self): return Point(3_000_000, 4_000_000)
    def GetLayer(self): return 0
    def GetNetname(self): return "SIG"

class MetricsTests(unittest.TestCase):
    def test_reports_lengths_vias_layers_nets_and_unconnected_items(self):
        result = route_metrics.collect_board_metrics(Board())
        self.assertEqual(result["track_length_mm"], 3.75)
        self.assertEqual(result["track_segment_count"], 2)
        self.assertEqual(result["via_count"], 1)
        self.assertEqual(result["via_layer_transition_count"], 1)
        self.assertEqual(result["unconnected_items"], 2)
        self.assertEqual(result["track_length_by_layer_mm"], {"B.Cu": 1.25, "F.Cu": 2.5})
        self.assertEqual(result["track_length_by_net_mm"], {"GND": 2.5, "VCC": 1.25})

    def test_endpoint_fallback_is_explicit_and_uses_euclidean_length(self):
        class FallbackBoard(Board):
            def GetTracks(self): return [FallbackTrack()]
        result = route_metrics.collect_board_metrics(FallbackBoard())
        self.assertEqual(result["track_length_mm"], 5.0)
        self.assertEqual(result["length_measurement_methods"], ["endpoint_chord_fallback"])

if __name__ == "__main__":
    unittest.main()
