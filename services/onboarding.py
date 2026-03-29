"""
Периодический onboarding — сбор и ревизия информации о ребёнке.

Два режима:
1. FILL — заполнение пустых полей (push "расскажи больше")
2. REVIEW — проверка актуальности старых записей
"""

import json
import random
from datetime import datetime, date, timedelta
from typing import Optional, Tuple, Dict, List

# Минимальный интервал между промптами (дни)
PROMPT_INTERVAL_DAYS = 7  # раз в неделю после FAST-фазы (новый вопрос)
REVIEW_INTERVAL_DAYS = 3  # через 3 дня после fill — review (если есть устаревшие)
# Ускоренный режим для новых юзеров (первые N промптов — чаще)
FAST_INTERVAL_DAYS = 2
FAST_PROMPTS_COUNT = 5
# Поле считается устаревшим после N дней
STALE_THRESHOLDS = {
    "child_features": 30,    # здоровье — раз в месяц
    "child_character": 45,   # характер — раз в 1.5 мес
    "child_notes": 21,       # заметки — раз в 3 недели
}
# Роли, которым показываем onboarding
ONBOARDING_ROLES = {"papa", "mama", "both"}

# Дисклеймеры — ротация
DISCLAIMERS = [
    "Чтобы мои советы были точнее — ",
    "Мини-вопрос для персонализации — ",
    "Обновлю контекст, чтобы не переспрашивать каждый раз — ",
    "Быстрый вопрос, чтобы лучше понимать {name} — ",
    "Это поможет давать ответы именно про {name} — ",
]

