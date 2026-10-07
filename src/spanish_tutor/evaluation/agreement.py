"""Agreement between ratings: Krippendorff's alpha.

Used for the judges (slice 4.4d): a judge that rates the same line 5 times is 5 "coders";
alpha says how much more they agree than chance would. 1 is perfect, 0 is chance, below 0
is systematic disagreement. Krippendorff's own guide: rely on alpha >= 0.80, draw only
tentative conclusions from 0.667 to 0.80, and none below.

Alpha rather than a simple agreement rate because it corrects for chance and for how the
scores are spread (a judge that always says 4 agrees with itself perfectly and tells us
nothing), handles any number of raters and missing ratings, and has a metric for ordinal
scales: on a 1-5 scale, 4 vs 5 is a smaller disagreement than 1 vs 5.

Computed from the coincidence matrix, as in K. Krippendorff (2011), "Computing
Krippendorff's Alpha-Reliability"; tests/test_agreement.py checks the paper's example.
"""

from collections import Counter
from collections.abc import Hashable, Iterable, Sequence


def alpha(units: Iterable[Sequence[Hashable]], metric: str = "ordinal") -> float | None:
    """Krippendorff's alpha. `units`: each unit's ratings (missing ones left out); units with
    fewer than 2 ratings are ignored. `metric`: 'nominal', 'ordinal' or 'interval'. None
    when it's undefined: nothing pairable, or every rating the same value."""
    coincidences: Counter = Counter()
    for unit in units:
        values = list(unit)
        m = len(values)
        if m < 2:
            continue
        for i, a in enumerate(values):
            for j, b in enumerate(values):
                if i != j:
                    coincidences[a, b] += 1 / (m - 1)
    totals: Counter = Counter()
    for (a, _), count in coincidences.items():
        totals[a] += count
    n = sum(totals.values())
    if n <= 1 or len(totals) < 2:
        return None
    values = sorted(totals)
    distance = _metric(metric, values, totals)
    observed = sum(count * distance(a, b) for (a, b), count in coincidences.items()) / n
    expected = sum(totals[a] * totals[b] * distance(a, b) for a in values for b in values) / (
        n * (n - 1)
    )
    return 1 - observed / expected


def _metric(name: str, values: list, totals: Counter):
    if name == "nominal":
        return lambda a, b: 0.0 if a == b else 1.0
    if name == "interval":
        return lambda a, b: float(a - b) ** 2
    if name == "ordinal":
        # The distance between two ranks counts the ratings that lie between them: the
        # sum of n_g from the lower to the higher, minus half of each end.
        position = {v: i for i, v in enumerate(values)}

        def ordinal(a, b) -> float:
            low, high = sorted((position[a], position[b]))
            between = sum(totals[values[g]] for g in range(low, high + 1))
            return (between - (totals[values[low]] + totals[values[high]]) / 2) ** 2

        return ordinal
    raise ValueError(f"unknown metric {name!r}")
