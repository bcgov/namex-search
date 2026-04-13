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
"""This module wraps the solr classes/fields for using namex solr."""
from dataclasses import asdict

from flask import Flask
from namex_solr_api.models import SolrSynonymList
from namex_solr_api.services.base_solr import Solr
from namex_solr_api.services.base_solr.utils import (QueryBuilder,
                                                     prep_query_str)

from .doc_models.name import Name, NameField
from .doc_models.possible_conflict import PCField, PossibleConflict


class NamexSolr(Solr):
    """Extends the solr wrapper class for namex specific functionality."""

    def __init__(self, config_prefix: str, app: Flask = None) -> None:
        super().__init__(config_prefix, app)
        self.query_builder = QueryBuilder(
            identifier_field_values=[],
            unique_parent_field=PCField.TYPE,
            synonym_field_map={NameField.NAME_Q_SYN: SolrSynonymList.Type.ALL})

        # fields
        self.resp_fields = [
            PCField.CORP_NUM.value,
            PCField.JURISDICTION.value,
            PCField.NR_NUM.value,
            PCField.START_DATE.value,
            PCField.STATE.value,
            PCField.TYPE.value,
            PCField.SUB_TYPE.value,
            PCField.NAMES.value,
            "[child]",
            NameField.CHOICE.value,
            NameField.NAME.value,
            NameField.NAME_STATE.value,
            NameField.SUBMIT_COUNT.value
        ]
        self.resp_fields_nested = [
            NameField.CHOICE.value,
            NameField.NAME.value,
            NameField.NAME_STATE.value,
            NameField.SUBMIT_COUNT.value,
            NameField.PARENT_ID.value,
            NameField.PARENT_JURISDICTION.value,
            NameField.PARENT_START_DATE.value,
            NameField.PARENT_STATE.value,
            NameField.PARENT_TYPE.value,
            NameField.PARENT_SUB_TYPE.value,
            NameField.UNIQUE_KEY.value,
        ]

    def create_or_replace_docs(self,
                               docs: list[PossibleConflict] | None = None,
                               raw_docs: list[dict] | None = None,
                               timeout=25,
                               additive=True):
        """Create or replace solr docs in the core.

        When additive=False, this method will:
        1. Delete all old child documents (e.g., NR6546542-name-0, NR6546542-name-1, etc.)
        2. Insert new documents with updated data

        This ensures no orphaned child documents remain after an update.
        """
        update_list = raw_docs if raw_docs else [asdict(doc) for doc in docs]

        if not additive and not raw_docs:
            # Delete old child documents before updating
            self._delete_old_child_docs(update_list)

            for pc_dict in update_list:
                # names
                if names := pc_dict.get(PCField.NAMES.value, None):
                    pc_dict[PCField.NAMES.value] = {"set": names}

        url = self.update_url if len(update_list) < 1000 else self.bulk_update_url  # noqa: PLR2004
        return self.call_solr("POST", url, json_data=update_list, timeout=timeout)

    def _delete_old_child_docs(self, parent_docs: list[dict]):
        """Delete all old child documents before updating parent docs.

        For each parent document (e.g., NR6546542), this queries Solr to find
        all existing child documents (e.g., NR6546542-name-0, NR6546542-name-1, ...)
        and deletes them to prevent orphaned records.

        Args:
            parent_docs: List of parent document dicts to update
        """
        from flask import current_app

        child_doc_ids_to_delete = []

        for parent_doc in parent_docs:
            parent_id = parent_doc.get("id")
            if not parent_id:
                continue

            try:
                # Query Solr to find all child docs with this parent_id
                # Child docs have IDs like: {parent_id}-name-0, {parent_id}-name-1, etc.
                query_payload = {
                    "q": f"{NameField.PARENT_ID.value}:{parent_id}",
                    "fl": NameField.UNIQUE_KEY.value,
                    "rows": 1000  # Reasonable limit for child docs
                }

                search_response = self.query(query_payload)
                docs = search_response.get("response", {}).get("docs", [])

                # Collect all child doc IDs for deletion
                for doc in docs:
                    if child_id := doc.get(NameField.UNIQUE_KEY.value):
                        child_doc_ids_to_delete.append(child_id)
                        current_app.logger.debug(
                            f"Marked child doc for deletion: {child_id} (parent: {parent_id})"
                        )

            except Exception as err:
                current_app.logger.warning(
                    f"Failed to query child docs for parent {parent_id}: {err}"
                )
                # Continue processing other parents even if one fails
                continue

        # Delete all collected child doc IDs
        if child_doc_ids_to_delete:
            try:
                current_app.logger.debug(
                    f"Deleting {len(child_doc_ids_to_delete)} old child documents"
                )
                self.delete_docs(child_doc_ids_to_delete)
                current_app.logger.info(
                    f"Successfully deleted {len(child_doc_ids_to_delete)} old child documents"
                )
            except Exception as err:
                current_app.logger.error(
                    f"Failed to delete old child documents: {err}"
                )
                raise

    @staticmethod
    def get_name_search_full_query_boost(query_value: str):
        """Return the list of full query boost information intended for business search."""
        full_query_boosts = [
            {
                "field": NameField.NAME_Q_EXACT,
                "value": prep_query_str(query_value),
                "boost": "3",
            },
            {
                "field": NameField.NAME_Q_SINGLE,
                "value": prep_query_str(query_value),
                "boost": "2",
            },
            {
                "field": NameField.NAME_Q,
                "value": prep_query_str(query_value),
                "boost": "5",
                "fuzzy": "5"
            },
            {
                "field": NameField.NAME_Q_AGRO,
                "value": prep_query_str(query_value),
                "boost": "3",
                "fuzzy": "10"
            }
        ]
        # add more boost clauses if a dash is in the query
        if "-" in query_value:
            full_query_boosts += [
                {
                    "field": NameField.NAME_Q,
                    "value": prep_query_str(query_value, "remove"),
                    "boost": "3",
                    "fuzzy": "5"
                },
                {
                    "field": NameField.NAME_Q,
                    "value": prep_query_str(query_value, "pad"),
                    "boost": "7",
                    "fuzzy": "5"
                },
                {
                    "field": NameField.NAME_Q,
                    "value": prep_query_str(query_value, "tighten"),
                    "boost": "7",
                    "fuzzy": "5"
                },
                {
                    "field": NameField.NAME_Q,
                    "value": prep_query_str(query_value, "tighten-remove"),
                    "boost": "3",
                    "fuzzy": "5"
                }
            ]
        return full_query_boosts
