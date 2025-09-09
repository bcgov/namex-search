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
# TODO: add search endpoints replicating namex queries ? Maybe don't need this
"""Exposes all of the search endpoints in Flask-Blueprint style."""
from http import HTTPStatus

from flask import Blueprint, jsonify, request
from flask.globals import request_ctx
from flask_cors import cross_origin

from namex_solr_api.exceptions import exception_response
from namex_solr_api.models import SearchHistory, User
from namex_solr_api.services import jwt, solr
from namex_solr_api.services.base_solr.utils import QueryParams
from namex_solr_api.services.namex_solr.doc_models import NameField, PCField
from namex_solr_api.services.namex_solr.utils import namex_search, prep_query_str_namex

bp = Blueprint("SEARCH", __name__, url_prefix="/search")


@bp.post("/possible-conflict-names")
@cross_origin(origins="*")
@jwt.requires_auth
def possible_conflict_names():
    """Return a list of possible conflict name results from solr."""
    try:
        # NOTE: request_ctx.current_user is set by jwt.requires_auth
        user = User.get_or_create_user_by_jwt(request_ctx.current_user)
        request_json = request.json
        # TODO: validate request
        # if errors:
        #     return bad_request_response("Errors processing request.", errors)  # noqa: ERA001

        # set base query params
        query_json: dict = request_json.get("query", {})
        value = query_json.get("value")
        query = {
            "value": prep_query_str_namex(value, "replace"),
            PCField.CORP_NUM_Q.value: prep_query_str_namex(query_json.get(PCField.CORP_NUM.value, "")),
            PCField.NR_NUM_Q.value: prep_query_str_namex(query_json.get(PCField.NR_NUM.value, ""))
        }
        # set faceted category params
        categories_json: dict = request_json.get("categories", {})
        # TODO: verify these states
        conflict_states = ["ACTIVE", "APPROVED", "CONDITION"]
        categories = {
            PCField.JURISDICTION: categories_json.get(PCField.JURISDICTION.value, None),
            PCField.STATE: categories_json.get(PCField.STATE.value, conflict_states)
        }
        # set nested child query params
        child_query = {
            NameField.NAME_Q_SINGLE.value: prep_query_str_namex(query_json.get(NameField.NAME.value, ""))
        }
        # set nested child faceted category params
        # TODO: verify these states
        conflict_name_states = ["A", "C", "CORP"]
        child_categories = {
            NameField.NAME_STATE: categories_json.get(NameField.NAME_STATE.value, conflict_name_states)
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
            fields=solr.resp_fields_nested,
            query_boost_fields={
                NameField.NAME_Q_AGRO: 2,
                NameField.NAME_Q_SINGLE: 2,
                NameField.NAME_Q_XTRA: 2,
                NameField.NAME_Q_SYN: 2
            },
            query_fields={
                NameField.NAME_Q: "child",
                NameField.NAME_Q_AGRO: "child",
                NameField.NAME_Q_SINGLE: "child",
                NameField.NAME_Q_XTRA: "child",
            },
            query_fuzzy_fields={
                NameField.NAME_Q: {"short": 1, "long": 2},
                NameField.NAME_Q_AGRO: {"short": 1, "long": 2},
                NameField.NAME_Q_SINGLE: {"short": 1, "long": 2}
            },
            query_synonym_fields={
                NameField.NAME_Q_SYN: "child"
            },
            full_query_boosts=solr.get_name_search_full_query_boost(value)
        )

        results = namex_search(params, solr, True)
        docs = results.get("response", {}).get("docs")

        # save search in the db
        SearchHistory(
            query=request_json,
            results=docs,
            submitter_id=user.id,
        ).save()

        response = {
            "searchResults": {
                "queryInfo": {
                    "categories": {
                        **categories,
                        **child_categories
                    },
                    "query": {
                        "value": query["value"],
                        PCField.CORP_NUM.value: query[PCField.CORP_NUM_Q.value],
                        PCField.NR_NUM.value: query[PCField.NR_NUM_Q.value],
                        NameField.NAME.value: child_query[NameField.NAME_Q_SINGLE.value]
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


@bp.post("/nrs")
@cross_origin(origins="*")
@jwt.requires_auth
def nrs():
    """Return a list of Name Request results from solr."""
    try:
        request_json = request.json
        # TODO: validate request
        # if errors:
        #     return bad_request_response("Errors processing request.", errors)  # noqa: ERA001

        # set base query params
        query_json: dict = request_json.get("query", {})
        value = query_json.get("value")
        query = {
            "value": prep_query_str_namex(value),
            PCField.CORP_NUM_Q.value: prep_query_str_namex(query_json.get(PCField.CORP_NUM.value, "")),
            PCField.NR_NUM_Q.value: prep_query_str_namex(query_json.get(PCField.NR_NUM.value, ""))
        }
        # set faceted category params
        categories_json: dict = request_json.get("categories", {})
        categories = {
            PCField.JURISDICTION: categories_json.get(PCField.JURISDICTION.value, None),
            PCField.STATE: categories_json.get(PCField.STATE.value, None),
            PCField.TYPE: ["NR"]
        }
        # set nested child query params
        child_query = {
            NameField.NAME_Q_SINGLE.value: prep_query_str_namex(query_json.get(NameField.NAME.value, ""))
        }
        # set nested child faceted category params
        child_categories = {
            NameField.NAME_STATE: categories_json.get(NameField.NAME_STATE.value, None)
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
                NameField.NAME_Q: 2,
                NameField.NAME_Q_AGRO: 2,
                NameField.NAME_Q_SINGLE: 2,
                NameField.NAME_Q_XTRA: 2
            },
            query_fields={
                PCField.NR_NUM_Q: "parent",
                PCField.NR_NUM_Q_EDGE: "parent",
                NameField.NAME_Q: "child",
                NameField.NAME_Q_AGRO: "child",
                NameField.NAME_Q_SINGLE: "child",
                NameField.NAME_Q_XTRA: "child",
            },
            query_fuzzy_fields={
                NameField.NAME_Q: {"short": 1, "long": 2},
                NameField.NAME_Q_AGRO: {"short": 1, "long": 2},
                NameField.NAME_Q_SINGLE: {"short": 1, "long": 2}
            },
            query_synonym_fields={
                NameField.NAME_Q_SYN: "child"
            },
            # NOTE: add items to this to improve ordering as needed
            full_query_boosts=[]
        )

        results = namex_search(params, solr, False)
        docs = results.get("response", {}).get("docs")

        response = {
            "searchResults": {
                "queryInfo": {
                    "categories": {
                        **categories,
                        **child_categories
                    },
                    "query": {
                        "value": query["value"],
                        PCField.CORP_NUM.value: query[PCField.CORP_NUM_Q.value],
                        PCField.NR_NUM.value: query[PCField.NR_NUM_Q.value],
                        NameField.NAME.value: child_query[NameField.NAME_Q_SINGLE.value]
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