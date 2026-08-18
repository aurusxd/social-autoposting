from pathlib import Path

import pytest

from app.core.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "12345")
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
  groups:
    - jid: "group@g.us"
      name: "Клиенты"
instagram:
  enabled: true
tiktok:
  enabled: true
""",
    )
    config = load_config(config_path, tmp_path / ".env")

    assert [(target.platform, target.kind) for target in config.targets] == [
        ("telegram", "channel"),
        ("whatsapp", "group"),
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


def test_token_is_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _write(tmp_path / "config.yaml", "telegram: {}\n")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config(config_path, tmp_path / ".env")


def test_owner_id_is_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _write(tmp_path / "config.yaml", "telegram: {}\n")
    monkeypatch.delenv("TELEGRAM_OWNER_ID")

    with pytest.raises(ConfigError, match="TELEGRAM_OWNER_ID"):
        load_config(config_path, tmp_path / ".env")


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
