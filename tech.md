  # tech.md — Cross-posting panel

v2 — интерфейсом стала веб-панель вместо Telegram-бота.

> Документ описывает исходный замысел. Часть разделов про публикаторы отстала
> от кода: WhatsApp работает через Whapi.Cloud, Instagram и TikTok — через
> Zernio. Актуальное описание — в [README.md](README.md).

## Проект

Веб-панель приёма контента. Пользователь открывает панель в браузере (ПК или телефон), пишет текст, загружает фото/видео, выбирает целевые каналы/группы и отправляет пост в WhatsApp (группы/каналы), Telegram (каналы), Instagram (личный профиль: лента/сторис), TikTok. Один пользователь (владелец), без мультитенантности.

## Стек

- Python 3.12+
- FastAPI + Jinja2 + uvicorn — веб-панель (приём контента + UI выбора целей)
- nginx — обратный прокси и TLS перед панелью
- aiogram 3.x — публикация в Telegram-каналы
- SQLite — хранилище (файл `data/app.db`)
- SQLAlchemy 2.x — ORM и репозитории
- Alembic — миграции схемы SQLite
- ruff — линт + форматирование
- pytest — тесты
- loguru — логирование
- PyYAML — конфиг
- Celery 5.x — выполнение заданий публикации
- Redis — брокер Celery; таблица `publish_jobs` остаётся источником состояния
- Публикаторы (неофициальные, риск блокировки аккаунта — известное ограничение проекта):
  - Telegram: aiogram Bot API (официальный)
  - WhatsApp: Green API или Wappi (WhatsApp Web-сессия) — провайдер фиксируется на этапе реализации паблишера, интерфейс не зависит от выбора
  - Instagram: instagrapi
  - TikTok: неофициальная библиотека загрузки (фиксируется на этапе реализации)

## Архитектура

```
app/
  web/
    main.py             # сборка FastAPI-приложения
    security.py         # cookie-сессии, блокировка входа, подпись загрузок
    dependencies.py     # конфиг, сессии, БД и шаблоны для обработчиков
    presenters.py       # идентификаторы целей и подписи для страниц
    routers/            # вход, страницы, JSON API
    templates/          # Jinja-шаблоны панели
    static/             # CSS и JS панели
  core/
    config.py           # загрузка config.yaml + .env
    drafts.py           # черновик поста и его медиа
    security.py         # PBKDF2 для пароля панели
  database/
    database.py          # SQLAlchemy engine + сессии SQLite
    models/              # ORM-модели
    repositories/        # операции над posts и publish_jobs
    alembic/             # окружение и версии Alembic
  services/
    media_storage.py      # приём загрузок в media/ и удаление файлов
    target_registry.py    # список целей: config.yaml + чаты Whapi
    submission_service.py # сохранение поста и publish_jobs одной транзакцией
    dispatch_service.py   # отправка идентификаторов заданий в Celery
  worker/
    celery_app.py         # конфигурация Celery и Redis broker
    tasks.py              # claim, публикация, ретраи и crash-recovery
  publishers/
    base.py               # Protocol Publisher
    telegram_publisher.py
    whatsapp_publisher.py
    instagram_publisher.py # feed/story через instagrapi + сохранение сессии
    tiktok_publisher.py
    fakes.py               # фейковые клиенты для тестов
main.py                    # точка входа Telegram UI
tests/
config.yaml
.env.example
pyproject.toml
```

## Схема БД (SQLite)

Миграции ведутся через Alembic в `app/database/alembic/versions/`. Alembic хранит текущую ревизию в таблице `alembic_version`. Сгенерированную миграцию нужно проверить до применения. Применённые ревизии не редактируются, изменения схемы оформляются новой ревизией.

```sql
CREATE TABLE posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    caption TEXT,
    status TEXT NOT NULL DEFAULT 'draft'  -- draft | queued | done | failed
);

CREATE TABLE media_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    file_path TEXT NOT NULL,       -- локальный путь под media/
    media_type TEXT NOT NULL,      -- photo | video
    tg_file_id TEXT,               -- file_id из Telegram, для повторной отправки без реаплоада
    position INTEGER NOT NULL DEFAULT 0  -- порядок в альбоме
);

CREATE TABLE publish_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    platform TEXT NOT NULL,        -- telegram | whatsapp | instagram | tiktok
    target_key TEXT NOT NULL,      -- id/jid цели из config.yaml, "self" для instagram/tiktok
    target_kind TEXT NOT NULL,     -- channel | group | feed | story
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | in_progress | done | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(post_id, platform, target_key, target_kind)
);

CREATE INDEX idx_publish_jobs_status ON publish_jobs(status);
```

`UNIQUE(post_id, platform, target_key, target_kind)` — точка идемпотентности на уровне БД: повторная постановка той же цели не создаёт дубль джоба.

## Контракты очереди

Таблица `publish_jobs` — источник истины о состоянии публикации, а Redis передаёт Celery только `job_id`. После сохранения поста бот отправляет `worker.publish_job(job_id)`. Worker атомарно переводит только `pending`-задание в `in_progress`, вызывает `Publisher.publish`, затем фиксирует `done`, снова `pending` для ретрая или `failed` (+ `last_error`, `attempts`). Если Redis временно недоступен, задание остаётся в SQLite и будет отправлено при следующем запуске worker.

Ретраи: до 3 попыток с задержками 10 и 60 секунд через Celery countdown. После третьей неудачи — `status = 'failed'`, дальше задание не трогается автоматически.

