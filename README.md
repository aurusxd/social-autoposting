# Social autoposting

Веб-панель принимает текст, фото и видео, сохраняет пост в SQLite и создаёт
задания публикации. Celery worker получает задания через Redis и публикует их в
Telegram-каналы, выбранные WhatsApp-группы/каналы, Instagram и TikTok.

Панель работает и на компьютере, и на телефоне: вёрстка адаптивная, тема
следует настройке системы. Telegram остаётся площадкой публикации — заменён
только интерфейс управления.

## Панель управления

- **Новый пост** — текст, загрузка файлов перетаскиванием или через диалог,
  порядок медиа стрелками, выбор площадок галочками с кнопкой «Все» у каждой
  платформы и обновлением списка WhatsApp-чатов на лету.
- **История** — публикации со статусом по каждой площадке, фильтр по статусу,
  постраничный список.
- **Карточка поста** — текст, вложения, состояние каждого задания с текстом
  ошибки, повтор неудачных заданий и удаление поста вместе с файлами.

Доступ закрыт логином и паролем: сессия хранится в подписанной cookie
(`HttpOnly`, `SameSite=Lax`), после пяти неудачных попыток вход блокируется на
пять минут.

## Подготовка

1. Установите Python 3.12+ и зависимости:
   `python -m pip install -e ".[dev]"`.
2. Скопируйте `.env.example` в `.env`.
3. Заполните доступ к панели:

   ```dotenv
   WEB_ADMIN_USERNAME=admin
   WEB_ADMIN_PASSWORD=придумайте-длинный-пароль
   WEB_SECRET_KEY=
   ```

   `WEB_SECRET_KEY` обязателен и должен быть не короче 32 символов — им
   подписываются cookie сессии и ссылки на загруженные файлы. Сгенерируйте:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Если менять ключ, все активные сессии станут недействительными.

   Вместо пароля в открытом виде можно положить PBKDF2-хеш — тогда
   `WEB_ADMIN_PASSWORD` оставьте пустым:

   ```bash
   python -m app.core.security
   ```

   Полученную строку впишите в `WEB_ADMIN_PASSWORD_HASH`.
4. Заполните `TELEGRAM_BOT_TOKEN`. Для включённых в `config.yaml` WhatsApp,
   Instagram и TikTok также заполните переменные соответствующих интеграций
   из разделов ниже.
5. Укажите Telegram-каналы в `config.yaml` и добавьте бота в них как
   администратора с правом публикации.
6. Примените миграции: `alembic upgrade head`.
7. Запустите Redis, например через Docker:
   `docker run --name social-autoposting-redis -p 6379:6379 -d redis:7-alpine`.

## Запуск

Откройте два терминала из корня проекта.

В первом запустите Celery worker (на Windows нужен пул `solo`):

```powershell
.\.venv\Scripts\celery.exe -A app.worker.celery_app:celery worker --loglevel=INFO --pool=solo
```

Во втором — веб-панель:

```powershell
.\.venv\Scripts\python.exe main.py
```

Панель откроется на <http://127.0.0.1:8000>. Адрес и порт меняются
переменными `WEB_HOST` и `WEB_PORT`; в Docker Compose они не используются,
там uvicorn запускается напрямую.

Для разработки с автоперезагрузкой:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.web.main:create_app --factory --reload
```

## WhatsApp через Whapi.Cloud

Публикатор отправляет текст, JPG/PNG/GIF/WebP и MP4/3GP в группы
(`...@g.us`) и каналы (`...@newsletter`). Несколько медиа отправляются по
очереди, подпись прикрепляется к первому файлу. Подпись длиннее 1024 символов
сначала отправляется отдельным текстовым сообщением. Если часть поста уже
отправлена, автоматический повтор отключается, чтобы не создавать дубли.

Списки групп и каналов **не хранятся в `config.yaml`** — бот запрашивает их у
Whapi при каждом открытии выбора площадок и показывает галочками. Достаточно
включить площадку:

```yaml
whatsapp:
  enabled: true
