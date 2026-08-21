# Social autoposting bot

Telegram-бот принимает текст, фото и видео, сохраняет пост в SQLite и создаёт
задания публикации. Celery worker получает задания через Redis и публикует их в
Telegram-каналы, выбранные WhatsApp-группы/каналы, Instagram и TikTok.

Подробная инструкция для передачи заказчику и запуска на сервере:
[`DEPLOYMENT.md`](DEPLOYMENT.md).

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

## WhatsApp: выбор движка

У WhatsApp два взаимоисключающих движка, переключаются переменной `.env`:

```dotenv
WHATSAPP_ENGINE=openwa   # или cloud
```

| | `openwa` | `cloud` |
|---|---|---|
| Официальность | неофициальный клиент | официальный Cloud API от Meta |
| Группы | любые, без лимита участников | только созданные через Groups API, **до 8 участников** |
| Каналы (`@newsletter`) | да | **нет, API не существует** |
| Личные сообщения | нет | да (`whatsapp.contacts`) |
| Требования | номер + QR-код | Official Business Account, номер в Meta |
| Риск | блокировка номера | нет |

`openwa` остаётся значением по умолчанию: только он умеет писать в каналы и в
большие группы. Cloud API имеет смысл, если получателей мало и важна
легальность. Каналы в `config.yaml` при `WHATSAPP_ENGINE=cloud` вызовут ошибку
конфигурации на старте — это не баг, официального API у каналов нет.

## WhatsApp через Cloud API

Движок `cloud` публикует через официальный WhatsApp Business Cloud API.
Требуется **Official Business Account**: для номеров из приложения WhatsApp
Business и для Multi-solution Conversations Groups API недоступен.

```dotenv
WHATSAPP_ENGINE=cloud
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_CLOUD_API_BASE_URL=https://graph.facebook.com
WHATSAPP_API_VERSION=v25.0
WHATSAPP_REQUEST_TIMEOUT=120
WHATSAPP_MEDIA_MAX_BYTES=16777216
```

Цели в `config.yaml`:

```yaml
whatsapp:
  groups:
    - jid: "120363000000000000"   # id из Groups API, без @g.us
      name: "Группа клиентов"
  contacts:
    - phone: "+79001234567"
      name: "Клиент"
```

Группы заводятся через Groups API (`POST /{phone-number-id}/groups`), участники
вступают только сами по инвайт-ссылке — эндпоинта «добавить участника» нет.
Лимиты Meta: 8 участников на группу, до 10 000 групп на номер, один бизнес на
группу.

Публикация идёт в `POST /{phone-number-id}/messages` с `recipient_type` `group`
или `individual`. Медиа сначала загружается в `POST /{phone-number-id}/media` и
дальше передаётся по `id`, поэтому публичный `MEDIA_PUBLIC_BASE_URL` этому
движку не нужен. Ограничения Cloud API: фото до 5 МБ (JPEG/PNG), видео до 16 МБ
(MP4/3GP) — они жёстче, чем у OpenWA. Как и в OpenWA, подпись длиннее 1024
символов уходит отдельным сообщением, а при частичной отправке автоповтор
отключается.

Свободный текст вне 24-часового окна общения Meta отклонит — понадобится
утверждённый шаблон. Отправка шаблонов в этом паблишере не реализована.

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
WHATSAPP_ENGINE=openwa
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

## Публичный медиа-сервер

Instagram и TikTok забирают файлы **по публичной HTTPS-ссылке**: загрузить их
байтами в API нельзя (единственное исключение — видео TikTok, оно грузится
напрямую). Поэтому `media-server` из Compose должен быть доступен из интернета.

```dotenv
MEDIA_PUBLIC_BASE_URL=https://media.example.com
MEDIA_ROOT=/app/media
```

`MEDIA_PUBLIC_BASE_URL` обязателен, когда включён Instagram или TikTok, и обязан
начинаться с `https://` — оба API отклоняют `http://`. Compose публикует nginx на
`${MEDIA_SERVER_BIND_HOST:-127.0.0.1}:${MEDIA_SERVER_PORT:-8080}`; поставьте
перед ним обратный прокси (nginx, Caddy, Traefik) с сертификатом Let's Encrypt.
Домен из `MEDIA_PUBLIC_BASE_URL` нужно подтвердить в TikTok Developer Portal
(**Manage apps → URL properties**), иначе фотопубликации получат
`url_ownership_unverified`.

