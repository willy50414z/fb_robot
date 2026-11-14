# fb_robot

整合 Facebook 社團貼文爬蟲（Apify）、PostgreSQL 連線與 upsert 寫入流程，並將同作者跨社團的重複貼文去重。

## 專案結構

```text
fb_robot/
├── pyproject.toml              # 套件定義、依賴、CLI entry point、pytest/ruff 設定
├── requirements.txt            # 相容舊流程的安裝入口（等同 pip install -e .）
├── .env.example                # 環境變數範本
├── src/fb_robot/
│   ├── __init__.py             # 對外 API（sync_group_posts、create_connection 等）
│   ├── __main__.py             # python -m fb_robot
│   ├── cli.py                  # 命令列介面：run / sync / fetch / groups
│   ├── config.py               # 解析環境變數：資料庫、Apify、去重參數、社團清單
│   ├── crawler.py              # 呼叫 Apify Actor 抓取社團貼文
│   ├── db.py                   # 建立 PostgreSQL 連線
│   ├── service.py              # 串接爬蟲與資料庫的同步流程（原 app.py）
│   └── upsert/
│       ├── hashing.py          # 正規化、content_hash、SimHash、Hamming distance
│       ├── records.py          # Actor 回傳欄位 -> DB 參數（build_params 等）
│       ├── sql.py              # 所有 SQL 陳述句
│       ├── schema.py           # ensure_schema：欄位/索引/註解與舊表遷移
│       └── processor.py        # canonical 判定、版本歷史、mapping 與交易管理
└── tests/                      # pytest 單元測試（不需要真實資料庫）
```

## 環境需求

- Python 3.10+
- PostgreSQL（需已建立對應資料表）
- Apify API Key（可呼叫 `getanyapi/facebook-group-posts-scraper`）

## 安裝

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## 設定

1. 複製 `.env.example` 為 `.env` 並填入實際值。
2. 環境變數一律由 `fb_robot.config` 解析：

| 變數 | 必要 | 說明 |
| --- | --- | --- |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | 是 | PostgreSQL 連線參數 |
| `DB_SSLMODE` / `DB_CONNECT_TIMEOUT` | 否 | 預設 `prefer` / `10` |
| `APIFY_API_KEY` | 是 | Apify API Key |
| `APIFY_ACTOR` / `APIFY_MAX_ITEMS` | 否 | Actor ID / 單次抓取上限（預設 30） |
| `FB_SYNC_INTERVAL_SECONDS` | 否 | `run` 每輪間隔（預設 120） |
| `FB_GROUP_MAP_JSON` | 否 | 社團清單 JSON，省略使用內建 `DEFAULT_GROUPS` |
| `FB_DEDUP_SIMHASH_MAX_DISTANCE` / `FB_DEDUP_LOOKBACK_DAYS` / `FB_DEDUP_LOOKBACK_LIMIT` | 否 | 去重比對參數（預設 3 / 30 / 200） |

3. 確認資料庫中已存在 `fb_group_posts`、`fb_group_posts_history`；`fb_post_group_map` 與多餘的欄位/索引/註解會由 `ensure_schema` 自動建立。

## 使用方式

### 1. CLI

```bash
fb-robot run                     # 依排程持續同步所有社團（等同 python app.py 的舊行為）
fb-robot sync                    # 單次同步全部社團
fb-robot sync --group-id 133186676696605
fb-robot fetch --group-id 133186676696605 --max-items 16 --output posts.json
fb-robot groups                  # 查看目前設定的社團
fb-robot --log-level DEBUG run   # 選項要放在子命令之前
```

沒有安裝 entry point 時可用 `python -m fb_robot run`。

### 2. 單次同步單一社團（程式呼叫）

```python
from fb_robot import sync_group_posts

stats = sync_group_posts(133186676696605)
print(stats)
# {'inserted': ..., 'updated_content': ..., 'touched': ..., 'deduped': ..., 'failed': ...}
```

### 3. 僅抓取貼文（不寫入資料庫）

```python
from fb_robot import get_fb_group_posts

posts = get_fb_group_posts(133186676696605, max_items=30)
print(len(posts))
```

### 4. 自訂設定（例如測試用社團清單）

```python
from fb_robot.config import AppSettings, get_settings
from fb_robot.service import sync_all_groups

settings = get_settings()
sync_all_groups(settings)
```

## 開發

```bash
pytest                 # 單元測試，使用假游標，不需要資料庫連線
ruff check src tests   # 静态检查
```

