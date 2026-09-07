def first_vowels(word, leading_vowel=False):
    vowels = ['A', 'E', 'I', 'O', 'U', 'Y']
    value = ''
    first_vowel_found = False
    for letter in word:
        if letter not in vowels and first_vowel_found:
            break
        if letter in vowels:
            value += letter
            first_vowel_found = True

    if not leading_vowel:
        if value == 'EY':
            value = 'A'
        if value == 'EI':
            value = 'A'
        if value == 'EA':
            value = 'A'
        if value == 'AY':
            value = 'A'
        if value == 'AI':
            value = 'A'
        if value == 'Y':
            value = 'I'
        if value == 'UE':
            value = 'U'
    else:
        if value == 'OY':
            value = 'OI'

    if 'AA' in value:
        value = value.replace('AA', 'A')

    return value


def first_consonants(word):
    consonants = ['B', 'C', 'D', 'F', 'G', 'H', 'J', 'K', 'L', 'M', 'N', 'P', 'Q', 'R', 'S', 'T', 'X', 'W', 'V', 'Z']
    value = ''
    first_consonant_found = False
    for letter in word:
        if letter not in consonants and first_consonant_found:
            break
        if letter in consonants:
            value += letter
            first_consonant_found = True

    if 'CHR' in value:
        value = value.replace('CHR', 'KR')

    if 'GG' in value:
        value = value.replace('GG', 'G')

    if 'C' in value:
        value = value.replace('C', 'K')

    if 'CR' in value:
        value = value.replace('CR', 'KR')

    if 'CL' in value:
        value = value.replace('CL', 'KL')

    if 'PH' in value:
        value = value.replace('PH', 'F')

    if 'GH' in value:
        value = value.replace('GH', 'G')

    if 'GN' in value:
        value = value.replace('GN', 'N')

    if 'KN' in value:
        value = value.replace('KN', 'N')

    if 'PN' in value:
        value = value.replace('PN', 'N')

    if 'PS' in value:
        value = value.replace('PS', 'S')

    if 'WR' in value:
        value = value.replace('WR', 'R')

    if 'RH' in value:
        value = value.replace('RH', 'R')

    if 'WH' in value:
        value = value.replace('WH', 'W')

    return value


def has_leading_vowel(word):
    if word[0] in ['A', 'E', 'I', 'O', 'U', 'Y']:
        return True
    return False


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