Повторная доставка Celery безопасна на уровне локального состояния: условный `UPDATE ... WHERE status = 'pending'` позволяет только одному worker захватить задание. Задания, застрявшие в `in_progress` дольше 15 минут, возвращаются в `pending` при старте worker. Абсолютную защиту от дубля на внешней площадке это не гарантирует: падение процесса после успешной сетевой публикации, но до `done` требует idempotency key самой площадки либо последующей сверки по `external_id`.

## Контракт `Publisher`

```python
# app/publishers/base.py
from typing import Protocol
from app.core.models import Post, PublishTarget, PublishResult

class Publisher(Protocol):
    platform: str  # "telegram" | "whatsapp" | "instagram" | "tiktok"

    async def publish(self, post: Post, target: PublishTarget) -> PublishResult:
        """Публикует пост в цель. Бросает PublisherError на неретраебл-ошибку
        (невалидные креды, забаненный аккаунт), возвращает PublishResult(success=False, retryable=True)
        на временную (таймаут, 5xx, rate limit)."""
        ...
```

```python
# app/core/models.py
@dataclass
class PublishTarget:
    key: str          # id/jid из config.yaml
    kind: str          # channel | group | feed | story
    name: str           # человекочитаемое имя, для логов и статус-репортов

@dataclass
class PublishResult:
    success: bool
    retryable: bool = False
    error: str | None = None
    retry_after: int | None = None  # индивидуальная задержка ретрая в секундах
    external_id: str | None = None  # id опубликованного поста на площадке, если есть
```

Новый паблишер обязан реализовать `Publisher` целиком, различать retryable/non-retryable ошибки явно. Не хватает поля в `PublishTarget`/`PublishResult` для конкретной площадки → не выдумывать поле, а аппендить сюда с бампом версии (см. «Владение ядром» ниже — для соло-режима это просто дисциплина «сначала правь tech.md, потом код»).

## Конфиг

`config.yaml` — источник истины по целям публикации, читается `app/core/config.py` в типизированную структуру (dataclass, не dict).

```yaml
telegram:
  channels:
    - id: "-1001234567890"
      name: "Основной канал"
whatsapp:
  groups:
    - jid: "1234567890-123456@g.us"
      name: "Группа клиентов"
  channels:
    - jid: "1234567890@newsletter"
      name: "WA-канал"
instagram:
  enabled: true
tiktok:
  enabled: true
```

`.env.example` — секреты (токены, креды провайдеров), не в `config.yaml`:
```
TELEGRAM_BOT_TOKEN=
WEB_ADMIN_USERNAME=admin
WEB_ADMIN_PASSWORD=
WEB_SECRET_KEY=
CELERY_BROKER_URL=redis://localhost:6379/0
DATABASE_URL=
WHATSAPP_API_KEY=
INSTAGRAM_USERNAME=
INSTAGRAM_PASSWORD=
INSTAGRAM_TOTP_SECRET=
INSTAGRAM_SESSION_PATH=data/instagram_session.json
INSTAGRAM_PROXY=
INSTAGRAM_REQUEST_TIMEOUT=30
TIKTOK_SESSION_ID=
```

## Фейки для тестов

`app/publishers/fakes.py` — по одному фейку на площадку, реализуют `Publisher`, пишут вызовы в список для ассертов, умеют возвращать `PublishResult(success=False, retryable=True)` по флагу — для теста пути ошибки.

## Стратегия тестов

Тесты выводятся из критериев приёмки задачи, не зеркалят реализацию.

Обязательно на каждый слайс:
- **Идемпотентность джоба.** Дважды вызвать обработку джоба с тем же `post_id`/`target` → ровно одна публикация (проверяется по фейку и по `UNIQUE`-constraint в БД).
- **Путь ошибки.** Фейк возвращает `retryable=True` → джоб уходит в retry, не в `failed` сразу; после 3 попыток → `failed`.
- **Контракт конфига.** Невалидный `config.yaml` (отсутствует обязательное поле) → явная ошибка при старте, не тихий пропуск цели.
- **Property-based (fast-check аналог для Python — hypothesis)** — на чистую логику без сети: разбор конфига, вычисление бэкоффа, маппинг статусов.

## Конвенции коммитов, PR, комментариев

- Язык коммитов, PR, комментариев в коде — английский.
- Формат коммита — Conventional Commits: `type(scope): summary`. `type` из `feat|fix|test|refactor|chore|docs`. `summary` в императиве, со строчной, без точки, до ~50 символов.
- Коммиты маленькие, по ходу работы, не один большой коммит в конце. По возможности каждый коммит проходит `ruff check`.
- Комментарии в коде — коротко, объясняют *почему*, не пересказывают код. Закомментированный код не оставлять.
- Без em-dash, без филлеров, активный залог.

## Definition of Done задачи

- `ruff check` и `ruff format --check` без ошибок.
- Тесты по доктрине выше проходят (`pytest`).
- Миграция (если есть) применяется на чистой БД без ошибок.
- Задача из roadmap закрыта, поведение соответствует критериям приёмки.

## Дорожная карта

1. Скелет: приём медиа в Telegram-боте, сохранение на диск (`media/`) + БД, `config.py`, миграция `0001_init.sql`, `ruff` настроен, `.env.example`.
2. Эталонная вертикаль: `publish_jobs` + воркер + `TelegramPublisher` end-to-end (пост из бота публикуется в тестовый Telegram-канал).
3. Инлайн-клавиатура выбора целей публикации (мультиселект каналов/групп из конфига) + статус-репорт в чат по завершении джобов.
4. `WhatsAppPublisher` (Green API/Wappi).
5. `InstagramPublisher` (instagrapi): лента + сторис. Реализовано.
6. `TikTokPublisher`.
7. Ретраи с бэкоффом, crash-recovery зависших `in_progress`-джобов, логирование через loguru.
