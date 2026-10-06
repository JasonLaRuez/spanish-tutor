"""Parsers for the raw corpora, run against tiny fixtures in the real file formats."""

import bz2
import json

import openpyxl
import pytest

from spanish_tutor.ingest import subtlex
from spanish_tutor.ingest.download import compact_entry
from spanish_tutor.ingest.tatoeba import (
    TaggerMismatch,
    load_sentences,
    meta_path,
    record_tagger,
    require_current,
)
from spanish_tutor.ingest.wiktionary import Wiktionary

# --- SUBTLEX-ESP -----------------------------------------------------------------------


def test_subtlex_reads_all_three_column_blocks_and_sums_duplicates(tmp_path):
    header = ["Word", "Freq. count", "Freq. per million", "Log freq."]
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append([*header, None, *header, None, *header])
    sheet.append(["casa", 10, 0.2, 1.0, None, "está", 5, 0.1, 0.7, None, "países", 3, 0, 0])
    sheet.append(["esta", 7, 0.2, 0.9, None, None, None, None, None, None, "países", 2, 0, 0])
    path = tmp_path / "subtlex.xlsx"
    workbook.save(path)

    counts = subtlex.load_counts(path)

    assert counts == {"casa": 10, "está": 5, "esta": 7, "países": 5}


# --- Tatoeba ---------------------------------------------------------------------------


def write_bz2(path, lines):
    with bz2.open(path, "wt", encoding="utf-8", newline="") as f:
        f.write("".join(line + "\n" for line in lines))


def test_tatoeba_joins_lowest_id_translation_and_keeps_quotes(tmp_path):
    write_bz2(
        tmp_path / "spa.tsv.bz2",
        [
            '1\tspa\tDijo "hola".\talice\t2020-01-01\t2020-01-01',
            "2\tspa\tSin traducción.\t\\N\t\\N\t\\N",
        ],
    )
    write_bz2(tmp_path / "eng.tsv.bz2", ['10\teng\tHe said "hello".', "11\teng\tHe said hi."])
    write_bz2(tmp_path / "links.tsv.bz2", ["1\t11", "1\t10"])

    sentences = load_sentences(
        tmp_path / "spa.tsv.bz2", tmp_path / "eng.tsv.bz2", tmp_path / "links.tsv.bz2"
    )

    first, second = sentences
    assert (first.es, first.en, first.author) == ('Dijo "hola".', 'He said "hello".', "alice")
    assert (second.en, second.author) == (None, None)


# --- Wiktionary ------------------------------------------------------------------------


def test_compact_entry_drops_inflected_form_senses():
    entry = {
        "word": "pies",
        "pos": "noun",
        "senses": [{"glosses": ["plural of pie"], "tags": ["form-of"], "form_of": [{}]}],
    }
    assert compact_entry(entry) is None


def test_compact_entry_keeps_form_of_senses_for_function_words():
    entry = {
        "word": "ti",
        "pos": "pron",
        "senses": [{"glosses": ["prepositional form of tú"], "tags": ["form-of"]}],
    }
    assert compact_entry(entry)["senses"][0]["gloss"] == "prepositional form of tú"


def test_compact_entry_records_alternative_spelling_target():
    entry = {
        "word": "ese",
        "pos": "pron",
        "senses": [{"glosses": ["alternative spelling of ése"], "alt_of": [{"word": "ése"}]}],
    }
    assert compact_entry(entry)["senses"][0]["alt_of"] == "ése"


def test_compact_entry_keeps_the_specific_subsense_gloss():
    entry = {
        "word": "casa",
        "pos": "noun",
        "senses": [{"glosses": ["house", "home (one's residence)"], "tags": []}],
    }
    assert compact_entry(entry)["senses"] == [{"gloss": "home (one's residence)", "tags": []}]


