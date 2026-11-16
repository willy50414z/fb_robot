"""fb_robot —— 抓取 Facebook 社團貼文並寫入 PostgreSQL。

外部使用方式：
  - 排程同步：`fb-robot run`（或 `python -m fb_robot`）
  - 程式呼叫：`from fb_robot import sync_group_posts`
"""

from fb_robot.config import (
    ApifySettings,
    AppSettings,
    ConfigError,
    DatabaseSettings,
    DedupSettings,
    get_settings,
)
from fb_robot.crawler import get_fb_group_posts
from fb_robot.db import create_connection
from fb_robot.service import run_sync_loop, sync_all_groups, sync_group_posts
from fb_robot.upsert import ensure_schema, process_items

__version__ = "0.1.0"

__all__ = [
    "AppSettings",
    "ApifySettings",
    "ConfigError",
    "DatabaseSettings",
    "DedupSettings",
    "__version__",
    "create_connection",
    "ensure_schema",
    "get_fb_group_posts",
    "get_settings",
    "process_items",
    "run_sync_loop",
    "sync_all_groups",
    "sync_group_posts",
]
