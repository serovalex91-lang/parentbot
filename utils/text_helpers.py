from typing import List

MAX_MESSAGE_LENGTH = 4096


def split_long_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> List[str]:
    """Разбить длинный текст на части для Telegram."""
    if len(text) <= max_len:
        return [text]

    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        # Обрезать по последнему переносу строки
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts
