"""The shared (lemma, pos) normalization every part of the system relies on."""

import pytest

from spanish_tutor.lexicon import (
    AccentRestorer,
    LemmaCorrector,
    analyze,
    normalize,
    normalize_text,
    vocabulary,
)


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
    [("se", "él"), ("lo", "él"), ("Me", "yo"), ("conmigo", "yo"), ("ustedes", "tú"), ("vos", "vo")],
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
    ("madre", "NOUN"), ("perro", "NOUN"), ("perro", "ADJ"), ("querer", "VERB"),
    ("tuyo", "PRON"), ("tuyo", "DET"), ("nube", "NOUN"),
    ("mi", "DET"), ("mi", "NOUN"), ("contigo", "ADV"),
    ("dia", "NOUN"), ("día", "NOUN"), ("habia", "VERB"),
    ("detrás", "ADV"), ("mañana", "NOUN"), ("mañana", "ADV"), ("después", "ADV"),
    ("regir", "VERB"), ("regar", "VERB"), ("riego", "NOUN"), ("hacer", "VERB"),
    ("hecho", "VERB"), ("quejar", "VERB"), ("quejarse", "VERB"), ("ese", "PRON"),
    ("ése", "PRON"), ("buen", "ADJ"), ("bueno", "ADJ"), ("saltar", "VERB"),
    ("zombie", "NOUN"), ("zombi", "NOUN"), ("gobierno", "NOUN"),
}  # fmt: skip
FORM_LINKS = {
    "crees": [("crear", "VERB"), ("creer", "VERB")],
    "has": [("haber", "VERB")],
    "hambrienta": [("hambriento", "ADJ")],
    "veces": [("vez", "NOUN")],
    "sales": [("salir", "VERB"), ("salar", "VERB")],
    "serio": [("seriar", "VERB")],
    "quieres": [("querer", "VERB")],
    "había": [("haber", "VERB")],
    "riego": [("regar", "VERB")],
    "hechos": [("hecho", "VERB")],
    "quejó": [("quejarse", "VERB")],
    "eso": [("ése", "PRON")],
    "buena": [("bueno", "ADJ")],
    "zombies": [("zombi", "NOUN")],
}
MISSPELLINGS = {("dia", "NOUN"): ("día", "NOUN"), ("habia", "VERB"): ("había", "VERB")}
PRIOR = {"creer": 10_568, "crear": 1_417, "haber": 60_000, "salir": 900, "salar": 900}


