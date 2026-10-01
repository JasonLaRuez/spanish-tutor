"""The conversation loop, with a scripted model and a deterministic analyzer (no API, no spaCy).

The analyzer maps surface forms through FORMS; capitalized NAMES are proper nouns (no
analyses, like spaCy's PROPN); anything else is analyzed as itself, a NOUN, so tagger
junk like "xyzzy" reaches the lexicon lookup and must be rejected there.
"""

import re

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from spanish_tutor.conversation import Generation, Tutor, TutorReply, vocabulary_block
from spanish_tutor.ingest.index_tatoeba import index_sentences
from spanish_tutor.ingest.tatoeba import AnalyzedSentence
from spanish_tutor.vectorstore import open_store
from spanish_tutor.words import GRADE_LOOKED_UP, GRADE_MISUSED, GRADE_USED, LexiconIndex

FORMS = {
    "yo": ("yo", "PRON"),
    "soy": ("ser", "VERB"),
    "es": ("ser", "VERB"),
    "estoy": ("estar", "VERB"),
    "cansado": ("cansado", "ADJ"),
    "hoy": ("hoy", "ADV"),
    "hola": ("hola", "INTJ"),
    "el": ("el", "DET"),
    "en": ("en", "ADP"),
    "gato": ("gato", "NOUN"),
    "come": ("comer", "VERB"),
    "comes": ("comer", "VERB"),
    "casa": ("casa", "NOUN"),
    "perro": ("perro", "NOUN"),
    "nada": ("nadar", "VERB"),
    "nadar": ("nadar", "VERB"),
    "río": ("río", "NOUN"),
    "rio": ("rio", "NOUN"),
    "pez": ("pez", "NOUN"),
}
NAMES = {"María"}
KNOWN = ["yo", "ser", "estar", "cansado", "hoy", "hola", "el", "en", "gato", "comer", "casa"]
UNKNOWN = ["perro", "nadar", "río"]  # in the lexicon, not in the word bank


def analyze(text):
    tokens = []
    for word in re.findall(r"\w+", text):
        if word in NAMES:
            tokens.append((word.lower(), []))
        else:
            surface = word.lower()
            tokens.append((surface, [FORMS.get(surface, (surface, "NOUN"))]))
    return tokens


