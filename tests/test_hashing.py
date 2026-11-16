"""文字正規化、內容雜湊與 SimHash 距離行為。"""

import hashlib

from fb_robot.upsert.hashing import (
    compute_simhash,
    hamming_distance64,
    make_content_hash,
    normalize_text,
    to_simhash_int,
)

TEXT_A = "出售 RTX 4070 顯示卡   盒裝齊全 台北面交"
TEXT_A_SPACED = "出售 rtx 4070 顯示卡 盒裝齊全 台北面交"
TEXT_B = "徵求 Macbook Air M2 13 吋 二手 北部面交 可小議價 附發票公司貨"


def test_normalize_text_collapses_whitespace_and_zero_width():
    assert normalize_text("  出售\u200b RTX  4070\n\t顯示卡  ") == "出售 rtx 4070 顯示卡"
    assert normalize_text(None) == ""
    assert normalize_text("") == ""


def test_make_content_hash_is_stable_across_formatting():
    assert make_content_hash(TEXT_A) == make_content_hash(TEXT_A_SPACED)
    assert make_content_hash("  Foo ") == hashlib.sha256(b"foo").hexdigest()
    assert make_content_hash(TEXT_A) != make_content_hash(TEXT_B)


def test_compute_simhash_is_deterministic_and_signed_64bit():
    value = compute_simhash(TEXT_A)

    assert value == compute_simhash(TEXT_A)
    assert -(2**63) <= value < 2**63
    assert compute_simhash("") == 0


def test_hamming_distance_compares_u64_wrapped_values():
    a = compute_simhash(TEXT_A)

    assert hamming_distance64(a, a) == 0
    assert hamming_distance64(a, a + 1) > 0
    assert hamming_distance64(-1, -1) == 0
    assert hamming_distance64(0, 1) == 1
    assert hamming_distance64(0, 3) == 2


def test_simhash_distance_ranks_near_duplicate_below_unrelated_text():
    long_post = (
        "出售 ASUS ROG Strix B650E-F Gaming WiFi 主機板 盒裝齊全 "
        "購入半年 附發票 CPU RAM 都有升級 台北新北可面交 歡迎私訊詢問"
    )
    appended = long_post + " 另附散熱膏與SATA線"
    unrelated = (
        "徵求 Macbook Air M2 13 吋 二手 北部面交 可小議價 附發票公司貨 謝謝 有意私訊"
        " 價格可談 另可加購保護殼與充電器 台中亦可"
    )
    base = compute_simhash(long_post)

    # 正規化後相同的內容必定距離 0
    assert hamming_distance64(base, compute_simhash("  " + long_post + "  ")) == 0
    # 增加少許文字的近似重複，距離要明顯小於完全不相關的貼文
    assert hamming_distance64(base, compute_simhash(appended)) < hamming_distance64(base, compute_simhash(unrelated))


def test_to_simhash_int_accepts_db_variants():
    assert to_simhash_int(None) is None
    assert to_simhash_int(12) == 12
    assert to_simhash_int("12") == 12
    assert to_simhash_int("abc") is None
