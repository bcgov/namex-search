from itertools import permutations

from namex_solr_api.config import Config
from namex_solr_api.services.base_solr.utils.formatting_helpers import prep_query_str
from namex_solr_api.services.base_solr.utils.query_builder import (
    SYNONYM_SKIP_WORDS,
    QueryBuilder,
)

from .formatting_helpers import (
    GLUED_FIRST_MIN_LEN,
    distinctive_coverage_terms,
    identity_term_groups,
    normalize_conflict_initials,
)
from .phonetic import keep_phonetic_match, replace_special_leading_sounds, sound_tail
from .synonym_helpers import (
    keep_family_synonym_highlights,
    name_surface_tokens,
    phrase_member_word_tokens,
    phrase_synonym_tokens,
)

BUCKET_SYNONYM = "synonym"
BUCKET_PHONETIC = "phonetic"
BUCKET_DROP = "drop"

_COVER_EXACT = "exact"
_COVER_STEM = "stem"
_COVER_FAMILY = "family"
_COVER_PHRASE = "phrase"
_COVER_PHONETIC = "phonetic"
_COVER_FUZZY = "fuzzy"
_COVER_STRONG = frozenset({_COVER_EXACT, _COVER_STEM})
_COVER_RANK = {
    _COVER_EXACT: 4,
    _COVER_STEM: 3,
    _COVER_FAMILY: 2,
    _COVER_FUZZY: 1,
    _COVER_PHONETIC: 1,
}
_COVER_TIER = {
    _COVER_EXACT: 2,
    _COVER_STEM: 2,
    _COVER_FAMILY: 1,
    _COVER_FUZZY: 1,
    _COVER_PHONETIC: 0,
}
_MIN_STEM = 4
_MIN_DISTINCTIVE = 4
_RANK_PREFIX_LEN = 3
_MIN_INITIALS_RUN = 2
_LONG_PHONETIC = 6
_STEM_SUFFIXES = (
    "ational",
    "ization",
    "iveness",
    "fulness",
    "ousness",
    "ations",
    "ings",
    "edly",
    "ally",
    "ation",
    "ators",
    "ences",
    "ances",
    "ions",
    "ing",
    "ers",
    "ied",
    "ies",
    "ion",
    "ity",
    "es",
    "ed",
    "er",
    "ly",
    "al",
    "s",
)


def _skip_tokens(query_terms: set[str]) -> set[str]:
    skip = {word.lower() for word in SYNONYM_SKIP_WORDS}
    for item in Config.DEFAULT_DESIGNATIONS:
        token = (item or "").lower().strip(".")
        if token and " " not in token:
            skip.add(token)
    return {token for token in skip if token not in query_terms}


def _usable_name_tokens(name: str, query_terms: set[str]) -> list[str]:
    normalized = prep_query_str(normalize_conflict_initials(name or ""), "replace")
    skip = _skip_tokens(query_terms)
    tokens = []
    for token in name_surface_tokens(normalized):
        lowered = token.lower().strip(".")
        if lowered and lowered not in skip:
            tokens.append(token)
    return tokens


