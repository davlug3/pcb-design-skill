#!/usr/bin/env python3
"""Ask visual questions from a phone or laptop, then hand the answers back.

This is a small, dependency-free HTTP server. The agent writes a question
payload (footprint choices, single/multi choice, text, confirm), starts the
server, and sends the printed URL to the user. The user picks answers in a
mobile-friendly page; the server validates them and writes a JSON answer file.

Examples:
    # One footprint decision, using the built-in common-package catalog.
    python3 scripts/visual_ask.py ask --footprint-for R1 --category resistor \\
        --out build/ask-R1.json --mobile

    # A full payload with several questions.
    python3 scripts/visual_ask.py ask examples/visual-ask-led.json \\
        --out build/answers.json --mobile

    # Return immediately (agent-friendly); read the URL and status files.
    python3 scripts/visual_ask.py ask examples/visual-ask-led.json \\
        --out build/answers.json --detach

The server binds to 127.0.0.1 by default. Use ``--mobile`` to bind to the LAN
and print a phone-accessible URL. It is a plain local/LAN service: there is no
account or token gate, so only run it on a network you trust.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import footprint_preview  # noqa: E402

WEB_DIR = ROOT / "web" / "visual-ask"
SCHEMA_PATH = ROOT / "schemas" / "visual-ask.schema.json"

QUESTION_TYPES = {"footprint", "choice", "multi", "text", "confirm"}
ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
OPTION_ID_RE = re.compile(r'^[^\x00-\x1f"<>]{1,300}$')
SAFE_SVG_RE = re.compile(r"<script\b.*?</script>", re.IGNORECASE | re.DOTALL)
SAFE_EVENT_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
MAX_TEXT = 5000
MAX_BODY = 1_000_000
STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}


class PayloadError(ValueError):
    """The question payload is malformed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_string(obj: dict[str, Any], key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PayloadError(f"{where}: {key!r} must be a non-empty string")
    return value.strip()


def _optional_string(obj: dict[str, Any], key: str, where: str, limit: int = 2000) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PayloadError(f"{where}: {key!r} must be a string")
    if len(value) > limit:
        raise PayloadError(f"{where}: {key!r} is too long")
    return value


def _clean_svg(svg: Any) -> str | None:
    if not isinstance(svg, str):
        return None
    text = svg.strip()
    if not text.startswith("<svg"):
        return None
    text = SAFE_SVG_RE.sub("", text)
    text = SAFE_EVENT_RE.sub(" data-removed=", text)
    return text


def _clean_image(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith("data:image/") or value.startswith("https://") or value.startswith("http://"):
        return value
    return None


def _normalize_option(raw: Any, qtype: str, where: str, seen: set[str]) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = {"footprint": raw} if qtype == "footprint" else {"id": raw, "label": raw}
    if not isinstance(raw, dict):
        raise PayloadError(f"{where}: option must be an object or string")
    if qtype == "footprint":
        footprint = raw.get("footprint") or raw.get("id")
        if not isinstance(footprint, str) or not footprint.strip():
            raise PayloadError(f"{where}: footprint option needs 'footprint' or 'id'")
        footprint = footprint.strip()
        option_id = str(raw.get("id") or footprint)
        label = str(raw.get("label") or footprint.split(":")[-1].replace("_", " "))
        description = str(raw.get("description") or "")
    else:
        option_id = _require_string(raw, "id", where)
        label = str(raw.get("label") or option_id)
        description = str(raw.get("description") or "")
        footprint = None
    if not OPTION_ID_RE.match(option_id):
        raise PayloadError(f"{where}: invalid option id {option_id!r}")
    if option_id in seen:
        raise PayloadError(f"{where}: duplicate option id {option_id!r}")
    seen.add(option_id)
    option: dict[str, Any] = {
        "id": option_id,
        "label": label.strip() or option_id,
        "description": description.strip(),
    }
    if footprint:
        option["footprint"] = footprint
    swatch = _optional_string(raw, "swatch", where, 60)
    if swatch:
        option["swatch"] = swatch
    image = _clean_image(raw.get("image"))
    if image:
        option["image"] = image
    svg = _clean_svg(raw.get("preview_svg"))
    if svg:
        option["preview_svg"] = svg
    if isinstance(raw.get("meta"), dict):
        option["meta"] = raw["meta"]
    if raw.get("error"):
        option["error"] = str(raw["error"])[:300]
    return option


def normalize_payload(
    payload: Any,
    *,
    enrich: bool = True,
    fetch: bool = True,
    footprints_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate a payload and optionally add real footprint previews."""
    if not isinstance(payload, dict):
        raise PayloadError("payload must be a JSON object")
    title = _require_string(payload, "title", "payload")
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise PayloadError("payload.questions must be a non-empty list")
    normalized: dict[str, Any] = {
        "title": title,
        "intro": str(payload.get("intro") or "").strip(),
        "submit_label": str(payload.get("submit_label") or "Send answers").strip(),
        "questions": [],
    }
    ids: set[str] = set()
    for index, raw_question in enumerate(questions):
        where = f"questions[{index}]"
        if not isinstance(raw_question, dict):
            raise PayloadError(f"{where}: question must be an object")
        question_id = _require_string(raw_question, "id", where)
        if not ID_RE.match(question_id):
            raise PayloadError(f"{where}: invalid question id {question_id!r}")
        if question_id in ids:
            raise PayloadError(f"{where}: duplicate question id {question_id!r}")
        ids.add(question_id)
        qtype = _require_string(raw_question, "type", where)
        if qtype not in QUESTION_TYPES:
            raise PayloadError(f"{where}: type must be one of {', '.join(sorted(QUESTION_TYPES))}")
        prompt = _require_string(raw_question, "prompt", where)
        required = raw_question.get("required", qtype != "text")
        if not isinstance(required, bool):
            raise PayloadError(f"{where}: 'required' must be a boolean")
        question: dict[str, Any] = {
            "id": question_id,
            "type": qtype,
            "prompt": prompt,
            "required": required,
        }
        for key in ("help", "placeholder", "custom_label", "custom_placeholder"):
            value = _optional_string(raw_question, key, where)
            if value is not None:
                question[key] = value
        if qtype in ("footprint", "choice", "multi"):
            options = raw_question.get("options")
            if not isinstance(options, list) or not options:
                raise PayloadError(f"{where}: {qtype} question needs a non-empty options list")
            seen: set[str] = set()
            question["options"] = [
                _normalize_option(option, qtype, f"{where}.options[{i}]", seen)
                for i, option in enumerate(options)
            ]
        if qtype == "footprint":
            allow_custom = raw_question.get("allow_custom", False)
            if not isinstance(allow_custom, bool):
                raise PayloadError(f"{where}: 'allow_custom' must be a boolean")
            question["allow_custom"] = allow_custom
            question.setdefault("custom_label", "Or enter a KiCad footprint ID")
            question.setdefault("custom_placeholder", "Library:Footprint")
        if qtype == "multi":
            min_selections = raw_question.get("min_selections", 0)
            if not isinstance(min_selections, int) or min_selections < 0:
                raise PayloadError(f"{where}: 'min_selections' must be a non-negative integer")
            question["min_selections"] = min_selections
        normalized["questions"].append(question)

    if enrich:
        for question in normalized["questions"]:
            if question["type"] != "footprint":
                continue
            for option in question["options"]:
                if option.get("preview_svg"):
                    continue
                result = footprint_preview.preview(
                    option["footprint"],
                    footprints_dir=footprints_dir,
                    fetch=fetch,
                )
                option["preview_svg"] = result["svg"]
                meta = option.setdefault("meta", {})
                if isinstance(meta, dict):
                    meta.update(
                        {
                            "pads": result["pads"],
                            "through_hole": result["through_hole"],
                            "size_mm": result["size_mm"],
                            "source": result["source"],
                            "available": result["available"],
                        }
                    )
                if not option.get("description") and result.get("description"):
                    option["description"] = result["description"][:240]
                if result.get("error"):
                    option["error"] = result["error"]
    return normalized


def _option_map(question: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {option["id"]: option for option in question.get("options", [])}


def validate_answers(
    payload: dict[str, Any], answers: Any
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Validate answers and return (normalized, errors).

    Accepts either raw values (``{"R1": "opt"}``) or UI envelopes
    (``{"R1": {"value": "opt"}}``).
    """
    if not isinstance(answers, dict):
        return {}, [{"question": "", "message": "answers must be a JSON object"}]
    normalized: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for question in payload["questions"]:
        qid = question["id"]
        qtype = question["type"]
        raw = answers.get(qid)
        meta: dict[str, Any] = {}
        if isinstance(raw, dict) and "value" in raw:
            value = raw.get("value")
            meta = raw
        else:
            value = raw
        if qtype == "text":
            text = value.strip() if isinstance(value, str) else ""
            if len(text) > MAX_TEXT:
                errors.append({"question": qid, "message": f"Answer is longer than {MAX_TEXT} characters."})
                continue
            if question["required"] and not text:
                errors.append({"question": qid, "message": "This answer is required."})
                continue
            normalized[qid] = {"type": "text", "value": text}
        elif qtype == "confirm":
            if not isinstance(value, bool):
                if question["required"]:
                    errors.append({"question": qid, "message": "Choose yes or no."})
                    continue
                normalized[qid] = {"type": "confirm", "value": None}
            else:
                normalized[qid] = {"type": "confirm", "value": value}
        elif qtype == "multi":
            values = value if isinstance(value, list) else []
            values = list(dict.fromkeys(item for item in values if isinstance(item, str)))
            options = _option_map(question)
            unknown = [item for item in values if item not in options]
            if unknown:
                errors.append({"question": qid, "message": f"Unknown selection: {', '.join(unknown)}"})
                continue
            minimum = max(1, question.get("min_selections", 0)) if question["required"] else 0
            if len(values) < minimum:
                message = (
                    f"Select at least {minimum} options."
                    if minimum > 1
                    else "Select at least one option."
                )
                errors.append({"question": qid, "message": message})
                continue
            normalized[qid] = {
                "type": "multi",
                "value": values,
                "labels": [options[item]["label"] for item in values],
            }
        else:  # footprint or choice
            custom = meta.get("custom") if isinstance(meta, dict) else None
            custom = custom.strip() if isinstance(custom, str) else ""
            options = _option_map(question)
            if qtype == "footprint" and question.get("allow_custom") and custom:
                if ":" not in custom and not custom.endswith(".kicad_mod"):
                    errors.append(
                        {
                            "question": qid,
                            "message": "Use a KiCad footprint ID such as Library:Footprint, or a .kicad_mod path.",
                        }
                    )
                    continue
                normalized[qid] = {
                    "type": "footprint",
                    "value": custom,
                    "label": custom,
                    "footprint": custom,
                    "custom": True,
                }
                continue
            if not isinstance(value, str) or not value:
                if question["required"]:
                    errors.append({"question": qid, "message": "Choose an option."})
                else:
                    normalized[qid] = {"type": qtype, "value": None, "label": None}
                continue
            option = options.get(value)
            if option is None:
                errors.append({"question": qid, "message": f"Unknown option: {value}"})
                continue
            normalized[qid] = {
                "type": qtype,
                "value": value,
                "label": option["label"],
            }
            if qtype == "footprint":
                normalized[qid]["footprint"] = option.get("footprint", value)
                normalized[qid]["custom"] = False
    return normalized, errors


def build_catalog_payload(
    ref: str,
    category: str,
    *,
    options: list[str] | None = None,
    prompt: str | None = None,
    title: str | None = None,
    intro: str | None = None,
) -> dict[str, Any]:
    items = footprint_preview.COMMON_FOOTPRINTS.get(category)
    if items is None:
        raise PayloadError(
            f"unknown category {category!r}; choose from {', '.join(sorted(footprint_preview.COMMON_FOOTPRINTS))}"
        )
    choices = [{"id": item_id, "footprint": item_id, "label": label} for item_id, label in items]
    seen = {choice["id"] for choice in choices}
    for extra in options or []:
        extra = extra.strip()
        if not extra or extra in seen:
            continue
        seen.add(extra)
        choices.append(
            {
                "id": extra,
                "footprint": extra,
                "label": extra.split(":")[-1].replace("_", " "),
            }
        )
    qid = "footprint_" + re.sub(r"[^A-Za-z0-9_.-]+", "_", ref).strip("_")
    return {
        "title": title or f"Choose a footprint for {ref}",
        "intro": intro
        or "Pick the physical package you want on the board. Previews use the real KiCad footprint geometry when it is available.",
        "questions": [
            {
                "id": qid or "footprint",
                "type": "footprint",
                "prompt": prompt or f"Which footprint should {ref} use?",
                "help": "Tap a package to select it. If your exact part is not listed, enter its KiCad footprint ID.",
                "allow_custom": True,
                "options": choices,
            }
        ],
    }


def infer_category(footprint: str | None) -> str | None:
    """Map a KiCad library name to a built-in catalog category."""
    if not footprint or ":" not in footprint:
        return None
    library = footprint.split(":", 1)[0]
    prefixes = (
        ("Resistor_", "resistor"),
        ("Capacitor_", "capacitor"),
        ("LED_", "led"),
        ("Connector_PinHeader_", "header"),
        ("Package_TO_SOT_", "sot"),
        ("Package_SO", "soic"),
        ("Package_QFP", "qfp"),
        ("Package_DFN_QFN", "qfn"),
        ("Package_DIP", "dip"),
    )
    for prefix, category in prefixes:
        if library.startswith(prefix):
            return category
    return None


def build_spec_payload(
    spec: dict[str, Any],
    *,
    only_missing: bool = False,
    extra_options: list[str] | None = None,
    categories: dict[str, str | None] | None = None,
    title: str | None = None,
    intro: str | None = None,
) -> dict[str, Any]:
    """Build one footprint question per component in a design spec.

    The current footprint is offered first, followed by the built-in catalog for
    the inferred package family. Components whose family is unknown get a text
    question instead, so the user can type an exact ``Library:Footprint``.

    ``categories`` overrides the inferred family by reference. This is useful
    for a SPICE import, where a ref such as ``Q1`` has no footprint yet but the
    SPICE prefix still tells us it is a transistor.
    """
    components = spec.get("components")
    if not isinstance(components, list) or not components:
        raise PayloadError("spec has no components")
    name = str(spec.get("name") or spec.get("title") or "board")
    questions: list[dict[str, Any]] = []
    extras = [item.strip() for item in (extra_options or []) if item.strip()]
    for component in components:
        if not isinstance(component, dict):
            continue
        ref = str(component.get("ref") or "").strip()
        if not ref:
            continue
        current = str(component.get("footprint") or "").strip()
        if only_missing and current:
            continue
        value = str(component.get("value") or "").strip()
        if categories is not None and ref in categories:
            category = categories[ref]
        else:
            category = infer_category(current)
        catalog = list(footprint_preview.COMMON_FOOTPRINTS.get(category, [])) if category else []
        choices: list[dict[str, Any]] = []
        seen: set[str] = set()
        if current:
            label = next((label for item_id, label in catalog if item_id == current), None)
            choices.append(
                {
                    "id": current,
                    "footprint": current,
                    "label": label or f"Current · {current.split(':')[-1].replace('_', ' ')}",
                    "description": "Current choice in the design spec.",
                }
            )
            seen.add(current)
        for item_id, label in catalog:
            if item_id in seen:
                continue
            choices.append({"id": item_id, "footprint": item_id, "label": label})
            seen.add(item_id)
        for item_id in extras:
            if item_id in seen:
                continue
            choices.append(
                {
                    "id": item_id,
                    "footprint": item_id,
                    "label": item_id.split(":")[-1].replace("_", " "),
                }
            )
            seen.add(item_id)
        question_id = "footprint_" + re.sub(r"[^A-Za-z0-9_.-]+", "_", ref).strip("_")
        label = f"{ref} ({value})" if value else ref
        if choices:
            questions.append(
                {
                    "id": question_id or "footprint",
                    "type": "footprint",
                    "prompt": f"Which footprint should {label} use?",
                    "help": (
                        "Tap a package to select it. The current spec choice is first."
                        if current
                        else "Tap a package to select it. If your exact part is not listed, enter its KiCad footprint ID."
                    ),
                    "allow_custom": True,
                    "options": choices,
                }
            )
        else:
            questions.append(
                {
                    "id": question_id or "footprint",
                    "type": "text",
                    "prompt": f"Which KiCad footprint should {label} use?",
                    "help": "No package family could be inferred. Enter the exact Library:Footprint ID.",
                    "placeholder": "Library:Footprint",
                    "required": True,
                }
            )
    if not questions:
        raise PayloadError("no components left to ask about")
    return {
        "title": title or f"Choose footprints for {name}",
        "intro": intro
        or (
            "Pick a package for each component that still needs a footprint."
            if only_missing
            else "The current spec choice is first. Pick a different package only where you want to change the physical part."
        ),
        "submit_label": "Send footprints",
        "questions": questions,
    }


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    temp.write_text(value, encoding="utf-8")
    temp.replace(path)


def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        return ip if ip and not ip.startswith("127.") else None
    except OSError:
        return None


def server_urls(host: str, port: int) -> list[str]:
    hosts: list[str] = []
    if host in ("0.0.0.0", "::", ""):
        hosts.append("127.0.0.1")
        lan = _lan_ip()
        if lan and lan not in hosts:
            hosts.append(lan)
    else:
        hosts.append(host)
    return [f"http://{item}:{port}/" for item in hosts]


class VisualAskServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        payload: dict[str, Any],
        out_path: Path,
        status_path: Path | None,
        session_id: str,
        verbose: bool = False,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.payload = payload
        self.out_path = out_path
        self.status_path = status_path
        self.session_id = session_id
        self.verbose = verbose
        self.answered = threading.Event()
        self.answer_doc: dict[str, Any] | None = None
        self.started_at = time.time()


class VisualAskHandler(BaseHTTPRequestHandler):
    server: VisualAskServer
    server_version = "pcb-visual-ask/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https: http:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, value: Any, status: int = 200) -> None:
        self._send_bytes(
            json.dumps(value).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _send_error_json(self, status: int, message: str, errors: Any = None) -> None:
        body: dict[str, Any] = {"ok": False, "error": message}
        if errors:
            body["errors"] = errors
        self._send_json(body, status)

    def _send_static(self, name: str) -> None:
        content_type = STATIC_FILES.get(name)
        if content_type is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        path = WEB_DIR / name
        if not path.is_file():
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"missing UI asset: {name}")
            return
        self._send_bytes(path.read_bytes(), content_type)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_json({"ok": True, "session": self.server.session_id, "state": "waiting"})
            return
        if path == "/":
            self._send_static("index.html")
        elif path in ("/styles.css", "/app.js", "/favicon.svg"):
            self._send_static(path.lstrip("/"))
        elif path == "/api/payload":
            self._send_json(
                {
                    "session": self.server.session_id,
                    "title": self.server.payload["title"],
                    "intro": self.server.payload.get("intro", ""),
                    "submit_label": self.server.payload.get("submit_label", "Send answers"),
                    "questions": self.server.payload["questions"],
                }
            )
        else:
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlparse(self.path).path != "/api/answer":
            self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid request size")
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "body must be valid JSON")
            return
        if not isinstance(body, dict):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
            return
        normalized, errors = validate_answers(self.server.payload, body.get("answers"))
        if errors:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "please fix the answers", errors)
            return
        document = {
            "session": self.server.session_id,
            "title": self.server.payload["title"],
            "submitted_at": _now(),
            "answers": normalized,
        }
        try:
            _atomic_write_json(self.server.out_path, document)
        except OSError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"could not save answers: {exc}")
            return
        self.server.answer_doc = document
        self.server.answered.set()
        self._send_json({"ok": True, "session": self.server.session_id})
        threading.Thread(target=self._shutdown_soon, daemon=True).start()

    def _shutdown_soon(self) -> None:
        time.sleep(0.25)
        self.server.shutdown()


