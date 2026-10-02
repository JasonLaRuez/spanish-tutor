"""Spanish characters typed on an English keyboard, as two-key markers.

    'a 'e 'i 'o 'u  ->  á é í ó ú     (a marker before the letter, either case)
    ~n              ->  ñ
    :u              ->  ü
    !palabra ?palabra  ->  ¡palabra ¿palabra   (a mark directly before a word)

So "?Qu'e tal? Ma~nana voy al ping:uino!" becomes "¿Qué tal? Mañana voy al pingüino!".
The learner gets to practice correct spelling without a Spanish keyboard. Marks after a
word are closing marks and stay as they are.
"""

import re

_ACUTE = dict(zip("aeiouAEIOU", "áéíóúÁÉÍÓÚ", strict=True))
_LETTER = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"

_ACCENTED_VOWEL = re.compile(r"'([aeiouAEIOU])")
# An opening mark: at the start, or after whitespace or an opening bracket or quote,
# and directly followed by a letter.
_OPENING_MARK = re.compile(rf"""(?:^|(?<=[\s(\[«"“]))([!?])(?=[{_LETTER}])""")


def expand_markers(text: str) -> str:
    """Replace the typed markers with the Spanish characters they stand for."""
    text = _ACCENTED_VOWEL.sub(lambda m: _ACUTE[m.group(1)], text)
    text = text.replace("~n", "ñ").replace("~N", "Ñ").replace(":u", "ü").replace(":U", "Ü")
    return _OPENING_MARK.sub(lambda m: "¡" if m.group(1) == "!" else "¿", text)
