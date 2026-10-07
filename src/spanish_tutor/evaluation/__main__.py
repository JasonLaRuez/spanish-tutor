"""The evaluation commands.

python -m spanish_tutor.evaluation report [--db PATH] [--session N]
python -m spanish_tutor.evaluation freeze [--force]
python -m spanish_tutor.evaluation benchmark [--pilot | --topic T ...]
python -m spanish_tutor.evaluation score RUN_DIR
python -m spanish_tutor.evaluation translations [--pilot]
python -m spanish_tutor.evaluation consistency RUN_ID [RUN_ID ...]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from spanish_tutor import db
from spanish_tutor.config import DB_PATH
from spanish_tutor.evaluation import benchmark, translation
from spanish_tutor.evaluation.metrics import format_report, report


def show_run(run_dir: Path) -> None:
    found = benchmark.score(run_dir)
    summary = found["summary"]
    print(f"Run {run_dir.name}: {', '.join(summary['topics'])}")
    print(
        f"  {summary['known_words']} known words; {summary['calls']} model calls, "
        f"{summary['cents']}¢ {summary['cents_by_kind']}"
    )
    print(benchmark.format_grading(found["grading"]))
    print(format_report(found["metrics"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="The evaluation framework (slice 4.4).")
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("report", help="print the log metrics, each with its n")
    show.add_argument("--db", type=Path, default=DB_PATH, help="default: the real word bank")
    show.add_argument("--session", type=int, help="one session only")
    freeze = commands.add_parser("freeze", help="snapshot the word bank for the benchmark")
    freeze.add_argument("--force", action="store_true", help="replace an existing snapshot")
    bench = commands.add_parser("benchmark", help="run the conversation benchmark (costs money)")
    which = bench.add_mutually_exclusive_group()
    which.add_argument("--pilot", action="store_true", help="the first topic only (~10¢)")
    which.add_argument("--topic", action="append", help="these topics only")
    scored = commands.add_parser("score", help="score a benchmark run")
    scored.add_argument("run_dir", type=Path)
    tr = commands.add_parser(
        "translations", help="translate, judge repeatedly, and judge attempts (costs money)"
    )
    tr.add_argument("--pilot", action="store_true", help="Rima XXIII and Rima XVII only (~5¢)")
    agree = commands.add_parser("consistency", help="how much a judge run agrees with itself")
    agree.add_argument("run_id", type=int, nargs="+")
    again = commands.add_parser(
        "rejudge", help="judge a translation run's lines again with the current rubric"
    )
    again.add_argument("translation_run", help="its directory name, e.g. 20261007-184120Z")
    again.add_argument("--poem", action="append", help="these poems only")
    versus = commands.add_parser("calibration", help="a judge run against Jason's ratings")
    versus.add_argument("run_id", type=int, nargs="+")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "report":
        # Read-only: a report never writes to the word bank it measures.
        conn = sqlite3.connect(f"file:{args.db.as_posix()}?mode=ro", uri=True)
        try:
            print(format_report(report(conn, args.session)))
        finally:
            conn.close()
    elif args.command == "freeze":
        print(f"Snapshot: {benchmark.freeze_base(force=args.force)}")
    elif args.command == "benchmark":
        if not benchmark.BASE_DB.exists():
            sys.exit("No snapshot yet: run `python -m spanish_tutor.evaluation freeze` first.")
        script = benchmark.load_script()
        topics = [script.topics[0].topic] if args.pilot else args.topic
        run_dir = benchmark.run(topics)
        show_run(run_dir)
        # The words the replies taught, for Jason to rate in the app.
        source = sqlite3.connect(run_dir / "benchmark.db")
        target = db.connect()
        if pending := db.pending_migrations(target):  # as the server does: back up first
            print(f"Upgrading the word bank (migrations {pending}); backup: {db.backup()}")
        db.init_schema(target)
        found = benchmark.score(run_dir)
        own = benchmark.first_session(found["summary"], benchmark.read_results(run_dir))
        added = benchmark.queue_new_word_flags(
            source, target, f"benchmark:{run_dir.name}:", from_session=own
        )
        source.close()
        target.close()
        print(f"Queued {added} taught words for rating. Run directory: {run_dir}")
    elif args.command == "score":
        show_run(args.run_dir)
    elif args.command == "translations":
        if not benchmark.BASE_DB.exists():
            sys.exit("No snapshot yet: run `python -m spanish_tutor.evaluation freeze` first.")
        summary = translation.run(pilot=args.pilot)
        print(f"Translated {len(summary['poems'])} poems ({summary['lines']} lines)")
        print(f"Cost (¢): {summary['cents']}")
        conn = db.connect()
        for run_id in (summary["judge_run"], summary["attempt_run"]):
            print(translation.format_consistency(run_id, translation.consistency(conn, run_id)))
        conn.close()
    elif args.command == "rejudge":
        conn = db.connect()
        run_id, spent = translation.rejudge(conn, args.translation_run, args.poem)
        print(f"Judge run {run_id} ({translation.PROMPT_VERSION}): {spent}¢")
        print(translation.format_consistency(run_id, translation.consistency(conn, run_id)))
        print(translation.format_calibration(run_id, translation.calibration(conn, run_id)))
        conn.close()
    else:
        conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
        for run_id in args.run_id:
            if args.command == "consistency":
                found = translation.consistency(conn, run_id)
                print(translation.format_consistency(run_id, found))
            else:
                found = translation.calibration(conn, run_id)
                print(translation.format_calibration(run_id, found))
        conn.close()


if __name__ == "__main__":
    main()
