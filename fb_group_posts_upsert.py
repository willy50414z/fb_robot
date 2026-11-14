"""
fb_group_posts_upsert.py

用途：
  把 getanyapi/facebook-group-posts-scraper 返回的帖子逐条写入
  fb_group_posts（最新状态）与 fb_group_posts_history（版本历史），
  並在同作者跨社團發布相似貼文時進行排除（去重）。

设计要点：
  - canonical 貼文以主表記錄保存；同一作者在不同社團的相似貼文不重複建立主表記錄。
  - fb_group_post_occurrences 記錄貼文在各社團的出現來源（1 對多）。
  - content_hash 對正規化正文做 SHA-256；simhash 用於近似相似比對。
  - 內容變更時仍維持主表/歷史表版本邏輯（僅針對 canonical 貼文）。
  - 每條來源貼文一個交易邊界，單條失敗不影響其他條。
"""

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

SIMHASH_MAX_DISTANCE = 3
DEDUP_LOOKBACK_DAYS = 30
DEDUP_LOOKBACK_LIMIT = 200


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def normalize_text(text: str | None) -> str:
    """正規化本文供比對：去首尾空白、壓縮空白、移除常見零寬字元。"""
    s = (text or "").replace("\u200b", "").replace("\ufeff", "")
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def make_content_hash(text: str | None) -> str:
    """對正規化正文做 SHA-256，用於判斷內容是否相同/變化。"""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def _simhash_tokens(text: str) -> list[str]:
    norm = normalize_text(text)
    if not norm:
        return []
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", norm)
    if len(tokens) >= 2:
        return tokens
    # 極短或無分詞結果時，退回字元 n-gram
    return [norm[i:i + 3] for i in range(max(1, len(norm) - 2))] or [norm]


def compute_simhash(text: str | None) -> int:
    """計算 64-bit SimHash（以 signed BIGINT 範圍儲存）。"""
    tokens = _simhash_tokens(text or "")
    if not tokens:
        return 0
    weights = [0] * 64
    for token in tokens:
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        for i in range(64):
            bit = 1 << i
            weights[i] += 1 if (h & bit) else -1
    value = 0
    for i, w in enumerate(weights):
        if w >= 0:
            value |= (1 << i)
    # 轉為 PostgreSQL BIGINT 可接受的 signed 64-bit
    if value >= 2**63:
        value -= 2**64
    return value


def _simhash_u64(value: int) -> int:
    return value & ((1 << 64) - 1)


def hamming_distance64(a: int, b: int) -> int:
    return (_simhash_u64(a) ^ _simhash_u64(b)).bit_count()


def to_simhash_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        # DB bigint 可能以 decimal/str 形式回來
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_get(row: Any, key: str, idx: int = 0):
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return row[idx]


def to_ts(value: Any) -> datetime | None:
    """
    把 Actor 返回的 publishTime 转成 TIMESTAMPTZ。
    兼容 unix 秒（int/float）、unix 毫秒、ISO 字符串、datetime。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # 13 位以上视为毫秒
        ts = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit():
            return to_ts(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def pick(item: dict, *keys: str, default=None):
    """按顺序尝试多个候选键名，返回第一个非 None 的值。"""
    for k in keys:
        v = item.get(k)
        if v is not None:
            return v
    return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# SQL 语句
# ---------------------------------------------------------------------------

# 情况 A：全新帖子 —— 主表 INSERT
SQL_INSERT_MAIN = """
INSERT INTO fb_group_posts (
    post_id, group_id, group_name, post_url,
    author_id, author_name,
    text, publish_time,
    reaction_count, comment_count, share_count,
    content_hash, simhash, start_date, end_date,
    created_at, updated_at
) VALUES (
    %(post_id)s, %(group_id)s, %(group_name)s, %(post_url)s,
    %(author_id)s, %(author_name)s,
    %(text)s, %(publish_time)s,
    %(reaction_count)s, %(comment_count)s, %(share_count)s,
    %(content_hash)s, %(simhash)s, NOW(), NOW(),
    NOW(), NOW()
)
"""

# 情况 A：全新帖子 —— 历史表插入首版
SQL_INSERT_HISTORY_FIRST = """
INSERT INTO fb_group_posts_history (
    post_id, group_id, publish_time,
    text, content_hash,
    reaction_count, comment_count, share_count,
    valid_from, valid_to, created_at
) VALUES (
    %(post_id)s, %(group_id)s, %(publish_time)s,
    %(text)s, %(content_hash)s,
    %(reaction_count)s, %(comment_count)s, %(share_count)s,
    NOW(), NULL, NOW()
)
"""

# 情况 B：内容变化 —— 关闭旧版本（同时把旧版本的动态指标刷成最后看到的值）
SQL_CLOSE_CURRENT_HISTORY = """
UPDATE fb_group_posts_history
SET valid_to       = NOW(),
    reaction_count = %(reaction_count)s,
    comment_count  = %(comment_count)s,
    share_count    = %(share_count)s
