from pathlib import Path

import pytest

from app.core.config import ConfigError, load_config
from app.core.security import hash_password, verify_password_hash


@pytest.fixture(autouse=True)
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("WEB_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD", "panel-password")
    monkeypatch.setenv("WEB_SECRET_KEY", "k" * 48)
    monkeypatch.delenv("WEB_ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("WEB_SECURE_COOKIES", raising=False)
    monkeypatch.delenv("WEB_MAX_UPLOAD_BYTES", raising=False)
    monkeypatch.delenv("WEB_SESSION_MAX_AGE", raising=False)
    monkeypatch.setenv("WHAPI_API_TOKEN", "whapi-token")
    monkeypatch.setenv("ZERNIO_API_KEY", "zernio-key")
    monkeypatch.setenv("ZERNIO_INSTAGRAM_ACCOUNT_ID", "instagram-account")
    monkeypatch.setenv("ZERNIO_TIKTOK_ACCOUNT_ID", "tiktok-account")
    monkeypatch.delenv("TELEGRAM_API_BASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_API_LOCAL", raising=False)
    monkeypatch.delenv("TELEGRAM_API_SERVER_FILES_PATH", raising=False)
    monkeypatch.delenv("TELEGRAM_API_CLIENT_FILES_PATH", raising=False)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_config_maps_all_ui_targets(tmp_path: Path) -> None:
    config_path = _write(
        tmp_path / "config.yaml",
        """
telegram:
  channels:
    - id: "-1001"
      name: "Новости"
whatsapp:
  enabled: true
instagram:
  enabled: true
tiktok:
  enabled: true
""",
    )
    config = load_config(config_path, tmp_path / ".env")

    assert [(target.platform, target.kind) for target in config.targets] == [
        ("telegram", "channel"),
        ("instagram", "feed"),
        ("instagram", "story"),
        ("tiktok", "feed"),
    ]
    assert config.instagram is not None
    assert config.instagram.api_key == "zernio-key"
    assert config.instagram.account_id == "instagram-account"
    assert config.instagram.request_timeout == 120
    assert config.tiktok is not None
    assert config.tiktok.api_key == "zernio-key"
    assert config.tiktok.account_id == "tiktok-account"
    assert config.tiktok.privacy_level == "PUBLIC_TO_EVERYONE"
    assert config.telegram_api.base_url == "https://api.telegram.org"
    assert not config.telegram_api.local
    assert config.whatsapp is not None
    assert config.whatsapp.api_token == "whapi-token"
    assert config.whatsapp.api_url == "https://gate.whapi.cloud"
    assert config.whatsapp.target_limit == 50


def test_missing_target_name_fails_at_startup(tmp_path: Path) -> None:
    config_path = _write(
        tmp_path / "config.yaml",
        'telegram:\n  channels:\n    - id: "-1001"\n',
    )
    with pytest.raises(ConfigError, match=r"telegram\.channels\.name"):
        load_config(config_path, tmp_path / ".env")


def test_duplicate_target_fails_at_startup(tmp_path: Path) -> None:
    config_path = _write(
        tmp_path / "config.yaml",
        """
telegram:
  channels:
    - id: "-1001"
      name: "Первый"
    - id: "-1001"
      name: "Дубликат"
""",
    )
    with pytest.raises(ConfigError, match="must be unique"):
        load_config(config_path, tmp_path / ".env")


def test_whatsapp_produces_no_static_targets(tmp_path: Path) -> None:
    config_path = _write(
        tmp_path / "config.yaml",
        "whatsapp:\n  enabled: true\n",
    )

    config = load_config(config_path, tmp_path / ".env")

    # Groups and channels are discovered through Whapi at runtime instead.
    assert config.targets == ()
    assert config.whatsapp is not None


def test_disabled_whatsapp_needs_no_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "whatsapp:\n  enabled: false\n")
    monkeypatch.delenv("WHAPI_API_TOKEN")

    config = load_config(config_path, tmp_path / ".env")

    assert config.whatsapp is None


def test_whatsapp_token_is_required_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "whatsapp:\n  enabled: true\n")
    monkeypatch.delenv("WHAPI_API_TOKEN")

    with pytest.raises(ConfigError, match="WHAPI_API_TOKEN"):
        load_config(config_path, tmp_path / ".env")


