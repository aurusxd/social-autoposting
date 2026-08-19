from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

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
    api_url: str
    api_key: str
    session_id: str
    request_timeout: int
    media_base_url: str | None
    media_root: Path
    media_max_bytes: int


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
class AppConfig:
    bot_token: str
    owner_id: int
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

    owner_id_raw = os.getenv("TELEGRAM_OWNER_ID", "").strip()
    if not owner_id_raw:
        raise ConfigError("TELEGRAM_OWNER_ID is required")
    try:
        owner_id = int(owner_id_raw)
    except ValueError as error:
        raise ConfigError("TELEGRAM_OWNER_ID must be an integer") from error
    if owner_id <= 0:
        raise ConfigError("TELEGRAM_OWNER_ID must be a positive integer")

    telegram_api = _telegram_api_config()
    whatsapp = _whatsapp_config(raw)
    instagram = _instagram_config(raw)
    tiktok = _tiktok_config(raw)
    return AppConfig(
        bot_token=token,
        owner_id=owner_id,
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

    whatsapp = _optional_mapping(raw, "whatsapp")
    for section, kind in (("groups", "group"), ("channels", "channel")):
        for item in _optional_list(whatsapp, section, "whatsapp"):
            targets.append(
                PublishTarget(
                    platform="whatsapp",
                    key=_whatsapp_jid(item, section, kind),
                    kind=kind,
                    name=_required_string(item, "name", f"whatsapp.{section}"),
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


def _whatsapp_jid(
    item: dict[str, Any],
    section: str,
    kind: str,
) -> str:
    jid = _required_string(item, "jid", f"whatsapp.{section}")
    expected_suffix = "@g.us" if kind == "group" else "@newsletter"
    if not jid.endswith(expected_suffix):
        raise ConfigError(f"whatsapp.{section}.jid must end with {expected_suffix}")
    return jid


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
    has_targets = bool(
        _optional_list(whatsapp, "groups", "whatsapp")
        or _optional_list(whatsapp, "channels", "whatsapp")
    )
    if not has_targets:
        return None

    api_url = (
        os.getenv("WHATSAPP_API_URL", "http://localhost:2785/api").strip().rstrip("/")
        or "http://localhost:2785/api"
    )
    if not api_url.startswith(("http://", "https://")):
        raise ConfigError("WHATSAPP_API_URL must use http:// or https://")

    timeout_raw = os.getenv("WHATSAPP_REQUEST_TIMEOUT", "120").strip()
    try:
        request_timeout = int(timeout_raw)
    except ValueError as error:
        raise ConfigError("WHATSAPP_REQUEST_TIMEOUT must be an integer") from error
    if request_timeout <= 0:
        raise ConfigError("WHATSAPP_REQUEST_TIMEOUT must be positive")

    max_bytes_raw = os.getenv(
        "WHATSAPP_MEDIA_MAX_BYTES",
        str(100 * 1024**2),
    ).strip()
    try:
        media_max_bytes = int(max_bytes_raw)
    except ValueError as error:
        raise ConfigError("WHATSAPP_MEDIA_MAX_BYTES must be an integer") from error
    if media_max_bytes <= 0:
        raise ConfigError("WHATSAPP_MEDIA_MAX_BYTES must be positive")

    media_base_url = os.getenv("WHATSAPP_MEDIA_BASE_URL", "").strip().rstrip("/")
    if media_base_url and not media_base_url.startswith(("http://", "https://")):
        raise ConfigError("WHATSAPP_MEDIA_BASE_URL must use http:// or https://")

    return WhatsAppConfig(
        api_url=api_url,
        api_key=_required_environment("WHATSAPP_API_KEY", "WhatsApp"),
        session_id=_required_environment("WHATSAPP_SESSION_ID", "WhatsApp"),
        request_timeout=request_timeout,
        media_base_url=media_base_url or None,
        media_root=Path(os.getenv("WHATSAPP_MEDIA_ROOT", "media").strip() or "media"),
        media_max_bytes=media_max_bytes,
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
