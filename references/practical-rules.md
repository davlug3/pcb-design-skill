# Practical & Physical PCB Design Rules

Sources (accessed 2026-11):
- JLCPCB, *PCB Capabilities* — https://jlcpcb.com/capabilities/pcb-capabilities (current process limits)
- JLCPCB, *PCB Design Rules and Guidelines* — https://jlcpcb.com/blog/pcb-design-rules-best-practices (layout guidance)
- IPC-2221 / IPC-2152 (trace width, current, clearance), IPC-7351 (footprint pads), IPC-A-610 (acceptability)
- KiCad 9 Pcbnew documentation — https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html

**Warning:** fabrication limits and material properties are vendor-, stackup- and process-specific. The numbers below are conservative starting points for a 2-layer prototype, not guarantees. Verify against the chosen fab's current capability page and your KiCad DRC rules before ordering.

## Starting-point defaults (2-layer prototype)

| Parameter | Conservative starting point |
|-----------|-----------------------------|
| Board | 1.6 mm FR4, 1 oz copper (35 µm), HASL or lead-free HASL |
| Signal trace / space | 0.20 / 0.20 mm (many fabs accept 0.127 mm; confirm) |
| Power trace | 0.4–1.0 mm, sized from current (see below) |
| Via | 0.6 mm diameter / 0.3 mm drill |
| Minimum drill | 0.3 mm |
| Annular ring | ≥0.13–0.15 mm |
| Copper to board edge | ≥0.3 mm (0.5 mm preferred) |
| Solder-mask clearance | 0.05–0.1 mm |
| Silkscreen width / text height | 0.15 mm / ≥1.0 mm |
| Courtyard | ≥0.25 mm around pads/body |
| Mounting hole to edge | ≥1.0 mm of material |

Move to 4 layers when you need a solid reference plane, dense routing, or controlled impedance. A common 4-layer order is **signal / GND / power / signal** with the plane layers adjacent to the signal layers.

## Schematic/intent handoff

- Group circuits by function (power, MCU/core, analog/RF, interfaces).
- Use explicit net names (`+3V3`, `+5V_USB`, `GND`, `USB_DP`) so intent is visible in the board file.
- Resolve ERC issues in the schematic first; a board generated without a schematic has no electrical cross-check.
- Capture layout constraints up front: decoupling placement, keepouts, controlled-impedance nets, length matching, high-current paths, isolation barriers.

## Placement

- Place connectors, mounting holes, power entry/regulation and the MCU first; these define the mechanical envelope.
- Group by function and separate noisy (switching supplies, digital, radio) from sensitive (analog, sensors, crystals) domains.
- Keep switching converters and their hot loops compact; keep crystals/oscillators close to their IC with a local ground and no traces underneath.
- Decoupling: one small (0.1 µF class) capacitor per IC power pin with the shortest possible loop (cap → pin → ground), plus bulk (1–10 µF) per rail. Values/packages come from the datasheet/PDN, not from habit.
- Respect courtyards and assembly spacing; leave room for rework and probes.
- Keep reference designators visible and consistently oriented; mark polarity on silkscreen.

## Routing fundamentals

- Route short and direct; prefer 45° bends; avoid unnecessary layer changes.
- Widen power and ground paths; use planes/pours for low-impedance distribution rather than long thin traces.
- Keep differential and high-speed pairs on one layer where possible, referenced to a continuous plane.
- Do not route high-speed/sensitive signals across a split or gap in the reference plane — the return current must detour, creating a large loop (EMI + SI degradation).
- Keep analog traces away from switching nodes; do not route under inductors or switch-mode components.
- Leave copper-to-edge margin and keep silkscreen off pads and vias.

## Current, trace width and vias

First-order IPC-2221 relationship (A in mil², ΔT in °C, I in A):

```
I = k · ΔT^0.44 · A^0.725        k = 0.048 (external), 0.024 (internal)
```

Solve for area, then width = area / copper thickness:

```
A = ( I / (k · ΔT^0.44) )^(1/0.725)
```

Example: 1 A, 10 °C rise, external, 1 oz (1.378 mil) → A ≈ 16.3 mil² → ≈ 11.8 mil ≈ **0.30 mm** minimum. Add margin for connectors, transients, high ambient and enclosed enclosures. IPC-2152 is the more modern basis; use a calculator for anything safety- or thermally-critical.

- Copper weight matters: 2 oz supports roughly twice the current for the same width but etches differently and changes impedance.
- Vias have far less copper cross-section than a wide trace; use multiple vias for high current and for power/ground transitions.
- Connectors, fuses and narrow neck-downs are common thermal/current bottlenecks — size them explicitly.
- Never rely on a zone alone for a high-current path unless you have checked the actual fill width.

## Clearance and creepage

