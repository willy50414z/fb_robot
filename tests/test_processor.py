"""以假游標驗證 upsert 分支邏輯（不需要真實資料庫）。"""

import pytest

from fb_robot.config import DedupSettings
from fb_robot.upsert import processor
from fb_robot.upsert.hashing import compute_simhash, make_content_hash
from fb_robot.upsert.processor import process_items, upsert_post

TEXT = "出售 RTX 4070 顯示卡 盒裝齊全 台北面交"
ITEM = {
    "id": "900001",
    "groupId": 382189418643825,
    "authorId": "A-1",
    "authorName": "王小明",
    "text": TEXT,
    "publishTime": 1763109000,
    "reactionCount": 4,
    "commentCount": 1,
    "shareCount": 0,
}

# SQL 辨識關鍵字 -> fetchone/fetchall 回應內容
MAPPING_BY_SOURCE = "WHERE group_post_id = %s"
CANONICAL_FOR_UPDATE = "FOR UPDATE"
AUTHOR_RECENT = "author_id = %(author_id)s"


class FakeCursor:
    def __init__(self, log, responses, fail_on=None):
        self.log = log
        self.responses = responses
        self.fail_on = fail_on
        self._result = None

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("boom")
        self._result = None
        for key, value in self.responses.items():
            if key in sql:
                self._result = value(params) if callable(value) else value
                break

    def fetchone(self):
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def fetchall(self):
        if isinstance(self._result, list):
            return self._result
        return [] if self._result is None else [self._result]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeConnection:
    """模擬 psycopg2 連線：`with conn` 即交易邊界。"""

    def __init__(self, responses=None, fail_on=None):
        self.log = []
        self.responses = responses or {}
        self.fail_on = fail_on
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self.log, self.responses, self.fail_on)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *exc_info):
        if exc_type is None:
            self.commits += 1
        else:
            self.rollbacks += 1
        return False

    def statements(self):
        return " ".join(sql for sql, _ in self.log)

    def canonical_writes(self):
        """主表/歷史表寫入時使用的 post_id。"""
        return [
            params["post_id"]
            for sql, params in self.log
            if isinstance(params, dict)
            and sql.lstrip().startswith(("INSERT INTO fb_group_posts", "UPDATE fb_group_posts"))
        ]


def canonical_row(post_id, content_hash):
    """只有查询该 post_id 時才存在 canonical（貼近 WHERE post_id = %s）。"""
    return lambda params: (post_id, content_hash) if params and str(params[0]) == post_id else None


def mapping_row(group_post_id, canonical_post_id, content_hash):
    """只有查询该来源贴文 ID 時才存在 mapping（貼近 WHERE group_post_id = %s）。"""
    if group_post_id == canonical_post_id:
        raise AssertionError("來源與 canonical 相同時不該建立 mapping 測試資料")
    return lambda params: (
        (canonical_post_id, "382189418643825", content_hash)
        if params and str(params[0]) == group_post_id
        else None
    )


def new_cursor(conn):
    return FakeCursor(conn.log, conn.responses, conn.fail_on)


def test_unknown_post_becomes_new_canonical():
    conn = FakeConnection()

    result = upsert_post(new_cursor(conn), ITEM)

    assert result == "inserted"
    assert "INSERT INTO fb_group_posts " in conn.statements()
    assert "INSERT INTO fb_group_posts_history" in conn.statements()
    # 新 canonical 只需寫主表/歷史表，不需要寫 mapping
    assert "INSERT INTO fb_post_group_map" not in conn.statements()


def test_similar_author_post_only_records_mapping():
    conn = FakeConnection(responses={
        AUTHOR_RECENT: [("700001", make_content_hash(TEXT), TEXT, compute_simhash(TEXT))],
        CANONICAL_FOR_UPDATE: canonical_row("700001", make_content_hash(TEXT)),
    })

    result = upsert_post(new_cursor(conn), ITEM)

    assert result == "deduped"
    assert "INSERT INTO fb_post_group_map" in conn.statements()
    assert "INSERT INTO fb_group_posts " not in conn.statements()
    assert "SET end_date" in conn.statements()


def test_mapping_source_with_newer_edit_updates_canonical():
    conn = FakeConnection(responses={
        MAPPING_BY_SOURCE: mapping_row("900001", "700001", "old-hash"),
        CANONICAL_FOR_UPDATE: canonical_row("700001", "canonical-hash"),
    })

    result = upsert_post(new_cursor(conn), ITEM)

    assert result == "updated_content"
    assert "UPDATE fb_group_posts_history" in conn.statements()
    assert "UPDATE fb_group_posts\n" in conn.statements()
    # 歷史版本與主表都應寫在 canonical 的 post_id 上，不是來源貼文 ID
    assert conn.canonical_writes() and set(conn.canonical_writes()) == {"700001"}


def test_dedup_threshold_comes_from_settings():
    almost_same = compute_simhash(TEXT) ^ 0b11111
    conn = FakeConnection(responses={
        AUTHOR_RECENT: [("700001", "other-hash", "完全不同的貼文內容", almost_same)],
    })

    result = upsert_post(new_cursor(conn), ITEM, DedupSettings(simhash_max_distance=0))

    assert result == "inserted"


def test_process_items_commits_each_post(monkeypatch):
    monkeypatch.setattr(processor, "ensure_schema", lambda conn: None)
    conn = FakeConnection()

    stats = process_items(conn, [ITEM])

    assert stats["inserted"] == 1
    assert conn.commits == 1


def test_process_items_counts_failure_and_rolls_back(monkeypatch):
    monkeypatch.setattr(processor, "ensure_schema", lambda conn: None)
    conn = FakeConnection(fail_on="INSERT INTO fb_group_posts ")

    stats = process_items(conn, [ITEM])

    assert stats["inserted"] == 0
    assert stats["failed"] == 1
    assert conn.rollbacks == 1


def test_process_items_ensures_schema_once(monkeypatch):
    calls = []
    monkeypatch.setattr(processor, "ensure_schema", lambda conn: calls.append(conn))
    conn = FakeConnection()

    process_items(conn, [])

    assert calls == [conn]


def test_upsert_post_rejects_item_without_post_id():
    conn = FakeConnection()

    with pytest.raises(ValueError, match="post_id"):
        upsert_post(new_cursor(conn), {"text": TEXT})
