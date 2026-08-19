# Social autoposting bot

Telegram-бот принимает текст, фото и видео, сохраняет пост в SQLite и создаёт
задания публикации. Celery worker получает задания через Redis и публикует их в
Telegram-каналы, выбранные WhatsApp-группы/каналы, Instagram и TikTok.

## Подготовка

1. Установите Python 3.12+ и зависимости:
   `python -m pip install -e ".[dev]"`.
2. Скопируйте `.env.example` в `.env`.
3. Заполните `TELEGRAM_BOT_TOKEN` и `TELEGRAM_OWNER_ID`. Для включённых в
   `config.yaml` WhatsApp, Instagram и TikTok также заполните переменные
   соответствующих интеграций из разделов ниже.
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

## WhatsApp через OpenWA

Публикатор отправляет текст, JPG/PNG/GIF/WebP и MP4/3GP в выбранные группы
(`...@g.us`) и каналы (`...@newsletter`). Несколько медиа отправляются по
очереди, подпись прикрепляется к первому файлу. Подпись длиннее 1024 символов
сначала отправляется отдельным текстовым сообщением. Если часть поста уже
отправлена, автоматический повтор отключается, чтобы не создавать дубли.

OpenWA использует неофициальные WhatsApp-клиенты. Всегда остаётся риск
ограничения или блокировки номера; используйте отдельный номер и не делайте
массовые рассылки незнакомым получателям. Compose по умолчанию включает Baileys,
потому что `whatsapp-web.js` сейчас не умеет отправлять медиа в каналы. Если
нужны только группы и важнее снизить риск, установите
`OPENWA_ENGINE_TYPE=whatsapp-web.js`.

Первичная настройка:

1. Создайте ключ длиной не менее 32 символов и сохраните его в
   `WHATSAPP_API_KEY`:

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
   ```

2. Запустите OpenWA: `docker compose up -d openwa`.
3. Откройте `http://127.0.0.1:2785`, введите API-ключ, создайте сессию
   `social-autoposting`, запустите её и отсканируйте QR-код в WhatsApp.
4. Скопируйте UUID сессии в `WHATSAPP_SESSION_ID` внутри `.env`.
5. Получите ID групп через `GET /api/sessions/{sessionId}/groups`. ID канала
   можно получить из ответа `POST /api/sessions/{sessionId}/channels/subscribe`
   с его invite-кодом. Запишите нужные JID и названия в `config.yaml`.
6. Перезапустите приложение: `docker compose up -d --build`.

Основные настройки:

```dotenv
WHATSAPP_API_URL=http://localhost:2785/api
WHATSAPP_API_KEY=
WHATSAPP_SESSION_ID=
WHATSAPP_REQUEST_TIMEOUT=120
WHATSAPP_MEDIA_MAX_BYTES=104857600
OPENWA_ENGINE_TYPE=baileys
OPENWA_BIND_HOST=127.0.0.1
OPENWA_PORT=2785
```

В Docker медиа передаются по закрытому `media-server` внутри сети Compose, без
Base64 и без публикации файлов наружу. При нативном запуске без
`WHATSAPP_MEDIA_BASE_URL` используется Base64. Порт панели OpenWA по умолчанию
доступен только на localhost; для удалённого сервера используйте SSH-туннель,
а не открывайте HTTP API в интернет.

## Instagram через Zernio

Instagram публикуется через официальный API, который предоставляет Zernio.
Подключите в Zernio профессиональный Instagram-аккаунт типа Business или
Creator. Публикатор поддерживает:

- одиночное JPG/PNG-фото в ленте;
- одиночное MP4/MOV-видео как Reel с показом в ленте;
- смешанную карусель до 10 фото и видео;
- одно фото или видео в Story.

Текст без медиа и несколько файлов в одной Story отклоняются до создания
задания. Максимальная длина подписи — 2200 символов. Instagram API не
показывает подпись в Story, поэтому для этой цели бот её не отправляет.

Настройки в `.env`:

