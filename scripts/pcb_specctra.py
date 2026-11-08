#!/usr/bin/env python3
"""Split Specctra DSN/SES operations for CI and local routing workflows.

The all-in-one pipeline uses ``design_pipeline.py all``. This helper is for
environments where pcbnew and Java cannot run in the same container:

    # inside KiCad
    python3 scripts/pcb_specctra.py export board.kicad_pcb board.dsn
    # on the host (Java 25+)
    java -jar freerouting.jar -de board.dsn -do board.ses --gui.enabled=false
    # inside KiCad
    python3 scripts/pcb_specctra.py import board.kicad_pcb board.ses
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pcb_route  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export/import Specctra DSN/SES with pcbnew.")
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("board")
    exp.add_argument("dsn")
    imp = sub.add_parser("import")
    imp.add_argument("board")
    imp.add_argument("ses")
    fill = sub.add_parser("fill")
    fill.add_argument("board")
    args = parser.parse_args(argv)

    try:
        if args.command == "export":
            pcb_route.export_dsn(Path(args.board), Path(args.dsn))
            print(args.dsn)
        elif args.command == "import":
            pcb_route.import_ses(Path(args.board), Path(args.ses))
            pcb_route.fill_zones(Path(args.board))
            print(args.board)
        else:
            pcb_route.fill_zones(Path(args.board))
            print(args.board)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
