"""ELELex CEFR levels: normalization, the fill SQL, and the readiness query."""

from spanish_tutor import progress
from spanish_tutor.ingest.elelex import (
    Entry,
    Staged,
    fill_levels,
    normalize,
    pos_of,
    read,
    stage,
)

#                A1   A2   B1   B2   C1
NEVER = (0.0, 0.0, 0.0, 0.0, 0.0)


def docs(*counts: float) -> tuple[float, ...]:
    return tuple(float(c) for c in counts)


def add(conn, lemma, pos):
    return conn.execute(
        "INSERT INTO lexemes (lemma, pos) VALUES (?, ?) RETURNING lexeme_id", (lemma, pos)
    ).fetchone()[0]


def level(conn, lemma, pos):
    return conn.execute(
        "SELECT cefr_level FROM lexemes WHERE lemma = ? AND pos = ?", (lemma, pos)
    ).fetchone()[0]


def know(conn, lexeme_id, *, produce=False):
    conn.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source) "
        "VALUES (?, 'recognition', 'taught', 'seed')",
        (lexeme_id,),
    )
    if produce:
        conn.execute(
            "INSERT INTO word_events (lexeme_id, mode, event_type, source, grade) "
            "VALUES (?, 'production', 'used', 'seed', 4)",
            (lexeme_id,),
        )


# --- Normalizing ELELex's entries ----------------------------------------------------------------


def test_freeling_tags_become_our_pos_and_names_are_dropped():
    assert [pos_of(t) for t in ("NCM", "NCF", "VM", "VS", "AQ0", "RG", "SP", "I")] == [
        "NOUN", "NOUN", "VERB", "VERB", "ADJ", "ADV", "ADP", "INTJ",
    ]  # fmt: skip
    assert [pos_of(t) for t in ("CC", "CS", "DA", "PP0")] == ["CCONJ", "SCONJ", "DET", "PRON"]
    assert pos_of("NP0") is None
    assert pos_of("NCM, NP0") == "NOUN"  # two tags listed: the first counts
    assert pos_of("Zu") is None and pos_of("") is None


def test_multi_word_entries_are_written_as_our_expressions():
    assert normalize("a_el_aire_libre") == "al aire libre"
    assert normalize("cerca_de_el_mar") == "cerca del mar"
    assert normalize("Sin_Embargo") == "sin embargo"
    staged = stage([Entry("de_acuerdo", "RG", docs(3, 0, 0, 0, 0))])
    assert staged == [Staged("de acuerdo", "EXPR", docs(3, 0, 0, 0, 0))]


def test_a_feminine_entry_of_a_gender_pair_is_the_feminine_word():
    staged = stage(
        [
            Entry("niño", "NCM", docs(5, 0, 0, 0, 0)),
            Entry("niño", "NCF", docs(0, 4, 0, 0, 0)),
            Entry("señor", "NCM", docs(5, 0, 0, 0, 0)),
            Entry("señor", "NCF", docs(3, 0, 0, 0, 0)),
            Entry("estudiante", "NCM", docs(3, 0, 0, 0, 0)),
            Entry("estudiante", "NCF", docs(3, 0, 0, 0, 0)),
            Entry("mano", "NCF", docs(9, 0, 0, 0, 0)),  # feminine only: its own word
        ]
    )
    assert [(s.lemma, s.pos) for s in staged] == [
        ("niño", "NOUN"),
        ("niña", "NOUN"),
        ("señor", "NOUN"),
        ("señora", "NOUN"),
        ("estudiante", "NOUN"),
        ("estudiante", "NOUN"),
        ("mano", "NOUN"),
    ]


def test_the_tsv_is_read_by_its_document_columns(tmp_path):
    levels = ["a1", "a2", "b1", "b2", "c1"]
    header = (
        ["word", "tag"]
        + [f"level_freq@{lv}" for lv in levels]
        + ["total_freq@total"]
        + [f"nb_doc@{lv}" for lv in levels]
        + ["nb_doc@total"]
    )
    row = ["casa", "NCF", "10", "9", "8", "7", "6", "8", "4", "5", "6", "7", "8", "30"]
    path = tmp_path / "ELELex.tsv"
    path.write_text(
        "\n".join("\t".join(f'"{c}"' for c in r) for r in (header, row)) + "\n", encoding="utf-8"
    )
    assert list(read(path)) == [Entry("casa", "NCF", docs(4, 5, 6, 7, 8))]


