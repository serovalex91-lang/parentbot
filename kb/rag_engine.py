import asyncio
from typing import List, Dict, Any, Optional
from loguru import logger

from kb.embedder import embed_query
from kb.chroma_client import query_collection


def _search_kb_sync(
    user_id: int,
    query: str,
    age_months: Optional[int] = None,
    excluded_book_ids: Optional[List[int]] = None,
    n_results: int = 10,
) -> List[Dict[str, Any]]:
    """
    Синхронный поиск по shared_kb и личной коллекции user_id.
    Применяет пост-фильтрацию по excluded_book_ids.
    """
    if excluded_book_ids is None:
        excluded_book_ids = []

    query_embedding = embed_query(query)

    shared_results = query_collection(
        scope="shared",
        user_id=None,
        query_embedding=query_embedding,
        n_results=n_results * 2,
        age_months=age_months,
    )
    personal_results = query_collection(
        scope="personal",
        user_id=user_id,
        query_embedding=query_embedding,
        n_results=n_results,
        age_months=age_months,
    )

    filtered_shared = [
        item for item in shared_results
        if item["metadata"].get("book_id") not in excluded_book_ids
    ]

    combined = filtered_shared + personal_results
    combined.sort(key=lambda x: x["distance"])

    top = combined[:n_results]
    logger.debug(
        "RAG: query='{}', age_months={}, shared={}/{}, personal={}, top={}",
        query[:50],
        age_months,
        len(filtered_shared),
        len(shared_results),
        len(personal_results),
        len(top),
    )
    return top


async def search_kb(
    user_id: int,
    query: str,
    age_months: Optional[int] = None,
    excluded_book_ids: Optional[List[int]] = None,
    n_results: int = 10,
) -> List[Dict[str, Any]]:
    """Async обёртка — выносит CPU-bound embed + ChromaDB I/O в отдельный поток."""
    return await asyncio.to_thread(
        _search_kb_sync,
        user_id=user_id,
        query=query,
        age_months=age_months,
        excluded_book_ids=excluded_book_ids,
        n_results=n_results,
    )


def format_chunks_for_prompt(chunks: List[Dict[str, Any]]) -> str:
    """Форматирует чанки в текст для вставки в промт."""
    if not chunks:
        return ""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(f"[{i}] {chunk['document']}")
    return "\n\n".join(parts)
