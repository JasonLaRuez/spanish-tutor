"""Test doubles shared by the conversation and API tests: no API calls, no spaCy.

The analyzer maps surface forms through FORMS; capitalized NAMES are proper nouns (no
analyses, like spaCy's PROPN); anything else is analyzed as itself, a NOUN, so tagger
junk like "xyzzy" reaches the lexicon lookup and must be rejected there.
"""

import re

from spanish_tutor.conversation import Generation, TutorReply

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
        return Generation(reply, input_tokens=100, cache_read_tokens=1000, output_tokens=50)

    def select_words(self, prompt):
        self.requests.append(prompt)
        return self.replies.pop(0)


def said(reply_es, misused=(), correction=None):
    return TutorReply(reply_es=reply_es, reply_en="(en)", misused=list(misused), note_en=correction)


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
