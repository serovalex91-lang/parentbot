"""
Уведомление админа о новых коммитах при старте бота.
Сравнивает HEAD с сохранённым хешем, формирует changelog.
"""
import os
import subprocess
from aiogram import Bot
from loguru import logger

from keyboards.main_kb import update_broadcast_keyboard

LAST_COMMIT_FILE = "data/.last_commit"


def _run_git(args: list[str]) -> str:
    """Запуск git-команды, возвращает stdout."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except Exception as e:
        logger.warning("git error: {}", e)
        return ""


def _get_current_head() -> str:
    return _run_git(["rev-parse", "HEAD"])


def _get_commits_since(last_hash: str) -> list[dict]:
    """Возвращает список коммитов от last_hash до HEAD."""
    log_output = _run_git([
        "log", f"{last_hash}..HEAD",
        "--pretty=format:%h|%s", "--no-merges",
    ])
    if not log_output:
        return []
    commits = []
    for line in log_output.strip().split("\n"):
        if "|" in line:
            short_hash, subject = line.split("|", 1)
            commits.append({"hash": short_hash, "subject": subject})
    return commits


def _format_changelog(commits: list[dict]) -> str:
    """Форматирует список коммитов в читаемый текст для юзеров."""
    lines = []
    for c in commits:
        subj = c["subject"]
        # Убираем префикс [agent] для чистоты
        if subj.startswith("[agent] "):
            subj = subj[8:]
        # Убираем тип (feat:, fix:, etc.) и делаем читаемым
        for prefix in ("feat: ", "fix: ", "refactor: ", "docs: ", "chore: "):
            if subj.startswith(prefix):
                subj = subj[len(prefix):]
                break
        lines.append(f"[+] {subj}")
    return "\n".join(lines)


async def check_and_notify(bot: Bot, admin_id: int):
    """Проверяет новые коммиты и отправляет уведомление админу."""
    current_head = _get_current_head()
    if not current_head:
        logger.warning("Не удалось получить текущий HEAD")
        return

    # Читаем последний известный хеш
    last_hash = ""
    if os.path.exists(LAST_COMMIT_FILE):
        with open(LAST_COMMIT_FILE, "r") as f:
            last_hash = f.read().strip()

    # Первый запуск — просто сохраняем хеш
    if not last_hash:
        _save_head(current_head)
        logger.info("Update notifier: первый запуск, сохранён HEAD {}", current_head[:7])
        return

    # Нет изменений
    if last_hash == current_head:
        return

    # Собираем коммиты
    commits = _get_commits_since(last_hash)
    if not commits:
        _save_head(current_head)
        return

    changelog = _format_changelog(commits)
    count = len(commits)
    word = _plural(count, "изменение", "изменения", "изменений")

    text = (
        f"<b>Обновление бота</b> ({count} {word})\n\n"
        f"{changelog}\n\n"
        f"Отправить уведомление пользователям?"
    )

    try:
        await bot.send_message(
            admin_id,
            text,
            reply_markup=update_broadcast_keyboard(changelog),
        )
        logger.info("Отправлено уведомление админу о {} коммитах", count)
    except Exception as e:
        logger.error("Не удалось уведомить админа: {}", e)

    _save_head(current_head)


def get_recent_changelog(n: int = 10) -> tuple[str, int]:
    """Возвращает (changelog_text, count) для последних N коммитов."""
    log_output = _run_git([
        "log", f"-{n}",
        "--pretty=format:%h|%s", "--no-merges",
    ])
    if not log_output:
        return "", 0
    commits = []
    for line in log_output.strip().split("\n"):
        if "|" in line:
            short_hash, subject = line.split("|", 1)
            commits.append({"hash": short_hash, "subject": subject})
    return _format_changelog(commits), len(commits)


def _save_head(commit_hash: str):
    os.makedirs(os.path.dirname(LAST_COMMIT_FILE), exist_ok=True)
    with open(LAST_COMMIT_FILE, "w") as f:
        f.write(commit_hash)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 19:
        return many
    mod = n % 10
    if mod == 1:
        return one
    if 2 <= mod <= 4:
        return few
    return many
