---
name: pcb-design-skill
description: Design and generate manufacturable KiCad printed circuit board (PCB) files (.kicad_pcb). Use when creating or editing PCB layouts, placing footprints, routing tracks and vias, defining board outlines, nets, copper zones or planes, or producing DRC-ready KiCad 8/9 s-expression output and Gerbers. Covers netlist modeling, stackup, DFM/DFA rules, and structural validation.
license: MIT
---

# PCB Design (KiCad 8/9)

Create manufacturable, syntactically valid KiCad PCB files and output them as `.kicad_pcb` s-expression files. **KiCad is the primary framework.** Do not rely on memory for the file format — use the KiCad developer documentation and the shipped, validator-clean assets as the source of truth.

**Target dialect: KiCad 9 (`(version 20241229)`).** KiCad 8 (`20240108`) is supported with the differences listed in `references/pcb-file-format.md#version-dialect-matrix` — notably layer ordinals, which changed again in 9. Never mix dialects: v6/v7-era `(tstamp ...)` or v6 `(width ...)` graphics inside a v8/v9 header are the most common cause of files that fail to load.

## Non-negotiables

1. **One dialect per file.** Header `version`, `generator_version`, track UUIDs, stroke syntax and footprint fields must all match the declared version.
2. **A board is a complete file, not a snippet.** It must open in KiCad and pass the structural checker below.
3. **Connectivity is geometric.** A track with the right net number that does not touch a pad or another track is still unrouted.
4. **No silent electrical guesses.** Pin-to-net mapping, polarity, footprint choice and safety spacing must be stated as assumptions or asked about.
5. **Do not claim fabrication compliance without verified fab limits.** Manufacturing numbers are stackup- and vendor-specific.

## Retrieval Sources (use before guessing)

| Resource | URL | Use for |
|----------|-----|---------|
| S-Expression intro (common syntax) | https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html | Syntax, quoting, coordinates, layers, footprints, pads, graphics, zones, groups |
| Board file format | https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/index.html | `.kicad_pcb` top-level sections, header, layers, setup, nets, tracks, zones |
| Footprint library format | https://dev-docs.kicad.org/en/file-formats/sexpr-footprint/index.html | `.kicad_mod` context and footprint fields |
| KiCad file formats index | https://dev-docs.kicad.org/en/file-formats/index.html | Schematic/symbol formats when a matching schematic is needed |
| KiCad DRC documentation | https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html#design-rule-checking | What the real DRC checks and which rules are configurable |
| KiCad custom design rules | https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html#custom-design-rules | `.kicad_dru` syntax and rule expressions |
| JLCPCB capabilities | https://jlcpcb.com/capabilities/pcb-capabilities | Current fab limits (verify before use) |
| IPC-2221 / IPC-2152 | https://www.ipc.org/ | Trace width/current, clearance and creepage fundamentals |

The developer docs lag the application in places (they still describe pre-v7 `(width ...)` graphics and `(tstamp ...)` tracks). When the docs and a real file from the target KiCad version disagree, **follow the real file** and note the discrepancy.

## Local References and Assets

| Path | Contents |
|------|----------|
| `references/pcb-file-format.md` | Version matrix, sections, layer ordinals, setup/stackup, footprints, pads, tracks, zones, checklist |
| `references/sexpr-primitives.md` | Token syntax, quoting, strokes, effects, UUIDs, pad/graphic/zone primitives, version-sensitive tokens |
| `references/practical-rules.md` | Stackup, placement, routing, current/width, SI/PI/EMI, impedance, thermal, mechanical, DFM/DFA, defaults |
| `assets/minimal-2layer.kicad_pcb` | Validator-clean blank 2-layer board (copy this as a starting point) |
| `assets/example-led.kicad_pcb` | Validator-clean routed example: THT header, SMD resistor/LED, vias, courtyard, mounting hole |
| `assets/example.kicad_dru` | Custom-rule examples (netclass width, high-voltage clearance) |
| `scripts/check_pcb.py` | Dependency-free structural/geometry pre-flight checker |

Use the search terms in these references before fetching a whole doc page: `version`, `generator_version`, `layers`, `stackup`, `setup`, `net`, `footprint`, `property`, `attr`, `pad`, `stroke`, `segment`, `via`, `zone`, `keepout`, `filled_polygon`, `Edge.Cuts`, `embedded_fonts`, `remove_unused_layers`, `courtyard`, `impedance`, `differential`, `3W`, `return path`, `DFM`, `trace width`.

## Workflow

