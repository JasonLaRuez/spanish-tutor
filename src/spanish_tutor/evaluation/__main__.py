"""The evaluation commands.

python -m spanish_tutor.evaluation report [--db PATH] [--session N]
python -m spanish_tutor.evaluation freeze [--force]
python -m spanish_tutor.evaluation benchmark [--pilot | --topic T ...]
python -m spanish_tutor.evaluation score RUN_DIR
python -m spanish_tutor.evaluation translations [--pilot]
python -m spanish_tutor.evaluation consistency RUN_ID [RUN_ID ...]
python -m spanish_tutor.evaluation lexicon-review STEP [--db PATH] [--name N] [--run N]
"""

import argparse
import random
import sqlite3
import sys
from pathlib import Path

from spanish_tutor import db
from spanish_tutor.config import DB_PATH
from spanish_tutor.evaluation import benchmark, retrieval, translation
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


PILOT_RANDOM, PILOT_PLANTED = 180, 20


def lexicon_review_step(args) -> None:
    """One step of the lexicon review (see lexicon_review.py)."""
    from spanish_tutor.evaluation import lexicon_review as lr

    real = args.db.resolve() == DB_PATH.resolve()
    if args.step == "pilot" and real:
        sys.exit("The pilot plants errors: run it on a copy (--db).")
    conn = db.connect(args.db)
    db.init_schema(conn)
    try:
        if args.step == "pilot":
            tier = lr.tier_a(conn)
            rng = random.Random(5)
            sample = rng.sample(tier, PILOT_RANDOM + PILOT_PLANTED * 3)
            planted = lr.plant_errors(conn, sample[PILOT_RANDOM:], PILOT_PLANTED)
            ids = sample[:PILOT_RANDOM] + [lexeme_id for lexeme_id, _ in planted]
            rng.shuffle(ids)
            lr.save_json(lr.STATE_DIR / "pilot-planted.json", planted)
            items = lr.entries(conn, ids)
            for name in ("pilot-a", "pilot-b"):
                state = lr.submit(conn, items, name=name, note="pilot")
                print(
                    f"{name}: run {state['run_id']}, batch {state['batch_id']}, {len(items)} words"
                )
        elif args.step == "submit":
            items = lr.entries(conn, lr.tier_a(conn))
            print(f"{len(items):,} Tier A words in {len(lr.chunks(items))} requests")
            if not args.yes and input("submit? [y/N] ").strip().lower() != "y":
                return
            model = lr.SECOND_OPINION if args.model == "opus" else lr.REVIEWER
            state = lr.submit(conn, items, name=args.name, model=model)
            print(f"run {state['run_id']}, batch {state['batch_id']}")
        elif args.step == "collect":
            print(lr.collect(conn, args.name))
        elif args.step == "pilot-report":
            states = {n: lr.load_json(lr.STATE_DIR / f"{n}.json") for n in ("pilot-a", "pilot-b")}
            planted = [tuple(p) for p in lr.load_json(lr.STATE_DIR / "pilot-planted.json")]
            a, b = (states[n]["run_id"] for n in ("pilot-a", "pilot-b"))
            print("consistency:", lr.consistency(conn, a, b))
            for n, run in (("a", a), ("b", b)):
                print(
                    f"run {n} ({run}): recall {lr.recall(conn, run, planted)}; {lr.report(conn, run)}"
                )
            cost = states["pilot-a"]["collected"]["dollars"]
            words = sum(len(chunk) for chunk in states["pilot-a"]["chunks"])
            tier = len(lr.tier_a(db.connect(DB_PATH)))
            print(
                f"cost per word ${cost / words:.5f}; Tier A ({tier:,} words): ~${cost / words * tier:.2f}"
            )
        elif args.step == "second-opinion":
            print(lr.second_opinion(conn, args.run, lr.claude_ask()))
        elif args.step == "queue":
            if args.second:
                groups = lr.flag_groups(conn, args.run, args.second)
                print({g: len(keys) for g, keys in groups.items()}, "flagged fields per group")
                print("queued:", lr.queue_groups(conn, args.run, args.second))
            else:
                flags, sample = lr.queue_for_rating(conn, args.run, None)
                print(f"queued {flags} flags and {sample} passed entries for the Rate page")
        elif args.step == "precision":
            for group, rates in lr.group_precision(conn).items():
                print(group, {k: f"{r.k}/{r.n}" for k, r in rates.items()})
        elif args.step == "apply":
            fixes = lr.accepted_fixes(conn)
            if real and fixes:
                print(f"backup: {db.backup(DB_PATH)}")
            print(f"applied {lr.apply(conn, fixes)} of {len(fixes)} accepted fixes")
        else:
            print(lr.report(conn, args.run))
    finally:
        conn.close()


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
    found = commands.add_parser(
        "retrieval", help="context relevance and faithfulness of retrieval (costs money)"
    )
    found.add_argument(
        "benchmark_runs", type=Path, nargs="*", help="default: every benchmark run directory"
    )
    found.add_argument(
        "--discussions-only", action="store_true", help="only the talk about a text (~25 cents)"
    )
    shown = commands.add_parser("retrieval-summary", help="summarize a retrieval judge run")
    shown.add_argument("run_id", type=int)
    lex = commands.add_parser(
        "lexicon-review",
        help="the LLM review of the word database (lexicon_review.py; some steps cost money)",
    )
    lex.add_argument(
        "step",
        choices=[
            "pilot",
            "submit",
            "collect",
            "pilot-report",
            "second-opinion",
            "queue",
            "precision",
            "apply",
            "report",
        ],
        help="pilot: plant errors on a copy and submit two runs; submit: Tier A on --db; "
        "collect: store a submitted run; pilot-report: consistency, recall, cost; "
        "second-opinion: Opus on a run's flags; queue: flags and a sample for the Rate page "
        "(with --second: a sample of each agreement group); precision: per group, from "
        "Jason's ratings; "
        "apply: write the fixes Jason accepted; report: a run's verdicts",
    )
    lex.add_argument("--db", type=Path, default=DB_PATH, help="default: the real word bank")
    lex.add_argument("--name", default="tier-a", help="the submitted run's name (state file)")
    lex.add_argument("--run", type=int, help="a review run id")
    lex.add_argument("--second", type=int, help="the second-opinion run id (queue)")
    lex.add_argument("--yes", action="store_true", help="submit without asking")
    lex.add_argument(
        "--model",
        choices=["sonnet", "opus"],
        default="sonnet",
        help="the reviewer for submit (default: Sonnet 5.5; opus: Opus 5.5)",
    )
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.command == "lexicon-review":
        lexicon_review_step(args)
    elif args.command == "report":
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
    elif args.command == "retrieval":
        runs = args.benchmark_runs or sorted(p for p in benchmark.RUNS_DIR.iterdir() if p.is_dir())
        summary = retrieval.run(runs, examples=not args.discussions_only)
        print(f"Cost (¢): {summary['cents']}")
        conn = db.connect()
        print(retrieval.format_summary(retrieval.summarize(conn, summary["run_id"])))
        conn.close()
    elif args.command == "retrieval-summary":
        conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
        print(retrieval.format_summary(retrieval.summarize(conn, args.run_id)))
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
