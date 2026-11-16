"""文字正規化與 SimHash 指紋計算。

跨社團重複貼文比對依賴這裡的正規化規則與 64-bit SimHash，
內容有任何調整都會影響既有資料的比對結果。
"""

import hashlib
import re
from typing import Any


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
