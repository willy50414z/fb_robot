-- =============================================================================
-- fb_robot 貼文分類 / 資料擷取 schema（v1）
--
-- 目的：把電腦零組件交易貼文經 LLM 拆解成「可查詢的商品清單」
--       （分類 / 品牌 / 型號 / 容量 / 價格 / 是否可購買 ...）。
--
-- 套用方式（擇一，全部陳述句皆冪等，可重複執行）：
--   psql "postgresql://USER@HOST:5432/DB" -v ON_ERROR_STOP=1 -f src/fb_robot/classify/schema.sql
--   或（後續實作）由 fb_robot.classify.ensure_classify_schema() 讀取本檔執行
--
-- 需求：PostgreSQL 12+（IDENTITY / GENERATED STORED / jsonb）；本專案實測 16。
-- 慣例：枚舉以 CHECK 限制而非 enum type，方便後續加值；
--       新增枚舉值時 DROP CONSTRAINT + ADD CONSTRAINT（先 NOT VALID 再 VALIDATE 可免全表掃描）。
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1) 分類字典：prompt 清單、CHECK 依據、下游顯示名共用同一份
--    is_product = FALSE 為分群節點（只出現在 parent_key），不會成為 fb_post_items.category
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fb_taxonomy (
    category_key   TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    parent_key     TEXT REFERENCES fb_taxonomy(category_key) ON DELETE SET NULL,
    is_product     BOOLEAN NOT NULL DEFAULT TRUE,
    capacity_hint  TEXT NOT NULL DEFAULT 'none'
                   CHECK (capacity_hint IN ('none', 'capacity', 'memory', 'screen_inch')),
    notes          TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (category_key <> COALESCE(parent_key, ''))
);

INSERT INTO fb_taxonomy (category_key, display_name, parent_key, is_product, capacity_hint, notes) VALUES
    ('storage',     '儲存裝置（分群）',  NULL,      FALSE, 'none',        '分群節點，不作為商品分類'),
    ('system',      '整機系統（分群）',  NULL,      FALSE, 'none',        '分群節點，不作為商品分類'),
    ('cpu',         '處理器',            NULL,      TRUE,  'none',        '桌上型/筆電 CPU，含板U一體的 CPU 說明'),
    ('motherboard', '主機板',            NULL,      TRUE,  'none',        '板載 CPU/記憶體時記入 attributes'),
    ('ram',         '記憶體',            NULL,      TRUE,  'memory',      'DDR4/DDR5/SODIMM，capacity 記單條容量'),
    ('gpu',         '顯示卡',            NULL,      TRUE,  'capacity',    'capacity 記顯示記憶體的容量'),
    ('ssd',         '固態硬碟',          'storage', TRUE,  'capacity',    'M.2/NVMe/SATA SSD，含 Optane'),
    ('hdd',         '傳統硬碟',          'storage', TRUE,  'capacity',    '3.5/2.5 吋 HDD、外接硬碟盒內盤'),
    ('psu',         '電源供應器',        NULL,      TRUE,  'none',        '瓦數放 attributes.watt'),
    ('cooler',      '散熱器',            NULL,      TRUE,  'none',        '空冷/水冷'),
    ('case',        '機殼',              NULL,      TRUE,  'none',        '含機殼風扇'),
    ('monitor',     '螢幕',              NULL,      TRUE,  'screen_inch', '尺寸（吋）放 capacity'),
    ('laptop',      '筆記型電腦',        'system',  TRUE,  'none',        '整台筆電，規格放 attributes'),
    ('desktop',     '桌機/整機',         'system',  TRUE,  'none',        '自組或品牌整台電腦'),
    ('mini_pc',     '迷你電腦',          'system',  TRUE,  'none',        'NUC、小方 PC'),
    ('console',     '遊戲主機',          'system',  TRUE,  'none',        'PS / Switch / Xbox'),
    ('network',     '網路設備',          NULL,      TRUE,  'none',        '分享器/數據機/網卡/Switch'),
    ('peripheral',  '週邊設備',          NULL,      TRUE,  'none',        '鍵盤/滑鼠/耳機/喇叭/攝影機'),
    ('cable',       '線材/配件',         NULL,      TRUE,  'none',        '延長線/轉接座/擋板/扣具'),
    ('other',       '其他硬體',          NULL,      TRUE,  'none',        '確定是電腦硬體但無法歸入以上分類'),
    ('unknown',     '資訊不足',          NULL,      TRUE,  'none',        '貼文文字過少，無法判斷品項')