@pytest.fixture
def corrector():
    return LemmaCorrector(
        is_word=DICTIONARY.__contains__,
        form_links=FORM_LINKS,
        prior=PRIOR,
        parts_of_speech=lambda word: [p for lemma, p in DICTIONARY if lemma == word],
        misspellings=MISSPELLINGS,
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


# --- A real word, but not the one the form belongs to -----------------------------------


def test_lemma_the_form_is_not_listed_under_is_relinked(corrector):
    # Both spaCy models lemmatize the verb "riego" as regir; it is a form of regar.
    assert normalize("riego", "regir", "VERB", corrector) == [("regar", "VERB")]
    assert normalize("buena", "buen", "ADJ", corrector) == [("bueno", "ADJ")]
    assert corrector.corrected == {
        ("riego", ("regir", "VERB"), ("regar", "VERB")): 1,
        ("buena", ("buen", "ADJ"), ("bueno", "ADJ")): 1,
    }


def test_a_participle_entry_does_not_replace_its_verb(corrector):
    assert normalize("hechos", "hacer", "VERB", corrector) == [("hacer", "VERB")]


def test_a_pronominal_entry_does_not_split_the_verb(corrector):
    assert normalize("quejó", "quejar", "VERB", corrector) == [("quejar", "VERB")]


@pytest.mark.parametrize(
    ("form", "analysis"),
    [("eso", ("ese", "PRON")), ("zombies", ("zombie", "NOUN"))],  # old spelling; variant
)
def test_pronouns_and_nouns_are_not_relinked(corrector, form, analysis):
    assert normalize(form, *analysis, corrector) == [analysis]
    assert not corrector.corrected


def test_relinking_ties_are_reported_not_guessed(corrector):
    assert normalize("sales", "saltar", "VERB", corrector) == [("saltar", "VERB")]
    assert corrector.unresolved == {("sales", ("saltar", "VERB")): 1}


def test_a_form_listed_under_its_tagger_lemma_is_left_alone(corrector):
    assert normalize("crees", "crear", "VERB", corrector) == [("crear", "VERB")]
    assert not corrector.corrected and not corrector.unresolved


# --- Common words mis-tagged as proper nouns ------------------------------------------


@pytest.mark.parametrize(
    ("form", "expected"),
    [
        ("madre", ("madre", "NOUN")),  # a headword under one POS
        ("perro", ("perro", "NOUN")),  # several POS: NOUN, the slot the tagger saw
        ("quieres", ("querer", "VERB")),  # an inflected form
    ],
)
def test_lowercase_proper_noun_that_is_a_dictionary_word_is_retagged(corrector, form, expected):
    assert normalize(form, form, "PROPN", corrector) == [expected]
    assert corrector.corrected == {(form, (form, "PROPN"), expected): 1}


def test_function_word_reading_beats_a_letter_or_note_noun(corrector):
    # Wiktionary's noun "mi" is the musical note E; the possessive is what's meant.
    assert normalize("mi", "mi", "PROPN", corrector) == [("mi", "DET")]


def test_personal_pronoun_form_is_retagged_as_a_pronoun(corrector):
    # Wiktionary files contigo as an adverb; normal tagging (and the word bank) says PRON.
    assert normalize("contigo", "contigo", "PROPN", corrector) == [("contigo", "PRON")]


def test_ambiguous_lowercase_proper_noun_is_reported_not_guessed(corrector):
    assert normalize("tuyo", "tuyo", "PROPN", corrector) == []  # PRON or DET: a tie
    assert corrector.unresolved == {("tuyo", ("tuyo", "PROPN")): 1}


def test_lowercase_proper_noun_unknown_to_the_dictionary_is_dropped_silently(corrector):
    assert normalize("toki", "toki", "PROPN", corrector) == []
    assert not corrector.unresolved and not corrector.corrected


def test_capitalized_proper_noun_stays_a_name_even_if_it_is_a_word(corrector):
    assert normalize("Madre", "Madre", "PROPN", corrector) == []


def test_without_a_corrector_proper_nouns_are_dropped():
    assert normalize("perro", "perro", "PROPN") == []


def test_model_retags_a_mid_sentence_common_word_it_calls_a_name(corrector):
    # es_dep_news_trf tags "gobierno" PROPN here, and "Juan" correctly so.
    tokens = next(analyze(["Juan dice que el gobierno miente."], corrector=corrector))
    assert ("gobierno", [("gobierno", "NOUN")]) in tokens
    assert ("juan", []) in tokens


# --- Misspellings that Wiktionary lists as entries ---------------------------------------


def test_misspelling_entry_is_respelled_and_reported_once(corrector):
    assert normalize("dia", "dia", "NOUN", corrector) == [("día", "NOUN")]
    assert corrector.corrected == {("dia", ("dia", "NOUN"), ("día", "NOUN")): 1}


def test_respelled_inflected_form_is_corrected_to_its_lemma(corrector):
    # habia -> había (the correct spelling) -> haber (the dictionary word).
    assert normalize("habia", "habia", "VERB", corrector) == [("haber", "VERB")]
    assert corrector.corrected == {("habia", ("habia", "VERB"), ("haber", "VERB")): 1}


def test_misspelled_word_tagged_as_a_name_is_respelled(corrector):
    assert normalize("dia", "dia", "PROPN", corrector) == [("día", "NOUN")]


# --- Words typed without their accents ----------------------------------------------------

WORDS = {"detrás", "mañana", "comí", "ano", "año", "esta", "está", "niño", "ñiño", "día"}
SUBTITLE_COUNTS = {"detrás": 5386, "mañana": 26537, "comí": 500, "año": 9000, "niño": 11185}


@pytest.fixture
def restorer():
    return AccentRestorer(WORDS, SUBTITLE_COUNTS)


@pytest.mark.parametrize(
    "typed, restored",
    [("detras", "detrás"), ("manana", "mañana"), ("comi", "comí"), ("nino", "niño")],
)
def test_accentless_spelling_is_restored_to_the_one_word_it_can_be(restorer, typed, restored):
    assert restorer.restore(typed) == restored


def test_a_spelling_that_is_already_a_word_is_never_restored(restorer):
    assert restorer.restore("ano") is None  # año was probably meant, but ano is a word
    assert restorer.restore("esta") is None


def test_ties_are_never_guessed_and_rare_words_are_ignored():
    restorer = AccentRestorer({"pína", "piná", "remové"}, {"pína": 50, "piná": 50, "remové": 0})
    assert restorer.restore("pina") is None  # a tie
    assert restorer.restore("remove") is None  # below the subtitle-frequency floor


def test_a_misspelling_left_out_of_the_word_list_is_restored():
    # Wiktionary's "dia: misspelling of día" is excluded from the words by load_corrector.
    assert AccentRestorer({"día"}, {"día": 30000}).restore("dia") == "día"


def test_text_is_restored_keeping_capitalization_and_counted(restorer):
    text = "Despues: tres detras de casa. MANANA, y manana."
    restorer.words.add("después")
    restorer.counts = {**SUBTITLE_COUNTS, "después": 20000}
    restorer.by_fold.setdefault("despues", []).append("después")
    assert restorer.restore_text(text) == "Después: tres detrás de casa. MAÑANA, y mañana."
    assert restorer.restored[("manana", "mañana")] == 2


def test_model_restores_accents_before_tagging(corrector):
    # Tagged after restoration, "detrás" gets its real POS in context (not an invented
    # noun "detra"), and a capitalized sentence-initial word isn't mistaken for a name.
    corrector.restorer = AccentRestorer(
        {"detrás", "mañana", "después"}, {"detrás": 5386, "mañana": 26537, "después": 20000}
    )
    tokens = next(
        analyze(["Despues trabajo detras de mi casa por la manana."], corrector=corrector)
    )
    assert ("detrás", [("detrás", "ADV")]) in tokens
    assert ("mañana", [("mañana", "NOUN")]) in tokens
    assert tokens[0][0] == "después" and tokens[0][1]


def test_spellings_a_reform_superseded_are_modernized_in_the_text():
    restorer = AccentRestorer({"fue", "fué", "dio"}, {}, respellings={"fué": "fue", "dió": "dio"})
    text = "Fué allá. Aquello fué lo mejor, y se lo dió."
    assert restorer.restore_text(text) == "Fue allá. Aquello fue lo mejor, y se lo dio."
    assert restorer.restored[("fué", "fue")] == 2


def test_model_analyzes_a_respelled_fue_like_a_modern_one(corrector):
    """An old book's "fué" gets the same analysis as "fue", not a word of its own. (The
    tagger reads "fue" as ser even when it means "went": a known limit, same for both.)"""
    corrector.restorer = AccentRestorer(set(), {}, respellings={"fué": "fue"})
    old, modern = analyze(["Aquello fué lo mejor.", "Aquello fue lo mejor."], corrector=corrector)
    assert old[1] == modern[1] == ("fue", [("ser", "VERB")])


def test_old_accented_spellings_are_modernized_in_the_text():
    restorer = AccentRestorer(set(), {}, respellings={"á": "a", "ántes": "antes"})
    assert (
        restorer.restore_text("Á su lado, ántes de ir á casa.") == "A su lado, antes de ir a casa."
    )


def test_typed_accents_are_never_changed():
    restorer = AccentRestorer({"sudan"}, {"sudan": 300})
    assert restorer.restore("sudán") is None  # only missing marks are added


@pytest.mark.parametrize(
    "text, restored",
    [
        ("Vivo en el País de Gales.", "Vivo en el País de Gales."),  # a name mid-sentence
        ("Hola. Despues vamos.", "Hola. Después vamos."),  # a new sentence
        ("¿Despues?", "¿Después?"),
        ("Le dije: Despues.", "Le dije: Después."),
    ],
)
def test_capitalized_words_are_restored_only_at_a_sentence_start(text, restored):
    restorer = AccentRestorer({"después", "galés"}, {"después": 20000, "galés": 500})
    assert restorer.restore_text(text) == restored


def test_gracias_is_never_the_plural_of_gracia():
    # The transformer reads the thanks as gracia ("grace") in about 40% of Tatoeba tokens.
    assert normalize("Gracias", "gracia", "NOUN") == [("gracias", "NOUN")]
    assert normalize("gracias", "gracias", "INTJ") == [("gracias", "INTJ")]
    assert normalize("gracia", "gracia", "NOUN") == [("gracia", "NOUN")]  # the singular stays


# --- Multi-word expressions -------------------------------------------------------------


def matcher():
    from spanish_tutor.lexicon import ExpressionMatcher

    return ExpressionMatcher(
        [
            ("sin embargo", ("sin", "embargo")),
            ("darse cuenta", ("dar", "cuenta")),
            ("al menos", ("a", "el", "menos")),
            ("a veces", ("a", "vez")),
            ("a veces más", ("a", "vez", "más")),  # longer, to test longest-first
        ]
    )


def test_an_expression_becomes_one_item_and_its_parts_none():
    tokens = [
        ("sin", [("sin", "ADP")]),
        ("embargo", [("embargo", "NOUN")]),
        ("llueve", [("llover", "VERB")]),
    ]
    assert matcher().apply(tokens) == [
        ("sin embargo", [("sin embargo", "EXPR")]),
        ("embargo", []),  # never credited or taught as "embargo" (seizure) here
        ("llueve", [("llover", "VERB")]),
    ]


def test_expressions_match_inflected_and_contracted_forms():
    di_cuenta = [
        ("me", [("me", "PRON")]),
        ("di", [("dar", "VERB")]),
        ("cuenta", [("cuenta", "NOUN")]),
    ]
    assert matcher().apply(di_cuenta)[1] == ("di cuenta", [("darse cuenta", "EXPR")])
    al_menos = [("al", [("a", "ADP"), ("el", "DET")]), ("menos", [("menos", "ADV")])]
    assert matcher().apply(al_menos)[0] == ("al menos", [("al menos", "EXPR")])


def test_expressions_dont_match_across_a_name_or_from_inside_a_contraction():
    across = [("sin", [("sin", "ADP")]), ("juan", []), ("embargo", [("embargo", "NOUN")])]
    assert matcher().apply(across) == across
    from spanish_tutor.lexicon import ExpressionMatcher

    inside = [("al", [("a", "ADP"), ("el", "DET")]), ("menos", [("menos", "ADV")])]
    assert ExpressionMatcher([("el menos", ("el", "menos"))]).apply(inside) == inside


def test_the_longest_expression_wins():
    tokens = [("a", [("a", "ADP")]), ("veces", [("vez", "NOUN")]), ("más", [("más", "ADV")])]
    assert matcher().apply(tokens)[0] == ("a veces más", [("a veces más", "EXPR")])


def test_shared_loaders_are_cached():
    # Building the corrector reads Wiktionary and SUBTLEX (seconds). analyze() calls
    # load_corrector() on every use, and the Tatoeba run reports on the one it used,
    # so it must be one shared instance. (A new function once displaced its @cache.)
    from spanish_tutor import lexicon

    for loader in (
        lexicon.load_corrector,
        lexicon.load_wiktionary,
        lexicon.load_expression_matcher,
    ):
        assert hasattr(loader, "cache_info"), loader.__name__
