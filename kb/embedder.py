from typing import List
from loguru import logger

_model = None
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


def get_model():
    global _model
    if _model is None:
        logger.info("Загрузка модели эмбеддингов {}...", MODEL_NAME)
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("Модель загружена")
    return _model


def warmup():
    """Предзагрузить модель при старте чтобы первый запрос не тормозил."""
    get_model()


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Получить эмбеддинги для списка текстов."""
    model = get_model()
    embeddings = model.encode(texts, batch_size=32, show_progress_bar=False)
    return embeddings.tolist()


def embed_query(text: str) -> List[float]:
    """Получить эмбеддинг для одного запроса."""
    return embed_texts([text])[0]
