"""呼叫 OpenAI 相容端點、解析並驗證 JSON；驗證失敗時帶錯誤訊息重試一次。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from fb_robot.classify.prompts import PromptBundle
from fb_robot.config import LlmSettings


class ClassifyError(RuntimeError):
    """呼叫失敗或輸出無法通過驗證；`raw` 為最後一次模型回覆（可能為空）。"""

    def __init__(self, message: str, raw: str | None = None, latency_ms: int | None = None):
        super().__init__(message)
        self.raw = raw
        self.latency_ms = latency_ms


@dataclass(frozen=True)
class LlmResult:
    payload: dict
    raw: str
    latency_ms: int


def make_client(settings: LlmSettings):
    from openai import OpenAI  # 延後匯入：只跑抓取/寫入時不需要安裝 classify extra

    return OpenAI(base_url=settings.api_base, api_key=settings.api_key, timeout=settings.timeout_seconds)


def extract_json(text: str) -> dict:
    """剝除 ``` 圍欄與前後綴文字後解析 JSON 物件。"""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("回覆中找不到 JSON 物件")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("頂層必須是 JSON 物件")
    return value


def validate_payload(payload: dict, schema: dict) -> None:
    import jsonschema

    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        raise ValueError("; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:5]))


def _response_format(settings: LlmSettings, schema: dict) -> dict | None:
    if settings.response_format == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": "fb_post_classification", "schema": schema}}
    if settings.response_format == "json_object":
        return {"type": "json_object"}
    return None


def _complete(client, settings: LlmSettings, prompt: PromptBundle, messages: list[dict]) -> tuple[str, int]:
    kwargs = {
        "model": settings.model,
        "messages": messages,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
    }
    response_format = _response_format(settings, prompt.schema)
    if response_format:
        kwargs["response_format"] = response_format
    started = time.monotonic()
    response = client.chat.completions.create(**kwargs)
    latency_ms = int((time.monotonic() - started) * 1000)
    return response.choices[0].message.content or "", latency_ms


def classify_text(client, settings: LlmSettings, prompt: PromptBundle, post_text: str) -> LlmResult:
    """分類單篇貼文。第一次輸出無法解析/驗證時，附上錯誤再請模型修正一次。"""
    messages = [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": prompt.render_user(post_text, settings.max_chars)},
    ]
    total_ms = 0
    raw = ""
    for attempt in (1, 2):
        try:
            raw, latency_ms = _complete(client, settings, prompt, messages)
        except Exception as exc:  # 連線、逾時、端點錯誤
            raise ClassifyError(f"api_error: {exc}", raw or None, total_ms or None) from exc
        total_ms += latency_ms
        try:
            payload = extract_json(raw)
            validate_payload(payload, prompt.schema)
            return LlmResult(payload, raw, total_ms)
        except ValueError as exc:  # json.JSONDecodeError 也是 ValueError
            if attempt == 2:
                raise ClassifyError(f"invalid_output: {exc}", raw, total_ms) from exc
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"你上一次的輸出有問題：{exc}\n請修正後只輸出符合規定的 JSON 物件。"},
            ]
    raise AssertionError("unreachable")
