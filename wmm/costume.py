# -*- coding: utf-8 -*-
"""服装 mod 识别：这个 mod 换的是哪套衣服、动了哪些部位。

## 为什么不能靠"读 pak 内部"（已实测，勿重复调研）

真实 mod 的 .pak 是 **UE5 v11 紧凑索引**：footer（魔数 `0x5A6F12E1`）能读，
挂载点能读（都是 `../../../Project_Plague/Content/`），文件数能读，
但**索引体里一个文件名字节都没有** —— 实测 4 个真实 pak，
`uasset` / `umap` 出现次数均为 0，可打印字节占比 28.8%（纯二进制结构），
pak 的数据段还是 **Oodle** 压缩（footer 的压缩方法表里写着 `Oodle`）。
→ **纯 Python 读不出 pak 里的资源路径**，除非引入 repak（第三方 Rust 程序）。

## 那靠什么识别（本模块的答案）

用户的 mod 有非常稳定的元数据习惯，实测四路证据，可靠性从高到低：

1. **服装配置 JSON**（最可靠，精确到槽位）
   服装加载器类 mod 会在 pak 旁边放一个 `.json`，形如：
       [{"RowName":"XXTB Slingkini Chest",
         "CostumeObject":"/Game/Mods/WCL_Costumes/Chest/..._Jacket...._Jacket",
         "CostumeType":"Chest", "CostumeName":"XXTB Slingkini[Chest]", ...}]
   `CostumeType` 直接就是部位（Chest/Arms/Legs/Helm），`CostumeName` 是新外观名。
2. **目录层级**（很可靠）
   作者按 `<游戏原衣服>/<新外观>/xxx.pak` 组织，例如
       `以往绅士mod合集/NSFW合集/1.衣服 套装、散件/明甲/女仆装/EVEOceanMaid_ReplaceMing_P.pak`
   外层目录名就是被替换的那套衣服。
3. **pak 文件名**（可靠）
   作者常把目标写进文件名：`ReplaceMing` / `ReplacePirate` /
   `EmbroidedHeadband`（织绣头饰）/ `EVEMaidArisa`（EVE 女仆阿丽莎）。
   部位也能从名字看出来：`_Jacket_` 上衣 / `_Hands_` 护手 / `_Pants_` 裤子 / `_Hat_` 帽子 / `Hair` 头发。
4. **说明文本**（补强）
   `均为替换织绣胸甲起效.txt` 这类一句话说明。

另外，真实 mod 的**一个包里常常同时有上衣/护手/裤子**（实测那个 140 MB 的
情趣内衣 mod 的 JSON 就有 Chest + Arms + Legs 三项）—— 所以 `slots` 是**集合**，
不是单值。这正是用户问的"一个 mod 里有的换整套、有的换头发、有的换裤子"。

## 能力边界（必须让用户看见）

- 这一切都是**推断**，不是解析 pak 得出的结论。所以每个结果都带 `source`
  与 `confidence`，界面上要标出来，不能让用户以为这是游戏内事实。
- 词表是**可扩充**的：`OUTFIT_VOCAB` 是种子，`extra_vocab` 允许调用方传入
  （导入时把上一级目录名一起喂进来，等于用作者自己的分类当词表）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 部位
SLOT_CHEST = "Chest"
SLOT_ARMS = "Arms"
SLOT_LEGS = "Legs"
SLOT_HELM = "Helm"
SLOT_HAIR = "Hair"
SLOT_FACE = "Face"
SLOT_FEET = "Feet"
SLOT_BODY = "Body"
SLOT_FULL = "Full"
#: 2026-10-29 新增：影之刃零有武器 mod（换刀/换剑），明末只有换衣服所以原来没有这一类
SLOT_WEAPON = "Weapon"

#: 槽位出现顺序（界面按这个顺序展示）
SLOT_ORDER: Tuple[str, ...] = (
    SLOT_WEAPON,
    SLOT_FULL, SLOT_CHEST, SLOT_ARMS, SLOT_LEGS, SLOT_HELM, SLOT_HAIR,
    SLOT_FACE, SLOT_FEET, SLOT_BODY,
)

SLOT_LABELS: Dict[str, str] = {
    SLOT_WEAPON: "武器",
    SLOT_FULL: "整套",
    SLOT_CHEST: "上衣",
    SLOT_ARMS: "护手",
    SLOT_LEGS: "裤子/裙",
    SLOT_HELM: "头饰",
    SLOT_HAIR: "发型",
    SLOT_FACE: "面部",
    SLOT_FEET: "鞋靴",
    SLOT_BODY: "身形",
}

#: 关键词 → 槽位。键一律小写；中文直接原样匹配。
SLOT_KEYWORDS: Dict[str, str] = {
    # 上衣
    # 武器（影之刃零）：换刀/换剑类 mod
    "weapon": SLOT_WEAPON, "weapons": SLOT_WEAPON, "blade": SLOT_WEAPON,
    "sword": SLOT_WEAPON, "katana": SLOT_WEAPON, "saber": SLOT_WEAPON,
    "dagger": SLOT_WEAPON, "spear": SLOT_WEAPON, "axe": SLOT_WEAPON,
    "武器": SLOT_WEAPON, "刀": SLOT_WEAPON, "剑": SLOT_WEAPON, "刃": SLOT_WEAPON,
    "长刀": SLOT_WEAPON, "太刀": SLOT_WEAPON, "长枪": SLOT_WEAPON,
    "jacket": SLOT_CHEST, "chest": SLOT_CHEST, "cloth_top": SLOT_CHEST,
    "top": SLOT_CHEST, "upper": SLOT_CHEST, "armor_top": SLOT_CHEST,
    "上衣": SLOT_CHEST, "胸甲": SLOT_CHEST, "衣服": SLOT_CHEST, "外套": SLOT_CHEST,
    "袍": SLOT_CHEST, "袄": SLOT_CHEST,
    # 护手
    "hands": SLOT_ARMS, "hand": SLOT_ARMS, "glove": SLOT_ARMS, "arms": SLOT_ARMS,
    "arm": SLOT_ARMS, "bracer": SLOT_ARMS,
    "护手": SLOT_ARMS, "手套": SLOT_ARMS, "袖": SLOT_ARMS, "臂": SLOT_ARMS,
    # 裤子 / 裙
    "pants": SLOT_LEGS, "legs": SLOT_LEGS, "leg": SLOT_LEGS, "skirt": SLOT_LEGS,
    "trouser": SLOT_LEGS,
    "裤": SLOT_LEGS, "裙": SLOT_LEGS, "腿": SLOT_LEGS, "下装": SLOT_LEGS,
    # 头饰
    "hat": SLOT_HELM, "helm": SLOT_HELM, "helmet": SLOT_HELM, "cap": SLOT_HELM,
    "headband": SLOT_HELM, "hairpin": SLOT_HELM, "headwear": SLOT_HELM,
    "帽子": SLOT_HELM, "头饰": SLOT_HELM, "头冠": SLOT_HELM, "发带": SLOT_HELM,
    "发簪": SLOT_HELM, "钗": SLOT_HELM, "冠": SLOT_HELM,
    # 发型
    "hair": SLOT_HAIR, "hairs": SLOT_HAIR, "hairstyle": SLOT_HAIR,
    # `LongTail` / `twinTail` / `midTail` —— 这游戏里 tail 指的是"马尾"这个发型，
    # 不是动物尾巴（实测作者的命名：`EmbroidedHeadband_W_longTail_P.pak`）。
    "tail": SLOT_HAIR, "ponytail": SLOT_HAIR,
    "发型": SLOT_HAIR, "头发": SLOT_HAIR, "长发": SLOT_HAIR, "短发": SLOT_HAIR,
    "马尾": SLOT_HAIR,
    # 面部
    "face": SLOT_FACE, "mask": SLOT_FACE, "makeup": SLOT_FACE, "skull": SLOT_FACE,
    "面具": SLOT_FACE, "脸部": SLOT_FACE, "面部": SLOT_FACE, "纹身": SLOT_FACE,
    # 鞋
    "shoe": SLOT_FEET, "shoes": SLOT_FEET, "boot": SLOT_FEET, "boots": SLOT_FEET,
    "feet": SLOT_FEET, "foot": SLOT_FEET,
    "鞋": SLOT_FEET, "靴": SLOT_FEET, "足": SLOT_FEET,
    # 身形（骨骼/体型）
    "body": SLOT_BODY, "skeleton": SLOT_BODY, "shape": SLOT_BODY,
    "physique": SLOT_BODY, "mesh": SLOT_BODY,
    "身形": SLOT_BODY, "体型": SLOT_BODY, "身材": SLOT_BODY, "骨骼": SLOT_BODY,
    # 整套
    "full": SLOT_FULL, "suit": SLOT_FULL, "set": SLOT_FULL, "outfit": SLOT_FULL,
    "costume": SLOT_FULL, "replaceall": SLOT_FULL,
    "套装": SLOT_FULL, "整套": SLOT_FULL, "全身": SLOT_FULL, "一键": SLOT_FULL,
    "全果": SLOT_FULL, "裸": SLOT_FULL,
}

#: UE 资源名 → 槽位（`CostumeObject` 路径里出现的类名，优先级高于自由关键词）
ASSET_CLASS_SLOTS: Dict[str, str] = {
    "jacket": SLOT_CHEST, "top": SLOT_CHEST, "cloth": SLOT_CHEST,
    "hands": SLOT_ARMS, "glove": SLOT_ARMS,
    "pants": SLOT_LEGS, "skirt": SLOT_LEGS,
    "hat": SLOT_HELM, "helm": SLOT_HELM, "headband": SLOT_HELM, "hairpin": SLOT_HELM,
    "hair": SLOT_HAIR,
    "mask": SLOT_FACE, "makeup": SLOT_FACE,
    "shoe": SLOT_FEET, "boot": SLOT_FEET,
    "body": SLOT_BODY, "skeleton": SLOT_BODY,
}

#: 服装加载器类的 JSON 里出现的槽位字段值 → 我们的槽位
JSON_SLOT_ALIASES: Dict[str, str] = {
    "chest": SLOT_CHEST, "torso": SLOT_CHEST, "jacket": SLOT_CHEST,
    "arms": SLOT_ARMS, "hands": SLOT_ARMS,
    "legs": SLOT_LEGS, "pants": SLOT_LEGS,
    "helm": SLOT_HELM, "head": SLOT_HELM, "hat": SLOT_HELM,
    "hair": SLOT_HAIR,
    "face": SLOT_FACE, "mask": SLOT_FACE,
    "feet": SLOT_FEET, "shoes": SLOT_FEET,
    "body": SLOT_BODY,
    "full": SLOT_FULL, "set": SLOT_FULL,
}

# ---------------------------------------------------------------- 衣服词表
#: 游戏内原衣服名的种子词表。
#:
#: 来源：用户真实 mod 库里的作者分类目录（`以往绅士mod合集/NSFW合集/1.衣服 套装、散件/`
#: 下面 36 个子目录名 = 作者按"替换哪套衣服"分的类）+ 常见英文别名。
#: **这不是猜的** —— 作者自己就是这么分类的，等于现成的映射表。
OUTFIT_VOCAB: Tuple[str, ...] = (
    # —— 中文原衣服名（来自作者分类目录）——
    "夜夫人", "大西督军", "头陀", "女眷", "宫女", "引魂法衣", "心魔",
    "抚南将军", "新娘", "方摇肚兜", "方玲", "明兵污衣", "明妃", "明甲",
    "流金", "海盗", "百户套装", "真武鞋", "神人守卫", "祭祀", "红衣女",
    "虎棉袄", "蚰蜒", "锦绣", "雪堡怪医", "雪堡管家鞋", "高级侍女绸衣",
    "黄金海盗", "黑白无常", "龙女", "假僧侣鞋", "变脸艺人靴子",
    "织绣胸甲", "织绣", "基础身形", "明军",
)

#: 英文/异写别名 → 中文原衣服名。
#:
#: ⚠️ **这里只放"游戏里真实存在的衣服"。** 踩过的坑：最初把
#: `maid → 女仆装`、`EVE → EVE` 也放进来了，结果
#: `剑星女仆装 海盗服/EVEMaidArisa_ReplacePirate_P.pak` 被判成
#: 「替换 女仆装、EVE、海盗」—— 但女仆装和 EVE 是**新外观**的名字，
#: 不是被替换的目标。被替换的只有「海盗」。
#: 判据：出现在作者分类目录 `1.衣服 套装、散件/` 下的名字才是原衣服名。
OUTFIT_ALIASES: Dict[str, str] = {
    "replaceming": "明甲", "ming": "明甲",
    "replacepirate": "海盗", "pirate": "海盗", "海寇": "海盗",
    "embroided": "织绣", "xiupao": "织绣", "embroidery": "织绣",
    "replaceembroided": "织绣",
}

#: 「替换」意图词：出现在名字里说明它是在替换某个东西
_REPLACE_HINTS = ("replace", "替换", "覆盖", "改成", "变成", "overwrite", "for_", "for ")

#: 明显不是衣服名、不能当"被替换目标"的词
_NOT_OUTFIT = frozenset({
    "project_plague", "project plague", "projectplague", "paks", "~mods", "logicmods",
    "content", "binaries", "win64", "mods", "files", "preview", "预览图", "预览",
    "main", "default", "base", "male", "female", "p", "pak", "mod", "ue4ss",
    "bitfix", "screenshots", "screenshot",
    # 用户机器上的常见路径词（2026-10-29：Desktop 含 top 被误判成上衣）
    "desktop", "downloads", "documents", "users", "桌面", "下载", "文档",
})

_URL_RE = re.compile(r"https?://\S+", re.I)
#: `pakchunk123-Windows_P.pak` 这类没有语义的名字，参与识别前先剔掉
_MEANINGLESS_STEM_RE = re.compile(r"^(pakchunk|chunk|patch)\d*", re.I)

#: 作者的**分类目录**名，只说明"这是衣服类"，不说明动了哪个部位。
#: 真实例：`1.衣服 套装、散件` 会同时命中「衣服→上衣」和「套装→整套」，
#: 于是每个 mod 都被标成「整套 + 上衣」—— 那是分类名的噪音，不是 mod 的性质。
#: 命中这些词就把该片段从**部位识别**里排除（衣服名识别仍然照做）。
_CATEGORY_HINT_WORDS = (
    "合集", "散件", "分类", "功能性", "系统", "其他", "其它", "整理", "大全",
    "套装、散件", "部件", "collection", "pack",
)


def is_category_name(seg: str) -> bool:
    """这个路径片段是不是"分类目录名"（只描述类别，不描述部位）。"""
    low = str(seg).lower()
    return any(w in low for w in _CATEGORY_HINT_WORDS)



def normalize_token(text: str) -> str:
    """把文件名/目录名归一成便于比对的 token。"""
    s = str(text).strip().lower()
    s = re.sub(r"\.(pak|ucas|utoc|json|txt|md|ini|png|jpe?g|webp|bmp|gif)$", "", s)
    s = re.sub(r"[_\-\s]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def is_meaningless(text: str) -> bool:
    """这个片段是不是"没有语义的"（安装层级名、pakchunk 编号、纯数字）。"""
    low = normalize_token(text)
    if not low:
        return True
    if low in _NOT_OUTFIT:
        return True
    if _MEANINGLESS_STEM_RE.match(low):
        return True
    if re.fullmatch(r"[\d\s._-]+", low):
        return True
    return False


_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def slots_from_text(text: str) -> List[str]:
    """从任意文本里找部位/武器关键词。返回去重后的槽位列表（按 SLOT_ORDER 排序）。

    ⚠️ 英文关键词**必须按词匹配**，不能直接子串包含（2026-10-29 事故：
    导入路径里的 `Desktop` 含 `top` → 被认成「上衣」，用户报
    「天锁斩月是武器 mod，你识别成衣服了」）。
    中文没有分词，仍然用子串匹配（「上衣」「刀」这类词不会被别的词包住）。
    """
    low = str(text).lower()
    tokens = {t for t in _WORD_SPLIT_RE.split(low) if t}
    hits: List[str] = []
    for keyword, slot in SLOT_KEYWORDS.items():
        if slot in hits:
            continue
        if keyword.isascii():
            # 关键词本身可能带下划线（如 cloth_top），拆成片段后要求**全部**命中
            parts = [p for p in _WORD_SPLIT_RE.split(keyword) if p]
            if parts and all(p in tokens for p in parts):
                hits.append(slot)
        elif keyword in low:
            hits.append(slot)
    return sorted(hits, key=lambda s: SLOT_ORDER.index(s) if s in SLOT_ORDER else 99)


def slots_from_asset_path(asset: str) -> List[str]:
    """从 UE 资源路径里判部位。

    从后往前扫（类名在最后，最具体），先命中先用。
    例：`.../cloth_02/XiuPao_Jacket.XiuPao_Jacket` → 上衣
    """
    if not asset:
        return []
    tail = str(asset).rsplit("/", 1)[-1].lower()
    tail = tail.split(".", 1)[0]
    parts = [p for p in re.split(r"[_\s]+", tail) if p]
    for part in reversed(parts):
        if part in ASSET_CLASS_SLOTS:
            return [ASSET_CLASS_SLOTS[part]]
    return []


def slots_from_asset_dir(asset: str) -> List[str]:
    """从资源路径的**目录**部分补判部位（`/Mods/WCL_Costumes/Chest/...`）。"""
    if not asset:
        return []
    dirs = str(asset).split("/")[:-1]
    for part in reversed(dirs):
        token = normalize_token(part)
        if token in JSON_SLOT_ALIASES:
            return [JSON_SLOT_ALIASES[token]]
        for keyword, slot in ASSET_CLASS_SLOTS.items():
            if keyword == token:
                return [slot]
    return []


def find_outfits(text: str, vocab: Sequence[str] = ()) -> List[str]:
    """在文本里找出被替换的游戏原衣服名。"""
    if not text:
        return []
    low = str(text).lower()
    merged = {**OUTFIT_ALIASES}
    for word in tuple(OUTFIT_VOCAB) + tuple(vocab):
        merged.setdefault(word.lower(), word)

    found: List[str] = []
    for key in sorted(merged, key=len, reverse=True):
        if not key or key in _NOT_OUTFIT:
            continue
        if key in low:
            label = merged[key]
            if label not in found:
                found.append(label)

    # 去掉被更长的命中包住的短词：`织绣胸甲` 命中后就不该再报 `织绣`。
    # 否则界面上会出现「替换 织绣胸甲、织绣」这种把一个东西说成两个的噪音。
    pruned: List[str] = []
    for item in found:
        if any(item != other and item in other for other in found):
            continue
        pruned.append(item)
    return pruned


# ---------------------------------------------------------------- 服装配置 JSON
@dataclass
class CostumeJsonResult:
    slots: List[str] = field(default_factory=list)
    look_names: List[str] = field(default_factory=list)
    replaced: List[str] = field(default_factory=list)
    assets: List[str] = field(default_factory=list)
    #: `CostumeIcon` 路径（形如 `.../Outfit/WuC_Base02/WuC_Base02_Jacket_S`）。
    #: 槽位编号就在这里 —— 它指向的**游戏原生图标**说明这个 mod 挂在哪个原套装槽位上。
    icons: List[str] = field(default_factory=list)
    #: `CostumeObject` 指向游戏原生资源的那些行的 `CostumeName`。
    #: 这些名字**本身就是被替换的原衣服名**（不是新外观名）。
    native_names: List[str] = field(default_factory=list)
    rows: int = 0


#: `CostumeObject` 是否指向**游戏自带资源**（而不是 mod 新增的）。
#: mod 新增的资源统一放在 `/Game/Mods/...` 下，游戏自己的在 `/Game/Assets/...`。
_NATIVE_ASSET_RE = re.compile(r"^/Game/Assets/", re.I)


def _is_native_asset(asset: str) -> bool:
    return bool(_NATIVE_ASSET_RE.match(str(asset).strip()))


#: `CostumeIcon` 路径里的 `WuC_BaseNN` 槽位编号 → 游戏原生套装名。
#:
#: 证据（实物，不是猜的）：服装加载器自带的 `WCL-Default_Embroided.json` 里
#:     CostumeObject = /Game/Assets/Character/player/player_wuchang_rebined/cloth/cloth_02/XiuPao_Jacket
#:     CostumeIcon   = /Game/Assets/UIAssets/Icon/Outfit/WuC_Base02/WuC_Base02_Jacket_S
#: 同一行把 `Base02` 槽位与 `cloth_02` / `XiuPao`（绣袍）/ `Embroided` 绑在一起，
#: 而 `embroided → 织绣` 在 OUTFIT_ALIASES 里同样有据。
#: → **只放有实物的编号**；没有证据的宁可认不出来，也不要认错（认错比认不出更糟）。
ICON_SLOT_OUTFITS: Dict[str, str] = {
    "base02": "织绣",
}

_ICON_SLOT_RE = re.compile(r"(?:WuC_)?(Base\d{1,2})(?![0-9])", re.I)


def outfits_from_icons(icons: Sequence[str]) -> List[str]:
    """从 `CostumeIcon` 的槽位编号反推被替换的游戏原套装。

    只认 `ICON_SLOT_OUTFITS` 里有实物的编号；其余一律不猜。
    """
    out: List[str] = []
    for icon in icons:
        for match in _ICON_SLOT_RE.finditer(str(icon)):
            name = ICON_SLOT_OUTFITS.get(match.group(1).lower())
            if name and name not in out:
                out.append(name)
    return out


def parse_costume_json(path: Path) -> Optional[CostumeJsonResult]:
    """解析服装加载器的 `.json`。不是这类文件返回 None。

    认出来的判据：是一个数组，且元素里有 `CostumeType` 或 `CostumeObject`。
    """
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list) or not data:
        return None
    if not all(isinstance(row, dict) for row in data):
        return None
    if not any(("CostumeType" in r or "CostumeObject" in r) for r in data):
        return None

    result = CostumeJsonResult(rows=len(data))
    for row in data:
        ctype = str(row.get("CostumeType", "")).strip().lower()
        if ctype in JSON_SLOT_ALIASES:
            slot = JSON_SLOT_ALIASES[ctype]
            if slot not in result.slots:
                result.slots.append(slot)
        obj = str(row.get("CostumeObject", "")).strip()
        native = _is_native_asset(obj)
        if obj:
            result.assets.append(obj)
            for slot in slots_from_asset_dir(obj) + slots_from_asset_path(obj):
                if slot not in result.slots:
                    result.slots.append(slot)
        icon = str(row.get("CostumeIcon", "")).strip()
        if icon and icon not in result.icons:
            result.icons.append(icon)
        name = str(row.get("CostumeName", "")).strip()
        if name:
            # `XXTB Slingkini[Chest]` → `XXTB Slingkini`
            clean = re.sub(r"\[[^\]]*\]", "", name).strip()
            if not clean:
                continue
            if native:
                # 指向游戏自带资源 → 这行描述的是**被替换的原衣服**本身，
                # 它的名字要归到 replaced 一侧，放进 look_names 会张冠李戴。
                if clean not in result.native_names:
                    result.native_names.append(clean)
            elif clean not in result.look_names:
                result.look_names.append(clean)
    result.slots.sort(key=lambda s: SLOT_ORDER.index(s) if s in SLOT_ORDER else 99)
    return result


# ---------------------------------------------------------------- 结论
CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"

CONF_LABELS = {CONF_HIGH: "确定", CONF_MEDIUM: "较可信", CONF_LOW: "推测"}


@dataclass
class CostumeInfo:
    """一次服装识别的结论。"""

    slots: Tuple[str, ...] = ()
    replaces: Tuple[str, ...] = ()
    look_name: str = ""
    source: str = ""
    confidence: str = CONF_LOW
    details: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.slots or self.replaces)

    def slots_text(self) -> str:
        if not self.slots:
            return ""
        return "、".join(SLOT_LABELS.get(s, s) for s in self.slots)

    def summary(self) -> str:
        """一行给卡片用的短文案。"""
        bits: List[str] = []
        if self.replaces:
            bits.append("替换 " + "、".join(self.replaces))
        if self.slots:
            bits.append(self.slots_text())
        if not bits:
            return ""
        return " · ".join(bits)


def _pick_look_name(
    segments: Sequence[str], pak_stems: Sequence[str], json_names: Sequence[str]
) -> str:
    """挑一个"新外观名"。JSON 最可靠，其次目录名，最后 pak 名。"""
    for name in json_names:
        if name and not is_meaningless(name):
            return name
    for seg in reversed(list(segments)):
        if seg and not is_meaningless(seg) and not is_category_name(seg):
            return seg
    for stem in pak_stems:
        if stem and not is_meaningless(stem):
            return stem
    return ""


def analyze(
    *,
    path_segments: Sequence[str] = (),
    pak_stems: Sequence[str] = (),
    text_blobs: Sequence[str] = (),
    costume_jsons: Sequence[Path] = (),
    extra_vocab: Sequence[str] = (),
) -> CostumeInfo:
    """综合四路证据给出服装识别结论。

    参数
    ----
    path_segments : 这个 mod 在原始位置上的路径片段（**从外到内**，含它自己）
    pak_stems     : 包内 pak 的主名（不含扩展名）
    text_blobs    : readme / 说明文本
    costume_jsons : 疑似服装配置 JSON 的路径
    extra_vocab   : 额外词表（一般传上一级目录名）
    """
    segments = [s for s in path_segments if s]
    stems = [s for s in pak_stems if s]
    details: List[str] = []

    # ---- ① 服装配置 JSON（最高优先级）----
    json_result: Optional[CostumeJsonResult] = None
    for jp in costume_jsons:
        parsed = parse_costume_json(jp)
        if parsed is not None:
            json_result = parsed
            details.append(f"读到服装配置 {jp.name}（{parsed.rows} 行）")
            break

    slots: List[str] = []
    confidence = CONF_LOW
    source = ""

    if json_result is not None:
        slots = list(json_result.slots)
        confidence = CONF_HIGH
        source = "服装配置 JSON"
        if json_result.look_names:
            details.append("外观名：" + "、".join(json_result.look_names[:3]))
        if json_result.assets:
            details.append("资源：" + json_result.assets[0])

    # ---- ② 目录名 / pak 名 / 文本 → 部位 ----
    if not slots:
        haystack_segments = [
            s for s in segments
            if not is_meaningless(s) and not is_category_name(s)
        ]
        for piece in list(haystack_segments) + list(stems):
            slots.extend(s for s in slots_from_text(piece) if s not in slots)
        if slots:
            confidence = CONF_MEDIUM
            source = "目录名/pak 名关键词"
            details.append("部位来自：" + "、".join(
                p for p in list(haystack_segments) + list(stems)
                if slots_from_text(p)
            )[:120])

    # ---- ③ 被替换的衣服 ----
    replaced: List[str] = []
    replaced_from_icon = False

    if json_result is not None:
        # ③a JSON 里直接指向游戏自带资源的那几行 —— 名字就是被替换的原衣服名。
        #     这是整条证据链里最硬的一环（作者自己写死了资源路径）。
        if json_result.native_names:
            for word in find_outfits("\n".join(json_result.native_names), extra_vocab):
                if word not in replaced:
                    replaced.append(word)
            if replaced:
                details.append("原生资源行：" + "、".join(json_result.native_names[:3]))
        # ③b 还没有 → 看 CostumeIcon 挂在哪个原套装槽位上（编号表见 ICON_SLOT_OUTFITS）
        if not replaced:
            for name in outfits_from_icons(json_result.icons):
                if name not in replaced:
                    replaced.append(name)
                    replaced_from_icon = True
            if replaced_from_icon:
                details.append("替换目标按图标槽位推断（非游戏内事实）")

    hay_all = "\n".join([*segments, *stems, *text_blobs])
    for word in find_outfits(hay_all, extra_vocab):
        if word not in replaced:
            replaced.append(word)

    if replaced and replaced_from_icon:
        # 「部位」来自配置 JSON（硬证据），但「替换目标」是从图标槽位推的，两段
        # 证据强度不同 —— 整体标"较可信"，别让用户以为整条都是游戏内事实。
        confidence = CONF_MEDIUM
        source = "服装配置 JSON（替换目标按图标槽位推断）"
    elif replaced and confidence != CONF_HIGH:
        # 命中词表是相当强的信号
        confidence = CONF_MEDIUM if confidence == CONF_LOW else confidence
        if not source:
            source = "名称/说明里的衣服名"
    if replaced and not replaced_from_icon:
        details.append("匹配到的衣服词：" + "、".join(replaced[:4]))

    # 来自说明文本的要单独标注（比目录名弱）
    if not replaced and text_blobs:
        for blob in text_blobs:
            for word in find_outfits(blob, extra_vocab):
                if word not in replaced:
                    replaced.append(word)
        if replaced:
            confidence = CONF_MEDIUM
            source = "说明文本"

    look = _pick_look_name(segments, stems, json_result.look_names if json_result else ())

    # 如果只认出"新外观名"，说明没有掌握被替换目标 —— 降级
    if not replaced and not json_result:
        confidence = CONF_LOW
        source = source or "仅按关键词推测"

    return CostumeInfo(
        slots=tuple(slots),
        replaces=tuple(replaced),
        look_name=look,
        source=source,
        confidence=confidence,
        details=tuple(details),
    )