# Банк вопросов: (field, question)
# {name} заменяется на имя ребёнка, {gender_pronoun} на "он/она"
QUESTIONS_BY_AGE: Dict[str, List[Tuple[str, str]]] = {
    "0-3": [
        ("child_notes", "Как {name} обычно засыпает — сам(а) или на ручках/с укачиванием?"),
        ("child_features", "Есть ли у {name} аллергии или особенности здоровья, о которых мне важно знать?"),
        ("child_notes", "Как у {name} с коликами? Беспокоят?"),
        ("child_character", "Как бы ты описал(а) темперамент {name} — спокойный, активный, требовательный?"),
        ("child_notes", "На каком вскармливании {name} — грудное, смесь, смешанное?"),
        ("child_notes", "Сколько раз за ночь {name} просыпается?"),
        ("child_features", "Были ли у {name} какие-то сложности при рождении?"),
        ("child_notes", "Как {name} реагирует на купание?"),
    ],
    "3-6": [
        ("child_notes", "{name} уже переворачивается? Как с моторикой?"),
        ("child_character", "Как {name} реагирует на незнакомых людей?"),
        ("child_notes", "Начали ли вы вводить прикорм? Как {name} реагирует?"),
        ("child_features", "Сколько зубов сейчас у {name}?"),
        ("child_notes", "Как у {name} с дневным сном — сколько раз и как долго?"),
        ("child_character", "Как бы ты описал(а) настроение {name} — в основном весёлый, серьёзный, переменчивый?"),
        ("child_notes", "Есть ли у {name} любимая игрушка или занятие?"),
        ("child_features", "Были ли у {name} проблемы с пищевой аллергией при вводе прикорма?"),
    ],
    "6-12": [
        ("child_notes", "{name} уже ползает? Или сразу пытается ходить?"),
        ("child_character", "Чем {name} сейчас больше всего увлекается?"),
        ("child_features", "Сколько зубов сейчас у {name}?"),
        ("child_notes", "Как {name} ведёт себя при расставании с тобой — спокойно или переживает?"),
        ("child_notes", "Какие слова или звуки {name} уже произносит?"),
        ("child_character", "Как {name} реагирует на слово «нет» или запреты?"),
        ("child_notes", "Как обстоят дела с ночным сном? Спит всю ночь или просыпается?"),
        ("child_features", "Есть ли продукты, которые {name} категорически не ест или от которых плохо?"),
        ("child_notes", "Что {name} любит делать больше всего — играть, смотреть книжки, музыку?"),
    ],
    "12-24": [
        ("child_notes", "Сколько слов примерно говорит {name}?"),
        ("child_character", "Как часто у {name} бывают истерики и что обычно их вызывает?"),
        ("child_notes", "{name} ходит уверенно или ещё держится за опору?"),
        ("child_features", "Сколько зубов сейчас у {name}?"),
        ("child_character", "Как {name} ведёт себя с другими детьми — интересуется, сторонится, отбирает игрушки?"),
        ("child_notes", "Какой режим дня у {name} — сколько дневных снов, во сколько ночь?"),
        ("child_notes", "{name} сам(а) ест ложкой или пока кормите?"),
        ("child_character", "Что сейчас вызывает у {name} самый большой страх или тревогу?"),
        ("child_features", "Есть ли у {name} проблемы со здоровьем, которые сейчас беспокоят?"),
        ("child_notes", "Какие любимые занятия у {name} — рисование, конструктор, мячи, танцы?"),
    ],
    "24-36": [
        ("child_notes", "Как {name} говорит — отдельные слова, фразы, предложения?"),
        ("child_character", "Как {name} переносит отказы и ограничения? Есть ли кризис «я сам»?"),
        ("child_notes", "{name} ходит в сад? Как адаптация?"),
        ("child_character", "Как {name} играет — один, с тобой, с другими детьми?"),
        ("child_notes", "{name} приучается к горшку? Как продвигается?"),
        ("child_features", "Есть ли у {name} какие-то страхи — темнота, громкие звуки, собаки?"),
        ("child_character", "Опиши {name} тремя словами — какой {gp} сейчас?"),
        ("child_notes", "Как {name} засыпает — с ритуалом, с книжкой, с тобой рядом?"),
        ("child_notes", "Что {name} сейчас больше всего любит — мультики, книги, прогулки, игры?"),
        ("child_features", "Как у {name} с зубами? Все молочные уже?"),
    ],
    "36-60": [
        ("child_character", "Как {name} ладит с другими детьми в саду/на площадке?"),
        ("child_notes", "{name} ходит в сад или на какие-то занятия?"),
        ("child_character", "Есть ли у {name} сейчас страхи или тревоги?"),
        ("child_notes", "Чем {name} увлекается — рисование, конструкторы, спорт, ролевые игры?"),
        ("child_character", "Как {name} справляется с конфликтами — дерётся, плачет, договаривается?"),
        ("child_notes", "Есть ли у {name} воображаемый друг или любимая ролевая игра?"),
        ("child_features", "Как у {name} со здоровьем — часто болеет, аллергии, хронические моменты?"),
        ("child_character", "Как {name} реагирует на новые места и незнакомых людей?"),
        ("child_notes", "Умеет ли {name} одеваться/раздеваться самостоятельно?"),
        ("child_notes", "Как {name} относится к еде — ест всё или привередничает?"),
    ],
    "60-84": [
        ("child_notes", "{name} ходит в школу или на подготовку?"),
        ("child_character", "Как {name} относится к учёбе/занятиям — с интересом или через силу?"),
        ("child_notes", "Есть ли у {name} близкие друзья?"),
        ("child_character", "Как {name} справляется с проигрышем или неудачей?"),
        ("child_features", "Есть ли у {name} особенности здоровья, которые влияют на учёбу или активность?"),
        ("child_notes", "Какие секции/кружки посещает {name}?"),
        ("child_character", "Как {name} ведёт себя дома — спокойный, энергичный, требует внимания?"),
        ("child_notes", "Умеет ли {name} читать? На каком уровне?"),
    ],
    "84+": [
        ("child_character", "Как у {name} отношения с одноклассниками?"),
        ("child_notes", "Какие предметы {name} нравятся, а какие вызывают сложности?"),
        ("child_character", "Как {name} реагирует на критику и замечания?"),
        ("child_notes", "Чем {name} увлекается вне школы?"),
        ("child_features", "Есть ли что-то со здоровьем, что беспокоит?"),
        ("child_character", "Как {name} ведёт себя при стрессе — замыкается, злится, плачет?"),
        ("child_notes", "Сколько экранного времени в день у {name}?"),
        ("child_character", "Есть ли темы, о которых {name} не хочет говорить?"),
    ],
}


