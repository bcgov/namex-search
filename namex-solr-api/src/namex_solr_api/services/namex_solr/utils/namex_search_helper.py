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
"""NameX solr search functions."""
import re

from namex_solr_api.services.base_solr.utils import QueryParams
from namex_solr_api.services.namex_solr import NamexSolr
from namex_solr_api.services.namex_solr.doc_models import NameField, PCField

from .add_category_filters import add_category_filters


def namex_search(params: QueryParams, solr: NamexSolr, is_name_search: bool):
    """Return the list of possible conflicts from Solr that match the query."""
    # initialize payload with base doc query (init query / filter)
    initial_queries = solr.query_builder.build_base_query(
        query=params.query,
        fields=params.query_fields,
        boost_fields=params.query_boost_fields,
        fuzzy_fields=params.query_fuzzy_fields,
        synonym_fields=params.query_synonym_fields,
        is_child_search=is_name_search)

    # boosts for term order result ordering
    for info in params.full_query_boosts:
        initial_queries["query"] += f' OR ({info["field"].value}:"{info["value"]}"'
        if fuzzy := info.get("fuzzy"):
            initial_queries["query"] += f'~{fuzzy}^{info["boost"]})'
        else:
            initial_queries["query"] += f'^{info["boost"]})'

    # add defaults
    parent_field = NameField.PARENT_TYPE.value if is_name_search else PCField.TYPE.value
    solr_payload = {
        **initial_queries,
        "queries": {
            "parents": f"{parent_field}:*",
            "parentFilters": " AND ".join(initial_queries["filter"]),
        },
        "fields": params.fields
    }
    if params.highlightedFields:
        solr_payload = {
            **solr_payload,
            **namex_search_highlighting(params)
        }
    # base doc faceted filters
    add_category_filters(solr_payload=solr_payload,
                         categories=params.categories,
                         is_child=False,
                         is_child_search=is_name_search,
                         solr=solr)
    # child filter queries
    if child_query := solr.query_builder.build_child_query(params.child_query, is_name_search):
        solr_payload["filter"].append(child_query)
    # child doc faceted filter queries
    add_category_filters(solr_payload=solr_payload,
                         categories=params.child_categories,
                         is_child=True,
                         is_child_search=is_name_search,
                         solr=solr)

    resp: dict[str, dict[str, dict[str, list[str]]]] = solr.query(solr_payload, params.start, params.rows)
    if solr_highlighting := resp.get('highlighting'):
        parsed_highlighting = {}
        for result_id, result in solr_highlighting.items():
            parsed_highlighting[result_id] = {}
            for field_enum in params.highlightedFields:
                if field_highlights := result.get(field_enum.value):
                    parsed_highlighting[result_id][field_enum.value] = []
                    for highlight in field_highlights:
                        parsed_highlighting[result_id][field_enum.value] += namex_search_parse_highlighting(highlight)
        resp['highlighting'] = parsed_highlighting
    return resp


def namex_search_highlighting(params: QueryParams):
    """Return the the highlighting params for the query."""
    return {
        "params": {
            "hl": "on",
            "hl.method": "unified",
            "hl.requireFieldMatch": "true",
            "hl.tag.pre": "|||",
            "hl.tag.post": "|||",
            "hl.fl": ",".join([x.value for x in params.highlightedFields])
        }
    }

def namex_search_parse_highlighting(highlighted_value: str) -> list[str]:
    """Return the parsed list of highlighted terms."""
    highlighted_rgx = r'\|\|\|([^\|]*)\|\|\|'
    return re.findall(highlighted_rgx, highlighted_value)
