# -*- coding: utf-8 -*-
"""无预览图时的**占位图标**选型（用户 2026-10-11）。

背景：用户库里 83 个 mod 有 68 个没有预览图，原先一律显示"首字字母卡"
（一个大汉字 + 按名字哈希取的随机配色）。用户看完问「这些没有预览图的 mod
怎么处理会更好些」—— 结论是把占位图从**装饰**改成**信息位**：用图标直接
回答"这个 mod 是换什么的"。

判据只用**已有**的三样，不新增任何推断：
  ① pak 主名   —— `SkillChange_1_P` 里的 `skill`、`WPN_zaozi_reverse_99` 里的 `WPN`
  ② mod 名称   —— 「水球术」「长刀」
  ③ `meta.slots` —— 服装部位（`costume.py` 那套四路证据算出来的）

⚠️ 精度原则与 `costume.py` 一致：**认错比认不出糟**。所以：
  - 名称里匹配武器词时**要求词位于结尾、或作为独立片段**。反例：
    「剑星黑色冷却服」（剑星 = 那个角色）名字里带「剑」，但它是服装 mod ——
    用"包含"判就会给它画一把刀。
  - 认不出来就落到中性的「其他」图标，**不硬猜**。

⚠️ 优先级：**pak 名 > mod 名称 > 服装部位**。真实反例：
「改武器技能」这个 mod 的 `meta.slots` 里挂着 `Chest`（旧版服装证据的误判），
但它的 pak 叫 `SkillChange_1_P.pak` —— pak 名证据更硬，所以判成「技能」。
「长刀 mod」同理：`slots` 里是 `Chest`，但名字结尾写着"长刀"。
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from . import costume

#: 非服装的四类
KIND_WEAPON = "weapon"
KIND_SKILL = "skill"
KIND_UI = "ui"
KIND_OTHER = "other"

#: 这四类的显示名
KIND_LABELS = {
    KIND_WEAPON: "武器",
    KIND_SKILL: "技能",
    KIND_UI: "界面",
    # 用户 2026-10-30：「当不知道这个 mod 是什么时，就写未知。不要瞎写」
    # —— 这不是文字偏好，是诚实性要求：认不出来就不该假装认得。
    KIND_OTHER: "未知",
}

# ---------------------------------------------------------------- pak 名证据
#: pak 主名里出现这些词 → 技能 / 法术类
_PAK_SKILL_WORDS = ("skill", "ability", "buff", "spell", "talent")
#: pak 主名里出现这些词 → 武器模型类
_PAK_WEAPON_WORDS = (
    "weapon", "sword", "blade", "katana", "greatsword", "dagger",
    "spear", "halberd", "axe", "bow",
)

# ---------------------------------------------------------------- 名称证据
#: 出现即算技能（这两个词够长够专，不必限定位置）
_NAME_SKILL_ANY = ("技能", "法术", "招式", "施法", "召唤", "术法", "神通")
#: 只在**结尾或独立片段**才算技能（「术」太短，放"包含"会误伤「艺术」）
_NAME_SKILL_TAIL = ("术",)
#: 只在**结尾或独立片段**才算武器。⚠️ 不能放"包含" —— 「剑星黑色冷却服」会被误判。
_NAME_WEAPON_TAIL = (
    "武器", "刀", "剑", "枪", "斧", "弓", "弩", "矛", "锤", "杖",
    "长刀", "太刀", "短刀", "双刀", "巨剑", "大剑", "长剑", "法杖",
)
#: 出现即算界面 / UI
_NAME_UI_WORDS = ("界面", "ui", "hud", "字体", "图标包")

#: 名字结尾常见的"噪声后缀"——去掉它们再看结尾，`长刀模型` / `水球术mod` 才认得出来
_TAIL_NOISE = re.compile(
    r"(mod|模型|替换|包|整合包|整合|补丁|版|patch|pack)$"
)


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def _tokens(name: str) -> list[str]:
    """按分隔符切成片段（中文不会被切开，所以中文词走"结尾"那条路）。"""
    return [t for t in re.split(r"[\s_\-·|,，。+()（）\[\]]+", _clean(name)) if t]


def _at_tail(name: str, word: str) -> bool:
    """word 在名字结尾，或去掉噪声后缀后在结尾。"""
    low = _clean(name)
    if low.endswith(word):
        return True
    return _TAIL_NOISE.sub("", low).strip().endswith(word)


def _as_token(name: str, word: str) -> bool:
    """word 是名字里一个独立片段（`长刀 模型` 里的 `长刀`）。"""
    return word in _tokens(name)


def _name_says(name: str, words: Iterable[str]) -> bool:
    return any(_at_tail(name, w) or _as_token(name, w) for w in words)


def _stem_says(stem: str, words: Iterable[str]) -> bool:
    return any(w in stem for w in words)


def _stem_has_ga_prefix(stem: str) -> bool:
    """UE 的能力/技能类资源常以 `GA_`（GameplayAbility）开头。"""
    return bool(re.match(r"^ga[_-]", stem))


def classify(
    *,
    name: str = "",
    pak_stems: Sequence[str] = (),
    slots: Sequence[str] = (),
) -> str:
    """给出这张占位卡该画哪个图标。返回 `KIND_*` 或 `costume.SLOT_*`。

    ⚠️ 返回值直接当 `theme.PLACEHOLDER_COLORS` 的键用，别擅自造新值。
    """
    stems = [str(s).lower() for s in pak_stems if s]

    # ---- ① pak 名（最硬）----
    for stem in stems:
        if _stem_says(stem, _PAK_SKILL_WORDS) or _stem_has_ga_prefix(stem):
            return KIND_SKILL
    for stem in stems:
        if stem.startswith("wpn") or _stem_says(stem, _PAK_WEAPON_WORDS):
            return KIND_WEAPON

    # ---- ② mod 名称 ----
    if _name_says(name, _NAME_SKILL_ANY) or _name_says(name, _NAME_SKILL_TAIL):
        return KIND_SKILL
    if _name_says(name, _NAME_WEAPON_TAIL):
        return KIND_WEAPON
    if any(w in _clean(name) for w in _NAME_UI_WORDS):
        return KIND_UI

    # ---- ③ mod 名称里直接写着部位 ----
    # ⚠️ 为什么名字要排在 `meta.slots` **前面**：`slots` 是按 `SLOT_ORDER` 排的，
    #   「黑红短裙」的 slots 是 `[Chest, Legs, Face, Body]` —— 取第一个就成了"上衣"，
    #   可名字里明明写着"短裙"。用户看到的图标应该跟他看到的名字对得上。
    #   只在名字**恰好给出一个部位**时才采信（多个 = 名字本身指代不清，别猜）。
    named_slots = costume.slots_from_text(name)
    if len(named_slots) == 1:
        return named_slots[0]

    # ---- ④ 服装部位（按 SLOT_ORDER 取最靠前的，保证可重现）----
    have = set(slots or ())
    for slot in costume.SLOT_ORDER:
        if slot in have:
            return slot

    return KIND_OTHER


def label_for(kind: str) -> str:
    """图标下面那行小字。"""
    if kind in KIND_LABELS:
        return KIND_LABELS[kind]
    return costume.SLOT_LABELS.get(kind, KIND_LABELS[KIND_OTHER])  # → 「未知」


def color_key(kind: str) -> str:
    """取色用的键 —— 认不出来的一律落到中性灰。"""
    return kind
