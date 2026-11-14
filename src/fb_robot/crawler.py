"""透過 Apify 的 getanyapi/facebook-group-posts-scraper 抓取 FB 社團貼文。"""

from __future__ import annotations

import logging

from apify_client import ApifyClient

from fb_robot.config import ApifySettings, get_settings

logger = logging.getLogger(__name__)


def get_fb_group_posts(
    group_id: int,
    max_items: int | None = None,
    settings: ApifySettings | None = None,
) -> list[dict]:
    """抓取指定 FB 社團的貼文並回傳貼文列表。

    `max_items` 未指定時使用設定值（`APIFY_MAX_ITEMS`，預設 30）。
    """
    settings = settings or get_settings().apify
    item_limit = max_items or settings.max_items

    client = ApifyClient(settings.api_key)
    run_input = {
        # 一次只傳一個社團連結，用 maxItems 控制總帖數，避免不必要的消耗
        "url": f"https://www.facebook.com/groups/{group_id}",
        "maxItems": item_limit,
    }

    logger.info("正在运行 Actor，请稍候... group_id=%s", group_id)
    run = client.actor(settings.actor).call(run_input=run_input)

    dataset_id = (
        run.get("defaultDatasetId") if isinstance(run, dict) else getattr(run, "default_dataset_id", None)
    )
    if not dataset_id:
        raise RuntimeError(f"無法取得 dataset id，run 回傳：{run!r}")
    logger.info("数据查看地址：https://console.apify.com/storage/datasets/%s", dataset_id)

    items: list[dict] = list(client.dataset(dataset_id).iterate_items())

    # 補上 group_id，避免來源資料缺少時無法正確寫入資料庫
    for item in items:
        item.setdefault("groupId", group_id)

    logger.info("group_id=%s 抓取完成，共 %s 筆", group_id, len(items))
    return items
