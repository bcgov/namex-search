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

    def test_delete_old_child_docs_finds_and_deletes_children(self, solr_service):
        """Test that _delete_old_child_docs finds old child docs and deletes them."""
        # Mock the query method to return existing child docs
        with patch.object(solr_service, 'query') as mock_query:
            with patch.object(solr_service, 'delete_docs') as mock_delete:
                # Simulate Solr returning 3 existing child docs
                mock_query.return_value = {
                    "response": {
                        "docs": [
                            {"id": "NR6546542-name-0"},
                            {"id": "NR6546542-name-1"},
                            {"id": "NR6546542-name-2"},
                        ]
                    }
                }
                
                parent_docs = [{"id": "NR6546542"}]
                solr_service._delete_old_child_docs(parent_docs)
                
                # Verify query was called to find child docs
                mock_query.assert_called_once()
                
                # Verify delete_docs was called with correct child IDs
                mock_delete.assert_called_once_with(
                    ["NR6546542-name-0", "NR6546542-name-1", "NR6546542-name-2"]
                )

    def test_delete_old_child_docs_handles_no_existing_children(self, solr_service):
        """Test that _delete_old_child_docs handles case with no existing children."""
        with patch.object(solr_service, 'query') as mock_query:
            with patch.object(solr_service, 'delete_docs') as mock_delete:
                # Simulate Solr returning no child docs
                mock_query.return_value = {"response": {"docs": []}}
                
                parent_docs = [{"id": "NR6546542"}]
                solr_service._delete_old_child_docs(parent_docs)
                
                # Verify query was called
                mock_query.assert_called_once()
                
                # Verify delete_docs was NOT called (no children to delete)
                mock_delete.assert_not_called()

    def test_delete_old_child_docs_multiple_parents(self, solr_service):
        """Test that _delete_old_child_docs handles multiple parent documents."""
        with patch.object(solr_service, 'query') as mock_query:
            with patch.object(solr_service, 'delete_docs') as mock_delete:
                # Mock different results for different parent IDs
                def query_side_effect(payload):
                    parent_id = payload["q"].split(":")[1]
                    if parent_id == "NR1":
                        return {
                            "response": {
                                "docs": [
                                    {"id": "NR1-name-0"},
                                    {"id": "NR1-name-1"},
                                ]
                            }
                        }
                    elif parent_id == "NR2":
                        return {
                            "response": {
                                "docs": [
                                    {"id": "NR2-name-0"},
                                ]
                            }
                        }
                    return {"response": {"docs": []}}
                
                mock_query.side_effect = query_side_effect
                
                parent_docs = [{"id": "NR1"}, {"id": "NR2"}]
                solr_service._delete_old_child_docs(parent_docs)
                
                # Verify query was called twice (once per parent)
                assert mock_query.call_count == 2
                
                # Verify delete_docs was called with all child IDs combined
                expected_child_ids = [
                    "NR1-name-0", "NR1-name-1",
                    "NR2-name-0"
                ]
                mock_delete.assert_called_once_with(expected_child_ids)

    def test_scenario_update_reduces_names_count(self, solr_service):
        """Test the main bug scenario: updating NR with fewer names than before.
        
        This tests the exact scenario from the bug report:
        - Old NR entry had 5 names (name-0 through name-4)
        - New NR entry has 3 names (name-0 through name-2)
        - ALL 5 old child docs should be deleted first (clean slate)
        - Then new 3 child docs are inserted
        """
        with patch.object(solr_service, 'query') as mock_query:
            with patch.object(solr_service, 'delete_docs') as mock_delete:
                with patch.object(solr_service, 'call_solr') as mock_call:
                    # Simulate Solr returning 5 existing child docs (old data)
                    mock_query.return_value = {
                        "response": {
                            "docs": [
                                {"id": "NR6546542-name-0"},
                                {"id": "NR6546542-name-1"},
                                {"id": "NR6546542-name-2"},
                                {"id": "NR6546542-name-3"},  # OLD - should be deleted
                                {"id": "NR6546542-name-4"},  # OLD - should be deleted
                            ]
                        }
                    }
                    mock_call.return_value = MagicMock()
                    
                    # New doc with only 3 names
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
                    
                    # Verify that ALL 5 old child docs are deleted
                    # (This ensures a clean slate before inserting new ones)
                    deleted_ids = mock_delete.call_args[0][0]
                    assert "NR6546542-name-0" in deleted_ids
                    assert "NR6546542-name-1" in deleted_ids
                    assert "NR6546542-name-2" in deleted_ids
                    assert "NR6546542-name-3" in deleted_ids
                    assert "NR6546542-name-4" in deleted_ids
                    assert len(deleted_ids) == 5  # All old children deleted

    def test_query_uses_correct_parent_id_field(self, solr_service):
        """Verify that the query uses the correct Solr field for parent_id."""
        with patch.object(solr_service, 'query') as mock_query:
            with patch.object(solr_service, 'delete_docs'):
                mock_query.return_value = {"response": {"docs": []}}
                
                parent_docs = [{"id": "CORP123"}]
                solr_service._delete_old_child_docs(parent_docs)
                
                # Verify the query payload uses correct field name
                call_args = mock_query.call_args[0][0]
                # The query should filter by parent_id field
                assert "parent_id" in call_args["q"].lower() or "parent_id" in str(call_args)

    def test_error_handling_query_failure(self, solr_service):
        """Test that errors in querying child docs don't prevent other parents being processed."""
        with patch.object(solr_service, 'query') as mock_query:
            with patch.object(solr_service, 'delete_docs'):
                # First call fails, second succeeds
                mock_query.side_effect = [
                    Exception("Solr connection error"),
                    {"response": {"docs": [{"id": "NR2-name-0"}]}}
                ]
                
                parent_docs = [{"id": "NR1"}, {"id": "NR2"}]
                # Should not raise exception
                solr_service._delete_old_child_docs(parent_docs)
                
                # Verify both queries were attempted despite first failure
                assert mock_query.call_count == 2

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
