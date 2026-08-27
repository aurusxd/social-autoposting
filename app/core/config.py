from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from app.core.security import PasswordFormatError, validate_password_hash

Platform = Literal["telegram", "whatsapp", "instagram", "tiktok"]
TargetKind = Literal["channel", "group", "feed", "story"]


class ConfigError(ValueError):
    """Raised when application configuration is invalid."""


@dataclass(frozen=True, slots=True)
class PublishTarget:
    platform: Platform
    key: str
    kind: TargetKind
    name: str


@dataclass(frozen=True, slots=True)
class TelegramAPIConfig:
    base_url: str
    local: bool
    server_files_path: Path
    client_files_path: Path


@dataclass(frozen=True, slots=True)
class WhatsAppConfig:
    api_token: str
    api_url: str
    request_timeout: int
    media_max_bytes: int
    target_limit: int


@dataclass(frozen=True, slots=True)
class InstagramConfig:
    api_key: str
    account_id: str
    api_base_url: str
    request_timeout: int


@dataclass(frozen=True, slots=True)
class TikTokConfig:
    api_key: str
    account_id: str
    api_base_url: str
    request_timeout: int
    privacy_level: str


@dataclass(frozen=True, slots=True)
class WebConfig:
    username: str
    password: str
    password_hash: str
    secret_key: str
    session_max_age: int
    max_upload_bytes: int
    secure_cookies: bool


@dataclass(frozen=True, slots=True)
class AppConfig:
    bot_token: str
    web: WebConfig
    telegram_api: TelegramAPIConfig
    targets: tuple[PublishTarget, ...]
    whatsapp: WhatsAppConfig | None
    instagram: InstagramConfig | None
    tiktok: TikTokConfig | None


def load_config(
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
) -> AppConfig:
    load_environment(env_path)
    raw = _read_yaml(Path(config_path))
    targets = tuple(_parse_targets(raw))
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is required")

    web = _web_config()
    telegram_api = _telegram_api_config()
    whatsapp = _whatsapp_config(raw)
    instagram = _instagram_config(raw)
    tiktok = _tiktok_config(raw)
    return AppConfig(
        bot_token=token,
        web=web,
        telegram_api=telegram_api,
        targets=targets,
        whatsapp=whatsapp,
        instagram=instagram,
        tiktok=tiktok,
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"Cannot read config file: {path}") from error

    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise ConfigError(f"Invalid YAML in {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml root must be a mapping")
    return raw


def _parse_targets(raw: dict[str, Any]) -> list[PublishTarget]:
    targets: list[PublishTarget] = []

    telegram = _optional_mapping(raw, "telegram")
    for item in _optional_list(telegram, "channels", "telegram"):
        targets.append(
            PublishTarget(
                platform="telegram",
                key=_required_string(item, "id", "telegram.channels"),
                kind="channel",
                name=_required_string(item, "name", "telegram.channels"),
            )
        )

    instagram = _optional_mapping(raw, "instagram")
    if _optional_bool(instagram, "enabled", "instagram"):
        targets.extend(
            (
                PublishTarget("instagram", "self", "feed", "Instagram · Лента"),
                PublishTarget("instagram", "self", "story", "Instagram · История"),
            )
        )

    tiktok = _optional_mapping(raw, "tiktok")
    if _optional_bool(tiktok, "enabled", "tiktok"):
        targets.append(PublishTarget("tiktok", "self", "feed", "TikTok"))

    identities = [(target.platform, target.key, target.kind) for target in targets]
    if len(identities) != len(set(identities)):
        raise ConfigError("Publication targets must be unique")
    return targets


def _optional_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


def _optional_list(
    parent: dict[str, Any], key: str, location: str
) -> list[dict[str, Any]]:
    value = parent.get(key, [])
    if not isinstance(value, list):
        raise ConfigError(f"{location}.{key} must be a list")
    if not all(isinstance(item, dict) for item in value):
        raise ConfigError(f"Every item in {location}.{key} must be a mapping")
    return value


