"""The conversation loop, with a scripted model and a deterministic analyzer (no API, no
spaCy). Both live in fakes.py, shared with the API tests."""

import pytest
from fakes import FORMS, Scripted, analyze, make_topic_store, notes, said, seed_bank
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from spanish_tutor.conversation import (
    FAREWELL_NOTE,
    GOODBYE_MESSAGE,
    Ending,
    Tutor,
    clean_note,
    is_farewell,
    parse_translation_request,
    reply_to,
    summary_prompt,
    vocabulary_block,
)
from spanish_tutor.ingest.index_tatoeba import index_sentences
from spanish_tutor.ingest.tatoeba import AnalyzedSentence
from spanish_tutor.topics import TopicWords
from spanish_tutor.vectorstore import open_store
from spanish_tutor.words import GRADE_MISUSED, GRADE_USED, LexiconIndex


@pytest.fixture
def bank(conn):
    seed_bank(conn)
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
        LEFT JOIN word_events AS e
               ON e.turn_id = t.turn_id AND e.event_type = 'taught' AND e.source = 'conversation'
        WHERE t.role = 'tutor' AND t.kind = 'conversation'
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


def test_a_wrong_word_the_learner_didnt_know_is_neither_credited_nor_taught(make_tutor, bank):
    # "jugo" (juice) for "juego": the learner meant another word.
    tutor, _ = make_tutor("Hola.", said("¡Qué bien!", wrong_words=["perro"]))
    tutor.open()
    tutor.respond("El perro come.")
    assert [e for e in events(bank) if e[0] == "perro"] == []
    assert "perro" not in recognized(bank)
    assert {"comer"} <= {e[0] for e in events(bank, event_type="used")}  # the rest counts


def test_a_wrong_word_never_creates_a_production_entry(make_tutor, bank):
    tutor, _ = make_tutor("Hola.", said("¡Bien!", wrong_words=["gato"]))  # known, not produced
    tutor.open()
    tutor.respond("El gato come.")
    assert [e for e in events(bank, event_type="used") if e[0] == "gato"] == []
    produced = bank.execute(
        "SELECT COUNT(*) FROM word_bank JOIN lexemes USING (lexeme_id) "
        "WHERE lemma = 'gato' AND mode = 'production'"
    ).fetchone()[0]
    assert produced == 0


def test_a_wrong_word_already_produced_gets_the_low_grade(make_tutor, bank):
    bank.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source, grade) "
        "SELECT lexeme_id, 'production', 'used', 'seed', 4 FROM lexemes WHERE lemma = 'ser'"
    )
    tutor, _ = make_tutor("Hola.", said("Yo estoy cansado.", wrong_words=["soy"]))  # ser/estar
    tutor.open()
    tutor.respond("Yo soy cansado.")
    grades = {lemma: grade for lemma, _, _, grade, _ in events(bank, event_type="used")}
    assert grades["ser"] == GRADE_MISUSED and grades["cansado"] == GRADE_USED


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


def test_looking_up_a_known_word_is_a_free_reminder(make_tutor, bank):
    # Not a recognition miss: the learner isn't penalized for using a reminder.
    tutor, _ = make_tutor("Hola.")
    tutor.open()
    before = bank.execute("SELECT COUNT(*) FROM word_events").fetchone()[0]
    item = tutor.look_up("gato")
    assert item.lemma == "gato" and item.definition_en == "<gato>"
    assert bank.execute("SELECT COUNT(*) FROM word_events").fetchone()[0] == before


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


@pytest.mark.parametrize(
    "raw, clean",
    [
        (
            'Say "Dos al frente y tres detrás de mi casa."}',
            'Say "Dos al frente y tres detrás de mi casa."',
        ),
        (
            "Use 'le gusta verlo' or 'le gusta mirarlo'.'",
            "Use 'le gusta verlo' or 'le gusta mirarlo'.",
        ),
        ('Say "estoy cansado"', 'Say "estoy cansado"'),  # a closing quote that belongs
        ("  \n", None),
        (None, None),
    ],
)
def test_notes_lose_stray_trailing_characters(raw, clean):
    assert clean_note(raw) == clean


