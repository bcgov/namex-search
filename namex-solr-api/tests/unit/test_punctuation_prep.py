"""Unit tests for ticket 34730 conflict initials query normalization."""

import inspect

import pytest

from namex_solr_api.resources.v1 import search
from namex_solr_api.services.base_solr.utils.formatting_helpers import prep_query_str
from namex_solr_api.services.namex_solr.utils.formatting_helpers import normalize_conflict_initials

H_EQUIVALENTS = [
    "HH INVESTMENTS",
    "H H INVESTMENTS",
    "H&H INVESTMENTS",
    "H.H. INVESTMENTS",
    "H & H INVESTMENTS",
    "H. & H. INVESTMENTS",
]

JM_EQUIVALENTS = [
    "JM HOLDINGS",
    "J.M. HOLDINGS",
    "J&M HOLDINGS",
    "J & M HOLDINGS",
    "J. & M. HOLDINGS",
]


def conflict_terms(name: str) -> list[str]:
    """Mirror possible-conflict value prep: normalize initials, then existing prep/split."""
    return prep_query_str(normalize_conflict_initials(name), "replace").split()


@pytest.mark.parametrize("name", H_EQUIVALENTS)
def test_h_initial_forms_produce_equivalent_conflict_terms(name):
    """HH / H H / H&H / H.H. / H & H / H. & H. must share conflict AND terms."""
    assert conflict_terms(name) == conflict_terms("H H INVESTMENTS")
    assert "and" not in conflict_terms(name)


def test_h_investments_stays_one_h():
    """A single H must not gain a second initial."""
    assert conflict_terms("H INVESTMENTS") == ["h", "investments"]
    assert conflict_terms("H INVESTMENTS") != conflict_terms("HH INVESTMENTS")


def test_in_business_does_not_split_in():
    """IN is a Solr English stopword and must stay a word."""
    assert conflict_terms("IN BUSINESS")[0] == "in"
    assert conflict_terms("IN BUSINESS") != ["i", "n", "business"]


def test_bc_does_not_split_to_b_c():
    """BC is the schema/NameX British Columbia token and must stay intact."""
    assert conflict_terms("BC HOLDINGS")[0] == "bc"
    assert conflict_terms("BC HOLDINGS") != ["b", "c", "holdings"]


@pytest.mark.parametrize("name", JM_EQUIVALENTS)
def test_jm_initial_forms_produce_equivalent_conflict_terms(name):
    """JM / J.M. / J&M / J & M / J. & M. must share conflict AND terms."""
    assert conflict_terms(name) == ["j", "m", "holdings"]


def test_jmj_stays_three_initials():
    """J.M.J. must not collapse to JM."""
    assert conflict_terms("J.M.J. HOLDINGS") == ["j", "m", "j", "holdings"]
    assert conflict_terms("J.M.J. HOLDINGS") != conflict_terms("J.M. HOLDINGS")


def test_possible_conflict_names_normalizes_before_prep():
    """Normalizer must run on the conflict name value before prep / AND-split."""
    source = inspect.getsource(search.possible_conflict_names)
    assert "normalize_conflict_initials" in source
    assert source.index("normalize_conflict_initials") < source.index("prep_query_str_namex(value, \"replace\")")


def test_nrs_does_not_use_conflict_initials_normalizer():
    """/nrs is out of scope for ticket 34730."""
    source = inspect.getsource(search.nrs)
    assert "normalize_conflict_initials" not in source
