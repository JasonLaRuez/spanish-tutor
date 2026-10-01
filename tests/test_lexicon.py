"""The shared (lemma, pos) normalization every part of the system relies on."""

import pytest

from spanish_tutor.lexicon import LemmaCorrector, analyze, normalize, normalize_text, vocabulary


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
    # corrector=None: raw spaCy plus the rules above, without downloaded Wiktionary data.
    return next(analyze([text], corrector=None))


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


# --- Lemma correction against Wiktionary ------------------------------------------------

DICTIONARY = {
    ("creer", "VERB"), ("crear", "VERB"), ("haber", "VERB"), ("oír", "VERB"),
    ("hambriento", "ADJ"), ("salir", "VERB"), ("salar", "VERB"), ("vez", "NOUN"),
    ("serio", "ADJ"), ("seriar", "VERB"), ("vosotros", "PRON"),
}  # fmt: skip
FORM_LINKS = {
    "crees": [("crear", "VERB"), ("creer", "VERB")],
    "has": [("haber", "VERB")],
    "hambrienta": [("hambriento", "ADJ")],
    "veces": [("vez", "NOUN")],
    "sales": [("salir", "VERB"), ("salar", "VERB")],
    "serio": [("seriar", "VERB")],
}
PRIOR = {"creer": 10_568, "crear": 1_417, "haber": 60_000, "salir": 900, "salar": 900}


@pytest.fixture
def corrector():
    return LemmaCorrector(
        is_word=DICTIONARY.__contains__,
        form_links=FORM_LINKS,
        prior=PRIOR,
        parts_of_speech=lambda word: [p for lemma, p in DICTIONARY if lemma == word],
    )


def test_dictionary_words_are_left_alone(corrector):
    assert normalize("creo", "creer", "VERB", corrector) == [("creer", "VERB")]
    assert not corrector.corrected


def test_inflected_form_left_as_lemma_is_corrected(corrector):
    assert normalize("has", "has", "AUX", corrector) == [("haber", "VERB")]
    assert normalize("veces", "veces", "NOUN", corrector) == [("vez", "NOUN")]


def test_invented_lemma_is_corrected_through_the_surface_form(corrector):
    assert normalize("crees", "creser", "VERB", corrector) == [("creer", "VERB")]  # more common


def test_surface_form_that_is_a_dictionary_word_replaces_a_bad_lemma(corrector):
    assert normalize("oír", "oir", "VERB", corrector) == [("oír", "VERB")]


def test_single_match_with_another_pos_is_used(corrector):
    assert normalize("hambrienta", "hambrienta", "NOUN", corrector) == [("hambriento", "ADJ")]


def test_ties_are_not_guessed_and_are_reported(corrector):
    assert normalize("sales", "sales", "VERB", corrector) == [("sales", "VERB")]
    assert corrector.unresolved == {("sales", ("sales", "VERB")): 1}


def test_corrections_are_counted_for_review(corrector):
    normalize("has", "has", "VERB", corrector)
    normalize("has", "has", "VERB", corrector)
    assert corrector.corrected == {("has", ("has", "VERB"), ("haber", "VERB")): 2}


def test_wrong_tag_on_a_dictionary_word_is_fixed_before_following_form_links(corrector):
    # "en serio" tagged NOUN: serio is an adjective, not the verb seriar it's a form of.
    assert normalize("serio", "serio", "NOUN", corrector) == [("serio", "ADJ")]
    assert normalize("vosotros", "vosotro", "NOUN", corrector) == [("vosotros", "PRON")]
