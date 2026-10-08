"""The lexicon review: selection, parsing verdicts, runs, measuring, rating and applying."""

import json
from types import SimpleNamespace

import pytest

from spanish_tutor.content import add_item
from spanish_tutor.evaluation import lexicon_review as lr
from spanish_tutor.evaluation import ratings


def lexeme(
    conn, lemma, pos="NOUN", definition="a thing", example=None, source="wiktionary", freq=10.0
):
    return conn.execute(
        "INSERT INTO lexemes (lemma, pos, definition_en, definition_source, example_es, example_en, "
        "frequency_per_million) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (lemma, pos, definition, source, example, example and f"[{example}]", freq),
    ).lastrowid


def know(conn, lexeme_id):
    conn.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source) "
        "VALUES (?, 'recognition', 'taught', 'seed')",
        (lexeme_id,),
    )


def index(conn, content_id, counts):
    conn.executemany(
        "INSERT INTO content_vocab (content_id, lexeme_id, occurrences) VALUES (?, ?, ?)",
        [(content_id, lexeme_id, n) for lexeme_id, n in counts.items()],
    )
    conn.execute(
        "UPDATE content_items SET tokens = ? WHERE content_id = ?",
        (sum(counts.values()), content_id),
    )


def test_tier_a_is_the_vocabulary_within_reach_the_word_bank_and_model_written_words(conn):
    casa, perro, gato, raro, nuevo, model = (
        lexeme(conn, "casa", freq=50), lexeme(conn, "perro", freq=40), lexeme(conn, "gato", freq=30),
        lexeme(conn, "raro", freq=5), lexeme(conn, "nuevo", freq=3),
        lexeme(conn, "parrandeo", source="model:claude-opus-5-5", freq=None),
    )  # fmt: skip
    know(conn, casa)
    know(conn, perro)
    easy = add_item(conn, "story", "Fácil", "x", source="g", is_private=False)
    index(conn, easy, {casa: 8, perro: 1, gato: 1})  # 10% unknown: within reach
    hard = add_item(conn, "story", "Difícil", "x", source="g", is_private=False)
    index(conn, hard, {casa: 1, raro: 9})  # 90% unknown
    assert lr.tier_a(conn, ceiling=0.2) == [casa, perro, gato, model]  # most frequent first
    assert nuevo not in lr.tier_a(conn) and raro not in lr.tier_a(conn)


def entry(lexeme_id, example=True):
    return lr.Entry(
        lexeme_id, f"w{lexeme_id}", "NOUN", "def", "wiktionary", "Ej." if example else None, "Ex."
    )


def test_unflagged_fields_are_correct_and_bad_answers_are_ignored():
    batch = [entry(1), entry(2, example=False)]
    rows = lr.verdict_rows(
        batch,
        [
            {
                "id": 1,
                "field": "definition_en",
                "verdict": "incorrect",
                "suggestion": " a house ",
                "reason": "r",
            },
            {
                "id": 1,
                "field": "definition_en",
                "verdict": "unsure",
                "suggestion": "",
                "reason": "later",
            },
            {
                "id": 2,
                "field": "example_en",
                "verdict": "incorrect",
                "suggestion": "x",
                "reason": "no example",
            },
            {
                "id": 99,
                "field": "pos",
                "verdict": "incorrect",
                "suggestion": "VERB",
                "reason": "not asked",
            },
            {
                "id": 1,
                "field": "gender",
                "verdict": "incorrect",
                "suggestion": "f",
                "reason": "no field",
            },
        ],
    )
    assert len(rows) == 5 + 3  # an entry without an example has three fields to review
    assert (1, "definition_en", "incorrect", "a house", "r") in rows  # the first answer wins
    assert all(r[2] == "correct" for r in rows if r[0] == 2)
    assert {r[1] for r in rows if r[0] == 2} == {"lemma", "pos", "definition_en"}


def test_the_prompt_shows_each_entry_and_marks_model_written_definitions():
    model = lr.Entry(7, "parrandeo", "NOUN", "partying", "model:claude-opus-5-5", None, None)
    text = lr.prompt([entry(1), model])
    assert "[1] w1 (NOUN): def" in text and '«Ej.» = "Ex."' in text
    assert "[7] parrandeo (NOUN): partying  [model-written definition]" in text


