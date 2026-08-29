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

from flask import current_app

from namex_solr_api.services.base_solr.utils.formatting_helpers import prep_query_str

# Punct/space between two single letters (H&H, H.H., H. & H.). Does not insert "and".
_INITIAL_PUNCT = re.compile(
    r"(?i)(?<![a-z])([a-z])(?:[\s]*[&./,!_\-'@+=]+[\s]*)+([a-z])\.?(?![a-z])"
)
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


def normalize_nr_num(value: str | None) -> str | None:
    """Normalize an NR number to a canonical no-whitespace format."""
    if value is None:
        return None
    return "".join(value.split())