def test_invalid_target_limit_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "whatsapp:\n  enabled: true\n")
    monkeypatch.setenv("WHATSAPP_TARGET_LIMIT", "0")

    with pytest.raises(ConfigError, match="WHATSAPP_TARGET_LIMIT"):
        load_config(config_path, tmp_path / ".env")


def test_token_is_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _write(tmp_path / "config.yaml", "telegram: {}\n")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config(config_path, tmp_path / ".env")


def test_panel_password_is_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "telegram: {}\n")
    monkeypatch.delenv("WEB_ADMIN_PASSWORD")

    with pytest.raises(ConfigError, match="WEB_ADMIN_PASSWORD"):
        load_config(config_path, tmp_path / ".env")


def test_short_secret_key_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "telegram: {}\n")
    monkeypatch.setenv("WEB_SECRET_KEY", "too-short")

    with pytest.raises(ConfigError, match="WEB_SECRET_KEY"):
        load_config(config_path, tmp_path / ".env")


def test_password_hash_replaces_the_plaintext_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "telegram: {}\n")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD_HASH", hash_password("secret", 1_000))

    config = load_config(config_path, tmp_path / ".env")

    assert config.web.password == ""
    assert verify_password_hash(config.web.password_hash, "secret")


def test_broken_password_hash_fails_at_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "telegram: {}\n")
    monkeypatch.setenv("WEB_ADMIN_PASSWORD_HASH", "not-a-hash")

    with pytest.raises(ConfigError, match="WEB_ADMIN_PASSWORD_HASH"):
        load_config(config_path, tmp_path / ".env")


def test_panel_defaults_are_applied(tmp_path: Path) -> None:
    config_path = _write(tmp_path / "config.yaml", "telegram: {}\n")

    config = load_config(config_path, tmp_path / ".env")

    assert config.web.username == "admin"
    assert config.web.password == "panel-password"
    assert config.web.session_max_age == 7 * 24 * 3600
    assert config.web.max_upload_bytes == 2000 * 1024**2
    assert not config.web.secure_cookies


def test_zernio_account_is_required_when_instagram_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "instagram:\n  enabled: true\n")
    monkeypatch.delenv("ZERNIO_INSTAGRAM_ACCOUNT_ID")

    with pytest.raises(ConfigError, match="ZERNIO_INSTAGRAM_ACCOUNT_ID"):
        load_config(config_path, tmp_path / ".env")


def test_zernio_credentials_are_required_when_tiktok_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "tiktok:\n  enabled: true\n")
    monkeypatch.delenv("ZERNIO_API_KEY")

    with pytest.raises(ConfigError, match="ZERNIO_API_KEY"):
        load_config(config_path, tmp_path / ".env")


def test_invalid_tiktok_privacy_level_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "tiktok:\n  enabled: true\n")
    monkeypatch.setenv("ZERNIO_TIKTOK_PRIVACY_LEVEL", "EVERYONE")

    with pytest.raises(ConfigError, match="ZERNIO_TIKTOK_PRIVACY_LEVEL"):
        load_config(config_path, tmp_path / ".env")


def test_local_telegram_api_settings_are_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "telegram: {}\n")
    monkeypatch.setenv("TELEGRAM_API_BASE_URL", "http://telegram-bot-api:8081/")
    monkeypatch.setenv("TELEGRAM_API_LOCAL", "true")
    monkeypatch.setenv("TELEGRAM_API_SERVER_FILES_PATH", "/server/files")
    monkeypatch.setenv("TELEGRAM_API_CLIENT_FILES_PATH", "/client/files")

    config = load_config(config_path, tmp_path / ".env")

    assert config.telegram_api.base_url == "http://telegram-bot-api:8081"
    assert config.telegram_api.local
    assert config.telegram_api.server_files_path == Path("/server/files")
    assert config.telegram_api.client_files_path == Path("/client/files")


def test_invalid_telegram_api_local_flag_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "telegram: {}\n")
    monkeypatch.setenv("TELEGRAM_API_LOCAL", "sometimes")

    with pytest.raises(ConfigError, match="TELEGRAM_API_LOCAL"):
        load_config(config_path, tmp_path / ".env")
