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
"""API endpoint for resyncing records in solr."""
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, request
from flask_cors import cross_origin

from namex_solr_api.exceptions import bad_request_response, exception_response
from namex_solr_api.models import SolrDoc, SolrDocEvent, User
from namex_solr_api.services import jwt, solr
from namex_solr_api.services.namex_solr.doc_models import PossibleConflict

bp = Blueprint("RESYNC", __name__, url_prefix="/resync")


@bp.post("")
@cross_origin(origins="*")
@jwt.requires_roles([User.Role.system.value])
def resync_solr():
    """Resync solr docs from the given date or identifiers given."""
    try:
        request_json: dict = request.json
        from_datetime = datetime.now(UTC)
        minutes_offset = request_json.get("minutesOffset")
        identifiers_to_resync = request_json.get("identifiers")
        if not minutes_offset and not identifiers_to_resync:
            return bad_request_response('Missing required field "minutesOffset" or "identifiers".')
        try:
            minutes_offset = float(minutes_offset)
        except:  # pylint: disable=bare-except
            if not identifiers_to_resync:
                return bad_request_response(
                    'Invalid value for field "minutesOffset". Expecting a number.')

        if minutes_offset:
            # get all updates since the from_datetime
            resync_date = from_datetime - timedelta(minutes=minutes_offset)
            identifiers_to_resync = SolrDoc.get_updated_entity_ids_after_date(resync_date)

        if identifiers_to_resync:
            current_app.logger.debug(f"Resyncing: {identifiers_to_resync}")
            _resync_solr(identifiers_to_resync)
        else:
            current_app.logger.debug("No records to resync.")

        return jsonify({"message": "Resync successful."}), HTTPStatus.CREATED

    except Exception as exception:
        return exception_response(exception)


def _resync_solr(identifiers: list[str]):
    """Re-apply the docs for the given identifiers."""
    possible_conflicts: list[PossibleConflict] = []
    doc_events: list[SolrDocEvent] = []
    for identifier in identifiers:
        doc_update = SolrDoc.find_most_recent_by_entity_id(identifier)
        possible_conflicts.append(PossibleConflict(**doc_update.doc))
        # add separate event for resync
        doc_event = SolrDocEvent(event_type=SolrDocEvent.Type.RESYNC, solr_doc_id=doc_update.id).save()
        doc_events.append(doc_event)
    try:
        if len(possible_conflicts) > 0:
            solr.create_or_replace_docs(possible_conflicts, additive=False)
            SolrDocEvent.update_events_status(SolrDocEvent.Status.COMPLETE, doc_events)

    except Exception as err:
        # log / update event / pass err
        current_app.logger.debug("Failed to RESYNC solr for %s", identifiers)
        SolrDocEvent.update_events_status(SolrDocEvent.Status.ERROR, doc_events)
        raise err
