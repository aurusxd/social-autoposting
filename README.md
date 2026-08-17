# Social autoposting bot

На текущем этапе реализован UI Telegram-бота: сбор черновика, предпросмотр,
мультивыбор площадок и подтверждение. Очередь и реальные публикации пока не
подключены.

## Запуск

1. Установите Python 3.12+ и зависимости: `pip install -e ".[dev]"`.
2. Скопируйте `.env.example` в `.env`.
3. Заполните `TELEGRAM_BOT_TOKEN` и `TELEGRAM_OWNER_ID` владельца бота.
4. Укажите реальные цели в `config.yaml`.
5. Запустите `python main.py`.

Команды бота: `/start`, `/new`, `/cancel`.
