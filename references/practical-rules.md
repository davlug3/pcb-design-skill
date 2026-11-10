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


## Mandatory layout policy (apply to every new design and every placement/routing revision)

These are engineering constraints, not aesthetic preferences. Apply them before invoking automatic placement/routing, encode what the spec and KiCad can express, and explicitly report any rule that could not be enforced. The order of authority is:

1. The actual component datasheet, package/land-pattern recommendation, and vendor reference layout for that exact device and operating conditions.
2. The selected PCB fabricator's **current** capability table, stackup, and DFM feedback; and the selected assembler's assembly/fiducial/panel requirements.
3. Applicable IPC/IEC/UL standards and interface specifications.
4. The defaults and heuristics in this document.
5. Generic layout conventions or aesthetic preferences.

Never let a generic rule override a device datasheet, reference design, interface standard, or confirmed manufacturing constraint. If the fabricator or assembler is not selected, use conservative prototype defaults, label them as assumptions, and do not claim vendor-specific compliance.

### Placement rules (mandatory review)

The automatic placer accepts optional `placement.proximity` pairs as **soft component-center objectives**. Use them to encode known functional relationships (for example, a local bypass capacitor and its IC, or a crystal and its host IC). The `weight` scales the Manhattan-distance penalty relative to the existing net-affinity objective; higher values express stronger preferences. These hints never override fixed references, board bounds, keepouts, or clearances. They do not encode pin coordinates, pad-to-pad loop length, orientation, layer, return-current geometry, thermal coupling, or datasheet-specific placement zones. Do not use a proximity pair as proof that a capacitor is close enough to a particular supply pin. Pin-aware constraints require footprint pad geometry plus device-specific pin/net mapping and must be validated against the exact datasheet/reference layout.

Example:

```json
"placement": {
  "mode": "auto",
  "proximity": [
    {"refs": ["C1", "U1"], "weight": 3.0, "reason": "local bypass capacitor per device layout"},
    {"refs": ["Y1", "U1"], "weight": 2.0, "reason": "crystal near oscillator pins"}
  ]
}
```


- **Start from physical constraints:** lock board outline, mounting holes, connector locations/orientations, keepouts, enclosure interfaces, and service access before placing circuitry. Place edge-facing connectors at the relevant board edge and preserve insertion, cable, screw, and tool access.
- **Place by current flow and function:** arrange power entry/protection/conversion, processing, analog sensing, and external interfaces so major current and signal paths are short and understandable. Keep functional blocks together; do not cluster solely because components share a net.
- **IC decoupling is a hard placement constraint:** identify every supply pin and its recommended bypass network from the exact datasheet/reference layout. Place the appropriate high-frequency capacitor adjacent to the associated supply pin on the same layer where practical; minimize the complete supply–capacitor–IC-ground loop, connect directly to a low-inductance ground reference, and avoid routing unrelated signals through that loop. Use a dedicated nearby ground via when it shortens the return path. Do not assume one 100 nF capacitor per IC is sufficient; multi-rail, high-current, RF, and fast-switching devices may require per-pin or per-rail networks and specific values.
- **Crystals/clock sources:** follow the IC/vendor reference layout; keep resonator and load components close to the pins, minimize loop area, preserve symmetry where specified, and keep noisy/high-speed traces and copper aggressors away. Do not route unrelated signals beneath the resonator unless the reference design explicitly permits it.
- **Switch-mode power:** place input ceramic capacitors, switching IC/FETs, catch diode/synchronous path, and inductor to minimize the high-di/dt hot loop. Keep SW copper only as large as electrically/thermally necessary and away from feedback, analog, antenna, and connector regions. Place output capacitors at the inductor/load return as specified by the controller datasheet. Treat vendor priority-layout diagrams as authoritative.
- **Analog/mixed signal:** group sensitive inputs, references, amplifiers, ADCs and their local filters. Keep them away from switch nodes, inductors, clocks and high-current paths. Prefer a continuous reference plane and placement-based functional partitioning; do not split ground reflexively. If a split is genuinely required, document the return-current strategy and ensure signals never cross a reference discontinuity.
- **RF/antenna:** follow the exact module/chip reference layout, antenna keepout, ground-via fence, feed geometry and stackup. Do not let autorouting alter antenna geometry or controlled-impedance feeds. RF placement/routing requires manual review and the profile's RF/EMC sign-off.
- **Thermal:** identify dissipative parts from worst-case power, not nominal current alone. Provide copper spreading, thermal vias, heatsink/mechanical clearance and airflow as required by thermal analysis and package guidance. Keep temperature-sensitive sensors, references, crystals and electrolytics away from heat sources.
- **Assembly/serviceability:** respect courtyard and pick-and-place clearances; align similar parts consistently where useful; preserve polarity/pin-1 visibility, readable references, probe access, connector mating space and rework access. Never rotate polarized or asymmetric parts without checking pin mapping and assembly orientation.

