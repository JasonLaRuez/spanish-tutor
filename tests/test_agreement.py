"""Krippendorff's alpha, checked against the published example."""

import pytest

from spanish_tutor.evaluation.agreement import alpha

# K. Krippendorff (2011), "Computing Krippendorff's Alpha-Reliability", section C: 4
# observers, 12 units, values 1-5, some missing (None). The paper gives alpha = 0.743
# nominal, 0.815 ordinal, 0.849 interval.
OBSERVERS = [
    [1, 2, 3, 3, 2, 1, 4, 1, 2, None, None, None],
    [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, None, 3],
    [None, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, None],
    [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, None],
]
UNITS = [[o[u] for o in OBSERVERS if o[u] is not None] for u in range(12)]


@pytest.mark.parametrize(
    "metric, expected", [("nominal", 0.743), ("ordinal", 0.815), ("interval", 0.849)]
)
def test_the_papers_example(metric, expected):
    assert alpha(UNITS, metric) == pytest.approx(expected, abs=0.0005)


def test_perfect_agreement_is_one():
    assert alpha([[1, 1, 1], [3, 3], [5, 5, 5, 5]]) == 1


def test_disagreement_beyond_chance_is_negative():
    assert alpha([[1, 5], [5, 1], [1, 5], [5, 1]]) < 0


def test_undefined_without_variation_or_pairs():
    assert alpha([[4, 4, 4], [4, 4]]) is None  # a judge that always says 4 tells us nothing
    assert alpha([[1], [2], [3]]) is None  # one rating per unit: nothing to compare


def test_ordinal_counts_near_misses_as_smaller_disagreements():
    near = alpha([[1, 1], [2, 2], [3, 3], [4, 5], [5, 5]], "ordinal")
    far = alpha([[1, 1], [2, 2], [3, 3], [1, 5], [5, 5]], "ordinal")
    assert near > far
