from typing import List, Dict, Optional
import anthropic
from loguru import logger

from config import Config

_client: Optional[anthropic.AsyncAnthropic] = None

_STYLE_MAP = {
    "gentle": (
        "Максимально мягко, с сопереживанием и поддержкой. "
        "Никаких назиданий. Валидация чувств. "
        "«Я понимаю, как тебе сейчас трудно...» Тёплый, безоценочный тон."
    ),
    "balanced": (
        "Сбалансированно — поддержка + конкретика. "
        "Сначала валидация чувств, потом практический совет. "
        "Дружелюбный, но информативный тон."
    ),
    "structured": (
        "Чётко, структурированно, с акцентом на границах. "
        "Конкретные шаги, ясные формулировки. Меньше эмоций, больше стратегии. "
        "«Вот что можно сделать: 1, 2, 3.»"
    ),
}


def init_claude(api_key: str):
    global _client
    _client = anthropic.AsyncAnthropic(api_key=api_key)


def get_client() -> anthropic.AsyncAnthropic:
    if _client is None:
        raise RuntimeError("Claude client не инициализирован. Вызови init_claude() при старте.")
    return _client


def _resolve_style(value: str) -> str:
    if not value:
        return ""
    if value in _STYLE_MAP:
        return _STYLE_MAP[value]
    return value  # пользовательский текст


def _build_system_prompt(
    role: str,
    age_display: str,
    age_context: str,
    kb_chunks: str,
    child_context: str = "",
    brave_results: str = "",
    my_style: str = "",
    partner_style: str = "",
) -> str:
    role_map = {
        "papa": "папе",
        "mama": "маме",
        "both": "обоим родителям",
    }
    role_text = role_map.get(role, "родителю")

    role_instructions = {
        "papa": (
            "Базовый стиль: аналитический, поддерживающий, чёткий. "
            "Фокус на концепции «надёжной базы», активных играх, установлении здоровых границ."
        ),
        "mama": (
            "Базовый стиль: тёплый, эмпатичный. "
            "Акцент на самоподдержке, профилактике эмоционального выгорания."
        ),
        "both": (
            "Помогай выработать единую родительскую стратегию, "
            "учитывая разные стили и потребности обоих партнёров."
        ),
    }.get(role, "")

    # Стиль для пользователя (как бот общается С НИМ)
    my_style_resolved = _resolve_style(my_style)
    my_style_block = ""
    if my_style_resolved:
        my_style_block = (
            f"\nСТИЛЬ ОБЩЕНИЯ С РОДИТЕЛЕМ (как ты говоришь с ним/ней): "
            f"{my_style_resolved}\n"
        )

    # Стиль для партнёра (как советовать общаться с партнёром)
    partner_style_resolved = _resolve_style(partner_style)
    partner_style_block = ""
    if partner_style_resolved:
        partner_style_block = (
            f"\nСТИЛЬ СОВЕТОВ ПРО ПАРТНЁРА (когда речь о втором родителе/партнёре): "
            f"{partner_style_resolved}\n"
        )

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
{my_style_block}{partner_style_block}
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
    my_style: str = "",
    partner_style: str = "",
) -> str:
    client = get_client()

    system_prompt = _build_system_prompt(
        role=role,
        age_display=age_display,
        age_context=age_context,
        kb_chunks=kb_chunks,
        child_context=child_context,
        brave_results=brave_results,
        my_style=my_style,
        partner_style=partner_style,
    )

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