### Routing and return-path rules (mandatory review)

- Establish a continuous reference plane for fast or sensitive signals. Route each signal over its intended reference; do not cross plane splits, voids, antipads or large cutouts that force return current to detour.
- Minimize high-frequency current-loop area. For switching converters, follow the datasheet's ordered placement/routing priorities; keep hot loops short and compact and prevent SW copper from becoming an unintended radiator.
- Route critical clocks, USB/Ethernet/DDR/LVDS and other fast-edge nets according to the applicable interface specification and stackup. Define impedance from the actual fab stackup and field-solver/calculator result; do not infer impedance from nominal trace width.
- Differential pairs require controlled width/gap, consistent coupling, symmetry, continuous reference, appropriate pair-to-pair skew limits, and minimal/as-symmetric-as-possible transitions. Do not apply arbitrary length matching where no timing requirement exists.
- Place return vias adjacent to signal vias when a high-speed signal changes reference layers. Avoid stubs and unnecessary layer changes.
- Size every power, ground, connector, fuse, via and neck-down bottleneck for continuous current, transient current, temperature rise, copper weight, ambient temperature and allowed voltage drop. Do not use a generic trace-width table as final sign-off; use IPC-2152 or validated thermal analysis and inspect the narrowest segment.
- Use 45-degree or curved corners where practical; avoid acute angles and unnecessary stubs. A 3W spacing rule is only a preliminary crosstalk heuristic, not a substitute for stackup-, edge-rate-, and geometry-aware SI analysis.
- Never allow autorouter completion to substitute for review of current loops, return paths, impedance, crosstalk, thermal bottlenecks, or sensitive analog/RF geometry.

### Fabrication and assembly rules (mandatory)

- Before finalizing geometry, select the actual fab/assembler where possible and retrieve its current capabilities for layer count, material, finished thickness, copper weight, trace/space, drill and via types, annular ring, aspect ratio, solder-mask registration/dam, copper-to-edge, slots, edge plating, surface finish, impedance control, and tolerances.
- Use ordinary-process capabilities rather than the vendor's advertised extreme/HDI limits unless the user explicitly accepts the cost, yield, and process tradeoffs and the vendor confirms the design.
- Keep copper-to-edge, hole-to-edge, NPTH-to-copper, solder-mask, silkscreen-to-pad, and component-to-edge clearances within the selected supplier's rules. Distinguish copper clearance from component-body/courtyard clearance.
- Avoid unnecessarily tiny drills, narrow traces/spaces, slivers, acute copper features, isolated copper islands, and excessive via-in-pad. Use via-in-pad only when the process supports the required fill/cap/plating and assembly method.
- Use solder-mask openings and paste apertures appropriate to the package and assembly process. Confirm exposed-pad stencil/paste segmentation and thermal-via treatment against the component and assembler guidance.
- Add global fiducials, local fiducials, tooling holes, rails, breakaway tabs, and panel features only to the selected assembler's requirements. Do not invent panelization geometry when the fabricator/assembler has not specified it.
- Ensure polarity, pin 1, reference designators, test points, connector labels, revision, and safety markings remain readable after assembly. Keep silkscreen off exposed pads and holes.
- Deliver fabrication and assembly outputs as a matched release set: Gerbers, NC drill, board outline, stackup/material notes, drill map, BOM, pick-and-place/centroid, assembly drawings, and IPC-D-356 or equivalent netlist where supported. Check that all files correspond to the same final board revision.

### Mandatory design review checklist

