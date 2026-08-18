# Social autoposting bot

Telegram-бот принимает текст, фото и видео, сохраняет пост в SQLite и создаёт
задания публикации. Celery worker получает задания через Redis и публикует их в
Telegram-каналы и Instagram. Для WhatsApp и TikTok пока есть только цели в UI:
соответствующие паблишеры ещё не реализованы.

## Подготовка

1. Установите Python 3.12+ и зависимости:
   `python -m pip install -e ".[dev]"`.
2. Скопируйте `.env.example` в `.env`.
3. Заполните `TELEGRAM_BOT_TOKEN` и `TELEGRAM_OWNER_ID`. Если Instagram включён
   в `config.yaml`, также заполните `INSTAGRAM_USERNAME` и `INSTAGRAM_PASSWORD`.
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

## Instagram

Публикатор поддерживает:

- одиночное JPG-фото или MP4-видео в ленте;
- карусель из JPG/MP4 в ленте;
- одну JPG-фотографию или одно MP4-видео в Story.

Текст без медиа и несколько файлов в одной Story отклоняются до создания
задания. Максимальная длина подписи — 2200 символов.

Настройки в `.env`:

```dotenv
INSTAGRAM_USERNAME=
INSTAGRAM_PASSWORD=
INSTAGRAM_TOTP_SECRET=
INSTAGRAM_SESSION_PATH=data/instagram_session.json
INSTAGRAM_PROXY=
INSTAGRAM_REQUEST_TIMEOUT=30
```

После первого успешного входа `instagrapi`-сессия сохраняется в
`data/instagram_session.json` и используется повторно. В Docker этот файл
попадает в постоянный volume `app_data`. Не добавляйте файл сессии в Git и не
копируйте его посторонним.

Если на аккаунте включена двухфакторная аутентификация через приложение,
укажите её TOTP secret в `INSTAGRAM_TOTP_SECRET`. Оставьте переменную пустой,
если 2FA не используется.

`instagrapi` использует неофициальный Instagram Private API. Первый вход может
потребовать подтверждения в официальном приложении, а слишком частые действия
могут вызвать временное ограничение или блокировку аккаунта. При ответе о
лимите Celery ждёт 15 минут перед следующей попыткой.

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
и Telegram-бота. Redis доступен только внутри compose-сети. SQLite,
Instagram-сессия, медиа и данные Redis сохраняются в именованных volumes и
переживают пересоздание контейнеров.

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
