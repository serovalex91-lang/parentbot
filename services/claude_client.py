import json
from typing import List, Dict, Optional
import anthropic
from loguru import logger

from config import Config

_client: Optional[anthropic.AsyncAnthropic] = None


def init_claude(api_key: str):
    global _client
    _client = anthropic.AsyncAnthropic(api_key=api_key)


def get_client() -> anthropic.AsyncAnthropic:
    if _client is None:
        raise RuntimeError("Claude client не инициализирован. Вызови init_claude() при старте.")
    return _client


def _build_system_prompt(
    role: str,
    age_display: str,
    age_context: str,
    kb_chunks: str,
    child_context: str = "",
    brave_results: str = "",
) -> str:
    role_map = {
        "papa": "папе",
        "mama": "маме",
        "both": "обоим родителям",
    }
    role_text = role_map.get(role, "родителю")

    role_instructions = {
        "papa": (
            "Стиль общения: аналитический, поддерживающий, чёткий. "
            "Фокус на концепции «надёжной базы», активных играх, установлении здоровых границ."
        ),
        "mama": (
            "Стиль общения: тёплый, эмпатичный. "
            "Акцент на самоподдержке, профилактике эмоционального выгорания."
        ),
        "both": (
            "Помогай выработать единую родительскую стратегию, "
            "учитывая разные стили и потребности обоих партнёров."
        ),
    }.get(role, "")

    child_context_block = ""
    if child_context:
        child_context_block = f"\nЛичные данные о ребёнке:\n{child_context}\n"

    kb_block = ""
    if kb_chunks:
        kb_block = f"\nКОНТЕКСТ ИЗ БАЗЫ ЗНАНИЙ:\n{kb_chunks}\n"
    else:
        kb_block = "\n[База знаний не содержит релевантных фрагментов для этого запроса.]\n"

    internet_block = ""
    if brave_results:
        internet_block = (
            f"\nИСТОЧНИКИ ИЗ ИНТЕРНЕТА (прошли фактчекинг на соответствие гуманной педагогике):\n"
            f"{brave_results}\n"
        )

    return f"""Ты — эксперт-консультант по осознанному родительству и детскому развитию.
Твоя миссия — помогать {role_text} растить ребёнка, опираясь на доказательную медицину, теорию привязанности и нейробиологию.

Текущий возраст ребёнка: {age_display} — {age_context}.
{child_context_block}
{role_instructions}

Жёсткие ограничения:
- Отвечай ТОЛЬКО на русском языке.
- Запрет на физические наказания, крики, стыжение, манипуляции.
- Приоритет — загруженная библиотека. Если ответа в базе нет — скажи об этом прямо.
- Не давай медицинских диагнозов.
- Внешние данные только после фактчекинга на соответствие гуманной педагогике.

Структура ответа: начинай с объяснения что чувствует ребёнок и почему (с точки зрения развития мозга). Завершай конкретным маленьким шагом.
{kb_block}{internet_block}"""


async def ask_claude(
    config: Config,
    role: str,
    age_display: str,
    age_context: str,
    kb_chunks: str,
    history: List[Dict[str, str]],
    user_message: str,
    child_context: str = "",
    brave_results: str = "",
) -> str:
    client = get_client()

    system_prompt = _build_system_prompt(
        role=role,
        age_display=age_display,
        age_context=age_context,
        kb_chunks=kb_chunks,
        child_context=child_context,
        brave_results=brave_results,
    )

    # Собрать messages из истории + текущий вопрос
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": user_message})

    try:
        response = await client.messages.create(
            model=config.claude_model,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text
    except Exception as e:
        logger.error("Ошибка Claude API: {}", e)
        raise
