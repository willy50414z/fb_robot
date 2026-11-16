"""載入 prompt 版本目錄（system.txt / user.txt / output.schema.json）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

PLACEHOLDER = "<<<POST_TEXT>>>"


@dataclass(frozen=True)
class PromptBundle:
    version: str
    system: str
    user_template: str
    schema: dict

    def render_user(self, post_text: str, max_chars: int) -> str:
        # 用 replace 而非 format：貼文與 schema 內可能含 {}
        return self.user_template.replace(PLACEHOLDER, post_text[:max_chars])


def load_prompt(version: str) -> PromptBundle:
    base = resources.files("fb_robot.classify") / "prompts" / version
    if not base.is_dir():
        raise FileNotFoundError(f"找不到 prompt 版本目錄：prompts/{version}")
    return PromptBundle(
        version=version,
        system=(base / "system.txt").read_text(encoding="utf-8"),
        user_template=(base / "user.txt").read_text(encoding="utf-8"),
        schema=json.loads((base / "output.schema.json").read_text(encoding="utf-8")),
    )
