# KiCad Board File Format (`.kicad_pcb`) — distilled reference

Source: https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/index.html  
Common syntax (layers, footprints, pads, graphics, zones): https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html

This is a working summary for generating KiCad s-expression board files. When in doubt, consult the source docs above.

## File

- Extension: `.kicad_pcb`
- Format: KiCad s-expression (KiCad 4.0+ lineage; current docs cover KiCad 6.0+ conventions). Strings are quoted where appropriate.

## Top-level layout

Typical sections (header must be first; some sections may be omitted if empty, but a usable board usually needs most of them):

1. Header: `(kicad_pcb ...)`
2. `(general ...)`
3. Page settings (`(paper ...)` / page section as used by KiCad)
4. `(layers ...)`
5. `(setup ...)` (may include `stackup`, `pcbplotparams`)
6. `(property ...)` (optional)
7. `(net ...)` entries
8. Footprints (`(footprint ...)` or embedded footprint definitions used on the board)
9. Graphic items (`(gr_text ...)`, `(gr_line ...)`, etc.)
10. Images (optional)
11. Tracks: `(segment ...)`, `(via ...)`, `(arc ...)`
12. `(zone ...)` entries
13. `(group ...)` entries (optional)

## Header (required)

```lisp
(kicad_pcb
  (version VERSION)      ; YYYYMMDD date format per docs
  (generator GENERATOR)  ; program identifier; third-party generators should use their own identifier
  ; ... sections ...
)
```

## General (required)

```lisp
  (general
    (thickness THICKNESS) ; overall board thickness, mm
  )
```

## Layers (required)

```lisp
  (layers
    (ORDINAL "CANONICAL_NAME" TYPE ["USER_NAME"])
    ; ...
  )
```

- `ORDINAL`: integer for stack ordering/mapping.
- `CANONICAL_NAME`: internal layer name (e.g., `F.Cu`, `B.Cu`, `Edge.Cuts`).
- `TYPE`: `jumper`, `mixed`, `power`, `signal`, or `user`.

Common 2-layer set includes signal copper `F.Cu`/`B.Cu`, technical layers (`F.SilkS`, `B.SilkS`, `F.Mask`, `B.Mask`, etc.), and `Edge.Cuts`.

## Setup (required)

Contains manufacturing/behavior settings; exact content varies by KiCad version and board needs.

```lisp
  (setup
    [(stackup ...)]
    (pad_to_mask_clearance CLEARANCE)
    [(solder_mask_min_width MINIMUM_WIDTH)]
    [(pad_to_paste_clearance CLEARANCE)]
    [(pad_to_paste_clearance_ratio RATIO)]
    [(aux_axis_origin X Y)]
    [(grid_origin X Y)]
    (pcbplotparams ...)
  )
```

- `stackup` may define per-layer type/thickness/material/dielectric and board options like `copper_finish`, `edge_connector`, `castellated_pads`, `edge_plating`.
- `pcbplotparams` stores last plot settings; include reasonable defaults if generating a complete file and the target KiCad expects it.

## Nets (required)

Define nets before/for reference by conductive items. Net 0 is commonly the empty net.

```lisp
  (net 0 "")
  (net 1 "VCC")
  (net 2 "GND")
  (net 3 "/SIGNAL")
```

- Ordinal is an integer; name is a string. Keep ordinals unique.

## Footprints

Board files contain footprint instances (see intro docs for the full `footprint` token). Each placed footprint typically includes library link/name context as appropriate, placement `(layer ...)`, `(at X Y [rot])`, `(uuid ...)`, optional properties/path, and child items: `fp_text`, `fp_line`/shapes, `pad`, zones, 3D model, etc.

Footprint pads connect nets via:

```lisp
(pad "1" thru_hole circle (at X Y) (size SX SY) (drill D)
  (layers *.Cu *.Mask F.SilkS)
  (net 1 "VCC")
  (uuid ...))
```

## Tracks

### Segment

```lisp
  (segment
    (start X Y)
    (end X Y)
    (width WIDTH)
    (layer LAYER_DEFINITION)   ; canonical copper layer, e.g. F.Cu
    [(locked)]
    (net NET_NUMBER)
    (tstamp UUID)
  )
```

### Via

```lisp
  (via
    [blind|micro]              ; omit for through-hole via
    [(locked)]
    (at X Y)
    (size DIAMETER)
    (drill DIAMETER)
    (layers F.Cu B.Cu)         ; canonical layer set connected
    [(remove_unused_layers)]
    [(keep_end_layers)]
    [(free)]
    (net NET_NUMBER)
    (tstamp UUID)
  )
```

### Arc

```lisp
  (arc
    (start X Y)
    (mid X Y)                  ; midpoint along the arc
    (end X Y)
    (width W)                  ; note: intro/examples may vary; follow docs for target version
    (layer LAYER_DEFINITION)
    [(locked)]
    (net NET_NUMBER)
    (tstamp UUID)
  )
```

## Zones

Zones are used for copper fills and keepout areas. Board-level zones use `(zone ...)` with `net`/`net_name` (keepouts use empty net name as documented), `layer`, `uuid`, hatch, connectivity/fill/min thickness settings, and an outline polygon:

```lisp
  (zone
    (net 2)
    (net_name "GND")
    (layer B.Cu)
    (uuid UUID)
    (hatch edge 0.508)
    (connect_pads (clearance 0.2))
    (min_thickness 0.1778)
    (fill yes (thermal_gap 0.254) (thermal_bridge_width 0.4064))
    (polygon (pts (xy X1 Y1) (xy X2 Y2) (xy X3 Y3) (xy X4 Y4)))
  )
```

For keepout zones, include `(keepout ...)` settings per the intro docs. Filled polygons/segments may appear after KiCad fills; generators can provide the outline and fill settings.

## Design/Output checklist

- [ ] `(kicad_pcb (version ...) (generator ...))` present.
- [ ] `general`, `layers`, `setup` present.
- [ ] All nets declared; conductive references use valid ordinals.
- [ ] Board outline closed on `Edge.Cuts`.
- [ ] Footprints have placement, pads, and required text/graphics as needed.
- [ ] Tracks/vias/arcs on valid copper layers with correct nets.
- [ ] Parentheses balanced; strings/identifiers follow s-expression rules.
- [ ] All dimensions in mm; no unnecessary exponential notation.
- [ ] Unique identifiers used where required (`uuid`/`tstamp`).

## Notes

- KiCad evolves the format; use the `version` date and tokens compatible with the user's target KiCad release when known.
- Prefer fetching the current docs over relying on memory for edge-case attributes (zone fill modes, pad options, custom pads, stackup details).
- For programmatic generation beyond static files, KiCad's IPC API and PCB Python bindings exist (see dev docs index), but this skill focuses on correct `.kicad_pcb` output.
