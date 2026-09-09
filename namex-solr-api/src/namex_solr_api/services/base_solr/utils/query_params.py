"""Solr query params."""
from dataclasses import dataclass, field

from namex_solr_api.common.base_enum import BaseEnum


@dataclass
class QueryParams:  # pylint: disable=too-few-public-methods
    """Class definition of query params."""

    query: dict[str, str]
    rows: int
    start: int
    categories: dict[BaseEnum, list[str]]
    child_query: dict[str, str]
    child_categories: dict[BaseEnum, list[str]]
    fields: list[str]
    highlighted_fields: list[BaseEnum]
    query_fields: dict[BaseEnum, str]
    query_boost_fields: dict[BaseEnum, int]
    query_fuzzy_fields: dict[BaseEnum, dict[str, int]]
    query_synonym_fields: dict[BaseEnum, str]
    full_query_boosts: list[dict[str, BaseEnum | str]]
    exclude_sub_types: list[str]
    expand_leftover_raw_synonyms: bool = False
    # Outer-wildcard tokens scored as presence (Solr ^=). Empty for /nrs
    # and normal conflict search so BM25 defaults stay unchanged.
    constant_score_terms: list[str] = field(default_factory=list)
    override_query: str | None = None
