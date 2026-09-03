# Copyright © 2025 Province of British Columbia
#
# Licensed under the BSD 3 Clause License, (the "License");
# you may not use this file except in compliance with the License.
# The template for the license can be found here
#    https://opensource.org/license/bsd-3-clause/
#
# Redistribution and use in source and binary forms,
# with or without modification, are permitted provided that the
# following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS “AS IS”
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
"""Solr formatting functions."""
import re
from dataclasses import dataclass

from flask import current_app

from namex_solr_api.services.base_solr.utils.formatting_helpers import prep_query_str

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
    return re.sub(r"\s+", " ", normalized).strip()


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


def build_initials_group_boosts(terms: list[str], boost: str | None = None) -> list[dict]:
    """All maximal 2+ single-letter runs AND all length>1 terms.

    Additional full-query boost; appended beside existing phrase boosts.
    """
    if boost is None:
        boost = INITIALS_GROUP_BOOST_WEIGHT
    from namex_solr_api.services.namex_solr.doc_models import NameField

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
