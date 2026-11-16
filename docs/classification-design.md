# 貼文分類設計（DDL + LLM 端點 + Prompt）

把爬進來的電腦零組件社團貼文，用 LLM 拆成可查詢、可比價的結構化資料。
本篇是**設計定稿**：DDL、prompt、端點契約；管線程式碼列於第 10 節的分期計畫。

## 1. 目標與非目標

**目標**

- 分類品類：cpu / gpu / ssd / hdd 為先務，同時涵蓋主機板、記憶體、電源等 19 類（見 `fb_taxonomy`）。
- v1 輸出只做 6 件事：品類、型號、品牌、容量、價格、可否購買，外加 `bundle` 與 `confidence` 兩個輔助欄（對齊 `docs/classification-sample.md` 的 6 欄人工標註）。
- 新舊狀態、保固、交易方式、地點、開放規格是 v2 預留：DDL 欄位已存在，v1 一律留 NULL，避免模型一次要填 25 個鍵而分散注意力。
- 一篇貼文可拆多項（"TOSHIBA 4TB $2999 / ADATA SU800 256GB $622"），成套出售算一項。
- 收購（want_to_buy）、一元起標（auction）、已售出（sold）要能被分辨，「現在還能買嗎」是一句 WHERE 就能答的問題。
- 可重複執行：同一篇貼文同一版本 prompt 不重複花錢；prompt 升級可批次重跑並保留舊結果比較。

**非目標**

- 不做圖片 OCR（純圖片貼文先標記 `image_only` 略過）。
- 不做站外比價、不做推薦排名。
- 不把 LLM 輸出當事實來源：`snippet` 保留原文證據，低信心進人工複核佇列。

## 2. 資料流

```text
Apify 抓文 ──> fb_group_posts（既有，canonical）
                    │  反向查詢：同 prompt 版本 + 模型 + content_hash 尚無成功結果的貼文
              fb-robot classify（單進程、逐篇呼叫本地 OpenAI 相容端點）
                    │
   fb_post_classifications（一篇一列，含原始回覆/延遲/錯誤）+ fb_post_items（一項目一列）
                    │
         v_fb_listing / v_fb_market_price（查詢端）
```

## 3. 資料模型（`src/fb_robot/classify/schema.sql`）

冪等 DDL：全程 `BEGIN … COMMIT`，物件一律 `IF NOT EXISTS` / `CREATE OR REPLACE` / `ON CONFLICT`，可重複執行；套用方式與 `upsert/schema.py` 的 `ensure_schema` 一致（`fb-robot classify` 啟動時由 `ensure_classify_schema()` 載入執行）。
相容性設定的 PostgreSQL（實測於 PG 16，避免 PG15+ 專屬語法）。

### 3.1 物件總覽

| 物件 | 性質 | 用途 |
| --- | --- | --- |
| `fb_taxonomy` | 字典表 | 分類清單、顯示名、容量語意（`capacity_hint`）、父子層級；`is_product = FALSE` 是分群節點（storage/system），不會成為商品分類 |
| `fb_brand` | 字典表 | 正規化品牌（`brand_key`），50 筆種子 |
| `fb_brand_alias` | 對照表 | 原文/別名 → `brand_key`（111 筆種子，`source` 分 manual / llm / seed） |
| `fb_post_classifications` | 事實表 | 一篇貼文 × 一個 `(prompt_version, model)` 一列；整篇意圖、分類集合、價格區間、信心、複核旗標 |
| `fb_post_items` | 事實表 | 一個商品項目一列；型號/容量/價格/可購買/規格，1 篇 → N 項 |
| `v_fb_listing` | 檢視 | 可購買的商品清單（`purchasable = 'yes'`） |
| `v_fb_market_price` | 檢視 | 分類 × 品牌 × 新舊狀態 × 成套與否的 TWD 價格分佈（P25/中位數/P75） |

### 3.2 關鍵決策