def make_server(
    payload: dict[str, Any],
    *,
    out_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    status_path: Path | None = None,
    session_id: str | None = None,
    verbose: bool = False,
    attempts: int = 20,
) -> VisualAskServer:
    """Create a bound server without serving. Useful for tests and embedding."""
    session_id = session_id or secrets.token_hex(8)
    last_error: OSError | None = None
    for offset in range(attempts):
        try:
            return VisualAskServer(
                (host, port + offset),
                VisualAskHandler,
                payload=payload,
                out_path=Path(out_path),
                status_path=Path(status_path) if status_path else None,
                session_id=session_id,
                verbose=verbose,
            )
        except OSError as exc:
            last_error = exc
            if port == 0:
                break
    raise last_error or OSError("could not bind the visual ask server")


def _write_status(server: VisualAskServer, state: str, **extra: Any) -> None:
    if not server.status_path:
        return
    document = {
        "session": server.session_id,
        "state": state,
        "pid": os.getpid(),
        "updated_at": _now(),
        "answer_file": str(server.out_path),
        "urls": server_urls(str(server.server_address[0]), server.server_address[1]),
    }
    document.update(extra)
    try:
        _atomic_write_json(server.status_path, document)
    except OSError:
        pass


def _open_url(url: str) -> bool:
    opener = shutil.which("termux-open-url")
    if opener:
        try:
            subprocess.Popen(
                [opener, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            pass
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def run_server(
    server: VisualAskServer,
    *,
    timeout: float = 1800,
    url_file: Path | None = None,
    open_browser: bool = False,
    print_json: bool = False,
) -> int:
    host, port = str(server.server_address[0]), server.server_address[1]
    urls = server_urls(host, port)
    if url_file:
        _atomic_write_text(url_file, "\n".join(urls) + "\n")
    _write_status(server, "waiting")
    print(f"Visual questions: {server.payload['title']}")
    for url in urls:
        print(f"  {url}")
    print(f"Waiting for an answer (timeout {int(timeout)}s)...", flush=True)
    if open_browser and urls:
        _open_url(urls[0])
    timer = threading.Timer(timeout, server.shutdown)
    timer.daemon = True
    timer.start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        timer.cancel()
        server.server_close()
    if server.answered.is_set() and server.answer_doc is not None:
        _write_status(server, "answered", submitted_at=server.answer_doc["submitted_at"])
        print(f"Answer saved to {server.out_path}")
        if print_json:
            print(json.dumps(server.answer_doc, indent=2))
        return 0
    _write_status(server, "timeout")
    print(f"No answer received; nothing written to {server.out_path}")
    return 2


def _detach(
    *,
    payload: dict[str, Any],
    out_path: Path,
    host: str,
    port: int,
    timeout: float,
    url_file: Path,
    status_file: Path,
    log_file: Path,
    open_browser: bool,
    verbose: bool,
) -> int:
    payload_file = out_path.with_name(out_path.stem + ".payload.json")
    _atomic_write_json(payload_file, payload)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "ask",
        "--payload-file",
        str(payload_file),
        "--out",
        str(out_path),
        "--host",
        host,
        "--port",
        str(port),
        "--timeout",
        str(timeout),
        "--url-file",
        str(url_file),
        "--status-file",
        str(status_file),
        "--log-file",
        str(log_file),
        "--_child",
    ]
    if verbose:
        command.append("--verbose")
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    elif os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    with log_file.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, **kwargs)
    deadline = time.time() + 12
    while time.time() < deadline:
        if url_file.is_file() and url_file.stat().st_size > 0:
            urls = [line.strip() for line in url_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            print(f"Visual questions: {payload['title']}")
            for url in urls:
                print(f"  {url}")
            print(f"Answer file: {out_path}")
            print(f"Status file: {status_file}")
            if open_browser and urls:
                _open_url(urls[0])
            return 0
        if process.poll() is not None:
            break
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
    print(f"visual ask server did not start; see {log_file}", file=sys.stderr)
    try:
        tail = log_file.read_text(encoding="utf-8")[-1200:]
        if tail:
            print(tail, file=sys.stderr)
    except OSError:
        pass
    return 1


def _load_payload_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PayloadError(f"could not read payload: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PayloadError(f"payload is not valid JSON: {exc}") from exc


def _ask_parser(subparsers: Any) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("ask", help="serve a visual question payload")
    parser.add_argument("payload", nargs="?", help="JSON payload file")
    parser.add_argument("--from-spec", metavar="SPEC", help="ask for footprints for every component in a design spec")
    parser.add_argument("--only-missing", action="store_true", help="with --from-spec, only ask about components with no footprint")
    parser.add_argument("--footprint-for", metavar="REF", help="build a one-question footprint payload for REF")
    parser.add_argument(
        "--category",
        help=(
            "common footprint category (resistor, capacitor, led, header, sot, "
            "soic, qfp, qfn, dip, inductor, diode, ic)"
        ),
    )
    parser.add_argument("--option", action="append", default=[], metavar="FOOTPRINT", help="extra footprint candidate (repeatable)")
    parser.add_argument("--title", help="override the page title")
    parser.add_argument("--intro", help="override the page introduction")
    parser.add_argument("--prompt", help="override the question prompt")
    parser.add_argument("--out", default="build/visual-answers.json", help="answer JSON path")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--mobile", action="store_true", help="bind to 0.0.0.0 and print a LAN URL for a phone")
    parser.add_argument("--port", type=int, default=8765, help="port (0 picks a free port)")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds to wait for an answer")
    parser.add_argument("--url-file", help="write the open URLs here")
    parser.add_argument("--status-file", help="write waiting/answered/timeout status here")
    parser.add_argument("--log-file", help="detached child log path")
    parser.add_argument("--open", action="store_true", dest="open_browser", help="open the page locally")
    parser.add_argument("--detach", action="store_true", help="start in the background and return immediately")
    parser.add_argument("--no-fetch", action="store_true", help="do not fetch footprints from the KiCad GitHub library")
    parser.add_argument("--footprints-dir", help="KiCad footprints root (contains *.pretty)")
    parser.add_argument("--json", action="store_true", dest="print_json", help="print the answer JSON when done")
    parser.add_argument("--verbose", action="store_true", help="log HTTP requests")
    parser.add_argument("--payload-file", help=argparse.SUPPRESS)
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask visual questions from a phone or laptop.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _ask_parser(subparsers)
    catalog = subparsers.add_parser("catalog", help="list built-in common footprints")
    catalog.add_argument("category", nargs="?", help="category name (omit for all)")
    schema = subparsers.add_parser("schema", help="print the payload JSON schema")
    schema.add_argument("--path", action="store_true", help="print the schema path instead of its contents")
    args = parser.parse_args(argv)

    if args.command == "catalog":
        if args.category and args.category not in footprint_preview.COMMON_FOOTPRINTS:
            print(
                f"unknown category {args.category!r}; choose from "
                f"{', '.join(sorted(footprint_preview.COMMON_FOOTPRINTS))}",
                file=sys.stderr,
            )
            return 2
        categories = (
            [args.category] if args.category else sorted(footprint_preview.COMMON_FOOTPRINTS)
        )
        print(
            json.dumps(
                {
                    category: [
                        {"id": item_id, "label": label}
                        for item_id, label in footprint_preview.COMMON_FOOTPRINTS[category]
                    ]
                    for category in categories
                },
                indent=2,
            )
        )
        return 0
    if args.command == "schema":
        if args.path:
            print(SCHEMA_PATH)
            return 0
        try:
            print(SCHEMA_PATH.read_text(encoding="utf-8"))
        except OSError as exc:
            print(f"schema not found: {exc}", file=sys.stderr)
            return 1
        return 0

    # ask
    if args.only_missing and not args.from_spec:
        print("--only-missing requires --from-spec", file=sys.stderr)
        return 2
    if args.detach and args.print_json:
        print("--json cannot be combined with --detach; read the answer file instead", file=sys.stderr)
        return 2
    out_path = Path(args.out).expanduser().resolve()
    url_file = Path(args.url_file).expanduser().resolve() if args.url_file else out_path.with_suffix(".url")
    status_file = (
        Path(args.status_file).expanduser().resolve()
        if args.status_file
        else out_path.with_suffix(".status.json")
    )
    log_file = Path(args.log_file).expanduser().resolve() if args.log_file else out_path.with_suffix(".log")
    host = "0.0.0.0" if args.mobile else args.host

    if args.payload_file:
        try:
            payload = _load_payload_file(Path(args.payload_file))
        except PayloadError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    else:
        if args.from_spec:
            if args.footprint_for:
                parser.error("--from-spec and --footprint-for are mutually exclusive")
            try:
                spec = _load_payload_file(Path(args.from_spec).expanduser())
                raw_payload = build_spec_payload(
                    spec,
                    only_missing=args.only_missing,
                    extra_options=args.option,
                    title=args.title,
                    intro=args.intro,
                )
            except PayloadError as exc:
                print(f"invalid spec: {exc}", file=sys.stderr)
                return 1
        elif args.footprint_for:
            if not args.category:
                parser.error("--footprint-for requires --category")
            try:
                raw_payload = build_catalog_payload(
                    args.footprint_for,
                    args.category,
                    options=args.option,
                    prompt=args.prompt,
                    title=args.title,
                    intro=args.intro,
                )
            except PayloadError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        elif args.payload:
            try:
                raw_payload = _load_payload_file(Path(args.payload).expanduser())
            except PayloadError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            if args.title:
                raw_payload["title"] = args.title
            if args.intro:
                raw_payload["intro"] = args.intro
        else:
            parser.error(
                "provide a payload file, or use --from-spec SPEC, or --footprint-for REF --category KIND"
            )
            return 2
        try:
            payload = normalize_payload(
                raw_payload,
                enrich=True,
                fetch=not args.no_fetch,
                footprints_dir=Path(args.footprints_dir).expanduser() if args.footprints_dir else None,
            )
        except PayloadError as exc:
            print(f"invalid payload: {exc}", file=sys.stderr)
            return 1

    if args.detach and not args._child:
        return _detach(
            payload=payload,
            out_path=out_path,
            host=host,
            port=args.port,
            timeout=args.timeout,
            url_file=url_file,
            status_file=status_file,
            log_file=log_file,
            open_browser=args.open_browser,
            verbose=args.verbose,
        )

    try:
        server = make_server(
            payload,
            out_path=out_path,
            host=host,
            port=args.port,
            status_path=status_file,
            verbose=args.verbose,
        )
    except OSError as exc:
        print(f"could not start the visual ask server: {exc}", file=sys.stderr)
        return 1
    return run_server(
        server,
        timeout=args.timeout,
        url_file=url_file,
        open_browser=args.open_browser,
        print_json=args.print_json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
