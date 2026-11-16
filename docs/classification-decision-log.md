# 貼文分類判讀決策流程（SOP + benchmark 計分依據）

配套檔案：docs/classification-sample.md（人工標註結果，117 列／86 篇）。
本檔記錄「我怎麼判」，目的有三：

1. 讓 prompt／schema 的修訂有規則可循，而不是每次靠模型自己領悟。
2. 讓人工複核與模型輸出可以逐案對質（同一套規則、同一套代號）。
3. 作為回放測試的計分依據：可接受答案（alternates）已寫在規則裡。

規則編號 R1–R14 被 sample 檔引用；修改規則請同步修改 sample 並在本檔追加「變更記錄」。

## 一、判讀 SOP（模型管線照這七步實作）

1. **正規化**：合併空白與換行、去 Emoji 裝飾線、保留原文語言（繁中/英文混雜不翻譯）。
2. **定意圖**：出售 / 收購 / 競標 / 純資訊或問答（R4）。意圖決定 `purchasable` 的可能範圍。
3. **切品項**：把一篇切成「可獨立成交的品項」，一篇多項 → 多列（R1）；成套單一價格 → 一列（R6）。
4. **定 category**：一個品項一個 `category_key`（R2、R3、R14）。
5. **對價格**：依 R7 優先序取價，注意編號清單的「編號錯位」與「原價/加購價」干擾（R8）。
6. **定 purchasable**：已售出／已劃掉／收購 → `N`；資訊不足 → `?`（R5、R13）。缺價不猜價（R9）。
7. **給信心**：任何一步靠推論而非原文明示者，`confidence` 降到 0.6 以下並進人工複核。
   本表每一列都標注了所屬規則，模型輸出可直接逐列 diff。

## 二、判定規則

- **R1 一篇多項 → 多列**：線索是編號清單、項目符、`----` 分隔線，或「每項各自有價格/保固」。
  同 post_id 允許多列，所以 benchmark 對齊用 post_id + category + 型號，不用 post_id 唯一鍵。
- **R2 一個品項一個 category**：以「買家用一句話怎麼稱呼它」為準。附贈品不另開列
  （DDR5 32GB 贈毀損主機板 → 只列 `ram`，主機板寫進型號括號）。
- **R3 整機 vs 零組件**：能獨立開機的完整主機 → `desktop`/`mini_pc`/`laptop`；
  「板U套」「半套」「三件一組」→ 以最主要元件（通常是 `cpu`）為 category，其餘內容以 ` + ` 寫進型號。
- **R4 出售/收購方向**：句首或主動詞出現「收/徵/求/尋」→ 收購（`purchasable=N`，金額是預算）。
  出售方證據是交易條件詞：「起標/結標/得標者/運費由得標者支付/賣貨便出貨」。
  兩類線索衝突時（例：標題「售○○」內文卻像徵求）以「誰持有物品＋交易條件」為準並降信心。
- **R5 purchasable 只對可成交品項評估**：同篇內「售出/已售出/已賣/已保留/有人要了」或被括號划掉的項目 → `N`，
  其餘項目仍 `Y`（4792044071018666：ADATA Legend 900 標「（售出）」→ N；同篇 Micron T710 → Y）。
- **R6 成套單一價格不拆列**：套裝價填在該列，避免因內含 5 個 category 而虛增 5 筆市場價格。
- **R7 價格優先序**：① 「售價/標價/價格/【價格】/$」後的數字 → ② 「N 元起標」→ `起標 N` →
  ③ 收購「預算 N」→ `預算 N` → ④ 只有原價/建議售價/加購價 → 不作為本價，填 `-`。
  同型不同狀態各有價（GM7000 99% / 95%）、同品不同規格各價（D41 16G×2 / ×4）→ 拆多列。
  編號清單要**照編號對照**，不照出現順序：30521839927404547 把第 5 項誤編成「1.」，價格區卻有 5 個值。
- **R8 口語金額換算**：`13.2萬`→132000、`7w`/`7萬`→70000、千分逗號與 `$` 移除；
  `@20000張` 在配件社團指 20,000 元（不是 2 萬張），屬低信心判讀（3229012383961500）。
