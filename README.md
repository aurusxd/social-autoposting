# Social autoposting bot

Telegram-бот принимает текст, фото и видео, сохраняет пост в SQLite и создаёт
задания публикации. Celery worker получает задания через Redis и публикует их в
Telegram-каналы. Для WhatsApp, Instagram и TikTok пока есть только цели в UI:
соответствующие паблишеры ещё не реализованы.

## Подготовка

1. Установите Python 3.12+ и зависимости:
   `python -m pip install -e ".[dev]"`.
2. Скопируйте `.env.example` в `.env`.
3. Заполните `TELEGRAM_BOT_TOKEN` и `TELEGRAM_OWNER_ID`.
4. Укажите Telegram-каналы в `config.yaml` и добавьте бота в них как
   администратора с правом публикации.
5. Примените миграции: `alembic upgrade head`.
6. Запустите Redis, например через Docker:
   `docker run --name social-autoposting-redis -p 6379:6379 -d redis:7-alpine`.

## Запуск

Откройте два терминала из корня проекта.

В первом запустите Celery worker (на Windows нужен пул `solo`):

```powershell
.\.venv\Scripts\celery.exe -A app.worker.celery_app:celery worker --loglevel=INFO --pool=solo
```

Во втором запустите Telegram-бота:

```powershell
.\.venv\Scripts\python.exe main.py
```

Команды бота: `/start`, `/new`, `/cancel`.

## Проверка Celery

При работающих Redis и worker отправьте тестовую задачу:

```powershell
.\.venv\Scripts\python.exe -c "from app.worker.tasks import healthcheck; print(healthcheck.delay().id)"
```

В логе worker должна появиться строка `Celery worker is alive`. Рабочие задачи
называются `worker.healthcheck` и `worker.publish_job`.

Очередь публикаций хранится в `publish_jobs`. При временной ошибке выполняются
до трёх попыток с задержками 10 и 60 секунд. Зависшие дольше 15 минут задания
возвращаются в очередь при запуске worker.

## Развёртывание на сервере через Docker Compose

На сервере нужны только Docker Engine и Compose plugin. После загрузки проекта:

```bash
cp .env.example .env
nano .env
nano config.yaml
docker compose up -d --build
docker compose ps
```

Compose сначала применяет Alembic-миграции, затем запускает Redis, Celery worker
и Telegram-бота. Redis доступен только внутри compose-сети. SQLite, медиа и
данные Redis сохраняются в именованных volumes и переживают пересоздание
контейнеров.

Посмотреть логи:

```bash
docker compose logs -f bot worker
```

Обновить приложение после получения нового кода:

```bash
docker compose up -d --build
```

Остановить контейнеры без удаления данных:

```bash
docker compose down
```

Не используйте `docker compose down -v`, если хотите сохранить базу, медиа и
очередь Redis.
