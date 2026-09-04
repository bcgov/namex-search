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

    def build_child_query(self, child_query: dict[str, str], is_child_search: bool) -> str | None:
        """Return the child query fq."""
        # add filter clauses for child query items
        child_q = ""
        for key, value in child_query.items():
            if not value:
                continue

            terms = value.split()
            if not child_q:
                child_q = self.create_clause(key, terms[0], True, is_child_search)
            else:
                child_q += f" AND {self.create_clause(key, terms[0], True, is_child_search)}"

            for term in terms[1:]:
                child_q += f" AND {self.create_clause(key, term, True, is_child_search)}"

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
        is_child_search: bool,
        boost_fields: dict[BaseEnum, int],
        stemmed_terms: list[str] | None = None,
    ):
        """Return the term clause with the added synonym clauses."""
        term = terms[term_index]
        for field, level in synonym_fields.items():
            if not synonym_info.get(field):
                synonym_info[field] = {
                    "synonym_terms": [],
                    "synonym_start_index": None,
                    "synonym_members": [],
                }
            synonym_terms = synonym_info[field]["synonym_terms"]
            synonym_start_index = synonym_info[field]["synonym_start_index"]

            field_value = field.value
            if level == "child" and not is_child_search:
                field_value = self.pre_child_filter_clause + field.value
            elif level != "child" and is_child_search:
                field_value = self.pre_parent_filter_clause + field.value

            synonym_clause = ""
            if synonym_terms and term_index < synonym_start_index + len(synonym_terms):
                synonym_clause = self._synonym_field_clause(
                    field_value, synonym_terms, synonym_info[field].get("synonym_members") or []
                )
            else:
                new_synonym_terms, new_members = self.find_synonym_terms(
                    term, term_index, terms, field, stemmed_terms
                )
                if new_synonym_terms:
                    synonym_info[field]["synonym_terms"] = new_synonym_terms
                    synonym_info[field]["synonym_start_index"] = term_index
                    synonym_info[field]["synonym_members"] = new_members
                    synonym_clause = self._synonym_field_clause(
                        field_value, new_synonym_terms, new_members
                    )

            if synonym_clause:
                if field in boost_fields:
                    synonym_clause += f"^{boost_fields[field]}"
                term_clause = self.join_clause(term_clause, f"({synonym_clause})", "OR")

        return term_clause

    def build_base_query(self,  # noqa: PLR0913
                         query: dict[str, str],
                         fields: dict[BaseEnum, str],
                         boost_fields: dict[BaseEnum, int],
                         fuzzy_fields: dict[BaseEnum, dict[str, int]],
                         synonym_fields: dict[BaseEnum, str],
                         is_child_search: bool,
                         clause_bridge="AND",
                         stemmed_terms: list[str] | None = None) -> dict:
        """Return a solr query with filters for each subsequent term."""
        terms = query["value"].split()
        if not stemmed_terms or len(stemmed_terms) != len(terms):
            stemmed_terms = terms
        synonym_info = {}
        query_clause = ""
        for term_index, term in enumerate(terms):
            term_clause = self.build_term_clause(term, fields, boost_fields, fuzzy_fields, is_child_search)
            term_clause = self.build_term_synonym_clauses(
                term_clause,
                terms,
                term_index,
                synonym_info,
                synonym_fields,
                is_child_search,
                boost_fields,
                stemmed_terms,
            )
            query_clause = self.join_clause(query_clause, f"({term_clause})", clause_bridge)

        filters = self.build_filter_clause(query, is_child_search)

        if not query_clause:
            query_clause = '""'

        return {
            "query": query_clause,
            "filter": filters,
            "synonym_members": self._collect_synonym_members(synonym_info),
        }

    def find_synonym_terms(
        self,
        start_term: str,
        start_term_index: int,
        terms: list[str],
        field: BaseEnum,
        stemmed_terms: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Return matching synonym key tokens and the group's member list."""
        from namex_solr_api.models import SolrSynonymList

        synonym_type = self.synonym_field_map[field]
        stemmed_terms = stemmed_terms or terms
        start_stem = (
            stemmed_terms[start_term_index]
            if start_term_index < len(stemmed_terms)
            else start_term
        )
        candidates = []
        seen_keys = set()
        for phrase in (start_term, start_stem):
            if not phrase:
                continue
            for row in SolrSynonymList.find_all_beginning_with_phrase(phrase, synonym_type):
                if row.synonym in seen_keys:
                    continue
                seen_keys.add(row.synonym)
                candidates.append(row)

        best_key_terms: list[str] = []
        best_members: list[str] = []
        for row in candidates:
            key_terms = row.synonym.split()
            if not key_terms or len(key_terms) > len(terms[start_term_index:]):
                continue
            if best_key_terms and len(key_terms) < len(best_key_terms):
                continue
            if self._query_covers_synonym_key(key_terms, start_term_index, terms, stemmed_terms):
                best_key_terms = key_terms
                best_members = [row.synonym, *(row.synonym_list or [])]
        return best_key_terms, best_members

    @staticmethod
    def _query_covers_synonym_key(
        key_terms: list[str],
        start_index: int,
        terms: list[str],
        stemmed_terms: list[str],
    ) -> bool:
        """True if each key token equals the query token or its agro stem."""
        for i, key_term in enumerate(key_terms):
            query_term = terms[start_index + i]
            query_stem = (
                stemmed_terms[start_index + i]
                if start_index + i < len(stemmed_terms)
                else query_term
            )
            if query_term.lower() != key_term.lower() and query_stem.lower() != key_term.lower():
                return False
        return True

    @staticmethod
    def _synonym_field_clause(field_value: str, key_terms: list[str], members: list[str]) -> str:
        """Emit the synonym key, plus one graph member so stems-only keys still hit."""
        if len(key_terms) != 1:
            return f"{field_value}:{' '.join(key_terms)}"
        key = key_terms[0]
        for member in members:
            tokens = str(member).split()
            if len(tokens) == 1 and tokens[0].lower() != key.lower():
                return f"{field_value}:({key} OR {tokens[0]})"
        return f"{field_value}:{key}"

    @staticmethod
    def _collect_synonym_members(synonym_info: dict) -> list[str]:
        members = []
        seen = set()
        for field_info in synonym_info.values():
            for member in field_info.get("synonym_members") or []:
                if member and member not in seen:
                    seen.add(member)
                    members.append(member)
        return members

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
