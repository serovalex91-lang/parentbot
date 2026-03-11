import re
from typing import List
import fitz  # PyMuPDF


CHUNK_SIZE_CHARS = 2000   # ~500 токенов для русского текста
CHUNK_OVERLAP_CHARS = 200


def extract_and_chunk(pdf_path: str) -> List[str]:
    """Извлечь текст из PDF и разбить на чанки."""
    text = _extract_text(pdf_path)
    if not text.strip():
        return []
    return _chunk_text(text)


def _extract_text(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    raw = "\n".join(pages)
    return _clean_text(raw)


def _clean_text(text: str) -> str:
    # Убрать множественные пробелы и пустые строки
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chunk_text(text: str, size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> List[str]:
    """Разбить текст на перекрывающиеся чанки по границам предложений."""
    # Разбить на предложения
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = []
    current_len = 0

    for sentence in sentences:
        sent_len = len(sentence)
        if current_len + sent_len > size and current:
            chunk = " ".join(current)
            chunks.append(chunk)
            # Overlap: оставить последние ~overlap символов
            overlap_text = chunk[-overlap:] if len(chunk) > overlap else chunk
            current = [overlap_text]
            current_len = len(overlap_text)
        current.append(sentence)
        current_len += sent_len + 1

    if current:
        chunks.append(" ".join(current))

    return [c.strip() for c in chunks if len(c.strip()) > 50]