- **一文多項**：`fb_post_items` 以 `UNIQUE (classification_id, ordinal)` 保序；`classification_id` FK `ON DELETE CASCADE`，重跑同一版會先刪同版 items 再插入。`ordinal` 用 `UNIQUE (classification_id, ordinal)` 而非 `NULLS NOT DISTINCT`，維持 PG12+ 相容。
- **版本快取鍵**：`fb_post_classifications UNIQUE (post_id, prompt_version, model)` + `content_hash`。本文沒變且版本沒升 → 不重分類；升版是新增一列，不是覆寫，方便 A/B 比較。
- **「可購買」直接存**：`purchasable`（`yes` / `no` / `unknown`）是模型判定結果，與 benchmark 的 `Y` / `N` / `?` 一一對應。原本的 `availability` + `is_purchasable` generated column 需要「新舊狀態 × 意圖」兩個輸入才有意義，而 v1 不再要求模型標新舊狀態；改成直接存也避免規則變更時要改 generated expression 並 `ALTER TABLE … GENERATED` 重建。
- **`bundle` 是布林，不是分類**：套裝／半套／整機共用一個價格時標 `TRUE`，讓 `v_fb_market_price` 能把它們分組而不是混進單品價格。整機（desktop/laptop）本身也是 `bundle = TRUE`。
- **枚舉用 CHECK，不用 PG enum type**：加值只改 CHECK，不用 `ALTER TYPE`；`category` 額外以 FK 指向 `fb_taxonomy`，讓 prompt 清單、DDL 約束、前端顯示名共用一份來源。
- **容量用十進位**（廠商標示值，不做 1024 換算）：`capacity_bytes` 為 generated column；螢幕以 `capacity_unit = 'inch'` 記吋數，`capacity_bytes` 自動為 NULL。
- **開放規格走 jsonb**：`attributes jsonb` + GIN(`jsonb_path_ops`)，避免每種規格都加欄位；`interface`/`socket`/`watt`/`vram_gb` 等常見鍵由 prompt 約束命名。
- **品牌三層**：`brand_raw`（原文，永不失真）→ `brand_key`（可為 NULL）→ `fb_brand_alias`。v1 的 prompt 把 `brand` 鎖在 50 個 `brand_key` 的 enum 裡（避免逐筆 FK 失敗），字典外的寫法留給 v2：以 `source = 'llm'` 入庫、`brand_key` 先留 NULL，人工確認後改 `manual` 並補上對應。
- **複核旗標**：`needs_review` + `review_reasons text[]`（部分索引），低信心、`items = []`、`category = 'unknown'`、混幣等都進這裡，而不是直接進主表當真相。

### 3.3 Prompt 鍵 → DB 欄位

v1 頂層只有 3 個鍵、item 只有 10 個鍵，因此部分事實表欄位改由管線派生。

| Prompt 輸出 | DB 欄位 | 寫入規則 |
| --- | --- | --- |
| `post_intent` | `fb_post_classifications.intent` | 9 值列舉，與 CHECK 對齊 |
| `summary` | `.summary` | 直存 |
| （衍生） | `.categories` / `.item_count` | 由 items 去重彙總 |
| （衍生） | `.review_reasons` | 寫入條件：items 空、`category = 'unknown'`、`MIN(items.confidence) < FB_LLM_MIN_CONFIDENCE`、價格為負、管線偵測到混幣；`needs_review` 由本欄派生，不可寫入 |
| （衍生） | `.status` | `ok` / `low_confidence` / `not_applicable` / `failed`；items 全空或全為 `unknown` → `not_applicable`（原本 `is_hardware_post` 的語意由它與 `item_count = 0` 覆蓋） |
| `items[]` | `fb_post_items`（逐筆） | `ordinal` 由陣列序決定；模型與 prompt 版本只記在父表，items 不再重複存 |
| `items[].category` | `.category` | FK → `fb_taxonomy`；非法值不寫入該列，改記 `review_reasons` |
| `items[].model` | `.model_raw` → `.model_norm` | 入庫前再做一次 lower + 去非 `[a-z0-9]`，避免模型大小寫不穩 |
| `items[].brand` | `.brand_key` | enum 已限定 50 個 `brand_key`，故必可入庫；`brand_raw` v1 留 NULL，v2 才存原文並搭配 `fb_brand_alias.source = 'llm'` 挖新別名 |
| `items[].capacity` | `.capacity_value` / `.capacity_unit` | 模型給原文字串（例：`2TB`、`16G*2`、`27吋`），由管線解析；解析失敗留 NULL 並記 `review_reasons`，不要求模型做單位換算 |
| `items[].price_amount` / `price_kind` | 同名欄 | 模型不輸出幣種，`price_currency` v1 留 NULL，查詢用 `COALESCE(price_currency, 'TWD')` 兜底；`price_note`、`warranty_raw` 等由 loader 從原文補 |
| `items[].purchasable` | `.purchasable` | 直存 `yes` / `no` / `unknown`（對應 benchmark 的 Y/N/?） |
| `items[].bundle` | `.bundle` | 直存 |
| `items[].snippet` / `confidence` | 同名欄 | 直存，人工複核與 prompt 調整的證據來源 |
| （v2 預留） | `.item_condition` / `.warranty_*` / `.location` / `.delivery_methods` / `.contact` / `.attributes` / `.quantity` / `.negotiable` / `.price_note` | v1 不輸出，留 NULL / 預設值 |

