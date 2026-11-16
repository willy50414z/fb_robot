"""把 Apify Actor 回傳的貼文資料整理成資料庫寫入參數。"""

import hashlib
from datetime import datetime, timezone
from typing import Any

from fb_robot.upsert.hashing import compute_simhash, normalize_text


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