ON CONFLICT (category_key) DO UPDATE SET
    display_name  = EXCLUDED.display_name,
    parent_key    = EXCLUDED.parent_key,
    is_product    = EXCLUDED.is_product,
    capacity_hint = EXCLUDED.capacity_hint,
    notes         = EXCLUDED.notes,
    updated_at    = NOW();

-- -----------------------------------------------------------------------------
-- 2) 品牌字典：brand_key 為正規化識別碼，alias 表負責「原文 -> 正規化」對照
--    LLM 提出的新品牌以 source = 'llm' 入庫等待人工複核，人工確認後改為 'manual'
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fb_brand (
    brand_key    TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fb_brand_alias (
    alias        TEXT PRIMARY KEY,
    brand_key    TEXT NOT NULL REFERENCES fb_brand(brand_key) ON DELETE CASCADE,
    source       TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'llm', 'seed')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fb_brand_alias_brand ON fb_brand_alias(brand_key);

INSERT INTO fb_brand (brand_key, display_name) VALUES
    ('intel', 'Intel'), ('amd', 'AMD'), ('nvidia', 'NVIDIA'),
    ('asus', 'ASUS'), ('gigabyte', 'GIGABYTE'), ('msi', 'MSI'), ('asrock', 'ASRock'),
    ('evga', 'EVGA'), ('zotac', 'ZOTAC'), ('pny', 'PNY'), ('galax', 'GALAX'),
    ('colorful', 'Colorful'), ('sapphire', 'Sapphire'), ('xfx', 'XFX'), ('powercolor', 'PowerColor'),
    ('samsung', 'Samsung'), ('skhynix', 'SK hynix'), ('micron', 'Micron'), ('crucial', 'Crucial'),
    ('kioxia', 'KIOXIA'), ('toshiba', 'TOSHIBA'), ('westerndigital', 'Western Digital'),
    ('seagate', 'Seagate'), ('kingston', 'Kingston'), ('adata', 'ADATA'), ('teamgroup', 'TeamGroup'),
    ('gskill', 'G.SKILL'), ('lexar', 'Lexar'), ('sandisk', 'SanDisk'), ('corsair', 'Corsair'),
    ('thermaltake', 'Thermaltake'), ('noctua', 'Noctua'), ('deepcool', 'DeepCool'),
    ('silverstone', 'SilverStone'), ('lianli', 'Lian Li'), ('cougar', 'Cougar'),
    ('zooten', 'Zooten'), ('hipro', 'HIPRO'), ('seasonic', 'Seasonic'), ('fsp', 'FSP'),
    ('enermax', 'Enermax'), ('bequiet', 'be quiet!'), ('coolermaster', 'Cooler Master'),
    ('logitech', 'Logitech'), ('razer', 'Razer'), ('apple', 'Apple'), ('lenovo', 'Lenovo'),
    ('dell', 'Dell'), ('hp', 'HP'), ('acer', 'Acer')
ON CONFLICT (brand_key) DO NOTHING;

INSERT INTO fb_brand_alias (alias, brand_key, source) VALUES
    ('intel', 'intel', 'seed'), ('英特爾', 'intel', 'seed'), ('nuc', 'intel', 'seed'),
    ('amd', 'amd', 'seed'), ('超微', 'amd', 'seed'), ('ryzen', 'amd', 'seed'),
    ('nvidia', 'nvidia', 'seed'), ('輝達', 'nvidia', 'seed'), ('fe', 'nvidia', 'seed'), ('公版', 'nvidia', 'seed'),
    ('asus', 'asus', 'seed'), ('華碩', 'asus', 'seed'), ('rog', 'asus', 'seed'), ('天問', 'asus', 'seed'), ('proart', 'asus', 'seed'),
    ('gigabyte', 'gigabyte', 'seed'), ('技嘉', 'gigabyte', 'seed'), ('aorus', 'gigabyte', 'seed'),
    ('msi', 'msi', 'seed'), ('微星', 'msi', 'seed'), ('ventus', 'msi', 'seed'), ('spatium', 'msi', 'seed'),
    ('asrock', 'asrock', 'seed'), ('華擎', 'asrock', 'seed'),
    ('evga', 'evga', 'seed'), ('zotac', 'zotac', 'seed'), ('pny', 'pny', 'seed'),
    ('galax', 'galax', 'seed'), ('影馳', 'galax', 'seed'),
    ('colorful', 'colorful', 'seed'), ('七彩虹', 'colorful', 'seed'),
    ('sapphire', 'sapphire', 'seed'), ('藍寶', 'sapphire', 'seed'), ('xfx', 'xfx', 'seed'), ('powercolor', 'powercolor', 'seed'),
    ('samsung', 'samsung', 'seed'), ('三星', 'samsung', 'seed'),
    ('sk hynix', 'skhynix', 'seed'), ('skhynix', 'skhynix', 'seed'), ('海力士', 'skhynix', 'seed'),
    ('micron', 'micron', 'seed'), ('美光', 'micron', 'seed'), ('crucial', 'crucial', 'seed'), ('英睿達', 'crucial', 'seed'),
    ('kioxia', 'kioxia', 'seed'), ('toshiba', 'toshiba', 'seed'), ('東芝', 'toshiba', 'seed'),
    ('wd', 'westerndigital', 'seed'), ('western digital', 'westerndigital', 'seed'), ('威騰', 'westerndigital', 'seed'),
    ('seagate', 'seagate', 'seed'), ('希捷', 'seagate', 'seed'), ('baracuda', 'seagate', 'seed'),
    ('kingston', 'kingston', 'seed'), ('金士頓', 'kingston', 'seed'), ('fury', 'kingston', 'seed'), ('hyperx', 'kingston', 'seed'),
    ('adata', 'adata', 'seed'), ('威剛', 'adata', 'seed'),
    ('teamgroup', 'teamgroup', 'seed'), ('十銓', 'teamgroup', 'seed'),
    ('gskill', 'gskill', 'seed'), ('芝奇', 'gskill', 'seed'),
    ('lexar', 'lexar', 'seed'), ('雷克沙', 'lexar', 'seed'),
    ('sandisk', 'sandisk', 'seed'), ('閃迪', 'sandisk', 'seed'),
    ('corsair', 'corsair', 'seed'), ('海盜船', 'corsair', 'seed'),
    ('thermaltake', 'thermaltake', 'seed'), ('曜越', 'thermaltake', 'seed'),
    ('noctua', 'noctua', 'seed'), ('貓頭鷹', 'noctua', 'seed'),
    ('deepcool', 'deepcool', 'seed'), ('九州風神', 'deepcool', 'seed'),
    ('silverstone', 'silverstone', 'seed'), ('lianli', 'lianli', 'seed'), ('聯力', 'lianli', 'seed'),
    ('cougar', 'cougar', 'seed'), ('美洲豹', 'cougar', 'seed'),
    ('zooten', 'zooten', 'seed'), ('祖騰', 'zooten', 'seed'),
    ('hipro', 'hipro', 'seed'), ('振華', 'hipro', 'seed'),
    ('seasonic', 'seasonic', 'seed'), ('海韻', 'seasonic', 'seed'),
    ('fsp', 'fsp', 'seed'), ('全漢', 'fsp', 'seed'),
    ('enermax', 'enermax', 'seed'), ('be quiet', 'bequiet', 'seed'),
    ('cooler master', 'coolermaster', 'seed'), ('酷冷至尊', 'coolermaster', 'seed'),
    ('logitech', 'logitech', 'seed'), ('羅技', 'logitech', 'seed'),
    ('razer', 'razer', 'seed'), ('雷蛇', 'razer', 'seed'),
    ('apple', 'apple', 'seed'), ('蘋果', 'apple', 'seed'), ('macbook', 'apple', 'seed'),
    ('lenovo', 'lenovo', 'seed'), ('聯想', 'lenovo', 'seed'), ('legion', 'lenovo', 'seed'),
    ('dell', 'dell', 'seed'), ('戴爾', 'dell', 'seed'), ('alienware', 'dell', 'seed'),
    ('hp', 'hp', 'seed'), ('惠普', 'hp', 'seed'), ('omen', 'hp', 'seed'),
    ('acer', 'acer', 'seed'), ('宏碁', 'acer', 'seed'), ('predator', 'acer', 'seed')
ON CONFLICT (alias) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3) 貼文層級分類結果：一篇貼文 x 一個 (prompt_version, model) = 一列
--    content_hash 記錄分類當下的本文版本，content_hash 變動才需要重新分類
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fb_post_classifications (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id            TEXT NOT NULL REFERENCES fb_group_posts(post_id) ON DELETE CASCADE,
    content_hash       TEXT NOT NULL,
    prompt_version     TEXT NOT NULL,
    model              TEXT NOT NULL,
    intent             TEXT NOT NULL DEFAULT 'unknown'
                       CHECK (intent IN ('sell', 'auction', 'want_to_buy', 'trade', 'giveaway',
                                         'question', 'info', 'spam', 'unknown')),
    categories         TEXT[] NOT NULL DEFAULT '{}',
    item_count         SMALLINT NOT NULL DEFAULT 0,
    review_reasons     TEXT[] NOT NULL DEFAULT '{}',
    needs_review       BOOLEAN GENERATED ALWAYS AS (review_reasons <> '{}') STORED,
    summary            TEXT,
    error              TEXT,
    raw_response       TEXT,
    latency_ms         INTEGER,
    status             TEXT NOT NULL DEFAULT 'ok'
                       CHECK (status IN ('ok', 'low_confidence', 'not_applicable', 'failed')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fb_post_classifications_unique UNIQUE (post_id, prompt_version, model),
    CONSTRAINT fb_post_classifications_item_count_chk CHECK (item_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_fb_post_classifications_intent
    ON fb_post_classifications(intent, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fb_post_classifications_categories
    ON fb_post_classifications USING GIN (categories);
CREATE INDEX IF NOT EXISTS idx_fb_post_classifications_review
    ON fb_post_classifications(created_at DESC) WHERE needs_review;

-- -----------------------------------------------------------------------------
-- 4) 商品層級擷取結果：一篇貼文可拆出多項（一文多項、一元起標、整台電腦都在資料裡）
--    「能不能買」直接存 purchasable（yes/no/unknown），與人工標註 benchmark 同一套設計。
--    prompt v1 輸出 10 個欄位（規格：prompts/v1/output.schema.json），對應關係：
--      category → category；model → model_raw（model_norm 由管線正規化）；
--      brand → brand_key（列舉已限定為 fb_brand.brand_key）；
--      capacity → capacity_value/capacity_unit（由管線解析字串，不交給模型做換算）；
--      price_amount/price_kind/purchasable/bundle/snippet/confidence → 同名欄位；
--      post_intent → fb_post_classifications.intent（意圖只有整篇一層，逐項方向是 v2）。
--    item_condition、quantity、price_currency、negotiable、warranty_*、
--    location、delivery_methods、contact、attributes 是 v2 預留欄位，v1 一律留 NULL。
--    未標幣種的價格一律視為 TWD（查詢用 COALESCE 兜底）。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fb_post_items (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id           TEXT NOT NULL REFERENCES fb_group_posts(post_id) ON DELETE CASCADE,
    ordinal           SMALLINT NOT NULL DEFAULT 1,
    classification_id BIGINT NOT NULL REFERENCES fb_post_classifications(id) ON DELETE CASCADE,

    category          TEXT NOT NULL REFERENCES fb_taxonomy(category_key),

    brand_raw         TEXT,
    brand_key         TEXT REFERENCES fb_brand(brand_key),
    model_raw         TEXT,
    model_norm        TEXT,

    item_condition    TEXT NOT NULL DEFAULT 'unknown'
                      CHECK (item_condition IN ('new_sealed', 'new_opened', 'used', 'refurbished',
                                                'for_parts', 'not_working', 'unknown')),
    purchasable       TEXT NOT NULL DEFAULT 'unknown'
                      CHECK (purchasable IN ('yes', 'no', 'unknown')),
    bundle            BOOLEAN NOT NULL DEFAULT FALSE,
    quantity          SMALLINT,

    capacity_value    NUMERIC(12,3),
    capacity_unit     TEXT CHECK (capacity_unit IN ('B', 'MB', 'GB', 'TB', 'PB', 'inch')),
    capacity_bytes    BIGINT GENERATED ALWAYS AS (
                          CASE
                              WHEN capacity_value IS NULL OR capacity_unit IS NULL
                                   OR capacity_unit = 'inch' THEN NULL
                              ELSE (capacity_value * CASE capacity_unit
                                       WHEN 'PB' THEN 1000000000000000
                                       WHEN 'TB' THEN 1000000000000
                                       WHEN 'GB' THEN 1000000000
                                       WHEN 'MB' THEN 1000000
                                       ELSE 1
                                   END)::BIGINT
                          END
                      ) STORED,

    price_amount      NUMERIC(12,2),
    price_currency    TEXT CHECK (price_currency IN ('TWD', 'USD', 'JPY', 'CNY', 'EUR', 'HKD')),
    price_kind        TEXT CHECK (price_kind IN ('asking', 'bundle', 'starting_bid', 'budget',
                                                 'reference', 'sold_price', 'unknown')),
    negotiable        BOOLEAN,
    price_note        TEXT,

    warranty_status   TEXT CHECK (warranty_status IN ('in_warranty', 'expired', 'none', 'unknown')),
    warranty_end      DATE,
    warranty_raw      TEXT,

    location          TEXT,
    delivery_methods  TEXT[] CHECK (delivery_methods IS NULL
                                    OR delivery_methods <@ ARRAY['面交', '宅配', '超商', '賣貨便',
                                                                 '店到店', '貨到付款', '線上', '其他']::text[]),
    contact           TEXT,

    attributes        JSONB NOT NULL DEFAULT '{}'::jsonb,
    snippet           TEXT,
    confidence        NUMERIC(4,3),

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fb_post_items_position_unique UNIQUE (classification_id, ordinal),
    CONSTRAINT fb_post_items_quantity_chk CHECK (quantity IS NULL OR (quantity BETWEEN 1 AND 999)),
    CONSTRAINT fb_post_items_capacity_chk CHECK (capacity_value IS NULL OR (capacity_value > 0 AND capacity_value <= 10000000)),
    CONSTRAINT fb_post_items_price_chk CHECK (price_amount IS NULL OR (price_amount >= 0 AND price_amount <= 10000000)),
    CONSTRAINT fb_post_items_confidence_chk CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);

CREATE INDEX IF NOT EXISTS idx_fb_post_items_post ON fb_post_items(post_id);
CREATE INDEX IF NOT EXISTS idx_fb_post_items_category
    ON fb_post_items(category, purchasable);
CREATE INDEX IF NOT EXISTS idx_fb_post_items_brand
    ON fb_post_items(brand_key, category);
CREATE INDEX IF NOT EXISTS idx_fb_post_items_model_norm
    ON fb_post_items(model_norm);
CREATE INDEX IF NOT EXISTS idx_fb_post_items_price
    ON fb_post_items(price_amount) WHERE price_amount IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fb_post_items_capacity
    ON fb_post_items(category, capacity_bytes) WHERE capacity_bytes IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fb_post_items_attributes_gin
    ON fb_post_items USING GIN (attributes jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_fb_post_items_created
    ON fb_post_items(created_at DESC);

-- -----------------------------------------------------------------------------
-- 5) 查詢端檢視：現售清單與價格分佈
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_fb_listing AS
SELECT p.post_id,
       p.group_id,
       p.group_name,
       p.publish_time,
       p.post_url,
       p.author_name,
       i.ordinal,
       i.category,
       t.display_name        AS category_name,
       i.brand_key,
       i.brand_raw,
       i.model_raw,
       i.model_norm,
       i.capacity_value,
       i.capacity_unit,
       i.capacity_bytes,
       i.item_condition,
       i.purchasable,
       i.bundle,
       i.price_amount,
       i.price_currency,
       i.price_kind,
       i.negotiable,
       i.warranty_status,
       i.location,
       i.delivery_methods,
       i.attributes,
       i.confidence,
       i.snippet
FROM fb_post_items i
JOIN fb_group_posts p ON p.post_id = i.post_id
LEFT JOIN fb_taxonomy t ON t.category_key = i.category
WHERE i.purchasable = 'yes';

CREATE OR REPLACE VIEW v_fb_market_price AS
SELECT i.category,
       i.brand_key,
       i.item_condition,
       i.bundle,
       COUNT(*)                                                    AS listings,
       MIN(i.price_amount)                                         AS price_min,
       PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY i.price_amount) AS price_p25,
       PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY i.price_amount) AS price_median,
       PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY i.price_amount) AS price_p75,
       MAX(i.price_amount)                                         AS price_max
