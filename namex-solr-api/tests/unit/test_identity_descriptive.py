"""Distinctive/descriptive search: term split, sound rule, identity-first ordering, boosts."""
from types import SimpleNamespace

from namex_solr_api.services.namex_solr.doc_models import NameField
from namex_solr_api.services.namex_solr.utils import (
    build_identity_descriptive_boosts,
    fit_phonetic_page,
    glued_exact_highlights,
    promote_adjacent_patterns,
    resolve_identity_descriptive,
    select_identity_descriptive_docs,
)
from namex_solr_api.services.namex_solr.utils.conflict_bucket import _real_phonetic_match, cover_query_token
from namex_solr_api.services.namex_solr.utils.namex_search_helper import format_full_query_boost
from namex_solr_api.services.namex_solr.utils.phonetic import primary_metaphone


def test_name_search_has_no_split_terms():
    """Empty boxes add nothing to the query."""
    assert resolve_identity_descriptive("", "") == ([], [])


def test_glued_name_word_is_one_exact_highlight():
    assert glued_exact_highlights("west coast", "WESTCOAST TRADING CORP.") == ["WESTCOAST"]
    assert glued_exact_highlights("coating west coast", "WESTCOAST COATING LTD.") == ["WESTCOAST"]
    assert glued_exact_highlights("west coast", "WEST COAST PEST CONTROL LTD.") == []


def test_split_terms_drop_designations_and_repeated_words():
    assert resolve_identity_descriptive("British Columbia", "Deconstructionists Ltd.") == (
        ["british", "columbia"],
        ["deconstructionists"],
    )
    assert resolve_identity_descriptive("west coast", "coast services") == (["west", "coast"], ["services"])


def test_primary_metaphone_codes():
    assert primary_metaphone("KLASSY") == primary_metaphone("CLASSY") == "KLS"
    assert primary_metaphone("PHONE") == "FN"
    assert primary_metaphone("KNIGHT") == "NT"
    assert primary_metaphone("THOMAS") == "0MS"
    assert primary_metaphone("BDFGLMNX") == "PTFKLMNK"  # X adds KS; code stays 8 long
    assert primary_metaphone("") == ""


def test_one_sound_rule_for_name_search_and_distinctive():
    assert _real_phonetic_match("CLASSY", "KLASSY")
    assert _real_phonetic_match("BEAR", "BAR")  # one extra vowel
    assert _real_phonetic_match("NIGHT", "KNIGHT")  # same first sounds
    assert _real_phonetic_match("FONE", "PHONE")  # same metaphone code
    assert _real_phonetic_match("KATHRYN", "KATHERINE")
    assert not _real_phonetic_match("BORE", "BAR")
    assert not _real_phonetic_match("456", "123")  # no letters -> no sound
    assert not _real_phonetic_match("3S", "SEE")  # not a word
    assert not _real_phonetic_match("CARDIFF", "CREATIVE")  # same code, different word
    assert cover_query_token("VPRO", ["VIPRO"]) == "fuzzy"  # a close spelling stays a close spelling


def _doc(name: str, record_id: str = "") -> dict:
    return {"id": record_id or name, "name": name, "bucket": "synonym"}


def test_identity_first_then_the_rest_nothing_dropped():
    docs = [
        _doc("GENOME BRITISH COLUMBIA"),
        _doc("BRITISH COLUMBIA LUNG ASSOCIATION"),
        _doc("BRITISH COLUMBIA DECONSTRUCTIONISTS LTD."),
    ]
    led, others = select_identity_descriptive_docs(docs, ["british", "columbia"], ["deconstructionists"])
    assert [doc["name"] for doc in led] == [
        "BRITISH COLUMBIA DECONSTRUCTIONISTS LTD.",
        "BRITISH COLUMBIA LUNG ASSOCIATION",
    ]
    assert [doc["name"] for doc in others] == ["GENOME BRITISH COLUMBIA"]


def test_other_matches_keep_name_search_order():
    docs = [_doc("BC DECONSTRUCTIONISTS LTD."), _doc("TEKNOSOFT INC.")]
    led, others = select_identity_descriptive_docs(docs, ["deconstructionists"], [])
    assert led == []
    assert [doc["name"] for doc in others] == ["BC DECONSTRUCTIONISTS LTD.", "TEKNOSOFT INC."]


