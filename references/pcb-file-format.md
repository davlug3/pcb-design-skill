# KiCad Board File Format (`.kicad_pcb`) — KiCad 8/9 reference

Sources:
- https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/index.html (board format)
- https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html (common syntax)
- Verified against `.kicad_pcb` files written by KiCad 6.0, 7.0, 8.0 and 9.0 (`versions 20211014`, `20221018`, `20240108`, `20241229`), 2026-11, plus the shipped `assets/*.kicad_pcb`.

The developer docs still show some legacy tokens (`(width ...)` graphics, unquoted strings, old examples). Real KiCad 7+ output uses quoted strings and `(stroke ...)` graphics, but **KiCad 7 still uses `(tstamp ...)` object IDs — `(uuid ...)` starts in KiCad 8**. Where docs and real files disagree, follow the real file.

## Version dialect matrix

| KiCad | `(version ...)` | Track/zone ID | Graphic outline | Footprint fields | Notes |
|-------|-----------------|---------------|-----------------|------------------|-------|
| 6.0 | `20211014` | `(tstamp ...)` | `(width W)` | `(fp_text reference ...)` | strings only quoted when necessary |
| 7.0 | `20221018` | `(tstamp ...)` | `(stroke (width W) (type T))` | `(fp_text reference ...)` | strings quoted; `generator` often still bare |
| 8.0 | `20240108` | `(uuid ...)` | `(stroke ...)` | `(property "Reference" ...)` | `(generator_version "8.0")` |
| 9.0 | `20241229` | `(uuid ...)` | `(stroke ...)` | `(property "Reference" ...)` | new layer ordinals, `(embedded_fonts no)`, `(tenting front back)` |

Emit exactly one dialect. A v9 header with v6 tokens is the most common load failure.

## Top-level layout

Header must be the first token; order of the rest is not critical, but this is what KiCad writes:

1. `(kicad_pcb ...)`
2. `(general ...)`
3. `(paper ...)` / `(title_block ...)`
4. `(layers ...)`
5. `(setup ...)`
6. `(net ...)` entries
7. `(footprint ...)` instances
8. Board graphics (`(gr_line ...)`, `(gr_text ...)`, …)
9. Tracks: `(segment ...)`, `(via ...)`, `(arc ...)`
10. `(zone ...)`
11. `(group ...)` (optional)
12. `(embedded_fonts no)`

A usable board needs at least `general`, `paper`, `layers`, `setup`, a net table and an `Edge.Cuts` outline.

## Header

```lisp
(kicad_pcb
  (version 20241229)            ; date stamp, not "9.0"
  (generator "pcbnew")          ; third-party tools must use their own identifier
  (generator_version "9.0")     ; required by KiCad 8/9 output
  ...
)
```

`generator` is a quoted string in KiCad 8/9 (`(generator "pcbnew")`); KiCad 6/7 commonly write it bare (`(generator pcbnew)`). Do **not** claim `pcbnew` if a script produced the file.

## General and page

```lisp
(general
  (thickness 1.6)               ; mm, overall board thickness
  (legacy_teardrops no)         ; v8/v9
)
(paper "A4")
(title_block
  (title "LED blinky")
  (rev "A")
  (comment 1 "Verify with KiCad DRC before fabrication.")
)
```

`thickness` is mechanically significant; 1.6 mm FR4 is the common default.

## Layers

```lisp
(layers
  (0  "F.Cu"     signal)
  (2  "B.Cu"     signal)
  (9  "F.Adhes"  user "F.Adhesive")
  (11 "B.Adhes"  user "B.Adhesive")
  (13 "F.Paste"  user)
  (15 "B.Paste"  user)
  (5  "F.SilkS"  user "F.Silkscreen")
  (7  "B.SilkS"  user "B.Silkscreen")
  (1  "F.Mask"   user)
  (3  "B.Mask"   user)
  (17 "Dwgs.User" user "User.Drawings")
  (19 "Cmts.User" user "User.Comments")
  (21 "Eco1.User" user "User.Eco1")
  (23 "Eco2.User" user "User.Eco2")
  (25 "Edge.Cuts" user)
  (27 "Margin"   user)
  (31 "F.CrtYd"  user "F.Courtyard")
  (29 "B.CrtYd"  user "B.Courtyard")
  (35 "F.Fab"    user)
  (33 "B.Fab"    user)
)
```