1. **Clarify design intent (do not guess silently on critical constraints).**
   - Board function, components, connectors, power/ground nets, target size and mechanical constraints.
   - Fabrication assumptions: layer count (default 2), units (mm), copper weight, stackup, surface finish, assembly process.
   - Target KiCad release. Default to KiCad 9 unless the user says otherwise.
   - If requirements are underspecified, state assumptions explicitly in the response and encode reasonable defaults in the file.

2. **Model the electrical netlist first.**
   - Define `(net 0 "")` plus every named net before items reference them. Keep ordinals stable and unique.
   - Ensure every pad, track, via and zone `net` ordinal exists; ensure pad `(net N "NAME")` names match the net table exactly.
   - If a matching schematic exists, footprints need `path`/reference links; a board-only design should be marked `board_only` where appropriate.

3. **Define mechanics, layers and stackup.**
   - Start from `assets/minimal-2layer.kicad_pcb` and keep its header/layers/setup valid.
   - Set the `layers` section with canonical names and the correct ordinals (see the table in `references/pcb-file-format.md`; `B.CrtYd`/`F.CrtYd` are not sequential).
   - Set `(general (thickness 1.6) (legacy_teardrops no))`, `(paper "A4")`, and a realistic `stackup` if stackup matters.
   - Provide a closed `Edge.Cuts` boundary (lines/arcs or one rect/circle/polygon). Keep copper away from the edge.

4. **Place footprints.**
   - Use complete embedded footprint definitions: `layer`, `uuid`, `at`, `property` fields (`Reference`/`Value`), `attr smd|through_hole`, pads, silkscreen, `F.CrtYd`/`B.CrtYd` courtyard, and optionally `F.Fab` and a 3D model.
   - Place by function: connectors, power entry and MCU first; group related circuits; separate analog/digital/noisy domains.
   - Put decoupling capacitors at each IC power/ground pin with minimal loop area; values/packages per datasheet or stated assumptions.
   - Respect courtyard and assembly spacing; keep references readable and polarized parts marked.

5. **Route connectivity.**
   - `segment` for tracks, `via` for layer changes, `arc` for curved tracks. KiCad 8/9 tracks use `(uuid ...)`, not `(tstamp ...)` (v6/7 still used `tstamp`).
   - Set `width` per item from current/stackup intent; power wider than signals. Net classes live in the project/design-rules file, **not** in `.kicad_pcb`.
   - Keep tracks short/direct, prefer 45° bends, minimize vias on high-speed paths.
   - Preserve return paths: do not route high-speed/sensitive traces across plane splits. Apply ~3W spacing for crosstalk-prone parallel runs.
   - Impedance/differential pairs are stackup + netclass + geometry decisions; only encode geometry that matches a stated stackup. There is no special diff-pair token in the board file.
   - Verify every segment endpoint actually touches a pad, via or track of the same net.

6. **Add planes/zones when useful.**
   - Use `zone` for copper pours and keepouts: `net`, `net_name`, `layer`, `uuid`, `hatch`, `connect_pads`, `min_thickness`, `fill`, and a closed `polygon`.
   - Keepout zones use `net 0`, an empty `net_name`, and a `keepout` block with `allowed`/`not_allowed` per object type.
   - `(fill yes ...)` declares intent only; KiCad must **refill zones** before Gerber output. A file without `filled_polygon` is valid but visually/electrically unfilled until refill.

7. **Validate before delivering (mandatory).**
   - Run `python3 scripts/check_pcb.py --strict <board>.kicad_pcb`. It checks syntax, header, nets, layers, UUIDs, pads, tracks, outline closure, zones and dangling endpoints.
   - If KiCad is installed, run the authoritative DRC and exports:
     ```bash
     kicad-cli pcb drc --severity-all --exit-code-violations board.kicad_pcb
     kicad-cli pcb export gerbers -o gerbers/ board.kicad_pcb
     kicad-cli pcb export drill -o gerbers/ board.kicad_pcb
     kicad-cli pcb export pos -o gerbers/ board.kicad_pcb
     ```
     Zone refill is a GUI action: open the board and run **Edit → Fill All Zones** before plotting.
   - If you push the skill repository to GitHub, `.github/workflows/validate.yml` runs `check_pcb.py --strict` and KiCad 9 DRC in CI. CI gates on **error-level** DRC violations so cosmetic silkscreen warnings do not block, and uploads the full `--severity-all` reports as the `kicad-drc-reports` artifact. DRC uses KiCad's built-in constraints unless a `<board>.kicad_pro` is present; ship the project files when the design depends on custom netclasses or rule minimums.
   - Fix every error and justify or fix every warning. Do not deliver a board that fails `--strict` without saying why.