# --- The fill SQL -----------------------------------------------------------------------------


def test_a_word_takes_the_first_level_with_enough_documents(conn):
    add(conn, "casa", "NOUN")
    add(conn, "rareza", "NOUN")
    add(conn, "rincón", "NOUN")
    counts = fill_levels(
        conn,
        [
            Staged("casa", "NOUN", docs(1, 3, 9, 9, 9)),  # A1 has only 1 document
            Staged("rareza", "NOUN", docs(2, 2, 2, 2, 2)),  # never 3 anywhere
            Staged("rincón", "NOUN", docs(0, 0, 0, 0, 3)),
        ],
    )
    assert level(conn, "casa", "NOUN") == "A2"
    assert level(conn, "rareza", "NOUN") is None
    assert level(conn, "rincón", "NOUN") == "C1"
    assert (counts.staged, counts.exact, counts.function_words) == (3, 2, 0)


def test_the_threshold_is_a_parameter(conn):
    add(conn, "casa", "NOUN")
    fill_levels(conn, [Staged("casa", "NOUN", docs(1, 3, 0, 0, 0))], min_docs=1)
    assert level(conn, "casa", "NOUN") == "A1"


def test_an_entry_staged_twice_takes_the_earlier_level(conn):
    add(conn, "estudiante", "NOUN")
    fill_levels(
        conn,
        [
            Staged("estudiante", "NOUN", docs(0, 3, 0, 0, 0)),
            Staged("estudiante", "NOUN", docs(3, 0, 0, 0, 0)),
        ],
    )
    assert level(conn, "estudiante", "NOUN") == "A1"


def test_function_words_match_their_lemma_under_any_pos_open_class_words_never(conn):
    add(conn, "sí", "INTJ")  # ELELex has sí only as an adverb
    add(conn, "bajo", "NOUN")  # ELELex has bajo only as a preposition
    counts = fill_levels(
        conn,
        [
            Staged("sí", "ADV", docs(3, 0, 0, 0, 0)),
            Staged("bajo", "ADP", docs(3, 0, 0, 0, 0)),
        ],
    )
    assert level(conn, "sí", "INTJ") == "A1"
    assert level(conn, "bajo", "NOUN") is None
    assert (counts.exact, counts.function_words) == (0, 1)


def test_the_exact_match_wins_over_the_lemma_match(conn):
    add(conn, "mismo", "DET")
    fill_levels(
        conn,
        [Staged("mismo", "DET", docs(0, 3, 0, 0, 0)), Staged("mismo", "PRON", docs(3, 0, 0, 0, 0))],
    )
    assert level(conn, "mismo", "DET") == "A2"


def test_a_refill_clears_stale_levels_and_inserts_no_words(conn):
    add(conn, "casa", "NOUN")
    fill_levels(conn, [Staged("casa", "NOUN", docs(3, 0, 0, 0, 0))])
    fill_levels(conn, [Staged("perro", "NOUN", docs(3, 0, 0, 0, 0))])
    assert level(conn, "casa", "NOUN") is None
    assert conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM sqlite_temp_master").fetchall() == []


# --- Readiness ---------------------------------------------------------------------------------


def test_readiness_counts_each_level_and_running_totals(conn):
    words = {
        ("casa", "A1"): "produce",
        ("perro", "A1"): "recognize",
        ("gato", "A1"): None,
        ("museo", "A2"): "recognize",
        ("paisaje", "A2"): None,
        ("rincón", "B1"): None,
    }
    for (lemma, cefr), known in words.items():
        lexeme_id = add(conn, lemma, "NOUN")
        conn.execute("UPDATE lexemes SET cefr_level = ? WHERE lexeme_id = ?", (cefr, lexeme_id))
        if known:
            know(conn, lexeme_id, produce=known == "produce")
    add(conn, "sin nivel", "EXPR")  # no level: not counted anywhere

    rows = progress.readiness(conn)
    assert [(r["level"], r["words"], r["recognized"], r["produced"]) for r in rows] == [
        ("A1", 3, 2, 1),
        ("A2", 2, 1, 0),
        ("B1", 1, 0, 0),
    ]
    assert [(r["words_up_to"], r["recognized_up_to"], r["produced_up_to"]) for r in rows] == [
        (3, 2, 1),
        (5, 3, 1),
        (6, 3, 1),
    ]


def test_readiness_is_empty_before_levels_are_filled(conn):
    add(conn, "casa", "NOUN")
    assert progress.readiness(conn) == []
