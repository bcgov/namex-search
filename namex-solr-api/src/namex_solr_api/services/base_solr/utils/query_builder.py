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
"""Manages common solr query building methods."""
import re

from namex_solr_api.common.base_enum import BaseEnum


class QueryBuilder:
    """Manages shared query building code."""

    identifier_field_values = None
    pre_child_filter_clause = None

    def __init__(self, identifier_field_values: list[str], unique_parent_field: BaseEnum):
        """Initialize the solr class."""
        self.identifier_field_values = identifier_field_values
        self.pre_child_filter_clause = "{!parent which = '-_nest_path_:* " + unique_parent_field.value + ":*'}"

    def create_clause(self, field_value: str, term: str, is_child=False) -> str:
        """Return the query clause for the field and term."""
        corp_prefix_regex = r"(^[aA-zZ]+)[0-9]+$"

        search_field = field_value
        if is_child:
            search_field = self.pre_child_filter_clause + search_field

        if field_value in self.identifier_field_values and (identifier := re.search(corp_prefix_regex, term)):
            prefix = identifier.group(1)
            no_prefix_term = term.replace(prefix, "", 1)

            return f'({search_field}:"{no_prefix_term}" AND {search_field}:"{prefix.upper()}")'

        return f"{search_field}:{term}"

    def build_filter_clause(self, query: dict[str, str]) -> list[str]:
        """Return the filters for the query."""
        filters = []
        for key, value in query.items():
            if key in ["value"] or not value:
                continue
            terms = value.split()
            for term in terms:
                filters.append(self.create_clause(key, term))
        return filters

    def build_child_query(self, child_query: dict[str, str]) -> str | None:
        """Return the child query fq."""
        # add filter clauses for child query items
        child_q = ""
        for key, value in child_query.items():
            if not value:
                continue

            terms = value.split()
            if not child_q:
                child_q = self.create_clause(key, terms[0], True)
            else:
                child_q += f" AND {self.create_clause(key, terms[0], True)}"

            for term in terms[1:]:
                child_q += f" AND {self.create_clause(key, term, True)}"

        if not child_q:
            return None

        return f"({child_q})"

    def build_facet_query(self,
                          field: BaseEnum,
                          values: list[str], is_nested: bool = False) -> str:
        """Return the facet filter clause for the given params."""
        filter_q = f'{field.value}:("{values[0]}"'
        if is_nested:
            filter_q = self.pre_child_filter_clause + f'{field.value}:"{values[0]}"'
        for val in values[1:]:
            if is_nested:
                filter_q += f' OR {field.value}: "{val}"'
            else:
                filter_q += f' OR "{val}"'
        if not is_nested:
            filter_q += ")"
        return filter_q

    def build_base_query(self,
                         query: dict[str, str],  # pylint: disable=too-many-arguments,too-many-branches
                         fields: dict[BaseEnum, str],
                         boost_fields: dict[BaseEnum, int],
                         fuzzy_fields: dict[BaseEnum, dict[str, int]]) -> dict[str, list[str]]:
        """Return a solr query with filters for each subsequent term."""
        terms = query["value"].split()

        query_clause = ""
        for term in terms:
            # each term only needs to match one of the given fields, but all terms must match at least 1
            term_clause = ""
            for field, level in fields.items():
                field_clause = self.create_clause(field.value, term, level == "child")
                pre_boost_clause = field_clause
                # add boost
                if field in boost_fields:
                    field_clause += f"^{boost_fields[field]}"

                term_clause = self.join_clause(term_clause, field_clause, "OR")
                # add fuzzy matching
                if field in fuzzy_fields \
                    and (fuzzy_str := self.get_fuzzy_str(term,
                                                         fuzzy_fields[field]["short"],
                                                         fuzzy_fields[field]["long"])):
                    # add another with fuzzy (this one will give a lower score on a hit if the original has a boost)
                    term_clause = self.join_clause(term_clause, f"{pre_boost_clause}{fuzzy_str}", "OR")

            query_clause = self.join_clause(query_clause, f"({term_clause})", "AND")

        # Add extra filters if applicable
        filters = self.build_filter_clause(query)

        if not query_clause:
            # handle empty string provided for query value
            query_clause = '""'

        return {"query": query_clause, "filter": filters}

    @staticmethod
    def build_facet(field: BaseEnum, is_nested: bool) -> dict[str, dict]:
        """Return the facet dict for the field."""
        facet = {field.value: {"type": "terms", "field": field.value}}
        if is_nested:
            facet[field.value]["domain"] = {"blockChildren": "{!v=$parents}"}
            facet[field.value]["facet"] = {"by_parent": "uniqueBlock({!v=$parents})"}

        return facet

    @staticmethod
    def get_fuzzy_str(term: str, short: int, long: int) -> str:
        """Return the fuzzy string for the term."""
        if len(term) < 4:  # noqa: PLR2004
            return ""
        if len(term) < 7:  # noqa: PLR2004
            return f"~{short}"
        return f"~{long}"

    @staticmethod
    def join_clause(current_clause: str, new_clause: str, join_str: str):
        """Return the current clause added with the new clause."""
        if current_clause:
            current_clause += f" {join_str} "
        return current_clause + new_clause
