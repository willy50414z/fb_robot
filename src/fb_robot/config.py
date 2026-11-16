"""集中管理環境設定：資料庫、Apify、去重參數與社團清單。

所有環境變數只在此模組解析，其他模組透過 `get_settings()` 取得已驗證的設定物件，
避免各處散落 `os.getenv`。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from dotenv import find_dotenv, load_dotenv

REQUIRED_DB_KEYS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")

#: 預設同步的 Facebook 社團（group_id -> 社團名稱）
DEFAULT_GROUPS: dict[int, str] = {
    1923977991158636: "電腦.PC零組件.筆電./二手/全新 拍賣",
    133186676696605: "電腦零件買賣集中營",
    382189418643825: "電腦硬體買賣社團",
    507315352749401: "電腦零組件交易買賣一元起標 CPU 主機板 顯示卡 硬碟 SSD 記憶體 RAM",
}


class ConfigError(RuntimeError):
    """環境變數缺少或格式不正確。"""


def load_env(dotenv_path: str | os.PathLike[str] | None = None, override: bool = False) -> None:
    """載入 `.env` 檔案（預設從目前工作目錄往上尋找）。

    找不到 `.env` 時不報錯，讓系統環境變數（生產環境注入）照常生效。
    `override=False`：已存在的环境变量不会被 `.env` 覆写。
    """
    path = dotenv_path or find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=override)


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _require(name: str) -> str:
    value = _get(name)
    if value is None:
        raise ConfigError(f"缺少必需的環境變數：{name}（請檢查 .env 檔案）")
    return value


def _as_int(name: str, default: int) -> int:
    value = _get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"環境變數 {name} 必須是整數，目前的值：{value!r}") from exc


@dataclass(frozen=True)
class DatabaseSettings:
    """PostgreSQL 連線參數。"""

    host: str
    port: int
    dbname: str
    user: str
    password: str
    sslmode: str = "prefer"
    connect_timeout: int = 10
    application_name: str = "fb_robot"

    @classmethod
    def from_env(cls) -> DatabaseSettings:
        missing = [key for key in REQUIRED_DB_KEYS if not os.getenv(key)]
        if missing:
            raise ConfigError(
                f"缺少必需的環境變數：{', '.join(missing)}。請複製 .env.example 為 .env 並填寫。"
            )
        return cls(
            host=_require("DB_HOST"),
            port=_as_int("DB_PORT", 5432),
            dbname=_require("DB_NAME"),
            user=_require("DB_USER"),
            password=_require("DB_PASSWORD"),
            sslmode=_get("DB_SSLMODE", "prefer") or "prefer",
            connect_timeout=_as_int("DB_CONNECT_TIMEOUT", 10),
        )


@dataclass(frozen=True)
class ApifySettings:
    """Apify Actor 抓取參數。"""

    api_key: str
    actor: str = "getanyapi/facebook-group-posts-scraper"
    max_items: int = 30

    @classmethod
    def from_env(cls) -> ApifySettings:
        return cls(
            api_key=_require("APIFY_API_KEY"),
            actor=_get("APIFY_ACTOR", cls.actor) or cls.actor,
            max_items=_as_int("APIFY_MAX_ITEMS", cls.max_items),
        )


@dataclass(frozen=True)
class DedupSettings:
    """同作者跨社團相似貼文比對參數。"""

    simhash_max_distance: int = 3
    lookback_days: int = 30
    lookback_limit: int = 200

    @classmethod
    def from_env(cls) -> DedupSettings:
        return cls(
            simhash_max_distance=_as_int("FB_DEDUP_SIMHASH_MAX_DISTANCE", cls.simhash_max_distance),
            lookback_days=_as_int("FB_DEDUP_LOOKBACK_DAYS", cls.lookback_days),
            lookback_limit=_as_int("FB_DEDUP_LOOKBACK_LIMIT", cls.lookback_limit),
        )


@dataclass(frozen=True)
class LlmSettings:
    """本地/OpenAI 相容端點的貼文分類參數。"""

    model: str
    api_base: str | None = None
    api_key: str = "local"
    temperature: float = 0.0
    max_tokens: int = 1500
    timeout_seconds: int = 120
    #: json_schema（送 output.schema.json）/ json_object / none（只靠 prompt）
    response_format: str = "json_schema"
    prompt_version: str = "v1"
    min_confidence: float = 0.6
    max_chars: int = 4000

    @classmethod
    def from_env(cls) -> LlmSettings:
        response_format = _get("FB_LLM_RESPONSE_FORMAT", cls.response_format) or cls.response_format
        if response_format not in ("json_schema", "json_object", "none"):
            raise ConfigError("FB_LLM_RESPONSE_FORMAT 只能是 json_schema / json_object / none")
        try:
            temperature = float(_get("FB_LLM_TEMPERATURE", str(cls.temperature)))
            min_confidence = float(_get("FB_LLM_MIN_CONFIDENCE", str(cls.min_confidence)))
        except ValueError as exc:
            raise ConfigError(f"FB_LLM_TEMPERATURE / FB_LLM_MIN_CONFIDENCE 必須是數字：{exc}") from exc
        return cls(
            model=_require("FB_LLM_MODEL"),
            api_base=_get("FB_LLM_API_BASE"),
            api_key=_get("FB_LLM_API_KEY", cls.api_key) or cls.api_key,
            temperature=temperature,
            max_tokens=_as_int("FB_LLM_MAX_TOKENS", cls.max_tokens),
            timeout_seconds=_as_int("FB_LLM_TIMEOUT_SECONDS", cls.timeout_seconds),
            response_format=response_format,
            prompt_version=_get("FB_LLM_PROMPT_VERSION", cls.prompt_version) or cls.prompt_version,
            min_confidence=min_confidence,
            max_chars=_as_int("FB_LLM_MAX_CHARS", cls.max_chars),
        )


def parse_groups(raw: str | None) -> dict[int, str]:
    """解析 `FB_GROUP_MAP_JSON`（`{"<group_id>": "<name>"}`）；未設定時回傳預設社團。"""
    if not raw:
        return dict(DEFAULT_GROUPS)
    try:
        parsed = {int(key): str(value) for key, value in json.loads(raw).items()}
    except (AttributeError, TypeError, ValueError) as exc:
        raise ConfigError(
            f'環境變數 FB_GROUP_MAP_JSON 必須是 {{"group_id": "name"}} 形式的 JSON：{exc}'
        ) from exc
    if not parsed:
        raise ConfigError("環境變數 FB_GROUP_MAP_JSON 不能是空的社團清單")
    return parsed


@dataclass(frozen=True)
class AppSettings:
    """主程式所需的全部設定。"""

    database: DatabaseSettings
    apify: ApifySettings
    dedup: DedupSettings = field(default_factory=DedupSettings)
    groups: dict[int, str] = field(default_factory=lambda: dict(DEFAULT_GROUPS))
    interval_seconds: int = 120

    @classmethod
    def from_env(cls) -> AppSettings:
        return cls(
            database=DatabaseSettings.from_env(),
            apify=ApifySettings.from_env(),
            dedup=DedupSettings.from_env(),
            groups=parse_groups(_get("FB_GROUP_MAP_JSON")),
            interval_seconds=_as_int("FB_SYNC_INTERVAL_SECONDS", cls.interval_seconds),
        )


_settings: AppSettings | None = None


def get_settings(refresh: bool = False) -> AppSettings:
    """取得（並快取）目前程序的設定，必要時先載入 `.env`。"""
    global _settings
    if _settings is None or refresh:
        load_env()
        _settings = AppSettings.from_env()
    return _settings
