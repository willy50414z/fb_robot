"""貼文寫入邏輯：canonical 判定、版本歷史與跨社團 mapping。

設計要點：
  - canonical 貼文以主表記錄保存；同一作者在不同社團的相似貼文不重複建立主表記錄。
  - fb_post_group_map 記錄貼文在各社團的出現來源（1 對多）。
  - content_hash 對正規化正文做 SHA-256；simhash 用於近似相似比對。
  - 內容變更時仍維持主表/歷史表版本邏輯（僅針對 canonical 貼文）。
  - 每條來源貼文一個交易邊界，單條失敗不影響其他條。
"""

import logging
from collections.abc import Iterable

from fb_robot.config import DedupSettings
from fb_robot.upsert.hashing import (
    compute_simhash,
    hamming_distance64,
    normalize_text,
    to_simhash_int,
)
from fb_robot.upsert.records import _row_get, build_params, pick
from fb_robot.upsert.schema import ensure_schema
from fb_robot.upsert.sql import (
    SQL_CLOSE_CURRENT_HISTORY,
    SQL_INSERT_HISTORY_FIRST,
    SQL_INSERT_HISTORY_NEW,
    SQL_INSERT_MAIN,
    SQL_SELECT_AUTHOR_RECENT_POSTS,
    SQL_SELECT_CANONICAL_FOR_UPDATE,
    SQL_SELECT_LATEST_MAPPED_HASH,
    SQL_SELECT_MAPPING_BY_SOURCE,
    SQL_TOUCH_CURRENT_HISTORY,
    SQL_TOUCH_MAIN,
    SQL_UPDATE_MAIN_CONTENT_CHANGED,
    SQL_UPSERT_MAPPING,
)

logger = logging.getLogger(__name__)

def find_similar_canonical_post(
    cur,
    params: dict,
    dedup: DedupSettings | None = None,
) -> str | None:
    """尋找同作者近期內容相同/高度相似的 canonical 貼文（跨社團）。"""
    dedup = dedup or DedupSettings.from_env()
    author_id = params.get("author_id")
    if not author_id or not normalize_text(params.get("text")):
        return None

    cur.execute(
        SQL_SELECT_AUTHOR_RECENT_POSTS,
        {
            "author_id": author_id,
            "post_id": params["post_id"],
            "days": dedup.lookback_days,
            "limit": dedup.lookback_limit,
        },
    )
    rows = cur.fetchall() or []
    new_hash = params["content_hash"]
    new_simhash = int(params["simhash"] or 0)

    # 先找正規化 hash 完全相同
    for row in rows:
        if _row_get(row, "content_hash", 1) == new_hash:
            return str(_row_get(row, "post_id", 0))

    # 再找 SimHash 高度相似
    for row in rows:
        cand_simhash = to_simhash_int(_row_get(row, "simhash", 3))
        if cand_simhash is None:
            cand_text = _row_get(row, "text", 2) or ""
            cand_simhash = compute_simhash(cand_text)
        if hamming_distance64(new_simhash, cand_simhash) <= dedup.simhash_max_distance:
            return str(_row_get(row, "post_id", 0))
    return None
def record_mapping(cur, canonical_post_id: str, params: dict) -> None:
    """記錄來源貼文對應的 canonical 與 group mapping（供後續更新參考）。"""
    cur.execute(
        SQL_UPSERT_MAPPING,
        {
            "canonical_post_id": canonical_post_id,
            "group_id": params.get("group_id"),
            "group_post_id": params["post_id"],
            "content_hash": params.get("content_hash"),
        },
    )
def _get_existing_mapping(cur, group_post_id: str):
    cur.execute(SQL_SELECT_MAPPING_BY_SOURCE, (group_post_id,))
    return cur.fetchone()
def _apply_latest_content_to_canonical(
    cur,
    canonical_post_id: str,
    params: dict,
    old_hash: str | None,
) -> str:
    if old_hash != params["content_hash"]:
        cur.execute(SQL_CLOSE_CURRENT_HISTORY, params)
        cur.execute(SQL_INSERT_HISTORY_NEW, params)
        cur.execute(SQL_UPDATE_MAIN_CONTENT_CHANGED, params)
        return "updated_content"
    cur.execute(SQL_TOUCH_MAIN, params)
    cur.execute(SQL_TOUCH_CURRENT_HISTORY, params)
    return "touched"
