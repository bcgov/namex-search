"""NameX solr search functions."""
import re
from dataclasses import replace
from itertools import permutations

from namex_solr_api.services.base_solr.utils import QueryParams
from namex_solr_api.services.base_solr.utils.query_builder import QueryBuilder
from namex_solr_api.services.namex_solr import NamexSolr
from namex_solr_api.services.namex_solr.doc_models import NameField, PCField

from .add_category_filters import add_category_filters
from .analysis_helpers import analyze_stemmed_agro_tokens
from .formatting_helpers import (
    identity_term_groups,
    reserved_coverage_params,
    reserved_glued_concat_query,
    reserved_prefix_params,
    should_run_reserved_coverage,
)

IDENTITY_DESCRIPTIVE_BOOST = 10_000
IDENTITY_FULL_BOOST = 1_000_000
PATTERN_CANDIDATE_ROWS = 25
LOOSE_FIRST_ROWS = 10
_MAX_PATTERN_WORDS = 5
_PATTERN_CLAUSE_BUDGET = 200


def format_full_query_boost(info: dict) -> str:
    """Render one full-query boost clause.

    Accepts three shapes: term_clauses (AND of pre-built clauses), values
    (AND of field:token), or value with an optional fuzzy width.
    """
    boost = info["boost"]
    score_op = "^=" if info.get("constant_score") else "^"
    if term_clauses := info.get("term_clauses"):
        inner = " AND ".join(term_clauses)
        return f"(({inner}){score_op}{boost})"
    field = info["field"].value
    if values := info.get("values"):
        inner = " AND ".join(f"{field}:{token}" for token in values)
        return f"(({inner}){score_op}{boost})"
    clause = f'{field}:"{info["value"]}"'
    if fuzzy := info.get("fuzzy"):
        clause += f"~{fuzzy}"
    return f"({clause}{score_op}{boost})"


def build_namex_query_payload(
    params: QueryParams,
    solr: NamexSolr,
    is_name_search: bool,
    is_strict: bool,
    clause_bridge: str | None = None,
) -> dict[str, list[str]]:
    if clause_bridge is None:
        clause_bridge = "AND" if is_strict else "OR"
    value = params.query.get("value", "")
    if (stem_map := params.stemmed_terms_map) is not None:
        # one upstream analyzer call serves every lane (lane values are term subsets)
        stemmed_terms = [stem_map.get(term, term) for term in value.split()]
    else:
        stemmed_terms = analyze_stemmed_agro_tokens(solr, value)
    return solr.query_builder.build_base_query(
        query=params.query,
        fields=params.query_fields,
        boost_fields=params.query_boost_fields,
        fuzzy_fields=params.query_fuzzy_fields,
        synonym_fields=params.query_synonym_fields,
        is_child_search=is_name_search,
        clause_bridge=clause_bridge,
        stemmed_terms=stemmed_terms,
        synonym_as_raw=True,
        constant_score_terms=frozenset(params.constant_score_terms or []),
    )


def query_with_full_boosts(
    params: QueryParams, solr: NamexSolr, is_name_search: bool, is_strict: bool
) -> str:
    payload = build_namex_query_payload(params, solr, is_name_search, is_strict)
    for info in params.full_query_boosts:
        payload["query"] += f" OR {format_full_query_boost(info)}"
    return payload["query"]


def join_embedded_conflict_query(or_query: str, reserved_queries: list[str]) -> str | None:
    if not or_query or not reserved_queries:
        return None
    return " OR ".join(f"({part})" for part in [or_query, *reserved_queries])


def embed_reserved_retrieve_query(params: QueryParams, solr: NamexSolr, is_strict: bool, start: int) -> str | None:
    if not should_run_reserved_coverage(is_strict, start, params.query.get("value", "")):
        return None
    reserved = []
    if prefix_params := reserved_prefix_params(params):
        reserved.append(query_with_full_boosts(prefix_params, solr, True, True))
    if coverage_params := reserved_coverage_params(params):
        reserved.append(query_with_full_boosts(coverage_params, solr, True, True))
    if concat_query := reserved_glued_concat_query(params.query.get("value", ""), solr=solr):
        reserved.append(concat_query)
    return join_embedded_conflict_query(query_with_full_boosts(params, solr, True, False), reserved)


