"""The shared (lemma, pos) normalization every part of the system relies on."""

import pytest

from spanish_tutor.lexicon import analyze, normalize, normalize_text, vocabulary


def test_accents_are_kept_so_minimal_pairs_stay_distinct():
    assert normalize_text("Sí") != normalize_text("Si")
    assert normalize_text("ESTÁ") == "está"


def test_text_is_nfc_normalized():
    decomposed = "está"  # 'a' + combining acute accent (U+0301)
    assert normalize_text(decomposed) == "está"


def test_aux_is_folded_into_verb():
    assert normalize("estoy", "estar", "AUX") == [("estar", "VERB")]


def test_contractions_expand_to_both_words():
    assert normalize("del", "del", "ADP") == [("de", "ADP"), ("el", "DET")]
    assert normalize("al", "al", "ADP") == [("a", "ADP"), ("el", "DET")]
    # The tagger sometimes labels "del" a determiner; it's still de + el.
    assert normalize("del", "del", "DET") == [("de", "ADP"), ("el", "DET")]


def test_clitic_verb_lemma_keeps_the_verb():
    assert normalize("dámelo", "dar yo él", "VERB") == [("dar", "VERB")]


def test_lemma_is_lowercased():
    assert normalize("Si", "Si", "SCONJ") == [("si", "SCONJ")]


@pytest.mark.parametrize(
    ("form", "lemma"),
    [("se", "él"), ("lo", "él"), ("Me", "yo"), ("conmigo", "yo"), ("ustedes", "tú")],
)
def test_personal_pronouns_keep_their_own_form(form, lemma):
    assert normalize(form, lemma, "PRON") == [(form.lower(), "PRON")]


def test_other_pronouns_keep_the_tagger_lemma():
    assert normalize("esos", "ese", "PRON") == [("ese", "PRON")]


@pytest.mark.parametrize(
    ("lemma", "pos"), [("Juan", "PROPN"), (".", "PUNCT"), ("3", "NUM"), ("x", "X")]
)
def test_non_vocabulary_is_dropped(lemma, pos):
    assert normalize(lemma, lemma, pos) == []


# --- Against the real spaCy model ------------------------------------------------------


def analyzed(text):
    return next(analyze([text]))


def test_model_copula_and_existential_haber_become_verbs():
    vocab = vocabulary(analyzed("Estoy cansado y hay mucho trabajo."))
    assert ("estar", "VERB") in vocab
    assert ("haber", "VERB") in vocab
    assert ("trabajo", "NOUN") in vocab


def test_model_lemmatizes_inflected_verbs():
    assert ("comer", "VERB") in vocabulary(analyzed("Los niños comen manzanas."))


def test_model_expands_contraction_under_one_token():
    tokens = analyzed("Vengo del mercado.")
    assert ("del", [("de", "ADP"), ("el", "DET")]) in tokens


def test_model_separates_clitic_and_personal_pronouns():
    vocab = vocabulary(analyzed("Me lo dijo ella, pero se fue conmigo."))
    assert {("me", "PRON"), ("lo", "PRON"), ("ella", "PRON"), ("conmigo", "PRON")} <= vocab
    assert ("yo", "PRON") not in vocab and ("él", "PRON") not in vocab


def test_model_keeps_names_as_tokens_without_analyses():
    tokens = analyzed("Juan está aquí.")
    assert ("juan", []) in tokens
