# Automatic design pipeline

This reference defines the professional automatic-design contract. The goal is
not "a router produced tracks"; it is **a reproducible board with verified
gates and an explicit release claim**.

## Pipeline

```text
input                         canonical spec             geometry                 release
-----                         --------------             --------                 -------
JSON spec -----------------+
KiCad netlist -------------+--> design spec --> validate --> build (pcbnew) --> route (Freerouting)
KiCad schematic -----------+                                   |                    |
Existing .kicad_pcb -------------------------------------------+--------------------+
                                                                                    |
                         +----------------------------------------------------------+
                         |
                         v
                structural + DRC + DFM + netlist parity + length match
                         |
                         v
                Gerbers / drill / position / STEP / IPC-D-356
                         |
                         v
                report/design-report.json + .md
                professional_claim = all hard gates + required sign-offs
```

## Canonical spec

`schemas/design-spec.schema.json` is the contract. `specs/led-blinky.json` is a
complete example. The important fields:

| Field | Meaning |
|---|---|
| `name`, `profile`, `title`, `revision` | Identity and board-class gate selection |
| `board.width_mm`, `height_mm`, `layers`, `thickness_mm`, `copper_oz` | Mechanics and stack |
| `board.stackup` | Physical copper/dielectric order, `er`, loss tangent, source, confirmed flag |
| `rules` | Minimum track, clearance, via, drill, annular ring, edge clearance, hole spacing |
| `netclasses` | Per-class width/clearance/via, optional impedance target and diff-pair geometry |
| `nets` | Class, voltage, current, impedance, diff-pair companion |
| `components` | Reference, value, **real KiCad library footprint**, position, pad→net map |
| `placement` | `fixed` or `auto`, margin, grid, clearance, keepouts |
| `routing` | Freerouting passes/threads/timeout/attempts |
| `zones` | Copper pours (net/layer/clearance/min thickness) |
| `fabrication` | Vendor and capability limits; checked against the design rules |
| `signoff` | Human sign-off items completed, reviewer, date, notes |
| `assumptions` | Explicit engineering assumptions; shown in the report |

Footprints use `Library:Footprint` and resolve against
`KICAD9_FOOTPRINT_DIR`, `KICAD_FOOTPRINT_DIR`, or `/usr/share/kicad/footprints`
(in the official container). A `.kicad_mod` path is also accepted. Do not
invent pad geometry in the spec; use the library.

## Input modes

### 1. JSON spec (canonical)

The agent converts the request into a spec. This is the most reproducible mode
and the only mode that fully expresses mechanics, rules, stackup and gates.

```bash
python3 scripts/design_spec.py validate specs/board.json
python3 scripts/design_pipeline.py all specs/board.json
```

### 2. KiCad netlist

The netlist is authoritative for components, footprints and pad→net
connectivity. The spec overlay supplies board size, rules, placement, zones and
routing. Every overlay component with pads must appear in the netlist; a
mechanical part (mounting hole) may be omitted.

```bash
python3 scripts/design_pipeline.py all specs/board.json --netlist design.net
```

### 3. KiCad schematic

The pipeline runs `kicad-cli sch export netlist`, then merges the result into
the overlay. This requires `kicad-cli` (the KiCad container or a local install).

```bash
python3 scripts/design_pipeline.py all specs/board.json --schematic design.kicad_sch
```

### 4. Existing `.kicad_pcb`

Use `--board` and `--skip-build` to route/check/export a board that already
exists. The spec still supplies the rules, netclasses and profile gates.

```bash
python3 scripts/design_pipeline.py all specs/board.json \
  --board existing.kicad_pcb --skip-build
```

### 5. Natural language

The agent writes the spec first, records assumptions in `assumptions[]`, then
runs the pipeline. Do not let a natural-language request bypass the spec: the
spec is the reviewable, reproducible artifact.

## Board-class profiles

Profiles live in `profiles/`. A profile defines the layer range, automation
level, hard gates, required sign-offs, routing defaults and fabrication
outputs. Every entry in `profile.fab_outputs` is a hard gate: a profile that
lists Gerbers, drill, position, STEP and IPC-D-356 fails the `fab` gate if any
one of them is missing.

| Profile | Layers | Automation | Human sign-off |
|---|---|---|---|
| `2layer-low-speed` | 2 | full | none |
| `4layer-impedance` | 4 | gated | `stackup`, `impedance` |
| `6layer-impedance` | 6 | gated | `stackup`, `impedance` |
| `rf-mixed-signal` | 2–12 | assisted | `rf`, `emc`, `thermal` |
| `flex-rigid` | 1–12 | assisted | `mechanical`, `flex_bend`, `assembly` |
| `high-voltage` | 1–8 | gated | `safety`, `creepage` |

