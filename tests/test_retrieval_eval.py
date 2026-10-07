"""Retrieval quality (slice 4.4e): context relevance and faithfulness, with fake judges."""

from spanish_tutor.conversation import Generation
from spanish_tutor.evaluation import retrieval
from spanish_tutor.evaluation.retrieval import (
    QUESTIONS,
    TRAPS,
    Claim,
    FaithfulnessJudgment,
    RelevanceJudgment,
    SentenceRelevance,
    faithfulness_prompt,
    judge_faithfulness,
    judge_relevance,
    relevance_prompt,
    summarize,
)
from spanish_tutor.evaluation.translation import start_run


def scripted(*replies):
    queue = list(replies)

    def ask(schema, prompt):
        return Generation(queue.pop(0), input_tokens=500, output_tokens=100)

    return ask


def relevance(*labels):
    return RelevanceJudgment(
        sentences=[SentenceRelevance(number=n, label=label) for n, label in enumerate(labels, 1)]
    )


EXAMPLES = [
    {
        "topic": "el jardín",
        "text": "Me gusta regar las plantas.",
        "hits": [
            {"id": 1, "es": "Riego las plantas.", "en": "I water the plants."},
            {"id": 2, "es": "El perro come.", "en": "The dog eats."},
        ],
    },
    {"topic": "el jardín", "text": "Hola.", "hits": []},  # nothing retrieved: nothing judged
]


def discussion(number, question, reply, trap=False):
    return {
        "content_id": 19,
        "title": "El cuento del pollo",
        "number": number,
        "question": question,
        "passages": ["Una bellota cae en su cabeza."],
        "reply": reply,
        "trap": trap,
    }


def test_the_questions_include_one_trap_per_text():
    assert {len(q) for q in QUESTIONS.values()} == {5}
    assert TRAPS == {(19, 5), (20, 5)}


def test_prompts_carry_what_the_judge_needs():
    prompt = relevance_prompt("el jardín", "Me gusta regar.", ["Riego las plantas."])
    assert "Topic: el jardín" in prompt and "1. Riego las plantas." in prompt
    prompt = faithfulness_prompt("El pollo", "TEXTO", "¿Por qué?", ["pasaje"], "Porque sí.")
    assert "TEXTO" in prompt and "[1] pasaje" in prompt and "Tutor's reply: Porque sí." in prompt


def test_relevance_labels_are_stored_per_retrieved_sentence(conn):
    run_id = start_run(conn, "judge", "v", {})
    calls = judge_relevance(conn, run_id, "r1", EXAMPLES, scripted(relevance("relevant", "off")))
    assert len(calls) == 1  # the message with nothing retrieved isn't judged
    rows = conn.execute(
        "SELECT i.source_ref, r.label FROM ratings AS r JOIN eval_items AS i USING (item_id) "
        "ORDER BY r.rating_id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("retrieval:examples:r1:1:tatoeba:1", "relevant"),
        ("retrieval:examples:r1:1:tatoeba:2", "off"),
    ]


def test_faithfulness_counts_claims_supported_by_the_passages(conn):
    run_id = start_run(conn, "judge", "v", {})
    judge_relevance(conn, run_id, "r1", EXAMPLES, scripted(relevance("relevant", "partly")))
    discussions = [
        discussion(1, "¿Por qué cree el pollo que el cielo ha caído?", "Porque una bellota cae."),
        discussion(5, "¿Cómo se llama el rey?", "Se llama Felipe.", trap=True),
    ]
    judgments = [
        FaithfulnessJudgment(
            claims=[
                Claim(claim="An acorn falls on the chick.", support="passages"),
                Claim(claim="The chick runs to the king.", support="text_only"),
            ],
            passages_relevance="relevant",
            says_text_is_silent=False,
        ),
        FaithfulnessJudgment(
            claims=[Claim(claim="The king is called Felipe.", support="not_in_text")],
            passages_relevance="off",
            says_text_is_silent=False,
        ),
    ]
    judge_faithfulness(conn, run_id, "r1", discussions, {19: "TEXTO"}, scripted(*judgments))

    found = summarize(conn, run_id)
    assert (found["faithfulness"].k, found["faithfulness"].n) == (1, 3)
    assert [c["claim"] for c in found["text_only"]] == ["The chick runs to the king."]
    assert [c["claim"] for c in found["invented"]] == ["The king is called Felipe."]
    assert found["traps"] == [("¿Cómo se llama el rey?", False, "Se llama Felipe.")]
    assert (found["passages_relevant"].k, found["passages_relevant"].n) == (1, 2)
    assert (found["examples_relevant"].k, found["examples_relevant_or_partly"].k) == (1, 2)
    assert "NOT IN THE TEXT: The king is called Felipe." in retrieval.format_summary(found)
    assert "trap: ¿Cómo se llama el rey? -> ANSWERS" in retrieval.format_summary(found)