Entry shape: `(ORDINAL "CANONICAL_NAME" TYPE ["USER_NAME"])`. `TYPE` is `jumper`, `mixed`, `power`, `signal` or `user`.

**Ordinals matter and are not sequential — and the scheme changed in KiCad 9.** Use the table that matches the file's version.

### KiCad 9 (`(version 20241229)`)

| Layer | Ordinal | Layer | Ordinal |
|-------|---------|-------|---------|
| `F.Cu` | 0 | `B.CrtYd` | 29 |
| `F.Mask` | 1 | `F.CrtYd` | 31 |
| `B.Cu` | 2 | `B.Fab` | 33 |
| `B.Mask` | 3 | `F.Fab` | 35 |
| `F.SilkS` | 5 | `User.1` | 39 |
| `B.SilkS` | 7 | `User.2` | 41 |
| `F.Adhes` | 9 | `User.3` | 43 |
| `B.Adhes` | 11 | `User.4` | 45 |
| `F.Paste` | 13 | `User.5` | 47 |
| `B.Paste` | 15 | `User.6` | 49 |
| `Dwgs.User` | 17 | `User.7` | 51 |
| `Cmts.User` | 19 | `User.8` | 53 |
| `Eco1.User` | 21 | `User.9` | 55 |
| `Eco2.User` | 23 | | |
| `Edge.Cuts` | 25 | | |
| `Margin` | 27 | | |

### KiCad 6/7/8 (historical layer IDs)

| Layer | Ordinal | Layer | Ordinal |
|-------|---------|-------|---------|
| `F.Cu` | 0 | `B.CrtYd` | 46 |
| `In1.Cu` … `In30.Cu` | 1 … 30 | `F.CrtYd` | 47 |
| `B.Cu` | 31 | `B.Fab` | 48 |
| `B.Adhes` | 32 | `F.Fab` | 49 |
| `F.Adhes` | 33 | `User.1` | 50 |
| `B.Paste` | 34 | `User.2` | 51 |
| `F.Paste` | 35 | … | … |
| `B.SilkS` | 36 | `User.9` | 58 |
| `F.SilkS` | 37 | | |
| `B.Mask` | 38 | | |
| `F.Mask` | 39 | | |
| `Dwgs.User` | 40 | | |
| `Cmts.User` | 41 | | |
| `Eco1.User` | 42 | | |
| `Eco2.User` | 43 | | |
| `Edge.Cuts` | 44 | | |
| `Margin` | 45 | | |

Inner copper: KiCad 9 uses `In1.Cu`=4, `In2.Cu`=6, … `In30.Cu`=62; KiCad 6/7/8 use `In1.Cu`=1 … `In30.Cu`=30. A 4-layer stack is `F.Cu`, `In1.Cu`, `In2.Cu`, `B.Cu` (v9 ordinals 0, 4, 6, 2; v6–8 ordinals 0, 1, 2, 31). Always copy the ordinals KiCad writes for the target stackup and version.

## Setup and stackup

```lisp
(setup
  (stackup
    (layer "F.SilkS" (type "Top Silk Screen") (color "White"))
    (layer "F.Paste" (type "Top Solder Paste"))
    (layer "F.Mask"  (type "Top Solder Mask") (color "Green") (thickness 0.01))
    (layer "F.Cu"    (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 1.51)
      (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
    (layer "B.Cu"    (type "copper") (thickness 0.035))
    (layer "B.Mask"  (type "Bottom Solder Mask") (color "Green") (thickness 0.01))
    (layer "B.Paste" (type "Bottom Solder Paste"))
    (layer "B.SilkS" (type "Bottom Silk Screen") (color "White"))
    (copper_finish "None")
    (dielectric_constraints no)
  )
  (pad_to_mask_clearance 0)
  (allow_soldermask_bridges_in_footprints no)
  (tenting front back)          ; v9
  (pcbplotparams ...)
)
```

