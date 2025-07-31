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
    pre_parent_filter_clause = None
    synonym_field_map = None

    def __init__(self, identifier_field_values: list[str], unique_parent_field: BaseEnum, synonym_field_map: dict[BaseEnum, BaseEnum]):
        """Initialize the solr class."""
        self.identifier_field_values = identifier_field_values
        self.pre_child_filter_clause = "{!parent which=\"" + unique_parent_field.value + ":*\"}"
        self.pre_parent_filter_clause = "{!child of=\"" + unique_parent_field.value + ":*\"}"
        self.synonym_field_map = synonym_field_map

    def create_clause(self, field_value: str, term: str, is_child: bool, is_child_search: bool) -> str:
        """Return the query clause for the field and term."""
        corp_prefix_regex = r"(^[aA-zZ]+)[0-9]+$"

        search_field = field_value
        if is_child and not is_child_search:
            search_field = self.pre_child_filter_clause + search_field
        elif not is_child and is_child_search:
            search_field = self.pre_parent_filter_clause + search_field

        if field_value in self.identifier_field_values and (identifier := re.search(corp_prefix_regex, term)):
            prefix = identifier.group(1)
            no_prefix_term = term.replace(prefix, "", 1)

            return f'({search_field}:"{no_prefix_term}" AND {search_field}:"{prefix.upper()}")'

        return f"{search_field}:{term}"

    def build_filter_clause(self, query: dict[str, str], is_child_search: bool) -> list[str]:
        """Return the filters for the query."""
        filters = []
        for key, value in query.items():
            if key in ["value"] or not value:
                continue
            terms = value.split()
            for term in terms:
                # NOTE: is_child is always false for now in supported filters
                filters.append(self.create_clause(key, term, False, is_child_search))
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
                child_q = self.create_clause(key, terms[0], True, True)
            else:
                child_q += f" AND {self.create_clause(key, terms[0], True, True)}"

            for term in terms[1:]:
                child_q += f" AND {self.create_clause(key, term, True, True)}"

        if not child_q:
            return None

        return f"({child_q})"

    def build_facet_query(self,
                          field: BaseEnum,
                          values: list[str],
                          is_child: bool,
                          is_child_search: bool) -> str:
        """Return the facet filter clause for the given params."""
        filter_q = ''
        if is_child and not is_child_search:
            filter_q = self.pre_child_filter_clause
        elif not is_child and is_child_search:
            filter_q = self.pre_parent_filter_clause
        filter_q += f'{field.value}:("{values[0]}"'
        for val in values[1:]:
            filter_q += f' OR "{val}"'
        filter_q += ")"
        return filter_q
    
    def build_term_clause(
        self,
        term: str,
        fields: dict[BaseEnum, str],
        boost_fields: dict[BaseEnum, int],
        fuzzy_fields: dict[BaseEnum, dict[str, int]],
        is_child_search: bool
    ) -> str:
        """Return the base term clause."""
        term_clause = ""
        for field, level in fields.items():
            field_clause = self.create_clause(field.value, term, level == "child", is_child_search)
            pre_boost_clause = field_clause
            # add boost
            if field in boost_fields:
                field_clause += f"^{boost_fields[field]}"

            term_clause = self.join_clause(term_clause, field_clause, "OR")
            # add fuzzy matching
            if field in fuzzy_fields and (fuzzy_str := self.get_fuzzy_str(term,
                                          fuzzy_fields[field]["short"],
                                          fuzzy_fields[field]["long"])):
                # add another with fuzzy (this one will give a lower score on a hit if the original has a boost)
                term_clause = self.join_clause(term_clause, f"{pre_boost_clause}{fuzzy_str}", "OR")
        return term_clause

    def build_term_synonym_clauses(  # noqa: PLR0913
        self,
        term_clause: str,
        terms: list[str],
        term_index: int,
        synonym_info: dict,
        synonym_fields: dict[BaseEnum, str],
        is_child_search: bool
    ):
        """Return the term clause with the added synonym clauses."""
        term = terms[term_index]
        for field, level in synonym_fields.items():
            if not synonym_info.get(field):
                synonym_info[field] = {"synonym_terms": [], "synonym_start_index": None}
            synonym_terms = synonym_info[field]["synonym_terms"]
            synonym_start_index = synonym_info[field]["synonym_start_index"]

            field_value = field.value
            if level == "child" and not is_child_search:
                field_value = self.pre_child_filter_clause + field.value
            elif level != "child" and is_child_search:
                field_value = self.pre_parent_filter_clause + field.value

            synonym_clause = ""
            if synonym_terms and term_index < synonym_start_index + len(synonym_terms):
                # a synonym matched on a previous term and includes the current term (multi word synonym)
                synonym_clause = f"{field_value}:{' '.join(synonym_terms)}"
            elif new_synonym_terms := self.find_synonym_terms(term, term_index, terms, field):
                synonym_info[field]["synonym_terms"] = new_synonym_terms
                synonym_info[field]["synonym_start_index"] = term_index
                synonym_clause = f"{field_value}:{' '.join(new_synonym_terms)}"

            if synonym_clause:
                term_clause = self.join_clause(term_clause, f"({synonym_clause})", "OR")

        return term_clause

    def build_base_query(self,  # noqa: PLR0913
                         query: dict[str, str],
                         fields: dict[BaseEnum, str],
                         boost_fields: dict[BaseEnum, int],
                         fuzzy_fields: dict[BaseEnum, dict[str, int]],
                         synonym_fields: dict[BaseEnum, str],
                         is_child_search: bool) -> dict[str, list[str]]:
        """Return a solr query with filters for each subsequent term."""
        terms = query["value"].split()
        synonym_info = {}
        query_clause = ""
        # Each term in the searched 'value' must match on at least one of:
        # 'fields', 'fuzzy_fields' or 'synonym_fields' query clauses.
        # This loop adds clauses for the all the given fields for each term
        for term_index, term in enumerate(terms):
            # Get the base clause, which references the fields, fuzzy fields and adds the boost clause for ordering
            term_clause = self.build_term_clause(term, fields, boost_fields, fuzzy_fields, is_child_search)

            # Add the synonym field clauses
            term_clause = self.build_term_synonym_clauses(term_clause, terms, term_index, synonym_info, synonym_fields, is_child_search)

            # Join the term clause to the full query
            query_clause = self.join_clause(query_clause, f"({term_clause})", "AND")

        # Add extra filters if applicable
        filters = self.build_filter_clause(query, is_child_search)

        if not query_clause:
            # handle empty string provided for query value
            query_clause = '""'

        return {"query": query_clause, "filter": filters}
    
    def find_synonym_terms(self, start_term: str, start_term_index: int, terms: list[str], field: BaseEnum) -> list[str]:
        """Return the synonym terms that match the starting term and following query terms."""
        # NOTE: when this is in a common space the model will be a common dependency similar to whats been done in lear
        from namex_solr_api.models import SolrSynonymList

        # the best match will be the one with the most words (i.e. british columbia > british)
        best_synonym_match_terms = []
        # check if term exists inside a synonym
        if synonyms := SolrSynonymList.find_all_beginning_with_phrase(start_term, self.synonym_field_map[field]):
            for synonym_terms in [syn.synonym.split() for syn in synonyms]:
                if len(synonym_terms) > len(terms[start_term_index:]) or len(synonym_terms) == 0:
                    # not possible to be this synonym
                    continue
                if len(synonym_terms) < len(best_synonym_match_terms):
                    # this is a shorter synonym than one thats already matched so skip
                    continue

                # see if all terms of the synonym are in the query
                full_synonym_in_query = True
                for i, synonym_term in enumerate(synonym_terms):
                    if terms[start_term_index + i].lower() != synonym_term.lower():
                        full_synonym_in_query = False
                        break
                if full_synonym_in_query:
                    best_synonym_match_terms = synonym_terms

        return best_synonym_match_terms

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