class FakeBatches:
    """messages.batches: one batch, answered with `problems` per request."""

    def __init__(self, answers):
        self.answers = answers  # custom_id -> problems list, or None for a failed request
        self.created = None

    def create(self, requests):
        self.created = requests
        return SimpleNamespace(id="batch-1")

    def retrieve(self, batch_id):
        return SimpleNamespace(
            processing_status="ended", request_counts=SimpleNamespace(processing=0)
        )

    def results(self, batch_id):
        for request in self.created:
            problems = self.answers.get(request["custom_id"])
            if problems is None:
                yield SimpleNamespace(
                    custom_id=request["custom_id"], result=SimpleNamespace(type="errored")
                )
                continue
            message = SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps({"problems": problems}))],
                usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
            )
            yield SimpleNamespace(
                custom_id=request["custom_id"],
                result=SimpleNamespace(type="succeeded", message=message),
            )


def client(answers):
    return SimpleNamespace(messages=SimpleNamespace(batches=FakeBatches(answers)))


def test_a_run_is_submitted_in_requests_of_25_and_collected_into_lexeme_reviews(
    conn, tmp_path, monkeypatch
):
    monkeypatch.setattr(lr, "STATE_DIR", tmp_path)
    ids = [lexeme(conn, f"palabra{i}", example="Ej.") for i in range(30)]
    fake = client({"lex-0": [{"id": ids[0], "field": "pos", "verdict": "incorrect", "suggestion": "VERB", "reason": "r"}],
                   "lex-1": None})  # fmt: skip
    state = lr.submit(conn, lr.entries(conn, ids), name="t", client=fake)
    requests = fake.messages.batches.created
    assert [len(r["params"]["messages"][0]["content"].split("\n[")) - 1 for r in requests] == [
        25,
        5,
    ]
    assert requests[0]["params"]["model"] == lr.REVIEWER
    summary = lr.collect(conn, "t", client=fake)
    assert summary["failed_requests"] == [1]  # the second request's 5 words stay unreviewed
    assert summary["rows"] == 25 * 5 and summary["flagged"] == 1
    assert summary["dollars"] == pytest.approx((1000 * 1.0 + 100 * 5.0) / 1e6)
    assert lr.report(conn, state["run_id"])["pos"] == {"correct": 24, "incorrect": 1}
    run = conn.execute(
        "SELECT kind, model, prompt_version FROM eval_runs WHERE run_id = ?", (state["run_id"],)
    )
    assert tuple(run.fetchone()) == ("judge", lr.REVIEWER, lr.PROMPT_VERSION)


def two_runs(conn):
    ids = [lexeme(conn, f"w{i}", example="Ej.") for i in range(3)]
    runs = []
    for flags in ([(ids[0], "pos"), (ids[1], "definition_en")], [(ids[0], "pos")]):
        run = lr.new_run(conn, lr.REVIEWER, {})
        rows = lr.verdict_rows(
            lr.entries(conn, ids),
            [
                {"id": i, "field": f, "verdict": "incorrect", "suggestion": "x", "reason": "r"}
                for i, f in flags
            ],
        )
        lr.store(conn, run, lr.REVIEWER, rows)
        runs.append(run)
    return ids, runs


def test_consistency_compares_two_runs_field_by_field(conn):
    _, (a, b) = two_runs(conn)
    found = lr.consistency(conn, a, b)
    assert (found["fields"], found["flagged_a"], found["flagged_b"], found["both"]) == (15, 2, 1, 1)
    assert 0 < found["alpha"] < 1


def test_recall_counts_the_planted_errors_a_run_flagged(conn):
    ids, (a, _) = two_runs(conn)
    found = lr.recall(conn, a, [(ids[0], "pos"), (ids[2], "lemma")])
    assert (found["planted"], found["caught"], found["missed"]) == (2, 1, [(ids[2], "lemma")])


