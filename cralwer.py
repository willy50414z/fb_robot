import os
from typing import Iterable

from apify_client import ApifyClient
from dotenv import load_dotenv


def get_fb_group_posts(group_id: int, max_items: int = 30) -> list[dict]:
    """抓取指定 FB 社團的貼文並回傳貼文列表。"""
    load_dotenv()

    api_key = os.getenv("APIFY_API_KEY")
    if not api_key:
        raise RuntimeError("缺少必需的環境變數：APIFY_API_KEY（請檢查 .env 檔案）")

    # 1. 初始化客户端
    client = ApifyClient(api_key)

    # 2. 构建输入参数
    run_input = {
        # 关键改动：用 "url" 代替 "startUrls"，并且一次只传一个社团链接
        "url": f"https://www.facebook.com/groups/{group_id}",
        # 用 maxItems 控制总帖数，设小一点避免不必要的消耗
        "maxItems": max_items,
    }

    # 3. 运行 Actor 并等待完成
    print(f"正在运行 Actor，请稍候... group_id={group_id}")
    run = client.actor("getanyapi/facebook-group-posts-scraper").call(run_input=run_input)

    # 4. 从数据集读取结果
    dataset_id = run.get("defaultDatasetId") if isinstance(run, dict) else getattr(run, "default_dataset_id", None)
    if not dataset_id:
        raise RuntimeError(f"無法取得 dataset id，run 回傳：{run!r}")
    print(f"数据查看地址：https://console.apify.com/storage/datasets/{dataset_id}")

    items = list(client.dataset(dataset_id).iterate_items())

    # 補上 group_id，避免來源資料缺少時無法正確寫入資料庫
    for item in items:
        item.setdefault("groupId", group_id)

    print(f"group_id={group_id} 抓取完成，共 {len(items)} 筆")
    return items