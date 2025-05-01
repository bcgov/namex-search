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
# TODO: add search endpoint replicating logic from bor
# TODO: add search endpoints replicating namex queries
"""Exposes all of the search endpoints in Flask-Blueprint style."""
from http import HTTPStatus

from flask import Blueprint, g, jsonify, request
from flask_cors import cross_origin

from namex_solr_api.exceptions import bad_request_response, exception_response
from namex_solr_api.models import SearchHistory, User
from namex_solr_api.services import jwt, solr
from namex_solr_api.services.base_solr.utils import QueryParams, parse_facets, prep_query_str
from namex_solr_api.services.namex_solr.doc_models import NameField, PCField
from namex_solr_api.services.namex_solr.utils import namex_search

bp = Blueprint("SEARCH", __name__, url_prefix="/search")


@bp.post("/possible-conflicts")
@cross_origin(origins="*")
# TODO: auth add back in
# @jwt.requires_auth
def possible_conflicts():
    """Return a list of possible conflict results from solr."""
    try:
        request_json = request.json
        # TODO: validate request
        # if errors:
        #     return bad_request_response("Errors processing request.", errors)

        # set base query params
        query_json: dict = request_json.get("query", {})
        value = query_json.get("value")
        query = {
            "value": prep_query_str(value),
            # TODO: add filter fields here
            # EntityField.BN_Q.value: prep_query_str_adv(query_json.get(EntityField.BN.value, "")),
            # EntityField.IDENTIFIER_Q.value: prep_query_str_adv(query_json.get(EntityField.IDENTIFIER.value, "")),
            # EntityField.LEGAL_NAME_SINGLE_Q.value: prep_query_str_adv(query_json.get(EntityField.LEGAL_NAME.value, "")),
        }
        # set faceted category params
        categories_json: dict = request_json.get("categories", {})
        categories = {
            # TODO: add category filter fields here
            # EntityField.ENTITY_TYPE: categories_json.get(EntityField.ENTITY_TYPE.value, None),
            # EntityField.LEGAL_TYPE: categories_json.get(EntityField.LEGAL_TYPE.value, None),
            # EntityField.STATE: categories_json.get(EntityField.STATE.value, None),
        }
        # set nested child query params
        child_query = {
            # TODO: add child filter fields here
            # AddressField.ADDRESS_Q.value: prep_query_str_adv(query_json.get(EntityField.ENTITY_ADDRESSES.value, "")),
            # EntityRoleField.RELATED_BN_Q.value: prep_query_str_adv(
            #     roles_json.get(EntityRoleField.RELATED_BN.value, "")
            # ),
            # EntityRoleField.RELATED_EMAIL_Q.value: prep_query_str_adv(
            #     roles_json.get(EntityRoleField.RELATED_EMAIL.value, "")
            # ),
            # EntityRoleField.RELATED_IDENTIFIER_Q.value: prep_query_str_adv(
            #     roles_json.get(EntityRoleField.RELATED_IDENTIFIER.value, "")
            # ),
            # EntityRoleField.RELATED_NAME_SINGLE_Q.value: prep_query_str_adv(
            #     roles_json.get(EntityRoleField.RELATED_NAME.value, "")
            # ),
            # EntityRoleField.RELATED_Q.value: prep_query_str_adv(roles_json.get("value", "")),
        }
        # set nested child faceted category params
        child_categories = {
            # TODO: add child category filter fields here
        }

        start = request_json.get("start", solr.default_start)
        rows = request_json.get("rows", solr.default_rows)

        params = QueryParams(
            query=query,
            rows=rows,
            start=start,
            categories=categories,
            child_query=child_query,
            child_categories=child_categories,
            fields=solr.resp_fields,
            query_boost_fields={
                # TODO: update for namex
                NameField.NAME_Q: 2,
                # EntityField.LEGAL_NAME_AGRO_Q: 2,
                # EntityField.LEGAL_NAME_SINGLE_Q: 2,
                # EntityField.LEGAL_NAME_XTRA_Q: 2,
            },
            query_fields={
                # TODO: update for namex
                NameField.NAME_Q: "child",
                # EntityField.LEGAL_NAME_AGRO_Q: "parent",
                # EntityField.LEGAL_NAME_SINGLE_Q: "parent",
                # EntityField.LEGAL_NAME_XTRA_Q: "parent",
                # EntityRoleField.RELATED_EMAIL_Q: "child",
                # AddressField.ADDRESS_Q: "child",
            },
            query_fuzzy_fields={
                # TODO: update for namex
                NameField.NAME_Q: {"short": 1, "long": 2},
                # EntityField.LEGAL_NAME_AGRO_Q: {"short": 1, "long": 2},
                # EntityField.LEGAL_NAME_SINGLE_Q: {"short": 1, "long": 2},
                # AddressField.ADDRESS_Q: {"short": 1, "long": 1},
                # EntityRoleField.RELATED_EMAIL_Q: {"short": 1, "long": 1},
            },
            # TODO: update for namex
            # query_synonym_fields={
                # EntityField.LEGAL_NAME_SYN_Q: "parent",
                # AddressField.ADDRESS_SYN_Q: "child"
            # },
        )

        results = namex_search(params, solr)
        docs = results.get("response", {}).get("docs")

        # save search in the db
        # TODO: add back in
        # SearchHistory(
        #     query=request_json,
        #     results=docs,
        #     submitter_id=user.id,
        #     submitter_account_id=request.headers.get("Account-Id", None),
        # ).save()

        response = {
            "facets": parse_facets(results),
            "searchResults": {
                "queryInfo": {
                    "categories": {
                        # TODO: update for namex
                        **categories,
                        # EntityField.ENTITY_ADDRESSES.value: address_categories,
                        # EntityField.ROLES.value: role_categories,
                    },
                    "query": {
                        "value": query["value"],
                        # TODO: update for namex
                        # EntityField.BN.value: query[EntityField.BN_Q.value],
                        # EntityField.IDENTIFIER.value: query[EntityField.IDENTIFIER_Q.value],
                        # EntityField.LEGAL_NAME.value: query[EntityField.LEGAL_NAME_SINGLE_Q.value],
                        # EntityField.ENTITY_ADDRESSES.value: child_query[AddressField.ADDRESS_Q.value],
                        # EntityField.ROLES.value: {
                        #     EntityRoleField.RELATED_BN.value: child_query[EntityRoleField.RELATED_BN_Q.value],
                        #     EntityRoleField.RELATED_EMAIL.value: child_query[EntityRoleField.RELATED_EMAIL_Q.value],
                        #     EntityRoleField.RELATED_IDENTIFIER.value: child_query[
                        #         EntityRoleField.RELATED_IDENTIFIER_Q.value
                        #     ],
                        #     EntityRoleField.RELATED_NAME.value: child_query[
                        #         EntityRoleField.RELATED_NAME_SINGLE_Q.value
                        #     ],
                        #     EntityRoleField.ROLE_DATES.value: child_date_ranges,
                        #     "value": child_query[EntityRoleField.RELATED_Q.value],
                        # },
                    },
                    "rows": rows or solr.default_rows,
                    "start": start or solr.default_start,
                },
                "totalResults": results.get("response", {}).get("numFound"),
                "results": docs,
            },
        }

        return jsonify(response), HTTPStatus.OK

    except Exception as exception:
        return exception_response(exception)