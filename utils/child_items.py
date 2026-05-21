"""Универсальная работа с записями child_context (features/character/notes).

Новый формат: список объектов
    {"id": str, "text": str, "ts": iso str, "age_at_save": int|None, "age_relevance": str|None}

Старый формат: строка "запись1; запись2; запись3"

Все helper-функции читают оба формата (для безопасности после миграции),
но всегда записывают в новый формат.
"""
import re
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional


ITEMIZED_FIELDS = ("child_features", "child_character", "child_notes")
SCHEMA_VERSION = 2


def _new_item(text: str, age_at_save: Optional[int] = None,
              age_relevance: Optional[str] = None,
              ts: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "text": text.strip(),
        "ts": ts or datetime.utcnow().isoformat(),
        "age_at_save": age_at_save,
        "age_relevance": age_relevance,
    }


def items_of(field_value: Any, default_ts: Optional[str] = None) -> List[Dict[str, Any]]:
    """Возвращает список записей в новом формате.
    Принимает str (старый формат), list (новый) или None."""
    if not field_value:
        return []
    if isinstance(field_value, list):
        return [_normalize_item(it, default_ts) for it in field_value if it]
    if isinstance(field_value, str):
        return [_new_item(p.strip(), ts=default_ts)
                for p in re.split(r"[;\n]+", field_value)
                if p.strip()]
    return []


def _normalize_item(item: Any, default_ts: Optional[str] = None) -> Dict[str, Any]:
    """Защита от мусора в списке: гарантирует все обязательные поля."""
    if isinstance(item, str):
        return _new_item(item, ts=default_ts)
    if not isinstance(item, dict):
        return _new_item(str(item), ts=default_ts)
    text = (item.get("text") or "").strip()
    if not text:
        return _new_item("", ts=default_ts)
    return {
        "id": item.get("id") or str(uuid.uuid4()),
        "text": text,
        "ts": item.get("ts") or default_ts or datetime.utcnow().isoformat(),
        "age_at_save": item.get("age_at_save"),
        "age_relevance": item.get("age_relevance"),
    }


def add_item(items: List[Dict[str, Any]], text: str,
             age_at_save: Optional[int] = None,
             age_relevance: Optional[str] = None) -> List[Dict[str, Any]]:
    """Добавляет новую запись в конец списка."""
    text = text.strip()
    if not text:
        return items
    return list(items) + [_new_item(text, age_at_save, age_relevance)]


def remove_item(items: List[Dict[str, Any]], item_id: str) -> List[Dict[str, Any]]:
    return [it for it in items if it.get("id") != item_id]


def update_item(items: List[Dict[str, Any]], item_id: str,
                new_text: str, age_at_save: Optional[int] = None) -> List[Dict[str, Any]]:
    """Заменяет text у записи с указанным id, обновляет ts."""
    new_text = new_text.strip()
    if not new_text:
        return remove_item(items, item_id)
    result = []
    for it in items:
        if it.get("id") == item_id:
            updated = dict(it)
            updated["text"] = new_text
            updated["ts"] = datetime.utcnow().isoformat()
            if age_at_save is not None:
                updated["age_at_save"] = age_at_save
            result.append(updated)
        else:
            result.append(it)
    return result


def touch_item(items: List[Dict[str, Any]], item_id: str) -> List[Dict[str, Any]]:
    """Обновляет ts записи без изменения текста (review:keep)."""
    now = datetime.utcnow().isoformat()
    result = []
    for it in items:
        if it.get("id") == item_id:
            updated = dict(it)
            updated["ts"] = now
            result.append(updated)
        else:
            result.append(it)
    return result


def get_item(items: List[Dict[str, Any]], item_id: str) -> Optional[Dict[str, Any]]:
    for it in items:
        if it.get("id") == item_id:
            return it
    return None


def resolve_id(items: List[Dict[str, Any]], short_or_full_id: str) -> Optional[str]:
    """Если передан полный id — вернёт его. Если префикс (8+ символов) —
    найдёт совпадающий полный id. None если не нашёл или коллизия."""
    if not short_or_full_id:
        return None
    matches = [it.get("id", "") for it in items
               if it.get("id", "").startswith(short_or_full_id)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1 and short_or_full_id in matches:
        return short_or_full_id
    return None


def items_to_text_list(items: List[Dict[str, Any]]) -> List[str]:
    """Извлекает текст всех записей. Для отображения."""
    return [it["text"] for it in items if it.get("text")]


def items_to_legacy_string(items: List[Dict[str, Any]]) -> str:
    """Для совместимости со старым кодом: 'a; b; c'."""
    return "; ".join(items_to_text_list(items))