- **R9 缺價不猜價**：無任何金額線索 → `-`，`purchasable` 仍可是 `Y`（「意者私訊」）。禁止用市價、保固日期或型號反推價格。
- **R10 型號忠於原文，只做最小正規化**：補品牌正名（華擎→ASRock、撼訊→PowerColor、十銓→TeamGroup、
  芝奇→G.SKILL、酷瑪→Cooler Master 之誤字）；原文未標的欄位寫 `（型號未標）`/`（容量未標）`；
  明顯筆誤照實質修正（`1TBGB`→1TB）；不得以健康度或保固到期日反推型號。
- **R11 未指定型號的收購也出列**：「收一張顯卡 預算 8000 不要 A 卡」→ `gpu`／型號 `-`／`預算 8000`／`N`。
  這一列對後續「需求盤」統計有價值，丟掉就少一份資訊。
- **R12 重複貼文不影響單篇判定**：同文重複（同 simhash）各自照原文標；市場價聚合階段再取最新
  `publish_time` 的價格。現成觀察：NUC12SNKi72 13500/14000、MacBook Air M4 32500/35500、
  RTX 2080 Ti 8500/未標 —— 三對都是同一賣家改價或重複發文。
- **R13 資訊不足**：`category=unknown`；只有在出現明確交易動作（面交/賣貨便/可幫送）時才給 `Y`，
  否則 `?`。「無原盒 功能都正常 桃園面交」→ unknown + Y；「有人想要嗎.....」→ unknown + ?。
  圖文並茂的貼文，文字不足是**正常**現象，不要硬猜品類。
- **R14 taxonomy 缺口（待決）**：記憶卡／USB 隨身碟不是 `ssd`/`hdd`，暫列 `other`；
  電源理線擴充模組採「買家搜尋直覺」列 `psu`（可接受 `cable`）；PCIe 顯卡延長線列 `cable`。
  建議正式 taxonomy 增加 `memory_card`，並定義「週邊零件追隨主機（psu）」的原則。

## 三、逐案取捨（只列有爭議的，其餘是規則的直接套用）

| post_id | 難點 | 判定 | 規則 | 可接受alternates |
| --- | --- | --- | --- | --- |
| 30521839927404547 | 內文編號錯位（第 5 項編成 1.），價格區 5 個值 | 照價格區編號對照，拆 5 列 | R1, R7 | 拒絕：只出 4 列或把 300 元掛錯品項 |
| 4792050367684703 | 一篇 9 張卡、三個「專區」標題 | 每張卡一列（sample 取 4 列代表） | R1, R7 | 拒絕：整篇合成 1 列 |
| 3228986347297437 | 整機與拆賣並存、同一記憶體兩種規格兩價 | 拆賣各列 + 整機 35000 單獨一列 | R1, R6 | 拒絕：把 35000 拆給各元件 |
| 3799243953556508 | 標題「售」但內文像徵求 | 拍賣出售（有起標/結標/貨到收款）→ Y | R4 | `cable`/`other` 可接受 |
| 4792075187682221 | 「$2500（暫售）」語意模糊 | 視為可購買 Y，低信心 | R5 | `?` 可接受，`N` 不接受 |
| 30521943597394180 | 末行「[DDR4+B760M 已售出]」 | 該組 2 列 N，其餘 4 列 Y | R1, R5 | 拒絕：整篇 N |
| 4792044071018666 | 「（售出）」夾在第 1 項後 | ADATA→N、Micron→Y | R5 | 拒絕：兩列都 Y |
| 3229012383961500 | 「@20000張」非標準金額 | 20000，低信心 | R8 | `-` 可接受（視為無法判定） |
| 30522057397382800 | 「2月購買 81000 現售65000」 | 取現售 65000 | R7 | 拒絕：81000 |
| 3799462886867948 | 「原價6988 售4800」 | 取 4800 | R7 | 拒絕：6988 |
| 4792118484344558 | 文末「升級 32GB/2TB 只要 2500」加購價 | 本價 23500，忽略加購 | R7 | 拒絕：2500 或 26000 |
| 30521997014055505 | 寫「預售金額」18000 可小議 | Y／18000 | R5, R9 | `?` 不接受（有明價與交易方式） |
| 3799785883502315 | 主機板是贈品且第二槽故障 | 只列 `ram` 12000 | R2, R7 | 拒絕：另開 motherboard Y |
| 4792190234337383 | 同型號兩條、健康度不同價格不同 | 兩列各價（7500／5000） | R1, R7 | 拒絕：合列或取平均 |
| 4792231960999877／4792151601007913 | 同文重複、價格 32500 vs 35500 | 各照原文標 | R12 | 拒絕：互相覆蓋 |
| 30521450140776859 | 與 4792153461007727 同文但少了價格 | 本篇填 `-`，不跨篇補值 | R9, R12 | 補 8500 只能在聚合階段，不得在單篇階段 |
| 30521535277435012／30521519734103233 | 記憶卡、USB 碟無對應 category | `other` | R14 | `ssd` 不接受（語意不同） |
| 4792029857686754 | 「預算2.2-2.4 萬」且有排除條件（藍寶石除外） | `預算 22000-24000`／N，排除條件未進本表 6 欄 | R4, R11 | 拒絕：折中取 23000 |
| 3229018243960914／3228987717297300 | 幾乎無文字 | unknown + Y／? | R13 | 拒絕：猜成任何品類 |

