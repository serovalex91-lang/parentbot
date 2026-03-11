from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from loguru import logger

from utils.age_calc import calculate_age
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
    _scheduler.start()
    logger.info("Планировщик запущен (возрастные уведомления в 10:00)")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        logger.info("Планировщик остановлен")