`tests/test_classify_prompts.py` 會靜態比對 prompt 的 few-shot JSON、`output.schema.json` 的列舉、DDL 的 CHECK 清單與字典種子；任何一邊改漏都會測試 fail。
## 4. Prompt（`src/fb_robot/classify/prompts/v1/`）

### 4.1 檔案

| 檔案 | 角色 |
| --- | --- |
| `system.txt` | 標註規則：意圖/分類/可購買/價格語意辭典、R1–R14 判定規則、品牌型號正規化、6 個 few-shot |
| `user.txt` | 使用者訊息範本，只含 `<<<POST_TEXT>>>` 佔位符 |
| `output.schema.json` | JSON Schema（draft 2020-12），給 `response_format` 與本地驗證共用 |

載入方式（實作時照這做，**用 `str.replace` 而非 `str.format`**，貼文與 schema 裡的 `{}` 會讓 format 爆炸）：

```python
user = USER_TEMPLATE.replace("<<<POST_TEXT>>>", post_text[:MAX_CHARS])
```

### 4.2 輸出契約

頂層 3 鍵：`post_intent`、`summary`、`items[]`。
每個 item 固定 10 鍵（缺值給 `null`，不可省略鍵）：
`category`、`model`、`brand`、`capacity`、`price_amount`、`price_kind`、`purchasable`、`bundle`、`snippet`、`confidence`。

這 10 鍵是 `docs/classification-sample.md` 6 欄標註的直接超集：`category` → product_type、`model` → 產品型號、`brand`/`capacity` 拆自型號字串、`price_amount` + `price_kind` → 價格、`purchasable` → purchasable。

四個關鍵辭典（與 DDL 的 CHECK / 字典一一對應）：

- `post_intent`（9）：`sell` `auction` `want_to_buy` `trade` `giveaway` `question` `info` `spam` `unknown`
- `category`（19）：`cpu` `motherboard` `ram` `gpu` `ssd` `hdd` `psu` `cooler` `case` `monitor` `laptop` `desktop` `mini_pc` `console` `network` `peripheral` `cable` `other` `unknown`
- `purchasable`（3）：`yes` `no` `unknown` — 對應 benchmark 的 `Y` / `N` / `?`
- `price_kind`（7）：`asking` `bundle` `starting_bid` `budget` `reference` `sold_price` `unknown`
- `brand`（50 + null）：直接是 `fb_brand.brand_key` 的 enum（見 `tests/test_classify_prompts.py::test_brand_enum_matches_ddl_brand_keys`），因為 `fb_post_items.brand_key` 有 FK

價格拆兩件事：金額（`price_amount`）與**語意**（`price_kind`）。幣種 v1 不要求模型輸出，入庫預設 `TWD`。
`budget`（收購預算）與 `asking`（售價）混在同一欄是這類資料最常見的錯誤來源，價格分佈檢視只取 `asking` / `starting_bid`。
`unknown` 是合法且必要的輸出（規則 R13）：文字不足時要敢於不猜，否則模型會硬掰品類。

