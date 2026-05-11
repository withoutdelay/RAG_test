from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from app.services.domain.taxonomy_registry import get_synonym_registry


@lru_cache(maxsize=1024)
def expand_domain_term(term: str) -> tuple[str, ...]:
    return get_synonym_registry().expand_term(term)


def expand_domain_terms(terms: Iterable[str]) -> list[str]:
    return get_synonym_registry().expand_terms(str(term or "") for term in terms)


def extract_domain_terms(text: str) -> list[str]:
    return get_synonym_registry().extract_terms(text)


def text_contains_domain_term(text: str, term: str) -> bool:
    return get_synonym_registry().contains(text, term)
