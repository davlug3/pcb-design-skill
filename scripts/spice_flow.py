#!/usr/bin/env python3
"""Interactive completion of an LTspice/SPICE import.

SPICE netlists describe connectivity, not physical parts. They carry no
footprints and no pad numbers. When the pipeline is given ``--ltspice``, this
module fills those gaps with the same visual question picker used for design
review:

* pin maps for multi-pin parts (BJT, MOSFET, JFET, subcircuits)
* footprints for parts the overlay does not already describe
* automatic placement when imported parts have no coordinates
* optional node aliases for LTspice auto-generated nets

Everything is optional in the sense that the user can supply the same data
with ``--pin-map``, ``--node-aliases`` or directly in the overlay spec. If the
import still needs information and asking is disabled (CI, ``--no-ask``), a
``SpiceInputError`` lists exactly what is missing.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import design_spec as ds
import visual_ask

AskFn = Callable[[dict[str, Any], Path], dict[str, Any]]


class SpiceInputError(ValueError):
    """The SPICE import needs information that only the user can provide."""


@dataclass
class SpiceImport:
    spec: dict[str, Any]
    pin_map: dict[str, Any]
    node_aliases: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)


@dataclass
class _AliasQuestions:
    payload: dict[str, Any]
    options: dict[str, dict[str, str | None]]
    nodes: dict[str, str]
    total_auto_nodes: int = 0


_AUTO_NODE_RE = re.compile(r"^N\d+$", re.IGNORECASE)
_LED_WORDS = ("LED", "RED", "GREEN", "BLUE", "YELLOW", "WHITE", "AMBER", "RGB")
_NODE_ORDER = {
    "Q": ("C", "B", "E", "S"),
    "M": ("D", "G", "S", "B"),
    "J": ("D", "G", "S"),
    "D": ("A", "K"),
}

# Common package pinouts. A value may be a list when one semantic pin connects
# to several pads, such as the tab of a SOT-223.
_PINOUT_OPTIONS: dict[str, list[tuple[str, str, dict[str, Any]]]] = {
    "Q": [
        ("sot23_bce", "SOT-23 · B=1, E=2, C=3", {"b": "1", "e": "2", "c": "3"}),
        ("to92_ebc", "TO-92 · E=1, B=2, C=3", {"e": "1", "b": "2", "c": "3"}),
        ("to92_cbe", "TO-92 · C=1, B=2, E=3", {"c": "1", "b": "2", "e": "3"}),
        (
            "sot223_bce",
            "SOT-223 · B=1, C=2+tab, E=3",
            {"b": "1", "c": ["2", "4"], "e": "3"},
        ),
    ],
    "M": [
        ("sot23_gsd", "SOT-23 · G=1, S=2, D=3", {"g": "1", "s": "2", "d": "3"}),
        ("sot23_gds", "SOT-23 · G=1, D=2, S=3", {"g": "1", "d": "2", "s": "3"}),
        (
            "sot223_gds",
            "SOT-223 · G=1, D=2+tab, S=3",
            {"g": "1", "d": ["2", "4"], "s": "3"},
        ),
    ],
    "J": [
        ("sot23_gsd", "SOT-23 · G=1, S=2, D=3", {"g": "1", "s": "2", "d": "3"}),
        ("sot23_gds", "SOT-23 · G=1, D=2, S=3", {"g": "1", "d": "2", "s": "3"}),
        ("to92_dgs", "TO-92 · D=1, G=2, S=3", {"d": "1", "g": "2", "s": "3"}),
        ("to92_gds", "TO-92 · G=1, D=2, S=3", {"g": "1", "d": "2", "s": "3"}),
    ],
}


def _sanitize_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "item"


def _footprint_qid(ref: str) -> str:
    # Keep this identical to visual_ask.build_spec_payload.
    return "footprint_" + re.sub(r"[^A-Za-z0-9_.-]+", "_", ref).strip("_")


def _answer_value(answer: Any) -> Any:
    if isinstance(answer, dict) and "value" in answer:
        return answer.get("value")
    return answer


def _category_for(prefix: str | None, value: str) -> str | None:
    if prefix == "R":
        return "resistor"
    if prefix == "C":
        return "capacitor"
    if prefix == "L":
        return "inductor"
    if prefix == "D":
        upper = value.upper()
        if any(word in upper for word in _LED_WORDS):
            return "led"
        return "diode"
    if prefix in ("Q", "M", "J"):
        return "sot"
    if prefix in ("X", "U", "A"):
        return "ic"
    return None


def _pinmap_placeholder(prefix: str, node_count: int) -> str:
    if prefix == "Q":
        base = {"c": "2", "b": "1", "e": "3"}
        if node_count >= 4:
            base["s"] = "4"
        return json.dumps(base)
    if prefix == "M":
        base = {"d": "3", "g": "1", "s": "2"}
        if node_count >= 4:
            base["b"] = "4"
        return json.dumps(base)
    if prefix == "J":
        return json.dumps({"d": "3", "g": "1", "s": "2"})
    return json.dumps([str(index) for index in range(1, max(node_count, 2) + 1)])


def _pinmap_payload(unresolved: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    questions: list[dict[str, Any]] = []
    option_maps: dict[str, dict[str, Any]] = {}
    for item in unresolved:
        ref = str(item["ref"])
        prefix = str(item["prefix"])
        qid = "pinmap_" + _sanitize_id(ref)
        nodes = list(item.get("nodes") or [])
        value = str(item.get("value") or "")
        label = f"{ref} ({value})" if value else ref

        if prefix in ("Q", "M", "J") and len(nodes) == 3:
            options = [
                {"id": option_id, "label": option_label}
                for option_id, option_label, _ in _PINOUT_OPTIONS[prefix]
            ]
            options.append({"id": "custom", "label": "Other · type the pin map below"})
            order = _NODE_ORDER.get(prefix, ())
            order_text = ", ".join(order[: len(nodes)])
            help_text = (
                "SPICE gives node names, not footprint pads. Pick the package "
                "pinout that matches the footprint."
            )
            if order_text:
                help_text += f" Node order: {order_text}."
            questions.append(
                {
                    "id": qid,
                    "type": "choice",
                    "prompt": f"Which pinout does {label} use?",
                    "help": help_text,
                    "required": False,
                    "options": options,
                }
            )
            option_maps[qid] = {
                option_id: mapping for option_id, _, mapping in _PINOUT_OPTIONS[prefix]
            }
            questions.append(
                {
                    "id": qid + "_custom",
                    "type": "text",
                    "required": False,
                    "prompt": f"Or enter a custom pin map for {ref}",
                    "help": (
                        "JSON object with pin names as keys and pad numbers as "
                        "values. Use a list for a tab or another multi-pad pin. "
                        "The footprint you choose later must have these pads. "
                        "If the part has a fourth substrate/bulk node, include "
                        "s (Q) or b (M)."
                    ),
                    "placeholder": _pinmap_placeholder(prefix, len(nodes)),
                }
            )
            continue

        if prefix == "X":
            subckt_name = item.get("subckt_name")
            subckt_pins = list(item.get("subckt_pins") or [])
            instance_args = list(item.get("args") or [])
            help_text = "Enter the pads in the same order as the instance nodes."
            if instance_args:
                help_text += f" Instance: {ref} {' '.join(map(str, instance_args))}."
            if subckt_pins:
                help_text += (
                    f" {subckt_name or 'Subcircuit'} pins: {', '.join(map(str, subckt_pins))}."
                )
            placeholder = (
                ", ".join(str(index) for index in range(1, len(subckt_pins) + 1))
                if subckt_pins
                else "1, 2, 3, 4"
            )
            questions.append(
                {
                    "id": qid,
                    "type": "text",
                    "required": True,
                    "prompt": f"Which pads does {ref} use?",
                    "help": help_text,
                    "placeholder": placeholder,
                }
            )
            continue

        order = _NODE_ORDER.get(prefix, ())
        order_text = ", ".join(order[: len(nodes)])
        help_text = f"{len(nodes)}-node {prefix} element."
        if order_text:
            help_text += f" Node order: {order_text}."
        help_text += (
            " Use a JSON object of pin names to pad numbers, or an ordered JSON "
            "array of pads."
        )
        questions.append(
            {
                "id": qid,
                "type": "text",
                "required": True,
                "prompt": f"Enter the pin map for {label}",
                "help": help_text,
                "placeholder": _pinmap_placeholder(prefix, len(nodes)),
            }
        )

    return (
        {
            "title": "Complete the SPICE pin map",
            "intro": (
                "The SPICE netlist has no footprint pad numbers. Choose a "
                "pinout for each multi-pin part."
            ),
            "submit_label": "Use these pinouts",
            "questions": questions,
        },
        option_maps,
    )


def _parse_pin_mapping(text: str, ref: str) -> Any:
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SpiceInputError(f"{ref}: pin map is not valid JSON: {exc}") from exc
        if not isinstance(value, (dict, list)):
            raise SpiceInputError(f"{ref}: pin map must be a JSON object or array")
        return value
    pads = [part for part in re.split(r"[\s,]+", text) if part]
    if not pads:
        raise SpiceInputError(f"{ref}: pin map is empty")
    return pads


def _apply_pinmap_answers(
    unresolved: list[dict[str, Any]],
    answers: dict[str, Any],
    option_maps: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for item in unresolved:
        ref = str(item["ref"])
        qid = "pinmap_" + _sanitize_id(ref)
        custom = _answer_value(answers.get(qid + "_custom"))
        if isinstance(custom, str) and custom.strip():
            refs[ref] = _parse_pin_mapping(custom, ref)
            continue
        value = _answer_value(answers.get(qid))
        if value is None or (isinstance(value, str) and not value.strip()):
            raise SpiceInputError(f"{ref}: no pinout answer was provided")
        if value == "custom":
            raise SpiceInputError(
                f"{ref}: choose a pinout or enter a custom pin map"
            )
        mapping = option_maps.get(qid, {}).get(str(value))
        if mapping is not None:
            refs[ref] = mapping
            continue
        if isinstance(value, str):
            refs[ref] = _parse_pin_mapping(value, ref)
            continue
        raise SpiceInputError(f"{ref}: unknown pinout option {value!r}")
    return refs


def _alias_questions(
    overlay: dict[str, Any],
    netlist: dict[str, Any],
    aliases: dict[str, str],
    *,
    limit: int = 12,
) -> _AliasQuestions | None:
    netlist_nets = {
        str(net.get("name"))
        for net in netlist.get("nets", [])
        if isinstance(net, dict) and net.get("name")
    }
    spec_nets = [
        str(net.get("name"))
        for net in overlay.get("nets", [])
        if isinstance(net, dict) and net.get("name")
    ]
    missing = [name for name in spec_nets if name not in netlist_nets]
    auto_nodes = sorted(
        name
        for name in netlist_nets
        if _AUTO_NODE_RE.match(name) and name not in aliases
    )
    if not missing or not auto_nodes:
        return None

    questions: list[dict[str, Any]] = []
    options: dict[str, dict[str, str | None]] = {}
    nodes: dict[str, str] = {}
    for node in auto_nodes[:limit]:
        qid = "node_" + _sanitize_id(node)
        question_options = [
            {
                "id": "keep",
                "label": f"Keep {node}",
                "description": "Use the raw SPICE node name.",
            }
        ]
        mapping: dict[str, str | None] = {"keep": None}
        for index, net_name in enumerate(missing):
            option_id = f"net_{index}"
            question_options.append({"id": option_id, "label": net_name})
            mapping[option_id] = net_name
        questions.append(
            {
                "id": qid,
                "type": "choice",
                "prompt": f"What is SPICE node {node}?",
                "help": "The overlay has net rules for: " + ", ".join(missing),
                "options": question_options,
            }
        )
        options[qid] = mapping
        nodes[qid] = node

    return _AliasQuestions(
        payload={
            "title": "Name the SPICE nodes",
            "intro": (
                "Map auto-generated SPICE nodes to the net names used by your "
                "design rules, or keep the raw names."
            ),
            "submit_label": "Use these net names",
            "questions": questions,
        },
        options=options,
        nodes=nodes,
        total_auto_nodes=len(auto_nodes),
    )


def _apply_alias_answers(
    questions: _AliasQuestions, answers: dict[str, Any]
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for qid, node in questions.nodes.items():
        value = _answer_value(answers.get(qid))
        if value is None or value == "keep":
            continue
        net_name = questions.options.get(qid, {}).get(str(value))
        if net_name:
            aliases[node] = net_name
    return aliases


def _missing_footprints(spec: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for comp in spec.get("components", []):
        if not isinstance(comp, dict) or not comp.get("ref"):
            continue
        if not str(comp.get("footprint") or "").strip():
            missing.append(str(comp["ref"]))
    return missing


def _missing_placement(spec: dict[str, Any]) -> list[str]:
    if spec.get("placement", {}).get("mode") != "fixed":
        return []
    missing: list[str] = []
    for comp in spec.get("components", []):
        if not isinstance(comp, dict) or not comp.get("ref"):
            continue
        at = comp.get("at")
        if not isinstance(at, dict) or "x_mm" not in at or "y_mm" not in at:
            missing.append(str(comp["ref"]))
    return missing


def _footprint_placement_payload(
    spec: dict[str, Any],
    prefixes: dict[str, str | None],
    missing_placement: list[str],
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    missing_refs = _missing_footprints(spec)
    categories: dict[str, str | None] = {}
    for comp in spec.get("components", []):
        if not isinstance(comp, dict) or not comp.get("ref"):
            continue
        ref = str(comp["ref"])
        categories[ref] = _category_for(
            prefixes.get(ref), str(comp.get("value") or "")
        )

    questions: list[dict[str, Any]] = []
    qid_to_ref: dict[str, str] = {}
    if missing_refs:
        try:
            footprint_payload = visual_ask.build_spec_payload(
                spec,
                only_missing=True,
                categories=categories,
                title="Choose footprints for the SPICE import",
                intro=(
                    "SPICE netlists do not carry footprints. Pick a package for "
                    "each imported part."
                ),
            )
        except visual_ask.PayloadError:
            footprint_payload = None
        if footprint_payload:
            questions.extend(footprint_payload["questions"])
            for ref in missing_refs:
                qid = _footprint_qid(ref)
                if qid in qid_to_ref:
                    raise SpiceInputError(
                        "two component references map to the same question id: "
                        f"{ref} and {qid_to_ref[qid]}"
                    )
                qid_to_ref[qid] = ref

    if missing_placement:
        questions.append(
            {
                "id": "placement_mode",
                "type": "choice",
                "prompt": "How should imported parts without coordinates be placed?",
                "help": (
                    "Auto placement is a deterministic shelf pack. Choose fixed "
                    "only if you will add coordinates yourself."
                ),
                "options": [
                    {
                        "id": "auto",
                        "label": "Use automatic placement (recommended)",
                    },
                    {
                        "id": "fixed",
                        "label": "Keep fixed placement; I will add coordinates",
                    },
                ],
            }
        )

    if not questions:
        return None, {}
    return (
        {
            "title": "Finish the SPICE import",
            "intro": "A few details are needed before the board can be built.",
            "submit_label": "Continue",
            "questions": questions,
        },
        qid_to_ref,
    )


def _apply_footprint_answers(
    spec: dict[str, Any],
    qid_to_ref: dict[str, str],
    answers: dict[str, Any],
) -> None:
    by_ref = {
        str(comp.get("ref")): comp
        for comp in spec.get("components", [])
        if isinstance(comp, dict) and comp.get("ref")
    }
    for qid, ref in qid_to_ref.items():
        answer = answers.get(qid)
        if answer is None:
            continue
        value = _answer_value(answer)
        footprint = ""
        if isinstance(answer, dict):
            footprint = str(answer.get("footprint") or value or "").strip()
        elif isinstance(value, str):
            footprint = value.strip()
        if footprint:
            by_ref[ref]["footprint"] = footprint


def _apply_placement_answer(
    spec: dict[str, Any], answers: dict[str, Any], warnings: list[str]
) -> None:
    value = _answer_value(answers.get("placement_mode"))
    if value == "auto":
        spec.setdefault("placement", {})["mode"] = "auto"
    elif value == "fixed":
        warnings.append(
            "placement.mode stays fixed; add `at` coordinates for imported "
            "parts or choose automatic placement"
        )


def _visual_asker(
    payload: dict[str, Any],
    out_path: Path,
    *,
    host: str,
    port: int,
    timeout: float,
    open_browser: bool,
    fetch: bool,
    footprints_dir: Path | None,
) -> dict[str, Any]:
    try:
        normalized = visual_ask.normalize_payload(
            payload,
            enrich=True,
            fetch=fetch,
            footprints_dir=footprints_dir,
        )
    except visual_ask.PayloadError as exc:
        raise SpiceInputError(f"invalid visual question payload: {exc}") from exc
    try:
        server = visual_ask.make_server(
            normalized,
            out_path=out_path,
            host=host,
            port=port,
            status_path=out_path.with_suffix(".status.json"),
        )
    except OSError as exc:
        raise SpiceInputError(f"could not start the visual question server: {exc}") from exc
    code = visual_ask.run_server(
        server,
        timeout=timeout,
        url_file=out_path.with_suffix(".url"),
        open_browser=open_browser,
    )
    if code != 0:
        raise SpiceInputError(
            f"no visual answer received within {int(timeout)}s; "
            f"the question page is still listed in {out_path.with_suffix('.url')}"
        )
    try:
        document = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpiceInputError(f"could not read visual answers: {exc}") from exc
    answers = document.get("answers")
    if not isinstance(answers, dict):
        raise SpiceInputError("visual answer file has no answers object")
    return answers


def _ask(
    payload: dict[str, Any] | None,
    *,
    ask_dir: Path,
    stem: str,
    asker: AskFn | None,
    host: str,
    port: int,
    timeout: float,
    open_browser: bool,
    fetch: bool,
    footprints_dir: Path | None,
) -> dict[str, Any]:
    if payload is None:
        return {}
    out_path = ask_dir / f"{stem}.answers.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if asker is not None:
        return asker(payload, out_path)
    return _visual_asker(
        payload,
        out_path,
        host=host,
        port=port,
        timeout=timeout,
        open_browser=open_browser,
        fetch=fetch,
        footprints_dir=footprints_dir,
    )


def _missing_pin_map_message(unresolved: list[dict[str, Any]]) -> str:
    lines = ["the SPICE netlist needs pin maps for multi-pin parts:"]
    for item in unresolved:
        prefix = str(item.get("prefix"))
        if prefix == "X":
            detail = "subcircuit"
        else:
            detail = f"{len(item.get('nodes') or [])}-node {prefix}"
        lines.append(f"  - {item.get('ref')} ({detail})")
    lines.append(
        "Run without --no-ask to answer the visual questions, or add entries "
        "to --pin-map."
    )
    return "\n".join(lines)


def _missing_post_merge_message(
    missing_footprints: list[str], missing_placement: list[str]
) -> str:
    lines = ["the SPICE import still needs:"]
    for ref in missing_footprints:
        lines.append(f"  - footprint for {ref}")
    for ref in missing_placement:
        lines.append(f"  - placement for {ref} (placement.mode is fixed)")
    lines.append(
        "Run without --no-ask to answer the visual questions, or add these to "
        "the overlay spec."
    )
    return "\n".join(lines)


def resolve_ltspice(
    overlay: dict[str, Any],
    ltspice_path: str | Path,
    *,
    pin_map: dict[str, Any] | None = None,
    node_aliases: dict[str, str] | None = None,
    ask: bool = True,
    asker: AskFn | None = None,
    ask_dir: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    timeout: float = 1800,
    open_browser: bool = False,
    fetch: bool = True,
    footprints_dir: Path | None = None,
) -> SpiceImport:
    """Resolve an LTspice netlist, asking the user for missing physical data.

    ``asker`` is injectable for tests. It receives ``(payload, out_path)`` and
    returns the normalized answer mapping.
    """
    base_pin_map = copy.deepcopy(pin_map or {})
    aliases = {str(key): str(value) for key, value in (node_aliases or {}).items()}
    warnings: list[str] = []
    asked: list[str] = []
    ask_dir = ask_dir or Path("build") / "spice-ask"
    ask_kwargs = {
        "ask_dir": ask_dir,
        "asker": asker,
        "host": host,
        "port": port,
        "timeout": timeout,
        "open_browser": open_browser,
        "fetch": fetch,
        "footprints_dir": footprints_dir,
    }

    netlist = ds.parse_ltspice_netlist(
        ltspice_path,
        pin_map=base_pin_map,
        node_aliases=aliases,
        strict=False,
    )
    warnings.extend(netlist.get("warnings", []))
    unresolved = list(netlist.get("unresolved", []))

    if unresolved:
        if not ask:
            raise SpiceInputError(_missing_pin_map_message(unresolved))
        payload, option_maps = _pinmap_payload(unresolved)
        answers = _ask(payload, stem="spice-pinmap", **ask_kwargs)
        refs = _apply_pinmap_answers(unresolved, answers, option_maps)
        base_pin_map.setdefault("refs", {}).update(refs)
        asked.append("pin maps")
        netlist = ds.parse_ltspice_netlist(
            ltspice_path,
            pin_map=base_pin_map,
            node_aliases=aliases,
            strict=True,
        )
        warnings.extend(netlist.get("warnings", []))
        if netlist.get("unresolved"):
            raise SpiceInputError(
                "pin maps were supplied but some parts are still unresolved: "
                + ", ".join(item["ref"] for item in netlist["unresolved"])
            )

    alias_questions = _alias_questions(overlay, netlist, aliases)
    if alias_questions is not None:
        if ask:
            answers = _ask(alias_questions.payload, stem="spice-nets", **ask_kwargs)
            new_aliases = _apply_alias_answers(alias_questions, answers)
            asked.append("node aliases")
            if new_aliases:
                aliases.update(new_aliases)
                netlist = ds.parse_ltspice_netlist(
                    ltspice_path,
                    pin_map=base_pin_map,
                    node_aliases=aliases,
                    strict=True,
                )
                warnings.extend(netlist.get("warnings", []))
            if alias_questions.total_auto_nodes > len(alias_questions.nodes):
                warnings.append(
                    "more than "
                    f"{len(alias_questions.nodes)} auto-generated SPICE nodes remain; "
                    "use --node-aliases for the rest"
                )
        else:
            warnings.append(
                "auto-generated SPICE nodes remain ("
                + ", ".join(sorted(alias_questions.nodes.values()))
                + "); pass --node-aliases to apply netclasses to them"
            )

    try:
        spec = ds.merge_netlist(overlay, netlist)
    except ValueError as exc:
        raise SpiceInputError(str(exc)) from exc
    spec["netlist_source"] = str(ltspice_path)

    prefixes = {
        str(comp.get("ref")): comp.get("spice_prefix")
        for comp in netlist.get("components", [])
        if isinstance(comp, dict) and comp.get("ref")
    }
    missing_footprints = _missing_footprints(spec)
    missing_placement = _missing_placement(spec)
    if missing_footprints or missing_placement:
        if not ask:
            raise SpiceInputError(
                _missing_post_merge_message(missing_footprints, missing_placement)
            )
        payload, qid_to_ref = _footprint_placement_payload(
            spec, prefixes, missing_placement
        )
        if payload is not None:
            answers = _ask(payload, stem="spice-footprints", **ask_kwargs)
            _apply_footprint_answers(spec, qid_to_ref, answers)
            _apply_placement_answer(spec, answers, warnings)
            if missing_footprints:
                asked.append("footprints")
            if missing_placement:
                asked.append("placement")
        remaining = _missing_footprints(spec)
        if remaining:
            raise SpiceInputError(
                "footprints are still missing for: " + ", ".join(remaining)
            )
        if _missing_placement(spec):
            warnings.append(
                "placement.mode is fixed and some imported parts have no "
                "coordinates; add `at` or use placement.mode=auto"
            )

    return SpiceImport(
        spec=spec,
        pin_map=base_pin_map,
        node_aliases=aliases,
        warnings=warnings,
        asked=asked,
    )


def missing_inputs(
    overlay: dict[str, Any],
    ltspice_path: str | Path,
    *,
    pin_map: dict[str, Any] | None = None,
    node_aliases: dict[str, str] | None = None,
) -> list[str]:
    """Return a human-readable list of data the SPICE import still needs.

    This never asks a question and never opens a server. It is used by the CI
    dispatch path, where interactive questions are not possible.
    """
    base_pin_map = copy.deepcopy(pin_map or {})
    netlist = ds.parse_ltspice_netlist(
        ltspice_path,
        pin_map=base_pin_map,
        node_aliases=node_aliases,
        strict=False,
    )
    items: list[str] = []
    for item in netlist.get("unresolved", []):
        prefix = str(item.get("prefix"))
        detail = "subcircuit" if prefix == "X" else f"{len(item.get('nodes') or [])}-node {prefix}"
        items.append(f"pin map for {item.get('ref')} ({detail})")
    if items:
        return items

    spec = ds.merge_netlist(overlay, netlist)
    for ref in _missing_footprints(spec):
        items.append(f"footprint for {ref}")
    for ref in _missing_placement(spec):
        items.append(f"placement for {ref} (placement.mode is fixed)")
    return items
