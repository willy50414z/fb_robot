"""分類管線的純函式測試（不連資料庫、不呼叫模型）。"""

import json
from types import SimpleNamespace

import pytest

from fb_robot.classify.llm import ClassifyError, classify_text, extract_json
from fb_robot.classify.normalize import normalize_model, parse_capacity
from fb_robot.classify.prompts import load_prompt
from fb_robot.classify.service import build_rows
from fb_robot.config import LlmSettings


@pytest.mark.parametrize(
    "text, expected",
    [
        ("240GB", (240.0, "GB", None)),
        ("4TB", (4.0, "TB", None)),
        ("8G*2", (8.0, "GB", 2)),
        ("16GB DDR4", (16.0, "GB", None)),
        ("27吋", (27.0, "inch", None)),
        ("1.5T", (1.5, "TB", None)),
        ("無", (None, None, None)),
        (None, (None, None, None)),
    ],
)
def test_parse_capacity(text, expected):
    assert parse_capacity(text) == expected


def test_normalize_model():
    assert normalize_model("RTX 4070 Super") == "rtx4070super"
    assert normalize_model("i7-12700") == "i712700"
    assert normalize_model(None) is None
    assert normalize_model("  ") is None


def test_extract_json_strips_fence_and_noise():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('好的：{"a": 1} 以上') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("沒有 JSON")


def _item(**over):
    base = {
        "category": "gpu", "model": "RTX 3060", "brand": "nvidia", "capacity": "12GB",
        "price_amount": 7000, "price_kind": "asking", "purchasable": "yes", "bundle": False,
        "snippet": "RTX 3060 12G 7000", "confidence": 0.9,
    }
    return {**base, **over}


def test_build_rows_flags_review():
    payload = {"post_intent": "sell", "summary": None, "items": [_item(), _item(category="unknown", confidence=0.3)]}
    post_row, items = build_rows(payload, 0.6)
    assert post_row["categories"] == ["gpu", "unknown"]
    assert post_row["review_reasons"] == ["unknown_category", "low_confidence"]
    assert post_row["status"] == "low_confidence"
    assert items[0]["capacity_value"] == 12.0 and items[0]["capacity_unit"] == "GB"
    assert items[0]["model_norm"] == "rtx3060"


def test_build_rows_no_items_needs_review():
    post_row, items = build_rows({"post_intent": "question", "summary": None, "items": []}, 0.6)
    assert items == [] and post_row["review_reasons"] == ["no_items"]


class _FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        content = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _settings():
    return LlmSettings(model="fake")


def test_classify_text_repairs_once():
    good = json.dumps({"post_intent": "sell", "summary": None, "items": [_item()]})
    client = _FakeClient(["不是 JSON", good])
    result = classify_text(client, _settings(), load_prompt("v1"), "出售 RTX 3060 12G 7000")
    assert client.calls == 2 and result.payload["items"][0]["model"] == "RTX 3060"


def test_classify_text_gives_up_after_repair():
    client = _FakeClient(["壞", "還是壞"])
    with pytest.raises(ClassifyError) as info:
        classify_text(client, _settings(), load_prompt("v1"), "x")
    assert client.calls == 2 and info.value.raw == "還是壞"
