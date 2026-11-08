# pcb-design-skill

An Agent Skill for **professional automatic PCB design** with KiCad 9. It turns a structured design spec — or a KiCad schematic/netlist — into a real library-footprint board, routes it with Freerouting, runs KiCad DRC/DFM and netlist-parity gates, exports a fabrication package, and writes a release report that says whether a professional claim is justified.

## What it does

- **Automatic design:** spec validation → board build with real KiCad footprints → Freerouting → DRC/DFM → Gerbers/drill/position/STEP/IPC-D-356 → gate report.
- **Multiple inputs:** canonical JSON spec, KiCad schematic, KiCad netlist, or an existing `.kicad_pcb` for route/check/export; a natural-language request is first converted into a reviewable spec.
- **Board-class profiles:** 2-layer low-speed (full automation), 4/6-layer controlled impedance, RF/mixed-signal, flex-rigid, and high-voltage. Profiles define the hard gates and required human sign-offs.
- **Professional honesty:** `professional_claim` is true only when every hard gate passes and every required sign-off is recorded. RF, EMC, thermal, safety and flex fatigue are never claimed without sign-off.
- **Manual review path:** the original KiCad 9 s-expression guidance, validator-clean assets and dependency-free structural checker remain available for review and patching.
- Targets **KiCad 9** (`(version 20241229)`), with a KiCad 8/6/7 dialect matrix for reading older boards.

## Contents

| Path | Purpose |
|------|---------|
| `SKILL.md` | Automatic pipeline, workflow, rules, sources and validation commands |
| `schemas/design-spec.schema.json` | Canonical design spec (board, rules, nets, components, placement, stackup, gates) |
| `profiles/*.json` | Board-class rule envelopes and release gates |
| `specs/led-blinky.json` | Reference spec for the automatic pipeline |
| `scripts/design_spec.py` | Dependency-free spec validator, netlist importer, impedance/current calculations, gate report |
| `scripts/pcb_build.py` | Spec → KiCad board with real library footprints (pcbnew) |
| `scripts/pcb_route.py` | DSN export → Freerouting → SES import → zone fill |
| `scripts/pcb_check.py` | Structural, DRC, DFM, netlist-parity and length-match gates |
| `scripts/pcb_fab.py` | Gerbers, drill, position, STEP, IPC-D-356 and fabrication README |
| `scripts/design_pipeline.py` | Orchestrates build/route/check/fab and writes the release report |
| `scripts/check_pcb.py` | Structural/geometry pre-flight validator |
| `tests/` | Unit tests for spec validation, impedance, netlist import and gates |
| `references/pcb-file-format.md` | Board sections, version matrix, layer ordinals, footprints, pads, tracks, zones |
| `references/sexpr-primitives.md` | Token syntax, quoting, strokes, UUIDs, pad/graphic/zone primitives |
| `references/practical-rules.md` | Stackup, placement, routing, current, SI/PI/EMI, impedance, thermal, DFM/DFA |
| `references/automation-pipeline.md` | Full automatic-design contract, gates, inputs, toolchain, troubleshooting |
| `assets/minimal-2layer.kicad_pcb` | Validator-clean blank 2-layer board |
| `assets/example-led.kicad_pcb` | Validator-clean hand-authored routed example |
| `assets/example.kicad_dru` | Custom-rule examples (netclass width, high-voltage clearance) |
| `.github/workflows/design.yml` | CI: build, route, DRC/DFM, fab export and report in the KiCad 9 container |
| `.github/workflows/validate.yml` | CI: structural checks plus KiCad 9 DRC on the shipped assets |

## Install

Copy or symlink this directory into your agent's skills directory, for example:

```bash
ln -s "$PWD" ~/.agents/skills/pcb-design-skill
# or
cp -R . ~/.config/opencode/skills/pcb-design-skill
```

The `name` in `SKILL.md` must match the directory name (`pcb-design-skill`).

## Automatic design

The automatic path needs:

- **KiCad 9** with its Python bindings (`python3 -c "import pcbnew"` must work)
- **Java 25+** (Freerouting 2.5.0 is built for Java 25)
- `freerouting-2.5.0.jar` (not committed; 62 MB)

On Termux, install `openjdk-25` and download the jar; KiCad itself is not available natively, so use CI for the pcbnew stages. On Debian/Ubuntu, use the official container or a KiCad 9 install.

```bash
# 1. Validate the design intent (no KiCad required)
python3 scripts/design_spec.py validate specs/led-blinky.json

# 2. Download Freerouting once
curl -fL -o scripts/freerouting.jar \
  https://github.com/freerouting/freerouting/releases/download/v2.5.0/freerouting-2.5.0.jar

# 3. Full automatic design
python3 scripts/design_pipeline.py all specs/led-blinky.json
```

Outputs land in `build/led-blinky/`:

```text
led-blinky.kicad_pcb        generated board
led-blinky.kicad_pro        design rules and netclasses
route-attempt-N/            DSN, SES and Freerouting logs
check/check.json            structural, DRC, DFM, parity, length-match results
fab/                        Gerbers, drill, position, STEP, IPC-D-356, zip, fab.json
report/design-report.json   machine-readable gate report
report/design-report.md     release summary
```

