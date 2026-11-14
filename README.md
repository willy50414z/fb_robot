# fb_robot

整合 Facebook 社團貼文爬蟲（Apify）、PostgreSQL 連線與 upsert 寫入流程，將抓取到的貼文存入資料庫。

## 專案結構

- `cralwer.py`：呼叫 Apify actor 抓取指定 FB 社團貼文，回傳貼文列表。
- `db.py`：從 `.env` 讀取資料庫參數並建立 PostgreSQL 連線。
- `fb_group_posts_upsert.py`：將貼文寫入/更新至 `fb_group_posts` 與 `fb_group_posts_history`（依 `content_hash` 判斷新增、內容變更或僅更新指標）。
- `app.py`：串接爬蟲與資料庫流程，依設定的社團清單執行同步。

## 環境需求

- Python 3.10+（建議）
- PostgreSQL（需已建立對應資料表）
- Apify API Key（可呼叫 `getanyapi/facebook-group-posts-scraper`）

安裝依賴：

```bash
pip install -r requirements.txt
```

## 設定

1. 複製 `.env.example` 為 `.env`，並填入實際值：
   - `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SSLMODE`, `DB_CONNECT_TIMEOUT`
   - `APIFY_API_KEY`
2. 確認資料庫中已存在 upsert 流程所需資料表（至少包含 `fb_group_posts`、`fb_group_posts_history`，欄位需符合 `fb_group_posts_upsert.py` 的 SQL）。

## 呼叫方式

### 1. 持續同步（主程式）

輪詢 `app.py` 中 `fb_group_map` 設定的所有社團，完成一輪後等待 120 秒再執行下一輪：

```bash
python app.py
```

### 2. 單次同步單一社團（程式呼叫）

```python
from app import sync_group_posts

stats = sync_group_posts(1923977991158636)  # group_id
print(stats)
# {'inserted': ..., 'updated_content': ..., 'touched': ..., 'failed': ...}
```

### 3. 僅抓取貼文（不寫入資料庫）

```python
from cralwer import get_fb_group_posts

posts = get_fb_group_posts(1923977991158636, max_items=30)
print(len(posts))
```

## 寫入邏輯（現況）

- 以 `post_id` 作為主表（canonical）識別貼文，`fb_group_posts` 保留去重後的貼文與最新狀態；`fb_group_posts_history` 保存內容版本。
- `process_items` 執行時會自動確保 schema/註解存在（`ensure_schema`）：`fb_group_posts.simhash` 欄位、`fb_post_group_map` 對應表、索引與資料表/欄位繁體中文 `COMMENT`。
- 匯入流程會先參考 `fb_post_group_map`（依來源 `group_post_id`）判斷該貼文是否已對應到 canonical：
  - 若來源已在 mapping 中：更新 mapping 的 `content_hash/last_seen_at`；若該來源內容有編輯且與 canonical 不同，則以該來源內容更新 canonical（取最新編輯內容）並寫入歷史版本。
  - 若來源本身為 canonical：正常更新主表/歷史；若其他 mapped 來源已有更新的編輯內容，則避免以較舊來源覆蓋。
  - 若為未知來源：先做同作者跨社團相似比對；命中則寫入 mapping（不新增重複 canonical），若該來源內容較新則更新 canonical；未命中才新增 canonical 主表/歷史。
- 每筆貼文獨立交易，單筆失敗不影響其他貼文。
- `process_items` 回傳統計：`inserted` / `updated_content` / `touched` / `deduped` / `failed`。

## 同作者跨社團相似貼文排除（已實作）

### 比對與篩選流程

1. **正規化本文**：去除首尾空白、壓縮空白、移除零寬字元並轉小寫後比對（`fb_group_posts_upsert.py: normalize_text`）。
2. **作者與範圍**：僅比對同一 `author_id`、最近 `DEDUP_LOOKBACK_DAYS` 天（預設 30）內的 canonical 貼文（最多 `DEDUP_LOOKBACK_LIMIT` 筆，預設 200）。
3. **相似度判斷**：
   - 正規化後 `content_hash` 相同 → 視為重複。
   - 否則比較 64-bit `simhash` 的 Hamming distance，`<= SIMHASH_MAX_DISTANCE`（預設 3）→ 視為高度相似/重複。
4. **對應與更新**：第一個抓到的貼文作為 canonical（例如 A 在 group 111）；其他社團抓到相同/相似貼文（例如 group 222、333）僅建立 mapping，不重複建立 canonical。後續更新會先參考 mapping；若某個 mapped 來源（如 333）先編輯且內容較新，canonical 會更新為最新編輯內容（不因其他來源仍是舊版而回退）。

> 相關參數位於 `fb_group_posts_upsert.py`：`SIMHASH_MAX_DISTANCE`、`DEDUP_LOOKBACK_DAYS`、`DEDUP_LOOKBACK_LIMIT`，可依資料特性調整。

### 資料表設計（1 對多 mapping）

是，**以「canonical 貼文」對「其他社團相同貼文來源」建立 1 對多 mapping**，使用單一對應表 `fb_post_group_map`：

- `fb_group_posts`（canonical 主記錄）：保留去重後主貼文與最新狀態/版本歷史關聯。
- `fb_post_group_map`（mapping 表）：記錄第一個抓到的 canonical 與其他社團相同貼文的對應關係。
  - `canonical_post_id`：對應 `fb_group_posts.post_id`（例如 A）
  - `group_id`：該對應來源所在社團（例如 222、333）
  - `group_post_id`：來源社團中的 FB 貼文 ID（唯一，用於後續更新時查找 mapping）
  - `content_hash`：該來源貼文最近一次觀測內容雜湊（用於判斷來源是否編輯/是否為最新）
  - `last_seen_at` / `created_at`：觀測與建立時間

範例：A 在 group 111 先抓到，group 222、333 抓到相同文章時，可記錄 mapping `(A, 222, <222貼文ID>, ...)`、`(A, 333, <333貼文ID>, ...)`（即 canonical 對多個 group 來源的對應）。

> 說明：`app.py` 會依 `fb_group_map` 逐一同步各社團；若先前僅手動執行單一社團測試，資料庫中只會有該社團資料。執行 `python app.py` 或逐一呼叫 `sync_group_posts` 可匯入/更新其他社團。所有相關 table/columns 皆已加上繁體中文註解（由 `ensure_schema` 套用）。

