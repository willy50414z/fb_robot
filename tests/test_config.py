"""`fb_robot.config` 的環境解析行為。"""

import pytest

from fb_robot.config import DEFAULT_GROUPS, AppSettings, ConfigError, DedupSettings, parse_groups

DB_ENV = {
    "DB_HOST": "db.internal",
    "DB_PORT": "6543",
    "DB_NAME": "fb_scraper",
    "DB_USER": "fb_app",
    "DB_PASSWORD": "secret",
    "APIFY_API_KEY": "apify_api_test",
}


def _set_env(monkeypatch, extra=None):
    for key in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "APIFY_API_KEY",
                "DB_SSLMODE", "DB_CONNECT_TIMEOUT", "APIFY_ACTOR", "APIFY_MAX_ITEMS",
                "FB_SYNC_INTERVAL_SECONDS", "FB_GROUP_MAP_JSON", "FB_DEDUP_SIMHASH_MAX_DISTANCE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in DB_ENV.items():
        monkeypatch.setenv(key, value)
    for key, value in (extra or {}).items():
        monkeypatch.setenv(key, value)


def test_from_env_uses_defaults(monkeypatch):
    _set_env(monkeypatch)

    settings = AppSettings.from_env()

    assert settings.database.host == "db.internal"
    assert settings.database.port == 6543
    assert settings.database.sslmode == "prefer"
    assert settings.database.connect_timeout == 10
    assert settings.apify.actor == "getanyapi/facebook-group-posts-scraper"
    assert settings.apify.max_items == 30
    assert settings.dedup == DedupSettings()
    assert settings.interval_seconds == 120
    assert settings.groups == DEFAULT_GROUPS


def test_from_env_reads_overrides(monkeypatch):
    _set_env(monkeypatch, {
        "DB_SSLMODE": "require",
        "DB_CONNECT_TIMEOUT": "3",
        "APIFY_ACTOR": "someone/other-actor",
        "APIFY_MAX_ITEMS": "16",
        "FB_SYNC_INTERVAL_SECONDS": "900",
        "FB_DEDUP_SIMHASH_MAX_DISTANCE": "5",
        "FB_DEDUP_LOOKBACK_DAYS": "7",
        "FB_DEDUP_LOOKBACK_LIMIT": "50",
        'FB_GROUP_MAP_JSON': '{"133186676696605": "電腦零件買賣集中營"}',
    })

    settings = AppSettings.from_env()

    assert settings.database.sslmode == "require"
    assert settings.database.connect_timeout == 3
    assert settings.apify.actor == "someone/other-actor"
    assert settings.apify.max_items == 16
    assert settings.interval_seconds == 900
    assert settings.dedup.simhash_max_distance == 5
    assert settings.dedup.lookback_days == 7
    assert settings.dedup.lookback_limit == 50
    assert settings.groups == {133186676696605: "電腦零件買賣集中營"}


def test_missing_required_key_lists_every_gap(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.delenv("DB_NAME")
    monkeypatch.delenv("DB_USER")

    with pytest.raises(ConfigError) as exc:
        AppSettings.from_env()

    message = str(exc.value)
    assert "DB_NAME" in message and "DB_USER" in message


def test_non_integer_env_raises_config_error(monkeypatch):
    _set_env(monkeypatch, {"DB_PORT": "not-a-port"})

    with pytest.raises(ConfigError, match="DB_PORT"):
        AppSettings.from_env()


def test_parse_groups_rejects_bad_json():
    with pytest.raises(ConfigError):
        parse_groups('{"group_id"}')
    with pytest.raises(ConfigError):
        parse_groups("{}")


def test_parse_groups_falls_back_to_default_groups():
    assert parse_groups(None)  # 未設定時使用內建社團清單
    assert parse_groups(None) == parse_groups("")