def upsert_post(cur, item: dict, dedup: DedupSettings | None = None) -> str:
    """
    在给定 cursor 上处理一条帖子。
    返回 'inserted' / 'updated_content' / 'touched' / 'deduped'，供日志统计。

    會先參考 fb_post_group_map 決定 canonical；同作者跨社團相似貼文僅保留一筆 canonical，
    其他來源記錄於 mapping。若 mapping 的來源貼文先被編輯，canonical 會更新為最新編輯內容。
    """
    dedup = dedup or DedupSettings.from_env()
    params = build_params(item)

    # 1) 來源貼文是否已存在於 mapping（已知的重複來源）或本身即為 canonical
    mapping = _get_existing_mapping(cur, params["post_id"])

    cur.execute(SQL_SELECT_CANONICAL_FOR_UPDATE, (params["post_id"],))
    canonical_row = cur.fetchone()
    if canonical_row is not None:
        canonical_post_id = params["post_id"]
        old_hash = _row_get(canonical_row, "content_hash", 1)
        # 先記錄本次來源狀態，再依最新編輯結果更新 canonical
        record_mapping(cur, canonical_post_id, params)
        # 若有其他 mapped 來源，取最新編輯內容（依 last_seen_at）
        cur.execute(SQL_SELECT_LATEST_MAPPED_HASH, (canonical_post_id,))
        latest = cur.fetchone()
        latest_hash = _row_get(latest, "content_hash", 1) if latest else None
        if latest_hash and latest_hash != params["content_hash"]:
            # 目前 canonical 來源不是最新編輯；保留最新來源內容已由其上次/本次流程決定，
            # 此處僅刷新時間與指標，不覆蓋為較舊內容。
            return "touched"
        return _apply_latest_content_to_canonical(cur, canonical_post_id, params, old_hash)

    if mapping is not None:
        canonical_post_id = str(_row_get(mapping, "canonical_post_id", 0))
        prev_source_hash = _row_get(mapping, "content_hash", 2)
        # 更新 mapping 中該來源的最新狀態
        record_mapping(cur, canonical_post_id, params)

        # 鎖定 canonical，判斷此來源編輯是否應成為最新內容
        cur.execute(SQL_SELECT_CANONICAL_FOR_UPDATE, (canonical_post_id,))
        crow = cur.fetchone()
        if crow is None:
            # 映射指向不存在的 canonical（異常資料），將此來源提升為 canonical
            cur.execute(SQL_INSERT_MAIN, params)
            cur.execute(SQL_INSERT_HISTORY_FIRST, params)
            record_mapping(cur, params["post_id"], params)
            return "inserted"

        cur_old_hash = _row_get(crow, "content_hash", 1)
        source_changed = prev_source_hash is None or prev_source_hash != params["content_hash"]
        if source_changed and params["content_hash"] != cur_old_hash:
            # 來源貼文內容有變化且與 canonical 不同：視為較新的編輯，更新 canonical
            params_for_canonical = {**params, "post_id": canonical_post_id}
            _apply_latest_content_to_canonical(cur, canonical_post_id, params_for_canonical, cur_old_hash)
            return "updated_content"

        # 來源內容未變或與 canonical 相同：僅刷新 canonical 指標/時間
        touch_params = {**params, "post_id": canonical_post_id}
        cur.execute(SQL_TOUCH_MAIN, touch_params)
        cur.execute(SQL_TOUCH_CURRENT_HISTORY, touch_params)
        return "deduped" if not source_changed else "touched"

    # 2) 全新來源：先做同作者跨社團相似比對
    canonical_post_id = find_similar_canonical_post(cur, params, dedup)
    if canonical_post_id:
        record_mapping(cur, canonical_post_id, params)
        # 若新發現的重複來源內容與 canonical 不同，視為最新編輯內容
        cur.execute(SQL_SELECT_CANONICAL_FOR_UPDATE, (canonical_post_id,))
        crow = cur.fetchone()
        if crow is not None:
            cur_old_hash = _row_get(crow, "content_hash", 1)
            if params["content_hash"] != cur_old_hash:
                params_for_canonical = {**params, "post_id": canonical_post_id}
                _apply_latest_content_to_canonical(cur, canonical_post_id, params_for_canonical, cur_old_hash)
                return "updated_content"
            touch_params = {**params, "post_id": canonical_post_id}
            cur.execute(SQL_TOUCH_MAIN, touch_params)
            cur.execute(SQL_TOUCH_CURRENT_HISTORY, touch_params)
        return "deduped"

    # ---------- 情况 A：全新 canonical 帖子 ----------
    cur.execute(SQL_INSERT_MAIN, params)
    cur.execute(SQL_INSERT_HISTORY_FIRST, params)
    return "inserted"
def process_items(
    conn,
    items: Iterable[dict],
    dedup: DedupSettings | None = None,
) -> dict[str, int]:
    """
    逐条处理一批帖子。每条帖子一个独立事务，单条失败不影响其他条。
    返回各类结果的计数，便于日志汇总。
    """
    dedup = dedup or DedupSettings.from_env()
    ensure_schema(conn)
    stats = {"inserted": 0, "updated_content": 0, "touched": 0, "deduped": 0, "failed": 0}

    for idx, item in enumerate(items, start=1):
        post_id = pick(item, "id", "postId", "post_id", default="<unknown>")
        try:
            with conn:           # with conn 在 psycopg2 中即为事务边界
                with conn.cursor() as cur:
                    result = upsert_post(cur, item, dedup)
            stats[result] += 1
            logger.info("第 %d 条 post_id=%s 结果=%s", idx, post_id, result)
        except Exception as exc:
            stats["failed"] += 1
            logger.exception("第 %d 条 post_id=%s 处理失败：%s", idx, post_id, exc)
            # with conn 已自动 rollback 该条的事务

    return stats
