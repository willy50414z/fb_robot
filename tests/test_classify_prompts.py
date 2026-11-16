"""分類 prompt、輸出 Schema 與 DDL 三者必須保持同步。

這組測試不連資料庫，只用靜態檔內容偵測「改了 prompt 忘記改 schema/DDL」這類的漂移。
item 的 10 個鍵就是 benchmark（docs/classification-sample.md）能表達的最小集合，
要增減欄位務必先改 docs/classification-decision-log.md 的決策紀錄。
"""

import json
import re
from importlib import resources

import pytest

PLACEHOLDER = "<<<POST_TEXT>>>"

SYSTEM_PROMPT = (resources.files("fb_robot.classify") / "prompts/v1/system.txt").read_text(encoding="utf-8")
USER_PROMPT = (resources.files("fb_robot.classify") / "prompts/v1/user.txt").read_text(encoding="utf-8")
DDL = (resources.files("fb_robot.classify") / "schema.sql").read_text(encoding="utf-8")
OUTPUT_SCHEMA = json.loads(
    (resources.files("fb_robot.classify") / "prompts/v1/output.schema.json").read_text(encoding="utf-8")
)
ITEM_SCHEMA = OUTPUT_SCHEMA["$defs"]["item"]

TOP_KEYS = {"post_intent", "summary", "items"}
ITEM_KEYS = {
    "category",
    "model",
    "brand",
    "capacity",
    "price_amount",
    "price_kind",
    "purchasable",
    "bundle",
    "snippet",
    "confidence",
}

# schema 鍵名 → DDL 的 CHECK 欄位名
ENUM_COLUMNS = [
    ("price_kind", "price_kind"),
    ("purchasable", "purchasable"),
]

# 允許 null 的欄位：null 必須出現在 enum 裡，否則「缺值給 null」會被驗證打槍
NULLABLE_ENUMS = ["brand", "price_kind"]


def _ddl_enum_values(column: str) -> set[str]:
    """取出 DDL 裡某欄位所有 `column IN (...)` 清單中最長的那一份（完整清單）。"""
    candidates = re.findall(rf"{column} IN \(([^)]*)\)", DDL)
    assert candidates, f"DDL 找不到 {column} 的 CHECK 清單"
    return set(re.findall(r"'([^']*)'", max(candidates, key=len)))


def _taxonomy_categories() -> tuple[set[str], set[str]]:
    rows = re.findall(r"^\s*\('([a-z_]+)',\s*'[^']*',\s*(?:NULL|'[a-z_]+'),\s*(TRUE|FALSE),", DDL, re.MULTILINE)
    keys = {key for key, _ in rows}
    products = {key for key, is_product in rows if is_product == "TRUE"}
    return keys, products


def _brand_keys() -> set[str]:
    block = DDL[DDL.index("INSERT INTO fb_brand (") :]
    block = block[: block.index("ON CONFLICT")]
    return set(re.findall(r"\('([a-z0-9_]+)',", block))


def _enum_of(schema: dict, node: dict) -> set[str]:
    """取得欄位的列舉值（排除代表可為 null 的 None），必要時先解析 $ref。"""
    if "enum" in node:
        values = node["enum"]
    else:
        values = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]["enum"]
    return {value for value in values if value is not None}


def _prompt_examples() -> list[dict]:
    payloads = re.findall(r"^輸出：(\{.*\})$", SYSTEM_PROMPT, re.MULTILINE)
    assert len(payloads) >= 3, "system prompt 的 few-shot 範例遺失"
    return [json.loads(payload) for payload in payloads]


def test_item_keys_are_the_benchmark_core():
    """10 鍵是 benchmark 的核心契約；增減欄位必須是刻意的（連同決策紀錄一起改）。"""
    assert set(ITEM_SCHEMA["properties"]) == ITEM_KEYS
    assert TOP_KEYS == set(OUTPUT_SCHEMA["properties"])


def test_output_schema_requires_every_declared_key():
    assert sorted(OUTPUT_SCHEMA["required"]) == sorted(OUTPUT_SCHEMA["properties"])
    assert sorted(ITEM_SCHEMA["required"]) == sorted(ITEM_SCHEMA["properties"])
    assert OUTPUT_SCHEMA["additionalProperties"] is False
    assert ITEM_SCHEMA["additionalProperties"] is False


def test_few_shot_examples_match_output_schema():
    top_keys = sorted(OUTPUT_SCHEMA["properties"])
    item_keys = sorted(ITEM_SCHEMA["properties"])
    for example in _prompt_examples():
        assert sorted(example) == top_keys
        for item in example.get("items", []):
            assert sorted(item) == item_keys