FROM fb_post_items i
WHERE COALESCE(i.price_currency, 'TWD') = 'TWD'
  AND i.price_amount IS NOT NULL
  AND i.price_kind IN ('asking', 'starting_bid')
  AND i.purchasable = 'yes'
GROUP BY i.category, i.brand_key, i.item_condition, i.bundle;

-- -----------------------------------------------------------------------------
-- 6) 資料表與欄位註解（繁體中文，維持與既有 schema.py 一致的風格）
-- -----------------------------------------------------------------------------
COMMENT ON TABLE fb_taxonomy IS '貼文分類字典（prompt 列舉、CHECK 依據、顯示名稱的單一來源）';
COMMENT ON COLUMN fb_taxonomy.category_key IS '分類代碼（小寫英文，LLM 輸出與程式比對用）';
COMMENT ON COLUMN fb_taxonomy.display_name IS '分類中文名';
COMMENT ON COLUMN fb_taxonomy.parent_key IS '上層分群（storage/system），NULL 表示無上層';
COMMENT ON COLUMN fb_taxonomy.is_product IS 'FALSE 代表僅供分群，不會作為商品分類寫入 fb_post_items';
COMMENT ON COLUMN fb_taxonomy.capacity_hint IS 'capacity 欄位語意：none 不適用 / capacity 容量 / memory 記憶體容量 / screen_inch 螢幕吋數';

