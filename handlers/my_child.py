import json
from aiogram import Router, F, Bot
from aiogram.types import Message
from loguru import logger

from config import Config
from utils.age_calc import calculate_age
from kb.rag_engine import search_kb, format_chunks_for_prompt
from services.claude_client import ask_claude
from services.onboarding import format_child_context_for_llm
from utils.text_helpers import split_long_message
from utils.thinking import ThinkingIndicator
import db.queries as db

router = Router()

# Аспекты развития ребёнка — ротируются при каждом нажатии кнопки
ASPECTS = [
    {
        "name": "физическое развитие и моторика",
        "query_focus": "физическое развитие моторика движения",
        "prompt": (
            "Расскажи подробно про <b>физическое развитие и моторику</b> ребёнка в возрасте {age} ({context}). "
            "Какие двигательные навыки формируются сейчас? Что нормально, а на что обратить внимание? "
        ),
    },
    {
        "name": "эмоциональное развитие и привязанность",
        "query_focus": "эмоции привязанность контакт с родителями",
        "prompt": (
            "Расскажи подробно про <b>эмоциональное развитие и привязанность</b> ребёнка в возрасте {age} ({context}). "
            "Как ребёнок выражает эмоции? Что важно для формирования привязанности? "
        ),
    },
    {
        "name": "сон и режим дня",
        "query_focus": "сон режим дня ритм бодрствование",
        "prompt": (
            "Расскажи подробно про <b>сон и режим дня</b> ребёнка в возрасте {age} ({context}). "
            "Сколько ребёнок должен спать? Как выстраивать режим? Что нормально для этого возраста? "
        ),
    },
    {
        "name": "питание и здоровье",
        "query_focus": "питание кормление здоровье рост вес",
        "prompt": (
            "Расскажи подробно про <b>питание и здоровье</b> ребёнка в возрасте {age} ({context}). "
            "Какие потребности в питании? На какие аспекты здоровья обратить внимание? "
        ),
    },
    {
        "name": "речь и коммуникация",
        "query_focus": "речь звуки коммуникация общение гуление",
        "prompt": (
            "Расскажи подробно про <b>речевое развитие и коммуникацию</b> ребёнка в возрасте {age} ({context}). "
            "Какие звуки и формы общения характерны? Как стимулировать речевое развитие? "
        ),
    },
    {
        "name": "игры и стимуляция развития",
        "query_focus": "игры занятия стимуляция развитие игрушки",
        "prompt": (
            "Расскажи подробно про <b>игры и стимуляцию развития</b> ребёнка в возрасте {age} ({context}). "
            "Какие игры и занятия полезны сейчас? Как играть с ребёнком в этом возрасте? "
        ),
    },
]


def _next_aspect(child_ctx: dict) -> tuple[int, dict]:
    """Возвращает (индекс, аспект) — следующий по кругу."""
    last_idx = child_ctx.get("_last_aspect_index", -1)
    next_idx = (last_idx + 1) % len(ASPECTS)
    return next_idx, ASPECTS[next_idx]


@router.message(F.text.startswith("👶 Расскажи о"))
async def my_child_handler(message: Message, bot: Bot, config: Config = None, db_user: dict = None):
    if not db_user:
        await message.answer("Сначала пройди настройку через /start")
        return

    birthdate = db_user.get("child_birthdate")
    if not birthdate:
        await message.answer(
            "📅 Дата рождения ребёнка не указана.\n"
            "Введи её через /setdate или в 👤 Мой профиль."
        )
        return

    age = calculate_age(birthdate)
    if not age:
        await message.answer("⚠️ Не удалось вычислить возраст. Проверь дату рождения.")
        return

    user_id = message.from_user.id

    # Определяем следующий аспект развития
    child_ctx = {}
    if db_user.get("child_context"):
        try:
            child_ctx = json.loads(db_user["child_context"])
        except Exception:
            pass

    aspect_idx, aspect = _next_aspect(child_ctx)

    async with ThinkingIndicator(bot, message.chat.id, "Ищу информацию по возрасту...") as thinking:
        excluded_ids = await db.get_excluded_book_ids(user_id)

        # RAG-запрос адаптирован под конкретный аспект
        query = f"{aspect['query_focus']} ребёнка в {age.display}"
        chunks = await search_kb(
            user_id=user_id,
            query=query,
            age_months=age.months,
            excluded_book_ids=excluded_ids,
            n_results=5,
        )

        if not chunks:
            await message.answer(
                f"📚 В базе знаний нет книг для возраста <b>{age.display}</b>.\n\n"
                "Загрузи подходящую книгу через 📖 Моя библиотека."
            )
            return

        kb_text = format_chunks_for_prompt(chunks)

        child_context = format_child_context_for_llm(db_user)

        user_prompt = (
            aspect["prompt"].format(age=age.display, context=age.context)
            + f"ВАЖНО: точный возраст ребёнка — {age.display}. Используй именно этот возраст в ответе, не округляй. "
            "Дай 2-3 конкретных совета и один маленький практический шаг на сегодня."
        )

        role = db_user.get("role", "both")
        my_style = child_ctx.get("my_style", "")
        partner_style = child_ctx.get("partner_style", "")

        await thinking.update(f"Генерирую рекомендации: {aspect['name']}...")

        try:
            result = await ask_claude(
                config=config,
                role=role,
                age_display=age.display,
                age_context=age.context,
                kb_chunks=kb_text,
                history=[],
                user_message=user_prompt,
                child_context=child_context,
                my_style=my_style,
                partner_style=partner_style,
                temperature=0.4,
            )
        except Exception as e:
            logger.error("Ошибка Claude в my_child: {}", e)
            await message.answer("❌ Ошибка при получении ответа. Попробуй позже.")
            return

    # Сохраняем индекс аспекта для следующего нажатия
    child_ctx["_last_aspect_index"] = aspect_idx
    await db.set_child_context(user_id, child_ctx)

    # НЕ сохраняем в историю диалога — кнопка автономна
    await db.add_token_usage(
        user_id=user_id,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
    )

    model_short = result.model.split("-")[1].capitalize()
    cost_line = (
        f"\n\n<i>{model_short} · "
        f"{result.input_tokens + result.output_tokens} tok · "
        f"${result.cost_usd:.4f}</i>"
    )
    response_text = result.text + cost_line

    for part in split_long_message(response_text):
        await message.answer(part)
