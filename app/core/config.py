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
class InstagramConfig:
    username: str
    password: str
    totp_secret: str | None
    session_path: Path
    proxy: str | None
    request_timeout: int


@dataclass(frozen=True, slots=True)
class AppConfig:
    bot_token: str
    owner_id: int
    targets: tuple[PublishTarget, ...]
    instagram: InstagramConfig | None


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

    instagram = _instagram_config(raw)
    return AppConfig(
        bot_token=token,
        owner_id=owner_id,
        targets=targets,
        instagram=instagram,
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
                    key=_required_string(item, "jid", f"whatsapp.{section}"),
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


def _optional_bool(parent: dict[str, Any], key: str, location: str) -> bool:
    value = parent.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{location}.{key} must be true or false")
    return value


def _instagram_config(raw: dict[str, Any]) -> InstagramConfig | None:
    instagram = _optional_mapping(raw, "instagram")
    if not _optional_bool(instagram, "enabled", "instagram"):
        return None

    username = _required_environment("INSTAGRAM_USERNAME")
    password = _required_environment("INSTAGRAM_PASSWORD")
    totp_secret = os.getenv("INSTAGRAM_TOTP_SECRET", "").strip() or None
    session_path = Path(
        os.getenv("INSTAGRAM_SESSION_PATH", "data/instagram_session.json").strip()
        or "data/instagram_session.json"
    )
    proxy = os.getenv("INSTAGRAM_PROXY", "").strip() or None
    timeout_raw = os.getenv("INSTAGRAM_REQUEST_TIMEOUT", "30").strip()
    try:
        request_timeout = int(timeout_raw)
    except ValueError as error:
        raise ConfigError("INSTAGRAM_REQUEST_TIMEOUT must be an integer") from error
    if request_timeout <= 0:
        raise ConfigError("INSTAGRAM_REQUEST_TIMEOUT must be positive")

    return InstagramConfig(
        username=username,
        password=password,
        totp_secret=totp_secret,
        session_path=session_path,
        proxy=proxy,
        request_timeout=request_timeout,
    )


def _required_environment(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise ConfigError(f"{key} is required when Instagram is enabled")
    return value


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