COMMENT ON TABLE fb_brand IS '品牌正規化字典';
COMMENT ON COLUMN fb_brand.brand_key IS '正規化品牌代碼（小寫，例：asus、westerndigital）';
COMMENT ON COLUMN fb_brand.display_name IS '品牌顯示名稱';

COMMENT ON TABLE fb_brand_alias IS '品牌別名對照表（原文/中文/子品牌 -> brand_key）';
COMMENT ON COLUMN fb_brand_alias.alias IS '別名（寫入前已正規化：小寫、去多餘空白）';
COMMENT ON COLUMN fb_brand_alias.source IS '來源：manual 人工確認 / llm 模型建議待複核 / seed 內建字典';

COMMENT ON TABLE fb_post_classifications IS '貼文層級 LLM 分類結果（每個 prompt 版本 + 模型一列，保留可比對的歷史）';
COMMENT ON COLUMN fb_post_classifications.post_id IS '對應 fb_group_posts.post_id（canonical 貼文）';
COMMENT ON COLUMN fb_post_classifications.content_hash IS '分類當下本文的 content_hash，本文變動即需重新分類';
COMMENT ON COLUMN fb_post_classifications.prompt_version IS 'prompt 版本（例：v1），改版後可並存比較';
COMMENT ON COLUMN fb_post_classifications.model IS '使用的模型識別碼（含 provider 前綴，例：openai/gpt-4o-mini）';
COMMENT ON COLUMN fb_post_classifications.intent IS '整篇意圖：sell 出售 / auction 競標 / want_to_buy 收購 / trade 交換 / giveaway 贈送 / question 詢問 / info 資訊 / spam 業配廣告 / unknown';
COMMENT ON COLUMN fb_post_classifications.categories IS '該貼文涉及的分類代碼清單（快速過濾用，明細見 fb_post_items）';
COMMENT ON COLUMN fb_post_classifications.item_count IS '拆出的商品項目數';
COMMENT ON COLUMN fb_post_classifications.needs_review IS '生成欄位：review_reasons 為非空陣列時 TRUE；寫入端只填 review_reasons，不可直接寫入本欄';
COMMENT ON COLUMN fb_post_classifications.review_reasons IS '需人工複核的原因清單';
COMMENT ON COLUMN fb_post_classifications.summary IS '模型產出的短句摘要，方便人工快速掃過';
COMMENT ON COLUMN fb_post_classifications.error IS '失敗原因（status = failed 時填寫）';
COMMENT ON COLUMN fb_post_classifications.raw_response IS '模型原始回覆文字，除錯與離線重播用';
COMMENT ON COLUMN fb_post_classifications.latency_ms IS '呼叫端點花費毫秒數';
COMMENT ON COLUMN fb_post_classifications.status IS '處理狀態：ok / low_confidence / not_applicable 非硬體貼文 / failed';

