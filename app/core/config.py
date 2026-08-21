from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

Platform = Literal["telegram", "whatsapp", "instagram", "tiktok"]
TargetKind = Literal["channel", "group", "contact", "feed", "story"]
WhatsAppEngine = Literal["openwa", "cloud"]
DEFAULT_GRAPH_API_VERSION = "v25.0"
WHATSAPP_ENGINES = frozenset({"openwa", "cloud"})
TIKTOK_PRIVACY_LEVELS = frozenset(
    {
        "PUBLIC_TO_EVERYONE",
        "MUTUAL_FOLLOW_FRIENDS",
        "FOLLOWER_OF_CREATOR",
        "SELF_ONLY",
    }
)


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
    """Settings for the unofficial OpenWA engine."""

    api_url: str
    api_key: str
    session_id: str
    request_timeout: int
    media_base_url: str | None
    media_root: Path
    media_max_bytes: int


@dataclass(frozen=True, slots=True)
class WhatsAppCloudConfig:
    """Settings for the official WhatsApp Business Cloud API engine."""

    access_token: str
    phone_number_id: str
    api_base_url: str
    api_version: str
    request_timeout: int
    media_max_bytes: int


@dataclass(frozen=True, slots=True)
class InstagramConfig:
    access_token: str
    ig_user_id: str
    api_base_url: str
    api_version: str
    request_timeout: int
    media_base_url: str
    media_root: Path
    status_poll_interval: int
    status_poll_attempts: int


@dataclass(frozen=True, slots=True)
class TikTokConfig:
    client_key: str
    client_secret: str
    refresh_token: str
    api_base_url: str
    request_timeout: int
    privacy_level: str
    media_base_url: str
    media_root: Path
    disable_comment: bool
    disable_duet: bool
    disable_stitch: bool
    auto_add_music: bool
    chunk_size: int
    status_poll_interval: int
    status_poll_attempts: int


@dataclass(frozen=True, slots=True)
class AppConfig:
    bot_token: str
    owner_id: int
    telegram_api: TelegramAPIConfig
    targets: tuple[PublishTarget, ...]
    whatsapp_engine: WhatsAppEngine
    whatsapp: WhatsAppConfig | None
    whatsapp_cloud: WhatsAppCloudConfig | None
    instagram: InstagramConfig | None
    tiktok: TikTokConfig | None


