"""串接爬蟲與資料庫：抓取 Facebook 社團貼文並寫入。"""

from __future__ import annotations

import logging
import time

from fb_robot.config import AppSettings, get_settings
from fb_robot.crawler import get_fb_group_posts
from fb_robot.db import create_connection
from fb_robot.upsert import process_items

logger = logging.getLogger(__name__)


def sync_group_posts(group_id: int, settings: AppSettings | None = None) -> dict[str, int]:
    """抓取單一社團貼文並寫入資料庫，回傳統計結果。"""
    settings = settings or get_settings()
    posts = get_fb_group_posts(group_id, settings=settings.apify)
    conn = create_connection(settings.database)
    try:
        return process_items(conn, posts, settings.dedup)
    finally:
        conn.close()


def sync_all_groups(settings: AppSettings | None = None) -> dict[int, dict[str, int]]:
    """依設定清單同步所有社團，回傳各社團的統計結果。"""
    settings = settings or get_settings()
    results: dict[int, dict[str, int]] = {}
    for group_id, group_name in settings.groups.items():
        logger.info("開始同步社團 %s (%s)", group_id, group_name)
        try:
            stats = sync_group_posts(group_id, settings)
            results[group_id] = stats
            logger.info("同步完成 %s (%s): %s", group_id, group_name, stats)
        except Exception:
            results[group_id] = {"failed": 1}
            logger.exception("同步失敗 %s (%s)", group_id, group_name)
    return results


def run_sync_loop(settings: AppSettings | None = None) -> None:
    """無限期輪詢同步，每一輪之間等待 `interval_seconds` 秒。"""
    settings = settings or get_settings()
    while True:
        sync_all_groups(settings)
        logger.info("等待 %s 秒後開始下一輪同步", settings.interval_seconds)
        time.sleep(settings.interval_seconds)