## 四、當成 benchmark 時怎麼打分

1. **對齊**：以 post_id 取回該篇標準答案集合，模型輸出逐列配對（先比 category，再比型號正規化後的 token 重疊）。
   未配對的標準答案列＝漏抓（recall 分母）；未配對的模型列＝幻覺（precision 分母）。
2. **指標與門檻**（先以本 117 列為基準）：
   - category 準確率 ≥ 0.92（`other` 與本表 R14 標註的Alternates 採 top-2 接受）。
   - `purchasable` 準確率 ≥ 0.95（模型給 `?` 而標準為 Y/N 時以 0.5 計，視為降信心而非錯誤）。
   - 有價樣本（價格非 `-`）的價格完全一致率 ≥ 0.90；`起標`／`預算` 前綴算不同值，不可與本價互換。
   - 品項數誤差（每篇 |模型列數 − 標準列數|）平均 ≤ 0.3；多項文（4792050367684703 等）單獨列榜。
3. **回歸紀律**：改 prompt 版本或 schema 欄位後，必須用同一批 post_id 回放並附 diff 報表；
   新遇到的誤判案例（尤其 R4 方向誤判、R7 價格錯位）追加進 sample 檔，benchmark 只增不減。
4. **禁止**：為了分數在 prompt 內塞本表的 post_id 或原文片段（會變成背答案）。要修規則就改 R 編號那條，並重跑全部。

## 五、對 schema 的直接結論（下一步簡化依據）

- 6 欄就能表達本表 100% 的判定，其中只有 3 欄需要模型「推理」：`product_type`、`價格`、`purchasable`。
- 需要保留但可合併進型號/附加欄的：品牌、容量、健康度、保固、成套內容 —— 不該各佔一個頂層欄位。
- 需要新增的結構只有 2 個：`bundle`（成套與否則第二輪再補）與 `confidence`（<0.6 進人工），其餘 25 鍵 item 可刪。
- `unknown` 必須是合法輸出（R13），否則模型會亂猜品類，這是本批樣本裡最貴的錯誤。

## 六、輸出契約精簡決策（v1 schema 重寫，2026-11-14）

本輪把 `output.schema.json` 從「頂層 6 鍵 + item 25 鍵」砍到「頂層 3 鍵 + item 10 鍵」，
並把 DDL 改成配合這個契約。以下逐項記錄「為什麼」，後續實作管線與跑 benchmark 時以本節為準。

### 6.1 砍欄位的依據

判定標準只有一條：**這一欄有沒有一個已經寫好的用途**（被人讀、被檢視取用、被拿來算價或過濾）。
按第五節的結論執行三分類：

- **保留（進 schema）**：`category`、`model`、`brand`、`capacity`、`price_amount`、`price_kind`、`purchasable`、`bundle`、`snippet`、`confidence`。
  前 8 個直接對應 benchmark 的 6 欄（型號欄同時藏了品牌/容量/規格），`snippet` 是人工複核的唯一證據，`confidence` 是複核佇列的進件條件。
