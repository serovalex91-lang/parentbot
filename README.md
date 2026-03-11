# ParentBot — Telegram-бот по осознанному родительству

Персональный AI-консультант по воспитанию детей, построенный на RAG (Retrieval-Augmented Generation) поверх загружаемых PDF-книг. Отвечает на вопросы родителей, опираясь на доказательную педагогику и теорию привязанности.

**Стек:** Python 3.11+, aiogram 3.x, Claude API (Anthropic), ChromaDB, sentence-transformers, SQLite, APScheduler
**Деплой:** VPS brain-server (`46.17.97.188`), systemd user-сервис `parentbot`

---

## Структура проекта

```
parentbot/
├── main.py                    # Точка входа, инициализация, порядок роутеров
├── config.py                  # Dataclass Config, load_config() из .env
│
├── handlers/
│   ├── start.py               # /start, онбординг, /myprofile, EditProfile, SetDate
│   ├── chat.py                # Основной чат: RAG → Claude → ответ
│   ├── my_child.py            # Кнопка "👶 Расскажи о дочке"
│   ├── pdf_upload.py          # Загрузка PDF: скачать → parse → embed → ChromaDB
│   ├── admin.py               # /admin панель: whitelist, статистика, рассылка, библиотека
│   └── help.py                # /help, кнопка "❓ Помощь"
│
├── keyboards/
│   └── main_kb.py             # Все клавиатуры: ReplyKeyboard и InlineKeyboard
│
├── kb/                        # Knowledge Base — база знаний
│   ├── chroma_client.py       # Обёртка ChromaDB: init, add_chunks, query, delete
│   ├── embedder.py            # sentence-transformers singleton + warmup()
│   ├── rag_engine.py          # search_kb(): shared + personal, post-фильтрация
│   └── pdf_processor.py       # PyMuPDF: extract_and_chunk()
│
├── services/
│   ├── claude_client.py       # Anthropic AsyncClient, system prompt, ask_claude()
│   ├── brave_search.py        # Brave Search API для режима kb_internet
│   └── scheduler.py           # APScheduler: ежедневные уведомления о книгах
│
├── db/
│   ├── schema.py              # SQL-схема, init_db()
│   └── queries.py             # Все запросы к SQLite через aiosqlite
│
├── middlewares/
│   └── auth.py                # AuthMiddleware: whitelist-проверка + загрузка db_user
│
├── states/
│   └── fsm.py                 # FSM StatesGroup: Onboarding, UploadPDF, EditProfile, SetDate, AdminPanel
│
├── utils/
│   ├── age_calc.py            # parse_birthdate(), calculate_age() → AgeInfo
│   └── text_helpers.py        # split_long_message() для длинных ответов Claude
│
├── .env.example               # Шаблон переменных окружения
├── requirements.txt
└── parentbot.service          # systemd unit-файл
```

---

## Архитектура и поток данных

### Основной чат (RAG pipeline)

```
Сообщение пользователя
        ↓
AuthMiddleware (whitelist-проверка, загружает db_user в data)
        ↓
handlers/chat.py :: handle_chat()
        ↓
1. calculate_age(birthdate)           — возраст ребёнка в месяцах
2. get_excluded_book_ids(user_id)     — какие книги пользователь отключил
3. search_kb(query, age_months, ...) — RAG: embed → ChromaDB
   ├── query_collection("shared")    — общие книги для всех пользователей
   └── query_collection("personal")  — личные книги этого пользователя
4. search_brave() (если kb_internet) — дополнительный поиск в интернете
5. get_last_messages(user_id)         — история диалога (до 20 сообщений)
6. ask_claude(system_prompt, history, user_message)  — ответ Claude
        ↓
split_long_message() → отправить по частям (лимит Telegram 4096 символов)
```

### Загрузка PDF

```
Пользователь отправляет PDF
        ↓
handle_document() → сохранить file_id в FSM-state
        ↓
process_age_range() — выбор диапазона или "auto"
        ↓
bot.download_file() → pdf_path
        ↓
extract_and_chunk(pdf_path)        — PyMuPDF → список строк-чанков
        ↓
[если auto] ask_claude() для определения возраста → "MIN:MAX"
        ↓
embed_texts(chunks)                — sentence-transformers → векторы
        ↓
add_book() в SQLite               → book_id
add_chunks() в ChromaDB           → chunk_ids
update_book_chroma_ids(book_id)   — сохранить IDs в SQLite
```

---

## База данных (SQLite)

### Таблицы

| Таблица | Описание |
|---------|----------|
| `whitelist` | Разрешённые Telegram ID |
| `users` | Профили пользователей (роль, дата рождения ребёнка, контекст, режим поиска) |
| `messages` | История диалогов (role: user/assistant) |
| `books` | Загруженные PDF книги (scope: shared/personal, возрастной диапазон в месяцах) |
| `user_book_exclusions` | Какие общие книги пользователь отключил для себя |
| `age_notifications` | Отправленные уведомления о книгах (дедупликация) |

