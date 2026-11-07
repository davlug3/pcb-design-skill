# Practical & Physical PCB Design Rules — distilled reference

Source: JLCPCB, *PCB Design Rules and Guidelines: A Complete Best Practices Guide* — https://jlcpcb.com/blog/pcb-design-rules-best-practices (retrieved for practical/physical guidance).  
**Note:** Manufacturing capability numbers (trace/space, drill, annular ring, via-in-pad availability, etc.) are **manufacturer- and stackup-dependent**. Use them as sanity-check examples; verify against the target fabricator's current capabilities and your KiCad DRC rules.

## Schematic/intent handoff (before layout)

- Group circuits by function (power, MCU/core, RF/analog sections) to simplify placement/routing.
- Use clear net names (e.g., `+3V3_RF`, `+5V_DDR`) so power/signal intent is explicit.
- Run electrical checks (ERC in the schematic flow) before layout; fix unconnected pins/shorted outputs/logical errors early.
- Annotate layout constraints in intent: decoupling placement, keepouts, controlled impedance nets, length matching, high-current paths.

## Stackup and placement

- **Stackup is a design decision.** 2-layer boards suit simple/low-cost designs; 4+ layers commonly provide dedicated ground/power planes for stable references, better PI/SI, and easier routing.
- **Place critical components first:** connectors, MCU/processor, power entry/regulators, mounting/mechanical constraints.
- **Group by function; separate noisy/sensitive domains:** keep analog, digital, and high-power/switching sections physically separated where practical.
- **Decoupling is mandatory for IC power integrity:** place decoupling capacitors as close as possible to each IC's VCC/GND pins. Use local high-frequency decoupling (e.g., 0.1 µF/0.01 µF classes as appropriate to the device) plus bulk capacitance (e.g., 1–10 µF) for lower-frequency demand. Choose values/packages per datasheet and PDN needs; do not guess for critical designs.

## Routing fundamentals

- **Trace width:** determined primarily by current, allowable temperature rise, copper thickness/weight, and whether the trace is external or internal (internal dissipates heat less effectively). Use a trace-width/current calculator and the target fab's copper options; widen power traces.
- **Spacing/clearance:** determined by voltage, fabrication capability, assembly, and noise/crosstalk requirements. Encode constraints in KiCad design rules where possible.
- **Copper thickness:** heavier copper (e.g., 2 oz vs 1 oz) supports higher current or narrower traces; affects impedance and etching—confirm stackup.
- **Routing style:** keep traces short and direct; prefer 45° bends; avoid unnecessary 90° corners.
- **Vias:** minimize on high-speed paths (parasitic inductance/discontinuity). For dense BGA/escape routing, via-in-pad (POFV/plated-over filled vias) may be required—only if the target fab/process supports it and assembly risk is managed.
- **Planes/return paths:** on multilayer boards, keep the reference (often ground) plane solid and continuous under high-speed/sensitive signals. **Never route high-speed traces over a split/gap in the return plane**—it forces a large return-current loop, increasing EMI and degrading SI.

## Signal integrity (SI)

- Treat high-speed nets as transmission-line problems when edge rates/lengths demand it (USB, Ethernet, RF, DDR, clocks, fast edges).
- Keep high-speed traces short and referenced to an unbroken plane.
- **3W rule (crosstalk guideline):** separate parallel traces by at least ~3× the trace width to reduce coupling; increase spacing or add ground shielding for sensitive/long runs.
- Avoid stubs, unnecessary layer transitions, and routing across plane splits.

## Controlled impedance & differential pairs

- Controlled impedance depends on trace geometry, copper thickness, dielectric material/thickness, and distance to the reference plane (stackup). Do **not** hard-code impedance claims without a stackup/calculation.
- Match trace impedance to source/load/system requirements when specified (common examples: 50 Ω single-ended, 90–100 Ω differential, depending on interface—verify per standard/device).
- **Differential pairs:** route as a coupled pair with consistent spacing/geometry; keep lengths matched per interface timing budget; route symmetrically; avoid vias when possible and, if used, place symmetrically on both lines. Keep a continuous reference plane.

## Power integrity (PI)

- Provide low-impedance power distribution (planes or appropriately sized traces/pours).
- Place decoupling near load pins; minimize loop area (cap → IC power pin → IC ground → cap).
- Consider bulk + local HF decoupling and plane resonance/PDN needs for fast digital/RF designs; state assumptions if not analyzed.

## EMI/EMC

- Solid reference planes and small current loops reduce emissions/susceptibility.
- Keep clocks/high-frequency traces short and well-contained.
- Filter I/O and power where appropriate (e.g., ferrite beads, LC/RC filters) per design requirements.
- Consider shielding for sensitive RF sections when needed.

## DFM/DFA checks (apply to generated geometry)

- **DRC/manufacturing limits:** trace/space, min drill, annular ring, via sizes, copper-to-edge, solder mask slivers/clearance, silkscreen-on-pad, paste/mask expansions—must match the chosen fab and KiCad board setup/design rules.
- **Annular ring:** ensure adequate copper around drilled holes/vias.
- **Solder mask:** maintain required clearances; avoid unintended mask-defined issues unless intentional.
- **Assembly:** provide realistic component spacing; verify footprint pad geometry against datasheets; orient polarized parts consistently where practical; include fiducials/assembly features when required by the assembler.
- **BGA/dense packages:** confirm escape routing, via strategy, and assembly capability before committing geometry.

## How to use in this skill

When generating a `.kicad_pcb`:
1. Encode physical intent as concrete KiCad geometry/rules where possible (net widths/classes, clearances, zone/pour strategy, decoupling footprint placement, short/direct routing).
2. State assumptions for current, impedance, stackup, and fab limits.
3. Recommend final checks in KiCad/fab tools: DRC, zone refill, 3D/clearance, impedance calculator/stackup confirmation, and manufacturer DFM review.
4. Do not claim compliance with a specific manufacturer unless the user specified it and you verified current rules.