# --- "¿Cómo se dice ...?" ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, phrase",
    [
        ('Como se dice "I remove weeds and water plants"?', "I remove weeds and water plants"),
        ('¿Cómo se dice "rake" en español?', "rake"),
        ("?C'omo se dice «to water» en espa~nol?", "to water"),
        ("como se dice rake", "rake"),
        ("Como se dice 'how are you?'", "how are you?"),
        ("cómo se dice “weeds” en espanol", "weeds"),
    ],
)
def test_translation_requests_are_recognized(text, phrase):
    assert parse_translation_request(text) == phrase


@pytest.mark.parametrize(
    "text", ["Me gusta el jardín.", "No sé cómo se dice eso.", 'Como se dice ""?', "Cómo estás?"]
)
def test_other_messages_are_not_translation_requests(text):
    assert parse_translation_request(text) is None


QUESTION = 'Como se dice "the dog swims in the river"?'


def test_translation_teaches_every_new_word_and_credits_nothing(make_tutor, bank):
    tutor, generator = make_tutor("Hola, ¿comes en casa?", "El perro nada en el río.")
    tutor.open()
    turn = tutor.translate(QUESTION, "the dog swims in the river")

    assert turn.spanish == "El perro nada en el río."
    assert sorted(item.lemma for item in turn.lessons) == ["nadar", "perro", "río"]  # no limit
    assert turn.pending == "Hola, ¿comes en casa?"
    assert len(generator.requests) == 2  # no retry for a translation
    tutor_turn = turns(bank)[-1]["turn_id"]
    assert sorted(e[0] for e in events(bank, event_type="taught")) == ["nadar", "perro", "río"]
    assert all(e[4] == tutor_turn for e in events(bank, event_type="taught"))
    assert events(bank, event_type="used") == []  # the English is never credited
    assert {"nadar", "perro", "río"} <= recognized(bank)


def test_translation_turns_are_marked_and_skip_adherence_metrics(make_tutor, bank):
    tutor, _ = make_tutor("Hola.", said("El perro nada.", correction="(why)"))
    tutor.open()
    tutor.translate(QUESTION, "the dog swims")

    learner, tutor_row = turns(bank)[-2:]
    assert (learner["role"], learner["kind"], learner["text_es"]) == (
        "learner",
        "translation",
        QUESTION,
    )
    assert (tutor_row["kind"], tutor_row["note_en"]) == ("translation", "(why)")
    assert tutor_row["draft_out_of_bank"] is None and tutor_row["retried"] is None


def test_the_translation_note_names_the_phrase_and_the_conversation_resumes(make_tutor):
    tutor, generator = make_tutor("Hola.", "El perro nada.", "¿Tu perro nada?")
    tutor.open()
    tutor.translate(QUESTION, "the dog swims")
    turn = tutor.respond("El perro nada.")

    translate_request = generator.requests[1]
    # Same system prefix (and, in ClaudeGenerator, the same output schema) as a
    # conversation reply, so a translation reads the conversation's prompt cache.
    assert translate_request[0].content == generator.requests[0][0].content
    assert isinstance(translate_request[-1], SystemMessage)
    assert '"the dog swims"' in translate_request[-1].content
    next_request = generator.requests[2]
    assert [m.content for m in next_request[-5:-2]][-1] == "El perro nada."  # in the history
    assert "Words taught this session: perro, nadar." in next_request[-1].content
    assert turn.lessons == []  # perro and nadar are known now


# --- Pre-teaching topic words -------------------------------------------------------------


@pytest.fixture
def topic_store(bank, tmp_path):
    return make_topic_store(bank, tmp_path / "chroma")


def test_pre_taught_words_are_taught_before_the_conversation_and_logged_with_it(
    make_tutor, bank, topic_store
):
    choice = TopicWords(words=["perro|NOUN", "nadar|VERB"], fewer_because=None)
    tutor, generator = make_tutor(choice, "Hola. El perro nada.", store=topic_store)
    lessons = tutor.pre_teach(2)
    turn = tutor.open()

    assert [item.lemma for item in lessons] == ["perro", "nadar"]
    assert "perro|NOUN" in generator.requests[0] and "río|NOUN" in generator.requests[0]
    assert turn.lessons == []  # the opening uses them without counting them as new
    opening = turns(bank)[0]
    pre_taught = [e for e in events(bank) if e[2] == "taught"]
    assert pre_taught == []  # events() shows source 'conversation' only
    rows = bank.execute(
        "SELECT l.lemma, e.turn_id FROM word_events AS e JOIN lexemes AS l USING (lexeme_id) "
        "WHERE e.source = 'pre_teach'"
    ).fetchall()
    assert sorted(tuple(r) for r in rows) == [
        ("nadar", opening["turn_id"]),
        ("perro", opening["turn_id"]),
    ]
    assert {"perro", "nadar"} <= recognized(bank)