### Ключевые поля `users`
- `role` — `papa` / `mama` / `both`
- `child_birthdate` — ISO-дата, от неё считается возраст
- `child_context` — JSON: `{child_name, child_gender, child_features, child_character, child_notes}`
- `search_mode` — `kb_only` / `kb_internet`
- `onboarded_at` — NULL пока не пройден онбординг

### Глобальный путь к БД
`db/queries.py` использует модульную переменную `_db_path`. **Обязательно** вызывать `db_queries.set_db_path(config.db_path)` в `main.py` **до** любых запросов к БД.

---

## ChromaDB — коллекции

Коллекции именуются:
- `shared_kb` — общая база для всех пользователей (загружает только admin)
- `user_{telegram_id}` — личная база каждого пользователя

Метаданные каждого чанка: `{book_id, age_min, age_max, scope, chunk_index}`

Фильтрация по возрасту в `query_collection()`:
```python
where_filter = {"$and": [{"age_min": {"$lte": age_months}}, {"age_max": {"$gte": age_months}}]}
```

Фильтрация по исключённым книгам — **пост-хок** (после запроса к ChromaDB), в `rag_engine.py`.

---

## Модель эмбеддингов

`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` — мультиязычная, хорошо работает с русским текстом.

Singleton в `kb/embedder.py`. При старте вызывается `warmup_embedder()` чтобы загрузить модель заранее и не тормозить первый запрос пользователя.

---

## Middleware

`AuthMiddleware` навешивается на `dp.message` и `dp.callback_query`.

Логика:
1. Если сообщение начинается с `/start` — пропускает без проверки (whitelist проверяется внутри хендлера)
2. Иначе: проверяет `whitelist` в БД
3. Если в whitelist: загружает `db_user = await db.get_user(user.id)` и кладёт в `data["db_user"]` и `data["config"]`

Хендлеры получают `db_user: dict = None` и `config: Config = None` через dependency injection aiogram.

---

## FSM-состояния (states/fsm.py)

| StatesGroup | States | Используется в |
|-------------|--------|----------------|
| `Onboarding` | `waiting_role`, `waiting_birthdate` | handlers/start.py |
| `UploadPDF` | `waiting_age_range` | handlers/pdf_upload.py |
| `EditProfile` | `waiting_field`, `waiting_value` | handlers/start.py |
| `SetDate` | `waiting_birthdate` | handlers/start.py |
| `AdminPanel` | `waiting_add_id`, `waiting_remove_id`, `waiting_broadcast_text` | handlers/admin.py |

---

## Порядок роутеров в main.py

**Критично!** aiogram проверяет хендлеры в порядке регистрации. Общий chat-хендлер (`F.text & ~F.text.startswith("/")`) должен быть последним:

```python
dp.include_router(start.router)     # /start, /setdate, /myprofile
dp.include_router(admin.router)     # /admin + библиотека книг
dp.include_router(help.router)      # ❓ Помощь — ОБЯЗАТЕЛЬНО перед chat
dp.include_router(pdf_upload.router)
dp.include_router(my_child.router)  # 👶 Расскажи о дочке
dp.include_router(chat.router)      # catch-all — ПОСЛЕДНИЙ
```

---

## Клавиатуры (keyboards/main_kb.py)

| Функция | Тип | Описание |
|---------|-----|----------|
| `main_menu(search_mode)` | ReplyKeyboard | Главное меню, кнопка режима динамическая |
| `role_keyboard()` | InlineKeyboard | Папа / Мама / Оба |
| `gender_keyboard()` | InlineKeyboard | Мальчик / Девочка |
| `age_range_keyboard()` | InlineKeyboard | Возрастные диапазоны + авто |
| `library_keyboard(...)` | InlineKeyboard | Список книг с include/exclude |
| `profile_keyboard()` | InlineKeyboard | Поля профиля для редактирования |
| `admin_keyboard(whitelist)` | InlineKeyboard | Панель администратора |
| `confirm_delete_keyboard(book_id)` | InlineKeyboard | Подтверждение удаления книги |

---

## Системный промпт (services/claude_client.py)

`_build_system_prompt()` динамически собирает промпт из:
- Роли пользователя (папа/мама/оба) → разный стиль общения
- Возраста ребёнка
- Личного контекста (`child_context` JSON)
- Чанков из KB
- Результатов Brave Search (если режим kb_internet)

Ключевые ограничения в промпте:
- Только русский язык
- Запрет физических наказаний, стыжения
- Приоритет — загруженная библиотека
- Нет медицинских диагнозов

---

## Планировщик (services/scheduler.py)

