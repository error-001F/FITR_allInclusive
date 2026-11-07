# FITR_allInclusive — Telegram-бот заказов 3D-печати и разработки моделей

aiogram 3, SQLAlchemy 2 (async) + SQLite, Python ≥ 3.11 (локально 3.14, на VPS 3.12).
ТЗ — `bot.docx`. Пользователь общается по-русски; комментарии и тексты бота — на русском.

## Запуск и деплой
- Локально: `.venv/bin/python -m bot` (из этой папки). Один токен — один экземпляр бота
  (иначе `TelegramConflictError`).
- Прод — VPS, systemd-сервис `fitr-bot`: `git pull && systemctl restart fitr-bot`,
  лог `journalctl -u fitr-bot -f`.
- `.env` (BOT_TOKEN, ADMIN_IDS, MASTER_USERNAME, PAYMENT_CARD/IBAN/RECIPIENT) **не читать**
  (deny в `.claude/settings.json`); проверять только «задано / не задано» через `get_settings()`.
- Данные: `data/bot.db`, журнал `data/orders_history.log` (в git не попадают).

## Структура `bot/`
- `catalog.py` — цены, справочники, тексты, статусы, `calculate_print_price` (единый источник).
- `config.py` — `Settings` (pydantic-settings).
- `database/` — модели (`Order`, `AdminCard`, `ChatMessage`); миграции колонок вручную в
  `engine.py: _add_missing_columns` (Alembic нет).
- `handlers/` — `common` (меню, «Мои заказы»), `orders` (оформление), `edit` (клиент правит
  заказ), `payment`, `chat` (переписка), `admin` (мастера). **Порядок роутеров в
  `handlers/__init__.py` важен** (общий `/cancel` в `orders` должен срабатывать раньше
  «принять любой ввод»).
- `keyboards/`, `services/` (orders, notifications, formatting, history, maintenance, chat, payment),
  `states.py` (FSM).

## Правила кода
- Пользовательский ввод в HTML-сообщениях — через `html.quote`.
- Значения из callback_data проверять по справочникам (их можно подделать); у каждой фичи свой
  префикс CallbackData.
- Номера заказов **перенумеровываются** после удаления (выданные — сразу, отклонённые — через сутки):
  номер в кнопке — только подпись, проверять владельца/статус/`created_at`.
- Тексты кнопок меню меняются → старые тексты добавлять в `*_TEXTS` в `keyboards/menu.py`.
- Не хранить `.bak`-файлы в проекте.
- Коммиты и push — только по просьбе пользователя.

## Проверка
Офлайн-симуляция без `.env`: тестовый стенд в scratchpad (`harness.py`: фейковая `BaseSession`,
`create_dispatcher` из `bot/__main__.py`, временная SQLite, `Settings(_env_file=None, ...)`),
сценарии через `dp.feed_update`. После изменений — регрессия соседних сценариев, затем проверка
пользователем в Telegram. README.md описывает настройки и функции, ARCHITECTURE.md — устройство
кода для пользователя (сценарии, рецепты изменений) — обновлять оба вместе с кодом.
