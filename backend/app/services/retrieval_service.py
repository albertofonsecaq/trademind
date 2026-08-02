"""
Hybrid retrieval: pgvector cosine + PostgreSQL full-text search, fused via RRF.
RRF score: 1/(k + rank) where k=60 (standard).
"""
from __future__ import annotations
import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RRF_K = 60
TOP_N = 20


async def hybrid_search(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    query_embedding: list[float],
    query_text: str,
    limit: int = TOP_N,
) -> list[dict]:
    """
    Returns list of dicts with: source_id, text, score, metadata.
    """
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    sql = text("""
        WITH vector_ranked AS (
            SELECT
                source_id, text, embedding_metadata,
                ROW_NUMBER() OVER (ORDER BY embedding <=> :embedding::vector) AS rank
            FROM embedding_rows
            WHERE workspace_id = :workspace_id
            ORDER BY embedding <=> :embedding::vector
            LIMIT 50
        ),
        fts_ranked AS (
            SELECT
                source_id, text, embedding_metadata,
                ROW_NUMBER() OVER (ORDER BY ts_rank(to_tsvector('english', text), query) DESC) AS rank
            FROM embedding_rows,
                 plainto_tsquery('english', :query_text) query
            WHERE workspace_id = :workspace_id
              AND to_tsvector('english', text) @@ query
            ORDER BY ts_rank(to_tsvector('english', text), query) DESC
            LIMIT 50
        ),
        rrf AS (
            SELECT
                COALESCE(v.source_id, f.source_id) AS source_id,
                COALESCE(v.text, f.text) AS text,
                COALESCE(v.embedding_metadata, f.embedding_metadata) AS metadata,
                COALESCE(1.0 / (:rrf_k + v.rank), 0) + COALESCE(1.0 / (:rrf_k + f.rank), 0) AS rrf_score
            FROM vector_ranked v
            FULL OUTER JOIN fts_ranked f ON v.source_id = f.source_id
        )
        SELECT source_id, text, metadata, rrf_score
        FROM rrf
        ORDER BY rrf_score DESC
        LIMIT :limit
    """)

    result = await db.execute(
        sql,
        {
            "embedding": embedding_str,
            "workspace_id": str(workspace_id),
            "query_text": query_text,
            "rrf_k": RRF_K,
            "limit": limit,
        },
    )
    rows = result.mappings().all()
    return [
        {
            "source_id": r["source_id"],
            "text": r["text"],
            "score": float(r["rrf_score"]),
            "metadata": r["metadata"] or {},
        }
        for r in rows
    ]