Adding a profile is a JSON-only change. Keep the same gate vocabulary:
`error`, `zero`, `required`, `computed`, `report`, `human`, `off`.

## Gates

| Gate | Raw evidence | Pass condition |
|---|---|---|
| `structural` | `check_pcb.py --strict` | 0 errors and 0 warnings |
| `drc` | `kicad-cli pcb drc --severity-error --exit-code-violations` | exit 0 |
| `unconnected` | DRC report + pcbnew connectivity | 0 |
| `dfm` | spec rules vs fab capabilities + board track/via/hole checks | 0 errors |
| `netlist_parity` | board pad nets vs resolved spec | exact match |
| `stackup` | copper count, thickness, dielectric order, confirmation | required profile passes |
| `impedance` | closed-form microstrip/stripline calculation from the stated stackup | every target within tolerance |
| `length_match` | routed track lengths per netclass | diff-pair skew within `length_match_mm` |
| `thermal` | IPC-2152 width estimate for `current_a` | reported; not a thermal simulation |
| `emc` / `rf` / `safety` / `mechanical` | not measurable by this pipeline | human sign-off only |

The DRC gate uses **error severity**. Full `--severity-all` reports are written
for review, but warnings (for example library-footprint version drift) do not
block release. Structural `--strict` is intentionally stricter.

### Fabrication outputs

`scripts/pcb_fab.py` exports only the layers that belong in a fabrication
package: copper, paste, mask, silkscreen and `Edge.Cuts`. It writes a manifest
(`fab.json`) and a fabrication README into the package.

| Output | KiCad 9 command | Notes |
|---|---|---|
| `gerbers` | `pcb export gerbers --layers ...` | Relevant copper/mask/silk/paste/edge layers only |
| `drill` | `pcb export drill` | Excellon drill file |
| `pos` | `pcb export pos --format csv` | Assembly position file |
| `step` | `pcb export step` | Board geometry; component 3D models need the KiCad 3D library |
| `ipc356` | `pcb export ipcd356` | Falls back to `ipc356` on older CLIs |

The `fab` gate is false when a required output is missing. The report includes
`required_outputs` and `missing_required` so a failed package is explicit.

### Professional claim

`report/design-report.json` contains:

```json
{
  "professional_claim": true,
  "failures": [],
  "required_signoff": [],
  "missing_signoff": [],
  "automated": {"structural": 0, "drc": 0, "unconnected": 0},
  "disclaimer": "..."
}
```

`professional_claim` is true only when:

1. the spec validates;
2. every hard gate (`error`, `zero`, `required`, `computed`) passes;
3. all `profile.human_signoff` items are in `signoff.completed`.

For RF, EMC, thermal, safety and flex fatigue, the pipeline can report and
constrain but cannot certify. That is why those profiles require a human
sign-off; the skill must never overclaim them.

## Toolchain

| Tool | Version | Role |
|---|---|---|
| KiCad | 9.0 (`20241229`) | pcbnew board build, DSN/SES, DRC, fab exports |
| pcbnew Python | bundled with KiCad 9 | board construction, zones, connectivity |
| `kicad-cli` | bundled with KiCad 9 | schematic netlist, DRC, Gerbers/drill/pos/STEP/IPC-D-356 |
| Freerouting | 2.5.0 | Specctra DSN/SES autorouter |
| Java | 25+ | Freerouting 2.5.0 runtime (class file major 69) |
| Python | 3.9+ | spec/gates/report; stdlib only |

Freerouting 2.5.0 and 2.4.1 are both built for Java 25 in the current releases.
The CI workflow downloads a pinned Adoptium JRE 25 instead of relying on the
Debian default (Java 17).

### Local vs CI (automatic)

`design_pipeline.py` defaults to `--backend auto`:

1. **Probe** — `scripts/env_probe.py` checks pcbnew, `kicad-cli`, Java 25, the
   Freerouting jar, `gh`, the git remote and whether the spec/scripts are
   committed and pushed.
2. **Local** — when every requested stage has its tools, the pipeline runs
   locally. This is the normal path on a real PC with KiCad 9 installed.
3. **CI** — when local tools are missing but `gh` is authenticated and the
   current branch is clean and pushed, the pipeline dispatches
   `.github/workflows/design.yml` with the spec path (and any `--schematic`,
   `--netlist`, `--board` or skip flags) as workflow inputs.
4. **Neither** — the pipeline stays local and reports exactly which tools are
   missing, plus why CI is not usable.

```bash
python3 scripts/env_probe.py
python3 scripts/design_pipeline.py all specs/board.json                  # auto
python3 scripts/design_pipeline.py all specs/board.json --backend local
python3 scripts/design_pipeline.py all specs/board.json --backend ci --ci-watch
```

