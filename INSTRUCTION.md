# Инструкция по запуску Social Autoposting Bot

В документе описаны два варианта запуска:

1. На обычном Windows-ПК через Docker Desktop.
2. На удалённом Linux-сервере через Docker Compose.

В обоих вариантах приложение работает одинаково: Telegram-бот принимает пост,
сохраняет его и передаёт Celery worker задания для публикации в Telegram,
WhatsApp, Instagram и TikTok.

## 1. Общая подготовка

### 1.1. Необходимые аккаунты и ключи

Перед запуском подготовьте:

1. Токен Telegram-бота от `@BotFather`.
2. Числовой Telegram ID владельца бота.
3. `api_id` и `api_hash` из раздела **API development tools** на
   <https://my.telegram.org>.
4. Для WhatsApp — один из двух вариантов:
   - `WHATSAPP_ENGINE=openwa` (по умолчанию): обычный WhatsApp-аккаунт, который
     подключается по QR-коду. Единственный способ писать в каналы и в группы
     больше 8 человек.
   - `WHATSAPP_ENGINE=cloud`: официальный Cloud API. Нужен Official Business
     Account, номер, подключённый в Meta, токен доступа и Phone Number ID.
     Группы ограничены 8 участниками, каналов нет вообще.
5. Если используется Instagram: профессиональный аккаунт Business или Creator,
   привязанный к странице Facebook, приложение Meta с разрешениями
   `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`,
   токен доступа и IG User ID.
6. Если используется TikTok: приложение в TikTok Developer Portal, прошедшее
   аудит, со скоупами `video.publish` и `user.info.basic`, а также
   client key, client secret и refresh-токен авторизованного аккаунта.
7. Если используется Instagram или TikTok: домен с HTTPS-сертификатом, по
   которому наружу будет отдаваться папка с медиа (см. пункт 1.2). Оба API
   скачивают файлы по ссылке, отдать их байтами нельзя.

OpenWA является неофициальным WhatsApp-клиентом. Возможны ограничения или
блокировка номера со стороны WhatsApp. Рекомендуется использовать отдельный
номер и не выполнять массовые рассылки незнакомым пользователям.

### 1.2. Настройка `.env`

Скопируйте `.env.example` в `.env` и заполните Telegram-переменные:

```dotenv
TELEGRAM_BOT_TOKEN=токен_от_BotFather
TELEGRAM_OWNER_ID=числовой_id_владельца
TELEGRAM_API_ID=api_id_из_my.telegram.org
TELEGRAM_API_HASH=api_hash_из_my.telegram.org
```

Выберите движок WhatsApp:

```dotenv
WHATSAPP_ENGINE=openwa
```

Для `openwa` добавьте:

```dotenv
WHATSAPP_API_KEY=длинный_случайный_ключ
WHATSAPP_SESSION_ID=
WHATSAPP_MEDIA_MAX_BYTES=104857600
OPENWA_ENGINE_TYPE=baileys
OPENWA_BIND_HOST=127.0.0.1
OPENWA_PORT=2785
OPENWA_BODY_SIZE_LIMIT=140mb
```

`WHATSAPP_SESSION_ID` пока оставьте пустым. Он появится после создания OpenWA-
сессии (разделы 2.4 и 3.4).

Для `cloud` вместо этого добавьте:

```dotenv
WHATSAPP_ENGINE=cloud
WHATSAPP_ACCESS_TOKEN=токен_доступа_Meta
WHATSAPP_PHONE_NUMBER_ID=id_номера_из_Meta
WHATSAPP_MEDIA_MAX_BYTES=16777216
```

`WHATSAPP_PHONE_NUMBER_ID` находится в Meta for Developers, в разделе
**WhatsApp → API Setup**. Разделы про OpenWA-сессию и QR-код при этом движке не
нужны, а сервис `openwa` можно не поднимать:
`docker compose up -d --scale openwa=0`.

Группы для `cloud` создаются через Groups API, участники вступают по инвайт-
ссылке, потолок — 8 человек на группу. В `config.yaml` при этом движке в
`whatsapp.groups.jid` кладут id из Groups API (без `@g.us`), а раздел
`whatsapp.channels` использовать нельзя — приложение остановится с ошибкой,
потому что официального API у каналов нет.

