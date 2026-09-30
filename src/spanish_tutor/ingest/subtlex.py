"""SUBTLEX-ESP word-form frequencies (Cuetos et al., 2011; CC BY-NC-SA 4.0).

The workbook stores the list in three side-by-side column blocks (A-D, F-I, K-N), each
`Word | Freq. count | Freq. per million | Log freq.`, alphabetical, with a few duplicate
words. Entries are word FORMS, with no lemmas or POS; see seed.py for how forms are
mapped to (lemma, pos).
"""

from collections import Counter
from pathlib import Path

import openpyxl

from spanish_tutor.lexicon import normalize_text

BLOCK_STARTS = (0, 5, 10)  # zero-based column index of each block's Word column


def load_counts(path: Path) -> Counter[str]:
    """Raw subtitle counts per normalized word form; duplicate rows are summed."""
    counts: Counter[str] = Counter()
    workbook = openpyxl.load_workbook(path, read_only=True)
    try:
        sheet = workbook.worksheets[0]
        for row in sheet.iter_rows(min_row=2, values_only=True):
            for start in BLOCK_STARTS:
                word, count = row[start : start + 2]
                if word is None or count is None:
                    continue
                counts[normalize_text(str(word))] += int(count)
    finally:
        workbook.close()
    return counts
