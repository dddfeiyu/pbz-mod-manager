# -*- coding: utf-8 -*-
"""预览图四档降级（需求文档 3.6）。

硬性要求（原文）：**不允许出现空白**。

| 档位 | 触发条件 | 做法 |
|---|---|---|
| 1 | mod 压缩包内有截图 | 自动提取（最常见） |
| 2 | 无图 | 生成**图标占位卡**：按「这是什么」画图标（上衣/武器/技能…），颜色按类别固定 |
| 3 | 用户手动指定 | 支持替换图片，工具记住 |
| 4 | （首版不做）| 从 pak 内提取游戏贴图 |

本模块负责**第 1 档的挑选**与**第 2 档的配色/取字**，
第 3 档由 mod 库里 `preview` 字段指向用户换进来的图；
真正的像素渲染在 `ui/assets.py`（那儿才需要 Qt）。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from . import theme
from .pakfiles import IMAGE_EXT_SET

#: 预览图档位常量（写进 meta.json，UI 据此决定怎么画）
TIER_EXTRACTED = 1      # 从压缩包里提的
TIER_LETTER_CARD = 2    # 图标占位卡（不落盘，运行时画）
TIER_USER = 3           # 用户手动指定
TIER_PAK_TEXTURE = 4    # 首版不做

PREVIEW_TIER_LABELS = {
    TIER_EXTRACTED: "压缩包内截图",
    TIER_LETTER_CARD: "图标占位卡",
    TIER_USER: "用户指定",
    TIER_PAK_TEXTURE: "pak 内贴图",
}

#: 小于这个字节数的图片不当作截图（多半是图标 / 占位符 / 损坏文件）
MIN_PREVIEW_BYTES = 3 * 1024

#: 文件名里出现这些词，是截图的概率大幅提升
_STRONG_HINTS = ("preview", "screenshot", "screen", "cover", "封面", "预览", "截图", "展示", "效果图")
#: 出现这些词说明它不是截图
_NEGATIVE_HINTS = ("icon", "logo", "avatar", "favicon", "banner_small", "thumb_icon", "图标")


@dataclass
class PreviewPick:
    """第 1 档的挑选结果。"""

    path: Optional[Path]
    score: float
    reason: str

    @property
    def ok(self) -> bool:
        return self.path is not None


def _score_image(path: Path, index: int) -> tuple[float, str]:
    """给一张候选图打分。分数越高越像「作者特意放的预览图」。"""
    name = path.name.lower()
    try:
        size = path.stat().st_size
    except OSError:
        size = 0

    score = 0.0
    reasons: List[str] = []

    if size < MIN_PREVIEW_BYTES:
        return -1.0, f"文件过小（{size} B），不像截图"

    if any(h in name for h in _NEGATIVE_HINTS):
        score -= 40
        reasons.append("文件名像图标/logo")

    hint_hit = next((h for h in _STRONG_HINTS if h in name), None)
    if hint_hit:
        score += 50
        reasons.append(f"文件名含「{hint_hit}」")
    else:
        reasons.append("文件名无明确提示")

    # 文件名是 1 / 01 / 001 这类编号，多半是作者按顺序命名的截图
    stem = os.path.splitext(path.stem)[0]
    if stem.isdigit():
        score += 15
        reasons.append("文件名是纯编号")

    # 尺寸分：截图通常比图标大得多。用对数避免大图完全压制其他信号。
    score += min(size / 200_000.0, 30.0)
    reasons.append(f"体积 {size // 1024} KB")

    # 越靠前的候选（遍历顺序）稍微加权，保证结果稳定可复现
    score += max(0.0, 5.0 - index * 0.1)

    # webp 优先度略高：实测真实 mod 的预览图就是 webp 为主
    if path.suffix.lower() == ".webp":
        score += 3
        reasons.append("webp（实测最常见）")

    return score, "；".join(reasons)


def pick_preview_image(images: Sequence[Path]) -> PreviewPick:
    """从压缩包里的图片里挑一张当预览图。

    只考虑**图片文件**，绝不生成任何东西 —— 挑不出来就返回 `ok=False`，
    由调用方降级到第 2 档（字母卡）。这样职责清晰，也便于单测。
    """
    candidates = [p for p in images if p.suffix.lower() in IMAGE_EXT_SET]
    if not candidates:
        return PreviewPick(None, 0.0, "压缩包内没有图片文件")

    scored = [(_score_image(p, i), p) for i, p in enumerate(candidates)]
    scored.sort(key=lambda item: item[0][0], reverse=True)
    (best_score, best_reason), best = scored[0]

    if best_score < 0:
        return PreviewPick(None, best_score, f"全部候选都不合适（最佳：{best_reason}）")
    return PreviewPick(best, best_score, best_reason)


# ---------------------------------------------------------------- 字母卡
def letter_card_index(name: str) -> int:
    """按名称哈希分配固定配色索引。

    **必须稳定**：同一个 mod 在任何机器、任何次启动都得到同一色。
    所以用 sha1（不用 Python 内置 hash —— 它带随机盐，重启就变）。
    """
    digest = hashlib.sha1(name.encode("utf-8")).digest()
    return digest[0] % len(theme.LETTER_CARD_PALETTE)


def letter_card_colors(name: str) -> tuple[str, str]:
    """返回 (底色, 文字色)。"""
    return theme.LETTER_CARD_PALETTE[letter_card_index(name)]


def letter_for(name: str) -> str:
    """取名称首字（中文取首字，英文转大写）。"""
    for ch in (name or "").strip():
        if ch.isspace():
            continue
        if ch.isascii() and ch.isalpha():
            return ch.upper()
        if ch.isascii() and ch.isdigit():
            return ch
        if not ch.isascii():
            return ch
    return "?"


@dataclass
class PreviewSpec:
    """UI 画预览图所需要的一切信息。"""

    tier: int
    image_path: Optional[Path]
    letter: str
    bg: str
    fg: str
    mod_name: str

    @property
    def is_letter_card(self) -> bool:
        return self.image_path is None


def build_preview_spec(
    *,
    mod_name: str,
    meta_preview: str = "",
    meta_tier: int = 0,
    mod_dir: Optional[Path] = None,
) -> PreviewSpec:
    """汇总出最终要显示的预览图方案（四档降级）。

    - `meta_preview` 非空：说明库里已存了图（第 1 档提的，或第 3 档用户换的）
    - 否则：第 2 档字母卡
    """
    if meta_preview and mod_dir is not None:
        candidate = Path(mod_dir) / meta_preview
        if candidate.is_file():
            tier = meta_tier if meta_tier in (TIER_EXTRACTED, TIER_USER) else TIER_USER
            bg, fg = letter_card_colors(mod_name)
            return PreviewSpec(tier, candidate, letter_for(mod_name), bg, fg, mod_name)

    bg, fg = letter_card_colors(mod_name)
    return PreviewSpec(TIER_LETTER_CARD, None, letter_for(mod_name), bg, fg, mod_name)