async def _generate_smart_question(
    context: dict, age_months: int, child_name: str, gender: str
) -> Optional[Tuple[str, str]]:
    """Генерирует контекстный вопрос через Haiku на основе уже известных данных."""
    try:
        from services.claude_client import get_client, MODEL_HAIKU, calculate_cost
        import db.queries as db_queries
    except Exception:
        return None

    known_parts = []
    if context.get("child_features"):
        known_parts.append(f"Особенности: {context['child_features']}")
    if context.get("child_character"):
        known_parts.append(f"Характер: {context['child_character']}")
    if context.get("child_notes"):
        known_parts.append(f"Заметки: {context['child_notes']}")
    known_str = "\n".join(known_parts) if known_parts else "Пока ничего не записано."

    name = child_name or "ребёнок"
    gp = "мальчик" if gender == "boy" else ("девочка" if gender == "girl" else "ребёнок")

    system = (
        "Ты помощник родительского бота. Сгенерируй ОДИН короткий вопрос родителю "
        "о ребёнке, чтобы узнать что-то новое и полезное для персонализации советов.\n\n"
        f"Ребёнок: {name}, {gp}, {age_months} месяцев.\n"
        f"Уже известно:\n{known_str}\n\n"
        "Правила:\n"
        "1. Вопрос должен быть НОВЫМ — не дублировать то, что уже известно.\n"
        "2. Подходить по возрасту.\n"
        "3. Быть конкретным, а не абстрактным.\n"
        "4. Использовать имя ребёнка.\n"
        "5. Формат ответа строго JSON:\n"
        '{"field": "child_notes", "question": "текст вопроса"}\n'
        'field — одно из: child_features, child_character, child_notes\n'
        "Только JSON, ничего больше."
    )

    try:
        client = get_client()
        response = await client.messages.create(
            model=MODEL_HAIKU,
            max_tokens=150,
            system=system,
            messages=[{"role": "user", "content": "Сгенерируй вопрос."}],
        )
        raw = response.content[0].text.strip()
        cost = calculate_cost(
            MODEL_HAIKU, response.usage.input_tokens, response.usage.output_tokens
        )
        from loguru import logger
        logger.info(
            "Smart question gen: in={} out={} cost=${:.4f}",
            response.usage.input_tokens, response.usage.output_tokens, cost,
        )

        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(raw[start:end])
            field = data.get("field", "child_notes")
            question = data.get("question", "")
            if field in ("child_features", "child_character", "child_notes") and question:
                return (field, question)
        return None
    except Exception:
        return None