def _edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, left_ch in enumerate(left, start=1):
        current = [i]
        for j, right_ch in enumerate(right, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (left_ch != right_ch)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def _fuzzy_allowed(term: str) -> int | None:
    fuzzy = QueryBuilder.get_fuzzy_str(term, 1, 2)
    if not fuzzy:
        return None
    return int(fuzzy[1:])


def _ck_fold(word: str) -> str:
    folded = replace_special_leading_sounds((word or "").upper())
    if folded.startswith("C"):
        return "K" + folded[1:]
    return folded


def _distinctive_name_anchor(name_tokens: list[str]) -> list[str]:
    for token in name_tokens:
        if len(token) >= _MIN_DISTINCTIVE:
            return [token]
    return list(name_tokens[:1]) if name_tokens else []


def _phonetic_distance_allowed(query: str, name_token: str) -> int:
    fuzzy = max(_fuzzy_allowed(query) or 0, _fuzzy_allowed(name_token) or 0)
    if min(len(query), len(name_token)) >= _LONG_PHONETIC:
        return max(fuzzy, 2)
    return fuzzy


def _real_phonetic_match(name_token: str, query: str) -> bool:
    folded_name = _ck_fold(name_token)
    folded_query = _ck_fold(query)
    if not keep_phonetic_match(folded_name, folded_query):
        return False
    if sound_tail(folded_name) != sound_tail(folded_query):
        return False
    distance = _edit_distance(folded_name, folded_query)
    return distance <= _phonetic_distance_allowed(query, name_token)


def _fuzzy_cover(query: str, name_tokens: list[str]) -> bool:
    allowed = _fuzzy_allowed(query)
    if allowed is None:
        return False
    query_lower = query.lower()
    return any(_edit_distance(query_lower, name_token.lower()) <= allowed for name_token in name_tokens)


def _initials_run_covered(letters: list[str], name_tokens: list[str]) -> bool:
    if not letters:
        return False
    glued = "".join(letter.lower() for letter in letters)
    lowered = [token.lower() for token in name_tokens]
    if glued in lowered:
        return True
    width = len(letters)
    for index in range(len(lowered) - width + 1):
        window = lowered[index : index + width]
        if all(
            len(token) == 1 and token == letters[offset].lower()
            for offset, token in enumerate(window)
        ):
            return True
    return False


def _light_stem(word: str) -> str:
    lowered = (word or "").lower()
    for suffix in _STEM_SUFFIXES:
        if lowered.endswith(suffix) and len(lowered) - len(suffix) >= _MIN_STEM:
            return lowered[: -len(suffix)]
    return lowered


def _tokens_share_stem(left: str, right: str) -> bool:
    query = (left or "").lower()
    name = (right or "").lower()
    if len(query) < _MIN_STEM or len(name) < _MIN_STEM:
        return False
    if query == name:
        return True
    if query.startswith(name) or name.startswith(query):
        return True
    return _light_stem(query) == _light_stem(name)


def _stem_cover(
    query: str,
    name_tokens: list[str],
    query_stems: set[str] | None = None,
    name_token_stems: dict[str, list[str]] | None = None,
) -> bool:
    stems = {stem.lower() for stem in (query_stems or set()) if stem}
    if keep_family_synonym_highlights(name_tokens, {query.lower()} | stems, stems, name_token_stems):
        return True
    for name_token in name_tokens:
        if _tokens_share_stem(query, name_token):
            return True
        for stem in stems:
            if _tokens_share_stem(stem, name_token) or name_token.lower().startswith(stem):
                return True
    return False


def _is_rank_slot(term: str) -> bool:
    if len(term) >= _MIN_DISTINCTIVE:
        return True
    return len(term) == _RANK_PREFIX_LEN and term.lower() not in SYNONYM_SKIP_WORDS


def _query_initials_covered(query_value: str, name_tokens: list[str]) -> int:
    terms = (query_value or "").split()
    index = 0
    while index < len(terms):
        if run := _letter_run(terms, index):
            if _initials_run_covered(run, name_tokens):
                return 1
            index += len(run)
        else:
            index += 1
    return 0


def _letter_run(terms: list[str], index: int) -> list[str] | None:
    term = terms[index]
    if not (len(term) == 1 and term.isalpha()):
        return None
    end = index + 1
    while end < len(terms) and len(terms[end]) == 1 and terms[end].isalpha():
        end += 1
    if end - index >= _MIN_INITIALS_RUN:
        return terms[index:end]
    return None


def _query_has_distinctive(terms: list[str]) -> bool:
    index = 0
    while index < len(terms):
        if run := _letter_run(terms, index):
            return True
        if len(terms[index]) >= _MIN_DISTINCTIVE:
            return True
        index += len(run) if run else 1
    return False


def _sets_for_term(mapping: dict[str, set[str]], term: str) -> set[str]:
    return mapping.get(term) or mapping.get(term.lower()) or set()


def _consecutive_concat_cover(query: str, name_tokens: list[str]) -> bool:
    target = (query or "").lower()
    if len(target) < GLUED_FIRST_MIN_LEN:
        return False
    pieces = [token.lower().strip(".") for token in name_tokens if token]
    for start in range(len(pieces)):
        acc = ""
        for used, token in enumerate(pieces[start:], start=1):
            acc += token
            if used >= 2 and acc == target:  # noqa: PLR2004
                return True
            if len(acc) > len(target):
                break
    return False


def cover_query_token(  # noqa: PLR0913
    query_token: str,
    name_tokens: list[str],
    family: set[str] | None = None,
    family_stems: set[str] | None = None,
    query_stems: set[str] | None = None,
    sound_tokens: list[str] | None = None,
    name_token_stems: dict[str, list[str]] | None = None,
) -> str | None:
    query = (query_token or "").strip()
    if not query:
        cover = None
    else:
        family = {token.lower() for token in (family or set())}
        family_stems = {stem.lower() for stem in (family_stems or set())}
        query_lower = query.lower()
        sound_tokens = name_tokens if sound_tokens is None else sound_tokens
        if (
            any(name_token.lower() == query_lower for name_token in name_tokens)
            or _consecutive_concat_cover(query, name_tokens)
        ):
            cover = _COVER_EXACT
        elif _stem_cover(query, name_tokens, query_stems, name_token_stems):
            cover = _COVER_STEM
        elif family and (
            keep_family_synonym_highlights(name_tokens, family, family_stems, name_token_stems)
            or phrase_synonym_tokens(name_tokens, family)
        ):
            cover = _COVER_FAMILY
        elif any(_real_phonetic_match(name_token, query) for name_token in sound_tokens):
            cover = _COVER_PHONETIC
        elif _fuzzy_cover(query, sound_tokens):
            cover = _COVER_FUZZY
        else:
            cover = None
    return cover


def _cover_for_run(  # noqa: PLR0913
    run: list[str],
    name_tokens: list[str],
    family_by_term: dict[str, set[str]],
    family_stems_by_term: dict[str, set[str]],
    query_stems_by_term: dict[str, set[str]],
    sound_tokens: list[str],
    name_token_stems: dict[str, list[str]] | None = None,
) -> str | None:
    if _initials_run_covered(run, name_tokens):
        return _COVER_EXACT
    glued = "".join(run)
    if len(run) >= _MIN_DISTINCTIVE and glued:
        return cover_query_token(
            glued,
            name_tokens,
            _sets_for_term(family_by_term, glued),
            _sets_for_term(family_stems_by_term, glued),
            _sets_for_term(query_stems_by_term, glued),
            sound_tokens,
            name_token_stems,
        )
    return None


def _iter_query_covers(  # noqa: PLR0913
    query_value: str,
    name: str,
    family_by_term: dict[str, set[str]] | None = None,
    family_stems_by_term: dict[str, set[str]] | None = None,
    query_stems_by_term: dict[str, set[str]] | None = None,
    name_token_stems: dict[str, list[str]] | None = None,
):
    terms = [term for term in (query_value or "").split() if term]
    if not terms:
        return
    query_terms = {term.lower() for term in terms}
    name_tokens = _usable_name_tokens(name, query_terms)
    family_by_term = family_by_term or {}
    family_stems_by_term = family_stems_by_term or {}
    query_stems_by_term = query_stems_by_term or {}
    require_all = not _query_has_distinctive(terms)
    awaiting_distinctive = not require_all
    index = 0
    while index < len(terms):
        term = terms[index]
        run = _letter_run(terms, index)
        if run:
            is_distinctive = True
            counts_for_rank = True
            consume_to = index + len(run)
        else:
            is_distinctive = len(term) >= _MIN_DISTINCTIVE
            counts_for_rank = is_distinctive or _is_rank_slot(term)
            consume_to = index + 1
        sound_tokens = (
            _distinctive_name_anchor(name_tokens)
            if awaiting_distinctive and is_distinctive
            else name_tokens
        )
        if run:
            cover = _cover_for_run(
                run,
                name_tokens,
                family_by_term,
                family_stems_by_term,
                query_stems_by_term,
                sound_tokens,
                name_token_stems,
            )
        else:
            cover = cover_query_token(
                term,
                name_tokens,
                _sets_for_term(family_by_term, term),
                _sets_for_term(family_stems_by_term, term),
                _sets_for_term(query_stems_by_term, term),
                sound_tokens,
                name_token_stems,
            )
        yield require_all, awaiting_distinctive, is_distinctive, counts_for_rank, cover
        if not require_all and awaiting_distinctive and is_distinctive and cover is not None:
            awaiting_distinctive = False
        index = consume_to


def distinctive_cover_count(  # noqa: PLR0913
    query_value: str,
    name: str,
    family_by_term: dict[str, set[str]] | None = None,
    family_stems_by_term: dict[str, set[str]] | None = None,
    query_stems_by_term: dict[str, set[str]] | None = None,
    name_token_stems: dict[str, list[str]] | None = None,
) -> int:
    covered, _strong = distinctive_cover_rank(
        query_value, name, family_by_term, family_stems_by_term, query_stems_by_term, name_token_stems
    )
    return covered


def distinctive_cover_rank(  # noqa: PLR0913
    query_value: str,
    name: str,
    family_by_term: dict[str, set[str]] | None = None,
    family_stems_by_term: dict[str, set[str]] | None = None,
    query_stems_by_term: dict[str, set[str]] | None = None,
    name_token_stems: dict[str, list[str]] | None = None,
) -> tuple[int, int]:
    covered = 0
    strong = 0
    for _require_all, _awaiting, _is_distinctive, counts_for_rank, cover in _iter_query_covers(
        query_value, name, family_by_term, family_stems_by_term, query_stems_by_term, name_token_stems
    ):
        if counts_for_rank and cover is not None:
            covered += 1
            if cover in _COVER_STRONG:
                strong += 1
    return covered, strong


def _token_cover(  # noqa: PLR0913
    term: str,
    name_tokens: list[str],
    family_by_term: dict[str, set[str]] | None,
    family_stems_by_term: dict[str, set[str]] | None,
    query_stems_by_term: dict[str, set[str]] | None,
    name_token_stems: dict[str, list[str]] | None = None,
) -> str | None:
    return cover_query_token(
        term,
        name_tokens,
        _sets_for_term(family_by_term or {}, term),
        _sets_for_term(family_stems_by_term or {}, term),
        _sets_for_term(query_stems_by_term or {}, term),
        name_tokens,
        name_token_stems,
    )


def _hyphen_glued_cover(
    glued_tokens: list[str] | None,
    name_tokens: list[str],
    *args,
) -> str | None:
    best = None
    for token in glued_tokens or ():
        cover = _token_cover(token, name_tokens, *args)
        if cover and (best is None or _COVER_RANK[cover] > _COVER_RANK[best]):
            best = cover
    return best


def conflict_rank_key(  # noqa: PLR0913
    query_value: str,
    name: str,
    family_by_term: dict[str, set[str]] | None = None,
    family_stems_by_term: dict[str, set[str]] | None = None,
    query_stems_by_term: dict[str, set[str]] | None = None,
    name_token_stems: dict[str, list[str]] | None = None,
    glued_tokens: list[str] | None = None,
) -> tuple[int, int, int, int, int, int, int]:
    """Pin closer leftover after identity is present.

    identity_present: first coverage token is family or better (bc counts).
    leftover_exact: last coverage token is exact/stem, not only family.
    first_identity_tier: 2 exact/stem, 1 family/fuzzy, 0 missing/phonetic.
    leftover_covered: last coverage term matched at all.
    prefix_complete: every prefix-span token is exact/stem.
    """
    terms = distinctive_coverage_terms(query_value)
    query_terms = {term.lower() for term in (query_value or "").split() if term}
    name_tokens = _usable_name_tokens(name, query_terms)
    initials_exact = _query_initials_covered(query_value, name_tokens)
    _covered, strong = distinctive_cover_rank(
        query_value, name, family_by_term, family_stems_by_term, query_stems_by_term, name_token_stems
    )
    leftover_covered = leftover_exact = identity_present = first_tier = prefix_complete = 0
    if terms:
        first_cover = _token_cover(
            terms[0], name_tokens, family_by_term, family_stems_by_term, query_stems_by_term, name_token_stems
        )
        if first_cover in _COVER_STRONG:
            first_tier = 2
        elif first_cover in {_COVER_FAMILY, _COVER_FUZZY}:
            first_tier = 1
        else:
            first_tier = 0
        identity_present = int(first_tier > 0)
        prefix_span = terms[:-1]
        if len(terms) >= 2:  # noqa: PLR2004
            leftover_cover = _token_cover(
                terms[-1],
                name_tokens,
                family_by_term,
                family_stems_by_term,
                query_stems_by_term,
                name_token_stems,
            )
            leftover_covered = int(leftover_cover is not None)
            leftover_exact = int(leftover_cover in _COVER_STRONG)
        else:
            prefix_span = []
        if prefix_span:
            prefix_complete = int(
                all(
                    _token_cover(
                        term,
                        name_tokens,
                        family_by_term,
                        family_stems_by_term,
                        query_stems_by_term,
                        name_token_stems,
                    )
                    in _COVER_STRONG
                    for term in prefix_span
                )
            )
        else:
            prefix_complete = 1
    glued_cover = _hyphen_glued_cover(
        glued_tokens,
        name_tokens,
        family_by_term,
        family_stems_by_term,
        query_stems_by_term,
        name_token_stems,
    )
    if glued_cover:
        leftover_covered = 1
        leftover_exact = max(leftover_exact, int(glued_cover in _COVER_STRONG))
        identity_present = 1
        first_tier = max(first_tier, _COVER_TIER[glued_cover])
        if not terms:
            prefix_complete = 1
    return (
        initials_exact,
        identity_present,
        leftover_exact,
        first_tier,
        leftover_covered,
        prefix_complete,
        strong,
    )


def rank_conflict_docs(  # noqa: PLR0913
    docs: list[dict],
    query_value: str,
    family_by_term: dict[str, set[str]] | None = None,
    family_stems_by_term: dict[str, set[str]] | None = None,
    query_stems_by_term: dict[str, set[str]] | None = None,
    name_token_stems: dict[str, list[str]] | None = None,
    glued_tokens: list[str] | None = None,
) -> list[dict]:
    return sorted(
        docs,
        key=lambda doc: tuple(
            -n
            for n in conflict_rank_key(
                query_value,
                doc.get("name") or "",
                family_by_term,
                family_stems_by_term,
                query_stems_by_term,
                name_token_stems,
                glued_tokens,
            )
        ),
    )


def visible_conflict_bucket(bucket: str) -> str:
    """Keep Solr hits on the page. Uncovered identity is synonym, ranked last."""
    return BUCKET_SYNONYM if bucket == BUCKET_DROP else bucket


def classify_conflict_bucket(  # noqa: PLR0913
    query_value: str,
    name: str,
    family_by_term: dict[str, set[str]] | None = None,
    family_stems_by_term: dict[str, set[str]] | None = None,
    query_stems_by_term: dict[str, set[str]] | None = None,
    name_token_stems: dict[str, list[str]] | None = None,
    glued_tokens: list[str] | None = None,
) -> str:
    terms = [term for term in (query_value or "").split() if term]
    if not terms:
        return BUCKET_SYNONYM

    saw_phonetic = False
    dropped = False
    for require_all, awaiting_distinctive, is_distinctive, _counts_for_rank, cover in _iter_query_covers(
        query_value, name, family_by_term, family_stems_by_term, query_stems_by_term, name_token_stems
    ):
        if cover == _COVER_PHONETIC and (require_all or (awaiting_distinctive and is_distinctive)):
            saw_phonetic = True

        if require_all and cover is None:
            dropped = True
            break
        if awaiting_distinctive and is_distinctive and cover is None:
            dropped = True
            break
    glued_cover = _hyphen_glued_cover(
        glued_tokens,
        _usable_name_tokens(name, {term.lower() for term in terms}),
        family_by_term,
        family_stems_by_term,
        query_stems_by_term,
        name_token_stems,
    )
    if glued_cover:
        return BUCKET_PHONETIC if glued_cover == _COVER_PHONETIC else BUCKET_SYNONYM
    if dropped:
        return BUCKET_DROP
    return BUCKET_PHONETIC if saw_phonetic else BUCKET_SYNONYM


_FIRST_QUALITY = {_COVER_EXACT: 2, _COVER_STEM: 2, _COVER_PHONETIC: 1, _COVER_FUZZY: 1}
_DESC_QUALITY = {_COVER_EXACT: 3, _COVER_STEM: 2, _COVER_FAMILY: 1, _COVER_PHRASE: 1}
_DESC_DIRECT = frozenset({_COVER_EXACT, _COVER_STEM, _COVER_FAMILY})
_DESC_COVERS = frozenset({_COVER_EXACT, _COVER_STEM, _COVER_FAMILY, _COVER_PHRASE})


def _with_plurals(words: set[str]) -> set[str]:
    expanded: set[str] = set()
    for word in words:
        lower = (word or "").lower()
        if not lower:
            continue
        expanded.add(lower)
        if lower.endswith("ies") and len(lower) > 4:  # noqa: PLR2004
            expanded.add(lower[:-3] + "y")
        elif lower.endswith("y") and len(lower) > 3 and lower[-2] not in "aeiou":  # noqa: PLR2004
            expanded.add(lower[:-1] + "ies")
        elif lower.endswith("s") and len(lower) > 3:  # noqa: PLR2004
            expanded.add(lower[:-1])
        else:
            expanded.add(lower + "s")
    return expanded


def _identity_group_cover(  # noqa: PLR0913
    group: list[str],
    name_tokens: list[str],
    sound_tokens: list[str],
    family_by_term: dict[str, set[str]],
    family_stems_by_term: dict[str, set[str]],
    query_stems_by_term: dict[str, set[str]],
    name_token_stems: dict[str, list[str]] | None,
) -> str | None:
    if len(group) >= 2:  # noqa: PLR2004
        return _cover_for_run(
            group,
            name_tokens,
            family_by_term,
            family_stems_by_term,
            query_stems_by_term,
            sound_tokens,
            name_token_stems,
        )
    cover = cover_query_token(
        group[0],
        name_tokens,
        _sets_for_term(family_by_term, group[0]),
        _sets_for_term(family_stems_by_term, group[0]),
        _sets_for_term(query_stems_by_term, group[0]),
        sound_tokens,
        name_token_stems,
    )
    if cover == _COVER_FAMILY:
        return None
    return cover


def _descriptive_group_cover(  # noqa: PLR0913
    group: list[str],
    name_tokens: list[str],
    family_by_term: dict[str, set[str]],
    family_stems_by_term: dict[str, set[str]],
    query_stems_by_term: dict[str, set[str]],
    name_token_stems: dict[str, list[str]] | None,
    other_terms: set[str] | None = None,
) -> str | None:
    if len(group) >= 2:  # noqa: PLR2004
        cover = _cover_for_run(
            group,
            name_tokens,
            family_by_term,
            family_stems_by_term,
            query_stems_by_term,
            name_tokens,
            name_token_stems,
        )
        return cover if cover in _DESC_COVERS else None
    query = group[0].lower()
    if any(token.lower() in _with_plurals({query}) and token.lower() != query for token in name_tokens):
        return _COVER_STEM
    family = _with_plurals(_sets_for_term(family_by_term, group[0]) | {query})
    cover = cover_query_token(
        group[0],
        name_tokens,
        family,
        _sets_for_term(family_stems_by_term, group[0]),
        _sets_for_term(query_stems_by_term, group[0]),
        name_tokens,
        name_token_stems,
    )
    if cover in _DESC_DIRECT:
        return cover
    phrase_tokens = [token for token in name_tokens if token.lower() not in (other_terms or set())]
    if phrase_member_word_tokens(phrase_tokens, family):
        return _COVER_PHRASE
    return None


def _leading_concat_exact(query: str, name_tokens: list[str]) -> bool:
    target = (query or "").lower()
    if len(target) < GLUED_FIRST_MIN_LEN or not name_tokens:
        return False
    acc = ""
    for used, token in enumerate(name_tokens, start=1):
        acc += token.lower().strip(".")
        if used >= 2 and acc == target:  # noqa: PLR2004
            return True
        if len(acc) >= len(target):
            return False
    return False


def _whole_first_word(group: list[str], name_tokens: list[str]) -> int:
    if not name_tokens:
        return 0
    if len(group) >= 2:  # noqa: PLR2004
        return int(_initials_run_covered(group, name_tokens[: len(group)]))
    if name_tokens[0].lower() == group[0].lower():
        return 1
    return int(_leading_concat_exact(group[0], name_tokens))


def _first_word_leads(group: list[str], name_tokens: list[str], cover: str | None) -> int:
    if cover not in _COVER_STRONG or not name_tokens:
        return 0
    if len(group) >= 2:  # noqa: PLR2004
        return int(_initials_run_covered(group, name_tokens[: len(group)]))
    first = name_tokens[0]
    return int(first.lower() == group[0].lower() or _tokens_share_stem(group[0], first))


def select_identity_descriptive_docs(  # noqa: PLR0913
    docs: list[dict],
    distinctive: list[str],
    descriptive: list[str],
    family_by_term: dict[str, set[str]] | None = None,
    family_stems_by_term: dict[str, set[str]] | None = None,
    query_stems_by_term: dict[str, set[str]] | None = None,
    name_token_stems: dict[str, list[str]] | None = None,
) -> list[dict]:
    identity_groups = identity_term_groups(distinctive)
    descriptive_groups = identity_term_groups(descriptive)
    if not identity_groups:
        return docs
    family_by_term = family_by_term or {}
    family_stems_by_term = family_stems_by_term or {}
    query_stems_by_term = query_stems_by_term or {}
    query_terms = {term.lower() for group in [*identity_groups, *descriptive_groups] for term in group}
    descriptive_words = {term.lower() for group in descriptive_groups for term in group}
    excluded_by_group = [
        descriptive_words - {term.lower() for term in group} for group in descriptive_groups
    ]
    ranked: list[tuple] = []
    for doc in docs:
        name_tokens = _usable_name_tokens(doc.get("name") or "", query_terms)
        identity_covers = []
        for index, group in enumerate(identity_groups):
            if index == 0 and len(group) == 1 and _leading_concat_exact(group[0], name_tokens):
                identity_covers.append(_COVER_EXACT)
                continue
            leading = name_tokens[:1] if index == 0 and len(group) == 1 else name_tokens
            identity_covers.append(
                _identity_group_cover(
                    group,
                    leading,
                    leading,
                    family_by_term,
                    family_stems_by_term,
                    query_stems_by_term,
                    name_token_stems,
                )
            )
        if identity_covers[0] is None:
            continue
        if identity_covers[0] == _COVER_PHONETIC:
            doc["bucket"] = BUCKET_PHONETIC
        elif identity_covers[0] == _COVER_FUZZY:
            doc["bucket"] = BUCKET_SYNONYM
        descriptive_covers = [
            _descriptive_group_cover(
                group,
                name_tokens,
                family_by_term,
                family_stems_by_term,
                query_stems_by_term,
                name_token_stems,
                excluded,
            )
            for group, excluded in zip(descriptive_groups, excluded_by_group, strict=True)
        ]
        ranked.append((doc, identity_covers, descriptive_covers, name_tokens))

    def sort_key(item: tuple) -> tuple[int, int, int, int, int, int, int, int, int, int]:
        _doc, identity_covers, descriptive_covers, name_tokens = item
        direct = [cover for cover in descriptive_covers if cover in _DESC_DIRECT]
        covered = [cover for cover in descriptive_covers if cover is not None]
        descriptive_ok = bool(covered)
        descriptive_quality = max((_DESC_QUALITY.get(cover, 0) for cover in (direct or covered)), default=0)
        later_hits = sum(cover is not None for cover in identity_covers[1:])
        described_tokens = sum(
            len(group) for group, cover in zip(descriptive_groups, descriptive_covers, strict=True) if cover
        )
        leftover = len(name_tokens) - 1 - later_hits - described_tokens
        return (
            later_hits,
            _whole_first_word(identity_groups[0], name_tokens),
            _FIRST_QUALITY.get(identity_covers[0], 0),
            _first_word_leads(identity_groups[0], name_tokens, identity_covers[0]),
            int(bool(direct)),
            len(covered),
            int(descriptive_ok and leftover <= 0),
            descriptive_quality,
            -max(leftover, 0),
            sum(_FIRST_QUALITY.get(cover, 0) for cover in identity_covers[1:]),
        )

    ranked.sort(key=sort_key, reverse=True)
    return [item[0] for item in ranked]


def _folded_forms(word: str) -> set[str]:
    forms = {word}
    if len(word) > 2 and word.endswith("y"):  # noqa: PLR2004
        forms.add(word[:-1] + "ies")
    if len(word) > 3 and word.endswith("ies"):  # noqa: PLR2004
        forms.add(word[:-3] + "y")
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):  # noqa: PLR2004
        forms.add(word[:-1])
    if len(word) > 2:  # noqa: PLR2004
        forms.add(word + "s")
    return forms


