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
"""Solr analysis helpers for synonym stem lookup."""
import re

STEMMED_AGRO_FIELD_TYPE = "text_stemmed_agro"


def _analysis_index_steps(analysis_response: dict) -> list:
    analysis = (analysis_response or {}).get("analysis") or {}
    field_types = analysis.get("field_types") or analysis.get("fieldTypes") or {}
    typed = field_types.get(STEMMED_AGRO_FIELD_TYPE) or {}
    if steps := typed.get("index"):
        return steps
    field_names = analysis.get("field_names") or analysis.get("fieldNames") or {}
    named = field_names.get(STEMMED_AGRO_FIELD_TYPE) or {}
    if steps := named.get("index"):
        return steps
    for payload in field_names.values():
        if isinstance(payload, dict) and payload.get("index"):
            return payload["index"]
    return []


def parse_stemmed_tokens(analysis_response: dict) -> list[str]:
    """Return final index tokens from a text_stemmed_agro analysis response."""
    index_steps = _analysis_index_steps(analysis_response)
    if not index_steps:
        return []
    last_step = index_steps[-1]
    if not isinstance(last_step, list):
        return []
    return [token["text"] for token in last_step if isinstance(token, dict) and token.get("text")]


def analyze_stemmed_agro_tokens(solr, query_value: str) -> list[str]:
    """Stem query tokens with the same analyzer as name_q_agro."""
    if not query_value or not str(query_value).strip():
        return []
    return parse_stemmed_tokens(solr.analyze_field(query_value.strip(), STEMMED_AGRO_FIELD_TYPE))


def synonym_members_in_name(name: str, members: list[str]) -> list[str]:
    """Return name tokens that belong to the resolved synonym group."""
    member_set = set()
    for member in members or []:
        for part in str(member).split():
            clean = re.sub(r"[^A-Za-z0-9]", "", part).upper()
            if clean:
                member_set.add(clean)
    found = []
    for token in (name or "").upper().split():
        clean = re.sub(r"[^A-Z0-9]", "", token)
        if clean and clean in member_set and clean not in found:
            found.append(clean)
    return found
