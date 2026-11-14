"""建立 PostgreSQL 連線。

連線參數來自 `fb_robot.config`（`.env` 或系統環境變數），缺少必要欄位時立即拋錯，
避免帶著半套配置去連庫。
"""

from __future__ import annotations

import psycopg2
from psycopg2.extensions import connection
from psycopg2.extras import RealDictCursor

from fb_robot.config import DatabaseSettings, get_settings


def create_connection(settings: DatabaseSettings | None = None) -> connection:
    """回傳一个新的 PostgreSQL 連線（預設使用真實游標 `RealDictCursor`）。"""
    settings = settings or get_settings().database
    return psycopg2.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.dbname,
        user=settings.user,
        password=settings.password,
        sslmode=settings.sslmode,
        connect_timeout=settings.connect_timeout,
        cursor_factory=RealDictCursor,
        application_name=settings.application_name,
    )