COMMENT ON COLUMN fb_post_items.classification_id IS '產生本列的分類結果 fb_post_classifications.id（同一篇貼文可並存多個 prompt 版本或模型的結果）';
COMMENT ON TABLE fb_post_items IS '貼文拆出的單一商品項目（分類/品牌/型號/容量/價格/可否購買）';
COMMENT ON COLUMN fb_post_items.post_id IS '所屬 canonical 貼文 ID';
COMMENT ON COLUMN fb_post_items.ordinal IS '同一篇貼文內的項目順序（由 1 起）';
COMMENT ON COLUMN fb_post_items.category IS '商品分類代碼（fb_taxonomy.category_key，is_product = TRUE）';
COMMENT ON COLUMN fb_post_items.brand_raw IS '貼文中的品牌原文';
COMMENT ON COLUMN fb_post_items.brand_key IS '正規化品牌（fb_brand.brand_key），無法辨識時為 NULL';
COMMENT ON COLUMN fb_post_items.model_raw IS '型號原文，例：RTX 3090 FE、SPATIUM S270';
COMMENT ON COLUMN fb_post_items.model_norm IS '正規化型號（小寫去空白/連接符），用於同型號聚合';
COMMENT ON COLUMN fb_post_items.item_condition IS '新舊狀態：new_sealed 全新未拆 / new_opened 僅拆試 / used 二手正常使用 / refurbished 整修 / for_parts 故障料件 / not_working 故障 / unknown';
COMMENT ON COLUMN fb_post_items.purchasable IS '這個品項現在能不能買到：yes 可買賣（出售或競標進行中）/ no 買不到（收購、已售出、內容被划掉）/ unknown 資訊不足；對應 benchmark 的 Y/N/?';
COMMENT ON COLUMN fb_post_items.bundle IS 'TRUE 表示本項目是套裝或半套（多個品項共用一個價格，例如板U套）。整機本身也標 TRUE，因此價格統計依本欄分組而不是直接排除';
COMMENT ON COLUMN fb_post_items.quantity IS '數量（記憶體 16G*2 記 1 條容量 + quantity 2）';
COMMENT ON COLUMN fb_post_items.capacity_value IS '容量或尺寸的數值（依廠商標示，十進位，不做 1024 換算）';
COMMENT ON COLUMN fb_post_items.capacity_unit IS '容量單位 B/MB/GB/TB/PB 或inch';
COMMENT ON COLUMN fb_post_items.capacity_bytes IS '換算後的位元組數（十進位），inch 為 NULL';
COMMENT ON COLUMN fb_post_items.price_amount IS '價格數字，無法辨識為 NULL（不可猜）';
COMMENT ON COLUMN fb_post_items.price_currency IS '幣種，台灣社團預設 TWD';
COMMENT ON COLUMN fb_post_items.price_kind IS '價格語意：asking 售價 / bundle 組合價 / starting_bid 起標價 / budget 收購預算 / reference 參考價 / sold_price 成交價 / unknown';
COMMENT ON COLUMN fb_post_items.negotiable IS '是否可議價（TRUE 可議價，FALSE 不議價，NULL 未提及）';
COMMENT ON COLUMN fb_post_items.price_note IS '價格補充說明，例：含運、可小議、含卡';
COMMENT ON COLUMN fb_post_items.warranty_status IS '保固狀態：in_warranty 在保 / expired 過保 / none 無保固 / unknown';
COMMENT ON COLUMN fb_post_items.warranty_end IS '保固到期日（能明確解析時才填）';
COMMENT ON COLUMN fb_post_items.warranty_raw IS '保固原文，例：原廠保固到 2031 年 11 月';
COMMENT ON COLUMN fb_post_items.location IS '交易地點原文，例：台中市南屯、高雄';
COMMENT ON COLUMN fb_post_items.delivery_methods IS '交易方式正規化清單：面交/宅配/超商/賣貨便/店到店/貨到付款/線上/其他';
COMMENT ON COLUMN fb_post_items.contact IS '聯絡方式，例：私訊、留言、電話';
COMMENT ON COLUMN fb_post_items.attributes IS '開放規格（jsonb，snake_case），例：{"interface":"PCIe4","form_factor":"2230","socket":"LGA1700","vram_gb":24,"watt":650}';
COMMENT ON COLUMN fb_post_items.snippet IS '支持上述欄位的原文證據（逐字，方便人工複核與 prompt 調整）';
COMMENT ON COLUMN fb_post_items.confidence IS '模型對本項目的自評信心 0-1';

COMMENT ON VIEW v_fb_listing IS '可購買（purchasable = yes：現售或標案進行中）商品清單，供前端或選品查詢';
COMMENT ON VIEW v_fb_market_price IS '分類 × 品牌 × 新舊狀態 × 成套與否（bundle）的 TWD 價格分佈（P25/中位數/P75）';

COMMIT;
