# KiCad S-Expression Primitives for PCB Design — distilled reference

Source: https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html (primary). Board-specific usage: https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/index.html.

Use this as a quick reference when generating `.kicad_pcb` content. Verify against the docs for uncertain attributes.

## Syntax rules

- S-expressions delimited by `(` and `)`.
- Tokens are lowercase.
- Tokens cannot contain whitespace or special characters other than `_`.
- Strings are double-quoted and UTF-8.
- Attributes may be required, optional (`[ ... ]` in docs), or chosen from alternatives (`a | b`).
- Human readability is a goal.

## Coordinates, sizes, angles

- **All values are in millimeters.**
- Avoid exponential floating-point notation for readability.
- Coordinates are relative to the origin of their containing object.
- Board/footprint files: minimum internal unit is 1 nm (max 6 decimal places, `0.000001` mm); extra precision may be truncated.
- Position: `(at X Y [ANGLE])`. Some objects omit angle.
- **Angle note from docs:** Symbol `text` angles are stored in tenths of a degree; all other angles are stored in degrees. Follow the object-specific rule.

## Points

```lisp
(pts (xy X Y) (xy X Y) ...)
```

## Stroke (outlines)

```lisp
(stroke (width W) (type TYPE) (color R G B A))
```

`TYPE` values include `dash`, `dash_dot`, `dash_dot_dot` (v7+), `dot`, `default`, `solid`.

## Text effects

```lisp
(effects
  (font [(face FACE_NAME)] (size H W) [(thickness T)] [bold] [italic] [(line_spacing R)])
  [(justify [left|right] [top|bottom] [mirror])]
  [hide]
)
```

`mirror` is supported in PCB/footprints context per docs.

## Canonical layers (board/footprint)

Drawable objects use `(layer ...)`. User renames are display/output only; internal names are canonical. Wildcards like `*.Cu` can represent matching layers where allowed.

Common canonical names:
- Copper: `F.Cu`, `In1.Cu`..`In30.Cu`, `B.Cu`
- Tech: `F.Adhes`, `B.Adhes`, `F.Paste`, `B.Paste`, `F.SilkS`, `B.SilkS`, `F.Mask`, `B.Mask`, `F.CrtYd`, `B.CrtYd`, `F.Fab`, `B.Fab`
- User/mechanical: `Dwgs.User`, `Cmts.User`, `Eco1.User`, `Eco2.User`, `Edge.Cuts`, `Margin` (per docs context), `User.1`..`User.9`

**Board outline:** use `Edge.Cuts` with a closed contour.

## Unique identifiers

```lisp
(uuid UUID)   ; RFC4122-like UUID; KiCad uses version 4 (random) in general; legacy conversions may differ
```

Board tracks use `(tstamp UUID)` in the board format docs; footprints/pads/graphics may use `(uuid UUID)` as specified by each token. Use unique values consistently.

## Footprints (board context)

A footprint placed on a board is a `(footprint ...)` definition (see intro docs). Key elements:
- Identification/library context and placement: `(layer ...)`, `(at X Y [rot])`, `(uuid ...)` for board instances.
- `(fp_text reference ...)`, `(fp_text value ...)` on appropriate layers (often silkscreen/fab as needed).
- Footprint graphics (`fp_line`, `fp_rect`, `fp_circle`, `fp_arc`, `fp_poly`, `fp_curve`, etc.).
- `(pad ...)` definitions with number, type, shape, position, size, drill (for THT), layers, and optional `(net N "name")`.
- Optional courtyard/fab/silkscreen/mask geometry.

## Pads (essentials)

```lisp
(pad "NUMBER" TYPE SHAPE (at X Y [rot]) (size W H)
  [(drill [oval] D [W] [(offset X Y)])]
  (layers LAYER_LIST)
  [(roundrect_rratio R)]
  (net N "NAME")
  (uuid UUID)
  [...options...])
```

- `TYPE`: `thru_hole`, `smd`, `connect`, `np_thru_hole`.
- `SHAPE`: `circle`, `rect`, `oval`, `trapezoid`, `roundrect`, `custom`.
- `layers`: e.g., `*.Cu *.Mask F.SilkS` for THT, or `F.Cu F.Paste F.Mask` for SMD (choose appropriately).
- Include drill for through-hole/NPTH as required.

## Board graphics

```lisp
(gr_line (start X Y) (end X Y) (layer Edge.Cuts) (width 0.15) (uuid ...))
(gr_arc (start X Y) (mid X Y) (end X Y) (layer Edge.Cuts) (width 0.15) (uuid ...))
(gr_rect (start X Y) (end X Y) (layer F.SilkS) (width 0.12) (uuid ...))
(gr_circle (center X Y) (end X Y) (layer F.SilkS) (width 0.12) (uuid ...))
(gr_poly (pts (xy ...) ...) (layer F.SilkS) (width 0.12) [(fill yes)] (uuid ...))
(gr_text "TEXT" (at X Y [rot]) (layer F.SilkS) (uuid ...) (effects ...))
```

Use `Edge.Cuts` for the board boundary. Keep widths sensible for fabrication/visibility.

## Zones (essentials)

```lisp
(zone
  (net N) (net_name "NAME")
  (layer F.Cu|B.Cu|...)
  (uuid UUID)
  [(name "ZONE_NAME")]
  (hatch none|edge|full PITCH)
  [(priority P)]
  (connect_pads [thru_hole_only|full|no] (clearance C))
  (min_thickness T)
  [(filled_areas_thickness no)]
  (fill yes [(mode hatched)] (thermal_gap G) (thermal_bridge_width W) ...)
  (polygon (pts (xy X Y) ...))
)
```

- Keepout zones add `(keepout (tracks ...)(vias ...)(pads ...)(copperpour ...)(footprints ...))`.
- Omit `net_name` semantics per keepout rules in docs (keepouts are not tied to a net in the usual way).

## Groups

```lisp
(group "NAME" (id UUID) (members UUID1 ... UUIDN))
```

## Practical generation tips

- Generate complete, balanced s-expressions; pretty-print indentation for readability.
- Use consistent UUIDs (unique per object). If you don't have a UUID generator, produce well-formed unique UUID v4-style strings and keep them stable within the file.
- For simple boards, prefer explicit, minimal tokens that KiCad accepts over copying every optional parameter from old examples.
- When target KiCad version is unknown, prefer modern, documented structures and state the assumption. Fetch docs for version-sensitive details.
