import logging
import time

from cralwer import get_fb_group_posts
from db import create_connection
from fb_group_posts_upsert import process_items


fb_group_map = {
    1923977991158636: "電腦.PC零組件.筆電./二手/全新 拍賣",
    133186676696605: "電腦零件買賣集中營",
    382189418643825: "電腦硬體買賣社團",
    507315352749401: "電腦零組件交易買賣一元起標 CPU 主機板 顯示卡 硬碟 SSD 記憶體 RAM",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def sync_group_posts(group_id: int) -> dict[str, int]:
    """抓取單一社團貼文並寫入資料庫。"""
    posts = get_fb_group_posts(group_id)
    conn = create_connection()
    try:
        stats = process_items(conn, posts)
    finally:
        conn.close()
    return stats


if __name__ == "__main__":
    while True:
        for group_id, group_name in fb_group_map.items():
            logger.info("開始同步社團 %s (%s)", group_id, group_name)
            try:
                stats = sync_group_posts(group_id)
                logger.info("同步完成 %s (%s): %s", group_id, group_name, stats)
            except Exception:
                logger.exception("同步失敗 %s (%s)", group_id, group_name)
        time.sleep(120)