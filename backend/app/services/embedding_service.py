"""
Multilingual embedding service using sentence-transformers.
Model is lazy-loaded on first call and cached in process memory.
EN/ES content shares one vector space — key requirement from spec.
"""
from __future__ import annotations
from functools import lru_cache
from app.core.config import settings

# multilingual-E5 expects a "query: " or "passage: " prefix per the model card
_PASSAGE_PREFIX = "passage: "
_QUERY_PREFIX = "query: "


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings.EMBEDDING_MODEL)


def embed_passage(text: str) -> list[float]:
    return _model().encode(_PASSAGE_PREFIX + text, normalize_embeddings=True).tolist()


def embed_query(text: str) -> list[float]:
    return _model().encode(_QUERY_PREFIX + text, normalize_embeddings=True).tolist()


def embed_passages_batch(texts: list[str]) -> list[list[float]]:
    prefixed = [_PASSAGE_PREFIX + t for t in texts]
    return _model().encode(prefixed, normalize_embeddings=True, batch_size=32).tolist()