def _pattern_word_match(
    query: str,
    token: str,
    query_stems_by_term: dict[str, set[str]] | None,
    name_token_stems: dict[str, list[str]] | None,
) -> str | None:
    word = (query or "").lower()
    surface = (token or "").lower().strip(".")
    if not word or not surface:
        return None
    if word == surface:
        return _COVER_EXACT
    if surface in _folded_forms(word) or word in _folded_forms(surface):
        return _COVER_STEM
    if _light_stem(word) == _light_stem(surface):
        return _COVER_STEM
    query_stems = {stem.lower() for stem in (query_stems_by_term or {}).get(word, set()) if stem}
    token_stems = {stem.lower() for stem in (name_token_stems or {}).get(surface, []) if stem}
    if query_stems and token_stems and query_stems & token_stems:
        return _COVER_STEM
    return None


def _rearranged_quality(
    words: list[str],
    window: list[str],
    query_stems_by_term: dict[str, set[str]] | None,
    name_token_stems: dict[str, list[str]] | None,
) -> str | None:
    if all(
        _pattern_word_match(word, token, query_stems_by_term, name_token_stems)
        for word, token in zip(words, window, strict=True)
    ):
        return None
    best = None
    for order in permutations(words):
        qualities = [
            _pattern_word_match(word, token, query_stems_by_term, name_token_stems)
            for word, token in zip(order, window, strict=True)
        ]
        if not all(qualities):
            continue
        quality = _COVER_EXACT if all(item == _COVER_EXACT for item in qualities) else _COVER_STEM
        if quality == _COVER_EXACT:
            return quality
        best = quality
    return best