# Варианты ответов для сложных вопросов
# Ключ — шаблон вопроса (до замены плейсхолдеров), значение — список (value, label, hint)
QUESTION_OPTIONS: Dict[str, List[Tuple[str, str, str]]] = {
    # 0-3
    "Как бы ты описал(а) темперамент {name} — спокойный, активный, требовательный?": [
        ("Спокойный", "Спокойный", "Легко успокаивается, не плачет без явной причины"),
        ("Активный", "Активный", "Много двигается, быстро реагирует на всё вокруг"),
        ("Требовательный", "Требовательный", "Нужно много внимания, громко сообщает о потребностях"),
        ("Переменчивый", "Переменчивый", "Бывает и спокойным, и активным — зависит от настроения"),
    ],
    "Как {name} обычно засыпает — сам(а) или на ручках/с укачиванием?": [
        ("Засыпает сам", "Сам(а)", "Кладёшь в кроватку — засыпает без помощи"),
        ("На ручках/укачивание", "На ручках", "Нужно укачивать или носить на руках"),
        ("С грудью/бутылочкой", "С кормлением", "Засыпает во время кормления"),
        ("По-разному", "По-разному", "Нет одного способа — зависит от дня"),
    ],
    # 3-6
    "Как {name} реагирует на незнакомых людей?": [
        ("Спокойно, с интересом", "С интересом", "Разглядывает, улыбается, не боится"),
        ("Настороженно", "Настороженно", "Притихает, прижимается к родителю"),
        ("Плачет, пугается", "Пугается", "Плачет или отворачивается от чужих"),
        ("Не обращает внимания", "Без реакции", "Не замечает, занят своим"),
    ],
    "Как бы ты описал(а) настроение {name} — в основном весёлый, серьёзный, переменчивый?": [
        ("В основном весёлый", "Весёлый", "Много улыбается и смеётся"),
        ("Серьёзный, наблюдательный", "Серьёзный", "Больше наблюдает, чем улыбается"),
        ("Переменчивый", "Переменчивый", "Настроение быстро меняется"),
    ],
    # 6-12
    "Как {name} реагирует на слово «нет» или запреты?": [
        ("Спокойно принимает", "Спокойно", "Отвлекается или переключается на другое"),
        ("Расстраивается, плачет", "Расстраивается", "Плачет, но быстро успокаивается"),
        ("Протестует, злится", "Протестует", "Кричит, бросает вещи, не соглашается"),
        ("Игнорирует", "Игнорирует", "Делает вид, что не слышит, и продолжает"),
    ],
    # 12-24
    "Сколько слов примерно говорит {name}?": [
        ("Пока только слоги и звуки", "Слоги/звуки", "«ма», «па», «ба», «му» — осознанные слоги, но не слова"),
        ("5-10 слов", "5-10 слов", "Простые слова: мама, папа, дай, на, нет и подобные"),
        ("10-30 слов", "10-30 слов", "Уверенный набор слов, пытается называть предметы"),
        ("30+ слов, начинает фразы", "30+ слов", "Говорит много, начинает складывать слова в фразы"),
    ],
    "Как часто у {name} бывают истерики и что обычно их вызывает?": [
        ("Редко, по конкретной причине", "Редко", "1-2 раза в неделю, обычно из-за усталости или голода"),
        ("Часто, из-за запретов", "Часто, запреты", "Почти каждый день, когда что-то запрещают"),
        ("Часто, без явной причины", "Часто, без причины", "Сложно понять что именно триггерит"),
        ("Почти не бывает", "Почти нет", "Ребёнок обычно спокойно реагирует"),
    ],
    "Как {name} ведёт себя с другими детьми — интересуется, сторонится, отбирает игрушки?": [
        ("Интересуется, играет рядом", "С интересом", "Подходит, наблюдает, пытается взаимодействовать"),
        ("Сторонится", "Сторонится", "Предпочитает играть отдельно, стесняется"),
        ("Отбирает игрушки, толкает", "Конфликтует", "Ещё не умеет делиться, бывают стычки"),
        ("По-разному", "По-разному", "Зависит от настроения и обстановки"),
    ],
    # 24-36
    "Как {name} говорит — отдельные слова, фразы, предложения?": [
        ("Отдельные слова", "Слова", "Говорит отдельными словами, фразы редко"),
        ("Короткие фразы из 2-3 слов", "Фразы", "«Мама дай», «Хочу пить», «Пойдём гулять»"),
        ("Предложения", "Предложения", "Строит полные предложения, рассказывает"),
        ("Речь ещё формируется", "Формируется", "Говорит, но многое непонятно окружающим"),
    ],
    "Как {name} переносит отказы и ограничения? Есть ли кризис «я сам»?": [
        ("Да, ярко выражен", "Кризис в разгаре", "Всё хочет сам, протестует при помощи, истерики"),
        ("Немного, управляемо", "Умеренно", "Иногда настаивает на своём, но можно договориться"),
        ("Пока спокойно", "Спокойно", "Принимает помощь, редко протестует"),
    ],
    "Как {name} играет — один, с тобой, с другими детьми?": [
        ("В основном один", "Сам(а)", "Увлекается игрушками самостоятельно"),
        ("Больше с родителями", "С родителями", "Приглашает играть вместе, зовёт"),
        ("С другими детьми", "С детьми", "Легко находит компанию на площадке/в саду"),
        ("По-разному", "По-разному", "Зависит от ситуации и настроения"),
    ],
    "Опиши {name} тремя словами — какой {gp} сейчас?": [],  # свободный ввод, без вариантов
    # 36-60
    "Как {name} ладит с другими детьми в саду/на площадке?": [
        ("Легко общается", "Легко", "Быстро находит друзей, инициирует игры"),
        ("Выборочно", "Выборочно", "Играет с 1-2 конкретными детьми"),
        ("Сложно, конфликтует", "Конфликтует", "Часто ссоры, не делится, дерётся"),
        ("Стесняется, в стороне", "Стесняется", "Предпочитает наблюдать или играть один"),
    ],
    "Как {name} справляется с конфликтами — дерётся, плачет, договаривается?": [
        ("Пытается договориться", "Договаривается", "Использует слова, ищет компромисс"),
        ("Плачет, зовёт взрослых", "Плачет", "Расстраивается, просит помощи"),
        ("Дерётся, толкает", "Дерётся", "Физически реагирует на конфликт"),
        ("Уходит, замыкается", "Уходит", "Избегает конфликта, уходит в сторону"),
    ],
    "Как {name} реагирует на новые места и незнакомых людей?": [
        ("С любопытством", "С любопытством", "Исследует, общается, не боится"),
        ("Настороженно поначалу", "Настороженно", "Сначала жмётся к родителям, потом осваивается"),
        ("С тревогой", "С тревогой", "Плачет или отказывается идти, долго привыкает"),
    ],
    # 60-84
    "Как {name} относится к учёбе/занятиям — с интересом или через силу?": [
        ("С интересом", "С интересом", "Сам просит почитать/позаниматься"),
        ("Нормально, но без фанатизма", "Нормально", "Занимается когда надо, без сопротивления"),
        ("Через силу", "Через силу", "Приходится уговаривать и мотивировать"),
        ("Зависит от темы", "По-разному", "Что-то нравится, что-то — нет"),
    ],
    "Как {name} справляется с проигрышем или неудачей?": [
        ("Спокойно", "Спокойно", "Расстраивается, но быстро переключается"),
        ("Сильно переживает", "Переживает", "Плачет, злится, может бросить игру"),
        ("Не сдаётся, пробует снова", "Упорный", "Расстраивается, но пытается ещё"),
        ("Избегает ситуаций", "Избегает", "Отказывается играть если может проиграть"),
    ],
    # 84+
    "Как {name} реагирует на критику и замечания?": [
        ("Спокойно, принимает", "Принимает", "Слушает и старается исправить"),
        ("Обижается", "Обижается", "Замыкается или плачет"),
        ("Злится, спорит", "Спорит", "Защищается, не соглашается"),
        ("Игнорирует", "Игнорирует", "Делает вид что не слышит"),
    ],
    "Как {name} ведёт себя при стрессе — замыкается, злится, плачет?": [
        ("Замыкается", "Замыкается", "Уходит в себя, не хочет разговаривать"),
        ("Злится", "Злится", "Кричит, хлопает дверями, может грубить"),
        ("Плачет", "Плачет", "Эмоционально реагирует, нужна поддержка"),
        ("Ищет поддержку", "Ищет поддержку", "Приходит обниматься, рассказывает"),
    ],
}