def test_few_shot_values_satisfy_schema():
    """few-shot 是模型的範本，它自己先違規就會把錯誤學走。"""
    summary_max = OUTPUT_SCHEMA["properties"]["summary"]["maxLength"]
    limits = {key: ITEM_SCHEMA["properties"][key].get("maxLength") for key in ITEM_SCHEMA["properties"]}
    for example in _prompt_examples():
        assert example["summary"] is None or len(example["summary"]) <= summary_max
        for item in example["items"]:
            for key, maximum in limits.items():
                value = item[key]
                if maximum is not None and isinstance(value, str):
                    assert len(value) <= maximum, f"{key} 超過長度上限：{value!r}"
            assert item["category"] in _enum_of(OUTPUT_SCHEMA, ITEM_SCHEMA["properties"]["category"])
            assert item["purchasable"] in _enum_of(OUTPUT_SCHEMA, ITEM_SCHEMA["properties"]["purchasable"])
            assert item["price_kind"] is None or item["price_kind"] in _enum_of(
                OUTPUT_SCHEMA, ITEM_SCHEMA["properties"]["price_kind"]
            )
            assert item["brand"] is None or item["brand"] in _enum_of(
                OUTPUT_SCHEMA, ITEM_SCHEMA["properties"]["brand"]
            )
            assert isinstance(item["bundle"], bool)


@pytest.mark.parametrize("schema_field, ddl_column", ENUM_COLUMNS)
def test_schema_enum_matches_ddl_check(schema_field, ddl_column):
    assert _enum_of(OUTPUT_SCHEMA, ITEM_SCHEMA["properties"][schema_field]) == _ddl_enum_values(ddl_column)


def test_nullable_enums_accept_null():
    for field in NULLABLE_ENUMS:
        assert None in ITEM_SCHEMA["properties"][field]["enum"], f"{field} 的 enum 缺少 null"


def test_post_intent_matches_ddl_intent_check():
    assert _enum_of(OUTPUT_SCHEMA, OUTPUT_SCHEMA["properties"]["post_intent"]) == _ddl_enum_values("intent")


def test_category_enum_matches_taxonomy_products():
    keys, products = _taxonomy_categories()
    allowed = _enum_of(OUTPUT_SCHEMA, ITEM_SCHEMA["properties"]["category"])
    assert allowed == products
    assert keys - products == {"storage", "system"}


def test_brand_enum_matches_ddl_brand_keys():
    """brand 會直接寫進 fb_post_items.brand_key（有 FK），列舉必須與字典完全一致，否則寫入會爆。"""
    assert _enum_of(OUTPUT_SCHEMA, ITEM_SCHEMA["properties"]["brand"]) == _brand_keys()


def test_prompt_brand_codes_exist_in_ddl():
    listed = re.search(r"brand：只能選本欄清單的小寫英文代碼（例：([^）]+)）", SYSTEM_PROMPT)
    assert listed, "system prompt 缺少 brand 代碼範例清單（測試與 prompt 的約定格式）"
    codes = {code.strip() for code in listed.group(1).split("、") if code.strip()}
    assert codes
    unknown = codes - _brand_keys()
    assert not unknown, f"prompt 列了 DDL 不認識的品牌代碼：{sorted(unknown)}"


def test_prompt_declares_purchasable_and_bundle():
    """purchasable 與 bundle 是這一版新增的兩欄，規則文字必須講清楚，否則模型一定亂給。"""
    for keyword in ("已售出", "收購", "保留", "意者私訊"):
        assert keyword in SYSTEM_PROMPT, f"purchasable 規則缺少線索：{keyword}"
    assert " + " in SYSTEM_PROMPT and "bundle" in SYSTEM_PROMPT


def test_few_shot_examples_pass_real_validator():
    """用真正的 Draft 2020-12 validator 驗一次（Schema 本身不合法時 check_schema 就會先爆）。"""
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator
    validator.check_schema(OUTPUT_SCHEMA)
    checker = validator(OUTPUT_SCHEMA)
    for index, example in enumerate(_prompt_examples(), start=1):
        errors = list(checker.iter_errors(example))
        assert not errors, f"第 {index} 個 few-shot 不符合 Schema：{[e.message for e in errors]}"


def test_user_prompt_template_is_placeholder_only():
    assert USER_PROMPT.count(PLACEHOLDER) == 1
    assert "{" not in USER_PROMPT and "}" not in USER_PROMPT
    assert USER_PROMPT.strip().splitlines()[-1] == PLACEHOLDER
