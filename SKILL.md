---
name: pcb-design-skill
description: Design, generate, automatically route, validate, and export manufacturable KiCad 9 printed circuit boards from a design spec, netlist, schematic, or existing board. Use when creating or editing PCB layouts, placing real library footprints, routing with Freerouting, defining stackups and nets, running KiCad DRC/DFM, or producing Gerbers and a professional release report. Covers automatic design, controlled-impedance/RF/flex/high-voltage gates, and structural validation.
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
6. **No release claim without a passing report.** A board is release-ready only when `report/design-report.json` says `professional_claim: true`. Otherwise list the failed gates and missing sign-offs.

## Automatic Design Pipeline (preferred for new boards)

Do not hand-author a release board when this pipeline can do it. The pipeline turns a **design spec** into a board with real KiCad library footprints, routes it with **Freerouting**, runs **KiCad DRC + DFM + netlist parity**, exports Gerbers/drill/position/STEP/IPC-D-356, and writes a gate report. Every output listed in `profile.fab_outputs` is a hard gate. Use hand-authoring only to review or patch a generated board.

```bash
# 1. Validate intent (no KiCad needed)
python3 scripts/design_spec.py validate specs/led-blinky.json

# 2. Full automatic design. Auto-selects local when KiCad 9 pcbnew, Java 25
#    and Freerouting 2.5.0 are present, otherwise dispatches GitHub Actions.
python3 scripts/design_pipeline.py all specs/led-blinky.json

# Electrical source of truth is a schematic or netlist; the spec supplies mechanics/rules.
python3 scripts/design_pipeline.py all specs/led-blinky.json --schematic design.kicad_sch
python3 scripts/design_pipeline.py all specs/led-blinky.json --netlist design.net

# Existing board: route/check/export without rebuilding.
python3 scripts/design_pipeline.py all specs/led-blinky.json --board existing.kicad_pcb --skip-build

# Stages only
python3 scripts/design_pipeline.py build specs/led-blinky.json -o build/led-blinky
python3 scripts/design_pipeline.py route build/led-blinky/led-blinky.kicad_pcb --spec specs/led-blinky.json
python3 scripts/design_pipeline.py check build/led-blinky/led-blinky.kicad_pcb --spec specs/led-blinky.json -o build/led-blinky/check
python3 scripts/design_pipeline.py fab   build/led-blinky/led-blinky.kicad_pcb --spec specs/led-blinky.json -o build/led-blinky/fab
```

**Canonical inputs.** `specs/<name>.json` is the source of truth for the automatic path. Use `schemas/design-spec.schema.json` and `specs/led-blinky.json` as the model. For a natural-language request, write the spec first, state assumptions in `assumptions[]`, then run the pipeline. For a KiCad schematic/netlist, the netlist is authoritative for components/pad nets; the spec overlay supplies board size, stackup, rules, placement and routing.

**Board-class profiles.** The profile selects the rule envelope and the release gates:

| Profile | Automation | Required human sign-off |
|---|---|---|
| `2layer-low-speed` | Full: build, place, route, DRC, DFM, fab | none |
| `4layer-impedance` / `6layer-impedance` | Gated: stackup + impedance + length-match checks | `stackup`, `impedance` |
| `rf-mixed-signal` | Assisted; RF nets should be re-routed by hand | `rf`, `emc`, `thermal` |
| `flex-rigid` | Assisted | `mechanical`, `flex_bend`, `assembly` |
| `high-voltage` | Gated; clearance/creepage checks | `safety`, `creepage` |

`professional_claim` is true only when every hard gate passes **and** every required sign-off is listed in `signoff.completed`. RF, EMC, thermal, safety and flex fatigue cannot be certified by the pipeline; say so instead of overclaiming. Record a reviewer in `signoff.reviewer`.

**Backend selection: local or CI (automatic).** Full automation needs `pcbnew` (KiCad 9 Python), `kicad-cli`, Java 25 and `freerouting-2.5.0.jar` (Freerouting 2.5.0 is built for Java 25). `--backend auto` is the default: it probes the current environment and runs locally when every requested stage has its tools, otherwise it dispatches `.github/workflows/design.yml`. That workflow runs in the official `kicad/kicad:9.0` container, installs a JRE 25, downloads Freerouting, runs the whole pipeline, and uploads `design-output` + `design-report`.

| Environment | Probe result | Selected backend |
|---|---|---|
| Real PC with KiCad 9 + Java 25 + jar | all stages ready | local |
| Termux / container without KiCad; `gh` authenticated; clean pushed repo | pcbnew/kicad-cli missing | CI |
| No local tools and no usable CI | missing | local, fails with the missing-tool list |

```bash
python3 scripts/env_probe.py                        # what can this machine run?
python3 scripts/env_probe.py --json                 # machine-readable probe
python3 scripts/design_pipeline.py all specs/led-blinky.json                     # auto
python3 scripts/design_pipeline.py all specs/led-blinky.json --backend local
python3 scripts/design_pipeline.py all specs/led-blinky.json --backend ci --ci-watch --ci-download build/ci
```

CI dispatch is conservative: it refuses a dirty tree or a branch ahead of its upstream, because Actions checks out the pushed revision and would otherwise design the wrong board. Commit and push the spec, profiles, schemas, scripts and workflow first. `gh` must be installed and authenticated (`pkg install gh`, then `gh auth login`). Use `--ci-repo owner/name` only when dispatching a repository other than the current one.

Stage commands (`build`, `route`, `check`, `fab`) run locally because CI cannot see uncommitted stage files; use `all --backend ci` for an end-to-end CI run. Inside GitHub Actions the pipeline always stays local, so a CI run never re-dispatches itself.

