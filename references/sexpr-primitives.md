# KiCad S-Expression Primitives (PCB) — KiCad 8/9 reference

Source: https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html (common syntax), https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/index.html (board context). Verified against KiCad 9.0 output, 2026-11.

Use `references/pcb-file-format.md` for section-level layout and `assets/*.kicad_pcb` for complete, validator-clean examples.

## Syntax rules

- S-expressions use `(` and `)`. Tokens are lowercase.
- **v7+ output quotes strings**, including layer names and net names: `(layer "F.Cu")`, `(net 1 "+5V")`. Older files may use bare tokens; do not emulate that in new files. (`generator` is commonly still a bare token in v7 output.)
- Tokens cannot contain whitespace or special characters other than `_`; canonical layer names contain `.` and are therefore written as quoted strings.
- Tokens may be required, optional (`[ ... ]`), or one of alternatives (`a | b`).
- Comments are not part of the format; do not rely on `;`.
- Human-readable indentation is expected but not semantic.

## Coordinates, sizes, angles

- All values are millimeters.
- PCB/footprint internal resolution is 1 nm: at most 6 decimal places (`0.000001` mm). Extra precision is truncated.
- Avoid exponential notation.
- Coordinates are relative to the containing object: board graphics are absolute; footprint graphics/pads are relative to the footprint origin.
- Position: `(at X Y [ANGLE])`, angle in **degrees** for PCB/footprint objects. (Symbol `text` angles are tenths of a degree — a schematic-only quirk.)
- Board origin is top-left; +X right, +Y down. Negative coordinates are legal but unusual.

## Points

```lisp
(pts (xy X Y) (xy X Y) ...)
```

Minimum 3 points for a zone/polygon outline; 2+ for a polyline.

## Stroke

```lisp
(stroke (width W) (type TYPE) (color R G B A))
```

- v7+ graphics use `stroke`; pre-v7 used `(width W)`.
- `TYPE`: `default`, `solid`, `dash`, `dot`, `dash_dot`, `dash_dot_dot` (v7+).
- `color` is optional RGBA (0–255); usually omitted.

## Text effects

```lisp
(effects
  (font [(face "FAMILY")] (size HEIGHT WIDTH) [(thickness T)] [bold] [italic])
  [(justify [left|right] [top|bottom] [mirror])]
  [(hide yes)]
)
```

- v8+ writes `(hide yes)`; older files may use bare `hide`.
- `mirror` is only valid in PCB/footprint context.
- Reference/value fonts are commonly 1.0 mm high, 0.15 mm thick; silkscreen text should stay ≥0.8–1.0 mm high.

## Canonical layers

Drawable objects use `(layer "NAME")`. Internal names are canonical; user renames are display-only.

- Copper: `F.Cu`, `In1.Cu`…`In30.Cu`, `B.Cu`
- Technical: `F.Adhes`, `B.Adhes`, `F.Paste`, `B.Paste`, `F.SilkS`, `B.SilkS`, `F.Mask`, `B.Mask`, `F.CrtYd`, `B.CrtYd`, `F.Fab`, `B.Fab`
- User: `Dwgs.User`, `Cmts.User`, `Eco1.User`, `Eco2.User`, `Edge.Cuts`, `Margin`, `User.1`…`User.9`
- Wildcards such as `*.Cu` and `*.Mask` are allowed in pad `layers` lists.

Use `Edge.Cuts` for the board boundary; `F.CrtYd`/`B.CrtYd` for courtyards; `F.Fab` for assembly body outlines.

## Unique identifiers

```lisp
(uuid "8f14e45f-ea2d-4b7e-9c1a-3b5d6e7f8091")
```

- Version 4 (random) UUID strings, unique within the file.
- v8/9 use `(uuid ...)` for footprints, pads, graphics, tracks, vias, zones and groups.
- v6 and v7 used `(tstamp ...)` for tracks, vias, zones and graphics. Do not mix.
- Groups use `(id UUID)` plus `(members UUID...)`.

## Footprints (board context)

```lisp
(footprint "LIB:ENTRY"
  (layer "F.Cu")
  (uuid "...")
  (at X Y [ROT])
  (descr "...")
  (tags "...")
  (property "Reference" "R1" (at X Y [ROT]) (layer "F.SilkS") (uuid "...") (effects ...))
  (property "Value" "10k"   (at X Y [ROT]) (layer "F.Fab")   (uuid "...") (effects ...))
  (property "Datasheet" ""  (at 0 0 0) (unlocked yes) (layer "F.Fab") (hide yes) (uuid "...") (effects ...))
  (attr smd|through_hole [board_only] [exclude_from_pos_files] [exclude_from_bom])
  (fp_line ...) (fp_rect ...) (fp_circle ...) (fp_arc ...) (fp_poly ...) (fp_curve ...)
  (fp_text user "..." (at X Y [ROT]) (layer "F.Fab") (uuid "...") (effects ...))
  (pad ...) ...
  (zone ...)                         ; footprint-local keepouts
  (embedded_fonts no)                ; v9
  (model "..." (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))
)
```

- `property` fields replace `(fp_text reference ...)`/`(fp_text value ...)` in v8/v9. `fp_text user` is still used for free text.
- Footprint graphics (`fp_*`) are not valid outside a footprint.
- `path` links to a schematic sheet/symbol. `board_only` marks footprints with no schematic counterpart.
- `attr through_hole` for THT, `attr smd` for SMD, `attr board_only` for mechanical parts.

## Pads

