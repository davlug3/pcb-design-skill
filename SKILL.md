---
name: pcb-design
description: Design and generate KiCad printed circuit board (PCB) files. Use when creating `.kicad_pcb` board layouts, placing footprints, routing tracks/vias, defining board outlines, nets, copper zones, or producing KiCad 6+ s-expression PCB output. Grounds decisions in KiCad developer file-format documentation rather than prior model knowledge.
---

# PCB Design (KiCad)

Create manufacturable, syntactically valid KiCad PCB designs and output them as `.kicad_pcb` s-expression files. **KiCad is the primary framework.** Do not rely on prior knowledge of the KiCad file format—use the KiCad developer documentation as the source of truth.

## Retrieval Sources (use before guessing)

| Resource | URL | Use for |
|----------|-----|---------|
| S-Expression intro (common syntax) | https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html | Syntax rules, coordinates, layers, stroke/text effects, footprints, pads, graphics, zones, groups |
| Board file format | https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/index.html | `.kicad_pcb` structure: header, general, page, layers, setup, nets, tracks, zones |
| Footprint library format | https://dev-docs.kicad.org/en/file-formats/sexpr-footprint/index.html | `.kicad_mod` / footprint definition context |
| KiCad file formats index | https://dev-docs.kicad.org/en/file-formats/index.html | Related formats (schematic/symbol) when needed |
| JLCPCB PCB design rules/best practices | https://jlcpcb.com/blog/pcb-design-rules-best-practices | Practical/physical guidance: placement, routing, SI/PI/EMI, impedance, differential pairs, DFM/DFA (verify fab-specific limits) |

If a token, attribute, layer name, unit, or section order is uncertain, fetch the relevant page. Local distilled references in `./references/` summarize key points from these sources but are not a substitute when details are ambiguous. Manufacturing capability numbers are manufacturer/stackup dependent—do not treat examples as universal.

## When to Use

- Generate a new KiCad board file (`.kicad_pcb`) from requirements.
- Add/edit board outline, layers, nets, footprints, pads, segments, vias, arcs, copper zones, or graphics.
- Convert a described circuit/layout into KiCad s-expression PCB output.
- Review or repair a generated `.kicad_pcb` for structural validity and common PCB/layout mistakes.
- Produce footprint-aware board geometry (pads, courtyards, silkscreen, mask intent) grounded in KiCad syntax.

## Local References

- `./references/pcb-file-format.md` — Board file sections, required tokens, minimal structure, nets/tracks/vias, plot/setup notes. Distilled from KiCad board format docs.
- `./references/sexpr-primitives.md` — S-expression conventions, coordinates/units, canonical layers, footprints/pads, graphics, zones, groups. Distilled from KiCad s-expression intro docs.
- `./references/practical-rules.md` — Practical/physical design rules: stackup/placement, trace width/current and spacing intent, return paths, decoupling, SI/PI/EMI, 3W crosstalk guideline, controlled impedance and differential pairs, DFM/DFA checks. Distilled from JLCPCB best-practices article; verify manufacturer-specific limits.

Search within references: `kicad_pcb`, `version`, `generator`, `general`, `layers`, `setup`, `net`, `segment`, `via`, `arc`, `zone`, `footprint`, `pad`, `Edge.Cuts`, `gr_line`, `uuid`, `at`, `pts`, `decoupling`, `impedance`, `differential`, `3W`, `return path`, `DFM`, `trace width`.

## Workflow

1. **Clarify design intent (do not guess silently on critical constraints).**
   - Board function, components, connectors, power/ground nets, target size/mechanical constraints.
   - Fabrication assumptions: layer count (default 2-layer unless specified), units (mm), copper weights/stackup details only if relevant.
   - If requirements are underspecified, state assumptions explicitly in the response and encode reasonable defaults in the file.

2. **Model the electrical netlist first.**
   - Assign every copper connection to a net. Define `(net 0 "")` plus named nets before/consistent with items that reference `net` ordinals.
   - Keep net ordinals stable and unique. Ensure pad/segment/via/zone `net` numbers match the nets section.

3. **Define board mechanics and stackup.**
   - Set board outline on `Edge.Cuts` as a closed shape (typically `gr_line`/`gr_arc` segments forming a loop).
   - Configure `layers` with canonical names and ordinals appropriate to the design (2-layer minimum: `F.Cu`, `B.Cu` plus masks/silks/`Edge.Cuts`; add inner layers only when specified).
   - Set `general (thickness ...)` and `setup` values appropriate to the board; avoid inventing obscure plot parameters when not needed.