- **改為管線派生（模型不再產）**：`ordinal`（陣列序）、`intent`（頂層 `post_intent` 下填 item）、`is_hardware_post`（有無非 unknown 項目）、`confidence`/`flags`/`categories`/`item_count`/`price_min`/`price_max`/`currency`/`needs_review`/`status`（全部由 items 彙總）。
  這些是「彙總」不是「判斷」，讓模型算只會製造不一致（同一篇的 `price_min` 出現兩個值），並白花 output tokens。
- **降為 v2 預留（DDL 留欄、v1 留 NULL）**：`item_condition`、`warranty_*`、`location`、`delivery_methods`、`contact`、`attributes`、`quantity`、`subcategory`、`negotiable`、`price_note`、`price_currency`。
  理由是 benchmark 裡沒有任何一列需要它們，而 25 鍵表格會讓小模型把注意力花在填空格上（幻覺欄位）。
  欄位留在表上是刻意保留了擴充性：加欄不影響既有輸出，v2 升 prompt 版本即可回填。

### 6.2 五個具體取捨

1. **`purchasable`（yes/no/unknown）取代 `availability` + generated column `is_purchasable`**。
   原設計要從「availability × intent × item_condition」推導，但 v1 已不要求標新舊狀態，推導鏈斷了；
   而且 generated column 一旦改規則就要 `ALTER TABLE … DROP/ADD GENERATED` 並重建，人工標註也無法覆寫。
   直接存三個值，與 benchmark 的 `Y`/`N`/`?` 一一對應，回放時可以逐列 diff，不需要再跑一次推導。
2. **新增 `bundle` 布林**。成套／半套／整機共用一個價格時為 TRUE。
   決策關鍵：整機（desktop/laptop）也是 bundle，所以 `v_fb_market_price` 把它**分組**而不是排除 ——
   排除會丟掉整機市場價，不分組則會把「板U套 13500」當成 cpu 的市場價，兩邊都錯。
3. **`capacity` 收原文字串**（`2TB`、`16G*2`、`27吋`，≤40 字），由管線解析成 `capacity_value`/`capacity_unit`。
   換算與拆包是確定性的事，交給模型會得到 `1.92TB`、`2000GB`、`2048GB` 三種寫法；解析失敗就留 NULL 進複核，不假裝成功。
4. **`brand` 用 50 個 `brand_key` 的 enum，不用 free string**。
   `fb_post_items.brand_key` 有 FK，free string 會讓一批貼文整批寫入失敗；代價是字典外的品牌（例：十銓的少見寫法）會被模型硬套到相近代碼。
   取捨：寧可錯到可預期的代碼 + `snippet` 留原文，也不要寫入中斷；擴充字典走 `fb_brand_alias`，比改 prompt 便宜。
5. **可為 null 的列舉必須把 `null` 放進 `enum`**。
   舊版寫 `type: ["string","null"]` 卻只列非 null 值，schema 實際上是「不準為 null」，與 prompt 的「缺值給 null」互相矛盾 —— 這是上一輪就存在、這一輪才被抓出的 latent bug（已加守門測試 `test_nullable_enums_accept_null`）。

### 6.3 few-shot 與 prompt 的對應調整

- 例子從 5 個增加到 6 個，新增「板U套 + 已賣出」：一次同時教 `bundle=true`、`purchasable=no`、型號用「 + 」串內容（R5、R6）。
- 結尾一例改為 `category="unknown"` + `purchasable="yes"`（R13），與 sample 檔的 unknown 列一致。
  原本結尾範例是 `items: []`，會教出「看不懂就回空陣列」的習慣，正是本批樣本最貴的錯誤（丟 recall）。
- 規則區直接引用本檔 R1–R14 的編號，prompt 與本檔同一套語言；規則改動時兩邊要同步，否則評測結果無法解釋。

### 6.4 DDL 側的修改與驗證

- 修掉上一輪導入的兩個語法錯誤：重複的 `quantity SMALLINT` 行；`COMMENT ON COLUMN … .bundle` 用了兩個相鄰字串字面量（在 PG 是 syntax error，整套 DDL 會直接掛）。
- `v_fb_listing` 增加輸出 `bundle`，`v_fb_market_price` 的 SELECT/GROUP BY 加入 `bundle`（見 6.2 第 2 點）。
- 對本機 PostgreSQL 16 完整跑過 `schema.sql`：每項檢查在獨立交易內執行後 ROLLBACK，確認 19 分類／50 品牌／111 別名入座、`capacity_bytes` 換算正確、`v_fb_listing` 只留 `purchasable='yes'`、非法 category／brand／purchasable／price_kind 會被 FK 與 CHECK 擋下。
  **DDL 仍尚未正式套用**，要等管線實作時由 `ensure_classify_schema()` 執行。