Before calling placement complete, review and record:
- [ ] Board outline, mounting, connector orientation, enclosure and keepouts are satisfied.
- [ ] Every IC's supply pins and datasheet-recommended bypass network were identified; capacitor placement and return loops were inspected.
- [ ] Switching hot loops, crystal/clock placement, analog/RF separation and thermal hot spots were reviewed where applicable.
- [ ] Functional blocks follow the intended signal and power flow; connector and service access are preserved.
- [ ] Critical nets and reference-plane continuity are identified before routing.

Before calling routing complete, review and record:
- [ ] Current/voltage-drop/temperature rise are checked for traces, vias, connectors, fuses and neck-downs.
- [ ] High-speed/RF/differential nets have appropriate stackup, reference, impedance and length/skew constraints.
- [ ] Return paths are continuous; no critical trace crosses a plane gap or split.
- [ ] Hot-loop area, SW-node copper, crosstalk, stubs and layer transitions are reviewed.
- [ ] All zones are refilled; DRC, unconnected-net, netlist-parity and DFM reports are inspected.

Before release, confirm:
- [ ] Fabricator and assembler rules were checked against the actual final geometry and output files.
- [ ] Gerber/drill/position/BOM/assembly files match the same revision and were inspected in a Gerber viewer or vendor DFM tool.
- [ ] All unresolved assumptions, deviations, warnings and human sign-offs are disclosed. A passing DRC is necessary but does not prove electrical, thermal, EMC, safety, or assembly correctness.

### Source hierarchy and reference reading

Use manufacturing sources first for process limits; use semiconductor-vendor application notes and reference designs for device-specific layout; use standards and technical books for underlying engineering principles. Re-check online limits on each project because capabilities change.

**Manufacturing / assembly sources (priority for manufacturability):**
- JLCPCB, PCB capabilities and process limits: https://jlcpcb.com/capabilities/pcb-capabilities
- JLCPCB, PCB design rules / DFM guidance: https://jlcpcb.com/blog/a-comprehensive-guide-for-optimal-results
- Sierra Circuits, DFM rules (silkscreen, mask and manufacturing clearances): https://www.protoexpress.com/kb/dfm-rules/
- CBS PCB, DFM guidelines (example conventional trace, spacing, edge and hole rules): https://cbspcb.com/dfm/
- For each order, the selected supplier's live capability/stackup page and its generated DFM report supersede these examples.

**Semiconductor vendor layout sources (priority for device-specific electrical layout):**
- Texas Instruments, *CC13xx/CC26xx Hardware Configuration and PCB Design Considerations*: https://www.ti.com/lit/pdf/swra640 (decoupling, ground return, RF and device layout)
- Texas Instruments, *High-Speed Layout Guidelines*: https://www.ti.com/lit/an/scaa082a/scaa082a.pdf (return paths, plane continuity, loop area)
- Analog Devices, *Basic Guidelines for Layout Design of Mixed-Signal PCBs*: https://www.analog.com/en/resources/analog-dialogue/articles/what-are-the-basic-guidelines-for-layout-design-of-mixed-signal-pcbs.html

**Technical books (principles and deeper study; not substitutes for current vendor process data):**
- Clyde F. Coombs Jr. and Happy T. Holden, *Printed Circuits Handbook*, 7th ed., McGraw-Hill, 2016. Especially Chapters 4–7 (DFM, manufacturing information, supplier qualification/process control), 16 (planning design/fabrication/assembly), 20–23 (electrical/mechanical design, SI/PI/EMC, current capacity and thermal design). Publisher: https://www.mheducation.com/highered/mhp/product/printed-circuits-handbook-seventh-edition.html
- Henry W. Ott, *Electromagnetic Compatibility Engineering*, Wiley, 2009. Grounding, bonding, return currents, shielding and EMC principles.
- Bruce R. Archambeault, *PCB Design for Real-World EMI Control*, Kluwer Academic Publishers, 2002. Practical EMC-oriented placement and routing principles.
- Howard Johnson and Martin Graham, *High-Speed Digital Design: A Handbook of Black Magic*, Prentice Hall, 1993. Transmission-line behavior, reflections, termination and high-speed layout.
- Douglas Brooks, *PCB Currents: How They Flow, How They React*, Prentice Hall, 2013. Current paths, return currents and PCB behavior.

When sources conflict, prefer the most specific and authoritative source for the issue: device datasheet/reference design for that device; chosen manufacturer's current process data for fabrication; applicable standard for compliance; then established technical literature. Do not copy numerical rules from books or articles without checking their assumptions and current supplier capability.