WHERE post_id = %(post_id)s
  AND valid_to IS NULL
"""

# 情况 B：内容变化 —— 插入新版本
SQL_INSERT_HISTORY_NEW = """
INSERT INTO fb_group_posts_history (
    post_id, group_id, publish_time,
    text, content_hash,
    reaction_count, comment_count, share_count,
    valid_from, valid_to, created_at
) VALUES (
    %(post_id)s, %(group_id)s, %(publish_time)s,
    %(text)s, %(content_hash)s,
    %(reaction_count)s, %(comment_count)s, %(share_count)s,
    NOW(), NULL, NOW()
)
"""

# 情况 B：内容变化 —— 把主表更新为最新版本
SQL_UPDATE_MAIN_CONTENT_CHANGED = """
UPDATE fb_group_posts
SET text           = %(text)s,
    content_hash   = %(content_hash)s,
    simhash        = %(simhash)s,
    reaction_count = %(reaction_count)s,
    comment_count  = %(comment_count)s,
    share_count    = %(share_count)s,
    end_date       = NOW(),
    updated_at     = NOW()
WHERE post_id = %(post_id)s
"""

# 情况 C：内容不变 —— 只刷新主表 end_date / updated_at / 动态指标
SQL_TOUCH_MAIN = """
UPDATE fb_group_posts
SET end_date       = NOW(),
    updated_at     = NOW(),
    reaction_count = %(reaction_count)s,
    comment_count  = %(comment_count)s,
    share_count    = %(share_count)s
WHERE post_id = %(post_id)s
"""

# 情况 C：内容不变 —— 同步刷新当前历史版本的动态指标
SQL_TOUCH_CURRENT_HISTORY = """
UPDATE fb_group_posts_history
SET reaction_count = %(reaction_count)s,
    comment_count  = %(comment_count)s,
    share_count    = %(share_count)s
WHERE post_id = %(post_id)s
  AND valid_to IS NULL
"""

# 读取当前主表里的 content_hash
SQL_SELECT_CURRENT_HASH = """
SELECT content_hash FROM fb_group_posts WHERE post_id = %s
"""

# 去重：同作者近期 canonical 貼文候選
SQL_SELECT_AUTHOR_RECENT_POSTS = """
SELECT post_id, content_hash, text, simhash, publish_time
FROM fb_group_posts
WHERE author_id = %(author_id)s
  AND post_id <> %(post_id)s
  AND publish_time >= NOW() - (%(days)s * INTERVAL '1 day')
ORDER BY publish_time DESC NULLS LAST
LIMIT %(limit)s
"""

SQL_SELECT_MAPPING_BY_SOURCE = """
SELECT canonical_post_id, group_id, content_hash
FROM fb_post_group_map
WHERE group_post_id = %s
"""

SQL_UPSERT_MAPPING = """
INSERT INTO fb_post_group_map (
    canonical_post_id, group_id, group_post_id, content_hash, last_seen_at
) VALUES (
    %(canonical_post_id)s, %(group_id)s, %(group_post_id)s, %(content_hash)s, NOW()
)
ON CONFLICT (group_post_id) DO UPDATE SET
    canonical_post_id = EXCLUDED.canonical_post_id,
    group_id = EXCLUDED.group_id,
    content_hash = EXCLUDED.content_hash,
    last_seen_at = NOW()
