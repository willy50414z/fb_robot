"""貼文資料 -> DB 參數的轉換行為。"""

from datetime import datetime, timezone

import pytest

from fb_robot.upsert.records import build_params, pick, to_int, to_ts


def test_to_ts_supports_actor_time_formats():
    expected = datetime(2026, 11, 14, 8, 30, tzinfo=timezone.utc)

    assert to_ts(1763109000) == datetime.fromtimestamp(1763109000, tz=timezone.utc)
    assert to_ts(1763109000000) == datetime.fromtimestamp(1763109000, tz=timezone.utc)
    assert to_ts("1763109000") == datetime.fromtimestamp(1763109000, tz=timezone.utc)
    assert to_ts("2026-11-14T08:30:00Z") == expected
    assert to_ts("2026-11-14T08:30:00") == expected
    assert to_ts("not-a-time") is None
    assert to_ts(None) is None
    assert to_ts("") is None


def test_to_ts_attaches_utc_to_naive_datetime():
    assert to_ts(datetime(2026, 11, 14, 8, 30)).tzinfo == timezone.utc


def test_pick_returns_first_non_none_key():
    item = {"postId": None, "id": "1234", "groupId": 42}

    assert pick(item, "id", "postId") == "1234"
    assert pick(item, "postId", "missing", default="fallback") == "fallback"
    assert pick(item, "groupId") == 42


def test_to_int_falls_back_on_bad_values():
    assert to_int("12") == 12
    assert to_int(None) == 0
    assert to_int("n/a", default=7) == 7


def test_build_params_maps_actor_camel_case_fields():
    params = build_params({
        "id": "100001",
        "groupId": 133186676696605,
        "groupName": "電腦零件買賣集中營",
        "url": "https://www.facebook.com/groups/133186676696605/posts/100001",
        "authorId": "A-1",
        "authorName": "王小明",
        "text": "出售 RTX 4070",
        "publishTime": 1763109000,
        "reactionCount": "12",
        "commentCount": None,
        "shareCount": 3,
    })

    assert params["post_id"] == "100001"
    assert params["group_id"] == 133186676696605
    assert params["group_name"] == "電腦零件買賣集中營"
    assert params["author_id"] == "A-1"
    assert params["reaction_count"] == 12
    assert params["comment_count"] == 0
    assert params["share_count"] == 3
    assert params["publish_time"] == datetime.fromtimestamp(1763109000, tz=timezone.utc)
    assert len(params["content_hash"]) == 64
    assert isinstance(params["simhash"], int)


def test_build_params_requires_post_id():
    with pytest.raises(ValueError, match="post_id"):
        build_params({"text": "沒有貼文 ID"})