Если используется Instagram или TikTok, укажите публичный адрес медиа-сервера:

```dotenv
MEDIA_PUBLIC_BASE_URL=https://media.ваш-домен.ru
MEDIA_ROOT=/app/media
```

Compose публикует nginx с медиа на `127.0.0.1:8080` (меняется переменными
`MEDIA_SERVER_BIND_HOST` и `MEDIA_SERVER_PORT`). Поставьте перед ним обратный
прокси с сертификатом Let's Encrypt — адрес обязан начинаться с `https://`,
`http://` оба API отклоняют.

Если используется Instagram, заполните:

```dotenv
INSTAGRAM_ACCESS_TOKEN=токен_доступа_Meta
INSTAGRAM_USER_ID=id_Instagram_аккаунта
INSTAGRAM_API_VERSION=v25.0
```

`INSTAGRAM_USER_ID` возвращает запрос
`GET https://graph.facebook.com/v25.0/{page-id}?fields=instagram_business_account`.
Лучше выпустить токен системного пользователя в Meta Business Suite: обычный
long-lived токен истекает через 60 дней, и его придётся менять руками.

Если используется TikTok, заполните:

```dotenv
TIKTOK_CLIENT_KEY=client_key_приложения
TIKTOK_CLIENT_SECRET=client_secret_приложения
TIKTOK_REFRESH_TOKEN=refresh_токен_аккаунта
TIKTOK_PRIVACY_LEVEL=PUBLIC_TO_EVERYONE
```

Домен из `MEDIA_PUBLIC_BASE_URL` подтвердите в TikTok Developer Portal в разделе
**Manage apps → URL properties**, иначе публикация фото вернёт
`url_ownership_unverified`. Access-токен TikTok живёт 24 часа — бот обновляет
его сам и хранит в таблице `oauth_tokens`, отдельных действий не требуется.

Не передавайте файл `.env` посторонним и не добавляйте его в Git.

### 1.3. Настройка `config.yaml`

Укажите площадки, которые должны отображаться в Telegram-боте:

```yaml
telegram:
  channels:
    - id: "-1001234567890"
      name: "Основной Telegram-канал"

whatsapp:
  groups:
    - jid: "120363000000000000@g.us"
      name: "Группа клиентов"
  channels:
    - jid: "120363000000000000@newsletter"
      name: "WhatsApp-канал"

instagram:
  enabled: true

tiktok:
  enabled: true
```

Telegram-бота нужно добавить администратором в каждый указанный Telegram-канал
и выдать ему право публикации сообщений.

Если Instagram или TikTok не используется, установите для площадки
`enabled: false` и оставьте соответствующие переменные `.env` пустыми.

Настоящие WhatsApp JID можно указать после подключения OpenWA. JID группы
заканчивается на `@g.us`, JID канала — на `@newsletter`.

---

# Вариант 1. Запуск на Windows-ПК

Этот вариант подходит для локальной работы, демонстрации и тестирования. Пока
бот работает, компьютер должен быть включён, подключён к интернету, а Docker
Desktop должен быть запущен.

## 2.1. Требования к компьютеру

- Windows 10/11 x64;
- включённая аппаратная виртуализация;
- Docker Desktop с WSL 2;
- минимум 4 ГБ свободной оперативной памяти;
- минимум 10 ГБ свободного места.

Установите Docker Desktop:
<https://docs.docker.com/desktop/setup/install/windows-install/>.

После установки откройте Docker Desktop и дождитесь запуска Docker Engine.
Проверьте его в PowerShell:

```powershell
docker --version
docker compose version
```

## 2.2. Подготовка проекта на ПК

Распакуйте проект, например в:

```text
C:\social-autoposting
```

Рекомендуется не размещать рабочий проект в синхронизируемой папке OneDrive.

Откройте PowerShell в папке проекта:

```powershell
cd C:\social-autoposting
Copy-Item .env.example .env
notepad .env
```

Заполните `.env` по разделу 1.2.

