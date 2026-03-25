import re
from dataclasses import dataclass
from datetime import date
from typing import List, Dict, Optional
import anthropic
from loguru import logger

from config import Config

_client: Optional[anthropic.AsyncAnthropic] = None

MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"

# Цены за 1M токенов (USD) — https://docs.anthropic.com/en/docs/about-claude/pricing
PRICING = {
    MODEL_SONNET: {"input": 3.0, "output": 15.0},
    MODEL_HAIKU:  {"input": 0.80, "output": 4.0},
}


@dataclass
class ClaudeResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = PRICING.get(model, PRICING[MODEL_HAIKU])
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000

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

# Маркеры сложных вопросов — нужен Sonnet
_COMPLEX_PATTERNS = [
    # Кризисные ситуации
    r"истерик|скандал|орёт|кричит|бьёт|дерётся|агресси|нен[ао]вижу",
    # Медицинские/психологические темы
    r"аутизм|сдвг|adhd|задержк[аи] развит|логопед|невролог|психолог|диагноз|отклонен",
    # Сложные семейные ситуации
    r"развод|разлук|измен|абьюз|насили|алкогол|зависимост|депресс|тревожн|суицид",
    # Подростковые проблемы
    r"наркотик|курит|пьёт|секс|порно|буллинг|травл|самоповрежд",
    # Партнёрские конфликты о воспитании
    r"муж не согласен|жена не понимает|свекров|тёщ|конфликт.*воспитан",
    # Длинные развёрнутые вопросы (>200 символов обычно сложнее)
    # Обрабатывается отдельно в функции
]
_COMPLEX_RE = re.compile("|".join(_COMPLEX_PATTERNS), re.IGNORECASE)


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
    return value


def _choose_model(user_message: str, kb_chunks: str, brave_results: str) -> str:
    """Роутинг: сложные вопросы → Sonnet, простые → Haiku."""
    # Сложный вопрос по содержанию
    if _COMPLEX_RE.search(user_message):
        return MODEL_SONNET

    # Длинный вопрос (>200 символов) — скорее всего сложная ситуация
    if len(user_message) > 200:
        return MODEL_SONNET

    # Если есть интернет-источники — нужен анализ достоверности
    if brave_results:
        return MODEL_SONNET

    # Много контекста из книг — нужен хороший синтез
    if kb_chunks and len(kb_chunks) > 5000:
        return MODEL_SONNET

    # Всё остальное — Haiku справится
    return MODEL_HAIKU


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
        "grandma": "бабушке",
        "grandpa": "дедушке",
        "relative": "родственнику",
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
        "grandma": (
            "Базовый стиль: тёплый, уважительный, поддерживающий. "
            "Учитывай разницу поколений в подходах к воспитанию. "
            "Помогай быть опорой для семьи, не подменяя родителей. "
            "Мягко объясняй современные подходы, если бабушкины методы устарели. "
            "Фокус: как помочь внуку/внучке, уважая границы родителей."
        ),
        "grandpa": (
            "Базовый стиль: спокойный, опытный, аналитический. "
            "Фокус на безопасных совместных активностях, передаче жизненного опыта. "
            "Помогай находить общий язык с внуком/внучкой без навязывания. "
            "Учитывай, что авторитет дедушки — через интерес и уважение, не через власть."
        ),
        "relative": (
            "Базовый стиль: нейтральный, тактичный, поддерживающий. "
            "Ты помогаешь родственнику (тётя, дядя, крёстный и т.д.) поддержать семью. "
            "Фокус: как взаимодействовать с ребёнком, не вмешиваясь в воспитательные решения родителей. "
            "Подсказывай как быть значимым взрослым в жизни ребёнка."
        ),
    }.get(role, "")

    my_style_resolved = _resolve_style(my_style)
    my_style_block = ""
    if my_style_resolved:
        my_style_block = (
            f"\nСТИЛЬ ОБЩЕНИЯ С РОДИТЕЛЕМ (как ты говоришь с ним/ней): "
            f"{my_style_resolved}\n"
        )

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

    today = date.today().strftime("%d.%m.%Y")

    return f"""Ты — эксперт-консультант по осознанному родительству и детскому развитию.
Твоя миссия — помогать {role_text} растить ребёнка, опираясь на доказательную медицину, теорию привязанности и нейробиологию.

Сегодняшняя дата: {today}.
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

КРИТИЧЕСКИ ВАЖНО — формат ответа:
- Ты пишешь в Telegram-чат. Telegram понимает ТОЛЬКО HTML-теги: <b>, <i>, <code>.
- ЗАПРЕЩЕНО использовать markdown: НЕ #, НЕ ##, НЕ **, НЕ __, НЕ ```, НЕ ---, НЕ >, НЕ *курсив*. Telegram покажет их КАК ЕСТЬ — сырым текстом.
- Для списков: нумерация (1. 2. 3.) или символы (• ▸). НЕ используй «- » (дефис-пробел).
- Для заголовков: <b>Жирный текст</b> с пустой строкой сверху.
- НИКОГДА не проси пользователя сообщить дату — ты уже знаешь сегодняшнюю дату и возраст ребёнка.

Структура ответа: начинай с объяснения что чувствует ребёнок и почему (с точки зрения развития мозга). Завершай конкретным маленьким шагом.
{kb_block}{internet_block}"""



def _sanitize_markdown(text: str) -> str:
    """Страховка: конвертирует остатки markdown в HTML для Telegram."""
    # Заголовки: # Text -> <b>Text</b>
    text = re.sub(r"^#{1,3}\s+(.+)$", lambda m: "<b>" + m.group(1) + "</b>", text, flags=re.MULTILINE)
    # Bold: **text** -> <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: "<b>" + m.group(1) + "</b>", text)
    # Italic: *text* -> <i>text</i>
    text = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", lambda m: "<i>" + m.group(1) + "</i>", text)
    # Горизонтальные линии: --- -> пустая строка
    text = re.sub(r"^-{3,}$", "", text, flags=re.MULTILINE)
    # Blockquote: > text -> text
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    return text

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
) -> ClaudeResponse:
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

    # Роутинг модели
    model = _choose_model(user_message, kb_chunks, brave_results)
    logger.info("Роутинг: model={} для запроса '{}'", model.split("-")[1], user_message[:50])

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        )
        text = _sanitize_markdown(response.content[0].text)
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = calculate_cost(model, input_tokens, output_tokens)

        logger.info(
            "Usage: model={} in={} out={} cost=${:.4f}",
            model.split("-")[1], input_tokens, output_tokens, cost,
        )

        return ClaudeResponse(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
    except Exception as e:
        logger.error("Ошибка Claude API ({}): {}", model, e)
        raise