def _widened_prefix(lead: str) -> str:
    if len(lead) >= 5 and lead.endswith("Y"):  # noqa: PLR2004
        return f"{lead[:-1]}*"
    if len(lead) >= 5 and lead.endswith("IES"):  # noqa: PLR2004
        return f"{lead[:-3]}*"
    if len(lead) >= 5 and lead.endswith("S") and not lead.endswith("SS"):  # noqa: PLR2004
        return f"{lead[:-1]}*"
    return f"{lead}*"


def build_loose_first_query(distinctive: list[str]) -> str | None:
    groups = identity_term_groups(distinctive)
    if not groups or len(groups[0]) != 1:
        return None
    term = groups[0][0]
    fuzzy = QueryBuilder.get_fuzzy_str(term, 1, 2)
    if not fuzzy:
        return None
    return f"{NameField.NAME_Q.value}:{term}{fuzzy}"


def _name_splits(word: str) -> list[tuple[str, str]]:
    if len(word) < 6:  # noqa: PLR2004
        return []
    return [(word[:index], word[index:]) for index in range(3, len(word) - 2)]


def build_identity_descriptive_query(
    solr: NamexSolr,
    params: QueryParams,
    distinctive: list[str],
    descriptive: list[str],
) -> str | None:
    identity_groups = identity_term_groups(distinctive)
    if not identity_groups:
        return None
    builder = solr.query_builder
    stems_by_term = params.stemmed_terms_map or {}
    constant = frozenset(params.constant_score_terms or [])
    synonym_info: dict = {}

    def clauses(groups: list[list[str]], with_synonym: bool) -> list[str]:
        words = [group[0] for group in groups if len(group) == 1]
        stems = [stems_by_term.get(term, term) for term in words]
        built = []
        word_index = 0
        for group in groups:
            if len(group) >= 2:  # noqa: PLR2004
                glued = "".join(group)
                built.append(f"{NameField.NAME_Q_EXACT.value}:{glued} OR {NameField.NAME_Q.value}:{glued}")
                continue
            clause = builder.build_term_clause(
                group[0],
                params.query_fields,
                params.query_boost_fields,
                params.query_fuzzy_fields,
                True,
                constant,
            )
            if with_synonym:
                clause = builder.build_term_synonym_clauses(
                    clause,
                    words,
                    word_index,
                    synonym_info,
                    params.query_synonym_fields,
                    True,
                    params.query_boost_fields,
                    stems,
                    True,
                    constant,
                )
            built.append(clause)
            word_index += 1
        return built

    identity_clauses = clauses(identity_groups, False)
    if identity_groups and len(identity_groups[0]) == 1:
        term = identity_groups[0][0]
        lead = "".join(ch for ch in term.upper() if ch.isalnum())
        broad = identity_clauses[0]
        if lead:
            anchored = f"name:{_widened_prefix(lead)} AND ({broad})"
            whole = f"name:{lead}\\ *"
            pieces = [f"(({whole})^{IDENTITY_FULL_BOOST})", f"({anchored})"]
            pieces.extend(
                f"((name:{left}\\ {right}*)^{IDENTITY_FULL_BOOST})" for left, right in _name_splits(lead)
            )
            identity_clauses[0] = " OR ".join(pieces)
    first = f"({identity_clauses[0]})" if identity_clauses else ""
    later = " AND ".join(f"({clause})" for clause in identity_clauses[1:] if clause)
    identity = f"{first} AND {later}" if later else first
    if not identity:
        return None
    described_clauses = [clause for clause in clauses(identity_term_groups(descriptive), True) if clause]
    described = " OR ".join(f"({clause})" for clause in described_clauses)
    if len(described_clauses) > 1:
        described = f"({described})"
    if not later:
        if not described:
            single = f"({identity})"
            loose = build_loose_first_query(distinctive)
            return f"{single} OR ({loose})" if loose else single
        return f"({identity}) OR (({identity} AND {described})^{IDENTITY_DESCRIPTIVE_BOOST})"
    full_desc = IDENTITY_FULL_BOOST * IDENTITY_DESCRIPTIVE_BOOST
    parts = [f"({first})", f"(({first} AND {later})^{IDENTITY_FULL_BOOST})"]
    if described:
        parts.append(f"(({first} AND {described})^{IDENTITY_DESCRIPTIVE_BOOST})")
        parts.append(f"(({first} AND {later} AND {described})^{full_desc})")
    return " OR ".join(parts)