def write_wiktionary(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def test_wiktionary_maps_pos_and_prefers_current_senses(tmp_path):
    path = tmp_path / "wikt.jsonl"
    write_wiktionary(
        path,
        [
            {
                "word": "bajo",
                "pos": "adj",
                "senses": [
                    {"gloss": "base, vile", "tags": ["archaic"]},
                    {"gloss": "short", "tags": []},
                    {"gloss": "low", "tags": []},
                    {"gloss": "quiet", "tags": []},
                ],
            },
            {"word": "bajo", "pos": "prep", "senses": [{"gloss": "under", "tags": []}]},
            {"word": "que", "pos": "conj", "senses": [{"gloss": "that", "tags": []}]},
            {"word": "María", "pos": "name", "senses": [{"gloss": "Mary", "tags": []}]},
        ],
    )

    wikt = Wiktionary(path)

    assert wikt.definition("bajo", "ADJ") == "short; low; quiet"
    assert wikt.definition("bajo", "ADP") == "under"
    assert ("que", "CCONJ") in wikt and ("que", "SCONJ") in wikt
    assert ("María", "PROPN") not in wikt
    assert wikt.definition("bajo", "NOUN") is None


def test_wiktionary_falls_back_to_disfavored_senses_when_nothing_else(tmp_path):
    path = tmp_path / "wikt.jsonl"
    write_wiktionary(
        path, [{"word": "vos", "pos": "pron", "senses": [{"gloss": "you", "tags": ["dated"]}]}]
    )
    assert Wiktionary(path).definition("vos", "PRON") == "you"


def test_function_words_match_across_compatible_pos(tmp_path):
    path = tmp_path / "wikt.jsonl"
    write_wiktionary(
        path,
        [
            {"word": "cómo", "pos": "adv", "senses": [{"gloss": "how", "tags": []}]},
            {"word": "serio", "pos": "adj", "senses": [{"gloss": "serious", "tags": []}]},
        ],
    )
    wikt = Wiktionary(path)
    assert wikt.definition("cómo", "PRON") == "how"  # spaCy: PRON, Wiktionary: adv
    assert ("serio", "NOUN") not in wikt  # open classes: a mismatch is a tagging error


def test_alternative_spelling_is_followed_to_the_real_definition(tmp_path):
    path = tmp_path / "wikt.jsonl"
    write_wiktionary(
        path,
        [
            {
                "word": "ese",
                "pos": "pron",
                "senses": [{"gloss": "alternative spelling of ése", "tags": [], "alt_of": "ése"}],
            },
            {"word": "ése", "pos": "pron", "senses": [{"gloss": "that one", "tags": []}]},
        ],
    )
    assert Wiktionary(path).definition("ese", "PRON") == "that one"


def test_definition_dedupes_pieces_across_senses(tmp_path):
    path = tmp_path / "wikt.jsonl"
    write_wiktionary(
        path,
        [
            {
                "word": "tener",
                "pos": "verb",
                "senses": [
                    {"gloss": "to have; to possess", "tags": []},
                    {"gloss": "to have; to possess", "tags": []},
                    {"gloss": "to be (a condition)", "tags": []},
                    {"gloss": "to hold", "tags": []},
                ],
            }
        ],
    )
    assert Wiktionary(path).definition("tener", "VERB") == (
        "to have; to possess; to be (a condition)"
    )


def test_form_links_keep_only_targets_that_are_dictionary_words(tmp_path):
    from spanish_tutor.ingest.download import form_links

    entry = {
        "word": "amiga",
        "pos": "noun",
        "senses": [
            {"glosses": ["female friend"], "form_of": [{"word": "amigo"}, {"word": "friend"}]}
        ],
    }
    links_path = tmp_path / "forms.tsv"
    links_path.write_text(
        "".join("\t".join(link) + "\n" for link in form_links(entry)), encoding="utf-8"
    )
    wikt_path = tmp_path / "wikt.jsonl"
    write_wiktionary(
        wikt_path, [{"word": "amigo", "pos": "noun", "senses": [{"gloss": "friend", "tags": []}]}]
    )

    assert Wiktionary(wikt_path).form_links(links_path) == {"amiga": [("amigo", "NOUN")]}


def test_compact_entry_keeps_irregular_comparatives_as_words():
    entry = {
        "word": "peor",
        "pos": "adj",
        "senses": [
            {"glosses": ["comparative degree of malo: worse"], "tags": ["comparative", "form-of"],
             "form_of": [{"word": "malo"}]},
            {"glosses": ["superlative degree of malo: worst"], "tags": ["superlative", "form-of"],
             "form_of": [{"word": "malo"}]},
        ],
    }  # fmt: skip
    assert [s["gloss"] for s in compact_entry(entry)["senses"]] == [
        "comparative degree of malo: worse"
    ]


# --- Misspelling entries ---------------------------------------------------------------


def wiktionary_of(tmp_path, entries):
    path = tmp_path / "wikt.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return Wiktionary(path)


def alt(word, pos, target, *tags):
    return {
        "word": word,
        "pos": pos,
        "senses": [{"gloss": f"x of {target}", "tags": ["alt-of", *tags], "alt_of": target}],
    }


def test_misspelling_only_entries_redirect_to_the_correct_word(tmp_path):
    wiktionary = wiktionary_of(
        tmp_path,
        [
            {"word": "día", "pos": "noun", "senses": [{"gloss": "day", "tags": []}]},
            alt("dia", "noun", "día", "misspelling"),
            alt("dia", "noun", "día", "obsolete"),  # same target: still a redirect
        ],
    )
    assert wiktionary.misspellings() == {("dia", "NOUN"): ("día", "NOUN")}


def superseded(word, pos, target, year):
    return {
        "word": word,
        "pos": pos,
        "senses": [
            {
                "gloss": f"superseded spelling of {target}, deprecated in {year} by the RAE",
                "tags": ["alt-of", "archaic"],
                "alt_of": target,
            }
        ],
    }


def test_only_spellings_the_1952_reform_superseded_are_respelled(tmp_path):
    wiktionary = wiktionary_of(
        tmp_path,
        [
            superseded("fué", "verb", "fue", 1952),
            superseded("dió", "verb", "dio", 1952),
            superseded("guión", "noun", "guion", 2010),  # still common: left alone
            {"word": "sólo", "pos": "adv", "senses": [{"gloss": "superseded spelling of solo",
                                                       "tags": ["alt-of"], "alt_of": "solo"}]},
            superseded("buho", "noun", "búho", 1952),
            {"word": "buho", "pos": "verb", "senses": [{"gloss": "a real sense", "tags": []}]},
        ],
    )  # fmt: skip
    assert wiktionary.reform_1952_spellings() == {"fué": "fue", "dió": "dio"}


def test_obsolete_spellings_that_differ_only_in_accents_are_respelled(tmp_path):
    wiktionary = wiktionary_of(
        tmp_path,
        [
            alt("á", "prep", "a", "obsolete"),
            alt("ántes", "adv", "antes", "obsolete"),
            alt("ay", "intj", "hay", "obsolete"),  # not an accent-only change
            alt("sólo", "adv", "solo", "archaic"),  # superseded in 2010, not obsolete
            alt("buen", "adj", "bueno", "alternative"),
        ],
    )
    assert wiktionary.obsolete_accent_spellings() == {"á": "a", "ántes": "antes"}


def test_alternative_forms_and_words_with_real_senses_are_not_redirected(tmp_path):
    wiktionary = wiktionary_of(
        tmp_path,
        [
            {"word": "bueno", "pos": "adj", "senses": [{"gloss": "good", "tags": []}]},
            alt("buen", "adj", "bueno", "alternative"),  # a separate word to learn
            {"word": "más", "pos": "adv", "senses": [{"gloss": "more", "tags": []}]},
            alt("mas", "adv", "más", "misspelling"),
            {"word": "mas", "pos": "adv", "senses": [{"gloss": "but (literary)", "tags": []}]},
            alt("ghost", "noun", "missing", "misspelling"),  # target isn't a word
        ],
    )
    assert wiktionary.misspellings() == {}


def test_nonstandard_spellings_are_only_misspellings_or_obsolete(tmp_path):
    wiktionary = wiktionary_of(
        tmp_path,
        [
            alt("jardin", "noun", "jardín", "obsolete"),
            alt("dia", "noun", "día", "misspelling"),
            alt("sólo", "adv", "solo", "archaic"),  # archaic: still a word
            alt("buen", "adj", "bueno", "alternative"),
            alt("ay", "verb", "hay", "obsolete"),
            {"word": "ay", "pos": "intj", "senses": [{"gloss": "ouch", "tags": []}]},
        ],
    )
    assert wiktionary.nonstandard_spellings() == {"jardin", "dia"}


def test_cached_corpus_from_the_current_tagger_is_accepted(tmp_path):
    analyzed = tmp_path / "tatoeba_analyzed.jsonl"
    record_tagger(analyzed)
    assert meta_path(analyzed).name == "tatoeba_analyzed.meta.json"
    require_current(analyzed)


@pytest.mark.parametrize("recorded", [None, "es_core_news_md 3.8.0"])
def test_cached_corpus_from_another_or_unrecorded_tagger_is_refused(tmp_path, recorded):
    analyzed = tmp_path / "tatoeba_analyzed.jsonl"
    if recorded:
        meta_path(analyzed).write_text(json.dumps({"tagger": recorded}), encoding="utf-8")
    with pytest.raises(TaggerMismatch, match=recorded or "unrecorded"):
        require_current(analyzed)


def test_cached_corpus_from_another_expression_list_is_refused(tmp_path, monkeypatch):
    from spanish_tutor.ingest import expressions

    analyzed = tmp_path / "tatoeba_analyzed.jsonl"
    monkeypatch.setattr(expressions, "signature", lambda: "aaaa")
    record_tagger(analyzed)
    require_current(analyzed)  # same tagger, same list
    monkeypatch.setattr(expressions, "signature", lambda: "bbbb")  # the list changed
    with pytest.raises(TaggerMismatch, match="bbbb"):
        require_current(analyzed)


def test_candidates_are_found_in_a_corpus_already_analyzed_with_expressions():
    # Rebuilding the candidates after the list exists: the corpus has "sin embargo" as one
    # EXPR item, which must still count as the lemma sequence (sin, embargo).
    from spanish_tutor.ingest.expressions import Candidate, find_in_corpus, sentence_lemmas
    from spanish_tutor.ingest.tatoeba import AnalyzedSentence

    tokens = [
        ("sin embargo", [("sin embargo", "EXPR")]),
        ("embargo", []),
        ("llueve", [("llover", "VERB")]),
    ]
    approved = {"sin embargo": ("sin", "embargo")}
    assert sentence_lemmas(tokens, approved) == ["sin", "embargo", "llover"]
    assert sentence_lemmas(tokens) == ["sin embargo", "llover"]  # no list: left as is

    sentence = AnalyzedSentence(id=1, es="Sin embargo, llueve.", en=None, author="x", tokens=tokens)
    candidate = Candidate("sin embargo", "adv", ["however"], lemmas=("sin", "embargo"))
    find_in_corpus([candidate], [sentence], approved)
    assert candidate.sentences == 1


# --- Project Gutenberg books ------------------------------------------------------------

GUTENBERG_BOOK = """Project Gutenberg header, license...
*** START OF THE PROJECT GUTENBERG EBOOK CUENTOS ***

#Cuentos#

#INDICE#

La gallina degollada
A la deriva

#LA GALLINA DEGOLLADA#

#Primavera#

Todo el día --dijo-- estaban sentados.

#A LA DERIVA#

El hombre pisó algo blando.

*** END OF THE PROJECT GUTENBERG EBOOK CUENTOS ***
License text.
"""


def manifest(chapters, **rules):
    from spanish_tutor.ingest.gutenberg import Manifest

    return Manifest(ebook=1, title="Libro", author=None, chapters=chapters, **rules)


def test_a_book_with_hash_headings_splits_into_titled_chapters(tmp_path):
    from spanish_tutor.ingest.gutenberg import body, split_chapters, write_chapters

    quiroga_rules = {"replace": (("^#([^#\n]+)#[ \t]*$", r"\1"), ("--", " — "))}
    chapters = split_chapters(
        body(GUTENBERG_BOOK),
        manifest(
            [("#LA GALLINA DEGOLLADA#", "La gallina degollada"), ("#A LA DERIVA#", "A la deriva")],
            **quiroga_rules,
        ),
    )
    assert chapters == [
        # The section heading is a plain line; the dialogue dash is spaced out.
        ("La gallina degollada", "Primavera\n\nTodo el día  — dijo —  estaban sentados."),
        ("A la deriva", "El hombre pisó algo blando."),
    ]
    paths = write_chapters(chapters, tmp_path / "book")
    assert [p.name for p in paths] == ["01 La gallina degollada.txt", "02 A la deriva.txt"]


READER = """CONTENTS

I. EL POLLO
II. LOS OSOS[1]

EL POLLO

Un día un pollo entra en un bosque.                                   5
--¿A dónde vas?--pregunta la gallina.

[Illustration: Un pollo]

    LOS OSOS[1]                                                        10

Había una vez tres osos.

[Note 1: The bears are a
well-known English tale.]

VOCABULARY

a, to
"""

READER_RULES = {
    "end": "VOCABULARY",
    "remove": (
        r"[ \t]{3,}\d{1,3}[ \t]*$",
        r"\[Note \d+:[^\]]*\]",
        r"\[Illustration[^\]]*\]",
        r"\[\d+\]",
    ),
    "replace": (("--", " — "),),
}


def test_a_graded_reader_loses_its_margin_numbers_notes_and_vocabulary():
    from spanish_tutor.ingest.gutenberg import split_chapters

    chapters = split_chapters(
        READER, manifest([("EL POLLO", "El pollo"), ("LOS OSOS", "Los osos")], **READER_RULES)
    )
    assert chapters == [
        # The contents list ("I. EL POLLO") isn't a heading: headings are whole lines.
        (
            "El pollo",
            "Un día un pollo entra en un bosque.\n — ¿A dónde vas? — pregunta la gallina.",
        ),
        ("Los osos", "Había una vez tres osos."),  # the note and the vocabulary are gone
    ]


def test_a_manifest_that_doesnt_fit_the_text_is_refused():
    from spanish_tutor.ingest.gutenberg import split_chapters

    with pytest.raises(ValueError, match="heading not found .*'LOS LOBOS'"):
        split_chapters(READER, manifest([("EL POLLO", "a"), ("LOS LOBOS", "b")]))
    with pytest.raises(ValueError, match="end line not found"):
        split_chapters(READER, manifest([("EL POLLO", "a")], end="ÍNDICE"))
    with pytest.raises(ValueError, match="heading not found"):  # headings come in order
        split_chapters(READER, manifest([("VOCABULARY", "a"), ("EL POLLO", "b")]))


def test_every_committed_manifest_loads_with_unique_headings(tmp_path):
    from spanish_tutor.ingest.gutenberg import MANIFESTS, load_manifest

    paths = sorted(MANIFESTS.glob("*.json"))
    assert paths, "no manifests found"
    for path in paths:
        book = load_manifest(int(path.stem))
        headings = [heading for heading, _ in book.chapters]
        assert book.ebook == int(path.stem) and book.title and headings, path.name
        assert len(set(headings)) == len(headings), path.name
    with pytest.raises(FileNotFoundError, match="no manifest for ebook 1"):
        load_manifest(1, tmp_path)