class Scripted:
    """A ReplyGenerator that returns scripted replies and records every request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, messages):
        self.requests.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, str):
            reply = TutorReply(reply_es=reply, reply_en="(en)", misused=[], correction_en=None)
        return Generation(reply, input_tokens=100, cache_read_tokens=1000, output_tokens=50)


def said(reply_es, misused=(), correction=None):
    return TutorReply(
        reply_es=reply_es, reply_en="(en)", misused=list(misused), correction_en=correction
    )


@pytest.fixture
def bank(conn):
    for word in KNOWN + UNKNOWN:
        pos = next(p for w, (lemma, p) in FORMS.items() if lemma == word)
        conn.execute(
            "INSERT INTO lexemes (lemma, pos, definition_en) VALUES (?, ?, ?)",
            (word, pos, f"<{word}>"),
        )
    conn.execute(
        """
        INSERT INTO word_events (lexeme_id, mode, event_type, source)
        SELECT lexeme_id, 'recognition', 'taught', 'seed' FROM lexemes WHERE lemma IN ({})
        """.format(",".join("?" * len(KNOWN))),
        KNOWN,
    )
    conn.commit()
    return conn


@pytest.fixture
def make_tutor(bank, make_wiktionary):
    wiktionary = make_wiktionary([("pez", "noun", "fish")])

    def make(*replies, store=None, topic="los animales"):
        generator = Scripted(*replies)
        tutor = Tutor(
            bank, generator, LexiconIndex(bank, wiktionary), analyze, store=store, topic=topic
        )
        return tutor, generator

    return make


def events(conn, **where):
    sql = """
        SELECT l.lemma, e.mode, e.event_type, e.grade, e.turn_id
        FROM word_events AS e JOIN lexemes AS l USING (lexeme_id)
        WHERE e.source = 'conversation'
    """
    for column in where:
        sql += f" AND e.{column} = :{column}"
    return [tuple(r) for r in conn.execute(sql + " ORDER BY e.event_id", where)]


def turns(conn):
    return conn.execute("SELECT * FROM turns ORDER BY turn_no").fetchall()


def recognized(conn):
    return {
        r[0]
        for r in conn.execute(
            "SELECT l.lemma FROM word_bank JOIN lexemes AS l USING (lexeme_id) "
            "WHERE mode = 'recognition'"
        )
    }


# --- Adherence: new words in the tutor's reply ------------------------------------------


def test_reply_within_the_bank_logs_seen_events_and_teaches_nothing(make_tutor, bank):
    tutor, generator = make_tutor("Hola, ¿comes en casa hoy?")
    turn = tutor.open()

    assert (turn.lessons, turn.retried, len(generator.requests)) == ([], False, 1)
    (row,) = turns(bank)
    assert (row["role"], row["draft_out_of_bank"], row["final_out_of_bank"], row["retried"]) == (
        "tutor",
        0,
        0,
        0,
    )
    assert {(lemma, t, g) for lemma, _, t, g, _ in events(bank)} == {
        (w, "seen", None) for w in ["hola", "comer", "en", "casa", "hoy"]
    }


def test_one_new_word_is_taught_without_a_retry(make_tutor, bank):
    tutor, generator = make_tutor("El perro come en casa.")
    turn = tutor.open()

    assert len(generator.requests) == 1
    assert [item.lemma for item in turn.lessons] == ["perro"]
    tutor_turn = turns(bank)[0]["turn_id"]
    assert ("perro", "recognition", "taught", None, tutor_turn) in events(bank)
    assert "perro" in recognized(bank)
    assert ("perro", "NOUN") in tutor.known


def test_two_new_words_trigger_exactly_one_retry(make_tutor, bank):
    tutor, generator = make_tutor("El perro nada en el río.", "El perro come.")
    turn = tutor.open()

    assert len(generator.requests) == 2
    retry_note = generator.requests[1][-1]
    assert isinstance(retry_note, SystemMessage)
    assert all(word in retry_note.content for word in ["perro", "nadar", "río"])
    assert (turn.retried, turn.draft_out_of_bank, [i.lemma for i in turn.lessons]) == (
        True,
        3,
        ["perro"],
    )
    row = turns(bank)[0]
    assert (row["draft_out_of_bank"], row["final_out_of_bank"], row["retried"]) == (3, 1, 1)
    assert [e[0] for e in events(bank, event_type="taught")] == ["perro"]
    assert {"nadar", "río"}.isdisjoint(e[0] for e in events(bank))  # only in the draft


def test_a_retry_that_still_has_new_words_teaches_them_all(make_tutor, bank):
    tutor, generator = make_tutor("El perro nada.", "El perro nada en el río.")
    turn = tutor.open()

    assert len(generator.requests) == 2  # never a second retry
    assert sorted(i.lemma for i in turn.lessons) == ["nadar", "perro", "río"]
    assert turns(bank)[0]["final_out_of_bank"] == 3


def test_names_and_non_words_are_never_counted_or_taught(make_tutor, bank):
    tutor, generator = make_tutor("María xyzzy come en casa.")
    turn = tutor.open()

    assert (turn.lessons, len(generator.requests)) == ([], 1)
    assert turns(bank)[0]["draft_out_of_bank"] == 0
    assert {e[0] for e in events(bank)} == {"comer", "en", "casa"}
    assert bank.execute("SELECT 1 FROM lexemes WHERE lemma = 'xyzzy'").fetchone() is None


def test_a_wiktionary_word_missing_from_the_lexicon_is_added_and_taught(make_tutor, bank):
    tutor, _ = make_tutor("El gato come pez.")
    turn = tutor.open()

    assert [(i.lemma, i.definition_en) for i in turn.lessons] == [("pez", "fish")]
    assert ("pez", "recognition", "taught") in [e[:3] for e in events(bank)]


def test_teaching_completeness_holds_in_sql(make_tutor, bank):
    """Every new word in a tutor turn has a `taught` event linked to that turn."""
    tutor, _ = make_tutor("El perro come.", "Yo nada.", "El perro nada en el río.")
    tutor.open()
    tutor.respond("Hola.")  # 'nada' as nadar: new, taught
    tutor.respond("Hola.")  # río new; perro and nadar now known

    gaps = bank.execute(
        """
        SELECT t.turn_id, t.final_out_of_bank, COUNT(e.event_id) AS taught
        FROM turns AS t
        LEFT JOIN word_events AS e ON e.turn_id = t.turn_id AND e.event_type = 'taught'
        WHERE t.role = 'tutor'
        GROUP BY t.turn_id
        HAVING t.final_out_of_bank <> COUNT(e.event_id)
        """
    ).fetchall()
    assert gaps == []
    assert [r["final_out_of_bank"] for r in turns(bank) if r["role"] == "tutor"] == [1, 1, 1]


# --- The learner's words ----------------------------------------------------------------


def test_learner_words_are_used_and_misused_words_get_a_lower_grade(make_tutor, bank):
    tutor, _ = make_tutor("Hola.", said("Yo estoy cansado hoy.", misused=["soy"]))
    tutor.open()
    tutor.respond("Yo soy cansado hoy.")

    learner_turn = next(r["turn_id"] for r in turns(bank) if r["role"] == "learner")
    used = {lemma: (grade, turn) for lemma, _, _, grade, turn in events(bank, event_type="used")}
    assert used == {
        "yo": (GRADE_USED, learner_turn),
        "ser": (GRADE_MISUSED, learner_turn),
        "cansado": (GRADE_USED, learner_turn),
        "hoy": (GRADE_USED, learner_turn),
    }


def test_misused_phrases_and_punctuation_still_match(make_tutor, bank):
    tutor, _ = make_tutor("Hola.", said("Yo estoy cansado.", misused=["Soy,"]))
    tutor.open()
    tutor.respond("Yo soy cansado.")
    grades = {lemma: grade for lemma, _, _, grade, _ in events(bank, event_type="used")}
    assert grades["ser"] == GRADE_MISUSED


def test_a_word_the_learner_uses_first_also_enters_recognition(make_tutor, bank):
    tutor, generator = make_tutor("Hola.", "El perro come en casa.")
    tutor.open()
    turn = tutor.respond("El perro.")

    assert {"used", "taught"} <= {e[2] for e in events(bank) if e[0] == "perro"}
    assert "perro" in recognized(bank)
    assert turn.lessons == []  # the tutor's use of perro is not "new"
    assert len(generator.requests) == 2


def test_missing_accent_credits_the_accented_word(make_tutor, bank):
    tutor, _ = make_tutor("Hola.", "Hola.")
    tutor.open()
    tutor.respond("El rio.")
    assert "río" in {e[0] for e in events(bank, event_type="used")}


def test_learner_non_words_are_reported_and_never_logged(make_tutor, bank):
    tutor, _ = make_tutor("Hola.", "Hola.")
    tutor.open()
    turn = tutor.respond("Yo sabo.")
    assert turn.not_words == ["sabo"]
    assert bank.execute("SELECT 1 FROM lexemes WHERE lemma = 'sabo'").fetchone() is None


# --- Looking words up ---------------------------------------------------------------------


def test_looking_up_a_known_word_is_a_recognition_miss(make_tutor, bank):
    tutor, _ = make_tutor("Hola.")
    tutor.open()
    item = tutor.look_up("gato")
    tutor_turn = turns(bank)[0]["turn_id"]
    assert item.lemma == "gato"
    assert ("gato", "recognition", "looked_up", GRADE_LOOKED_UP, tutor_turn) in events(bank)


def test_looking_up_an_unknown_word_teaches_it(make_tutor, bank):
    tutor, _ = make_tutor("Hola.")
    tutor.open()
    tutor.look_up("perro")
    assert ("perro", "taught") in [(e[0], e[2]) for e in events(bank)]
    assert [lex.lemma for lex in tutor.taught] == ["perro"]


def test_looking_up_a_bare_word_matches_the_headword_without_the_tagger(make_tutor, bank):
    # The real tagger calls a lone "perro" a proper noun; the headword match avoids it.
    tutor, _ = make_tutor("Hola.")
    tutor.analyze = lambda text: [(text, [])]  # a tagger that drops everything
    tutor.open()
    assert tutor.look_up("Perro").lemma == "perro"
    assert tutor.look_up("rio").lemma == "río"  # accents optional


def test_looking_up_an_inflected_form_uses_the_analyzer(make_tutor, bank):
    tutor, _ = make_tutor("Hola.")
    tutor.open()
    assert (tutor.look_up("comes").lemma, tutor.look_up("comes").pos) == ("comer", "VERB")


def test_looking_up_a_non_word_returns_none(make_tutor, bank):
    tutor, _ = make_tutor("Hola.")
    tutor.open()
    assert tutor.look_up("xyzzy") is None
    assert events(bank, event_type="looked_up") == []


# --- Prompt structure and history ---------------------------------------------------------


def test_system_prompt_is_byte_identical_across_turns(make_tutor):
    tutor, generator = make_tutor("El perro come.", "Hola.", "Hola.")
    tutor.open()  # teaches perro
    tutor.respond("Hola.")
    tutor.respond("Hola.")

    systems = [request[0] for request in generator.requests]
    assert all(s.content == systems[0].content for s in systems)
    assert "perro" not in systems[0].content[1]["text"]  # frozen at session start
    assert "Words taught this session: perro." in generator.requests[1][-1].content


def test_history_keeps_only_the_final_reply(make_tutor):
    tutor, generator = make_tutor("El perro nada en el río.", "El perro come.", "Hola.")
    tutor.open()
    tutor.respond("Hola.")

    history = generator.requests[-1][1:-2]  # between the system prompt and this turn
    assert [type(m) for m in history] == [HumanMessage, SystemMessage, AIMessage]
    assert history[2].content == "El perro come."
    assert "río" not in "".join(str(m.content) for m in history if isinstance(m, AIMessage))


def test_vocabulary_block_is_grouped_and_sorted():
    block = vocabulary_block({("gato", "NOUN"), ("casa", "NOUN"), ("comer", "VERB")})
    assert block.endswith("nouns: casa, gato\nverbs: comer")


def test_turn_metrics_sum_over_a_retry(make_tutor, bank):
    tutor, _ = make_tutor("El perro nada en el río.", "El perro come.")
    tutor.open()
    row = turns(bank)[0]
    assert (row["input_tokens"], row["cache_read_tokens"], row["output_tokens"]) == (200, 2000, 100)
    assert row["latency_ms"] >= 0


def test_retrieved_examples_go_in_the_turn_note_not_the_system_prompt(make_tutor, tmp_path):
    store = open_store(tmp_path / "chroma", embeddings=DeterministicFakeEmbedding(size=32))
    sentence = AnalyzedSentence(
        id=1,
        es="El gato come en casa.",
        en="The cat eats at home.",
        author="x",
        tokens=[(w, [FORMS[w]]) for w in ["el", "gato", "come", "en", "casa"]],
    )
    index_sentences(store, [sentence], report=lambda _: None)
    tutor, generator = make_tutor("Hola.", store=store)
    tutor.open()

    request = generator.requests[0]
    assert "El gato come en casa." in request[-1].content
    assert "El gato come en casa." not in str(request[0].content)
