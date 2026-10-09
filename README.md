# FITR_allInclusive
vibe-coded project to administrate course works and 3d model purchases

## Запуск бота

Все команды выполняются из корня проекта (папка с `requirements.txt`).

Первый раз (или после `git pull`, если менялся `requirements.txt`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Запуск:

```bash
source .venv/bin/activate
python -m bot
```

Бот работает, если в логе есть строка `Run polling for bot @...`. Остановить — `Ctrl+C`.

Одновременно может работать только один экземпляр бота: ошибка `TelegramConflictError`
значит, что он уже запущен где-то ещё. `.env` читается только при старте — после правки
перезапустите бота.

## Настройки (`.env`)

Файл `.env` в корне проекта, в git не попадает:

```
BOT_TOKEN=токен от @BotFather
MASTER_USERNAME=ник мастера без @
ADMIN_ID=числовой Telegram id мастера (узнать у @userinfobot)
PAYMENT_DETAILS=реквизиты для оплаты
```

Обязателен только `BOT_TOKEN`.

## База данных

Заказы хранятся в SQLite: `data/bot.db` (создаётся при первом запуске, в git не попадает).

Открыть консоль базы:

```bash
sqlite3 data/bot.db
```

Полезные команды внутри консоли:

```sql
.headers on
.mode column
.tables                                          -- список таблиц
.schema orders                                   -- структура таблицы заказов
SELECT * FROM orders ORDER BY id DESC LIMIT 10;  -- последние 10 заказов
SELECT id, customer_name, phone, status FROM orders WHERE status = 'new';
.quit
```

Быстрый просмотр без входа в консоль:

```bash
sqlite3 -header -column data/bot.db "SELECT * FROM orders ORDER BY id DESC LIMIT 10;"
```

Для удобного просмотра в VS Code подойдёт расширение «SQLite Viewer» — откройте `data/bot.db`.
