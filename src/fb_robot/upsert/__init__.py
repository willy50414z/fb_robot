"""跨社團貼文寫入流程的對外介面。"""

from fb_robot.upsert.hashing import (
    compute_simhash,
    hamming_distance64,
    make_content_hash,
    normalize_text,
)
from fb_robot.upsert.processor import process_items, upsert_post
from fb_robot.upsert.records import build_params
from fb_robot.upsert.schema import ensure_schema

__all__ = [
    "build_params",
    "compute_simhash",
    "ensure_schema",
    "hamming_distance64",
    "make_content_hash",
    "normalize_text",
    "process_items",
    "upsert_post",
]
