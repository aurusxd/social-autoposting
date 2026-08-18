from pathlib import Path

import pytest

from app.core.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "12345")
    monkeypatch.setenv("INSTAGRAM_USERNAME", "instagram-user")
    monkeypatch.setenv("INSTAGRAM_PASSWORD", "instagram-password")
    monkeypatch.setenv("ZERNIO_API_KEY", "zernio-key")
    monkeypatch.setenv("ZERNIO_TIKTOK_ACCOUNT_ID", "tiktok-account")


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
    assert config.instagram.username == "instagram-user"
    assert config.instagram.session_path == Path("data/instagram_session.json")
    assert config.instagram.request_timeout == 30
    assert config.tiktok is not None
    assert config.tiktok.api_key == "zernio-key"
    assert config.tiktok.account_id == "tiktok-account"
    assert config.tiktok.privacy_level == "PUBLIC_TO_EVERYONE"


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


def test_instagram_credentials_are_required_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "instagram:\n  enabled: true\n")
    monkeypatch.delenv("INSTAGRAM_USERNAME")

    with pytest.raises(ConfigError, match="INSTAGRAM_USERNAME"):
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