def load_config(
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
) -> AppConfig:
    load_environment(env_path)
    raw = _read_yaml(Path(config_path))
    engine = _whatsapp_engine()
    targets = tuple(_parse_targets(raw, engine))
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
    has_whatsapp_targets = any(target.platform == "whatsapp" for target in targets)
    whatsapp = _whatsapp_config(has_whatsapp_targets, engine)
    whatsapp_cloud = _whatsapp_cloud_config(has_whatsapp_targets, engine)
    instagram = _instagram_config(raw)
    tiktok = _tiktok_config(raw)
    return AppConfig(
        bot_token=token,
        owner_id=owner_id,
        telegram_api=telegram_api,
        targets=targets,
        whatsapp_engine=engine,
        whatsapp=whatsapp,
        whatsapp_cloud=whatsapp_cloud,
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


def _parse_targets(
    raw: dict[str, Any],
    whatsapp_engine: WhatsAppEngine,
) -> list[PublishTarget]:
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
    for section, kind in (
        ("groups", "group"),
        ("channels", "channel"),
        ("contacts", "contact"),
    ):
        for item in _optional_list(whatsapp, section, "whatsapp"):
            targets.append(
                PublishTarget(
                    platform="whatsapp",
                    key=_whatsapp_key(item, section, kind, whatsapp_engine),
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


def _whatsapp_key(
    item: dict[str, Any],
    section: str,
    kind: str,
    engine: WhatsAppEngine,
) -> str:
    """Each engine names its recipients differently, so validate per engine."""
    location = f"whatsapp.{section}"
    if kind == "contact":
        if engine != "cloud":
            raise ConfigError(
                "whatsapp.contacts requires WHATSAPP_ENGINE=cloud: "
                "OpenWA targets are groups and channels"
            )
        return _whatsapp_phone_number(item, location)

    if engine == "cloud":
        if kind == "channel":
            raise ConfigError(
                "whatsapp.channels cannot be used with WHATSAPP_ENGINE=cloud: "
                "WhatsApp Channels have no official API"
            )
        # Cloud group ids come from the Groups API and carry no @g.us suffix.
        return _required_string(item, "jid", location)

    jid = _required_string(item, "jid", location)
    expected_suffix = "@g.us" if kind == "group" else "@newsletter"
    if not jid.endswith(expected_suffix):
        raise ConfigError(f"{location}.jid must end with {expected_suffix}")
    return jid


def _whatsapp_phone_number(item: dict[str, Any], location: str) -> str:
    """Cloud API addresses a person by phone number in E.164 without the plus."""
    raw = _required_string(item, "phone", location)
    digits = raw.removeprefix("+").replace(" ", "").replace("-", "")
    if not digits.isdigit():
        raise ConfigError(f"{location}.phone must be a phone number in E.164 format")
    return digits


def _optional_bool(parent: dict[str, Any], key: str, location: str) -> bool:
    value = parent.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{location}.{key} must be true or false")
    return value


def _instagram_config(raw: dict[str, Any]) -> InstagramConfig | None:
    instagram = _optional_mapping(raw, "instagram")
    if not _optional_bool(instagram, "enabled", "instagram"):
        return None

    api_base_url = _environment_url(
        "INSTAGRAM_API_BASE_URL",
        "https://graph.facebook.com",
    )
    api_version = (
        os.getenv("INSTAGRAM_API_VERSION", DEFAULT_GRAPH_API_VERSION).strip().strip("/")
        or DEFAULT_GRAPH_API_VERSION
    )
    media_base_url, media_root = _public_media_settings("Instagram")

    return InstagramConfig(
        access_token=_required_environment("INSTAGRAM_ACCESS_TOKEN", "Instagram"),
        ig_user_id=_required_environment("INSTAGRAM_USER_ID", "Instagram"),
        api_base_url=api_base_url,
        api_version=api_version,
        request_timeout=_environment_int("INSTAGRAM_REQUEST_TIMEOUT", 120),
        media_base_url=media_base_url,
        media_root=media_root,
        status_poll_interval=_environment_int("INSTAGRAM_STATUS_POLL_INTERVAL", 5),
        status_poll_attempts=_environment_int("INSTAGRAM_STATUS_POLL_ATTEMPTS", 60),
    )


def _whatsapp_engine() -> WhatsAppEngine:
    engine = os.getenv("WHATSAPP_ENGINE", "openwa").strip().lower() or "openwa"
    if engine not in WHATSAPP_ENGINES:
        raise ConfigError(
            "WHATSAPP_ENGINE must be one of: " + ", ".join(sorted(WHATSAPP_ENGINES))
        )
    return engine  # type: ignore[return-value]


def _whatsapp_cloud_config(
    has_targets: bool,
    engine: WhatsAppEngine,
) -> WhatsAppCloudConfig | None:
    if not has_targets or engine != "cloud":
        return None

    api_version = (
        os.getenv("WHATSAPP_API_VERSION", DEFAULT_GRAPH_API_VERSION).strip().strip("/")
        or DEFAULT_GRAPH_API_VERSION
    )
    return WhatsAppCloudConfig(
        access_token=_required_environment("WHATSAPP_ACCESS_TOKEN", "WhatsApp Cloud"),
        phone_number_id=_required_environment(
            "WHATSAPP_PHONE_NUMBER_ID",
            "WhatsApp Cloud",
        ),
        api_base_url=_environment_url(
            "WHATSAPP_CLOUD_API_BASE_URL",
            "https://graph.facebook.com",
        ),
        api_version=api_version,
        request_timeout=_environment_int("WHATSAPP_REQUEST_TIMEOUT", 120),
        media_max_bytes=_environment_int("WHATSAPP_MEDIA_MAX_BYTES", 16 * 1024**2),
    )


def _whatsapp_config(
    has_targets: bool,
    engine: WhatsAppEngine,
) -> WhatsAppConfig | None:
    if not has_targets or engine != "openwa":
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

    privacy_level = os.getenv(
        "TIKTOK_PRIVACY_LEVEL",
        "PUBLIC_TO_EVERYONE",
    ).strip()
    if privacy_level not in TIKTOK_PRIVACY_LEVELS:
        raise ConfigError(
            "TIKTOK_PRIVACY_LEVEL must be one of: "
            + ", ".join(sorted(TIKTOK_PRIVACY_LEVELS))
        )

    media_base_url, media_root = _public_media_settings("TikTok")
    chunk_size = _environment_int("TIKTOK_UPLOAD_CHUNK_SIZE", 10 * 1024**2)
    if not 5 * 1024**2 <= chunk_size <= 64 * 1024**2:
        raise ConfigError("TIKTOK_UPLOAD_CHUNK_SIZE must be between 5 MB and 64 MB")

    return TikTokConfig(
        client_key=_required_environment("TIKTOK_CLIENT_KEY", "TikTok"),
        client_secret=_required_environment("TIKTOK_CLIENT_SECRET", "TikTok"),
        refresh_token=_required_environment("TIKTOK_REFRESH_TOKEN", "TikTok"),
        api_base_url=_environment_url(
            "TIKTOK_API_BASE_URL",
            "https://open.tiktokapis.com",
        ),
        request_timeout=_environment_int("TIKTOK_REQUEST_TIMEOUT", 300),
        privacy_level=privacy_level,
        media_base_url=media_base_url,
        media_root=media_root,
        disable_comment=_environment_bool("TIKTOK_DISABLE_COMMENT", default=False),
        disable_duet=_environment_bool("TIKTOK_DISABLE_DUET", default=False),
        disable_stitch=_environment_bool("TIKTOK_DISABLE_STITCH", default=False),
        auto_add_music=_environment_bool("TIKTOK_AUTO_ADD_MUSIC", default=True),
        chunk_size=chunk_size,
        status_poll_interval=_environment_int("TIKTOK_STATUS_POLL_INTERVAL", 5),
        status_poll_attempts=_environment_int("TIKTOK_STATUS_POLL_ATTEMPTS", 60),
    )


def _public_media_settings(integration: str) -> tuple[str, Path]:
    """Instagram and TikTok photos are pulled from a public URL, never uploaded."""
    media_base_url = os.getenv("MEDIA_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not media_base_url:
        raise ConfigError(
            f"MEDIA_PUBLIC_BASE_URL is required when {integration} is enabled: "
            f"{integration} downloads media over a public HTTPS URL"
        )
    if not media_base_url.startswith("https://"):
        raise ConfigError("MEDIA_PUBLIC_BASE_URL must use https://")
    media_root = Path(os.getenv("MEDIA_ROOT", "media").strip() or "media")
    return media_base_url, media_root


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


def _environment_url(key: str, default: str) -> str:
    value = os.getenv(key, default).strip().rstrip("/") or default
    if not value.startswith(("http://", "https://")):
        raise ConfigError(f"{key} must use http:// or https://")
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