- `pcbplotparams` is last-plot state. A compact, valid v9 block is in `assets/example-led.kicad_pcb`; KiCad fills defaults if it is omitted.
- `stackup` is optional but required if you make impedance or thickness claims. The sum of copper + dielectric must match `(general (thickness ...))`.

## Nets

```lisp
(net 0 "")
(net 1 "+5V")
(net 2 "GND")
(net 3 "LED_A")
```

- `ORDINAL` is an integer; the name is a quoted string. Ordinals must be unique.
- Net 0 is the empty net with an empty name.
- Conductive items reference only the ordinal: `(net 2)`. Pads also carry the name: `(net 2 "GND")`; it must match the net table.
- **Net classes are not in the board file.** They live in the project (`.kicad_pro`) and custom rules in `.kicad_dru`.

## Footprints

KiCad 9 footprint instance (abridged from a real file):

```lisp
(footprint "Resistor_SMD:R_0805_2012Metric"
  (layer "F.Cu")
  (uuid "0f0e...")
  (at 15 10 0)                         ; board placement, mm + degrees
  (descr "Resistor SMD 0805")
  (tags "resistor smd")
  (property "Reference" "R1"
    (at 0 -1.55 0) (layer "F.SilkS") (uuid "...")
    (effects (font (size 1 1) (thickness 0.15))))
  (property "Value" "330R"
    (at 0 1.55 0) (layer "F.Fab") (uuid "...")
    (effects (font (size 1 1) (thickness 0.15))))
  (property "Datasheet" "" (at 0 0 0) (unlocked yes) (layer "F.Fab") (hide yes)
    (uuid "...") (effects (font (size 1.27 1.27) (thickness 0.15))))
  (attr smd)                           ; or through_hole; board_only for mechanical
  (fp_line (start -0.75 -0.75) (end 0.75 -0.75)
    (stroke (width 0.12) (type solid)) (layer "F.SilkS") (uuid "..."))
  (fp_line ...)                        ; F.CrtYd courtyard, F.Fab body
  (pad "1" smd roundrect (at -0.95 0) (size 1.0 1.45)
    (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25)
    (net 1 "+5V") (uuid "..."))
  (embedded_fonts no)
  (model "${KICAD9_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0805_2012Metric.wrl"
    (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))
)
```

Rules:

- The footprint definition is **fully embedded** on the board; the library link is metadata, not a runtime lookup.
- `attr` is required for correct DRC/assembly classification: `smd`, `through_hole`, or `board_only` for mechanical parts (mounting holes).
- Provide a courtyard (`F.CrtYd`/`B.CrtYd`). Missing courtyards cause DRC warnings and assembly collisions.
- Provide `Reference` and `Value` fields. In KiCad 8/9 these are `property` tokens; `fp_text user` remains valid for free text.
- `path` links a footprint to a schematic symbol. Board-only designs can omit it, but "Update PCB from Schematic" may then treat the footprint as extra.
- `(embedded_fonts no)` is written inside each v9 footprint.

## Pads

```lisp
(pad "1" thru_hole rect (at 0 0) (size 1.7 1.7) (drill 1)
  (layers "*.Cu" "*.Mask") (remove_unused_layers no)
  (net 1 "+5V") (pinfunction "P1") (pintype "passive") (uuid "..."))

(pad "2" smd roundrect (at 0.95 0) (size 1.0 1.45)
  (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25)
  (net 3 "LED_A") (pintype "passive") (uuid "..."))

(pad "" np_thru_hole circle (at 0 0) (size 2.6 2.6) (drill 2.6)
  (layers "*.Cu" "*.Mask") (uuid "..."))
```

- Types: `thru_hole`, `smd`, `connect`, `np_thru_hole`. Shapes: `circle`, `rect`, `oval`, `trapezoid`, `roundrect`, `custom`.
- `thru_hole`/`np_thru_hole` require `drill`; SMD pads must not have one.
- THT pads usually use `"*.Cu" "*.Mask"`; SMD pads use `"F.Cu" "F.Paste" "F.Mask"` (or `B.*`).
- Mechanical pads numbered `"0"` or `""` legitimately have no net. Electrical pads without a net are usually a mistake.
- KiCad 9 writes `remove_unused_layers` (plural) even though the docs show the singular form.

## Board graphics