def test_planted_errors_change_a_copy_and_are_returned(conn):
    ids = [
        lexeme(conn, f"palabra{i}", definition=f"def {i}", example=f"Ej {i}.") for i in range(40)
    ]
    planted = lr.plant_errors(conn, ids, n=8)
    assert len(planted) >= 6 and {f for _, f in planted} <= set(lr.FIELDS)
    for lexeme_id, field in planted:
        column = "lemma" if field == "lemma" else field
        value = conn.execute(
            f"SELECT {column} FROM lexemes WHERE lexeme_id = ?", (lexeme_id,)
        ).fetchone()[0]
        assert value != {"lemma": f"palabra{lexeme_id - ids[0]}", "pos": "NOUN",
                         "definition_en": f"def {lexeme_id - ids[0]}",
                         "example_en": f"[Ej {lexeme_id - ids[0]}.]"}[field]  # fmt: skip


def test_second_opinion_reviews_only_what_was_flagged(conn):
    ids, (a, _) = two_runs(conn)
    asked = []

    def ask(text):
        asked.append(text)
        return (
            [
                {
                    "id": ids[0],
                    "field": "pos",
                    "verdict": "incorrect",
                    "suggestion": "VERB",
                    "reason": "r",
                }
            ],
            500,
            50,
        )

    found = lr.second_opinion(conn, a, ask)
    assert found["entries"] == 2 and found["flagged"] == 1 and len(asked) == 1
    assert "[" + str(ids[2]) + "]" not in asked[0]  # the word nobody flagged isn't asked about


def test_flags_and_a_sample_are_queued_and_only_accepted_fixes_are_applied(conn):
    ids = [lexeme(conn, f"w{i}", definition=f"def {i}", example=f"Ej {i}.") for i in range(6)]
    run = lr.new_run(conn, lr.REVIEWER, {})
    problems = [
        {
            "id": ids[0],
            "field": "definition_en",
            "verdict": "incorrect",
            "suggestion": "a better def",
            "reason": "r",
        },
        {
            "id": ids[1],
            "field": "example_en",
            "verdict": "incorrect",
            "suggestion": "Better.",
            "reason": "r",
        },
        {"id": ids[2], "field": "pos", "verdict": "incorrect", "suggestion": "VERB", "reason": "r"},
        {
            "id": ids[3],
            "field": "definition_en",
            "verdict": "unsure",
            "suggestion": "maybe",
            "reason": "r",
        },
    ]
    lr.store(conn, run, lr.REVIEWER, lr.verdict_rows(lr.entries(conn, ids), problems))
    assert lr.queue_for_rating(conn, run, None, entries_sample=2) == (4, 2)
    flags = {i["content"]["lexeme_id"]: i for i in ratings.queue(conn, "lexeme_flag")}
    assert len(ratings.queue(conn, "lexeme_entry")) == 2
    for lexeme_id, label in (
        (ids[0], "fix"),
        (ids[1], "fix"),
        (ids[2], "fix"),
        (ids[3], "not_a_problem"),
    ):
        ratings.rate(conn, flags[lexeme_id]["item_id"], label=label)
    fixes = lr.accepted_fixes(conn)
    assert sorted(f["lexeme_id"] for f in fixes) == [ids[0], ids[1]]  # a pos fix is never applied
    conn.execute(
        "UPDATE lexemes SET example_en = 'edited meanwhile' WHERE lexeme_id = ?", (ids[1],)
    )
    assert lr.apply(conn, fixes) == 1  # changed since it was queued: skipped, not overwritten
    row = conn.execute(
        "SELECT definition_en, definition_source FROM lexemes WHERE lexeme_id = ?", (ids[0],)
    )
    assert tuple(row.fetchone()) == ("a better def", "wiktionary+reviewed")
    assert (
        conn.execute("SELECT pos FROM lexemes WHERE lexeme_id = ?", (ids[2],)).fetchone()[0]
        == "NOUN"
    )


def two_reviewers(conn):
    """Five words reviewed by two runs: word 0 both incorrect, 1 only the primary, 2 only the
    secondary, 3 one unsure, 4 passed by both."""
    ids = [lexeme(conn, f"w{i}", definition=f"def {i}", example=f"Ej {i}.") for i in range(5)]
    primary, secondary = lr.new_run(conn, lr.SECOND_OPINION, {}), lr.new_run(conn, lr.REVIEWER, {})

    def flag(i, verdict, suggestion):
        return {
            "id": ids[i],
            "field": "definition_en",
            "verdict": verdict,
            "suggestion": suggestion,
            "reason": "r",
        }

    items = lr.entries(conn, ids)
    lr.store(
        conn,
        primary,
        lr.SECOND_OPINION,
        lr.verdict_rows(
            items,
            [flag(0, "incorrect", "opus fix"), flag(1, "incorrect", "o1"), flag(3, "unsure", "")],
        ),
    )
    lr.store(conn, secondary, lr.REVIEWER,
             lr.verdict_rows(items, [flag(0, "incorrect", "sonnet fix"), flag(2, "incorrect", "s2")]))  # fmt: skip
    return ids, primary, secondary