### 6.5 對 benchmark 的影響

- 本檔第四節的門檻不變（category ≥ 0.92、`purchasable` ≥ 0.95、有價一致率 ≥ 0.90、品項數誤差 ≤ 0.3）。
- sample 的 6 欄 100% 可由新契約表達，評測不需要推導欄；`起標`／`預算` 前綴對應 `price_kind`，比對時以「金額 + 語意」雙條件判定，不可與本價互換。
- `brand` 不計分（本表沒有品牌欄），避免拿沒標過的東西當答案。

## 七、DDL 欄位審計（2026-11-14）

對 `schema.sql` 的 7 張表共 110 個欄位逐個查「誰寫入／誰讀取」，分四級：

- **KEEP**：v1 有寫入者且有讀者（或刻意正規劃的快取）。
- **DERIVE**：由 `fb_post_items` 彙總或正規化得來，保留屬「省 JOIN」的取捨。
- **DROP**：v1 沒有寫入者、沒有讀者，或與其他欄重複。
- **V2**：v2 預留，v1 恆 NULL（留不留是產品決定，不是錯誤）。

### 7.1 判定為 DROP 的 17 欄

| 表.欄位 | 誰寫入 | 誰讀取 | 理由 |
| --- | --- | --- | --- |
| `fb_post_items.model` | 管線 | 無 | 與父表 `fb_post_classifications.model` 同值（items 必繼承同一個 classification），純重複 |
| `fb_post_items.prompt_version` | 管線 | 無 | 同上 |
| `fb_post_items.intent` | 管線由 `post_intent` 下填 | 無 | v1 恆等於父表 `intent`；逐項意圖是 v2 的事 |
| `fb_post_items.subcategory` | 無 | `v_fb_listing` 恆 NULL | 無 FK、prompt v1 不輸出，只有 view 選到一個永遠空的值 |
| `fb_post_classifications.is_hardware_post` | 無（prompt 無此輸出） | 無 | 語意已被 `status = 'not_applicable'` 與 `item_count = 0` 覆蓋 |
| `fb_post_classifications.price_min` | 管線彙總 | 無 | 價格統計走 `v_fb_market_price`；還附帶一條 `price_min <= price_max` CHECK 要維護 |
| `fb_post_classifications.price_max` | 管線彙總 | 無 | 同上 |
| `fb_post_classifications.currency` | 管線彙總 | 無 | 明細見 `fb_post_items.price_currency` |
| `fb_post_classifications.confidence` | 無（v1 只有 item 層 confidence） | 無 | 恆 NULL；要整體信心就用 items 的 `MIN`/`AVG` 算 |
| `fb_post_classifications.flags` | 無 | 無 | `multi_item`/`bundle_post`/`text_too_short`/`image_only` 全可從 items 或主表推導 |
| `fb_post_classifications.updated_at` | 無 | 無 | 重新分類採 DELETE + INSERT，恆等 `created_at`（本專案 `updated_at` 由程式寫、無 trigger） |
| `fb_taxonomy.enabled` | 無 | 無 | 與 `is_product` 語意重疊（都是「這類還用不用」）；要停用就改 `is_product` 或刪列 |
| `fb_taxonomy.sort_order` | seed | 無 | 沒有任何 `ORDER BY` 讀者 |
| `fb_taxonomy.created_at` | 預設值 | 無 | 字典表由 seed 版本控制，時間戳無意義 |
| `fb_brand.notes` | 無 | 無 | seed 從不寫入 |
| `fb_brand.updated_at` | 預設值 | 無 | UPSERT 用 `ON CONFLICT DO NOTHING`，此值永遠不會變 |
| `fb_llm_calls.provider` | 管線 | 無 | `model` 已含 provider 前綴（`openai/gpt-4o-mini`），`split_part(model, '/', 1)` 即可 |