Every output listed in `profile.fab_outputs` is a hard gate. The 2-layer profile requires Gerbers, drill, position, STEP and IPC-D-356; a missing or failed output keeps `professional_claim` false. Gerbers contain only copper, paste, mask, silkscreen and edge cuts, and the fabrication README records the export log.

The pipeline exits non-zero unless `professional_claim` is true. That is the point: a routed board is not a release until the gates and required sign-offs pass.

**Other inputs:**

```bash
python3 scripts/design_pipeline.py all specs/board.json --schematic design.kicad_sch
python3 scripts/design_pipeline.py all specs/board.json --netlist design.net
python3 scripts/design_pipeline.py all specs/board.json --board existing.kicad_pcb --skip-build
```

**Profiles:** `2layer-low-speed` (full automation), `4layer-impedance` / `6layer-impedance` (stackup + impedance + length-match gates; sign-off required), `rf-mixed-signal` (RF/EMC/thermal sign-off), `flex-rigid` (mechanical/flex/assembly sign-off), `high-voltage` (safety/creepage sign-off).

## Manual use

Ask the agent to generate or review a PCB, for example:

> Design a 2-layer 30 × 20 mm board with a 5 V input header, an 0805 resistor and LED, and a GND pour. Output a KiCad 9 `.kicad_pcb` and validate it.

Then validate locally:

```bash
python3 scripts/check_pcb.py assets/example-led.kicad_pcb
python3 scripts/check_pcb.py --strict board.kicad_pcb
```

`check_pcb.py` requires only Python 3.9+ and checks:

- balanced s-expressions and required sections
- `version` / `generator_version` consistency
- declared layers and canonical ordinals
- net references and pad net-name consistency
- duplicate UUIDs and reference designators
- pad type/shape/drill/layer rules
- closed `Edge.Cuts` outline
- zero-length tracks and dangling endpoints (geometric connectivity)
- zones marked filled but missing `filled_polygon`
- footprints missing `attr` or courtyards

It is **not** a DRC replacement. It has been verified against the official KiCad 6.0, 7.0, 8.0 and 9.0 demo boards (0 structural errors on each). When KiCad 8/9 is installed, also run:

```bash
kicad-cli pcb drc --severity-all --exit-code-violations board.kicad_pcb
kicad-cli pcb export gerbers -o gerbers/ board.kicad_pcb
# KiCad 9 calls this ipcd356; older CLIs use ipc356.
kicad-cli pcb export ipcd356 -o board-ipc356.net board.kicad_pcb
```

## Automatic design in CI

`.github/workflows/design.yml` runs the full pipeline on pushes/PRs that touch a spec, profile, schema, script or the workflow:

1. **Spec job** — compiles the scripts, validates every `specs/*.json`, and checks the reference spec.
2. **Design job** — runs the official `kicad/kicad:9.0` container as root, installs `curl`, downloads a pinned Adoptium JRE 25, downloads Freerouting 2.5.0, and runs `design_pipeline.py all specs/led-blinky.json`.
3. **Artifacts** — `design-output` (board, project, route logs, check results, fabrication package) and `design-report`.
4. The job fails if `professional_claim` is false, so a broken/unrouted/DRC-failing board cannot silently merge.

The container is `linux/amd64`; use a GitHub-hosted runner or an amd64 Docker host. For local arm64, use a native KiCad 9 install, or let CI do the pcbnew/routing stages.

## Automated DRC in CI

`.github/workflows/validate.yml` runs on pushes and pull requests that touch a board, a custom-rule file, or the validator:

1. **Structural checks** — `check_pcb.py --strict` on every `assets/*.kicad_pcb`.
2. **KiCad 9 DRC** — the official `kicad/kicad:9.0` CLI container runs `kicad-cli pcb drc` on each board.
   - A full `--severity-all` report is written to the job summary and uploaded as the `kicad-drc-reports` artifact.
   - The job fails only on **error-level** violations (`--severity-error --exit-code-violations`), so cosmetic silkscreen warnings do not block a merge.
   - DRC uses KiCad's built-in constraints unless a `<board>.kicad_pro` is present. Commit the project file when the design relies on custom netclasses or rule minimums; otherwise CI checks against the default 0.20 mm track/clearance, 0.50 mm via and 0.30 mm hole minimums.
   - DRC does not refill copper zones. Run **Edit → Fill All Zones** in KiCad and save the board before committing a design that uses pours.
3. Trigger it manually with **Actions → Validate PCB assets → Run workflow**.

The container is `linux/amd64`; run the workflow on a GitHub-hosted runner (as configured) or any amd64 Docker host. For a local arm64 machine, use KiCad 9 from your distribution or the `kicad/kicad:9.0-arm64` image.

## KiCad version support

| KiCad | File version | Status |
|-------|--------------|--------|
| 9.0 | `20241229` | Primary target (new layer-ordinal scheme) |
| 8.0 | `20240108` | Supported (historical layer ordinals; see dialect matrix) |
| 7.0 / 6.0 | `20221018` / `20211014` | Read-only guidance; do not mix tokens |

## License

MIT — see `LICENSE`.