def test_name_matching_every_distinctive_word_is_kept_on_top():
    docs = [
        _doc("WEST COAST KINESIOLOGY SERVICES LTD."),
        _doc("WEST COAST ELEVATOR SERVICES LTD."),
        _doc("WEST COAST LANDSCAPING MAINTENANCE LTD."),
    ]
    led, others = select_identity_descriptive_docs(
        docs, ["west", "coast", "landscaping", "maintenance"], ["services"]
    )
    assert [doc["name"] for doc in fit_phonetic_page(led, 50)] == [
        "WEST COAST LANDSCAPING MAINTENANCE LTD.",
        "WEST COAST KINESIOLOGY SERVICES LTD.",
        "WEST COAST ELEVATOR SERVICES LTD.",
    ]
    assert others == []


def test_rearranged_leading_words_go_first():
    forward = [_doc("WEST COAST LANDSCAPING LTD.")]
    candidates = [*forward, _doc("COAST WEST LTD.")]
    promoted = promote_adjacent_patterns(forward, candidates, ["west", "coast"])
    assert [doc["name"] for doc in promoted] == ["COAST WEST LTD.", "WEST COAST LANDSCAPING LTD."]


def test_same_name_different_records_are_both_kept():
    forward = [_doc("COAST WEST LTD.", "NR 1"), _doc("COAST WEST LTD.", "BC0000001")]
    promoted = promote_adjacent_patterns(forward, forward, ["west", "coast"])
    assert [doc["id"] for doc in promoted] == ["NR 1", "BC0000001"]


def test_page_order_keeps_described_sound_alikes_before_plain_ones():
    docs = [
        {"name": "PLAIN", "bucket": "synonym"},
        {"name": "SOUND ONLY", "bucket": "phonetic"},
        {"name": "SOUND + DESC", "bucket": "phonetic", "_phonetic_desc": True},
        {"name": "IDENTITY + DESC", "bucket": "synonym", "_keep": True},
    ]
    page = fit_phonetic_page(docs, 50)
    assert [doc["name"] for doc in page] == ["IDENTITY + DESC", "SOUND + DESC", "PLAIN", "SOUND ONLY"]
    assert all("_keep" not in doc and "_phonetic_desc" not in doc for doc in page)


def test_boosts_are_added_to_the_one_query():
    builder = SimpleNamespace(
        build_term_clause=lambda term, *args: f"t:{term}",
        build_term_synonym_clauses=lambda clause, words, index, *args: f"{clause} OR syn:{words[index]}",
    )
    params = SimpleNamespace(
        stemmed_terms_map={},
        constant_score_terms=[],
        query_fields={},
        query_boost_fields={},
        query_fuzzy_fields={},
        query_synonym_fields={},
    )
    boosts = build_identity_descriptive_boosts(SimpleNamespace(query_builder=builder), params, ["klassy"], ["bar"])
    identity, sound, described_sound, word_order = boosts
    assert "((name:KLASSY\\ *)^1000000)" in identity["term_clauses"][0]  # name starts with KLASSY
    assert "(name:KLASS* AND (t:klassy))" in identity["term_clauses"][0]  # plural-widened start
    assert "((name_q_exact:klassy)^1000000)" in identity["term_clauses"][0]  # starts with KLASSY, spaces ignored
    assert "AND (t:bar OR syn:bar))^10000)" in identity["term_clauses"][0]  # + descriptive with synonyms
    first_word_sound = (  # sound-alike of the FIRST word only
        '_query_:"{!xmlparser}<SpanFirst end=\\"1\\">'
        '<SpanOrTerms fieldName=\\"name_q_phon_en\\">klassy</SpanOrTerms></SpanFirst>"'
    )
    assert sound == {"term_clauses": [first_word_sound], "boost": 10_000}
    assert described_sound["term_clauses"] == [f"({first_word_sound}) AND (t:bar OR syn:bar)"]
    assert format_full_query_boost(word_order) == '(name_q:"klassy bar"~4^10000)'
    assert word_order["field"] == NameField.NAME_Q