def test_pre_taught_words_are_in_the_frozen_vocabulary_and_every_note(make_tutor, topic_store):
    choice = TopicWords(words=["perro|NOUN", "nadar|VERB"], fewer_because=None)
    tutor, generator = make_tutor(choice, "Hola.", "Bien.", store=topic_store)
    tutor.pre_teach(2)
    tutor.open()
    tutor.respond("Hola.")

    opening_request, reply_request = generator.requests[1], generator.requests[2]
    vocabulary = opening_request[0].content[1]["text"]
    assert "perro" in vocabulary and "nadar" in vocabulary
    assert "perro, nadar" in opening_request[-2].text  # the opening message
    for request in (opening_request, reply_request):
        note = request[-1].content
        assert "taught for today's topic, before the conversation: perro, nadar." in note
        # The words are for the learner to practice; the tutor uses some, not all.
        assert "invite the learner to use them" in note and "don't need to use them all" in note
        assert "Words taught this session: none yet." in request[-1].content


def test_pre_teaching_needs_a_topic_and_must_come_before_the_opening(make_tutor, topic_store):
    tutor, _ = make_tutor("Hola.", store=topic_store, topic=None)
    assert tutor.pre_teach(3) == []
    tutor, _ = make_tutor("Hola.", store=topic_store)
    tutor.open()
    assert tutor.pre_teach(3) == []  # the vocabulary is frozen once the conversation opens


def test_fewer_topic_words_than_requested_come_with_a_reason(make_tutor, topic_store):
    choice = TopicWords(words=["perro|NOUN"], fewer_because="Only perro is about animals.")
    tutor, _ = make_tutor(choice, "Hola.", store=topic_store)
    lessons = tutor.pre_teach(5)
    assert [item.lemma for item in lessons] == ["perro"]
    # 3 candidates for 5 requested, and Claude chose 1 of them.
    assert tutor.pre_teach_shortfall == (
        "Only 3 words about “los animales” turned up that you don't know yet. "
        "Only perro is about animals."
    )


def test_all_requested_topic_words_means_no_shortfall(make_tutor, topic_store):
    choice = TopicWords(words=["perro|NOUN", "nadar|VERB"], fewer_because=None)
    tutor, _ = make_tutor(choice, "Hola.", store=topic_store)
    tutor.pre_teach(2)
    assert tutor.pre_teach_shortfall is None


def test_a_turn_reports_the_words_the_learner_used(make_tutor):
    tutor, _ = make_tutor("Hola.", "¡Bien!")
    tutor.open()
    assert tutor.respond("Yo estoy cansado hoy.").used == ["yo", "estar", "cansado", "hoy"]


# --- Ending a conversation ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "ends"),
    [
        ("¡Hasta luego!", True),
        ("Bueno, tengo que irme. ¡Adiós!", True),
        ("Gracias, nos vemos", True),
        ("¡Chao, profesor!", True),
        ("adios", True),  # accents optional
        ("hasta manana!", True),
        ("Hasta la próxima vez.", True),
        ("Mi abuela dice hasta luego a todos.", False),  # not at the end
        ("¿Cómo se dice adiós?", False),
        ("chaos", False),
    ],
)
def test_a_message_ending_with_a_goodbye_ends_the_conversation(text, ends):
    assert is_farewell(text) is ends


def test_a_typed_goodbye_is_a_turn_then_the_summary_is_stored(make_tutor, bank):
    tutor, generator = make_tutor(
        "Hola.", said("Adiós, hasta pronto.", correction="Say 'cansado'."), notes()
    )
    tutor.open()
    ending = tutor.end("Yo estoy cansado. ¡Adiós!")

    assert isinstance(ending, Ending) and tutor.ended
    assert ending.turn.reply_es == "Adiós, hasta pronto."
    assert FAREWELL_NOTE in generator.requests[1][-1].content  # no question, just goodbye
    assert {"yo", "estar", "cansado"} <= set(ending.turn.used)  # the goodbye's words count
    assert (ending.went_well_en, ending.work_on) == (
        "You asked good questions.",
        ["Practice estar."],
    )
    assert ending.stats["messages"] == 1 and ending.stats["corrections"] == 1
    assert sorted(ending.stats["first_time"]) == ["cansado", "estar", "yo"]
    session = bank.execute("SELECT ended_at FROM sessions").fetchone()
    assert session["ended_at"] is not None
    row = bank.execute("SELECT went_well_en, work_on_en, output_tokens FROM session_summaries")
    assert tuple(row.fetchone()) == ("You asked good questions.", "Practice estar.", 120)


