"""The web API, with a scripted model and the fake analyzer (no Claude, no spaCy).

Each test gets a temporary database file: the read-only endpoints open their own
connections, which an in-memory database can't share.
"""

import pytest
from fakes import KNOWN, Scripted, analyze, said, seed_bank
from fastapi.testclient import TestClient

from spanish_tutor import db
from spanish_tutor.api.app import create_app
from spanish_tutor.conversation import Resources
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

    def make(*replies, web_dist=tmp_path / "no-ui"):
        generator = Scripted(*replies)

        def load():
            conn = db.connect(db_path, check_same_thread=False)
            return Resources(conn, generator, LexiconIndex(conn, wiktionary), analyze, None)

        client = TestClient(create_app(load=load, db_path=db_path, web_dist=web_dist))
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


def test_a_known_word_can_be_looked_up_and_a_non_word_is_404(serve):
    client, _ = serve("Hola.")
    session = start(client)["session_id"]

    found = client.post(f"/api/sessions/{session}/lookup", json={"word": "gato"})
    assert (found.status_code, found.json()["definition_en"]) == (200, "<gato>")
    assert client.post(f"/api/sessions/{session}/lookup", json={"word": "xyzzy"}).status_code == 404


def test_a_conversation_the_server_doesnt_have_open_is_404(serve):
    client, _ = serve()
    response = client.post("/api/sessions/99/messages", json={"text": "hola"})
    assert response.status_code == 404


@pytest.mark.parametrize("new_words", [1, 11, -1])
def test_new_word_counts_outside_2_to_10_are_rejected(serve, new_words):
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