v7+ uses `stroke`, not `width`:

```lisp
(gr_line (start 0 0) (end 30 0)
  (stroke (width 0.1) (type solid)) (layer "Edge.Cuts") (uuid "..."))

(gr_rect (start 0 0) (end 30 20)
  (stroke (width 0.1) (type solid)) (fill no) (layer "Edge.Cuts") (uuid "..."))

(gr_text "LED BLINKY" (at 16 18.5) (layer "F.SilkS") (uuid "...")
  (effects (font (size 1 1) (thickness 0.15))))
```

- Outline: `Edge.Cuts` must form a closed contour. A single `gr_rect`/`gr_circle`/`gr_poly` is closed; a line/arc chain must have every endpoint shared by exactly two segments.
- Silkscreen line width ~0.12–0.15 mm, text height ≥0.8–1.0 mm; avoid silk over pads.
- `stroke` types: `default`, `solid`, `dash`, `dot`, `dash_dot`, `dash_dot_dot` (v7+).

## Tracks

```lisp
(segment (start 6 10) (end 14.05 10) (width 0.4) (layer "F.Cu") (net 1) (uuid "..."))

(via (at 6 14) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 2) (uuid "..."))

(arc (start 0 0) (mid 1 1) (end 2 0) (width 0.25) (layer "F.Cu") (net 1) (uuid "..."))
```

- `uuid` (KiCad 8/9; KiCad 6/7 used `tstamp`).
- `via` layers must be declared copper layers; `drill < size`; keep annular ring sane.
- Connectivity is geometric: endpoints must coincide with a pad, another track (including T-junctions), or a via of the same net.
- Zero-length segments and duplicate/overlapping collinear segments should be removed.

## Zones

```lisp
(zone
  (net 2) (net_name "GND") (layer "B.Cu") (uuid "...")
  (hatch edge 0.508)
  (connect_pads (clearance 0.2))
  (min_thickness 0.25)
  (filled_areas_thickness no)
  (fill yes (thermal_gap 0.3) (thermal_bridge_width 0.3))
  (polygon (pts (xy 1 1) (xy 29 1) (xy 29 19) (xy 1 19)))
)
```

- `fill yes` declares intent. KiCad adds `filled_polygon` blocks only after a refill. A freshly generated zone is structurally valid but **unfilled** until the user runs Edit → Fill All Zones.
- `connect_pads` mode: `yes`, `no` or `thru_hole_only`; with no mode, the default is thermal relief.
- Keepout zone:

```lisp
(zone
  (net 0) (net_name "") (layer "F.Cu") (uuid "...")
  (hatch edge 0.5)
  (connect_pads (clearance 0))
  (min_thickness 0.25)
  (keepout
    (tracks not_allowed) (vias not_allowed) (pads allowed)
    (copperpour not_allowed) (footprints allowed))
  (fill (thermal_gap 0.5) (thermal_bridge_width 0.5))
  (polygon (pts (xy 5 5) (xy 10 5) (xy 10 10) (xy 5 10)))
)
```

## Groups

```lisp
(group "Power section" (id "uuid") (members "uuid1" "uuid2"))
```

## Generation checklist

- [ ] `version` and `generator_version` agree with the tokens used.
- [ ] `general`, `paper`, `layers`, `setup`, net table and `Edge.Cuts` present.
- [ ] Layer ordinals match the table for the file's KiCad version (v9 vs v6/7/8).
- [ ] Every net ordinal referenced by a pad/segment/via/zone exists.
- [ ] Every pad net name matches the net table.
- [ ] Footprints have `attr`, pads, courtyard, Reference/Value, and an object ID (`uuid` in 8/9, `tstamp` in 6/7).
- [ ] `Edge.Cuts` is closed; copper is inside it.
- [ ] Tracks use the ID form for the version: `(uuid ...)` in 8/9, `(tstamp ...)` in 6/7; graphics use `(stroke ...)` in 7+.
- [ ] Every track endpoint touches its net (pad/via/track).
- [ ] Zones that claim `(fill yes)` are refilled before plotting.
- [ ] `scripts/check_pcb.py --strict` is clean.
- [ ] `kicad-cli pcb drc` is clean (or deviations are explained).