Сгенерировать ключ OpenWA можно через Docker:

```powershell
docker run --rm python:3.12-alpine python -c "import secrets; print(secrets.token_hex(32))"
```

Вставьте полученное значение в `WHATSAPP_API_KEY`.

Откройте и настройте список площадок:

```powershell
notepad config.yaml
```

## 2.3. Первый переход на локальный Telegram Bot API

Docker Compose использует локальный Telegram Bot API, чтобы бот мог получать
файлы больше 20 МБ. Перед самым первым запуском нужно один раз отключить bot
token от облачного Telegram Bot API.

В PowerShell из папки проекта:

```powershell
$telegramBotToken = ((Get-Content .env | Where-Object {
    $_ -match '^TELEGRAM_BOT_TOKEN='
} | Select-Object -First 1) -split '=', 2)[1].Trim()

Invoke-RestMethod -Method Post `
    -Uri "https://api.telegram.org/bot$telegramBotToken/logOut"

Remove-Variable telegramBotToken
```

В ответе должно быть `ok: True`. Эта операция выполняется только один раз.

## 2.4. Создание OpenWA-сессии на ПК

Запустите OpenWA:

```powershell
docker compose up -d openwa
docker compose logs --tail=100 openwa
```

Откройте в браузере:
<http://127.0.0.1:2785/sessions>.

Далее:

1. Введите `WHATSAPP_API_KEY` из `.env`.
2. Создайте сессию, например `social-autoposting`.
3. Запустите сессию.
4. В телефоне откройте **WhatsApp → Связанные устройства → Привязка
   устройства**.
5. Отсканируйте QR-код.
6. Дождитесь состояния `connected`.
7. Скопируйте UUID сессии в `.env`:

   ```dotenv
   WHATSAPP_SESSION_ID=uuid-сессии
   ```

### Получение JID на Windows

Подставьте реальные значения из `.env`, не отправляя их посторонним:

```powershell
$key = "реальный_WHATSAPP_API_KEY"
$session = "реальный_WHATSAPP_SESSION_ID"
$headers = @{"X-API-Key" = $key}
```

Получить группы:

```powershell
Invoke-RestMethod -Headers $headers `
  "http://127.0.0.1:2785/api/sessions/$session/groups" |
  ConvertTo-Json -Depth 10
```

Получить каналы:

```powershell
Invoke-RestMethod -Headers $headers `
  "http://127.0.0.1:2785/api/sessions/$session/channels" |
  ConvertTo-Json -Depth 10
