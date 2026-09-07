"""NameX solr search functions."""
import re

from namex_solr_api.services.base_solr.utils import QueryParams
from namex_solr_api.services.namex_solr import NamexSolr
from namex_solr_api.services.namex_solr.doc_models import NameField, PCField

from .add_category_filters import add_category_filters
from .analysis_helpers import analyze_stemmed_agro_tokens


def format_full_query_boost(info: dict) -> str:
    """Render one full-query boost clause.

    Accepts three shapes: term_clauses (AND of pre-built clauses), values
    (AND of field:token), or value with an optional fuzzy width.
    """
    boost = info["boost"]
    if term_clauses := info.get("term_clauses"):
        inner = " AND ".join(term_clauses)
        return f"(({inner})^{boost})"
    field = info["field"].value
    if values := info.get("values"):
        inner = " AND ".join(f"{field}:{token}" for token in values)
        return f"(({inner})^{boost})"
    clause = f'{field}:"{info["value"]}"'
    if fuzzy := info.get("fuzzy"):
        clause += f"~{fuzzy}"
    return f"({clause}^{boost})"


def _apply_full_query_boosts(query_clause: str, boosts: list) -> str:
    for info in boosts:
        query_clause += f" OR {format_full_query_boost(info)}"
    return query_clause


def namex_search(params: QueryParams, solr: NamexSolr, is_name_search: bool, is_strict: bool = True):
    """Return the list of possible conflicts from Solr that match the query."""
    # initialize payload with base doc query (init query / filter)
    stemmed_terms = analyze_stemmed_agro_tokens(solr, params.query.get("value", ""))
    query_kwargs = {
        "query": params.query,
        "fields": params.query_fields,
        "boost_fields": params.query_boost_fields,
        "fuzzy_fields": params.query_fuzzy_fields,
        "synonym_fields": params.query_synonym_fields,
        "is_child_search": is_name_search,
        "clause_bridge": "AND" if is_strict else "OR",
        "stemmed_terms": stemmed_terms,
    }
    initial_queries = solr.query_builder.build_base_query(
        **query_kwargs,
        synonym_as_raw=True,
        expand_leftover_raw_synonyms=bool(
            getattr(params, "expand_leftover_raw_synonyms", False)
        ),
    )
    initial_queries["query"] = _apply_full_query_boosts(
        initial_queries["query"], params.full_query_boosts
    )

    highlight_query = None
    if params.highlighted_fields:
        highlight_query = initial_queries["query"]

    # add defaults
    parent_field = NameField.PARENT_TYPE.value if is_name_search else PCField.TYPE.value
    solr_payload = {
        **initial_queries,
        "queries": {
            "parents": f"{parent_field}:*",
            "parentFilters": " AND ".join(initial_queries["filter"]),
        },
        "fields": params.fields
    }
    if params.highlighted_fields:
        solr_payload = {
            **solr_payload,
            **namex_search_highlighting(params, highlight_query)
        }
    # base doc faceted filters
    add_category_filters(solr_payload=solr_payload,
                         categories=params.categories,
                         is_child=False,
                         is_child_search=is_name_search,
                         solr=solr)
    # child filter queries
    if child_query := solr.query_builder.build_child_query(params.child_query, is_name_search):
        solr_payload["filter"].append(child_query)

    # exclude firms
    if len(params.exclude_sub_types) > 0:
        exclude_sub_types = f"({') OR ('.join(params.exclude_sub_types)})"
        solr_payload["filter"].append(f"-sub_type:({exclude_sub_types})")
        solr_payload["filter"].append(f"-parent_sub_type:({exclude_sub_types})")

    # child doc faceted filter queries
    add_category_filters(solr_payload=solr_payload,
                         categories=params.child_categories,
                         is_child=True,
                         is_child_search=is_name_search,
                         solr=solr)

    resp: dict[str, dict[str, dict[str, list[str]]]] = solr.query(solr_payload, params.start, params.rows)
    parsed_highlighting = {}
    if solr_highlighting := resp.get('highlighting'):
        for result_id, result in solr_highlighting.items():
            parsed_highlighting[result_id] = {}
            for field_enum in params.highlighted_fields:
                if field_highlights := result.get(field_enum.value):
                    parsed_highlighting[result_id][field_enum.value] = []
                    for highlight in field_highlights:
                        parsed_highlighting[result_id][field_enum.value] += namex_search_parse_highlighting(highlight)
    resp['highlighting'] = parsed_highlighting
    return resp


def namex_search_highlighting(params: QueryParams, highlight_query: str | None = None):
    """Return the the highlighting params for the query."""
    hl_params = {
        "hl": "on",
        "hl.method": "unified",
        "hl.requireFieldMatch": "true",
        "hl.tag.pre": "|||",
        "hl.tag.post": "|||",
        "hl.fl": ",".join([x.value for x in params.highlighted_fields]),
    }
    if highlight_query:
        hl_params["hl.q"] = highlight_query
    return {"params": hl_params}


def namex_search_parse_highlighting(highlighted_value: str) -> list[str]:
    """Return the parsed list of highlighted terms."""
    highlighted_rgx = r'\|\|\|([^\|]*)\|\|\|'
    return re.findall(highlighted_rgx, highlighted_value)
