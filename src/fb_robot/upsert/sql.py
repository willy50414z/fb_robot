"""跨社團貼文 upsert / mapping 所需的 SQL 語句。"""

# 情况 A：全新帖子 —— 主表 INSERT
SQL_INSERT_MAIN = """
INSERT INTO fb_group_posts (
    post_id, group_id, group_name, post_url,
    author_id, author_name,
    text, publish_time,
    reaction_count, comment_count, share_count,
    content_hash, simhash, start_date, end_date,
    created_at, updated_at
) VALUES (
    %(post_id)s, %(group_id)s, %(group_name)s, %(post_url)s,
    %(author_id)s, %(author_name)s,
    %(text)s, %(publish_time)s,
    %(reaction_count)s, %(comment_count)s, %(share_count)s,
    %(content_hash)s, %(simhash)s, NOW(), NOW(),
    NOW(), NOW()
)
"""

# 情况 A：全新帖子 —— 历史表插入首版
# 情况 A：全新帖子 —— 历史表插入首版
SQL_INSERT_HISTORY_FIRST = """
INSERT INTO fb_group_posts_history (
    post_id, group_id, publish_time,
    text, content_hash,
    reaction_count, comment_count, share_count,
    valid_from, valid_to, created_at
) VALUES (
    %(post_id)s, %(group_id)s, %(publish_time)s,
    %(text)s, %(content_hash)s,
    %(reaction_count)s, %(comment_count)s, %(share_count)s,
    NOW(), NULL, NOW()
)
"""

# 情况 B：内容变化 —— 关闭旧版本（同时把旧版本的动态指标刷成最后看到的值）
# 情况 B：内容变化 —— 关闭旧版本（同时把旧版本的动态指标刷成最后看到的值）
SQL_CLOSE_CURRENT_HISTORY = """
UPDATE fb_group_posts_history
SET valid_to       = NOW(),
    reaction_count = %(reaction_count)s,
    comment_count  = %(comment_count)s,
    share_count    = %(share_count)s
WHERE post_id = %(post_id)s
  AND valid_to IS NULL
"""

# 情况 B：内容变化 —— 插入新版本
# 情况 B：内容变化 —— 插入新版本
SQL_INSERT_HISTORY_NEW = """
INSERT INTO fb_group_posts_history (
    post_id, group_id, publish_time,
    text, content_hash,
    reaction_count, comment_count, share_count,
    valid_from, valid_to, created_at
) VALUES (
    %(post_id)s, %(group_id)s, %(publish_time)s,
    %(text)s, %(content_hash)s,
    %(reaction_count)s, %(comment_count)s, %(share_count)s,
    NOW(), NULL, NOW()
)
"""

# 情况 B：内容变化 —— 把主表更新为最新版本
# 情况 B：内容变化 —— 把主表更新为最新版本
SQL_UPDATE_MAIN_CONTENT_CHANGED = """
UPDATE fb_group_posts
SET text           = %(text)s,
    content_hash   = %(content_hash)s,
    simhash        = %(simhash)s,
    reaction_count = %(reaction_count)s,
    comment_count  = %(comment_count)s,
    share_count    = %(share_count)s,
    end_date       = NOW(),
    updated_at     = NOW()
WHERE post_id = %(post_id)s
"""

# 情况 C：内容不变 —— 只刷新主表 end_date / updated_at / 动态指标
# 情况 C：内容不变 —— 只刷新主表 end_date / updated_at / 动态指标
SQL_TOUCH_MAIN = """
UPDATE fb_group_posts
SET end_date       = NOW(),
    updated_at     = NOW(),
    reaction_count = %(reaction_count)s,
    comment_count  = %(comment_count)s,
    share_count    = %(share_count)s
WHERE post_id = %(post_id)s
"""

# 情况 C：内容不变 —— 同步刷新当前历史版本的动态指标
# 情况 C：内容不变 —— 同步刷新当前历史版本的动态指标
SQL_TOUCH_CURRENT_HISTORY = """
UPDATE fb_group_posts_history
SET reaction_count = %(reaction_count)s,
    comment_count  = %(comment_count)s,
    share_count    = %(share_count)s
WHERE post_id = %(post_id)s
  AND valid_to IS NULL
"""

# 读取当前主表里的 content_hash
# 读取当前主表里的 content_hash
SQL_SELECT_CURRENT_HASH = """
SELECT content_hash FROM fb_group_posts WHERE post_id = %s
"""

# 去重：同作者近期 canonical 貼文候選
# 去重：同作者近期 canonical 貼文候選
SQL_SELECT_AUTHOR_RECENT_POSTS = """
SELECT post_id, content_hash, text, simhash, publish_time
FROM fb_group_posts
WHERE author_id = %(author_id)s
  AND post_id <> %(post_id)s
  AND publish_time >= NOW() - (%(days)s * INTERVAL '1 day')
ORDER BY publish_time DESC NULLS LAST
LIMIT %(limit)s
"""
SQL_SELECT_MAPPING_BY_SOURCE = """
SELECT canonical_post_id, group_id, content_hash
FROM fb_post_group_map
WHERE group_post_id = %s
"""
SQL_UPSERT_MAPPING = """
INSERT INTO fb_post_group_map (
    canonical_post_id, group_id, group_post_id, content_hash, last_seen_at
) VALUES (
    %(canonical_post_id)s, %(group_id)s, %(group_post_id)s, %(content_hash)s, NOW()
)
ON CONFLICT (group_post_id) DO UPDATE SET
    canonical_post_id = EXCLUDED.canonical_post_id,
    group_id = EXCLUDED.group_id,
    content_hash = EXCLUDED.content_hash,
    last_seen_at = NOW()
"""
SQL_SELECT_CANONICAL_FOR_UPDATE = """
SELECT post_id, content_hash
FROM fb_group_posts
WHERE post_id = %s
FOR UPDATE
"""
SQL_SELECT_LATEST_MAPPED_HASH = """
SELECT group_post_id, content_hash, last_seen_at
FROM fb_post_group_map
WHERE canonical_post_id = %s
ORDER BY last_seen_at DESC NULLS LAST, group_post_id DESC
LIMIT 1
"""
