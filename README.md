# FITR_allInclusive
vibe-coded project to administrate course works and 3d model purchases

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Создайте в корне проекта файл `.env` (он в `.gitignore`):

```
BOT_TOKEN=<токен от @BotFather>
```

Запуск бота:

```bash
python -m bot
```

## Структура

```
bot/
├── __main__.py     # точка входа
├── config.py       # настройки из .env
├── handlers/       # обработчики команд и сообщений (роутеры)
├── keyboards/      # клавиатуры
├── middlewares/    # middleware
└── services/       # бизнес-логика
```