def test_the_summary_request_has_the_transcript_notes_and_numbers(make_tutor):
    tutor, generator = make_tutor("Hola.", said("¡Adiós!", correction="Use estar."), notes())
    tutor.open()
    tutor.end("Yo soy cansado. Adiós.")
    prompt = generator.requests[-1]
    assert "Learner: Yo soy cansado. Adiós." in prompt
    assert "(note to the learner: Use estar.)" in prompt
    assert "Corrections given: 1" in prompt and "don't invent mistakes" in prompt


def test_ending_without_a_goodbye_credits_no_words(make_tutor, bank):
    tutor, generator = make_tutor("Hola.", "¡Hasta luego!", notes())
    tutor.open()
    ending = tutor.end()  # the app's button

    assert generator.requests[1][-2].text == GOODBYE_MESSAGE
    assert ending.turn.used == []
    assert bank.execute("SELECT COUNT(*) FROM turns WHERE role = 'learner'").fetchone()[0] == 0
    assert events(bank, event_type="used") == []


def test_a_failed_summary_still_ends_the_conversation_with_its_stats(make_tutor, bank):
    tutor, _ = make_tutor("Hola.", "Adiós.", RuntimeError("overloaded"))
    tutor.open()
    ending = tutor.end()
    assert (ending.went_well_en, ending.notes_error) == (None, "overloaded")
    assert ending.stats["messages"] == 0
    assert bank.execute("SELECT ended_at FROM sessions").fetchone()[0] is not None
    assert bank.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0] == 0


def test_messages_are_routed_to_the_ending_by_their_goodbye(make_tutor):
    tutor, _ = make_tutor("Hola.", "¡Bien!", "Adiós.", notes())
    tutor.open()
    _, turn = reply_to(tutor, "Estoy cansado hoy.")
    assert not isinstance(turn, Ending)
    written, ending = reply_to(tutor, "Bueno, me voy. ¡Adi'os!")  # a typed marker
    assert written == "Bueno, me voy. ¡Adiós!" and isinstance(ending, Ending)


def test_summary_prompt_lists_todays_unused_words():
    stats = {
        "messages": 3, "corrections": 0, "how_to_say": 1, "first_time": [],
        "pre_taught": ["regar", "césped"], "pre_taught_used": ["regar"],
    }  # fmt: skip
    prompt = summary_prompt("el jardín", [], stats)
    assert "Not used by the learner: césped." in prompt and "about el jardín" in prompt


# --- Prompt caching of the conversation --------------------------------------------------------


def cache_marks(request):
    """Where a request marks cache breakpoints: (message index, ttl or '5m')."""
    marks = []
    for i, message in enumerate(request):
        if isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, dict) and "cache_control" in block:
                    marks.append((i, block["cache_control"].get("ttl", "5m")))
    return marks


def test_the_latest_message_is_the_conversation_cache_breakpoint(make_tutor):
    tutor, generator = make_tutor("Hola.", "¡Bien!", "¿Y hoy?")
    tutor.open()
    tutor.respond("Estoy cansado.")
    tutor.respond("Hoy como en casa.")

    for request in generator.requests:
        # Two breakpoints: the system prompt (1 hour), then the newest message (5 minutes),
        # the order the API requires (longer lifetimes first).
        latest = max(i for i, m in enumerate(request) if isinstance(m, HumanMessage))
        assert cache_marks(request) == [(0, "1h"), (latest, "5m")]
    # Earlier messages are sent unmarked but otherwise identical, so the next request's
    # prefix matches what the previous one cached.
    previous, current = generator.requests[1], generator.requests[2]
    assert current[4].content == [{"type": "text", "text": "Estoy cansado."}]
    assert previous[4].content[0]["text"] == current[4].content[0]["text"]


def test_cache_writes_are_stored_by_lifetime(make_tutor, bank):
    tutor, _ = make_tutor("Hola.")
    tutor.open()
    row = turns(bank)[0]
    assert (row["cache_write_5m_tokens"], row["cache_write_1h_tokens"]) == (60, 0)
