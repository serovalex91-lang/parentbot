# Деплой ParentBot

## Где что
- Сервер: `brain@46.17.97.188` — SSH: `ssh -F ~/workspace/.ssh/config brain`
- Путь на сервере: `/home/brain/projects/parentbot` (НЕ `~/parentbot`)
- venv сервера: `.venv/` (Python 3.10)
- Сервис: **user-level** systemd → `systemctl --user restart parentbot.service`
  (НЕ `sudo systemctl` — юнит лежит в `~/.config/systemd/user/parentbot.service`.
  `systemctl is-active parentbot.service` без `--user` покажет ложный `inactive`.)
- БД профилей: `db/parentbot.db` (в .gitignore, git её не трогает). Рядом `.db-shm`/`.db-wal` — рабочие файлы SQLite WAL, живые, не удалять.

## Аутентификация git
- Локальный репо (`~/projects/parentbot`): remote = чистый https, credential.helper берёт токен из `$GITHUB_TOKEN` (env), нигде не хранится.
  При пересоздании токена в /settings → работает сразу, ничего править не надо.
- На сервере `$GITHUB_TOKEN` НЕТ. Fetch/pull делать через временный URL с токеном, инжектируемым из ЛОКАЛЬНОГО env по ssh:
  ```
  git fetch "https://x-access-token:${GITHUB_TOKEN}@github.com/serovalex91-lang/parentbot.git" main
  ```
  Токен на сервере не сохраняется (temp-URL, не в remote).

## Порядок деплоя (проверенный 2026-07-24)
```
# локально: смёржить фичу в main + push
git checkout main && git merge --ff-only agent/<фича> && git push

# на сервере:
ssh -F ~/workspace/.ssh/config brain
cd /home/brain/projects/parentbot
cp db/parentbot.db db/parentbot.db.bak-deploy-$(date +%Y%m%d-%H%M%S)   # 1. бэкап БД
git fetch "https://x-access-token:${GITHUB_TOKEN}@github.com/serovalex91-lang/parentbot.git" main
git stash -u                                                            # 2. убрать локальные правки/untracked с пути (страховка)
git merge --ff-only FETCH_HEAD                                          # 3. обновить (только если HEAD — предок origin)
systemctl --user restart parentbot.service                             # 4. рестарт (~10 сек даунтайм, грузит ML-модель)
```
Проверка успеха в логе: `journalctl --user -u parentbot.service` → «ParentBot запущен» + «Модель загружена» + «Планировщик запущен», без Traceback.

## Правила (чтобы не повторять расследование)
- **НЕ править код прямо на сервере.** Разработка → commit → push → deploy на сервере.
  Иначе на проде копится код, которого нет в git (было с 21 мая по 24 июля — уцелело случайно, правки совпали с origin).
- `git merge --ff-only` — только если `git merge-base --is-ancestor HEAD FETCH_HEAD` истинно. Если разошлись — разбираться вручную, НЕ reset --hard.
- Mac-мусор (`._*` AppleDouble) появляется при копировании через Mac/rsync — безвреден, чистить `git stash -u` + drop или вручную.
- reset --hard на сервере не использовать (живой прод + БД).