CI dispatch refuses to run from a dirty working tree or an unpushed branch,
because Actions checks out the pushed revision. Commit and push the spec,
profiles, schemas, scripts and workflow first. Stage commands
(`build`/`route`/`check`/`fab`) are local-only: CI cannot see uncommitted stage
files, so use `all --backend ci` for an end-to-end run. Inside Actions the
pipeline forces the local backend so it can never dispatch itself recursively.

- **Local with KiCad 9:** `design_pipeline.py all` works end to end; set
  `KICAD9_FOOTPRINT_DIR` if the libraries are not in `/usr/share/kicad/footprints`.
- **Local without KiCad (e.g. arm64 Termux):** generate/validate specs locally,
  commit and push, then let `--backend auto` dispatch CI. Java 25 is available
  in Termux via `pkg install openjdk-25`, but pcbnew is not.
- **CI:** the design workflow runs as root inside `kicad/kicad:9.0`, installs
  `curl`, downloads JRE 25 + Freerouting, and runs the pipeline. It uploads
  `design-output` and `design-report`. A manual dispatch can select any
  `specs/*.json` and pass schematic/netlist/board inputs or skip flags.

## Routing behavior

- The builder exports DSN with `pcbnew.ExportSpecctraDSN`.
- Each attempt runs Freerouting 2.5 with `-de`, `-do`,
  `--router.autorouter.max_passes`, `--router.optimizer.max_threads`,
  `--router.optimizer.enabled` and `--gui.enabled=false`.
- The SES is imported with `pcbnew.ImportSpecctraSES`, zones are refilled, and
  the attempt's unconnected count is measured with pcbnew connectivity.
- The pipeline keeps the attempt with the fewest unconnected items (then fewer
  tracks) and stops early at zero unconnected.
- The final DRC/parity gate is authoritative. Freerouting success is not a
  release condition by itself.

## Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `pcbnew is not importable` | Local Python is not KiCad's Python | Run in the KiCad container, or use KiCad's bundled python / set `KICAD_PYTHON` |
| `Java not found` / `UnsupportedClassVersionError` | Java < 25 for Freerouting 2.5.0 | Install Java 25+, or run CI |
| `Freerouting produced no SES` | DSN invalid, Java failure, or timeout | Read `route-attempt-N/freerouting-N.log`; check Java version and DSN |
| DRC report cannot be written | Container user cannot write `check/` | The workflow runs as root and creates writable dirs; locally `chmod 0777 check` |
| `lib_footprint_mismatch` | Embedded footprint differs from the installed library | Warning only; use the same library version or accept the warning |
| Unconnected > 0 | Router could not finish | More passes/attempts, improve placement, reduce density, add layers |
| DRC errors after route | Clearance/width/edge/silk violations | Fix the spec rules/placement; do not hand-edit and re-claim |
| Impedance target fails | Width/stackup combination is wrong | Adjust `track_width_mm`, dielectric thickness/er, or target |
| `professional_claim: false` with no DRC error | Missing human sign-off | Complete the profile sign-off or change to the correct profile |
| `CI DISPATCH FAILED: refusing ... uncommitted` | Actions would build the pushed revision, not the working tree | Commit and push the spec/scripts, then rerun `--backend ci` |
| `CI DISPATCH FAILED: ... no upstream` | Branch was never pushed | `git push -u origin <branch>` |
| `CI DISPATCH FAILED: gh CLI not found` | GitHub CLI missing | `pkg install gh` (Termux) or install `gh`, then `gh auth login` |
| Auto chose local but pcbnew is missing | CI is not usable (dirty tree, no auth, or no workflow) | Fix the CI blockers printed by the probe, or run on a KiCad host |

## Extending the pipeline

1. Add a profile JSON with the same gate vocabulary.
2. Add a spec with `board.layers`, `stackup`, `netclasses` and `nets`.
3. Add tests for any new calculation/gate.
4. Run `python3 -m unittest discover -s tests -v`.
5. Run the design workflow and inspect `report/design-report.md`, `fab/fab.json`
   and the DRC report, not just the CI exit code.

## Limits

- The impedance formulas are closed-form approximations, not a field solver or
  the fabricator's coupon.
- IPC-2152/2221 checks are design aids; they are not thermal or safety
  certification.
- Freerouting is not impedance-aware, RF-aware or length-aware. The pipeline
  measures and gates what it can, and requires sign-off for what it cannot.
- Auto-placement is deterministic shelf packing; review it before release.
- Flex bend life, EMC, thermal, isolation and regulatory compliance are outside
  the pipeline's authority.
- STEP files contain board geometry. Component 3D models are included only when
  the KiCad 3D library is installed in the build environment.
