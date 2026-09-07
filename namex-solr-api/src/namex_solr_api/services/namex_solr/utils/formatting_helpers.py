"""Solr formatting functions."""
import re
from dataclasses import dataclass

from flask import current_app

from namex_solr_api.services.base_solr.utils.formatting_helpers import prep_query_str
from namex_solr_api.services.base_solr.utils.query_builder import SYNONYM_SKIP_WORDS

# Punct/space between two single letters (H&H, H.H., H. & H.). Does not insert "and".
_INITIAL_PUNCT = re.compile(
    r"(?i)(?<![a-z])([a-z])(?:[\s]*[&./,!_\-'@+=]+[\s]*)+([a-z])\.?(?![a-z])"
)
# Pairwise loop cannot consume the last initial's period when a space (or end of
# string) follows: "j. r. m. investments" → "j r m. investments".
_DANGLING_INITIAL_DOT = re.compile(r"(?i)(?<![a-z])([a-z])\.(?![a-z])")
# Same leftover when the period is glued to the next word: "j r m.investments".
_INITIAL_DOT_WORD = re.compile(r"(?i)(?<![a-z])([a-z])\.([a-z]{2,})")
_TWO_LETTER = re.compile(r"(?i)(?<![a-z])([a-z]{2})(?![a-z])")
# Do not split 2-letter tokens that this repo already treats as whole words:
# - 2-letter English stopwords from namex-solr/.../lang/stopwords_en.txt (includes "in")
# - "bc" from possible.conflicts British Columbia fold and NameX _name_pre_processing
_KEEP_TWO_LETTER = frozenset({
    "an", "as", "at", "be", "by", "if", "in", "is", "it", "no", "of", "on", "or", "to",
    "bc", "ca"
})
_DISTINCTIVE_LETTER_RUN = 4


def _glue_distinctive_letter_runs(text: str) -> str:
    tokens = text.split()
    glued: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if len(token) == 1 and token.isalpha():
            end = index + 1
            while end < len(tokens) and len(tokens[end]) == 1 and tokens[end].isalpha():
                end += 1
            if end - index >= _DISTINCTIVE_LETTER_RUN:
                glued.append("".join(tokens[index:end]))
                index = end
                continue
        glued.append(token)
        index += 1
    return " ".join(glued)