4. **Place footprints/components.**
   - Use valid `footprint` structures with `layer`, `at` (for board placement), `fp_text` reference/value as appropriate, pads, and graphics.
   - Choose pad types/shapes/layers deliberately (`thru_hole` vs `smd`, drill, size, copper/mask/paste layers).
   - Place by function: connectors/MCU/power first; group related circuits; separate analog/digital/noisy domains where practical.
   - Place decoupling capacitors at each IC's power/ground pins with minimal loop area (values/packages per datasheet/PDN intent; state assumptions if unknown).
   - Respect clearances and courtyard intent; keep reference/value readable where practical.

5. **Route connectivity.**
   - Use `segment` for copper tracks, `via` for layer transitions, `arc` for curved tracks when appropriate.
   - Route on correct copper `layer` tokens; set `width` per net class/current intent (power nets wider than signals). For high current or controlled impedance, state assumptions and recommend calculator/stackup verification.
   - Keep traces short/direct; prefer 45° bends; minimize vias on high-speed paths.
   - Preserve return paths: avoid routing high-speed/sensitive traces over splits in the reference plane. Consider 3W spacing for parallel/crosstalk-prone runs.
   - For differential/impedance-controlled nets, only encode geometry that matches a stated stackup/requirement; otherwise flag as requiring impedance design and length matching in KiCad/fab tools.
   - Ensure every routed item references the correct `net` ordinal.

6. **Add planes/zones when useful.**
   - Use `zone` for copper pours/keepouts. Define `net`, `net_name`, `layer`, outline `polygon (pts ...)`, and `fill` settings.
   - Prefer solid reference behavior for PI/SI when layer stackup supports it; keep pours connected with sensible `connect_pads`, `min_thickness`, `thermal_*`, and hatch/fill attributes per docs.

7. **Validate before delivering.**
   - **Format:** s-expression well-formed (balanced parentheses, quoted strings where required); required board sections present (`kicad_pcb` header with `version`/`generator`, `general`, page/paper, `layers`, `setup`, nets, content).
   - **Geometry:** units in mm; reasonable coordinates/sizes; canonical layer names; closed `Edge.Cuts` boundary.
   - **Connectivity:** nets declared; pads/tracks/vias/zones reference valid ordinals.
   - **Physical/DFM sanity:** trace widths/spacings/drills/annular expectations are plausible for the stated fab process; decoupling placed near IC power pins; no obvious return-path splits under critical routes; polarized/mechanical constraints noted. Do not claim fab compliance without verified manufacturer rules.
   - **IDs:** `uuid`/`tstamp` present and unique where expected (verify format expectations in docs if unsure).

8. **Output.**
   - Write the complete file with `.kicad_pcb` extension.
   - Summarize assumptions (layer count, stackup, copper, current/trace sizing, impedance/diff-pair constraints, fab limits), net list, component/placement notes, and checks to run in KiCad/with the fabricator (ERC intent review, DRC, zone refill, 3D/clearance, impedance/stackup verification, manufacturer DFM/DFA).

## Critical Rules

1. **Docs over priors.** When format details matter, follow https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html and https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/index.html.
2. **Valid structure over partial snippets.** A generated `.kicad_pcb` should be loadable in KiCad as a board file, not just a fragment, unless the user explicitly asks for a fragment.
3. **Millimeters and canonical tokens.** Use mm values, lowercase tokens, quoted strings as required, and canonical layer names (e.g., `F.Cu`, `B.Cu`, `Edge.Cuts`, `F.SilkS`, `F.Mask`).
4. **Net integrity.** Net `0` is typically the empty net; all conductive items must reference correct net ordinals.
5. **Closed board outline.** Provide a manufacturable `Edge.Cuts` boundary.
6. **No silent electrical guesses.** If pin-to-net mapping, polarity, footprint choice, or safety spacing is unclear, state the assumption or ask.

## Anti-Patterns

- Inventing KiCad tokens/attributes or layer names not supported by the docs.
- Mixing coordinate units or using non-canonical layer names.
- Referencing net numbers that do not exist in the `(net ...)` section.
- Omitting `Edge.Cuts` or leaving an open board outline.
- Placing pads/tracks without layers, sizes, or required geometry.
- Relying on memory for exact s-expression shapes when the docs can be checked.
- Claiming electrical correctness/manufacturability without stating assumptions and recommended KiCad DRC checks.
