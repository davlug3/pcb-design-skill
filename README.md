# pcb-design-skill

An Agent Skill for designing manufacturable KiCad printed circuit boards (`.kicad_pcb`) with an LLM. It grounds the work in the KiCad file-format documentation, ships validator-clean example boards, and includes a dependency-free pre-flight checker.

## What it does

- Models the netlist before layout, then defines the board outline, layers/stackup, footprints, routing, vias and copper zones.
- Targets **KiCad 9** (`(version 20241229)`) by default, with a KiCad 8/6/7 dialect matrix.
- Catches the common generation failures: mixed-version tokens, undeclared nets, wrong layer ordinals, open outlines, dangling tracks, unfilled zones and incomplete footprints.
- States assumptions for current, impedance and fabrication limits instead of overclaiming.

## Contents

| Path | Purpose |
|------|---------|
| `SKILL.md` | Workflow, rules, sources and validation commands |
| `references/pcb-file-format.md` | Board sections, version matrix, layer ordinals, footprints, pads, tracks, zones |
| `references/sexpr-primitives.md` | Token syntax, quoting, strokes, UUIDs, pad/graphic/zone primitives |
| `references/practical-rules.md` | Stackup, placement, routing, current, SI/PI/EMI, impedance, thermal, DFM/DFA |
| `assets/minimal-2layer.kicad_pcb` | Validator-clean blank 2-layer board |
| `assets/example-led.kicad_pcb` | Validator-clean routed example (THT header, SMD R/LED, vias, mounting hole) |
| `assets/example.kicad_dru` | Custom-rule examples (netclass width, high-voltage clearance) |
| `scripts/check_pcb.py` | Structural/geometry pre-flight validator |
| `.github/workflows/validate.yml` | CI: structural checks plus KiCad 9 DRC in the official CLI container |

## Install

Copy or symlink this directory into your agent's skills directory, for example:

```bash
ln -s "$PWD" ~/.agents/skills/pcb-design-skill
# or
cp -R . ~/.config/opencode/skills/pcb-design-skill
```

The `name` in `SKILL.md` must match the directory name (`pcb-design-skill`).

## Use

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
```

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