```

Whapi.Cloud работает поверх неофициального протокола WhatsApp Web. Остаётся риск
ограничения номера; используйте отдельный номер и не делайте массовые рассылки
незнакомым получателям.

Первичная настройка:

1. Зарегистрируйтесь на <https://whapi.cloud>, создайте инстанс и привяжите
   номер по QR-коду в их консоли.
2. Скопируйте токен инстанса в `WHAPI_API_TOKEN` внутри `.env`.
3. Проверьте состояние инстанса:

   ```bash
   curl -H "Authorization: Bearer $WHAPI_API_TOKEN" https://gate.whapi.cloud/health
   ```

   Поле `status.text` должно быть `AUTH` или `LAUNCH`.
4. Поставьте `whatsapp.enabled: true` в `config.yaml`.
5. Перезапустите приложение: `docker compose up -d --build`.

Основные настройки:

```dotenv
WHAPI_API_TOKEN=
WHAPI_API_URL=https://gate.whapi.cloud
WHAPI_REQUEST_TIMEOUT=120
WHAPI_MEDIA_MAX_BYTES=104857600
WHATSAPP_TARGET_LIMIT=50
```

Медиа уходят прямо в Whapi multipart-запросом, поэтому публичный URL для файлов
не нужен и отдельный медиа-сервер в Compose не поднимается.

Ограничения каналов:

- в списке появляются только каналы, где аккаунт — владелец или админ;
  подписчик публиковать не может;
- каналы WhatsApp недоступны пользователям в РФ — это ограничение самого
  мессенджера, а не провайдера. Для российского номера `GET /newsletters`
  отвечает заглушкой `{"code": 200}` вместо списка; бот пишет об этом в лог
  и показывает только группы;
- при обрезке по `WHATSAPP_TARGET_LIMIT` каналы идут первыми, чтобы
  многочисленные группы их не вытеснили.

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

## Большие видео в Telegram

Облачный Telegram Bot API разрешает боту загружать видео не больше 50 МБ.
Docker Compose запускает локальный Bot API 10.2 в режиме `--local`, который
поднимает предел до 2000 МБ. Сами файлы приходят из браузера, поэтому лимит на
скачивание больше не важен — важен лимит на отправку в канал.

Размер, который принимает панель, задаёт `WEB_MAX_UPLOAD_BYTES` (по умолчанию
2000 МБ). Если меняете его, поправьте и `client_max_body_size` в
[deploy/nginx/panel.conf](deploy/nginx/panel.conf) — иначе nginx отклонит
файл раньше приложения с ошибкой 413.

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
docker compose stop web worker
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

Локальный API работает при запуске через Docker Compose. При нативном запуске
`python main.py` используется облачный API и сохраняется лимит 50 МБ на
отправку.

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

Compose запускает локальный Telegram Bot API и Redis, применяет
Alembic-миграции, затем поднимает Celery worker, веб-панель и nginx. WhatsApp
обслуживает облачный Whapi.Cloud, поэтому отдельных контейнеров под него нет.
Наружу смотрит только nginx (порты 80 и 443); панель, внутренний Telegram API и
Redis из интернета недоступны. SQLite, медиа, данные Redis и локального
Telegram API сохраняются в именованных volumes и переживают пересоздание
контейнеров.

После запуска панель открывается по адресу сервера: `http://IP-сервера/`.

Посмотреть логи:

```bash
docker compose logs -f web worker nginx
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

## HTTPS для панели

Пока панель работает по HTTP, пароль и cookie сессии идут по сети открытым
текстом. На сервере, доступном из интернета, выпустите сертификат.

1. Направьте A-запись домена на IP сервера и дождитесь, пока он начнёт
   резолвиться.
2. Впишите домен в `server_name` в
   [deploy/nginx/panel.conf](deploy/nginx/panel.conf) (в HTTPS-блоке) и
   поднимите стек: `docker compose up -d`.
3. Выпустите сертификат через webroot, который уже отдаёт nginx:

   ```bash
   docker run --rm -v social-autoposting_letsencrypt:/etc/letsencrypt -v social-autoposting_certbot_webroot:/var/www/certbot certbot/certbot certonly --webroot -w /var/www/certbot -d panel.example.com --email you@example.com --agree-tos --no-eff-email
   ```

   Имена томов — это имя проекта Compose (`social-autoposting`) плюс имя тома;
   проверить можно через `docker volume ls`.

4. Раскомментируйте HTTPS-блок и редирект с 80 порта в том же файле, поправьте
   пути к сертификату под свой домен.
5. Включите защищённые cookie в `.env` и перезапустите:

   ```dotenv
   WEB_SECURE_COOKIES=true
   ```

   ```bash
   docker compose up -d
   docker compose restart nginx
   ```

Продление сертификата — той же командой `certonly` по расписанию (cron или
systemd timer) с последующим `docker compose restart nginx`.

Дополнительно стоит закрыть фаерволом всё, кроме 22, 80 и 443:

```bash
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
```