def get_question_options(question_template: str) -> Optional[List[Tuple[str, str, str]]]:
    """Возвращает варианты ответов для вопроса, или None если это свободный ввод."""
    options = QUESTION_OPTIONS.get(question_template)
    if options is None or len(options) == 0:
        return None
    return options


def _get_age_key(age_months: int) -> str:
    if age_months < 3:
        return "0-3"
    elif age_months < 6:
        return "3-6"
    elif age_months < 12:
        return "6-12"
    elif age_months < 24:
        return "12-24"
    elif age_months < 36:
        return "24-36"
    elif age_months < 60:
        return "36-60"
    elif age_months < 84:
        return "60-84"
    else:
        return "84+"


def _replace_placeholders(text: str, child_name: str, gender: str) -> str:
    name = child_name or "ребёнок"
    gp = "он" if gender == "boy" else ("она" if gender == "girl" else "он/она")
    return text.replace("{name}", name).replace("{gp}", gp)


def should_prompt(db_user: dict) -> bool:
    """Проверяет, пора ли показать onboarding-вопрос."""
    role = db_user.get("role", "")
    if role not in ONBOARDING_ROLES:
        return False

    if not db_user.get("onboarded_at"):
        return False

    if not db_user.get("child_birthdate"):
        return False

    last_prompt = db_user.get("last_onboarding_prompt")
    if not last_prompt:
        return True

    try:
        last_dt = datetime.fromisoformat(last_prompt)
        context = {}
        if db_user.get("child_context"):
            try:
                context = json.loads(db_user["child_context"])
            except Exception:
                pass
        ts = context.get("_timestamps", {})
        prompts_done = len(ts)

        if prompts_done < FAST_PROMPTS_COUNT:
            # FAST-фаза: каждые 2 дня
            interval = FAST_INTERVAL_DAYS
        else:
            # NORMAL-фаза: зависит от типа последнего промпта
            last_type = context.get("_last_prompt_type", "fill")
            if last_type == "fill":
                # После нового вопроса — через 3 дня проверяем review
                interval = REVIEW_INTERVAL_DAYS
            else:
                # После review — ждём остаток недели до следующего fill
                interval = PROMPT_INTERVAL_DAYS - REVIEW_INTERVAL_DAYS

        return datetime.utcnow() - last_dt > timedelta(days=interval)
    except (ValueError, TypeError):
        return True