def _adjacent_pattern_rank(
    words: list[str],
    name: str,
    query_stems_by_term: dict[str, set[str]] | None,
    name_token_stems: dict[str, list[str]] | None,
) -> tuple[int, int, int] | None:
    tokens = _usable_name_tokens(name, {word.lower() for word in words})
    width = len(words)
    outside = len(tokens) - width
    if width < 2 or outside < 0 or outside > 1:  # noqa: PLR2004
        return None
    quality = _rearranged_quality(
        words,
        tokens[:width],
        query_stems_by_term,
        name_token_stems,
    )
    if quality is None:
        return None
    return (1, int(quality == _COVER_EXACT), -outside)


def promote_adjacent_patterns(  # noqa: PLR0913
    forward: list[dict],
    candidates: list[dict],
    distinctive: list[str],
    rows: int,
    query_stems_by_term: dict[str, set[str]] | None = None,
    name_token_stems: dict[str, list[str]] | None = None,
) -> list[dict]:
    """Place a leading rearrangement, with at most one following word, ahead of the forward list."""
    words = [word for word in distinctive if word]
    ranked: list[tuple[tuple[int, int, int], dict]] = []
    for doc in candidates:
        rank = _adjacent_pattern_rank(words, doc.get("name") or "", query_stems_by_term, name_token_stems)
        if rank is not None:
            ranked.append((rank, doc))
    ranked.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    top: list[dict] = []
    for _rank, doc in ranked:
        name = (doc.get("name") or "").upper()
        if not name or name in seen:
            continue
        seen.add(name)
        top.append({**doc, "bucket": BUCKET_SYNONYM})
    phonetic = []
    rest = []
    for doc in forward:
        name = (doc.get("name") or "").upper()
        if not name or name in seen:
            continue
        seen.add(name)
        if doc.get("bucket") == BUCKET_PHONETIC:
            phonetic.append(doc)
        else:
            rest.append(doc)
    if rows <= 0:
        return top + rest + phonetic
    if len(phonetic) >= rows:
        return phonetic[:rows]
    top = top[: rows - len(phonetic)]
    rest_room = rows - len(phonetic) - len(top)
    return top + rest[:rest_room] + phonetic