Ежедневно в 10:00 проверяет всех пользователей с заполненной датой рождения ребёнка.
Если ребёнок скоро выйдет из возрастного диапазона книги (< 60 месяцев до max), — отправляет напоминание.
Каждое уведомление отправляется только один раз (таблица `age_notifications`).
При обновлении даты рождения — `age_notifications` для пользователя сбрасываются.

---

## Режимы поиска

| Режим | Описание |
|-------|----------|
| `kb_only` (по умолчанию) | Только RAG по загруженным книгам |
| `kb_internet` | RAG + Brave Search API, результаты добавляются в системный промпт |

Переключается кнопкой в главном меню (toggle).

---

## Переменные окружения (.env)

```env
BOT_TOKEN=                          # Telegram Bot API token
ADMIN_TELEGRAM_ID=                  # Telegram ID администратора
WHITELIST_IDS=id1,id2               # Начальный whitelist (через запятую)
ANTHROPIC_API_KEY=                  # Claude API key
BRAVE_API_KEY=                      # Brave Search API key (для режима kb_internet)
DATA_DIR=/home/brain/projects/parentbot/data
DB_PATH=/home/brain/projects/parentbot/db/parentbot.db
CHROMA_DIR=/home/brain/projects/parentbot/data/chroma
CLAUDE_MODEL=claude-sonnet-4-6      # Модель Claude
MAX_HISTORY_MESSAGES=20             # Глубина контекста диалога
LOG_LEVEL=INFO
```

Начальный whitelist из `.env` и `ADMIN_TELEGRAM_ID` записываются в БД при `init_db()`. Добавлять новых пользователей можно через `/admin` прямо в боте.

---

## Деплой

**Сервер:** `brain-server` (`ssh brain-server`)
**Путь:** `/home/brain/projects/parentbot/`
**Сервис:** `systemctl --user restart parentbot`
**Логи:** `journalctl --user -u parentbot -n 50 --no-pager`

### Обновить файлы на сервере
```bash
scp -r handlers/ services/ kb/ db/ keyboards/ middlewares/ states/ utils/ \
    main.py config.py brain-server:/home/brain/projects/parentbot/
ssh brain-server "systemctl --user restart parentbot"
```

### Первый деплой
```bash
# На сервере
cd /home/brain/projects/parentbot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
mkdir -p ~/.config/systemd/user
cp parentbot.service ~/.config/systemd/user/
systemctl --user enable parentbot
systemctl --user start parentbot
```

---

## Функциональность по ролям

### Обычный пользователь
- Онбординг: выбор роли → дата рождения → главное меню
- Редактирование профиля: роль, пол/имя/характер/особенности ребёнка, дата рождения
- Вопрос в свободной форме → RAG + Claude
- "Расскажи о дочке/сыне" → автоматический запрос под текущий возраст
- Загрузка личных PDF (scope=personal)
- Управление библиотекой: включать/отключать книги для себя
- Переключение режима поиска

### Администратор (`is_admin=1`)
- Все функции пользователя
- `/admin` → панель с кнопками:
  - Добавить/удалить пользователя из whitelist
  - Посмотреть список whitelist
  - Статистика KB (книги, фрагменты, пользователи)
  - Рассылка всем активным пользователям
- Загружаемые книги попадают в **общую** базу (scope=shared), доступную всем

---

## Известные особенности и ограничения

1. **PDF только текстовые** — сканы (без слоя текста) не поддерживаются (PyMuPDF)
2. **Лимит PDF** — 20 MB (ограничение Telegram Bot API при скачивании)
3. **Возрастной диапазон** — в месяцах (0–999 = любой возраст)
4. **История диалога** — последние N сообщений (настраивается через MAX_HISTORY_MESSAGES)
5. **ChromaDB** — данные не шифруются, хранятся на диске сервера
6. **Кнопка "👶 Расскажи о дочке"** — текст кнопки хардкодный, не зависит от пола ребёнка (можно улучшить)

---

## Как добавить новую функцию

### Новый хендлер
1. Создать файл в `handlers/` или добавить к существующему
2. Определить `router = Router()`
3. Зарегистрировать в `main.py` через `dp.include_router()` — **до** `chat.router`

### Новое FSM-состояние
1. Добавить класс в `states/fsm.py`
2. В хендлере: `await state.set_state(MyState.waiting_something)`
3. Хендлер для состояния: `@router.message(MyState.waiting_something)`

### Новое поле в профиле пользователя
- Поля хранятся в `users.child_context` как JSON
- Добавить кнопку в `keyboards/main_kb.py → profile_keyboard()`
- Добавить ветку в `handlers/start.py → profile_edit_start()`
- Показать в `handlers/start.py → cmd_myprofile()`

### Новая таблица БД
1. Добавить `CREATE TABLE IF NOT EXISTS ...` в `db/schema.py → SCHEMA`
2. Добавить функции в `db/queries.py`
