from pathlib import Path

import pytest

from app.core.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "12345")
    monkeypatch.setenv("WHATSAPP_API_KEY", "w" * 32)
    monkeypatch.setenv("WHATSAPP_SESSION_ID", "whatsapp-session")
    monkeypatch.setenv("MEDIA_PUBLIC_BASE_URL", "https://media.example/")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "graph-token")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841400000000000")
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "client-key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("TIKTOK_REFRESH_TOKEN", "refresh-token")
    for key in (
        "TELEGRAM_API_BASE_URL",
        "TELEGRAM_API_LOCAL",
        "TELEGRAM_API_SERVER_FILES_PATH",
        "TELEGRAM_API_CLIENT_FILES_PATH",
        "INSTAGRAM_API_VERSION",
        "TIKTOK_PRIVACY_LEVEL",
        "TIKTOK_UPLOAD_CHUNK_SIZE",
        "WHATSAPP_ENGINE",
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_API_VERSION",
    ):
        monkeypatch.delenv(key, raising=False)


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
    assert config.instagram.access_token == "graph-token"
    assert config.instagram.ig_user_id == "17841400000000000"
    assert config.instagram.api_base_url == "https://graph.facebook.com"
    assert config.instagram.api_version == "v25.0"
    assert config.instagram.media_base_url == "https://media.example"
    assert config.instagram.request_timeout == 120
    assert config.tiktok is not None
    assert config.tiktok.client_key == "client-key"
    assert config.tiktok.client_secret == "client-secret"
    assert config.tiktok.refresh_token == "refresh-token"
    assert config.tiktok.api_base_url == "https://open.tiktokapis.com"
    assert config.tiktok.privacy_level == "PUBLIC_TO_EVERYONE"
    assert config.tiktok.auto_add_music
    assert config.tiktok.chunk_size == 10 * 1024**2
    assert config.telegram_api.base_url == "https://api.telegram.org"
    assert not config.telegram_api.local
    assert config.whatsapp is not None
    assert config.whatsapp.session_id == "whatsapp-session"
    assert config.whatsapp.media_max_bytes == 100 * 1024**2


def test_cloud_engine_reads_its_own_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(
        tmp_path / "config.yaml",
        """
whatsapp:
  groups:
    - jid: "120363000000000000"
      name: "Группа"
  contacts:
    - phone: "+7 900 123-45-67"
      name: "Клиент"
""",
    )
    monkeypatch.setenv("WHATSAPP_ENGINE", "cloud")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "graph-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123456789")

    config = load_config(config_path, tmp_path / ".env")

    assert config.whatsapp_engine == "cloud"
    assert config.whatsapp is None
    assert config.whatsapp_cloud is not None
    assert config.whatsapp_cloud.access_token == "graph-token"
    assert config.whatsapp_cloud.phone_number_id == "123456789"
    assert config.whatsapp_cloud.api_base_url == "https://graph.facebook.com"
    assert [(target.kind, target.key) for target in config.targets] == [
        ("group", "120363000000000000"),
        ("contact", "79001234567"),
    ]


def test_cloud_engine_rejects_whatsapp_channels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(
        tmp_path / "config.yaml",
        "whatsapp:\n  channels:\n    - jid: 1234@newsletter\n      name: Канал\n",
    )
    monkeypatch.setenv("WHATSAPP_ENGINE", "cloud")

    with pytest.raises(ConfigError, match="no official API"):
        load_config(config_path, tmp_path / ".env")


def test_openwa_engine_rejects_contact_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(
        tmp_path / "config.yaml",
        'whatsapp:\n  contacts:\n    - phone: "79001234567"\n      name: Клиент\n',
    )
    monkeypatch.setenv("WHATSAPP_ENGINE", "openwa")

    with pytest.raises(ConfigError, match="WHATSAPP_ENGINE=cloud"):
        load_config(config_path, tmp_path / ".env")


def test_cloud_credentials_are_required_when_engine_is_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(
        tmp_path / "config.yaml",
        'whatsapp:\n  groups:\n    - jid: "1203630"\n      name: Группа\n',
    )
    monkeypatch.setenv("WHATSAPP_ENGINE", "cloud")

    with pytest.raises(ConfigError, match="WHATSAPP_ACCESS_TOKEN"):
        load_config(config_path, tmp_path / ".env")


def test_unknown_whatsapp_engine_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "telegram: {}\n")
    monkeypatch.setenv("WHATSAPP_ENGINE", "baileys")

    with pytest.raises(ConfigError, match="WHATSAPP_ENGINE"):
        load_config(config_path, tmp_path / ".env")


def test_invalid_contact_phone_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(
        tmp_path / "config.yaml",
        "whatsapp:\n  contacts:\n    - phone: not-a-number\n      name: Клиент\n",
    )
    monkeypatch.setenv("WHATSAPP_ENGINE", "cloud")

    with pytest.raises(ConfigError, match=r"E\.164"):
        load_config(config_path, tmp_path / ".env")


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


def test_whatsapp_target_jid_must_match_target_kind(tmp_path: Path) -> None:
    config_path = _write(
        tmp_path / "config.yaml",
        "whatsapp:\n  channels:\n    - jid: group@g.us\n      name: Wrong\n",
    )

    with pytest.raises(ConfigError, match="@newsletter"):
        load_config(config_path, tmp_path / ".env")


def test_whatsapp_credentials_are_required_when_targets_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(
        tmp_path / "config.yaml",
        "whatsapp:\n  groups:\n    - jid: group@g.us\n      name: Group\n",
    )
    monkeypatch.delenv("WHATSAPP_SESSION_ID")

    with pytest.raises(ConfigError, match="WHATSAPP_SESSION_ID"):
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


def test_instagram_user_id_is_required_when_instagram_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "instagram:\n  enabled: true\n")
    monkeypatch.delenv("INSTAGRAM_USER_ID")

    with pytest.raises(ConfigError, match="INSTAGRAM_USER_ID"):
        load_config(config_path, tmp_path / ".env")


def test_tiktok_credentials_are_required_when_tiktok_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "tiktok:\n  enabled: true\n")
    monkeypatch.delenv("TIKTOK_CLIENT_SECRET")

    with pytest.raises(ConfigError, match="TIKTOK_CLIENT_SECRET"):
        load_config(config_path, tmp_path / ".env")


def test_invalid_tiktok_privacy_level_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "tiktok:\n  enabled: true\n")
    monkeypatch.setenv("TIKTOK_PRIVACY_LEVEL", "EVERYONE")

    with pytest.raises(ConfigError, match="TIKTOK_PRIVACY_LEVEL"):
        load_config(config_path, tmp_path / ".env")


def test_public_media_url_is_required_for_official_apis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "instagram:\n  enabled: true\n")
    monkeypatch.delenv("MEDIA_PUBLIC_BASE_URL")

    with pytest.raises(ConfigError, match="MEDIA_PUBLIC_BASE_URL"):
        load_config(config_path, tmp_path / ".env")


def test_public_media_url_must_use_https(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "tiktok:\n  enabled: true\n")
    monkeypatch.setenv("MEDIA_PUBLIC_BASE_URL", "http://media.example")

    with pytest.raises(ConfigError, match="https://"):
        load_config(config_path, tmp_path / ".env")


def test_tiktok_chunk_size_outside_api_limits_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write(tmp_path / "config.yaml", "tiktok:\n  enabled: true\n")
    monkeypatch.setenv("TIKTOK_UPLOAD_CHUNK_SIZE", str(1024))

    with pytest.raises(ConfigError, match="TIKTOK_UPLOAD_CHUNK_SIZE"):
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
