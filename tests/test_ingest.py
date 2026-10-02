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
