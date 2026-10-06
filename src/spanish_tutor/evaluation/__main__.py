"""python -m spanish_tutor.evaluation report [--db PATH] [--session N]"""

import argparse
import sqlite3
import sys
from pathlib import Path

from spanish_tutor.config import DB_PATH
from spanish_tutor.evaluation.metrics import format_report, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluation metrics over the learning log.")
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("report", help="print the log metrics, each with its n")
    show.add_argument("--db", type=Path, default=DB_PATH, help="default: the real word bank")
    show.add_argument("--session", type=int, help="one session only")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # Read-only: a report never writes to the word bank it measures.
    conn = sqlite3.connect(f"file:{args.db.as_posix()}?mode=ro", uri=True)
    try:
        print(format_report(report(conn, args.session)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
