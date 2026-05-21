"""Формирование списка книг для system prompt и детекция упоминания книги в запросе."""
import re
from typing import List, Dict, Any, Optional

_NUM_MAP = {
    r"\b30\b": "тридцать",
    r"\b1000000\b": "миллионов",
    r"\b1\s*000\s*000\b": "миллионов",
    r"\bмлн\b": "миллионов",
}


def _normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    for pat, repl in _NUM_MAP.items():
        text = re.sub(pat, repl, text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_filename(original_name: str) -> str:
    name = re.sub(r"\.(epub|fb2|pdf|zip)$", "", original_name, flags=re.I)
    return name.strip()


def _split_title_author(name: str) -> tuple[str, str]:
    """Делит 'Название — Автор' на (title, author). Если нет — author пустой."""
    parts = re.split(r"\s+[—–-]\s+", name, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return name.strip(), ""


def format_book_list(books: List[Dict[str, Any]]) -> str:
    """Возвращает форматированный список книг для system prompt."""
    if not books:
        return ""
    lines = []
    for b in books:
        title, author = _split_title_author(_clean_filename(b["original_name"]))
        age_min = b.get("age_range_min", 0)
        age_max = b.get("age_range_max", 216)
        age_str = _format_age_range(age_min, age_max)
        if author:
            lines.append(f"• «{title}» — {author} ({age_str})")
        else:
            lines.append(f"• «{title}» ({age_str})")
    return "\n".join(lines)


def _format_age_range(months_min: int, months_max: int) -> str:
    def fmt(m: int) -> str:
        if m < 12:
            return f"{m} мес"
        years = m // 12
        return f"{years} лет"
    return f"возраст: {fmt(months_min)}–{fmt(months_max)}"


_STOP_WORDS = {
    "книга", "книгу", "книги", "книгой", "книге",
    "автор", "автора", "автору", "авторе",
    "глава", "главы", "главе",
    "ребенок", "ребенка", "ребенку", "ребенке", "ребенком",
    "родитель", "родителя", "родителю", "родителей", "родителям",
    "расскажи", "расскажешь", "скажи", "поясни", "опиши", "почитай",
    "интернет", "интересно",
    "которая", "которой", "которую", "которое", "который",
    "развитие", "развития", "развитию",
    "что", "это", "как", "про", "для", "его", "ее", "мне",
    "также", "тоже", "тебе", "тебя", "тебя", "тебя",
    "хочу", "хочешь", "хочется",
    "добавил", "добавила", "добавили",
    "понять", "понимать", "понимаю",
}


def _prefix(word: str, n: int = 5) -> str:
    return word[:n]


def _prefix_tokens(text: str, n: int = 5) -> List[str]:
    """Префиксы слов длиной >= 5 (без stop-words)."""
    tokens = []
    for w in _normalize(text).split():
        if len(w) < 5:
            continue
        if w in _STOP_WORDS:
            continue
        tokens.append(_prefix(w, n))
    return tokens


def detect_book_mention(query: str, books: List[Dict[str, Any]]) -> Optional[int]:
    """Если в запросе упомянуто название или автор книги — возвращает book_id.

    Алгоритм:
    1. Точный substring match нормализованного названия/автора (≥6 символов) — highest priority.
    2. Иначе — пересечение префиксов слов (5 первых букв, без стоп-слов). Порог:
       - совпало 2+ префиксов из названия, ИЛИ
       - совпал 1 префикс из автора длиной точно 5+ символов (фамилия).

    При нескольких совпадениях выбирается с наибольшим скором.
    """
    norm_q = _normalize(query)
    q_pref = set(_prefix_tokens(query))

    best_id = None
    best_score = 0.0

    for b in books:
        name = _clean_filename(b["original_name"])
        title, author = _split_title_author(name)

        score = 0.0

        # 1) Точный substring (нормализованный)
        for candidate in (title, author):
            if candidate and len(candidate) >= 6:
                norm_c = _normalize(candidate)
                if norm_c and norm_c in norm_q:
                    score = max(score, len(norm_c) + 100)

        # 2) Префиксный матч по названию (нужно >=2 совпадения)
        title_pref = set(_prefix_tokens(title))
        title_overlap = title_pref & q_pref
        if len(title_overlap) >= 2:
            score = max(score, 30 + len(title_overlap) * 5)

        # 3) Префиксный матч по автору (достаточно 1 — фамилия)
        author_pref = set(_prefix_tokens(author))
        author_overlap = author_pref & q_pref
        if len(author_overlap) >= 1:
            score = max(score, 20 + len(author_overlap) * 5)

        # 4) Одно совпадение из названия — только если название из одного значимого слова
        if not title_overlap and not author_overlap:
            continue
        if score == 0 and len(title_pref) == 1 and len(title_overlap) == 1:
            score = 15

        if score > best_score:
            best_score = score
            best_id = b["id"]

    return best_id
