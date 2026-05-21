import asyncio
import json

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from loguru import logger

from utils.age_calc import calculate_age
from utils.child_items import items_of, remove_item, ITEMIZED_FIELDS
import db.queries as db

_scheduler: AsyncIOScheduler = None


async def _check_age_notifications(bot: Bot):
    """Ежедневная проверка: уведомить если ребёнок скоро выйдет из возрастного диапазона книги."""
    users = await db.get_all_active_users()
    books = await db.get_shared_books()

    for user in users:
        birthdate = user.get("child_birthdate")
        if not birthdate:
            continue
        age = calculate_age(birthdate)
        if not age:
            continue

        for book in books:
            age_min = book.get("age_range_min", 0)
            age_max = book.get("age_range_max", 0)

            # Книга релевантна только если ребёнок В диапазоне
            if not (age_min <= age.months <= age_max):
                continue

            # Уведомить если осталось 1-3 месяца до выхода из диапазона
            months_left = age_max - age.months
            if 1 <= months_left <= 3:
                already_sent = await db.was_notification_sent(user["id"], book["id"])
                if not already_sent:
                    try:
                        await bot.send_message(
                            user["id"],
                            f"📚 <b>Напоминание о книге</b>\n\n"
                            f"Книга «{book['original_name']}» актуальна ещё примерно "
                            f"{months_left} мес. для вашего ребёнка ({age.display}).\n\n"
                            "Успей прочитать или задай вопросы по ней!"
                        )
                        await db.mark_notification_sent(user["id"], book["id"])
                        logger.info(
                            "Уведомление отправлено: user={}, book={}",
                            user["id"], book["id"]
                        )
                    except Exception as e:
                        logger.warning(
                            "Не удалось отправить уведомление user={}: {}", user["id"], e
                        )


async def _weekly_context_audit():
    """Раз в неделю: для каждого активного пользователя проходим по всем itemized
    полям и через Flash валидируем каждую запись (мусор/норма) + ставим
    age_relevance тегам тем записям где его ещё нет.

    Мусор удаляется тихо. Возраст-просроченные записи попадают в обычную
    очередь review автоматически (через _is_age_outdated).
    """
    try:
        from services.claude_client import tag_age_relevance
    except Exception as e:
        logger.warning("Audit: не удалось импортировать tagger: {}", e)
        return

    users = await db.get_all_active_users()
    total_users = 0
    total_tagged = 0
    total_junked = 0
    total_cost = 0.0

    for user in users:
        birthdate = user.get("child_birthdate")
        if not birthdate:
            continue
        age = calculate_age(birthdate)
        if not age:
            continue

        ctx_str = user.get("child_context")
        if not ctx_str:
            continue
        try:
            ctx = json.loads(ctx_str)
        except Exception:
            continue

        changed = False
        per_user_cost = 0.0
        for field in ITEMIZED_FIELDS:
            items = items_of(ctx.get(field))
            if not items:
                continue
            updated = list(items)
            removed_ids = set()

            for it in items:
                if it.get("age_relevance"):
                    continue
                text = (it.get("text") or "").strip()
                if not text:
                    removed_ids.add(it.get("id"))
                    continue
                try:
                    result = await tag_age_relevance(text, field, age.months)
                except Exception as e:
                    logger.warning("Audit tag error user={}: {}", user["id"], e)
                    continue
                per_user_cost += result.cost_usd or 0.0

                if result.tag == "junk":
                    removed_ids.add(it.get("id"))
                    total_junked += 1
                else:
                    for j, u in enumerate(updated):
                        if u.get("id") == it.get("id"):
                            updated[j] = dict(u, age_relevance=result.tag)
                            total_tagged += 1
                            break
                # лёгкая пауза чтобы не упереться в rate-limit
                await asyncio.sleep(0.05)

            if removed_ids:
                updated = [u for u in updated if u.get("id") not in removed_ids]
            if updated != items:
                ctx[field] = updated
                changed = True

        if changed:
            await db.set_child_context(user["id"], ctx)
            total_users += 1
        if per_user_cost > 0:
            total_cost += per_user_cost
            try:
                await db.add_token_usage(
                    user_id=user["id"],
                    model="audit-flash",
                    input_tokens=0, output_tokens=0,
                    cost_usd=per_user_cost,
                )
            except Exception:
                pass

    logger.info(
        "Weekly audit: users_changed={} tagged={} junk_removed={} cost=${:.4f}",
        total_users, total_tagged, total_junked, total_cost,
    )


def start_scheduler(bot: Bot):
    global _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _check_age_notifications,
        trigger="cron",
        hour=10,
        minute=0,
        kwargs={"bot": bot},
        id="age_notifications",
        replace_existing=True,
    )
    _scheduler.add_job(
        _weekly_context_audit,
        trigger="cron",
        day_of_week="mon",
        hour=4,
        minute=0,
        id="child_context_audit",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Планировщик запущен (возрастные уведомления 10:00; weekly audit пн 04:00)")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        logger.info("Планировщик остановлен")