```

После запросов удалите секреты из текущей PowerShell-сессии:

```powershell
Remove-Variable key, session, headers
```

Запишите нужные JID и названия в `config.yaml`.

## 2.5. Запуск приложения на ПК

Проверьте Compose-конфигурацию:

```powershell
docker compose config --quiet
```

Соберите и запустите приложение:

```powershell
docker compose up -d --build
docker compose ps -a
```

Контейнеры `init-app-volumes` и `migrate` должны завершиться с кодом `0`. Это
нормально. Остальные основные контейнеры должны иметь состояние `running` или
`healthy`.

Посмотреть логи:

```powershell
docker compose logs -f bot worker
```

Остановить просмотр логов можно сочетанием `Ctrl+C`; контейнеры продолжат
работать.

## 2.6. Повторный запуск на ПК

После перезагрузки компьютера запустите Docker Desktop, откройте PowerShell в
папке проекта и выполните:

```powershell
docker compose up -d
```

Остановить приложение без удаления данных:

```powershell
docker compose down
```

---

# Вариант 2. Развертывание на Linux-сервере

Этот вариант подходит для постоянной круглосуточной работы.

## 3.1. Требования к серверу

Рекомендуемая конфигурация:

- Ubuntu 22.04/24.04 или другой актуальный Linux-дистрибутив;
- 2 CPU;
- от 2 ГБ RAM, от 4 ГБ при работе с большими видео;
- от 10 ГБ свободного места;
- SSH-доступ;
- Docker Engine и Docker Compose plugin;
- исходящие HTTPS-соединения в интернет.

Инструкция по установке Docker на Ubuntu:
<https://docs.docker.com/engine/install/ubuntu/>.

Проверка:

```bash
docker --version
docker compose version
```

## 3.2. Загрузка проекта на сервер

Скопируйте проект на сервер, например в `/opt/social-autoposting`, затем
перейдите в папку:

```bash
cd /opt/social-autoposting
```

Создайте рабочий `.env`:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Сгенерируйте ключ OpenWA:

```bash
openssl rand -hex 32
```

Вставьте ключ в `WHATSAPP_API_KEY`, заполните остальные переменные по разделу
1.2 и настройте площадки:

```bash
nano config.yaml
```

## 3.3. Первый переход на локальный Telegram Bot API

До первого полного запуска один раз отключите bot token от облачного Telegram
Bot API:

```bash
read -rsp "Telegram bot token: " telegram_token
echo
curl -sS -X POST "https://api.telegram.org/bot${telegram_token}/logOut"
unset telegram_token
```

В ответе должно быть `"ok":true`. При обновлениях и перезапусках повторять
операцию не нужно.

## 3.4. Создание OpenWA-сессии на сервере

Запустите OpenWA:

```bash
docker compose up -d openwa
docker compose logs --tail=100 openwa
```

OpenWA доступен только через `127.0.0.1` сервера. На своём компьютере создайте
SSH-туннель:

```bash
ssh -L 2785:127.0.0.1:2785 user@IP_СЕРВЕРА
```

Оставьте SSH-соединение открытым и перейдите на своём компьютере по адресу:
<http://127.0.0.1:2785/sessions>.

Далее:

1. Введите `WHATSAPP_API_KEY`.
2. Создайте и запустите сессию `social-autoposting`.
3. Отсканируйте QR-код через меню **Связанные устройства** в WhatsApp.
4. Дождитесь состояния `connected`.
5. Скопируйте UUID сессии в серверный `.env`:

   ```dotenv
   WHATSAPP_SESSION_ID=uuid-сессии
   ```

Не запускайте одну OpenWA-сессию одновременно на компьютере и сервере.

### Получение JID на сервере

В серверном терминале:

```bash
read -rsp "OpenWA API key: " openwa_key
echo
session_id="UUID_СЕССИИ"
```

Получить группы:

```bash
curl -sS -H "X-API-Key: ${openwa_key}" \
  "http://127.0.0.1:2785/api/sessions/${session_id}/groups"
```

Получить каналы:

```bash
curl -sS -H "X-API-Key: ${openwa_key}" \
  "http://127.0.0.1:2785/api/sessions/${session_id}/channels"
