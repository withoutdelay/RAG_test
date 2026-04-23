"""Domain knowledge helpers."""

from .synonyms import expand_domain_term, expand_domain_terms, extract_domain_terms, text_contains_domain_term
from .term_lexicon import build_corpus_term_lexicon, expand_terms_with_lexicon, extract_terms_from_lexicon

__all__ = [
    "build_corpus_term_lexicon",
    "expand_domain_term",
    "expand_domain_terms",
    "expand_terms_with_lexicon",
    "extract_domain_terms",
    "extract_terms_from_lexicon",
    "text_contains_domain_term",
]
