_VOWELS = ['A', 'E', 'I', 'O', 'U', 'Y']
_NON_LEADING_VOWEL_FOLDS = {
    'EY': 'A',
    'EI': 'A',
    'EA': 'A',
    'AY': 'A',
    'AI': 'A',
    'Y': 'I',
    'UE': 'U',
}
_CONSONANTS = ['B', 'C', 'D', 'F', 'G', 'H', 'J', 'K', 'L', 'M', 'N', 'P', 'Q', 'R', 'S', 'T', 'X', 'W', 'V', 'Z']
_CONSONANT_FOLDS = (
    ('CHR', 'KR'),
    ('GG', 'G'),
    ('C', 'K'),
    ('CR', 'KR'),
    ('CL', 'KL'),
    ('PH', 'F'),
    ('GH', 'G'),
    ('GN', 'N'),
    ('KN', 'N'),
    ('PN', 'N'),
    ('PS', 'S'),
    ('WR', 'R'),
    ('RH', 'R'),
    ('WH', 'W'),
)


def first_vowels(word, leading_vowel=False):
    value = ''
    first_vowel_found = False
    for letter in word:
        if letter not in _VOWELS and first_vowel_found:
            break
        if letter in _VOWELS:
            value += letter
            first_vowel_found = True

    if not leading_vowel:
        value = _NON_LEADING_VOWEL_FOLDS.get(value, value)
    elif value == 'OY':
        value = 'OI'

    if 'AA' in value:
        value = value.replace('AA', 'A')

    return value


def first_consonants(word):
    value = ''
    first_consonant_found = False
    for letter in word:
        if letter not in _CONSONANTS and first_consonant_found:
            break
        if letter in _CONSONANTS:
            value += letter
            first_consonant_found = True

    for old, new in _CONSONANT_FOLDS:
        if old in value:
            value = value.replace(old, new)

    return value


def has_leading_vowel(word):
    return word[0] in _VOWELS


def replace_special_leading_sounds(word):
    for special_leading_sound, replacement in [['QU', 'KW'], ['EX', 'X'], ['MAC', 'MC']]:
        if word[: len(special_leading_sound)] == special_leading_sound:
            word = replacement + word[len(special_leading_sound) :]

    return word


def keep_phonetic_match(word, query):
    word = (word or "").upper()
    query = (query or "").upper()
    if not word or not query:
        return False

    word = replace_special_leading_sounds(word)
    query = replace_special_leading_sounds(query)
    if not word or not query:
        return False

    word_has_leading_vowel = has_leading_vowel(word)
    query_has_leading_vowel = has_leading_vowel(query)

    word_first_consonant = first_consonants(word)
    query_first_consonant = first_consonants(query)

    query_first_vowels = first_vowels(query, query_has_leading_vowel)
    word_first_vowels = first_vowels(word, word_has_leading_vowel)

    if query_has_leading_vowel:
        query_sound = query_first_vowels + query_first_consonant
    else:
        query_sound = query_first_consonant + query_first_vowels

    if word_has_leading_vowel:
        word_sound = word_first_vowels + word_first_consonant
    else:
        word_sound = word_first_consonant + word_first_vowels

    return word_sound == query_sound


def primary_metaphone(word: str, max_length: int = 8) -> str:  # noqa: PLR0912, PLR0915
    """Primary Double Metaphone code. Alternate codes are not produced."""
    raw = "".join(char for char in (word or "").upper() if "A" <= char <= "Z")
    if not raw or max_length <= 0:
        return ""
    padded = f"{raw}      "
    code: list[str] = []
    index = 1 if raw.startswith(("GN", "KN", "PN", "WR", "PS")) else 0

    def add(value: str) -> None:
        if value and len("".join(code)) < max_length:
            code.append(value)

    while index < len(raw) and len("".join(code)) < max_length:
        char = padded[index]
        if index > 0 and char == padded[index - 1] and char != "C":
            index += 1
            continue
        nxt = padded[index + 1]
        if char in "AEIOU":
            if index == 0:
                add(char)
            index += 1
        elif char == "B":
            add("P")
            index += 2 if nxt == "B" else 1
        elif char == "C":
            if nxt == "H":
                add("X")
                index += 2
            elif nxt in "IEY":
                add("S")
                index += 1
            else:
                add("K")
                index += 2 if nxt == "C" else 1
        elif char == "D":
            if padded[index:index + 2] == "DG" and padded[index + 2] in "IEY":
                add("J")
                index += 3
            else:
                add("T")
                index += 2 if nxt == "D" else 1
        elif char == "F":
            add("F")
            index += 2 if nxt == "F" else 1
        elif char == "G":
            if nxt == "H":
                add("" if index > 0 else "K")
                index += 2
            elif nxt in "IEY":
                add("J")
                index += 1
            else:
                add("K")
                index += 2 if nxt == "G" else 1
        elif char == "H":
            if nxt in "AEIOU" and (index == 0 or padded[index - 1] not in "AEIOU"):
                add("H")
            index += 1
        elif char == "J":
            add("J")
            index += 1
        elif char == "K":
            add("K")
            index += 2 if nxt == "K" else 1
        elif char == "L":
            add("L")
            index += 2 if nxt == "L" else 1
        elif char == "M":
            add("M")
            index += 2 if nxt == "M" else 1
        elif char == "N":
            add("N")
            index += 2 if nxt == "N" else 1
        elif char == "P":
            if nxt == "H":
                add("F")
                index += 2
            else:
                add("P")
                index += 2 if nxt == "P" else 1
        elif char == "Q":
            add("K")
            index += 1
        elif char == "R":
            add("R")
            index += 2 if nxt == "R" else 1
        elif char == "S":
            if nxt == "H" or padded[index:index + 3] in {"SIO", "SIA"}:
                add("X")
                index += 2 if nxt == "H" else 3
            else:
                add("S")
                index += 2 if nxt == "S" else 1
        elif char == "T":
            if padded[index:index + 3] in {"TIA", "TIO", "TCH"}:
                add("X")
                index += 3
            elif nxt == "H":
                add("0")
                index += 2
            else:
                add("T")
                index += 2 if nxt == "T" else 1
        elif char == "V":
            add("F")
            index += 1
        elif char == "W":
            if nxt in "AEIOU":
                add("A")
            index += 1
        elif char == "X":
            add("KS")
            index += 1
        elif char == "Y":
            if nxt in "AEIOU":
                add("A")
            index += 1
        elif char == "Z":
            add("S")
            index += 1
        else:
            index += 1
    return "".join(code)[:max_length]


def sound_tail(word: str) -> str:
    upper = replace_special_leading_sounds((word or "").upper())
    if not upper:
        return ""
    index = 0
    if has_leading_vowel(upper):
        while index < len(upper) and upper[index] in _VOWELS:
            index += 1
        while index < len(upper) and upper[index] in _CONSONANTS:
            index += 1
    else:
        while index < len(upper) and upper[index] in _CONSONANTS:
            index += 1
        while index < len(upper) and upper[index] in _VOWELS:
            index += 1
    return upper[index:]
