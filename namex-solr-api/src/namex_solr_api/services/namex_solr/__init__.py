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
import re
import time
from dataclasses import asdict

from flask import Flask

from namex_solr_api.models import SolrSynonymList
from namex_solr_api.services.base_solr import Solr
from namex_solr_api.services.base_solr.utils import QueryBuilder, prep_query_str

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

    _DELETE_BATCH_SIZE = 200
    _DELETE_TIMEOUT = 30  # per-batch timeout; delete is best-effort, don't block the import

    @staticmethod
    def _escape_solr_value(value: str) -> str:
        """Escape Solr query special characters in a field value."""
        return re.sub(r'([\\+\-!\(\)\{\}\[\]\^"~*?:|&;])', r'\\\1', value)

    def _delete_old_child_docs(self, parent_docs: list[dict]):
        """Delete all old child documents before updating parent docs.

        Uses the Solr JSON update API with field:("v1" "v2" ...) IN-style syntax,
        batched to avoid query size limits.

        This is best-effort: if a batch delete fails or times out, a warning is
        logged and the import continues. At worst, a few orphaned child docs may
        remain until the next import overwrites them.

        Args:
            parent_docs: List of parent document dicts to update
        """
        from flask import current_app

        parent_ids = [doc.get("id") for doc in parent_docs if doc.get("id")]
        missing = [doc for doc in parent_docs if not doc.get("id")]
        if missing:
            current_app.logger.warning(
                "%d parent docs missing id. Example: %s",
                len(missing),
                missing[0]
            )
        if not parent_ids:
            return

        field = NameField.PARENT_ID.value
        for i in range(0, len(parent_ids), self._DELETE_BATCH_SIZE):
            batch = parent_ids[i:i + self._DELETE_BATCH_SIZE]
            # Space-separated quoted+escaped values — Solr IN-style syntax.
            # Does not use OR, which causes parser explosion on large lists.
            values = " ".join(f'"{self._escape_solr_value(pid)}"' for pid in batch)
            # No commitWithin: the subsequent insert uses update_url (commit=true),
            # which commits both the delete and insert together. Adding commitWithin
            # risks Solr firing the delete commit *after* the insert, which would
            # delete the freshly inserted child docs that share the same parent_id.
            payload = {"delete": {"query": f"{field}:({values})"}}
            batch_num = i // self._DELETE_BATCH_SIZE + 1
            t0 = time.monotonic()
            current_app.logger.debug("Deleting child docs for %d parents (batch %d)", len(batch), batch_num)
            try:
                self.call_solr("POST", self.bulk_update_url, json_data=payload, timeout=self._DELETE_TIMEOUT)
                current_app.logger.debug("Delete batch %d done in %.1fs", batch_num, time.monotonic() - t0)
            except Exception as err:
                # Non-fatal: log and continue so the import itself is not blocked.
                # Orphaned child docs will be overwritten on the next import.
                current_app.logger.warning(
                    "Child doc delete failed for batch %d after %.1fs (will continue): %s",
                    batch_num, time.monotonic() - t0, err
                )
        current_app.logger.debug("Finished child doc cleanup for %d parents", len(parent_ids))

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