def test_flags_are_grouped_by_how_sure_the_two_reviewers_are(conn):
    ids, primary, secondary = two_reviewers(conn)
    groups = lr.flag_groups(conn, primary, secondary)
    assert {g: [k[0] for k in keys] for g, keys in groups.items()} == {
        "both": [ids[0]], "primary_only": [ids[1]], "secondary_only": [ids[2]], "unsure": [ids[3]],
    }  # fmt: skip


def test_each_group_is_sampled_with_both_opinions_and_precision_comes_from_ratings(conn):
    ids, primary, secondary = two_reviewers(conn)
    assert lr.queue_groups(conn, primary, secondary, per_group=5, passed_sample=5) == {
        "both": 1, "primary_only": 1, "secondary_only": 1, "unsure": 1, "passed": 1,
    }  # fmt: skip
    flags = {i["content"]["lexeme_id"]: i for i in ratings.queue(conn, "lexeme_flag")}
    both = flags[ids[0]]["content"]
    assert (both["group"], both["suggestion"]) == ("both", "opus fix")  # the primary's fix
    assert both["second_opinion"] == "Sonnet: incorrect — “sonnet fix” — r"
    assert flags[ids[2]]["content"]["suggestion"] == "s2"  # only the secondary has one
    assert flags[ids[2]]["content"]["second_opinion"].startswith("Opus: correct")
    assert [i["content"]["lexeme_id"] for i in ratings.queue(conn, "lexeme_entry")] == [ids[4]]
    ratings.rate(conn, flags[ids[0]]["item_id"], label="fix")
    ratings.rate(conn, flags[ids[1]]["item_id"], label="not_a_problem")
    ratings.rate(conn, ratings.queue(conn, "lexeme_entry")[0]["item_id"], label="ok")
    found = lr.group_precision(conn)
    assert (found["both"]["real"].k, found["both"]["real"].n) == (1, 1)
    assert (found["primary_only"]["real"].k, found["primary_only"]["real"].n) == (0, 1)
    assert (found["passed"]["wrong"].k, found["passed"]["wrong"].n) == (0, 1)
    assert [f["suggestion"] for f in lr.accepted_fixes(conn)] == ["opus fix"]


def test_a_split_keeps_only_senses_drawn_from_the_current_definition():
    item = {"lexeme_id": 7, "definition_en": "(Chile, slang) money; (vulgar) penis; a kind of pot"}
    answer = {
        "id": 7,
        "kind": "register",
        "main_en": "a kind of pot",
        "senses": [
            {"sense_en": "money", "register": "slang", "region": "Chile"},
            {"sense_en": "penis", "register": "vulgar", "region": ""},
            {"sense_en": "a famous footballer", "register": "slang", "region": ""},  # invented
            {"sense_en": "money", "register": "poetic", "region": ""},  # not a register
        ],
    }
    assert lr.check_split(item, answer) == {
        "lexeme_id": 7,
        "kind": "register",
        "main_en": "a kind of pot",
        "senses": [
            {"sense_en": "money", "register": "slang", "region": "Chile"},
            {"sense_en": "penis", "register": "vulgar", "region": None},
        ],
    }
    assert lr.check_split(item, {**answer, "main_en": " "}) is None
    assert lr.check_split(item, {**answer, "kind": "maybe"}) is None
    wrong = lr.check_split(item, {**answer, "kind": "wrong"})
    assert wrong["senses"] == []  # a wrong definition keeps nothing