- Clearance is driven by voltage, altitude, pollution degree, coating, and the applicable standard (IEC 60664-1, IPC-2221). Do not scale linearly from a single “0.5 mm” rule.
- Creepage (surface distance) is generally larger than clearance for high voltage; slots and conformal coating change the calculation.
- Mains/isolated sections need a clearly marked barrier, appropriate spacing, and often slots; escalate to the relevant safety standard.
- Low-voltage digital: 0.15–0.2 mm is typical; analog/high-impedance nodes need more for leakage and guard rings.

## Signal integrity (SI)

- Treat USB, Ethernet, HDMI, DDR, RF, clocks and fast edges as transmission lines when length/edge rate demands it.
- Keep high-speed traces short, referenced to an unbroken plane, and avoid stubs.
- 3W guideline: parallel traces separated by ≥3× trace width reduce crosstalk; use more spacing or ground shielding for long/sensitive runs.
- Minimize layer transitions on high-speed nets; when unavoidable, provide return vias next to signal vias.
- Match lengths only where the interface timing budget requires it, and match the right reference (pair-to-pair skew, not just total length).

## Controlled impedance and differential pairs

- Impedance depends on trace width, copper thickness, dielectric thickness/εr, soldermask and the reference-plane distance. **No impedance claim is valid without a stackup and a field-solver/calculator result.**
- Common targets (verify per interface): 50 Ω single-ended, 90 Ω USB, 100 Ω Ethernet/LVDS.
- Differential pairs: constant gap and width, symmetric routing, matched lengths, minimal vias, continuous reference plane. If vias are required, place them symmetrically on both lines.
- In KiCad, diff-pair width/gap and impedance live in the **netclass/project + stackup**, not in `.kicad_pcb`; the board file only contains the resulting geometry.
- Report the assumed stackup and the tool used; mark impedance as unverified if no calculation was done.

## Power integrity (PI)

- Provide low-impedance distribution: planes or short wide traces, with local decoupling at each load.
- Minimize the loop: capacitor → IC power pin → IC ground → capacitor.
- Use bulk + local HF decoupling; for fast digital/RF, consider plane capacitance, PDN analysis and plane resonance.
- Do not daisy-chain power through thin traces between ICs; star or plane distribution is better.

## EMI/EMC

- Solid reference planes and small current loops are the primary defense.
- Keep clocks and switching nodes short, contained and away from connectors/edges.
- Filter I/O and power where appropriate (ferrite beads, common-mode chokes, RC/LC), per interface requirements.
- Consider shielding cans or grounded fences for sensitive RF sections.
- Stitching vias along board edges and around RF blocks help, but cannot fix a split return path.

## Thermal

- Copper area, thermal vias, copper weight and airflow determine temperature rise.
- Power components (regulators, FETs, LEDs) need copper pours/thermal pads and often thermal vias to an inner/bottom copper area.
- Keep heat-sensitive parts (crystals, sensors, electrolytics) away from hot components.
- Do not put thermal relief spokes on pads that must conduct heat; use solid connections where appropriate.

## Mechanical and manufacturing

- Include mounting holes (NPTH, no annular ring) with adequate keepout and material to the edge.
- Add fiducials (three global, non-collinear) if the assembler requires them; keep them clear of silkscreen and mask.
- Respect connector overhang, cable bend radius, enclosure walls, screw heads and standoffs.
- Panelization, mouse-bites and edge rails are fabrication decisions; confirm with the fab/assembler.
- Specify surface finish (HASL/ENIG), board thickness, copper weight, mask/silk colors only when they matter.

## DFM/DFA checks (apply to generated geometry)

- **DRC limits:** trace/space, min drill, annular ring, via size/drill, copper-to-edge, hole-to-hole, hole-to-copper.
- **Mask/paste:** solder-mask clearance and slivers; paste aperture ratio; avoid mask-defined pads unless intentional.
- **Silkscreen:** minimum width/height, no silk on pads or holes, readable references, polarity marks.
- **Courtyards:** present for every footprint; no overlaps; assembly spacing respected.
- **Footprints:** pad geometry matches the datasheet/IPC-7351; pin 1 and polarity correct; paste/mask layers correct for the process.
- **Holes:** NPTH vs PTH correct; no unsupported via-in-pad without filling/capping.
- **Panel/assembly:** fiducials, tooling holes, edge clearance, component orientation and rework access.

## How to use this in the skill

1. Encode intent as concrete geometry/rules: widths, clearances, pours, decoupling placement, short/direct routes.
2. State assumptions for current, impedance, stackup and fab limits.
3. Run `scripts/check_pcb.py --strict` and, when available, `kicad-cli pcb drc`.
4. Recommend final checks in KiCad/fab tools: DRC, zone refill, 3D/clearance, impedance calculator/stackup confirmation, manufacturer DFM/DFA.
5. Never claim compliance with a specific manufacturer unless the user specified it and you verified current rules.