def _required_string(item: dict[str, Any], key: str, location: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _optional_bool(parent: dict[str, Any], key: str, location: str) -> bool:
    value = parent.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{location}.{key} must be true or false")
    return value


def _instagram_config(raw: dict[str, Any]) -> InstagramConfig | None:
    instagram = _optional_mapping(raw, "instagram")
    if not _optional_bool(instagram, "enabled", "instagram"):
        return None

    api_key, account_id, api_base_url, request_timeout = _zernio_connection(
        "Instagram",
        "ZERNIO_INSTAGRAM_ACCOUNT_ID",
    )

    return InstagramConfig(
        api_key=api_key,
        account_id=account_id,
        api_base_url=api_base_url,
        request_timeout=request_timeout,
    )


def _whatsapp_config(raw: dict[str, Any]) -> WhatsAppConfig | None:
    whatsapp = _optional_mapping(raw, "whatsapp")
    if not _optional_bool(whatsapp, "enabled", "whatsapp"):
        return None

    api_url = (
        os.getenv("WHAPI_API_URL", "https://gate.whapi.cloud").strip().rstrip("/")
        or "https://gate.whapi.cloud"
    )
    if not api_url.startswith(("http://", "https://")):
        raise ConfigError("WHAPI_API_URL must use http:// or https://")

    return WhatsAppConfig(
        api_token=_required_environment("WHAPI_API_TOKEN", "WhatsApp"),
        api_url=api_url,
        request_timeout=_environment_int("WHAPI_REQUEST_TIMEOUT", 120),
        media_max_bytes=_environment_int("WHAPI_MEDIA_MAX_BYTES", 100 * 1024**2),
        target_limit=_environment_int("WHATSAPP_TARGET_LIMIT", 50),
    )


def _web_config() -> WebConfig:
    username = os.getenv("WEB_ADMIN_USERNAME", "admin").strip()
    if not username:
        raise ConfigError("WEB_ADMIN_USERNAME must not be empty")

    password = os.getenv("WEB_ADMIN_PASSWORD", "").strip()
    password_hash = os.getenv("WEB_ADMIN_PASSWORD_HASH", "").strip()
    if not password and not password_hash:
        raise ConfigError(
            "WEB_ADMIN_PASSWORD or WEB_ADMIN_PASSWORD_HASH is required "
            "to sign in to the control panel"
        )
    if password_hash:
        # Fail at startup rather than on the first login attempt.
        try:
            validate_password_hash(password_hash)
        except PasswordFormatError as error:
            raise ConfigError(f"WEB_ADMIN_PASSWORD_HASH is invalid: {error}") from error
        password = ""

    secret_key = os.getenv("WEB_SECRET_KEY", "").strip()
    if len(secret_key) < 32:
        raise ConfigError(
            "WEB_SECRET_KEY must be at least 32 characters; generate one with "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )

    return WebConfig(
        username=username,
        password=password,
        password_hash=password_hash,
        secret_key=secret_key,
        session_max_age=_environment_int("WEB_SESSION_MAX_AGE", 7 * 24 * 3600),
        max_upload_bytes=_environment_int("WEB_MAX_UPLOAD_BYTES", 2000 * 1024**2),
        secure_cookies=_environment_bool("WEB_SECURE_COOKIES", default=False),
    )


def _telegram_api_config() -> TelegramAPIConfig:
    base_url = (
        os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")
        .strip()
        .rstrip("/")
        or "https://api.telegram.org"
    )
    if not base_url.startswith(("http://", "https://")):
        raise ConfigError("TELEGRAM_API_BASE_URL must use http:// or https://")

    local = _environment_bool("TELEGRAM_API_LOCAL", default=False)
    server_files_path = Path(
        os.getenv(
            "TELEGRAM_API_SERVER_FILES_PATH",
            "/var/lib/telegram-bot-api",
        ).strip()
        or "/var/lib/telegram-bot-api"
    )
    client_files_path = Path(
        os.getenv(
            "TELEGRAM_API_CLIENT_FILES_PATH",
            "/var/lib/telegram-bot-api",
        ).strip()
        or "/var/lib/telegram-bot-api"
    )
    return TelegramAPIConfig(
        base_url=base_url,
        local=local,
        server_files_path=server_files_path,
        client_files_path=client_files_path,
    )


def _tiktok_config(raw: dict[str, Any]) -> TikTokConfig | None:
    tiktok = _optional_mapping(raw, "tiktok")
    if not _optional_bool(tiktok, "enabled", "tiktok"):
        return None

    api_key, account_id, api_base_url, request_timeout = _zernio_connection(
        "TikTok",
        "ZERNIO_TIKTOK_ACCOUNT_ID",
    )

    privacy_level = os.getenv(
        "ZERNIO_TIKTOK_PRIVACY_LEVEL",
        "PUBLIC_TO_EVERYONE",
    ).strip()
    allowed_privacy_levels = {
        "PUBLIC_TO_EVERYONE",
        "MUTUAL_FOLLOW_FRIENDS",
        "FOLLOWER_OF_CREATOR",
        "SELF_ONLY",
    }
    if privacy_level not in allowed_privacy_levels:
        raise ConfigError(
            "ZERNIO_TIKTOK_PRIVACY_LEVEL must be one of: "
            + ", ".join(sorted(allowed_privacy_levels))
        )

    return TikTokConfig(
        api_key=api_key,
        account_id=account_id,
        api_base_url=api_base_url,
        request_timeout=request_timeout,
        privacy_level=privacy_level,
    )


def _zernio_connection(
    integration: str,
    account_id_variable: str,
) -> tuple[str, str, str, int]:
    api_key = _required_environment("ZERNIO_API_KEY", integration)
    account_id = _required_environment(account_id_variable, integration)
    api_base_url = (
        os.getenv("ZERNIO_API_BASE_URL", "https://zernio.com/api").strip().rstrip("/")
        or "https://zernio.com/api"
    )
    if not api_base_url.startswith(("http://", "https://")):
        raise ConfigError("ZERNIO_API_BASE_URL must use http:// or https://")

    timeout_raw = os.getenv("ZERNIO_REQUEST_TIMEOUT", "120").strip()
    try:
        request_timeout = int(timeout_raw)
    except ValueError as error:
        raise ConfigError("ZERNIO_REQUEST_TIMEOUT must be an integer") from error
    if request_timeout <= 0:
        raise ConfigError("ZERNIO_REQUEST_TIMEOUT must be positive")
    return api_key, account_id, api_base_url, request_timeout


def _required_environment(key: str, integration: str = "Instagram") -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise ConfigError(f"{key} is required when {integration} is enabled")
    return value


def _environment_int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigError(f"{key} must be an integer") from error
    if value <= 0:
        raise ConfigError(f"{key} must be positive")
    return value


def _environment_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be true or false")


def load_environment(env_path: str | Path = ".env") -> None:
    """Load missing environment variables from a local dotenv-style file."""
    path = Path(env_path)
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ConfigError(f"Cannot read environment file: {path}") from error

    for line_number, source_line in enumerate(lines, start=1):
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"Invalid .env line {line_number}")
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        if not key:
            raise ConfigError(f"Invalid .env line {line_number}")
        os.environ.setdefault(key, value.strip().strip("\"'"))