```dotenv
ZERNIO_API_KEY=
ZERNIO_INSTAGRAM_ACCOUNT_ID=
ZERNIO_API_BASE_URL=https://zernio.com/api
ZERNIO_REQUEST_TIMEOUT=120
```

`ZERNIO_INSTAGRAM_ACCOUNT_ID` — поле `_id` Instagram-аккаунта из ответа
`GET https://zernio.com/api/v1/accounts?platform=instagram&status=connected`.
API-ключ и общие настройки Zernio используются совместно с TikTok. Медиа сначала
загружается по временной ссылке Zernio, затем worker создаёт публикацию со
стабильным `x-request-id`, чтобы безопасно повторять запрос после сетевого сбоя.

## TikTok через Zernio

TikTok публикуется через официальный Content Posting API, который предоставляет
Zernio. Собственный TikTok Developer App, сайт и домен не нужны. Подключите
TikTok-аккаунт в Zernio и добавьте в `.env`:

```dotenv
ZERNIO_API_KEY=
ZERNIO_TIKTOK_ACCOUNT_ID=
ZERNIO_API_BASE_URL=https://zernio.com/api
ZERNIO_REQUEST_TIMEOUT=120
ZERNIO_TIKTOK_PRIVACY_LEVEL=PUBLIC_TO_EVERYONE
```

`ZERNIO_TIKTOK_ACCOUNT_ID` — поле `_id` TikTok-аккаунта из ответа
`GET https://zernio.com/api/v1/accounts?platform=tiktok&status=connected`.
Поддерживаются одно видео MP4/MOV/WebM или фотокарусель JPG/PNG/WebP. Фото и
видео в одной публикации смешивать нельзя. Перед публикацией worker получает
временную ссылку Zernio, загружает туда локальные файлы и передаёт полученные
URL в задачу TikTok. Для повторов используется стабильный `x-request-id`, чтобы
сетевой сбой не создавал дубли.

По умолчанию публикация публичная. Если аккаунт не разрешает публичный уровень,
укажите в `ZERNIO_TIKTOK_PRIVACY_LEVEL` одно из значений, которое доступно ему:
`MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR` или `SELF_ONLY`.

## Видео больше 20 МБ из Telegram

Облачный Telegram Bot API разрешает боту скачивать файлы только до 20 МБ.
Docker Compose запускает локальный Bot API 10.2 в режиме `--local`: скачивание
становится неограниченным, а загрузка поддерживается до 2000 МБ.

Получите `api_id` и `api_hash` в разделе **API development tools** на
`https://my.telegram.org` и добавьте их в `.env`:

```dotenv
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
```

Перед самым первым запуском локального API нужно один раз отключить bot token
от облачного Bot API. Сначала остановите старый bot/worker, затем выполните в
PowerShell из корня проекта:

```powershell
docker compose stop bot worker
$telegramBotToken = ((Get-Content .env | Where-Object {
    $_ -match '^TELEGRAM_BOT_TOKEN='
} | Select-Object -First 1) -split '=', 2)[1].Trim()
Invoke-RestMethod -Method Post `
    -Uri "https://api.telegram.org/bot$telegramBotToken/logOut"
Remove-Variable telegramBotToken
docker compose up -d --build
```

`logOut` выполняется только при первом переходе с облачного API. При обычных
перезапусках повторять его не нужно. Данные локального API находятся в volume
`telegram_bot_api_data`; не удаляйте его через `docker compose down -v`.

Большие входящие файлы читаются напрямую из этого volume, поэтому режим
поддерживается при запуске приложения через Docker Compose. При нативном запуске
`python main.py` используется облачный API и сохраняется лимит 20 МБ.

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

Compose запускает локальные Telegram Bot API, OpenWA, внутреннюю раздачу медиа
и Redis, применяет Alembic-миграции, затем запускает Celery worker и
Telegram-бота. Внутренние API, media-server и Redis не публикуются наружу;
панель OpenWA привязана к `127.0.0.1`. SQLite, медиа, сессии WhatsApp, данные
Redis и локального Telegram API сохраняются в именованных volumes и переживают
пересоздание контейнеров.

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
