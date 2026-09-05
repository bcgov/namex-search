"""Unit tests for synonym stem lookup (no member-paint highlighter)."""

from types import SimpleNamespace
from unittest.mock import patch

from namex_solr_api.models import SolrSynonymList
from namex_solr_api.services.base_solr.utils.query_builder import QueryBuilder
from namex_solr_api.services.namex_solr.doc_models import NameField
from namex_solr_api.services.namex_solr.utils.analysis_helpers import parse_stemmed_tokens

CARRIER_MEMBERS = ["barge", "carrier", "transport", "truck"]


def _row(synonym: str, members: list[str]) -> SimpleNamespace:
    return SimpleNamespace(synonym=synonym, synonym_list=members)


SYNONYM_ROWS = [
    _row("carrier", CARRIER_MEMBERS),
    _row("truck", CARRIER_MEMBERS),
    _row("british columbia", ["bc", "british columbia"]),
    _row("british", ["british"]),
    _row("constructco", ["abode", "construction", "constructco"]),
    _row("construction", ["abode", "construction", "constructco"]),
]


def _find_beginning_with(phrase: str, _synonym_type):
    prefix = phrase.lower()
    return [row for row in SYNONYM_ROWS if row.synonym.lower().startswith(prefix)]


def _builder() -> QueryBuilder:
    return QueryBuilder(
        identifier_field_values=[],
        unique_parent_field=NameField.NAME,
        synonym_field_map={NameField.NAME_Q_SYN: SolrSynonymList.Type.ALL},
    )


def test_parse_stemmed_tokens_uses_last_index_step():
    response = {
        "analysis": {
            "field_types": {
                "text_stemmed_agro": {
                    "index": [
                        "org.apache.lucene.analysis.en.PorterStemFilter",
                        [{"text": "consum"}, {"text": "contract"}],
                    ]
                }
            }
        }
    }

    assert parse_stemmed_tokens(response) == ["consum", "contract"]


@patch.object(SolrSynonymList, "find_all_beginning_with_phrase", side_effect=_find_beginning_with)
def test_carriers_resolves_carrier_key(_mock_find):
    assert _builder().find_synonym_terms(
        "carriers", 0, ["carriers"], NameField.NAME_Q_SYN, ["carrier"]
    ) == ["carrier"]


@patch.object(SolrSynonymList, "find_all_beginning_with_phrase", side_effect=_find_beginning_with)
def test_trucking_resolves_truck_key(_mock_find):
    assert _builder().find_synonym_terms(
        "trucking", 0, ["trucking"], NameField.NAME_Q_SYN, ["truck"]
    ) == ["truck"]


@patch.object(SolrSynonymList, "find_all_beginning_with_phrase", side_effect=_find_beginning_with)
def test_constructco_still_resolves_raw_key(_mock_find):
    assert _builder().find_synonym_terms(
        "constructco", 0, ["constructco"], NameField.NAME_Q_SYN, ["constructco"]
    ) == ["constructco"]


@patch.object(SolrSynonymList, "find_all_beginning_with_phrase", side_effect=_find_beginning_with)
def test_british_columbias_keeps_longest_phrase(_mock_find):
    assert _builder().find_synonym_terms(
        "british",
        0,
        ["british", "columbias"],
        NameField.NAME_Q_SYN,
        ["british", "columbia"],
    ) == ["british", "columbia"]


@patch.object(SolrSynonymList, "find_all_beginning_with_phrase", side_effect=_find_beginning_with)
def test_unknown_stem_returns_empty(_mock_find):
    assert _builder().find_synonym_terms(
        "zzzzzzz", 0, ["zzzzzzz"], NameField.NAME_Q_SYN, ["zzzzzzz"]
    ) == []


@patch.object(SolrSynonymList, "find_all_beginning_with_phrase", side_effect=_find_beginning_with)
def test_carriers_query_emits_key_only(_mock_find):
    built = _builder().build_base_query(
        query={"value": "carriers"},
        fields={NameField.NAME_Q: "child"},
        boost_fields={NameField.NAME_Q_SYN: 2},
        fuzzy_fields={},
        synonym_fields={NameField.NAME_Q_SYN: "child"},
        is_child_search=True,
        stemmed_terms=["carrier"],
    )

    assert "name_q_synonym:carrier" in built["query"]
    assert "synonym_members" not in built
