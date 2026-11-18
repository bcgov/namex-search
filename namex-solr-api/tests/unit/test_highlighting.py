"""Tests for the possible-conflict highlighting params."""
from types import SimpleNamespace

from namex_solr_api.services.namex_solr.doc_models import NameField
from namex_solr_api.services.namex_solr.utils.namex_search_helper import namex_search_highlighting


def test_highlighting_turns_off_weight_matches():
    """weightMatches=true makes Solr re-run the fuzzy/wildcard hl.q per doc (7-15s); keep it off."""
    params = SimpleNamespace(highlighted_fields=[NameField.NAME_Q_SINGLE])
    hl_params = namex_search_highlighting(params, "name_q_single:test")["params"]
    assert hl_params["hl.weightMatches"] == "false"