Schema 的兩個硬約束：`additionalProperties: false` 且 `required == properties`（少鍵就多驗一次，避免欄位無聲消失）；可為 null 的欄位把 `null` 放進 `enum`（JSON Schema 不會因為 `type: ["string","null"]` 就放行 enum 外的 null）。`capacity`/`snippet`/`model` 是自由字串，OpenAI strict mode 不接受，故 不使用 strict mode。

### 4.3 版本規則

- 目錄名就是版本（`prompts/v1/`），改規則 = 開新版本目錄，不原地改寫（否則歷史結果無法解釋）。
- 同版本內 `system.txt`、`user.txt`、`output.schema.json` 三者必須同步；鍵名變更要升版。
- 升版後直接重跑 `classify`（新版本沒有結果，自然入選）；舊版結果保留在 `fb_post_classifications`（`prompt_version` 不同列），用同一份人工標註集比較準確率再決定是否棄用舊版。

## 5. LLM 端點（本地 OpenAI 相容）

用 `openai` SDK 連本地端點（LM Studio / vLLM / Ollama 皆可），設定見 `.env.example`，解析在 `config.LlmSettings`。

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `FB_LLM_MODEL` | （必填） | 端點上的模型名稱 |
| `FB_LLM_API_BASE` / `FB_LLM_API_KEY` | SDK 預設 / `local` | 端點位址與金鑰 |
| `FB_LLM_RESPONSE_FORMAT` | `json_schema` | `json_schema` / `json_object` / `none`；端點不支援時手動降一層，不做自動偵測 |
| `FB_LLM_TEMPERATURE` | `0` | 標註要可重現 |
| `FB_LLM_MAX_TOKENS` | `1500` | 截斷會造成輸出無法解析 |
| `FB_LLM_TIMEOUT_SECONDS` | `120` | 本地推論較慢，逾時放寬 |
| `FB_LLM_PROMPT_VERSION` | `v1` | 對應 `prompts/<version>/` |
| `FB_LLM_MIN_CONFIDENCE` | `0.6` | 低於此值 → `low_confidence` 待複核 |
| `FB_LLM_MAX_CHARS` | `4000` | 貼文本文截斷上限 |

流程（`classify/llm.py`、`classify/service.py`）：