8. **Output.**
   - Write the complete `.kicad_pcb` file (and `.kicad_dru`/`.kicad_pro` only if rules/netclasses are required).
   - Summarize assumptions: layer count, stackup, copper weight, current/trace sizing, impedance/diff-pair constraints, fab limits, and what was not verified.
   - List the checks run and the checks the user still needs (KiCad DRC, zone refill, 3D/clearance, impedance/stackup, fab DFM/DFA).

## Minimal skeleton (KiCad 9)

The full working version is `assets/minimal-2layer.kicad_pcb`. The required shape is:

```lisp
(kicad_pcb
  (version 20241229)
  (generator "your-tool")          ; not "pcbnew" for third-party output
  (generator_version "1.0")
  (general (thickness 1.6) (legacy_teardrops no))
  (paper "A4")
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user) ...)
  (setup (pad_to_mask_clearance 0) (pcbplotparams ...))
  (net 0 "") (net 1 "+5V") (net 2 "GND")
  (footprint "Package:Name" (layer "F.Cu") (uuid "...") (at 10 10) (attr smd)
    (property "Reference" "R1" (at 0 -1.5) (layer "F.SilkS") (uuid "...") (effects ...))
    (pad "1" smd roundrect (at -0.95 0) (size 1 1.45) (layers "F.Cu" "F.Paste" "F.Mask")
      (roundrect_rratio 0.25) (net 1 "+5V") (uuid "..."))
    (embedded_fonts no))
  (segment (start 10 10) (end 20 10) (width 0.3) (layer "F.Cu") (net 1) (uuid "..."))
  (gr_line (start 0 0) (end 30 0) (stroke (width 0.1) (type solid))
    (layer "Edge.Cuts") (uuid "..."))
  (embedded_fonts no)
)
```

## Critical Rules

1. **Docs plus real files over priors.** Verify version-sensitive syntax against the docs and a file written by the target KiCad release.
2. **Canonical names and ordinals.** Use `F.Cu`, `B.Cu`, `F.SilkS`, `F.CrtYd`, `Edge.Cuts`, etc., with the ordinals for the file's KiCad version. Quote strings, layer names and net names in v7+ output.
3. **Net integrity.** Net 0 is the empty net; all conductive items reference declared ordinals; pad net names match the net table.
4. **Complete footprints.** No board-level `footprint` without `attr`, pads, courtyard and a Reference field.
5. **Closed outline.** Provide a manufacturable `Edge.Cuts` boundary and keep copper inside it.
6. **Refill zones.** `(fill yes)` without `filled_polygon` is not a finished pour.
7. **State the limits.** Never claim DRC/fab compliance you did not verify.

## Anti-Patterns

- Writing v6/v7 syntax into a v8/v9 header: `(tstamp ...)`, `(width ...)` on graphics, unquoted `"F.Cu"`, bare `hide`.
- Emitting `(net_class ...)` in the board file (removed from the board format; use `.kicad_pro`/`.kicad_dru`).
- Referencing net numbers that are not declared, or pad net names that disagree with the net table.
- Omitting `Edge.Cuts`, leaving it open, or using zero-length/duplicate outline segments.
- Footprints without `attr`, courtyard, pads or Reference/Value fields.
- `(fill yes ...)` zones that were never refilled before plotting.
- Tracks that reference the right net but do not geometrically touch a pad/via/track.
- Guessing complex footprint pad geometry from memory instead of using a library/datasheet.
- Claiming electrical correctness, impedance or manufacturability without assumptions and KiCad DRC/DFM checks.

## Scope: When Not to Hand-Generate

Hand-writing s-expressions is appropriate for small/medium boards, simple footprints and scripted prototypes. For dense BGA escape routing, impedance-critical RF/high-speed, flex/rigid, or large designs, use KiCad's GUI or the Python/IPC API to place real library footprints and let KiCad compute connectivity, fills and DRC. Say so rather than emitting a fragile approximation.

## Validation Commands

```bash
# Structural/geometry pre-flight (no KiCad required)
python3 scripts/check_pcb.py board.kicad_pcb
python3 scripts/check_pcb.py --strict board.kicad_pcb

# Authoritative checks when KiCad 8/9 is installed
kicad-cli pcb drc --severity-all --exit-code-violations board.kicad_pcb
kicad-cli pcb export gerbers -o gerbers/ board.kicad_pcb
kicad-cli pcb export drill -o gerbers/ board.kicad_pcb
```