## 寫入邏輯

- 以 `post_id` 作為主表（canonical）識別貼文，`fb_group_posts` 保留去重後的貼文與最新狀態；`fb_group_posts_history` 保存內容版本。
- `process_items` 執行時會先確保 schema/註解存在（`ensure_schema`）：`fb_group_posts.simhash` 欄位、`fb_post_group_map` 對應表、索引與資料表/欄位繁體中文 `COMMENT`。
- 匯入流程會先參考 `fb_post_group_map`（依來源 `group_post_id`）判斷該貼文是否已對應到 canonical：
  - 若來源已在 mapping 中：更新 mapping 的 `content_hash/last_seen_at`；若該來源內容有編輯且與 canonical 不同，則以該來源內容更新 canonical（取最新編輯內容）並寫入歷史版本。
  - 若來源本身為 canonical：正常更新主表/歷史；若其他 mapped 來源已有更新的編輯內容，則避免以較舊來源覆蓋。
  - 若為未知來源：先做同作者跨社團相似比對；命中則寫入 mapping（不新增重複 canonical），若該來源內容較新則更新 canonical；未命中才新增 canonical 主表/歷史。
- 每筆貼文獨立交易，單筆失敗不影響其他貼文。
- `process_items` 回傳統計：`inserted` / `updated_content` / `touched` / `deduped` / `failed`。

## 同作者跨社團相似貼文排除

### 比對與篩選流程

1. **正規化本文**：去除首尾空白、壓縮空白、移除零寬字元並轉小寫後比對（`src/fb_robot/upsert/hashing.py` 的 `normalize_text`）。
2. **作者與範圍**：僅比對同一 `author_id`、最近 `lookback_days`（預設 30）天內的 canonical 貼文（每位作者最多 `lookback_limit` 筆，預設 200）。
3. **相似度判斷**：
   - 正規化後 `content_hash` 相同 → 視為重複。
   - 否則比較 64-bit `simhash` 的 Hamming distance，`<= simhash_max_distance`（預設 3）→ 視為高度相似/重複。
4. **對應與更新**：第一個抓到的貼文作為 canonical（例如 A 在 group 111）；其他社團抓到相同/相似貼文（例如 group 222、333）僅建立 mapping，不重複建立 canonical。後續更新會先參考 mapping；若某個 mapped 來源（如 333）先編輯且內容較新，canonical 會更新為最新編輯內容（不因其他來源仍是舊版而回退）。

> 三個參數可用環境變數 `FB_DEDUP_*` 調整（見 `fb_robot.config.DedupSettings`），或在呼叫 `process_items` 時傳入自訂 `DedupSettings`。

### 資料表設計（1 對多 mapping）

以「canonical 貼文」對「其他社團相同貼文來源」建立 1 對多 mapping，使用單一對應表 `fb_post_group_map`：

- `fb_group_posts`（canonical 主記錄）：保留去重後主貼文與最新狀態/版本歷史關聯。
- `fb_post_group_map`（mapping 表）：記錄第一個抓到的 canonical 與其他社團相同貼文的對應關係。
  - `canonical_post_id`：對應 `fb_group_posts.post_id`（例如 A）
  - `group_id`：該對應來源所在社團（例如 222、333）
  - `group_post_id`：來源社團中的 FB 貼文 ID（唯一，用於後續更新時查找 mapping）
  - `content_hash`：該來源貼文最近一次觀測內容雜湊（用於判斷來源是否編輯/是否為最新）
  - `last_seen_at` / `created_at`：觀測與建立時間

範例：A 在 group 111 先抓到，group 222、333 抓到相同文章時，可記錄 mapping `(A, 222, <222貼文ID>, ...)`、`(A, 333, <333貼文ID>, ...)`。

> 所有相關 table/columns 皆已加上繁體中文註解（由 `ensure_schema` 套用）。

## 從舊版腳本遷移

| 舊版 | 新版 |
| --- | --- |
| `python app.py` | `fb-robot run` 或 `python -m fb_robot` |
| `from app import sync_group_posts` | `from fb_robot import sync_group_posts` |
| `from cralwer import get_fb_group_posts` | `from fb_robot import get_fb_group_posts`（模組改名為 `crawler`） |
| `from db import create_connection` | `from fb_robot import create_connection` |
| `from fb_group_posts_upsert import process_items` | `from fb_robot import process_items` |
| 模組常數 `SIMHASH_MAX_DISTANCE` 等 | `DedupSettings`（環境變數或函式參數） |