1. 剝除 ``` 圍欄後解析 JSON，再以 `output.schema.json`（jsonschema）驗證。
2. 解析或驗證失敗：把錯誤訊息附回對話，**重試 1 次**；仍失敗則寫一列 `status = 'failed'`（保留 `error`、`raw_response`），下次執行會再挑到它。
3. 驗證通過後，容量字串由 `normalize.parse_capacity` 解析成數值 + 單位（含 `8G*2` 的 quantity），型號由 `normalize_model` 產生 `model_norm`。
4. `review_reasons`：`no_items`、`unknown_category`、`low_confidence`。
5. 單一交易寫入：`UPSERT classification` → 刪同一筆分類下的舊 items → 逐列 `INSERT`。

刻意不做（驗證後再看需不需要）：成本與用量上限、併發、自動偵測結構化輸出能力、佇列與重試退避、獨立的呼叫稽核表。

## 6. 待分類的判定

沒有佇列表。`classify/service.py::SELECT_PENDING` 挑出「有本文、且該 `(prompt_version, model)` 下沒有同 `content_hash` 的成功結果」的貼文，依發文時間新到舊，取 `--limit` 篇。本文變動或升 prompt 版本會自然重新入選；純圖片（空本文）貼文不處理。

## 7. CLI

```bash
fb-robot classify --dry-run --limit 5     # 呼叫模型並印出結果，不寫 DB（也不套用 DDL）
fb-robot classify --limit 20              # 處理 20 篇待分類貼文
fb-robot classify --post-id <id>          # 只處理指定貼文
```

## 8. 評測方法

1. gold set 已就緒：`docs/classification-sample.md`（117 列／86 篇人工標註，覆蓋單品、多項、成套、競標、收購、已售出、無價、unknown）。
2. 對齊方式與指標門檻見 `docs/classification-decision-log.md` 第四節：category ≥ 0.92、`purchasable` ≥ 0.95、有價樣本價格完全一致率 ≥ 0.90、每篇品項數誤差 ≤ 0.3。
3. 本表 6 欄都能由 v1 輸出直接重現，不需要額外推導：product_type ← `category`、產品型號 ← `model`、價格 ← `price_amount` + `price_kind`（`起標`／`預算` 前綴即 `starting_bid`／`budget`，與本價不可互換）、purchasable ← `purchasable`。
4. 已知的易錯點（評測時單獨列榜）：已售出但原文仍在、收購貼文誤判方向（R4）、編號清單錯位造成價格掛錯品項（R7）。
5. 改 prompt 版本或 schema 欄位後必須用同一批 post_id 回放並附 diff 報表；新誤判案例追加進 sample 與決策檔，benchmark 只增不減。

## 9. 未決問題

- **純圖片貼文**：`text` 空的貼文（現行 108 篇裡有）要 OCR、還是永久 `skipped`？
- **組合價拆分**：`bundle = TRUE` 的總價要不要按市價分攤到各內容（建議不分攤，保留原值，統計時靠 `bundle` 分組）。
- **單價 vs 總價**：「兩顆 3500」是 bundle，但「4 條記憶體 3500 一盒」要記單條還是整盒，需要一條明文化規則。
- **混幣貼文**：同時出現 USDT 與 TWD 時怎麼辦——v1 不記幣種（`fb_post_items.price_currency` 恆 NULL、查詢一律視為 TWD），要分辨就得由管線掃原文偵測混幣並寫 `review_reasons`，或等 v2 讓模型輸出幣種。
- **價格時效**：`v_fb_market_price` 是否要加時間窗（只取 90 天內）；目前排除規則是 `purchasable = 'yes'`（已售出與收購自然被排除）+ `price_kind IN ('asking','starting_bid')`。
- **模型選擇**：便宜小模型 vs. 大模型的成本/準確率取捨，需 gold set 數據才能定；`FB_LLM_MODEL` 已可切換。

## 10. 實作範圍

| 階段 | 內容 | 狀態 |
| --- | --- | --- |
| P1 | `ensure_classify_schema`、prompt 載入、LLM 呼叫 + 驗證 + 重試、寫入、`classify` 指令 | 已完成，待實測 |
| P2 | 以 117 列 benchmark 回放，量準確率（門檻見決策檔第四節）並調 prompt | 待驗證結果決定 |
| P3 | 擴充（`model_norm` 別名、時間窗/離群值、成本與用量控制、佇列、人工複核命令）| 驗證後再評估 |

## 11. 檔案對照

| 路徑 | 內容 |
| --- | --- |
| `src/fb_robot/classify/schema.sql` | 冪等 DDL + 字典種子 + 檢視 + `COMMENT ON` |
| `src/fb_robot/classify/prompts/v1/system.txt` | 標註規則 + few-shot |
| `src/fb_robot/classify/prompts/v1/user.txt` | 使用者訊息範本（`<<<POST_TEXT>>>`） |
| `src/fb_robot/classify/prompts/v1/output.schema.json` | 輸出 JSON Schema |
| `tests/test_classify_prompts.py` | prompt / schema / DDL 一致性守門測試 |
| `docs/classification-sample.md` | 人工標註的 benchmark ground truth（117 列／86 篇） |
| `docs/classification-decision-log.md` | 判讀規則 R1–R14、逐案取捨、計分門檻與決策變更記錄 |
| `.env.example` | `FB_LLM_*` 設定範本 |
| `src/fb_robot/classify/{prompts,llm,normalize,service}.py` | prompt 載入、端點呼叫與驗證、欄位正規化、挑件與寫入 |
| `tests/test_classify_pipeline.py` | 容量/型號正規化、JSON 解析、repair 流程、複核判定 |
