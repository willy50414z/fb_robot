"""建立/維護 upsert 流程所需的 schema、索引與資料表註解。"""

from fb_robot.upsert.records import _row_get


def ensure_schema(conn) -> None:
    """確保跨社團去重所需欄位/資料表存在（向後相容既有 schema）。"""
    with conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE fb_group_posts ADD COLUMN IF NOT EXISTS simhash BIGINT")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fb_post_group_map (
                    canonical_post_id TEXT NOT NULL REFERENCES fb_group_posts(post_id) ON DELETE CASCADE,
                    group_id TEXT NOT NULL,
                    group_post_id TEXT NOT NULL UNIQUE,
                    content_hash TEXT,
                    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            # 若舊版 occurrences 表存在，遷移為精簡 mapping 資料
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema='public' AND table_name='fb_group_post_occurrences'
                ) AS exists
                """
            )
            row = cur.fetchone()
            exists = _row_get(row, "exists", 0)
            if exists:
                cur.execute(
                    """
                    INSERT INTO fb_post_group_map (canonical_post_id, group_id, group_post_id, content_hash, last_seen_at)
                    SELECT canonical_post_id, group_id, group_post_id, content_hash, COALESCE(last_seen_at, NOW())
                    FROM fb_group_post_occurrences
                    WHERE group_id IS NOT NULL AND group_post_id IS NOT NULL
                      AND canonical_post_id <> group_post_id
                    ON CONFLICT (group_post_id) DO NOTHING
                    """
                )
                cur.execute("DROP TABLE IF EXISTS fb_group_post_occurrences")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_fb_group_posts_author_publish ON fb_group_posts(author_id, publish_time DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_fb_group_posts_author_hash ON fb_group_posts(author_id, content_hash)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_fb_post_group_map_canonical ON fb_post_group_map(canonical_post_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_fb_post_group_map_group ON fb_post_group_map(group_id)"
            )

            # 資料表與欄位註解（繁體中文）
            cur.execute("COMMENT ON TABLE fb_group_posts IS 'Facebook 社團貼文主表（canonical，保留去重後的貼文與最新狀態）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.id IS '主鍵流水號'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.post_id IS 'Facebook 貼文 ID（canonical 貼文唯一識別）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.group_id IS '來源社團 ID（canonical 貼文首次/主要來源社團）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.group_name IS '來源社團名稱'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.post_url IS '貼文連結'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.author_id IS '作者 ID'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.author_name IS '作者名稱'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.text IS '貼文內容（目前最新版本）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.publish_time IS '貼文發布時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.reaction_count IS '心情/反應數（最近一次抓取）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.comment_count IS '留言數（最近一次抓取）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.share_count IS '分享數（最近一次抓取）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.content_hash IS '正規化本文內容雜湊值（用於判斷內容是否變更/相同）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.start_date IS '首次抓取/收錄時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.end_date IS '最近一次確認仍存在或更新的時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.created_at IS '資料建立時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.updated_at IS '資料更新時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts.simhash IS '本文 SimHash 指紋（用於同作者跨社團相似貼文比對）'")

            cur.execute("COMMENT ON TABLE fb_group_posts_history IS 'Facebook 社團貼文內容版本歷史表'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.id IS '主鍵流水號'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.post_id IS '對應 canonical 貼文 ID'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.group_id IS '來源社團 ID'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.publish_time IS '貼文發布時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.text IS '該版本貼文內容'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.content_hash IS '該版本內容雜湊值'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.reaction_count IS '該版本觀測到的心情/反應數'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.comment_count IS '該版本觀測到的留言數'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.share_count IS '該版本觀測到的分享數'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.valid_from IS '此版本生效起始時間'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.valid_to IS '此版本生效結束時間（NULL 表示目前有效版本）'")
            cur.execute("COMMENT ON COLUMN fb_group_posts_history.created_at IS '版本記錄建立時間'")

            cur.execute("COMMENT ON TABLE fb_post_group_map IS '同內容貼文跨社團對應表（第一個抓到的 canonical 貼文對應其他社團相同貼文來源）'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.canonical_post_id IS 'canonical 貼文 ID（對應 fb_group_posts.post_id）'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.group_id IS '來源社團 ID（重複/對應來源所在社團）'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.group_post_id IS '來源社團中的 Facebook 貼文 ID（唯一）'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.content_hash IS '該來源貼文最近一次觀測到的內容雜湊值（用於判斷來源是否有更新）'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.last_seen_at IS '最近一次看到此來源貼文的時間'")
            cur.execute("COMMENT ON COLUMN fb_post_group_map.created_at IS '對應關係建立時間'")


# ---------------------------------------------------------------------------
# 核心逻辑
# ---------------------------------------------------------------------------