def normalize_conflict_initials(query: str | None) -> str:
    """Normalize glued/punctuated initials to the spaced form GCP AND-split already handles.

    Conflict path only. Runs before prep_query_str / QueryBuilder whitespace split.
    """
    if not query:
        return ""

    normalized = query
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = _INITIAL_PUNCT.sub(r"\1 \2", normalized)

    normalized = _DANGLING_INITIAL_DOT.sub(r"\1 ", normalized)
    normalized = _INITIAL_DOT_WORD.sub(r"\1 \2", normalized)

    def split_glued_initials(match: re.Match) -> str:
        token = match.group(1)
        if token.lower() in _KEEP_TWO_LETTER:
            return token
        return f"{token[0]} {token[1]}"

    normalized = _TWO_LETTER.sub(split_glued_initials, normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return _glue_distinctive_letter_runs(normalized)


def remove_designation_tokens(query: str, designations: list[str] | None = None) -> str:
    """Remove DESIGNATIONS tokens anywhere in the query.

    Match-prep equivalent of NameX words_to_filter_from_name(): drop skip words
    (be, the, and, ...) and designation tokens (ltd, inc, llc, ...) before AND-split.
    Spaced phrases are ignored here; they are trailing-strip only.
    Ranking/boosts must keep the raw query and should not call this.
    """
    if not query:
        return ""

    if designations is None:
        designations = current_app.config.get("DESIGNATIONS") or []

    skip = {str(token).lower() for token in designations if token and " " not in str(token)}
    return " ".join(token for token in query.split() if token.lower() not in skip)


def strip_trailing_designations(query: str, designations: list[str] | None = None) -> str:
    """Strip trailing skip tokens and legal-designation phrases.

    Longest match first so 'limited liability company' is removed as a phrase.
    Repeats until nothing trailing matches (kind ltd inc → kind).
    Requires a preceding space (or whole-string match) so skip tokens like
    'o' / 'on' do not clip 'hello' / 'boston'.
    """
    if not query:
        return ""

    if designations is None:
        designations = current_app.config.get("DESIGNATIONS") or []

    query = query.lower().strip()
    phrases = sorted({str(item).lower() for item in designations if item}, key=len, reverse=True)
    changed = True
    while query and changed:
        changed = False
        for phrase in phrases:
            if query == phrase:
                return ""
            suffix = f" {phrase}"
            if query.endswith(suffix):
                query = query[: -len(suffix)].strip()
                changed = True
                break
    return query


def prep_query_str_namex(query: str, dash: str | None = None, replace_and = True, remove_designations = True) -> str:
    r"""Return the query string prepped for solr call.

    Rules:
        - no doubles: &,+
        - escape beginning: +,-,/,!
        - escape everywhere: ",:,[,],*,~,<,>,?,\
        - remove: (,),^,{,},|,\
        - lowercase: all
        - (default) replace &,+ with ' and '
        - (optional) replace - with '', ' ', or ' - '
        - (optional) replace ' - ' with '-'
        - (optional) remove designations
    """
    if not query:
        return ""

    if remove_designations and (designations := current_app.config.get("DESIGNATIONS")):
        query = strip_trailing_designations(query, designations)

    return prep_query_str(query, dash, replace_and)


# Outranks scattered single-letter OR coordination from the base query.
INITIALS_GROUP_BOOST_WEIGHT = "80"
# Outranks exact-first-word + synonym-only second-word (ltd ≈ holdings).
DISTINCTIVE_COVERAGE_BOOST_WEIGHT = "80"
# Matches QueryBuilder's fuzzy floor; excludes initials and stop-like tokens.
_DISTINCTIVE_MIN_TERM_LEN = 4
_COVERAGE_MIN_TERM_LEN = 3
MIN_COVERAGE_TERMS = 2


def is_coverage_prefix_term(token: str) -> bool:
    if not token:
        return False
    if len(token) >= _DISTINCTIVE_MIN_TERM_LEN:
        return True
    if len(token) < _COVERAGE_MIN_TERM_LEN:
        return False
    return token.lower() not in SYNONYM_SKIP_WORDS


def _designations_for_match_prep() -> list[str]:
    """Use the request app list when present; otherwise DEFAULT_DESIGNATIONS."""
    from flask import has_app_context

    if has_app_context() and (designations := current_app.config.get("DESIGNATIONS")):
        return designations
    from namex_solr_api.config import Config
    return list(Config.DEFAULT_DESIGNATIONS)


def conflict_match_prep_terms(query_value: str, designations: list[str] | None = None) -> list[str]:
    """Token list used by possible-conflict match prep (not raw ranking boosts)."""
    if designations is None:
        designations = _designations_for_match_prep()
    normalized = normalize_conflict_initials(query_value)
    prepared = strip_trailing_designations(prep_query_str(normalized, "replace"), designations)
    return remove_designation_tokens(prepared, designations).split()


def initials_group_runs(terms: list[str]) -> list[str]:
    runs: list[str] = []
    i = 0
    while i < len(terms):
        if len(terms[i]) == 1 and terms[i].isalpha():
            j = i + 1
            while j < len(terms) and len(terms[j]) == 1 and terms[j].isalpha():
                j += 1
            if j - i >= 2:  # noqa: PLR2004
                runs.append("".join(terms[i:j]))
            i = j
        else:
            i += 1
    return runs


def candidate_letter_tokens(name: str) -> set[str]:
    return {token.upper() for token in re.findall(r"[A-Za-z]+", name or "")}


def _append_unique(rewritten: list[str], seen: set[str], token: str) -> None:
    if token not in seen:
        rewritten.append(token)
        seen.add(token)


def _glued_run_at(terms: list[str], index: int) -> tuple[str | None, int]:
    if not (len(terms[index]) == 1 and terms[index].isalpha()):
        return None, 1
    end = index + 1
    while end < len(terms) and len(terms[end]) == 1 and terms[end].isalpha():
        end += 1
    if end - index >= 2:  # noqa: PLR2004
        return "".join(terms[index:end]).upper(), end - index
    return None, 1


def apply_initials_group_exact_highlights(
    exact_highlights: list[str],
    query_terms: list[str],
    candidate_name: str,
) -> list[str]:
    if not exact_highlights:
        return exact_highlights

    terms = [term.lower() for term in query_terms if term]
    runs = initials_group_runs(terms)
    if not runs:
        return exact_highlights

    name_tokens = candidate_letter_tokens(candidate_name)
    successful = {run.upper() for run in runs if run.upper() in name_tokens}
    if not successful:
        return exact_highlights

    suppress = {letter for glued in successful for letter in glued if letter.isalpha()}
    exact_set = {token.upper() for token in exact_highlights}

    rewritten: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(terms):
        glued, width = _glued_run_at(terms, i)
        if glued and glued in successful:
            _append_unique(rewritten, seen, glued)
            i += width
            continue
        upper = terms[i].upper()
        if upper in exact_set and upper not in seen and not (upper in suppress and len(upper) == 1):
            _append_unique(rewritten, seen, upper)
        i += 1

    for token in exact_highlights:
        upper = token.upper()
        if upper in suppress and len(upper) == 1:
            continue
        if upper not in seen:
            rewritten.append(upper)
            seen.add(upper)
    return rewritten


def build_initials_group_boosts(terms: list[str], boost: str | None = None) -> list[dict]:
    """All maximal 2+ single-letter runs AND all length>1 terms.

    Additional full-query boost; appended beside existing phrase boosts.
    """
    if boost is None:
        boost = INITIALS_GROUP_BOOST_WEIGHT
    from namex_solr_api.services.namex_solr.doc_models import NameField

    runs = initials_group_runs(terms)
    rest = [token for token in terms if len(token) > 1]
    if not runs or not rest:
        return []
    return [
        {
            "field": NameField.NAME_Q,
            "values": [*runs, *rest],
            "boost": boost,
        }
    ]


def _distinctive_term_clause(term: str) -> str:
    """Return the coverage clause for one term, using the base query's fuzzy widths."""
    from namex_solr_api.services.base_solr.utils.query_builder import QueryBuilder
    from namex_solr_api.services.namex_solr.doc_models import NameField

    parts = [
        f"{NameField.NAME_Q.value}:{term}",
        f"{NameField.NAME_Q_PHON_EN.value}:{term}",
    ]
    if fuzzy := QueryBuilder.get_fuzzy_str(term, 1, 2):
        parts.append(f"{NameField.NAME_Q.value}:{term}{fuzzy}")
    return f"({' OR '.join(parts)})"


def distinctive_coverage_terms(query_value: str) -> list[str]:
    return [token for token in (query_value or "").split() if is_coverage_prefix_term(token)]


def should_run_reserved_coverage(strict: bool, start: int, query_value: str) -> bool:
    return (
        not strict
        and start == 0
        and len(distinctive_coverage_terms(query_value)) >= MIN_COVERAGE_TERMS
    )


def reserved_coverage_params(params):
    from dataclasses import replace

    from namex_solr_api.services.namex_solr.doc_models import NameField

    terms = distinctive_coverage_terms(params.query.get("value", ""))
    if len(terms) < MIN_COVERAGE_TERMS:
        return None
    query_fields = {
        field: role
        for field, role in (params.query_fields or {}).items()
        if field != NameField.NAME_Q_PHON_EN
    }
    return replace(
        params,
        query={**params.query, "value": " ".join(terms)},
        start=0,
        full_query_boosts=[],
        query_fields=query_fields,
        expand_leftover_raw_synonyms=True,
    )


def merge_reserved_coverage(
    coverage_docs: list[dict],
    or_docs: list[dict],
    rows: int,
    id_field: str = "id",
) -> list[dict]:
    if rows <= 0:
        return []
    merged: list[dict] = []
    seen: set[str] = set()
    for doc in list(coverage_docs or []) + list(or_docs or []):
        key = doc.get(id_field)
        if key is None or key in seen:
            continue
        seen.add(key)
        merged.append(doc)
        if len(merged) >= rows:
            break
    return merged


def build_distinctive_coverage_boosts(
    terms: list[str], boost: str | None = None
) -> list[dict]:
    """Raise names that cover every distinctive term.

    Coverage is exact, phonetic or fuzzy per term; designation synonyms
    (ltd ≈ holdings) do not count.
    """
    distinctive = [term for term in terms if len(term) >= _DISTINCTIVE_MIN_TERM_LEN]
    if len(distinctive) < 2:  # noqa: PLR2004
        return []
    if boost is None:
        boost = DISTINCTIVE_COVERAGE_BOOST_WEIGHT
    return [
        {
            "term_clauses": [_distinctive_term_clause(term) for term in distinctive],
            "boost": boost,
        }
    ]


def normalize_nr_num(value: str | None) -> str | None:
    """Normalize an NR number to a canonical no-whitespace format."""
    if value is None:
        return None
    return "".join(value.split())


@dataclass(frozen=True)
class ConflictWildcard:
    """Leading/trailing * flags for examiner conflict search.

    value is the star-stripped query used for match-prep and ranking boosts.
    It is still the raw name (skip words stay) — only outer * is removed.
    """

    value: str
    leading: bool
    trailing: bool


_NAME_TOKEN = re.compile(r"[a-z0-9]+")


def parse_conflict_wildcard(query: str | None) -> ConflictWildcard:
    """Detect a leading and/or trailing * on the whole examiner query.

    Internal stars (WEST FOR* TIMBER) are left unchanged. A query that is
    only * is not treated as a positional operator.
    """
    if not query:
        return ConflictWildcard("", False, False)

    raw = query.strip()
    if not raw or not raw.replace("*", "").strip():
        return ConflictWildcard(raw, False, False)

    leading = raw.startswith("*")
    trailing = raw.endswith("*")
    cleaned = raw.strip("*").strip() if leading or trailing else raw
    if not cleaned:
        return ConflictWildcard(raw, False, False)
    return ConflictWildcard(cleaned, leading, trailing)


def apply_conflict_wildcard_boosts(boosts: list[dict], leading: bool) -> list[dict]:
    """Drop the name_q_exact prefix boost when the query has a leading *."""
    if not leading:
        return list(boosts)

    from namex_solr_api.services.namex_solr.doc_models import NameField

    return [item for item in boosts if item.get("field") != NameField.NAME_Q_EXACT]


def leading_wildcard_sort_key(name: str, query: str) -> int:
    """0 = query term is preceded by other words/characters; 1 = starts with term."""
    tokens = _NAME_TOKEN.findall((name or "").lower())
    terms = (query or "").lower().split()
    if not tokens or not terms:
        return 1

    term = terms[0]
    for index, token in enumerate(tokens):
        if token == term or token.startswith(term):
            return 0 if index > 0 else 1
        if term in token:
            return 0
    return 1


def apply_leading_wildcard_rank(docs: list[dict], query: str) -> list[dict]:
    """Stable-promote names where the first query term is not the first token.

    Does not drop documents. Only used for leading-only *TERM on start=0.
    """
    return sorted(docs, key=lambda doc: leading_wildcard_sort_key(doc.get("name") or "", query))