```

Очистите переменные текущей оболочки:

```bash
unset openwa_key session_id
```

Запишите нужные JID в серверный `config.yaml`.

## 3.5. Запуск приложения на сервере

Проверьте конфигурацию:

```bash
docker compose config --quiet
```

Запустите проект:

```bash
docker compose up -d --build
docker compose ps -a
```

Контейнеры `init-app-volumes` и `migrate` должны завершиться с кодом `0` — это
нормально. `bot`, `worker`, `redis`, `telegram-bot-api`, `openwa` и
`media-server` должны работать.

Проверьте логи:

```bash
docker compose logs --tail=200 bot worker openwa
```

Для постоянного просмотра:

```bash
docker compose logs -f bot worker
```

## 3.6. Обновление проекта на сервере

После загрузки новой версии кода:

```bash
cd /opt/social-autoposting
docker compose up -d --build
docker compose ps -a
```

Миграции базы данных применяются автоматически.

Остановить приложение без удаления данных:

```bash
docker compose down
```

Запустить снова:

```bash
docker compose up -d
```

---

## 4. Проверка публикаций

После запуска:

1. Напишите боту `/start` с Telegram-аккаунта, ID которого указан в
   `TELEGRAM_OWNER_ID`.
2. Создайте пост командой `/new`.
3. Сначала отправьте короткий текст на одну площадку.
4. Затем проверьте фотографию с подписью.
5. После этого отдельно проверьте видео.

Успешная публикация отображается в логе worker:

```text
Publish job 1 completed
```

Предупреждение о Base64 не является ошибкой, если после него задание успешно
завершилось:

```text
OpenWA rejected media URL; retrying the same file as Base64
Publish job 1 completed
```

## 5. Хранение данных

Приложение использует именованные Docker volumes:

- `app_data` — SQLite-база (в том числе таблица `oauth_tokens` с обновляемым
  refresh-токеном TikTok);
- `app_media` — загруженные фотографии и видео; их же отдаёт наружу
  `media-server` по адресу `MEDIA_PUBLIC_BASE_URL`;
- `redis_data` — данные очереди Redis;
- `telegram_bot_api_data` — данные локального Telegram Bot API;
- `openwa_data` — авторизация и сессия WhatsApp.

Обычная команда сохраняет данные:

```bash
docker compose down
```

Не используйте следующую команду, если данные нужно сохранить:

```bash
docker compose down -v
```

Флаг `-v` удаляет volumes. После удаления `openwa_data` потребуется снова
сканировать QR-код.

Перед переносом или крупным обновлением сохраните резервные копии `.env`,
`config.yaml` и Docker volumes.

## 6. Частые проблемы

### Бот не отвечает

Проверьте `TELEGRAM_OWNER_ID` и логи:

```bash
docker compose logs --tail=200 bot
```

### Worker не получает задания

```bash
docker compose ps redis worker
docker compose logs --tail=200 redis worker
```

### `Permission denied: /app/media/...`

```bash
docker compose run --rm init-app-volumes
docker compose restart bot worker
```

### `whatsapp.channels cannot be used with WHATSAPP_ENGINE=cloud`

У WhatsApp Channels нет официального API. Либо уберите раздел
`whatsapp.channels` из `config.yaml`, либо вернитесь на `WHATSAPP_ENGINE=openwa`.

### Cloud API отвечает про 24-часовое окно

Свободный текст Meta разрешает только в течение 24 часов после сообщения
собеседника. Вне окна нужен утверждённый шаблон — его отправка в боте не
реализована.

### WhatsApp-сессия отключена

Откройте панель OpenWA. На ПК она доступна напрямую через localhost, на сервере
— через SSH-туннель. Если WhatsApp удалил связанное устройство, отсканируйте
новый QR-код.

### Instagram отвечает `The image/video you specified is not accessible`

Meta не смогла скачать файл по `MEDIA_PUBLIC_BASE_URL`. Проверьте, что адрес
доступен из интернета и работает по HTTPS:

```bash
curl -I https://media.ваш-домен.ru/имя_файла.jpg
```

### TikTok отвечает `url_ownership_unverified`

Домен из `MEDIA_PUBLIC_BASE_URL` не подтверждён в TikTok Developer Portal.
Откройте **Manage apps → URL properties**, добавьте домен и пройдите проверку.
Видео это не затрагивает — оно загружается байтами, а не по ссылке.

### TikTok отвечает `privacy level not allowed`

Аккаунт не разрешает уровень из `TIKTOK_PRIVACY_LEVEL`. Текст ошибки содержит
список доступных значений — подставьте одно из них.

### Видео WhatsApp больше 100 МБ

По умолчанию проект ограничивает WhatsApp-файл значением:

```dotenv
WHATSAPP_MEDIA_MAX_BYTES=104857600
```

Простое увеличение лимита повышает потребление оперативной памяти, особенно
при fallback через Base64. Рекомендуется сжимать видео до 100 МБ.

### Telegram сообщает `file is too big`

Убедитесь, что заполнены `TELEGRAM_API_ID` и `TELEGRAM_API_HASH`, контейнер
`telegram-bot-api` работает, а однократный запрос `logOut` был выполнен до
первого запуска локального Bot API.

## 7. Безопасность

- Не передавайте рабочий `.env` и ключи API посторонним.
- Не открывайте OpenWA-порт `2785` всему интернету.
- На сервере используйте SSH-туннель для панели OpenWA.
- Не добавляйте `.env`, базу и резервные копии volumes в Git.
- Для OpenWA рекомендуется отдельный WhatsApp-номер.
- Не запускайте одну WhatsApp-сессию одновременно в двух местах.

