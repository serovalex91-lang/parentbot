import os
import re
import zipfile
from typing import List
from xml.etree import ElementTree as ET


CHUNK_SIZE_CHARS = 2000   # ~500 токенов для русского текста
CHUNK_OVERLAP_CHARS = 200


def extract_and_chunk(book_path: str) -> List[str]:
    """Извлечь текст из книги и разбить на чанки. Поддерживает PDF, EPUB, FB2, FB2.ZIP."""
    text = _extract_text(book_path)
    if not text.strip():
        return []
    return _chunk_text(text)


def _extract_text(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".pdf"):
        raw = _extract_pdf(path)
    elif lower.endswith(".epub"):
        raw = _extract_epub(path)
    elif lower.endswith(".fb2.zip") or lower.endswith(".zip"):
        raw = _extract_fb2_zip(path)
    elif lower.endswith(".fb2"):
        raw = _extract_fb2(path)
    else:
        raise ValueError(f"Unsupported book format: {os.path.basename(path)}")
    return _clean_text(raw)


def _extract_pdf(pdf_path: str) -> str:
    import fitz  # lazy import
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages)


def _extract_epub(epub_path: str) -> str:
    """Достать текст из EPUB — это zip с XHTML файлами."""
    parts = []
    with zipfile.ZipFile(epub_path) as z:
        # Берём только основные текстовые файлы, не nav/toc
        for name in z.namelist():
            low = name.lower()
            if not (low.endswith(".xhtml") or low.endswith(".html") or low.endswith(".htm")):
                continue
            if "nav" in low or "toc" in low or "title" in low:
                continue
            try:
                html = z.read(name).decode("utf-8", errors="ignore")
            except Exception:
                continue
            parts.append(_strip_html(html))
    return "\n\n".join(p for p in parts if p.strip())


def _extract_fb2(fb2_path: str) -> str:
    with open(fb2_path, "rb") as f:
        data = f.read()
    return _parse_fb2_bytes(data)


def _extract_fb2_zip(zip_path: str) -> str:
    """FB2 часто упакован в zip — внутри один .fb2 файл."""
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name.lower().endswith(".fb2"):
                return _parse_fb2_bytes(z.read(name))
    return ""


def _parse_fb2_bytes(data: bytes) -> str:
    """Парсим FB2 как XML, извлекаем текст из <body>."""
    try:
        # FB2 это XML — может быть с BOM или странным objc namespace
        root = ET.fromstring(data)
    except ET.ParseError:
        # Пробуем разобрать как HTML-подобное
        return _strip_html(data.decode("utf-8", errors="ignore"))
    parts = []
    # Все <body>: основной + примечания
    for body in root.iter():
        if not body.tag.endswith("body"):
            continue
        # Извлекаем весь текст рекурсивно
        text = " ".join(body.itertext())
        parts.append(text)
    return "\n\n".join(parts)


def _strip_html(html: str) -> str:
    # Уберём script/style, потом теги
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    # HTML entities
    html = (html.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return html


def _clean_text(text: str) -> str:
    # Убрать множественные пробелы и пустые строки
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chunk_text(text: str, size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> List[str]:
    """Разбить текст на перекрывающиеся чанки по границам предложений."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = []
    current_len = 0

    for sentence in sentences:
        sent_len = len(sentence)
        if current_len + sent_len > size and current:
            chunk = " ".join(current)
            chunks.append(chunk)
            overlap_text = chunk[-overlap:] if len(chunk) > overlap else chunk
            current = [overlap_text]
            current_len = len(overlap_text)
        current.append(sentence)
        current_len += sent_len + 1

    if current:
        chunks.append(" ".join(current))

    return [c.strip() for c in chunks if len(c.strip()) > 50]
