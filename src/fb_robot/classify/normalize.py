"""把模型輸出的字串欄位整理成資料庫欄位（容量解析、型號正規化）。"""

from __future__ import annotations

import re

_UNIT_MAP = {
    "pb": "PB",
    "tb": "TB",
    "gb": "GB",
    "mb": "MB",
    "t": "TB",
    "g": "GB",
    "m": "MB",
    "吋": "inch",
    "inch": "inch",
    '"': "inch",
    "”": "inch",
    "″": "inch",
}
_CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(pb|tb|gb|mb|inch|吋|[tgm]|\"|”|″)(?![a-z])", re.IGNORECASE)
_QUANTITY_RE = re.compile(r"[*xX×]\s*(\d{1,3})\b")


def parse_capacity(text: str | None) -> tuple[float | None, str | None, int | None]:
    """'8G*2' -> (8.0, 'GB', 2)；'27吋' -> (27.0, 'inch', None)；無法解析回 (None, None, None)。"""
    if not text:
        return None, None, None
    match = _CAPACITY_RE.search(text)
    if not match:
        return None, None, None
    value = float(match.group(1))
    if value <= 0:
        return None, None, None
    unit = _UNIT_MAP[match.group(2).lower()]
    quantity = _QUANTITY_RE.search(text[match.end() :])
    return value, unit, int(quantity.group(1)) if quantity else None


def normalize_model(text: str | None) -> str | None:
    """小寫並去除空白與連接符，用於同型號聚合（'RTX 4070 Super' -> 'rtx4070super'）。"""
    if not text:
        return None
    value = re.sub(r"[\s\-_/]+", "", text.lower())
    return value or None