```lisp
(pad "NUMBER" TYPE SHAPE (at X Y [ROT]) (size W H)
  [(drill [oval] D [W] [(offset X Y)])]
  (layers "LAYER" ...)
  [(roundrect_rratio R)]
  [(chamfer_ratio R)] [(chamfer CORNER ...)]
  [(property pad_prop_*)]
  [(remove_unused_layers no)] [(keep_end_layers)]
  (net N "NAME")
  [(pinfunction "NAME")] [(pintype "TYPE")]
  (uuid "...")
  [(solder_mask_margin M)] [(solder_paste_margin M)] [(solder_paste_margin_ratio R)]
  [(clearance C)] [(zone_connect 0|1|2|3)] [(thermal_width W)] [(thermal_gap G)]
  [(options (clearance outline|convexhull) (anchor rect|circle))]
  [(primitives ...)]
)
```

- `NUMBER` is a quoted string; mechanical pads may be `"0"` or `""`.
- `TYPE`: `thru_hole`, `smd`, `connect`, `np_thru_hole`.
- `SHAPE`: `circle`, `rect`, `oval`, `trapezoid`, `roundrect`, `custom`.
- THT/NPTH need `drill`; SMD must not have one.
- THT layers: `"*.Cu" "*.Mask"`; SMD: `"F.Cu" "F.Paste" "F.Mask"` (or `B.*`).
- `roundrect_rratio` 0–1 (0.25 is a common SMD value).
- Custom pads use `options` + `primitives` containing `gr_line`/`gr_poly`/etc.; those inner graphics use `(width ...)` and `(fill yes)` — this is the one place where `width` is still correct in v9.
- Pad `net` name must match the board net table.

## Pad drill

```lisp
(drill 0.8)                       ; round
(drill oval 0.8 1.6)              ; slotted
(drill 0.8 (offset 0.2 0))        ; offset from pad center
```

## Board graphics (`gr_*`)

```lisp
(gr_line (start 0 0) (end 30 0) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts") (uuid "..."))
(gr_rect (start 0 0) (end 30 20) (stroke (width 0.1) (type solid)) (fill no) (layer "F.SilkS") (uuid "..."))
(gr_circle (center 15 10) (end 16 10) (stroke (width 0.12) (type solid)) (layer "F.SilkS") (uuid "..."))
(gr_arc (start 0 0) (mid 5 5) (end 10 0) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts") (uuid "..."))
(gr_poly (pts (xy 0 0) (xy 5 0) (xy 5 5)) (stroke (width 0.1) (type solid)) (fill yes) (layer "F.SilkS") (uuid "..."))
(gr_text "TEXT" (at X Y [ROT]) (layer "F.SilkS") (uuid "...") (effects ...))
(gr_text_box "TEXT" (start X Y) (end X Y) (layer "F.SilkS") (uuid "...") (effects ...) [(stroke ...)])
```

- `Edge.Cuts` outline must be closed. A `gr_rect`, `gr_circle` or closed `gr_poly` counts as closed on its own.
- Use sensible widths: outline 0.05–0.15 mm, silk 0.12–0.15 mm, courtyard 0.05 mm.

## Zones

```lisp
(zone
  (net N) (net_name "NAME")
  (layer "F.Cu")
  (uuid "...")
  [(name "ZONE")]
  (hatch none|edge|full PITCH)
  [(priority P)]
  (connect_pads [yes|no|thru_hole_only] (clearance C))
  (min_thickness T)
  [(filled_areas_thickness no)]
  (fill yes [(mode hatched)] (thermal_gap G) (thermal_bridge_width W)
        [(smoothing chamfer|fillet)] [(radius R)]
        [(island_removal_mode 0|1|2)] [(island_area_min A)])
  (polygon (pts (xy X Y) ...))
  [(filled_polygon (layer "F.Cu") (pts ...)) ...]
)
```

- `hatch` is display-only.
- `connect_pads`: `yes` (solid), `no` (disconnected) or `thru_hole_only` (THT pads solid, SMD thermal); with no mode keyword, thermal relief is the default.
- `fill yes` is intent; `filled_polygon` only exists after a refill.
- Keepouts add a `keepout` block:

```lisp
(keepout
  (tracks allowed|not_allowed) (vias allowed|not_allowed) (pads allowed|not_allowed)
  (copperpour allowed|not_allowed) (footprints allowed|not_allowed))
```

Keepouts use `(net 0)` and `(net_name "")`.

## Groups

```lisp
(group "NAME" (id "UUID") (members "UUID1" "UUID2" ...))
```

## Version-sensitive token summary

| Feature | KiCad 6 | KiCad 7 | KiCad 8/9 |
|---------|---------|---------|-----------|
| Track/via/zone ID | `(tstamp ...)` | `(tstamp ...)` | `(uuid ...)` |
| Graphic outline | `(width W)` | `(stroke (width W) (type T))` | `(stroke ...)` |
| Hide text | bare `hide` | bare `hide` | `(hide yes)` |
| Footprint fields | `(fp_text reference ...)` | `(fp_text reference ...)` | `(property "Reference" ...)` |
| String quoting | minimal | quoted | quoted |
| Layer ordinals | historical IDs | historical IDs | new scheme (9.0) |
| Font face | — | `(face "...")` | `(face "...")` |
| Text boxes | — | `gr_text_box` / `fp_text_box` | yes |
| Board-level fonts | — | — | `(embedded_fonts no)` (9) |
| Soldermask bridges | — | — | `(allow_soldermask_bridges_in_footprints no)` (8+) |
| Tenting | — | — | `(tenting front back)` (9) |

## Practical generation tips

- Copy `assets/example-led.kicad_pcb` and replace contents rather than writing a header from memory.
- Keep every numeric field in mm with ≤6 decimals and no exponent.
- Generate unique UUIDv4 strings; never reuse one.
- Prefer explicit, minimal tokens that KiCad writes over every optional parameter from old examples.
- After any edit, run `python3 scripts/check_pcb.py --strict <board>`.