"""

SQL_SELECT_CANONICAL_FOR_UPDATE = """
SELECT post_id, content_hash
FROM fb_group_posts
WHERE post_id = %s
FOR UPDATE
"""

SQL_SELECT_LATEST_MAPPED_HASH = """
SELECT group_post_id, content_hash, last_seen_at
FROM fb_post_group_map
WHERE canonical_post_id = %s
ORDER BY last_seen_at DESC NULLS LAST, group_post_id DESC
LIMIT 1
"""


def ensure_schema(conn) -> None:
    """確保跨社團去重所需欄位/資料表存在（向後相容既有 schema）。"""
    with conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE fb_group_posts ADD COLUMN IF NOT EXISTS simhash BIGINT")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fb_post_group_map (
                    canonical_post_id TEXT NOT NULL REFERENCES fb_group_posts(post_id) ON DELETE CASCADE,
                    group_id TEXT NOT NULL,
                    group_post_id TEXT NOT NULL UNIQUE,
                    content_hash TEXT,
                    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            # 若舊版 occurrences 表存在，遷移為精簡 mapping 資料
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema='public' AND table_name='fb_group_post_occurrences'
                ) AS exists
                """
            )
            row = cur.fetchone()
            exists = _row_get(row, "exists", 0)
            if exists:
                cur.execute(
                    """
                    INSERT INTO fb_post_group_map (canonical_post_id, group_id, group_post_id, content_hash, last_seen_at)
                    SELECT canonical_post_id, group_id, group_post_id, content_hash, COALESCE(last_seen_at, NOW())
                    FROM fb_group_post_occurrences
                    WHERE group_id IS NOT NULL AND group_post_id IS NOT NULL
                      AND canonical_post_id <> group_post_id
                    ON CONFLICT (group_post_id) DO NOTHING
                    """
                )
                cur.execute("DROP TABLE IF EXISTS fb_group_post_occurrences")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_fb_group_posts_author_publish ON fb_group_posts(author_id, publish_time DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_fb_group_posts_author_hash ON fb_group_posts(author_id, content_hash)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_fb_post_group_map_canonical ON fb_post_group_map(canonical_post_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_fb_post_group_map_group ON fb_post_group_map(group_id)"
            )

            # 資料表與欄位註解（繁體中文）
            cur.execute("COMMENT ON TABLE fb_group_posts IS 'Facebook 社團貼文主表（canonical，保留去重後的貼文與最新狀態）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.id IS '主鍵流水號'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.post_id IS 'Facebook 貼文 ID（canonical 貼文唯一識別）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.group_id IS '來源社團 ID（canonical 貼文首次/主要來源社團）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.group_name IS '來源社團名稱'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.post_url IS '貼文連結'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.author_id IS '作者 ID'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.author_name IS '作者名稱'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.text IS '貼文內容（目前最新版本）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.publish_time IS '貼文發布時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.reaction_count IS '心情/反應數（最近一次抓取）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.comment_count IS '留言數（最近一次抓取）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.share_count IS '分享數（最近一次抓取）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.content_hash IS '正規化本文內容雜湊值（用於判斷內容是否變更/相同）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.start_date IS '首次抓取/收錄時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.end_date IS '最近一次確認仍存在或更新的時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.created_at IS '資料建立時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.updated_at IS '資料更新時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.simhash IS '本文 SimHash 指紋（用於同作者跨社團相似貼文比對）'")

            cur.execute("COMMENT ON TABLE fb_group_posts_history IS 'Facebook 社團貼文內容版本歷史表'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.id IS '主鍵流水號'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.post_id IS '對應 canonical 貼文 ID'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.group_id IS '來源社團 ID'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.publish_time IS '貼文發布時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.text IS '該版本貼文內容'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.content_hash IS '該版本內容雜湊值'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.reaction_count IS '該版本觀測到的心情/反應數'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.comment_count IS '該版本觀測到的留言數'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.share_count IS '該版本觀測到的分享數'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.valid_from IS '此版本生效起始時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.valid_to IS '此版本生效結束時間（NULL 表示目前有效版本）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.created_at IS '版本記錄建立時間'")

            cur.execute("COMMENT ON TABLE fb_post_group_map IS '同內容貼文跨社團對應表（第一個抓到的 canonical 貼文對應其他社團相同貼文來源）'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.canonical_post_id IS 'canonical 貼文 ID（對應 fb_group_posts.post_id）'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.group_id IS '來源社團 ID（重複/對應來源所在社團）'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.group_post_id IS '來源社團中的 Facebook 貼文 ID（唯一）'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.content_hash IS '該來源貼文最近一次觀測到的內容雜湊值（用於判斷來源是否有更新）'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.last_seen_at IS '最近一次看到此來源貼文的時間'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.created_at IS '對應關係建立時間'")


# ---------------------------------------------------------------------------
# 核心逻辑
# ---------------------------------------------------------------------------

def build_params(item: dict) -> dict:
    post_id = pick(item, "id", "postId", "post_id")
    if not post_id:
        raise ValueError(f"帖子缺少 post_id 字段，原始数据键：{list(item.keys())}")

    text = pick(item, "text", "message", "postText", default="") or ""
    normalized = normalize_text(text)
    params = {
        "post_id":        str(post_id),
        "group_id":       pick(item, "groupId", "group_id"),
        "group_name":     pick(item, "groupName", "group_name"),
        "post_url":       pick(item, "url", "postUrl", "link"),
        "author_id":      pick(item, "authorId", "author_id", "userId"),
        "author_name":    pick(item, "authorName", "author_name", "userName"),
        "text":           text,
        "publish_time":   to_ts(pick(item, "publishTime", "publish_time", "time")),
        "reaction_count": to_int(pick(item, "reactionCount", "reactions", "likeCount")),
        "comment_count":  to_int(pick(item, "commentCount", "comments")),
        "share_count":    to_int(pick(item, "shareCount", "shares")),
        "content_hash":   hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "simhash":        compute_simhash(text),
    }
    return params


def find_similar_canonical_post(cur, params: dict) -> str | None:
    """尋找同作者近期內容相同/高度相似的 canonical 貼文（跨社團）。"""
    author_id = params.get("author_id")
    if not author_id or not normalize_text(params.get("text")):
        return None

    cur.execute(
        SQL_SELECT_AUTHOR_RECENT_POSTS,
        {
            "author_id": author_id,
            "post_id": params["post_id"],
            "days": DEDUP_LOOKBACK_DAYS,
            "limit": DEDUP_LOOKBACK_LIMIT,
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
        if hamming_distance64(new_simhash, cand_simhash) <= SIMHASH_MAX_DISTANCE:
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


def _apply_latest_content_to_canonical(cur, canonical_post_id: str, params: dict, old_hash: str | None) -> str:
    if old_hash != params["content_hash"]:
        cur.execute(SQL_CLOSE_CURRENT_HISTORY, params)
        cur.execute(SQL_INSERT_HISTORY_NEW, params)
        cur.execute(SQL_UPDATE_MAIN_CONTENT_CHANGED, params)
        return "updated_content"
    cur.execute(SQL_TOUCH_MAIN, params)
    cur.execute(SQL_TOUCH_CURRENT_HISTORY, params)
    return "touched"


def upsert_post(cur, item: dict) -> str:
    """
    在给定 cursor 上处理一条帖子。
    返回 'inserted' / 'updated_content' / 'touched' / 'deduped'，供日志统计。

    會先參考 fb_post_group_map 決定 canonical；同作者跨社團相似貼文僅保留一筆 canonical，
    其他來源記錄於 mapping。若 mapping 的來源貼文先被編輯，canonical 會更新為最新編輯內容。
    """
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
    canonical_post_id = find_similar_canonical_post(cur, params)
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


def process_items(conn, items: Iterable[dict]) -> dict[str, int]:
    """
    逐条处理一批帖子。每条帖子一个独立事务，单条失败不影响其他条。
    返回各类结果的计数，便于日志汇总。
    """
    ensure_schema(conn)
    stats = {"inserted": 0, "updated_content": 0, "touched": 0, "deduped": 0, "failed": 0}

    for idx, item in enumerate(items, start=1):
        post_id = pick(item, "id", "postId", "post_id", default="<unknown>")
        try:
            with conn:           # with conn 在 psycopg2 中即为事务边界
                with conn.cursor() as cur:
                    result = upsert_post(cur, item)
            stats[result] += 1
            logger.info("第 %d 条 post_id=%s 结果=%s", idx, post_id, result)
        except Exception as exc:
            stats["failed"] += 1
            logger.exception("第 %d 条 post_id=%s 处理失败：%s", idx, post_id, exc)
            # with conn 已自动 rollback 该条的事务

    return stats