### 7.2 該改成 generated 而不是留空值的 1 欄

- `fb_post_classifications.needs_review` 與 `review_reasons <> '{}'` 是同一件事，改寫為
  `needs_review BOOLEAN GENERATED ALWAYS AS (review_reasons <> '{}') STORED`，
  同時把 `idx_fb_post_classifications_review` 的條件從 `WHERE needs_review` 改成 `WHERE review_reasons <> '{}'`。
  好處：寫入端不可能寫出不一致的組合。

### 7.3 V2 預留的 13 欄（v1 恆 NULL，要拍板）

`fb_post_items` 的 `item_condition`、`quantity`、`negotiable`、`price_note`、`warranty_status`、
`warranty_end`、`warranty_raw`、`location`、`delivery_methods`、`contact`、`attributes`、
`price_currency`、`brand_raw`。

砍它們的連動成本（不是零風險）：

- `v_fb_market_price` 的 `COALESCE(price_currency, 'TWD') = 'TWD'` 要改成固定 TWD 註解；`GROUP BY` 要拿掉 `item_condition`——該維度 v1 恆 `unknown`，目前是死的，但 v2 加回保固/新舊時就要改 view。
- `v_fb_listing` 的 SELECT 要同步縮減。
- `brand_raw` 若還要「從貼文挖新品牌別名 → `fb_brand_alias.source = 'llm'` 等人工複核」這條流程，就需要它；不做就砍。

### 7.4 順帶發現的索引與結構冗餘

- `idx_fb_post_items_attributes_gin`：v1 `attributes` 恆 `'{}'`，只有寫入開銷。
- `idx_fb_post_items_price`（`WHERE price_amount IS NOT NULL`）：兩個 view 都以 `category`／`purchasable`／`price_kind` 過濾，單獨走價格的查詢目前不存在。
- `idx_fb_post_items_created`：時間軸查詢實際走 `fb_group_posts.publish_time`。
- `fb_taxonomy.parent_key` 與 `storage`／`system` 兩列非商品節點：沒有任何讀者，卻強迫 prompt 與測試必須排除非商品分類。若要極簡，三者可一起拿掉（代價是失去「儲存/整機」這層顯示分群）。

### 7.5 數量與實作注意事項

- 110 欄（taxonomy 10／brand 5／alias 4／classifications 20／items 36／llm_calls 20／queue 15）→ 照 7.1 砍 17 欄＝93 欄（-15%）；連 7.3 的 13 欄一起清＝80 欄（-27%）。
- `tests/test_classify_prompts.py` 用正規表達式比對 DDL：`_ddl_enum_values()` 抓 `column IN (...)` 最長的一份，砍掉 `fb_post_items.intent` 後仍會命中父表的同名 CHECK，測試不受影響；但 `_taxonomy_categories()` 依賴 seed 的 `('key', 'name', parent, TRUE/FALSE,` 四段格式，動 `parent_key` 必須同步改該測試，否則產品分類集合會抓成空集合。
- 砍欄屬設計變更，需同步改：`schema.sql`（表本體＋索引＋第 194–203 行的欄位對應註解＋`COMMENT ON` 區）、`docs/classification-design.md` §3.1/§3.3、本檔第八節。
## 變更記錄

| 日期 | 變更 | 影響 |
| --- | --- | --- |
| 2026-11-14 | 建立 R1–R14 判讀規則、逐案取捨、benchmark 計分方式 | 配套 sample（117 列／86 篇） |
| 2026-11-14 | 第五節：確認 6 欄足以表達 100% 判定，只有 3 欄需要推理 | 作為 schema 精簡依據 |
| 2026-11-14 | 本節：輸出契約改為頂層 3 鍵 + item 10 鍵；`purchasable` 直存、新增 `bundle`、`capacity` 收原文、`brand` 用 enum、修 nullable enum 驗證漏洞 | `output.schema.json`、`system.txt`、`schema.sql` 檢視、`tests/test_classify_prompts.py`（40 passed） |
| 2026-11-14 | 第七節：逐欄審計 110 欄，裁定 17 欄 DROP、1 欄改 generated、13 欄待拍板 V2、4 筆索引/結構冗餘 | 僅紀錄，DDL 尚未改 |
