"""A clinician rating sheet as a fillable PDF, and a filled form back as the sheet's CSV.

    python scripts/rating_form.py build --sheet <packet>/rate_constructed.csv \\
        --title "Constructed notes" --raters 2
    python scripts/rating_form.py read --form rate_constructed_AOA.pdf \\
        --sheet <packet>/rate_constructed.csv --out rate_constructed_AOA.csv

The form is built from the blind sheet alone, beside it unless ``--out`` says otherwise.
``--instrument reading`` or ``harm`` builds and reads addendum 4's sheets (A/B/C; H0 to H4).
``read`` writes the sheet's own columns, which ``scripts/chart_read.py agreement --rating
name=<csv>`` reads unchanged, and exits 1 while any item is unanswered or answered twice.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clinical_eval import rating_form as rf


def cmd_build(args: argparse.Namespace) -> int:
    out = args.out or args.sheet.with_suffix(".pdf")
    instrument = rf.INSTRUMENTS[args.instrument]
    report = rf.build(args.sheet, out, title=args.title, raters=args.raters, instrument=instrument)
    print(json.dumps(report, indent=2))
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    filled = rf.read(args.form, args.sheet, rf.INSTRUMENTS[args.instrument])
    rf.write_rows(filled.rows, args.out)
    report = {
        "form": str(args.form),
        "ratings": str(args.out),
        "initials": filled.initials,
        "date": filled.date,
        "items": len(filled.rows),
        "answered": sum(bool(row["answer"]) for row in filled.rows),
        "flagged": sum(bool(row["flag"]) for row in filled.rows),
        "unanswered": filled.unanswered,
        "answered_twice": filled.conflicts,
        "comment": filled.comment,
    }
    print(json.dumps(report, indent=2))
    return 1 if filled.unanswered or filled.conflicts else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="write the fillable form for a rating sheet")
    build.add_argument("--sheet", type=Path, required=True)
    build.add_argument("--title", required=True, help="what the sheet holds, shown to the rater")
    build.add_argument("--raters", type=int, default=1)
    build.add_argument("--out", type=Path)
    build.add_argument("--instrument", choices=sorted(rf.INSTRUMENTS), default="labels")
    build.set_defaults(handler=cmd_build)
    read = sub.add_parser("read", help="a filled form back into the sheet's columns")
    read.add_argument("--form", type=Path, required=True)
    read.add_argument("--sheet", type=Path, required=True)
    read.add_argument("--out", type=Path, required=True)
    read.add_argument("--instrument", choices=sorted(rf.INSTRUMENTS), default="labels")
    read.set_defaults(handler=cmd_read)
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
