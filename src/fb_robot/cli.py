"""命令列介面：`fb-robot run | sync | fetch | groups | classify`。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from fb_robot.config import AppSettings, ConfigError, DatabaseSettings, LlmSettings, get_settings, load_env
from fb_robot.crawler import get_fb_group_posts
from fb_robot.service import run_sync_loop, sync_all_groups, sync_group_posts

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s - %(message)s"


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format=LOG_FORMAT)


def _load_settings(args: argparse.Namespace) -> AppSettings:
    if getattr(args, "env_file", None):
        load_env(args.env_file)
    return get_settings()


def _cmd_run(args: argparse.Namespace) -> int:
    run_sync_loop(_load_settings(args))
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    if args.group_id:
        failed = 0
        for group_id in args.group_id:
            stats = sync_group_posts(group_id, settings)
            failed += stats.get("failed", 0)
            print(json.dumps({"group_id": group_id, **stats}, ensure_ascii=False))
        return 1 if failed else 0
    results = sync_all_groups(settings)
    failed = sum(stats.get("failed", 0) for stats in results.values())
    print(json.dumps({str(k): v for k, v in results.items()}, ensure_ascii=False))
    return 1 if failed else 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    posts = get_fb_group_posts(args.group_id, max_items=args.max_items, settings=settings.apify)
    if args.output:
        Path(args.output).write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已寫入 {args.output}（{len(posts)} 筆）")
    else:
        print(f"抓取 {args.group_id} 完成，共 {len(posts)} 筆（使用 --output 存成 JSON）")
    return 0


def _cmd_groups(args: argparse.Namespace) -> int:
    for group_id, group_name in _load_settings(args).groups.items():
        print(f"{group_id}\t{group_name}")
    return 0


def _cmd_classify(args: argparse.Namespace) -> int:
    from fb_robot.classify.service import classify_pending, ensure_classify_schema
    from fb_robot.db import create_connection

    if getattr(args, "env_file", None):
        load_env(args.env_file)
    else:
        load_env()
    llm = LlmSettings.from_env()
    conn = create_connection(DatabaseSettings.from_env())
    try:
        if not args.dry_run:
            ensure_classify_schema(conn)
        stats = classify_pending(conn, llm, limit=args.limit, post_id=args.post_id, dry_run=args.dry_run)
    finally:
        conn.close()
    print(json.dumps(stats, ensure_ascii=False))
    return 1 if stats["failed"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fb-robot", description="Facebook 社團貼文同步工具")
    parser.add_argument("--log-level", default="INFO", help="日誌等級，例如 DEBUG/INFO/WARNING（預設 INFO）")
    parser.add_argument("--env-file", help="指定 .env 檔案路徑（預設自動尋找）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="依排程持續同步所有社團（主控台程序）")
    run.set_defaults(func=_cmd_run)

    sync = subparsers.add_parser("sync", help="單次同步，未指定社團時同步全部")
    sync.add_argument("--group-id", type=int, action="append", help="要同步的社團 ID，可重複指定")
    sync.set_defaults(func=_cmd_sync)

    fetch = subparsers.add_parser("fetch", help="僅抓取貼文，不寫入資料庫")
    fetch.add_argument("--group-id", type=int, required=True, help="要抓取的社團 ID")
    fetch.add_argument("--max-items", type=int, help="單次抓取上限（預設 APIFY_MAX_ITEMS）")
    fetch.add_argument("--output", help="把抓到的原始貼文存成 JSON 檔案")
    fetch.set_defaults(func=_cmd_fetch)

    groups = subparsers.add_parser("groups", help="列出目前設定的社團清單")
    groups.set_defaults(func=_cmd_groups)

    classify = subparsers.add_parser("classify", help="用 LLM 把尚未分類的貼文拆成商品項目（需安裝 .[classify]）")
    classify.add_argument("--limit", type=int, default=20, help="最多處理幾篇（預設 20）")
    classify.add_argument("--post-id", help="只處理指定貼文")
    classify.add_argument("--dry-run", action="store_true", help="呼叫模型並印出結果，不寫入資料庫")
    classify.set_defaults(func=_cmd_classify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
