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
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
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
"""Test suite for child document deletion when updating parent documents."""

from unittest.mock import MagicMock, call, patch

import pytest

from namex_solr_api.services.namex_solr import NamexSolr
from namex_solr_api.services.namex_solr.doc_models import (Name,
                                                           PossibleConflict)


@pytest.fixture
def solr_service(app):
    """Create a NamexSolr instance for testing."""
    solr = NamexSolr("SOLR")
    solr.init_app(app)
    return solr


class TestChildDocumentDeletion:
    """Test suite for verifying child document deletion on parent updates."""

    def test_additive_true_does_not_delete_children(self, solr_service):
        """Test that additive=True does not trigger child document deletion."""
        with patch.object(solr_service, 'call_solr') as mock_call:
            with patch.object(solr_service, '_delete_old_child_docs') as mock_delete:
                docs = [
                    PossibleConflict(
                        id="NR6546542",
                        names=[Name(name="Test Name", name_state="APPROVED")],
                        state="ACTIVE",
                        type="NR",
                        sub_type="NR"
                    )
                ]
                mock_call.return_value = MagicMock()

                solr_service.create_or_replace_docs(docs=docs, additive=True)

                # _delete_old_child_docs should NOT be called when additive=True
                mock_delete.assert_not_called()

    def test_additive_false_triggers_child_deletion(self, solr_service):
        """Test that additive=False triggers child document deletion."""
        with patch.object(solr_service, 'call_solr') as mock_call:
            with patch.object(solr_service, '_delete_old_child_docs') as mock_delete:
                docs = [
                    PossibleConflict(
                        id="NR6546542",
                        names=[Name(name="Test Name", name_state="APPROVED")],
                        state="ACTIVE",
                        type="NR",
                        sub_type="NR"
                    )
                ]
                mock_call.return_value = MagicMock()

                solr_service.create_or_replace_docs(docs=docs, additive=False)

                # _delete_old_child_docs SHOULD be called when additive=False
                mock_delete.assert_called_once()

    def test_delete_old_child_docs_issues_single_delete_query(self, solr_service):
        """Test that _delete_old_child_docs issues a single delete-by-query to Solr."""
        with patch.object(solr_service, 'call_solr') as mock_call:
            mock_call.return_value = MagicMock()

            parent_docs = [{"id": "NR6546542"}]
            solr_service._delete_old_child_docs(parent_docs)

            # Verify a single call was made (not one per parent)
            mock_call.assert_called_once()
            _, kwargs = mock_call.call_args[0], mock_call.call_args[1] or {}
            xml = mock_call.call_args[1].get("xml_data", "") or mock_call.call_args[0][2] if len(mock_call.call_args[0]) > 2 else ""
            # The XML should contain the parent ID and parent_id field
            assert "NR6546542" in str(mock_call.call_args)
            assert "parent_id" in str(mock_call.call_args)

    def test_delete_old_child_docs_skips_empty_parent_list(self, solr_service):
        """Test that _delete_old_child_docs does nothing when parent list is empty."""
        with patch.object(solr_service, 'call_solr') as mock_call:
            solr_service._delete_old_child_docs([])
            mock_call.assert_not_called()

    def test_delete_old_child_docs_skips_docs_without_id(self, solr_service):
        """Test that _delete_old_child_docs skips docs with no id."""
        with patch.object(solr_service, 'call_solr') as mock_call:
            solr_service._delete_old_child_docs([{"other_field": "value"}])
            mock_call.assert_not_called()

    def test_delete_old_child_docs_multiple_parents_single_call(self, solr_service):
        """Test that multiple parents result in a single batched delete-by-query."""
        with patch.object(solr_service, 'call_solr') as mock_call:
            mock_call.return_value = MagicMock()

            parent_docs = [{"id": "NR1"}, {"id": "NR2"}]
            solr_service._delete_old_child_docs(parent_docs)

            # Verify only ONE Solr call was made regardless of parent count
            mock_call.assert_called_once()
            call_str = str(mock_call.call_args)
            assert "NR1" in call_str
            assert "NR2" in call_str
            assert "parent_id" in call_str

    def test_scenario_update_reduces_names_count(self, solr_service):
        """Test the main bug scenario: updating NR with fewer names than before.
        Verifies that a delete-by-query for the parent is issued before the update,
        ensuring orphaned child docs from a previous larger name list are cleaned up.
        """
        with patch.object(solr_service, 'call_solr') as mock_call:
            mock_call.return_value = MagicMock()

            new_docs = [
                PossibleConflict(
                    id="NR6546542",
                    names=[
                        Name(name="Name 1", name_state="APPROVED"),
                        Name(name="Name 2", name_state="APPROVED"),
                        Name(name="Name 3", name_state="APPROVED"),
                    ],
                    state="ACTIVE",
                    type="NR",
                    sub_type="NR"
                )
            ]

            solr_service.create_or_replace_docs(docs=new_docs, additive=False)

            # First call should be the delete-by-query for old children
            assert mock_call.call_count >= 2
            first_call_str = str(mock_call.call_args_list[0])
            assert "NR6546542" in first_call_str
            assert "parent_id" in first_call_str

    def test_delete_uses_correct_parent_id_field(self, solr_service):
        """Verify that the delete-by-query uses the correct Solr field for parent_id."""
        with patch.object(solr_service, 'call_solr') as mock_call:
            mock_call.return_value = MagicMock()

            parent_docs = [{"id": "CORP123"}]
            solr_service._delete_old_child_docs(parent_docs)

            call_str = str(mock_call.call_args)
            assert "parent_id" in call_str
            assert "CORP123" in call_str

    def test_error_handling_call_solr_failure(self, solr_service):
        """Test that a Solr error during child doc deletion is non-fatal (warning only)."""
        with patch.object(solr_service, 'call_solr') as mock_call:
            mock_call.side_effect = Exception("Solr connection error")

            parent_docs = [{"id": "NR1"}, {"id": "NR2"}]
            # Should NOT raise — delete failure is best-effort
            solr_service._delete_old_child_docs(parent_docs)

    def test_raw_docs_bypass_deletion(self, solr_service):
        """Test that raw_docs bypass child deletion logic (intentional)."""
        with patch.object(solr_service, 'call_solr') as mock_call:
            with patch.object(solr_service, '_delete_old_child_docs') as mock_delete:
                raw_docs = [
                    {
                        "id": "NR6546542",
                        "names": [{"name": "Test"}]
                    }
                ]
                mock_call.return_value = MagicMock()

                solr_service.create_or_replace_docs(
                    raw_docs=raw_docs,
                    additive=False
                )

                # When raw_docs are provided, deletion should be skipped
                # (assumption: raw_docs are already pre-processed)
                mock_delete.assert_not_called()
                mock_delete.assert_not_called()
