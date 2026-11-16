"""挑出尚未分類的貼文、呼叫模型、把結果寫進 fb_post_classifications / fb_post_items。

沒有佇列表：「待分類」就是一句反向查詢（同 prompt 版本 + 模型 + 本文雜湊尚無成功結果）。
"""

from __future__ import annotations

import json
import logging
from importlib import resources

from fb_robot.classify.llm import ClassifyError, LlmResult, classify_text, make_client
from fb_robot.classify.normalize import normalize_model, parse_capacity
from fb_robot.classify.prompts import PromptBundle, load_prompt
from fb_robot.config import LlmSettings
from fb_robot.upsert.records import _row_get

logger = logging.getLogger(__name__)

SELECT_PENDING = """
SELECT p.post_id, p.text, p.content_hash
FROM fb_group_posts p
WHERE p.text IS NOT NULL AND btrim(p.text) <> ''
  AND (%(post_id)s::text IS NULL OR p.post_id = %(post_id)s)
  AND NOT EXISTS (
      SELECT 1 FROM fb_post_classifications c
      WHERE c.post_id = p.post_id
        AND c.prompt_version = %(version)s
        AND c.model = %(model)s
        AND c.content_hash IS NOT DISTINCT FROM p.content_hash
        AND c.status <> 'failed'
  )
ORDER BY p.publish_time DESC NULLS LAST
LIMIT %(limit)s
"""

UPSERT_CLASSIFICATION = """
INSERT INTO fb_post_classifications
    (post_id, content_hash, prompt_version, model, intent, categories, item_count,
     review_reasons, summary, error, raw_response, latency_ms, status)
VALUES
    (%(post_id)s, %(content_hash)s, %(prompt_version)s, %(model)s, %(intent)s, %(categories)s, %(item_count)s,
     %(review_reasons)s, %(summary)s, %(error)s, %(raw_response)s, %(latency_ms)s, %(status)s)
ON CONFLICT ON CONSTRAINT fb_post_classifications_unique DO UPDATE SET
    content_hash = EXCLUDED.content_hash, intent = EXCLUDED.intent, categories = EXCLUDED.categories,
    item_count = EXCLUDED.item_count, review_reasons = EXCLUDED.review_reasons, summary = EXCLUDED.summary,
    error = EXCLUDED.error, raw_response = EXCLUDED.raw_response, latency_ms = EXCLUDED.latency_ms,
    status = EXCLUDED.status, created_at = NOW()
RETURNING id
"""

INSERT_ITEM = """
INSERT INTO fb_post_items
    (post_id, ordinal, classification_id, category, brand_key, model_raw, model_norm, purchasable, bundle,
     quantity, capacity_value, capacity_unit, price_amount, price_kind, snippet, confidence)
VALUES
    (%(post_id)s, %(ordinal)s, %(classification_id)s, %(category)s, %(brand_key)s, %(model_raw)s, %(model_norm)s,
     %(purchasable)s, %(bundle)s, %(quantity)s, %(capacity_value)s, %(capacity_unit)s, %(price_amount)s,
     %(price_kind)s, %(snippet)s, %(confidence)s)
"""


def ensure_classify_schema(conn) -> None:
    """套用 classify/schema.sql（全部陳述句冪等；檔案自帶 BEGIN/COMMIT，故用 autocommit）。"""
    ddl = (resources.files("fb_robot.classify") / "schema.sql").read_text(encoding="utf-8")
    previous = conn.autocommit
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(ddl)
    finally:
        conn.autocommit = previous


def build_rows(payload: dict, min_confidence: float) -> tuple[dict, list[dict]]:
    """把模型輸出轉成 (貼文層欄位, 商品列清單)，並判定是否需要人工複核。"""
    reasons: list[str] = []
    items: list[dict] = []
    for ordinal, item in enumerate(payload["items"], start=1):
        value, unit, quantity = parse_capacity(item.get("capacity"))
        items.append(
            {
                "ordinal": ordinal,
                "category": item["category"],
                "brand_key": item.get("brand"),
                "model_raw": item.get("model"),
                "model_norm": normalize_model(item.get("model")),
                "purchasable": item["purchasable"],
                "bundle": item["bundle"],
                "quantity": quantity,
                "capacity_value": value,
                "capacity_unit": unit,
                "price_amount": item.get("price_amount"),
                "price_kind": item.get("price_kind"),
                "snippet": item.get("snippet"),
                "confidence": item.get("confidence"),
            }
        )
    if not items:
        reasons.append("no_items")
    if any(i["category"] == "unknown" for i in items):
        reasons.append("unknown_category")
    if any(i["confidence"] is not None and i["confidence"] < min_confidence for i in items):
        reasons.append("low_confidence")
    post_row = {
        "intent": payload["post_intent"],
        "summary": payload.get("summary"),
        "categories": sorted({i["category"] for i in items}),
        "item_count": len(items),
        "review_reasons": reasons,
        "status": "low_confidence" if "low_confidence" in reasons else "ok",
    }
    return post_row, items


def _save(conn, post: dict, settings: LlmSettings, post_row: dict, items: list[dict], result: LlmResult | None,
          error: str | None = None, raw: str | None = None, latency_ms: int | None = None) -> None:
    params = {
        "post_id": post["post_id"],
        "content_hash": post["content_hash"],
        "prompt_version": settings.prompt_version,
        "model": settings.model,
        "intent": "unknown",
        "categories": [],
        "item_count": 0,
        "review_reasons": [],
        "summary": None,
        "error": error,
        "raw_response": result.raw if result else raw,
        "latency_ms": result.latency_ms if result else latency_ms,
        "status": "failed",
        **post_row,
    }
    with conn:
        with conn.cursor() as cur:
            cur.execute(UPSERT_CLASSIFICATION, params)
            classification_id = _row_get(cur.fetchone(), "id", 0)
            cur.execute("DELETE FROM fb_post_items WHERE classification_id = %s", (classification_id,))
            for item in items:
                cur.execute(INSERT_ITEM, {**item, "post_id": post["post_id"], "classification_id": classification_id})


def classify_pending(
    conn,
    settings: LlmSettings,
    limit: int = 20,
    post_id: str | None = None,
    dry_run: bool = False,
    client=None,
    prompt: PromptBundle | None = None,
) -> dict[str, int]:
    """處理最多 `limit` 篇待分類貼文；dry-run 只印結果、不寫庫。"""
    prompt = prompt or load_prompt(settings.prompt_version)
    client = client or make_client(settings)
    with conn.cursor() as cur:
        cur.execute(
            SELECT_PENDING,
            {"post_id": post_id, "version": settings.prompt_version, "model": settings.model, "limit": limit},
        )
        posts = cur.fetchall()
    stats = {"selected": len(posts), "ok": 0, "review": 0, "failed": 0}
    for post in posts:
        try:
            result = classify_text(client, settings, prompt, post["text"])
            post_row, items = build_rows(result.payload, settings.min_confidence)
        except ClassifyError as exc:
            stats["failed"] += 1
            logger.warning("貼文 %s 分類失敗：%s", post["post_id"], exc)
            if not dry_run:
                _save(conn, post, settings, {}, [], None, error=str(exc), raw=exc.raw, latency_ms=exc.latency_ms)
            continue
        stats["review" if post_row["review_reasons"] else "ok"] += 1
        if dry_run:
            print(json.dumps({"post_id": post["post_id"], **post_row, "items": items}, ensure_ascii=False, default=str))
        else:
            _save(conn, post, settings, post_row, items, result)
    return stats