async def get_fill_question(
    db_user: dict, age_months: int, exclude_questions: set = None
) -> Optional[Tuple[str, str, str, Optional[List[Tuple[str, str, str]]]]]:
    """Возвращает (field, question, disclaimer, options) для заполнения пустого поля.
    options — список (value, label, hint) или None для свободного ввода.
    Если банк исчерпан — генерирует вопрос через Haiku.
    exclude_questions — множество уже заданных вопросов (для ручного онбординга).
    None если нечего спрашивать."""
    context = {}
    if db_user.get("child_context"):
        try:
            context = json.loads(db_user["child_context"])
        except Exception:
            pass

    child_name = context.get("child_name", "")
    gender = context.get("child_gender", "")
    age_key = _get_age_key(age_months)
    questions = QUESTIONS_BY_AGE.get(age_key, [])
    exclude = exclude_questions or set()

    # Поля, которые ещё пустые — приоритет
    empty_fields = []
    for field in ("child_features", "child_character", "child_notes"):
        if not context.get(field):
            empty_fields.append(field)

    # Фильтруем вопросы для пустых полей (исключая уже заданные)
    # exclude содержит вопросы после замены плейсхолдеров, поэтому сравниваем тоже после замены
    candidates = [(f, q) for f, q in questions
                  if f in empty_fields and _replace_placeholders(q, child_name, gender) not in exclude]

    # Если все базовые заполнены — берём любой вопрос для обогащения
    if not candidates:
        candidates = [(f, q) for f, q in questions
                      if _replace_placeholders(q, child_name, gender) not in exclude]

    # Если банк исчерпан — генерируем через Haiku
    if not candidates:
        generated = await _generate_smart_question(context, age_months, child_name, gender)
        if generated:
            field, question_template = generated
            options = None
        else:
            return None
    else:
        field, question_template = random.choice(candidates)
        options = get_question_options(question_template)

    question = _replace_placeholders(question_template, child_name, gender)
    disclaimer = random.choice(DISCLAIMERS)
    disclaimer = disclaimer.replace("{name}", child_name or "ребёнка")

    return field, question, disclaimer, options


def get_review_question(
    db_user: dict,
) -> Optional[Tuple[str, str, str, str]]:
    """Возвращает (field, current_value, field_label, date_str) для ревизии.
    None если нечего проверять."""
    context = {}
    if db_user.get("child_context"):
        try:
            context = json.loads(db_user["child_context"])
        except Exception:
            pass

    timestamps = context.get("_timestamps", {})
    now = datetime.utcnow()

    field_labels = {
        "child_features": "Особенности/здоровье",
        "child_character": "Характер",
        "child_notes": "Заметки",
    }

    stale_fields = []
    for field, threshold_days in STALE_THRESHOLDS.items():
        value = context.get(field)
        if not value:
            continue
        ts_str = timestamps.get(field)
        if not ts_str:
            # Нет даты — считаем устаревшим
            stale_fields.append((field, value, field_labels.get(field, field), "дата неизвестна"))
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
            if now - ts > timedelta(days=threshold_days):
                date_display = ts.strftime("%d.%m.%Y")
                stale_fields.append((field, value, field_labels.get(field, field), date_display))
        except (ValueError, TypeError):
            stale_fields.append((field, value, field_labels.get(field, field), "дата неизвестна"))

    if not stale_fields:
        return None

    # Сортируем по старости (самые старые первыми, "дата неизвестна" — в начало)
    def _sort_key(item):
        date_str = item[3]
        if date_str == "дата неизвестна":
            return ""  # самый старый
        return date_str  # DD.MM.YYYY — сортировка строковая, ок для одного формата

    stale_fields.sort(key=_sort_key)
    return stale_fields[0]