WhatsApp продолжает ходить к тому же nginx по внутреннему адресу
`WHATSAPP_MEDIA_BASE_URL=http://media-server`, наружу для него ничего не нужно.

## Instagram через Graph API

Instagram публикуется напрямую через официальный Instagram Graph API
(Content Publishing). Нужен профессиональный аккаунт Business или Creator,
привязанный к странице Facebook, и приложение Meta с разрешениями
`instagram_basic`, `instagram_content_publish`, `pages_read_engagement`.

```dotenv
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_USER_ID=
INSTAGRAM_API_BASE_URL=https://graph.facebook.com
INSTAGRAM_API_VERSION=v25.0
INSTAGRAM_REQUEST_TIMEOUT=120
INSTAGRAM_STATUS_POLL_INTERVAL=5
INSTAGRAM_STATUS_POLL_ATTEMPTS=60
```

`INSTAGRAM_USER_ID` — идентификатор Instagram-аккаунта (IG User ID), его выдаёт
`GET /{page-id}?fields=instagram_business_account`. В `INSTAGRAM_ACCESS_TOKEN`
положите **токен системного пользователя** Meta Business: обычный long-lived
токен истекает через 60 дней и потребует ручной замены.

Публикатор поддерживает:

- одиночное JPEG-фото в ленте (`media_type=IMAGE`);
- одиночное MP4/MOV-видео как Reel (`media_type=REELS`, `share_to_feed=true`);
- карусель до 10 фото и видео (элементы создаются с `is_carousel_item=true`);
- одно фото или видео в Story (`media_type=STORIES`).

Каждый контейнер опрашивается по `status_code`, пока Instagram не завершит
обработку, и только потом вызывается `media_publish`. Максимальная длина
подписи — 2200 символов; в Story подпись не отправляется, её там нет в API.
Лимит аккаунта — 100 публикаций через API за 24 часа.

**PNG больше не поддерживается**: Graph API принимает для фото только JPEG.

## TikTok через Content Posting API

TikTok публикуется напрямую через официальный Content Posting API в режиме
Direct Post. Нужно собственное приложение в TikTok Developer Portal со скоупами
`video.publish` и `user.info.basic`, прошедшее аудит.

```dotenv
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_REFRESH_TOKEN=
TIKTOK_API_BASE_URL=https://open.tiktokapis.com
TIKTOK_REQUEST_TIMEOUT=300
TIKTOK_PRIVACY_LEVEL=PUBLIC_TO_EVERYONE
TIKTOK_DISABLE_COMMENT=false
TIKTOK_DISABLE_DUET=false
TIKTOK_DISABLE_STITCH=false
TIKTOK_AUTO_ADD_MUSIC=true
TIKTOK_UPLOAD_CHUNK_SIZE=10485760
TIKTOK_STATUS_POLL_INTERVAL=5
TIKTOK_STATUS_POLL_ATTEMPTS=60
```

`TIKTOK_REFRESH_TOKEN` получается один раз при OAuth-авторизации аккаунта.
Access-токен живёт 24 часа, поэтому worker обновляет его сам и складывает
результат в таблицу `oauth_tokens`. TikTok при обновлении может выдать **новый**
refresh-токен — он тоже сохраняется в БД, значение из `.env` остаётся запасным:
если сохранённый токен отвергнут, worker один раз повторит обновление с ним.

Что публикуется:

- одно видео MP4/MOV/WebM до 4 ГБ — грузится байтами (`FILE_UPLOAD`), чанками
  по `TIKTOK_UPLOAD_CHUNK_SIZE` (допустимо 5–64 МБ), файл до 64 МБ уходит одним
  куском;
- фотокарусель JPG/PNG/WebP до 35 файлов — забирается TikTok по публичным
  ссылкам (`PULL_FROM_URL`), другого способа для фото в API нет.

Смешивать фото и видео в одной публикации нельзя. Перед каждой публикацией
worker вызывает `creator_info/query` — это требование API — и оттуда же берёт
запреты аккаунта на комментарии, дуэты и стичи. Если аккаунт не разрешает
уровень из `TIKTOK_PRIVACY_LEVEL`, публикация падает с понятной ошибкой до
загрузки файла; допустимые значения: `PUBLIC_TO_EVERYONE`,
`MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR`, `SELF_ONLY`.

После загрузки worker опрашивает `status/fetch` до `PUBLISH_COMPLETE`. Статус
`FAILED` считается окончательной ошибкой, а истечение таймаута опроса —
успехом: пост уже принят TikTok, и повтор создал бы дубль.

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
