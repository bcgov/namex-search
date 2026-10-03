"""Manages common solr synonym payload build methods."""
import re

from namex_solr_api.models import SolrSynonymList
from namex_solr_api.services.base_solr.utils.query_builder import SYNONYM_SKIP_WORDS

_NAME_SURFACE_TOKEN = re.compile(r"[A-Za-z0-9']+")


def _get_synonyms(synonym_type: SolrSynonymList.Type) -> dict[str, list[str]]:
    """Return all synonyms in the db for the given type as a dictionary."""
    return {
        x.synonym: x.synonym_list for x in SolrSynonymList.find_all_by_synonym_type(synonym_type)
    }


def get_synonyms() -> dict[SolrSynonymList.Type, dict[str, list[str]]]:
    """Return all synonyms used for SOLR queries."""
    return {SolrSynonymList.Type.ALL: _get_synonyms(SolrSynonymList.Type.ALL)}


def retrieve_synonym_families_by_term(
    query_value: str,
    query_builder,
    synonym_field,
    stemmed_terms: list[str] | None = None,
    browse_families: dict[str, list[str]] | None = None,
) -> dict[str, set[str]]:
    terms = (query_value or "").split()
    if not terms:
        return {}
    stemmed_terms = stemmed_terms or terms
    browse = None if browse_families is None else {key.lower(): values for key, values in browse_families.items()}
    synonym_type = query_builder.synonym_field_map[synonym_field]
    families: dict[str, set[str]] = {}
    for index, term in enumerate(terms):
        allowed: set[str] = set()
        if browse is not None:
            for member in browse.get(term.lower(), []):
                _add_family_surfaces(allowed, member)
            families[term] = allowed
            continue
        key_terms = query_builder.find_synonym_terms(
            term, index, terms, synonym_field, stemmed_terms
        )
        if key_terms:
            key = " ".join(key_terms)
            _add_family_surfaces(allowed, key)
            if row := SolrSynonymList.find_by_synonym(key, synonym_type):
                for member in row.synonym_list or []:
                    _add_family_surfaces(allowed, member)
        families[term] = allowed
    return families


def _add_family_surfaces(allowed: set[str], text: str) -> None:
    if not text or not (lower := text.lower().strip()):
        return
    if lower not in SYNONYM_SKIP_WORDS:
        allowed.add(lower)


def name_surface_tokens(name: str) -> list[str]:
    return _NAME_SURFACE_TOKEN.findall(name or "")


def candidate_synonym_highlight_tokens(solr_tokens: list[str], name: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for token in [*solr_tokens, *name_surface_tokens(name)]:
        for part in re.sub(r"<[^>]+>", "", token).upper().split():
            if part and part not in seen:
                seen.add(part)
                candidates.append(part)
    return candidates


def _analyzed_stem_in_family(stem: str, family: set[str], stems: set[str]) -> bool:
    if stem in family or stem in stems:
        return True
    if len(stem) > 1 and stem.endswith("e"):
        base = stem[:-1]
        return base in family or base in stems
    return False


def keep_family_synonym_highlights(
    tokens: list[str],
    family_tokens: set[str],
    family_stems: set[str] | None = None,
    token_stems: dict[str, list[str]] | None = None,
) -> list[str]:
    family = {token.lower() for token in family_tokens}
    stems = {stem.lower() for stem in (family_stems or set())}
    token_stems = token_stems or {}
    kept: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        upper = token.upper()
        if not upper or upper in seen:
            continue
        lower = token.lower()
        if lower in SYNONYM_SKIP_WORDS:
            continue
        if lower in family:
            seen.add(upper)
            kept.append(upper)
            continue
        analyzed = [stem.lower() for stem in token_stems.get(lower, [])]
        if any(_analyzed_stem_in_family(stem, family, stems) for stem in analyzed):
            seen.add(upper)
            kept.append(upper)
    return kept


def _phrase_parts(family_tokens: set[str]):
    for member in family_tokens:
        parts = [part for part in (member or "").lower().split() if part]
        if len(parts) >= 2:  # noqa: PLR2004
            yield parts


def phrase_member_word_tokens(tokens: list[str], family_tokens: set[str]) -> list[str]:
    if not tokens:
        return []
    words = {part for parts in _phrase_parts(family_tokens) for part in parts}
    kept: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        lower = token.lower()
        if lower not in words or lower in SYNONYM_SKIP_WORDS:
            continue
        upper = token.upper()
        if upper not in seen:
            seen.add(upper)
            kept.append(upper)
    return kept


def phrase_synonym_tokens(tokens: list[str], family_tokens: set[str]) -> list[str]:
    if not tokens:
        return []
    lowered = [token.lower() for token in tokens]
    kept: list[str] = []
    seen: set[str] = set()
    for parts in _phrase_parts(family_tokens):
        width = len(parts)
        for index in range(len(lowered) - width + 1):
            if lowered[index : index + width] != parts:
                continue
            for token in tokens[index : index + width]:
                upper = token.upper()
                if upper not in seen:
                    seen.add(upper)
                    kept.append(upper)
    return kept