def _name_token(word: str) -> str:
    return "".join(ch for ch in word.upper() if ch.isalnum())


def _span_patterns(tokens: list[str]) -> list[str]:
    exact = [_name_token(token) for token in tokens]
    if any(not token for token in exact):
        return []
    spans = ["\\ ".join(exact)]
    loose = []
    widened = False
    for token in exact:
        if len(token) >= 5 and token.endswith("Y"):  # noqa: PLR2004
            loose.append(f"{token[:-1]}*")
            widened = True
        elif len(token) >= 5 and token.endswith("IES"):  # noqa: PLR2004
            loose.append(f"{token[:-3]}*")
            widened = True
        elif len(token) >= 5 and token.endswith("S") and not token.endswith("SS"):  # noqa: PLR2004
            loose.append(f"{token[:-1]}*")
            widened = True
        else:
            loose.append(token)
    if widened:
        spans.append("\\ ".join(loose))
    return spans


_GAP_TOKENS = ("&", "AND", "\\+")


def _span_clauses(span: str) -> list[str]:
    tail = "" if span.endswith("*") else "*"
    parts = span.split("\\ ")
    phrases = [span]
    if len(parts) > 1:
        for index in range(1, len(parts)):
            for gap in _GAP_TOKENS:
                phrases.append("\\ ".join([*parts[:index], gap, *parts[index:]]))
    return [f"(name:{phrase}{tail})" for phrase in phrases]


def pattern_search_terms(distinctive: list[str], descriptive: list[str]) -> list[str]:
    """Rearrange the distinctive and descriptive words when the search has two or more."""
    words = [word for word in (*distinctive, *descriptive) if word]
    if len(words) < 2:  # noqa: PLR2004
        return []
    return words


def _adjacent_pattern_clauses(words: list[str]) -> list[str]:
    cleaned = [word for word in words if word]
    if len(cleaned) < 2 or len(cleaned) > _MAX_PATTERN_WORDS:  # noqa: PLR2004
        return []
    typed = tuple(word.lower() for word in cleaned)
    clauses = []
    for perm in permutations(cleaned):
        if tuple(word.lower() for word in perm) == typed:
            continue
        for span in _span_patterns(perm):
            clauses.extend(_span_clauses(span))
    return clauses


def build_adjacent_pattern_query(words: list[str]) -> str | None:
    """Find the same words side by side in an order other than the typed order."""
    clauses = _adjacent_pattern_clauses(words)
    if not clauses:
        return None
    return " OR ".join(clauses)


def adjacent_pattern_queries(words: list[str]) -> list[str]:
    clauses = _adjacent_pattern_clauses(words)
    if not clauses:
        return []
    return [
        " OR ".join(clauses[start : start + _PATTERN_CLAUSE_BUDGET])
        for start in range(0, len(clauses), _PATTERN_CLAUSE_BUDGET)
    ]


def apply_embedded_reserved_retrieve(params: QueryParams, solr: NamexSolr, is_strict: bool, start: int) -> QueryParams:
    if fat_query := embed_reserved_retrieve_query(params, solr, is_strict, start):
        return replace(params, override_query=fat_query)
    return params


def namex_search(params: QueryParams, solr: NamexSolr, is_name_search: bool, is_strict: bool = True):
    """Return the list of possible conflicts from Solr that match the query."""
    initial_queries = build_namex_query_payload(
        params,
        solr,
        is_name_search,
        is_strict,
        clause_bridge="AND" if is_strict else "OR",
    )
    for info in params.full_query_boosts:
        initial_queries["query"] += f" OR {format_full_query_boost(info)}"
    built_query = initial_queries["query"]
    if override_query := getattr(params, "override_query", None):
        initial_queries["query"] = override_query

    highlight_query = built_query if params.highlighted_fields else None

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
    if child_query := solr.query_builder.build_child_query(params.child_query, is_name_search, True):
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
