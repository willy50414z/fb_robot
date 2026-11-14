"""
db.py —— 从 .env 读取连接参数并建立 PostgreSQL 连接
"""

import os

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

# 在模块导入时加载一次 .env
# override=False：若系统环境变量已存在，则不覆盖，方便生产环境用系统变量注入
load_dotenv(override=False)


REQUIRED_KEYS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]


def _get_env(key: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(key, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"缺少必需的环境变量：{key}（请检查 .env 文件）")
    return value or ""


def create_connection() -> "psycopg2.extensions.connection":
    """
    从 .env 读取参数并返回一个新的 PostgreSQL 连接。
    缺少必需字段时立即抛错，避免带着半套配置去连库。
    """
    # 一次性检查所有必需项，报错信息更完整
    missing = [k for k in REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            f"缺少必需的环境变量：{', '.join(missing)}。"
            f"请复制 .env.example 为 .env 并填写。"
        )

    return psycopg2.connect(
        host=_get_env("DB_HOST", required=True),
        port=int(_get_env("DB_PORT", default="5432")),
        dbname=_get_env("DB_NAME", required=True),
        user=_get_env("DB_USER", required=True),
        password=_get_env("DB_PASSWORD", required=True),
        sslmode=_get_env("DB_SSLMODE", default="prefer"),
        connect_timeout=int(_get_env("DB_CONNECT_TIMEOUT", default="10")),
        cursor_factory=RealDictCursor,
        application_name="fb_group_posts_upsert",
    )