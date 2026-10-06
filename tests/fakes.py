"""Test doubles shared by the conversation and API tests: no API calls, no spaCy.

The analyzer maps surface forms through FORMS; capitalized NAMES are proper nouns (no
analyses, like spaCy's PROPN); anything else is analyzed as itself, a NOUN, so tagger
junk like "xyzzy" reaches the lexicon lookup and must be rejected there.
"""

import re

from langchain_core.embeddings import DeterministicFakeEmbedding

from spanish_tutor.content import add_item, index_item
from spanish_tutor.conversation import Generation, TutorReply
from spanish_tutor.ingest.index_tatoeba import index_sentences
from spanish_tutor.ingest.tatoeba import AnalyzedSentence
from spanish_tutor.vectorstore import open_store
from spanish_tutor.words import LexiconIndex

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
            reply = TutorReply(reply_es=reply, reply_en="(en)", misused=[], note_en=None)
        return Generation(
            reply,
            input_tokens=100,
            cache_read_tokens=1000,
            cache_write_5m_tokens=60,
            output_tokens=50,
        )

    def select_words(self, prompt):
        self.requests.append(prompt)
        return self.replies.pop(0)

    def summarize(self, prompt):
        self.requests.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Generation(reply, input_tokens=800, cache_read_tokens=0, output_tokens=120)


def notes(went_well="You asked good questions.", *work_on):
    from spanish_tutor.conversation import SessionNotes

    return SessionNotes(went_well_en=went_well, work_on=list(work_on) or ["Practice estar."])


def said(reply_es, misused=(), correction=None, wrong_words=()):
    """A scripted reply. `misused` are wrong forms; `wrong_words` are different words."""
    from spanish_tutor.conversation import Misuse

    flags = [Misuse(written=w, wrong_word=False) for w in misused]
    flags += [Misuse(written=w, wrong_word=True) for w in wrong_words]
    return TutorReply(reply_es=reply_es, reply_en="(en)", misused=flags, note_en=correction)


def seed_bank(conn):
    """Put KNOWN and UNKNOWN in the lexicon (definition "<word>"), and teach KNOWN."""
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


def make_topic_store(conn, directory):
    """A vector store of sentences about a dog swimming in a river, with frequencies for the
    candidate scoring: perro, nadar and río (unknown to the seeded bank) are the candidates."""
    conn.executemany(
        "UPDATE lexemes SET frequency_per_million = ? WHERE lemma = ?",
        [(30.0, "perro"), (5.0, "nadar"), (20.0, "río"), (5000.0, "el"), (900.0, "casa")],
    )
    conn.commit()
    store = open_store(directory, embeddings=DeterministicFakeEmbedding(size=32))
    vocab = [FORMS[w] for w in ["el", "perro", "nada", "río"]]
    sentences = [
        AnalyzedSentence(
            id=i,
            es="El perro nada en el río.",
            en="The dog swims.",
            author=None,
            tokens=[(lemma, [(lemma, pos)]) for lemma, pos in vocab],
        )
        for i in range(4)
    ]
    index_sentences(store, sentences, report=lambda _: None)
    return store


# --- Content (test_content, test_resolve) ------------------------------------------------

# The fake tagger for content: a form maps to its analyses. "del" is a contraction (two analyses), a
# capitalized name has none, and "sin embargo" is an approved expression: its first token
# carries the phrase and the second has no analyses, as lexicon.ExpressionMatcher does.
CONTENT_FORMS = {
    "el": [("el", "DET")],
    "gato": [("gato", "NOUN")],
    "come": [("comer", "VERB")],
    "duerme": [("dormir", "VERB")],
    "sale": [("salir", "VERB")],
    "del": [("de", "ADP"), ("el", "DET")],
    "jardín": [("jardín", "NOUN")],
    "llueve": [("llover", "VERB")],
    "nada": [("nadar", "VERB")],
    "fué": [("fuar", "VERB")],  # an old spelling the tagger gets wrong
    "yeah": [("yeah", "NOUN")],
}
CONTENT_NAMES = {"María"}
CONTENT_LEXICON = [
    ("el", "DET"), ("gato", "NOUN"), ("comer", "VERB"), ("dormir", "VERB"),
    ("salir", "VERB"), ("de", "ADP"), ("jardín", "NOUN"), ("llover", "VERB"),
    ("sin embargo", "EXPR"), ("ser", "VERB"),
]  # fmt: skip


def analyze_content(text):
    words = re.findall(r"\w+", text)
    tokens, i = [], 0
    while i < len(words):
        word = words[i]
        if word.lower() == "sin" and i + 1 < len(words) and words[i + 1].lower() == "embargo":
            tokens += [("sin embargo", [("sin embargo", "EXPR")]), ("embargo", [])]
            i += 2
            continue
        if word in CONTENT_NAMES:
            tokens.append((word.lower(), []))
        else:
            form = word.lower()
            tokens.append((form, CONTENT_FORMS.get(form, [(form, "NOUN")])))
        i += 1
    return tokens


def analyze_many(units):
    return [analyze_content(u) for u in units]


def add_content_lexicon(conn):
    """The content tests' lexicon: lemma -> lexeme_id."""
    return {
        lemma: conn.execute(
            "INSERT INTO lexemes (lemma, pos) VALUES (?, ?)", (lemma, pos)
        ).lastrowid
        for lemma, pos in CONTENT_LEXICON
    }


def story(conn, text):
    return add_item(conn, "story", "Cuento", text, source="gutenberg:1", is_private=False)


def vocab(conn, content_id):
    return dict(
        conn.execute(
            """
            SELECT l.lemma, v.occurrences FROM content_vocab AS v JOIN lexemes AS l USING (lexeme_id)
            WHERE v.content_id = ?
            """,
            (content_id,),
        ).fetchall()
    )


def index_content(conn, content_id, wiktionary=None, resolver=None, analyzer="setup-1"):
    return index_item(
        conn,
        LexiconIndex(conn, wiktionary),
        content_id,
        analyze_many,
        analyzer,
        resolver=resolver,
        reviewer="model:test",
    )
