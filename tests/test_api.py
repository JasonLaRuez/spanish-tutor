"""The web API, with a scripted model and the fake analyzer (no Claude, no spaCy).

Each test gets a temporary database file: the read-only endpoints open their own
connections, which an in-memory database can't share.
"""

import pytest
from fakes import (
    KNOWN,
    Scripted,
    analyze,
    fake_speaker,
    make_topic_store,
    notes,
    said,
    seed_bank,
)
from fastapi.testclient import TestClient

from spanish_tutor import db
from spanish_tutor.api.app import create_app
from spanish_tutor.conversation import Resources
from spanish_tutor.topics import TopicWords
from spanish_tutor.words import LexiconIndex


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "word_bank.db"
    conn = db.connect(path)
    db.init_schema(conn)
    seed_bank(conn)
    conn.close()
    return path


@pytest.fixture
def serve(db_path, tmp_path, make_wiktionary):
    """serve(*replies) -> (TestClient, the scripted model). The client runs the app's startup."""
    wiktionary = make_wiktionary([("pez", "noun", "fish")])
    clients = []

    def make(*replies, web_dist=tmp_path / "no-ui", topics=False, translate=None, speaker=None):
        generator = Scripted(*replies)
        store = None
        if topics:  # sentences about a dog swimming, for topic pre-teaching
            conn = db.connect(db_path)
            store = make_topic_store(conn, tmp_path / "chroma")
            conn.close()

        def load():
            conn = db.connect(db_path, check_same_thread=False)
            return Resources(
                conn, generator, LexiconIndex(conn, wiktionary), analyze, store, translate
            )

        speaker = speaker or fake_speaker(tmp_path / "voices")[0]
        app = create_app(load=load, db_path=db_path, web_dist=web_dist, speaker=speaker)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client, generator

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def start(client, **body):
    response = client.post("/api/sessions", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def rows(db_path, sql):
    conn = db.connect(db_path)
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


# --- Conversations ------------------------------------------------------------------------


def test_starting_a_session_returns_the_opening_and_records_it(serve, db_path):
    client, _ = serve("Hola, ¿comes en casa hoy?")
    started = start(client, topic="el jard'in")

    assert started["topic"] == "el jardín"  # accent markers expanded, as in the CLI
    assert started["lessons"] == []
    assert started["opening"]["kind"] == "conversation"
    assert started["opening"]["reply_es"] == "Hola, ¿comes en casa hoy?"
    assert rows(db_path, "SELECT session_id, role, text_es FROM turns") == [
        (started["session_id"], "tutor", "Hola, ¿comes en casa hoy?")
    ]


def test_a_session_reports_topic_words_and_why_fewer_than_requested(serve):
    choice = TopicWords(
        words=["perro|NOUN", "nadar|VERB"], practice=[], fewer_because="Only two fit."
    )
    client, _ = serve(choice, "Hola.", topics=True)
    started = start(client, topic="los animales", new_words=5)

    assert [lesson["lemma"] for lesson in started["lessons"]] == ["perro", "nadar"]
    assert started["requested_words"] == 5
    assert started["shortfall"] == (
        "Only 3 words about “los animales” turned up that you don't know yet. Only two fit."
    )
    opening = client.get(f"/api/sessions/{started['session_id']}").json()["turns"][0]
    assert opening["pre_taught"] == ["perro", "nadar"]  # restores the checklist on reload


def test_a_session_with_every_requested_word_has_no_shortfall(serve):
    choice = TopicWords(words=["perro|NOUN", "nadar|VERB"], practice=[], fewer_because=None)
    client, _ = serve(choice, "Hola.", topics=True)
    started = start(client, topic="los animales", new_words=2)
    assert (started["requested_words"], started["shortfall"]) == (2, None)


def test_a_gap_in_topic_words_is_filled_with_practice_words(serve, db_path):
    conn = db.connect(db_path)
    conn.execute(  # río recognized from reading, never used
        "INSERT INTO word_events (lexeme_id, mode, event_type, source) "
        "SELECT lexeme_id, 'recognition', 'taught', 'reading' FROM lexemes WHERE lemma = 'río'"
    )
    conn.commit()
    conn.close()
    choice = TopicWords(words=["perro|NOUN"], practice=["río|NOUN"], fewer_because=None)
    client, _ = serve(choice, "Hola.", topics=True)
    started = start(client, topic="los animales", new_words=2)

    assert [(item["lemma"], item["practice"]) for item in started["lessons"]] == [
        ("perro", False),
        ("río", True),
    ]
    assert started["shortfall"] is None
    opening = client.get(f"/api/sessions/{started['session_id']}").json()["turns"][0]
    assert opening["pre_taught"] == ["perro", "río"]  # both on today's checklist after a reload


def test_a_message_expands_markers_and_logs_the_learners_words(serve, db_path):
    client, _ = serve("Hola.", said("¡Qué bien!", correction="Say 'estoy'."))
    session = start(client)["session_id"]

    reply = client.post(f"/api/sessions/{session}/messages", json={"text": "?yo estoy cansado"})

    body = reply.json()
    assert body["written"] == "¿yo estoy cansado"
    assert body["turn"]["kind"] == "conversation"
    assert body["turn"]["note_en"] == "Say 'estoy'."
    used = rows(
        db_path,
        "SELECT l.lemma FROM word_events e JOIN lexemes l USING (lexeme_id) "
        "WHERE e.event_type = 'used' ORDER BY l.lemma",
    )
    assert used == [("cansado",), ("estar",), ("yo",)]
    assert body["turn"]["used"] == ["yo", "estar", "cansado"]  # for the word checklist


def test_how_to_say_is_answered_as_a_translation_and_teaches_its_words(serve, db_path):
    client, _ = serve("Hola.", said("el perro", correction="Perro is masculine."))
    session = start(client)["session_id"]

    body = client.post(
        f"/api/sessions/{session}/messages", json={"text": '¿Cómo se dice "the dog"?'}
    ).json()

    turn = body["turn"]
    assert (turn["kind"], turn["reply_es"], turn["pending"]) == ("translation", "el perro", "Hola.")
    assert [lesson["lemma"] for lesson in turn["lessons"]] == ["perro"]
    assert body["taught"] == ["perro"]
    assert rows(db_path, "SELECT DISTINCT kind FROM turns WHERE turn_no > 1") == [("translation",)]


def test_a_known_word_can_be_looked_up_and_a_non_word_is_404(serve, db_path):
    client, _ = serve("Hola.")
    session = start(client)["session_id"]

    found = client.post(f"/api/sessions/{session}/lookup", json={"word": "gato"})
    assert (found.status_code, found.json()["definition_en"]) == (200, "<gato>")
    assert found.json()["model_written"] is False
    assert rows(db_path, "SELECT COUNT(*) FROM word_events WHERE event_type = 'looked_up'") == [
        (0,)
    ]  # a reminder, not a miss
    assert client.post(f"/api/sessions/{session}/lookup", json={"word": "xyzzy"}).status_code == 404


def test_a_conversation_the_server_doesnt_have_open_is_404(serve):
    client, _ = serve()
    response = client.post("/api/sessions/99/messages", json={"text": "hola"})
    assert response.status_code == 404


@pytest.mark.parametrize("new_words", [1, 21, -1])
def test_new_word_counts_outside_2_to_20_are_rejected(serve, new_words):
    client, _ = serve()
    response = client.post("/api/sessions", json={"topic": "x", "new_words": new_words})
    assert response.status_code == 422


def test_an_empty_message_is_rejected(serve):
    client, _ = serve("Hola.")
    session = start(client)["session_id"]
    assert client.post(f"/api/sessions/{session}/messages", json={"text": ""}).status_code == 422


# --- History and progress -------------------------------------------------------------------


def test_history_lists_the_session_and_its_transcript(serve):
    client, _ = serve("Hola.", said("¡Qué bien!", correction="Say 'estoy'."), said("el perro"))
    session = start(client, topic="yo")["session_id"]
    client.post(f"/api/sessions/{session}/messages", json={"text": "yo estoy cansado"})
    client.post(f"/api/sessions/{session}/messages", json={"text": '¿Cómo se dice "dog"?'})

    (summary,) = client.get("/api/sessions").json()
    assert summary["session_id"] == session and summary["active"] is True
    assert (summary["messages"], summary["how_to_say"], summary["corrections"]) == (1, 1, 1)
    assert (summary["words_taught"], summary["words_used"]) == (1, 3)

    transcript = client.get(f"/api/sessions/{session}").json()
    assert [(t["role"], t["kind"]) for t in transcript["turns"]] == [
        ("tutor", "conversation"),
        ("learner", "conversation"),
        ("tutor", "conversation"),
        ("learner", "translation"),
        ("tutor", "translation"),
    ]
    assert transcript["turns"][1]["used"] == ["yo", "estar", "cansado"]
    assert transcript["turns"][4]["taught"] == ["perro"]
    assert client.get("/api/sessions/99").status_code == 404


def test_progress_counts_the_word_bank_and_its_growth(serve):
    client, _ = serve("Hola.", "¡Bien!")
    before = client.get("/api/progress").json()
    assert (before["recognition"], before["production"]) == (len(KNOWN), 0)

    session = start(client)["session_id"]
    client.post(f"/api/sessions/{session}/messages", json={"text": "yo estoy cansado"})

    after = client.get("/api/progress").json()
    assert after["production"] == 3
    recognition = [g for g in after["growth"] if g["mode"] == "recognition"]
    assert [(g["session_id"], g["running_total"]) for g in recognition] == [(None, len(KNOWN))]
    production = [g for g in after["growth"] if g["mode"] == "production"]
    assert [(g["session_id"], g["words_added"], g["running_total"]) for g in production] == [
        (session, 3, 3)
    ]
    assert {w["lemma"] for w in after["try_using"]} == set(KNOWN) - {"yo", "estar", "cansado"}


# --- The web UI ---------------------------------------------------------------------------------


def test_the_built_ui_is_served_with_a_fallback_for_its_own_routes(serve, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>tutor</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    client, _ = serve(web_dist=dist)

    assert client.get("/").text == "<html>tutor</html>"
    assert client.get("/history/3").text == "<html>tutor</html>"  # a UI route, reloaded
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/health").json() == {"ok": True}
    assert "tutor" in client.get("/..%2Fword_bank.db").text  # never a file outside dist


# --- Ending a conversation ---------------------------------------------------------------------


def test_a_goodbye_message_ends_the_conversation_with_a_summary(serve):
    client, _ = serve("Hola.", said("¡Hasta pronto!", correction="Use estar."), notes())
    session = start(client)["session_id"]

    body = client.post(
        f"/api/sessions/{session}/messages", json={"text": "Yo estoy cansado. ¡Hasta luego!"}
    ).json()

    assert body["turn"]["reply_es"] == "¡Hasta pronto!"
    summary = body["summary"]
    assert (summary["messages"], summary["corrections"]) == (1, 1)
    assert sorted(summary["first_time"]) == ["cansado", "estar", "yo"]
    assert summary["went_well_en"] == "You asked good questions."
    assert summary["work_on"] == ["Practice estar."]
    # Ended: closed on the server, marked in history, summary kept with the transcript.
    assert (
        client.post(f"/api/sessions/{session}/messages", json={"text": "hola"}).status_code == 404
    )
    (listed,) = client.get("/api/sessions").json()
    assert listed["ended_at"] is not None and listed["active"] is False
    transcript = client.get(f"/api/sessions/{session}").json()
    assert transcript["summary"]["went_well_en"] == "You asked good questions."
    assert transcript["summary"]["first_time"] == summary["first_time"]


def test_the_end_button_ends_without_a_learner_turn(serve, db_path):
    client, _ = serve("Hola.", "¡Hasta luego!", notes())
    session = start(client)["session_id"]

    body = client.post(f"/api/sessions/{session}/end").json()

    assert body["turn"]["reply_es"] == "¡Hasta luego!"
    assert body["summary"]["messages"] == 0 and body["summary"]["work_on"] == ["Practice estar."]
    assert rows(db_path, "SELECT role FROM turns") == [("tutor",), ("tutor",)]
    assert client.post(f"/api/sessions/{session}/end").status_code == 404  # already ended


def test_an_open_conversation_has_no_summary(serve):
    client, _ = serve("Hola.")
    session = start(client)["session_id"]
    transcript = client.get(f"/api/sessions/{session}").json()
    assert transcript["summary"] is None and transcript["session"]["ended_at"] is None


# --- Content and recommendations ---------------------------------------------------------


@pytest.fixture
def no_ceiling(monkeypatch):
    """These tests use mostly unknown words; the ceiling is tested in test_recommend."""
    from spanish_tutor import recommend

    monkeypatch.setattr(recommend, "MAX_UNKNOWN_SHARE", 1.0)


def add_indexed(db_path, title, lemmas, kind="song", book=None):
    """An indexed item whose vocabulary is `lemmas` (each once); a chapter if `book` is
    (title, chapter_no). Returns its content_id."""
    from spanish_tutor import content

    conn = db.connect(db_path)
    with conn:
        if book:
            book_id = conn.execute(
                "SELECT book_id FROM books WHERE title = ?", (book[0],)
            ).fetchone()
            if book_id is None:
                book_id, _ = content.add_book(
                    conn, book[0], [], source="gutenberg:1", is_private=False
                )
            else:
                book_id = book_id[0]
            content_id = conn.execute(
                "INSERT INTO content_items (kind, title, book_id, chapter_no, text_es) "
                "VALUES ('chapter', ?, ?, ?, ?)",
                (title, book_id, book[1], f"Texto de {title}."),
            ).lastrowid
        else:
            content_id = content.add_item(
                conn, kind, title, f"Texto de {title}.", source="private", is_private=True
            )
        ids = [
            conn.execute("SELECT lexeme_id FROM lexemes WHERE lemma = ?", (lemma,)).fetchone()[0]
            for lemma in lemmas
        ]
        conn.executemany(
            "INSERT INTO content_vocab VALUES (?, ?, 1)", [(content_id, i) for i in ids]
        )
        conn.execute(
            "UPDATE content_items SET tokens = ?, unresolved_tokens = 0, analyzer = 't', "
            "indexed_at = CURRENT_TIMESTAMP WHERE content_id = ?",
            (len(ids), content_id),
        )
    conn.close()
    return content_id


def test_recommendations_rank_songs_by_new_words_and_books_with_their_next_chapter(
    serve, db_path, no_ceiling
):
    add_indexed(db_path, "Difícil", ["gato", "perro", "nadar"])
    add_indexed(db_path, "Fácil", ["gato", "casa", "perro"])
    add_indexed(db_path, "Capítulo uno", ["gato"], book=("Libro", 1))
    add_indexed(db_path, "Capítulo dos", ["río"], book=("Libro", 2))
    client, _ = serve()

    found = client.get("/api/recommend").json()

    assert [(i["title"], i["new_words"]) for i in found["items"]] == [("Fácil", 1), ("Difícil", 2)]
    (libro,) = found["books"]
    assert (libro["title"], libro["state"], libro["next_chapter_title"]) == (
        "Libro",
        "new",
        "Capítulo uno",
    )
    assert libro["density"] == pytest.approx(0.5)


def test_starting_and_finishing_moves_a_book_to_its_next_chapter(serve, db_path, no_ceiling):
    first = add_indexed(db_path, "Capítulo uno", ["gato"], book=("Libro", 1))
    add_indexed(db_path, "Capítulo dos", ["río"], book=("Libro", 2))
    client, _ = serve()

    assert client.post(f"/api/content/{first}/start", json={"chosen_via": "recommended"}).is_success
    assert client.post(f"/api/content/{first}/finish").is_success

    libro = client.get("/api/recommend").json()["books"][0]
    assert (libro["state"], libro["next_chapter_no"]) == ("in progress", 2)
    assert rows(db_path, "SELECT event, chosen_via FROM content_events ORDER BY event_id") == [
        ("started", "recommended"),
        ("finished", None),
    ]


def test_a_start_must_say_how_the_item_was_chosen_and_name_a_real_item(serve, db_path):
    song = add_indexed(db_path, "Canción", ["gato"])
    client, _ = serve()
    assert client.post(f"/api/content/{song}/start", json={"chosen_via": "whim"}).status_code == 422
    assert client.post("/api/content/99/start", json={"chosen_via": "requested"}).status_code == 404
    assert client.post("/api/content/99/finish").status_code == 404
    assert rows(db_path, "SELECT COUNT(*) FROM content_events") == [(0,)]


def test_an_item_comes_with_its_text_and_new_words(serve, db_path):
    song = add_indexed(db_path, "Canción", ["gato", "perro", "río"])
    client, _ = serve()

    detail = client.get(f"/api/content/{song}").json()

    assert (detail["title"], detail["text_es"], detail["chapters"]) == (
        "Canción",
        "Texto de Canción.",
        0,
    )
    assert sorted(w["lemma"] for w in detail["new_words"]) == ["perro", "río"]
    assert (detail["started"], detail["finished"]) == (False, False)
    assert client.get("/api/content/99").status_code == 404


def test_the_catalog_and_a_surprise(serve, db_path, no_ceiling):
    client, _ = serve()
    assert client.get("/api/recommend/surprise").status_code == 404  # nothing to read yet
    assert client.get("/api/content").json() == []

    song = add_indexed(db_path, "Canción", ["gato", "perro"])
    pick = client.get("/api/recommend/surprise").json()
    assert (pick["content_id"], pick["kind"], pick["new_words"]) == (song, "song", 1)
    (entry,) = client.get("/api/content").json()
    assert (entry["title"], entry["new_words"], entry["state"]) == ("Canción", 1, None)


def test_items_over_the_ceiling_are_listed_apart(serve, db_path):
    from spanish_tutor import recommend

    add_indexed(db_path, "Conocida", ["gato", "casa"])  # all known
    add_indexed(db_path, "Difícil", ["gato", "perro"])  # half unknown: over 10%
    add_indexed(db_path, "Capítulo uno", ["perro"], book=("Libro", 1))
    client, _ = serve()

    found = client.get("/api/recommend").json()

    assert [i["title"] for i in found["items"]] == ["Conocida"]
    assert [i["title"] for i in found["too_hard_items"]] == ["Difícil"]
    assert found["too_hard_items"][0]["unknown_share"] == pytest.approx(0.5)
    assert (found["books"], [b["title"] for b in found["too_hard_books"]]) == ([], ["Libro"])
    assert found["max_unknown_share"] == recommend.MAX_UNKNOWN_SHARE


# --- Reading -----------------------------------------------------------------------------


def add_story(db_path, text):
    from spanish_tutor import content

    conn = db.connect(db_path)
    with conn:
        content_id = content.add_item(
            conn, "story", "Cuento", text, source="gutenberg:1", is_private=False
        )
    conn.close()
    return content_id


STORY = "El gato come en casa.\n\nEl perro nada en el río."


def test_a_reading_session_studies_the_new_words_then_reads(serve, db_path):
    story = add_story(db_path, STORY)
    client, _ = serve()

    started = client.post("/api/reading", json={"content_id": story, "chosen_via": "recommended"})
    assert started.status_code == 200, started.text
    state = started.json()
    session = state["session_id"]
    assert state["paragraphs"] == [["El gato come en casa."], ["El perro nada en el río."]]
    assert (state["total_new"], state["remaining"], state["readable_until"]) == (3, 3, 1)
    assert set(state["unstudied"]) == {"perro", "nada", "río"}

    batch = client.get(f"/api/reading/{session}/batch", params={"n": 2}).json()
    assert [w["lesson"]["lemma"] for w in batch["words"]] == ["perro", "nadar"]
    assert batch["words"][0]["context"] == "El perro nada en el río."
    state = client.post(
        f"/api/reading/{session}/study",
        json={"lexeme_ids": [w["lexeme_id"] for w in batch["words"]]},
    ).json()
    assert (state["remaining"], state["unstudied"]) == (1, ["río"])

    looked = client.post(f"/api/reading/{session}/lookup", json={"word": "río"}).json()
    assert looked["lesson"]["lemma"] == "río"
    assert (looked["state"]["remaining"], looked["state"]["readable_until"]) == (0, 2)
    assert client.post(f"/api/reading/{session}/lookup", json={"word": "xyzzy"}).status_code == 404
    assert rows(
        db_path, "SELECT kind, text_es FROM turns WHERE session_id = 1 ORDER BY turn_no"
    ) == [("study", "perro, nadar"), ("study", "río")]


def test_talking_about_a_text_needs_it_finished_then_continues_as_a_conversation(serve, db_path):
    story = add_story(db_path, STORY)
    client, _ = serve("¿Te gustó el cuento?", "Sí, el gato come en casa.")
    session = client.post(
        "/api/reading", json={"content_id": story, "chosen_via": "requested"}
    ).json()["session_id"]

    assert client.post(f"/api/reading/{session}/discuss").status_code == 409
    assert client.post(f"/api/reading/{session}/finish").json()["finished"] is True
    started = client.post(f"/api/reading/{session}/discuss").json()
    assert (started["session_id"], started["opening"]["reply_es"]) == (
        session,
        "¿Te gustó el cuento?",
    )
    reply = client.post(f"/api/sessions/{session}/messages", json={"text": "Sí, me gustó."})
    assert reply.status_code == 200, reply.text

    transcript = client.get(f"/api/sessions/{session}").json()
    assert [t["kind"] for t in transcript["turns"]] == [
        "reading", "conversation", "conversation", "conversation",
    ]  # fmt: skip
    assert transcript["session"]["skill"] == "reading"
    assert rows(db_path, "SELECT event, chosen_via, session_id FROM content_events") == [
        ("started", "requested", session),
        ("finished", None, session),
    ]


def test_reading_endpoints_name_real_items_and_open_sessions(serve, db_path):
    client, _ = serve()
    assert (
        client.post("/api/reading", json={"content_id": 99, "chosen_via": "requested"}).status_code
        == 404
    )
    assert client.get("/api/reading/99").status_code == 404
    assert client.get("/api/reading/99/batch").status_code == 404


# --- Lyrics ------------------------------------------------------------------------------


def add_poem(db_path, text):
    from spanish_tutor import content

    conn = db.connect(db_path)
    with conn:
        poem = content.add_item(
            conn, "poem", "Rima", text, source="gutenberg:53552", is_private=False, author="Bécquer"
        )
    conn.close()
    return poem


def translator(*numbers_per_call):
    """A fake translation model: each call answers the given line numbers."""
    from spanish_tutor.conversation import Generation
    from spanish_tutor.lyrics import LineTranslation, SongTranslation

    calls = []

    def translate(schema, prompt):
        numbers = numbers_per_call[len(calls)]
        calls.append(prompt)
        lines = [
            LineTranslation(
                line_no=n, natural_en=f"natural {n}", literal_en=f"literal {n}", note_en=None
            )
            for n in numbers
        ]
        return Generation(SongTranslation(lines=lines), input_tokens=900, output_tokens=300)

    translate.calls = calls
    return translate


POEM = "El gato come en casa.\nEl perro nada en el río."


def test_a_poem_opens_as_lyrics_with_a_translation_made_once(serve, db_path):
    poem = add_poem(db_path, POEM)
    translate = translator((1, 2))
    client, _ = serve(translate=translate)

    state = client.post("/api/reading", json={"content_id": poem, "chosen_via": "requested"}).json()
    assert (state["skill"], state["kind"], state["paragraphs"]) == (
        "lyrics",
        "poem",
        [["El gato come en casa.", "El perro nada en el río."]],
    )
    session = state["session_id"]
    first = client.get(f"/api/reading/{session}/translation").json()
    assert [(l["line_no"], l["es"], l["natural_en"]) for l in first["lines"]] == [
        (1, "El gato come en casa.", "natural 1"),
        (2, "El perro nada en el río.", "natural 2"),
    ]
    assert client.get(f"/api/reading/{session}/translation").json() == first
    assert len(translate.calls) == 1  # stored, then reused


def test_attempts_come_back_compared(serve, db_path):
    from spanish_tutor.lyrics import AttemptFeedback, LineFeedback

    poem = add_poem(db_path, POEM)
    feedback = AttemptFeedback(lines=[LineFeedback(line_no=2, verdict="right", comment_en="Yes!")])
    client, _ = serve(feedback, translate=translator((1, 2)))
    session = client.post(
        "/api/reading", json={"content_id": poem, "chosen_via": "recommended"}
    ).json()["session_id"]

    compared = client.post(
        f"/api/reading/{session}/attempt",
        json={"attempts": [{"line_no": 2, "text": "The dog swims."}]},
    ).json()["lines"]

    assert [(c["line_no"], c["attempt"], c["verdict"]) for c in compared] == [
        (1, None, None),
        (2, "The dog swims.", "right"),
    ]
    assert rows(db_path, "SELECT role, kind FROM turns ORDER BY turn_no") == [
        ("learner", "attempt"),
        ("tutor", "attempt"),
    ]


def test_only_songs_and_poems_have_translations_and_a_bad_one_is_reported(serve, db_path):
    story = add_story(db_path, STORY)
    poem = add_poem(db_path, POEM)
    client, _ = serve(translate=translator((1,), (1, 1)))
    reading = client.post(
        "/api/reading", json={"content_id": story, "chosen_via": "requested"}
    ).json()
    assert reading["skill"] == "reading"
    assert client.get(f"/api/reading/{reading['session_id']}/translation").status_code == 409
    lyrics = client.post(
        "/api/reading", json={"content_id": poem, "chosen_via": "requested"}
    ).json()
    broken = client.get(f"/api/reading/{lyrics['session_id']}/translation")
    assert broken.status_code == 502 and "lines exactly once" in broken.json()["detail"]


# --- Speech ----------------------------------------------------------------------------


def test_text_is_spoken_as_cacheable_audio(serve, tmp_path):
    speaker, loads = fake_speaker(tmp_path / "voices-1")
    client, _ = serve(speaker=speaker)
    response = client.get("/api/speech", params={"text": "¿Riegas tu jardín?", "accent": "es"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert response.content[:4] == b"RIFF"
    assert loads[0][0].name == "es_ES-davefx-medium.onnx"
    assert loads[0][1].said[0][0] == "¿Riegas tu jardín?"


def test_speech_defaults_to_the_mexican_voice(serve, tmp_path):
    speaker, loads = fake_speaker(tmp_path / "voices-1")
    client, _ = serve(speaker=speaker)
    assert client.get("/api/speech", params={"text": "hola"}).status_code == 200
    assert loads[0][0].name == "es_MX-claude-high.onnx"


@pytest.mark.parametrize(
    "params", [{"text": "   "}, {"text": "x" * 1501}, {"text": "hola", "accent": "ar"}, {}]
)
def test_unspeakable_requests_are_refused(serve, params):
    client, _ = serve()
    assert client.get("/api/speech", params=params).status_code == 422


def test_a_missing_voice_is_a_503_that_says_how_to_get_it(serve, tmp_path):
    speaker, _ = fake_speaker(tmp_path / "voices-1", accents=("es",))
    client, _ = serve(speaker=speaker)
    response = client.get("/api/speech", params={"text": "hola", "accent": "mx"})
    assert response.status_code == 503 and "speech download" in response.json()["detail"]


def test_voices_report_which_accents_are_downloaded(serve, tmp_path):
    speaker, _ = fake_speaker(tmp_path / "voices-1", accents=("es",))
    client, _ = serve(speaker=speaker)
    assert client.get("/api/speech/voices").json() == [
        {"accent": "mx", "label": "México", "available": False},
        {"accent": "es", "label": "España", "available": True},
    ]


# --- Rating (the evaluation's hand ratings) --------------------------------------------------


def queue_word(db_path, ref="turn:1", lemma="perro"):
    conn = db.connect(db_path)
    item_id = conn.execute(
        "INSERT INTO eval_items (item_type, source_ref, content) VALUES ('new_word_flag', ?, ?)",
        (ref, f'{{"lemma": "{lemma}", "sentence": "El {lemma}."}}'),
    ).lastrowid
    conn.commit()
    conn.close()
    return item_id


def test_the_rating_page_gets_its_items_and_criterion(serve, db_path):
    item_id = queue_word(db_path)
    client, _ = serve()
    found = client.get("/api/eval/items/new_word_flag").json()
    assert found["criterion"]["labels"] == ["new", "known", "not_a_word"]
    assert found["criterion"]["scale"] is None
    assert found["items"][0] == {
        "item_id": item_id,
        "item_type": "new_word_flag",
        "source_ref": "turn:1",
        "content": {"lemma": "perro", "sentence": "El perro."},
        "score": None,
        "label": None,
        "rated_at": None,
    }
    assert client.get("/api/eval/items/nonsense").status_code == 404


def test_a_rating_is_recorded_and_counted(serve, db_path):
    item_id = queue_word(db_path)
    queue_word(db_path, "benchmark:r:turn:2", "qué")
    client, _ = serve()
    assert client.post("/api/eval/ratings", json={"item_id": item_id, "label": "new"}).json() == {
        "ok": True
    }
    overview = client.get("/api/eval").json()
    assert overview["progress"]["new_word_flag"] == {"items": 2, "rated": 1}
    assert overview["new_word_precision"]["real"]["k"] == 1
    assert overview["new_word_precision"]["benchmark"]["n"] == 0
    assert overview["new_word_precision"]["benchmark"]["value"] is None


def test_a_rating_the_criterion_doesnt_allow_is_a_422(serve, db_path):
    item_id = queue_word(db_path)
    client, _ = serve()
    response = client.post("/api/eval/ratings", json={"item_id": item_id, "label": "maybe"})
    assert response.status_code == 422 and "new, known, not_a_word" in response.json()["detail"]