def test_split_candidates_are_the_words_whose_definition_was_flagged(conn):
    ids = [lexeme(conn, f"w{i}", definition=f"def {i}") for i in range(3)]
    run = lr.new_run(conn, lr.REVIEWER, {})
    problems = [
        {"id": ids[0], "field": "definition_en", "verdict": "incorrect", "suggestion": "better",
         "reason": "rare sense first"},
        {"id": ids[1], "field": "example_en", "verdict": "incorrect", "suggestion": "x",
         "reason": "r"},
    ]  # fmt: skip
    lr.store(conn, run, lr.REVIEWER, lr.verdict_rows(lr.entries(conn, ids), problems))
    found = lr.split_candidates(conn, [run])
    assert [c["lexeme_id"] for c in found] == [ids[0]]
    assert found[0]["notes"] == ["incorrect: rare sense first (suggests: better)"]
    assert "[" + str(ids[0]) + "] w0 (NOUN): def 0" in lr.split_prompt(found)


def test_applying_a_register_fix_keeps_the_other_senses_and_a_wrong_one_keeps_none(conn):
    slang = lexeme(conn, "lana", definition="wool; (Mexico, slang) money")
    wrong = lexeme(conn, "w", definition="a mistranslation")
    fixes = [
        {"lexeme_id": slang, "field": "definition_en", "current": "wool; (Mexico, slang) money",
         "suggestion": "wool"},
        {"lexeme_id": wrong, "field": "definition_en", "current": "a mistranslation",
         "suggestion": "the right thing"},
    ]  # fmt: skip
    splits = {
        slang: {"lexeme_id": slang, "kind": "register", "main_en": "wool",
                "senses": [{"sense_en": "money", "register": "slang", "region": "Mexico"}]},
        wrong: {"lexeme_id": wrong, "kind": "wrong", "main_en": "the right thing", "senses": []},
    }  # fmt: skip
    assert lr.apply(conn, fixes, splits, split_model="claude-opus-5-5") == 2
    rows = conn.execute(
        "SELECT lexeme_id, sense_en, register, region, source, reviewer FROM lexeme_senses"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        (slang, "money", "slang", "Mexico", "wiktionary", "claude-opus-5-5")
    ]
    assert (
        conn.execute("SELECT definition_en FROM lexemes WHERE lexeme_id = ?", (slang,)).fetchone()[
            0
        ]
        == "wool"  # Jason's accepted fix, not the split's main sense
    )


def test_a_group_is_applied_in_bulk_with_the_primarys_fix_and_rated_fixes_win(conn):
    ids, primary, secondary = two_reviewers(conn)
    bulk = lr.group_fixes(conn, primary, secondary, "both")
    assert [(f["lexeme_id"], f["suggestion"], f["reviewer"]) for f in bulk] == [
        (ids[0], "opus fix", lr.SECOND_OPINION)
    ]
    assert [
        f["suggestion"] for f in lr.group_fixes(conn, primary, secondary, "secondary_only")
    ] == [
        "s2"  # only the secondary has a suggestion
    ]
    rated = [{**bulk[0], "suggestion": "Jason's pick"}]
    merged = lr.merge_fixes(rated, bulk)
    assert [f["suggestion"] for f in merged] == ["Jason's pick"]
    assert lr.apply(conn, merged) == 1
    assert (
        conn.execute("SELECT definition_en FROM lexemes WHERE lexeme_id = ?", (ids[0],)).fetchone()[
            0
        ]
        == "Jason's pick"
    )


def test_an_applied_example_translation_credits_the_model_that_suggested_it(conn):
    w = lexeme(conn, "w", example="Ej.")
    run = lr.new_run(conn, lr.SECOND_OPINION, {})
    lr.store(conn, run, lr.SECOND_OPINION, lr.verdict_rows(lr.entries(conn, [w]), [
        {"id": w, "field": "example_en", "verdict": "incorrect", "suggestion": "Better.",
         "reason": "r"},
    ]))  # fmt: skip
    fix = {"lexeme_id": w, "field": "example_en", "current": "[Ej.]", "suggestion": "Better."}
    assert lr.apply(conn, [fix]) == 1  # a rated fix: no reviewer recorded, looked up
    assert tuple(
        conn.execute(
            "SELECT example_en, example_en_source FROM lexemes WHERE lexeme_id = ?", (w,)
        ).fetchone()
    ) == ("Better.", f"reviewed:{lr.SECOND_OPINION}")
