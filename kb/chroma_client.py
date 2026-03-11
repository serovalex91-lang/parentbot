import uuid
from typing import List, Dict, Any, Optional
import chromadb
from loguru import logger

_client: Optional[chromadb.PersistentClient] = None


def init_chroma(chroma_dir: str):
    global _client
    _client = chromadb.PersistentClient(path=chroma_dir)
    logger.info("ChromaDB инициализирован: {}", chroma_dir)


def get_client() -> chromadb.PersistentClient:
    if _client is None:
        raise RuntimeError("ChromaDB не инициализирован. Вызови init_chroma() при старте.")
    return _client


def _collection_name(scope: str, user_id: Optional[int] = None) -> str:
    if scope == "shared":
        return "shared_kb"
    return f"user_{user_id}"


def add_chunks(
    scope: str,
    user_id: Optional[int],
    chunks: List[str],
    embeddings: List[List[float]],
    book_id: int,
    age_min: int,
    age_max: int,
) -> List[str]:
    """Добавить чанки в коллекцию. Возвращает список ID."""
    collection = get_client().get_or_create_collection(
        name=_collection_name(scope, user_id),
        metadata={"hnsw:space": "cosine"},
    )
    ids = [str(uuid.uuid4()) for _ in chunks]
    metadatas = [
        {
            "book_id": book_id,
            "age_min": age_min,
            "age_max": age_max,
            "scope": scope,
            "chunk_index": i,
        }
        for i in range(len(chunks))
    ]
    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=chunks,
        metadatas=metadatas,
    )
    return ids


def query_collection(
    scope: str,
    user_id: Optional[int],
    query_embedding: List[float],
    n_results: int = 10,
    age_months: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Запросить коллекцию. Возвращает список {document, metadata, distance}."""
    try:
        collection = get_client().get_or_create_collection(
            name=_collection_name(scope, user_id),
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as e:
        logger.warning("Ошибка получения коллекции {}: {}", scope, e)
        return []

    # Сколько всего документов в коллекции
    count = collection.count()
    if count == 0:
        return []

    # Запрашиваем больше чем нужно для post-hoc фильтрации
    n_fetch = min(n_results * 3, count)

    where_filter = None
    if age_months is not None:
        where_filter = {
            "$and": [
                {"age_min": {"$lte": age_months}},
                {"age_max": {"$gte": age_months}},
            ]
        }

    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_fetch,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        logger.warning("Ошибка запроса ChromaDB: {}", e)
        return []

    items = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for doc, meta, dist in zip(docs, metas, distances):
        items.append({"document": doc, "metadata": meta, "distance": dist})

    return items


def delete_chunks(scope: str, user_id: Optional[int], chunk_ids: List[str]):
    """Удалить чанки по ID."""
    try:
        collection = get_client().get_or_create_collection(
            name=_collection_name(scope, user_id)
        )
        collection.delete(ids=chunk_ids)
    except Exception as e:
        logger.error("Ошибка удаления чанков из ChromaDB: {}", e)
        raise