**Iteration rule.** When a gate fails, change the spec (placement, width, clearance, stackup, class assignment) and regenerate. Do not edit a generated board and then claim the report applies; the report records the exact resolved spec (`<name>.spec.json`).

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
| `schemas/design-spec.schema.json` | Canonical automatic-design spec (board, rules, nets, placement, stackup, gates) |
| `profiles/*.json` | Board-class rule envelopes and release gates |
| `specs/led-blinky.json` | Reference spec for the automatic pipeline |
| `scripts/design_spec.py` | Dependency-free spec validator, netlist importer, impedance/current calculations, gate report |
| `scripts/pcb_build.py` | Spec → KiCad board using real library footprints (pcbnew) |
| `scripts/pcb_route.py` | DSN export → Freerouting → SES import → zone fill |
| `scripts/pcb_check.py` | Structural, DRC, DFM, netlist-parity and length-match gates |
| `scripts/pcb_fab.py` | Gerbers, drill, position, STEP, IPC-D-356 and fabrication README |
| `scripts/design_pipeline.py` | Orchestrates the whole automatic design and writes the release report; `--backend auto` selects local or CI |
| `scripts/env_probe.py` | Detects pcbnew, kicad-cli, Java 25, Freerouting, gh/git and recommends local vs CI |
| `scripts/ci_dispatch.py` | Dispatches `.github/workflows/design.yml`, optionally watches and downloads artifacts |
| `scripts/check_pcb.py` | Dependency-free structural/geometry pre-flight checker |
| `.github/workflows/design.yml` | CI: build, route, DRC/DFM, fab export and report in the KiCad 9 container |

Use the search terms in these references before fetching a whole doc page: `version`, `generator_version`, `layers`, `stackup`, `setup`, `net`, `footprint`, `property`, `attr`, `pad`, `stroke`, `segment`, `via`, `zone`, `keepout`, `filled_polygon`, `Edge.Cuts`, `embedded_fonts`, `remove_unused_layers`, `courtyard`, `impedance`, `differential`, `3W`, `return path`, `DFM`, `trace width`.

## Manual / Review Workflow

Use the automatic pipeline first. This workflow is for reviewing a generated board, patching a design whose spec cannot express a constraint, or working in an environment where the pipeline tools are unavailable.

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
   - For a generated board, run `python3 scripts/design_pipeline.py check <board> --spec <spec> -o check/`. It runs the structural checker, KiCad DRC, DFM, netlist parity and length matching and writes `check.json`.
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
   - Write the complete `.kicad_pcb` file (and `.kicad_dru`/`.kicad_pro` only if rules/netclasses are required). For an automatic run, the deliverable is the generated board plus `report/design-report.json`, `report/design-report.md` and the fabrication package.
   - Summarize assumptions: layer count, stackup, copper weight, current/trace sizing, impedance/diff-pair constraints, fab limits, and what was not verified.
   - List the checks run and the checks the user still needs. If `professional_claim` is false, list every failure and missing sign-off; do not present the board as release-ready.

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

## Scope and Professional Honesty

The automatic pipeline is designed to produce **professional, DRC-validated, fab-ready boards** for supported profiles, not to pretend that every electrical/mechanical property is solved. The hard gates (structural, DRC, unconnected, DFM, netlist parity, length match, stackup/impedance when the profile enables them, and required fabrication outputs) are automated. The profile decides which properties additionally require qualified human sign-off.

- `2layer-low-speed`: full automatic release is possible when the report says `professional_claim: true`.
- Controlled impedance: the pipeline computes geometry from the stated stackup and gates the result, but the fabricator's stackup and coupon must be confirmed; sign-off is still required.
- RF/mixed-signal: Freerouting is not acceptable for impedance-critical RF paths. Treat the route as a draft, re-route RF nets by hand, and obtain RF/EMC/thermal sign-off.
- Flex/rigid-flex: bend radius, coverlay, stiffeners, assembly and fatigue life are outside the pipeline; mechanical sign-off is required.
- High voltage: the pipeline applies the stated clearance/creepage rules and DRC, but safety isolation and regulatory compliance require a qualified reviewer.
- Dense BGA escape, very large boards, and designs with constraints the spec cannot express: use KiCad's GUI or IPC API, and say so rather than emitting a fragile approximation.

Never describe a design as professional, fabrication-ready, impedance-controlled, RF-verified, safe or compliant unless the report supports that exact claim.

## Validation Commands

```bash
# Environment: which backend can run here?
python3 scripts/env_probe.py
python3 scripts/env_probe.py --spec specs/board.json --require ci

# Spec validation (no KiCad required)
python3 scripts/design_spec.py validate specs/board.json
python3 scripts/design_spec.py explain specs/board.json
python3 -m unittest discover -s tests -v

# Automatic design: build, route, DRC/DFM, fab, report
python3 scripts/design_pipeline.py all specs/led-blinky.json

# Structural/geometry pre-flight (no KiCad required)
python3 scripts/check_pcb.py board.kicad_pcb
python3 scripts/check_pcb.py --strict board.kicad_pcb

# Authoritative checks when KiCad 8/9 is installed
kicad-cli pcb drc --severity-all --exit-code-violations board.kicad_pcb
kicad-cli pcb export gerbers -o gerbers/ board.kicad_pcb
kicad-cli pcb export drill -o gerbers/ board.kicad_pcb
# KiCad 9 calls the IPC-D-356 exporter ipcd356; older CLIs use ipc356.
kicad-cli pcb export ipcd356 -o board-ipc356.net board.kicad_pcb
```

**Freerouting 2.5.0 requires Java 25+.** It is intentionally not committed (62 MB). Download it next to the scripts or set `FREEROUTING_JAR`:

```bash
curl -fL -o scripts/freerouting.jar \
  https://github.com/freerouting/freerouting/releases/download/v2.5.0/freerouting-2.5.0.jar
```