async def pick_onboarding_action(
    db_user: dict, age_months: int
) -> Optional[dict]:
    """Выбирает действие: fill или review.
    FAST-фаза: только fill (новые вопросы каждые 2 дня).
    NORMAL-фаза (после 5 вопросов):
      - Раз в неделю — fill (новый вопрос), гарантированно
      - Через 3 дня после fill — review, ТОЛЬКО если есть реально устаревшие поля
      - Если устаревших нет — review пропускается, ждём следующий fill
    Возвращает dict с type и данными, или None."""
    context = {}
    if db_user.get("child_context"):
        try:
            context = json.loads(db_user["child_context"])
        except Exception:
            pass

    ts = context.get("_timestamps", {})
    prompts_done = len(ts)

    # FAST-фаза (первые N вопросов) — только fill
    if prompts_done < FAST_PROMPTS_COUNT:
        fill = await get_fill_question(db_user, age_months)
        if fill:
            field, question, disclaimer, options = fill
            return {
                "type": "fill",
                "field": field,
                "question": question,
                "disclaimer": disclaimer,
                "options": options,
            }
        return None

    # NORMAL-фаза — определяем что сейчас нужно
    last_type = context.get("_last_prompt_type", "fill")

    if last_type == "fill":
        # Последний был fill → сейчас очередь review (если есть устаревшие)
        review = get_review_question(db_user)
        if review:
            field, value, label, date_str = review
            return {
                "type": "review",
                "field": field,
                "value": value,
                "label": label,
                "date_str": date_str,
            }
        return None
    else:
        # Последний был review (или первый раз) → новый вопрос
        fill = await get_fill_question(db_user, age_months)
        if fill:
            field, question, disclaimer, options = fill
            return {
                "type": "fill",
                "field": field,
                "question": question,
                "disclaimer": disclaimer,
                "options": options,
            }
        return None


def update_context_field(context: dict, field: str, value: str) -> dict:
    """Обновляет поле в child_context с timestamp."""
    context[field] = value
    if "_timestamps" not in context:
        context["_timestamps"] = {}
    context["_timestamps"][field] = datetime.utcnow().isoformat()
    return context


def remove_context_field(context: dict, field: str) -> dict:
    """Удаляет поле из child_context и его timestamp."""
    context.pop(field, None)
    ts = context.get("_timestamps", {})
    ts.pop(field, None)
    return context


def format_child_summary(db_user: dict, age_display: str = "") -> str:
    """Формирует красивую сводку по ребёнку для экспорта."""
    context = {}
    if db_user.get("child_context"):
        try:
            context = json.loads(db_user["child_context"])
        except Exception:
            pass

    child_name = context.get("child_name", "ребёнок")
    gender_map = {"boy": "мальчик", "girl": "девочка"}
    gender = gender_map.get(context.get("child_gender", ""), "")

    parts = []
    header = f"<b>Всё о {child_name}</b>"
    if age_display:
        header += f" ({age_display})"
    if gender:
        header += f" — {gender}"
    parts.append(header)

    if context.get("child_features"):
        parts.append(f"\n<b>Здоровье и особенности:</b>\n{context['child_features']}")

    if context.get("child_character"):
        parts.append(f"\n<b>Характер:</b>\n{context['child_character']}")

    if context.get("child_notes"):
        parts.append(f"\n<b>Заметки:</b>\n{context['child_notes']}")

    if not any(context.get(f) for f in ("child_features", "child_character", "child_notes")):
        parts.append("\n<i>Пока ничего не записано. Заполни профиль или отвечай на мои вопросы — информация будет накапливаться.</i>")

    return "\n".join(parts)
