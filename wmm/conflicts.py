# -*- coding: utf-8 -*-
"""元数据级冲突检测（需求文档 3.5 与 F-5.1 ~ F-5.6）。

**v1 只做元数据级**：靠 mod 的 `overrides` 声明与目标文件名算交集，
不解析 pak 内部（那要 `repak` / `retoc`，是 v2 的事）。

三条设计原则（都来自文档原文，别改）：

  1. **提示而非拦截**（F-5.5）。检测结果只在卡片上显示角标、在详情里列清单，
     **绝不阻止安装** —— 用户看过提示后有权强行同时启用。
     同一个资源被两个 mod 改，游戏不会崩，只是其中一个不生效而已。
  2. **精确到资源路径**（F-5.1）。提示文案必须长这样：
     「服装包甲 与 服装包乙 → SK_Wuchang_Body.uasset」，而不是「这两个 mod 冲突」。
  3. **分级按交集程度**（F-5.3）：
     🔴 硬冲突 = **路径完全相同**（两个 mod 改同一个资源，必有一个不生效）
     🟡 潜在   = **同目录同类型**（例如都在 `.../Character/...`，可能撞也可能不撞）
     🟢 无交集 = 什么都没撞上

关于「谁赢」（F-5.4）：UE 按 pak 主名里的编号决定加载优先级，**编号大的后加载、覆盖小的**。
实测本机用户的 `~mods` 里就躺着 `Project_Plague-Windows_10000_P.pak` 和
`Project_Plague-Windows_999999_9999999_P.pak` —— 就是在用编号抢优先级。
名字里没有编号时，退化为「后安装的赢」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import gamedir, installer, library as library_mod, modstore

# ---------------------------------------------------------------- 分级
LEVEL_HARD = "hard"
LEVEL_POTENTIAL = "potential"
LEVEL_NONE = "none"

LEVEL_EMOJI = {LEVEL_HARD: "🔴", LEVEL_POTENTIAL: "🟡", LEVEL_NONE: "🟢"}
LEVEL_LABELS = {LEVEL_HARD: "硬冲突", LEVEL_POTENTIAL: "潜在冲突", LEVEL_NONE: "无交集"}
#: 数值越大越严重，聚合多个冲突项时取最大
LEVEL_RANK = {LEVEL_NONE: 0, LEVEL_POTENTIAL: 1, LEVEL_HARD: 2}

KIND_TARGET = "target"        # 目标文件名撞了（F-4.6 / F-5.1）
KIND_RESOURCE = "resource"    # overrides 路径完全相同（🔴）
KIND_DIRECTORY = "directory"  # overrides 同目录（🟡）
KIND_VERSION = "version"      # 同一 mod 多版本并存（F-5.6）
KIND_FOREIGN = "foreign"      # 目标位置已有非本工具管的东西
#: 两个 mod 替换**同一件游戏内衣服**（2026-10-11 用户报：「这两个都是同一件衣服吧，
#: 怎么可以同时安装？」）。这是 pak 内部读不出来时唯一还能拿到的强证据 ——
#: `replaces` 由 costume.py 的四路证据（服装 JSON / CostumeIcon 槽位 /
#: 作者分类目录名 / pak 名 / 说明文本）推断出来。
KIND_REPLACES = "replaces"

KIND_LABELS = {
    KIND_TARGET: "目标文件名相同",
    KIND_RESOURCE: "改动同一个资源",
    KIND_DIRECTORY: "改动同一个目录",
    KIND_VERSION: "同一 mod 的多版本并存",
    KIND_FOREIGN: "目标位置已有文件（非本工具安装）",
    KIND_REPLACES: "替换同一件衣服",
}


# ---------------------------------------------------------------- 数据结构
@dataclass
class ConflictItem:
    """一对冲突。`resource` 是精确到资源路径/文件名的那个东西。"""

    a_id: str
    a_name: str
    b_id: str
    b_name: str
    level: str
    kind: str
    resource: str
    winner_id: str = ""
    winner_name: str = ""
    winner_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "a_id": self.a_id, "a_name": self.a_name,
            "b_id": self.b_id, "b_name": self.b_name,
            "level": self.level, "kind": self.kind,
            "resource": self.resource,
            "winner_id": self.winner_id, "winner_name": self.winner_name,
            "winner_reason": self.winner_reason,
        }

    @property
    def emoji(self) -> str:
        return LEVEL_EMOJI.get(self.level, "🟢")

    def line(self) -> str:
        """F-5.1 要求的那种一行话：`甲 与 乙 → 资源`。

        单边冲突（对方不是 mod，例如目标位置已有用户手工放的文件）没有 `b_name`，
        这时退化成更容易读的写法。

        ⚠️ 用「与」而不是 `⟷`：U+27F7 这类数学箭头在部分中文字体里**没有字形**，
        Qt 会画成空心方框（离屏渲染时实测到了）。「与」是常用汉字，任何环境都一定有。
        `→`（U+2192）没问题，全角度标点字体都带它。
        """
        if not self.b_name:
            head = self.a_name
        else:
            head = f"{self.a_name} 与 {self.b_name}"
        text = f"{self.emoji} {head} → {self.resource}"
        if self.winner_name:
            text += f"（{self.winner_name} 生效）"
        return text


@dataclass
class ModConflict:
    """某个 mod 的冲突汇总 —— 卡片角标和详情页读这个。"""

    mod_id: str
    mod_name: str
    level: str = LEVEL_NONE
    resources: List[str] = field(default_factory=list)
    against: List[str] = field(default_factory=list)
    items: List[ConflictItem] = field(default_factory=list)

    @property
    def emoji(self) -> str:
        return LEVEL_EMOJI.get(self.level, "🟢")

    @property
    def label(self) -> str:
        return LEVEL_LABELS.get(self.level, "")

    def lines(self) -> List[str]:
        return [i.line() for i in self.items]


@dataclass
class ConflictReport:
    """一次检测的完整结果。"""

    items: List[ConflictItem] = field(default_factory=list)
    by_mod: Dict[str, ModConflict] = field(default_factory=dict)
    #: 检测时实际参与比较的 mod（默认只有已安装的）
    considered: List[str] = field(default_factory=list)
    #: 没法检测的原因（例如一个 mod 都没装）—— 给 UI 显示，不要静默
    note: str = ""
    #: 是否使用了元数据（overrides）。false 表示只能按文件名判，精度有限（v1 常态）
    used_metadata: bool = False

    @property
    def hard_count(self) -> int:
        return sum(1 for i in self.items if i.level == LEVEL_HARD)

    @property
    def potential_count(self) -> int:
        return sum(1 for i in self.items if i.level == LEVEL_POTENTIAL)

    def level_of(self, mod_id: str) -> str:
        mc = self.by_mod.get(mod_id)
        return mc.level if mc else LEVEL_NONE

    def summary(self) -> str:
        if not self.considered:
            return self.note or "还没有已安装的 mod，暂时没有冲突可查。"
        if not self.items:
            return f"已检查 {len(self.considered)} 个已装 mod，没有发现冲突。"
        bits = []
        if self.hard_count:
            bits.append(f"🔴 硬冲突 {self.hard_count} 处")
        if self.potential_count:
            bits.append(f"🟡 潜在冲突 {self.potential_count} 处")
        return f"检查了 {len(self.considered)} 个已装 mod：" + "，".join(bits) + "。"

    def to_catalog_map(self) -> Dict[str, Tuple[str, List[str]]]:
        """转成 `catalog.build_views(conflicts=...)` 要的形状。

        注意：**必须给所有被检查过的 mod 都塞一条**（哪怕是 🟢），
        否则界面上「无冲突」和「没检查」会分不清。
        """
        out: Dict[str, Tuple[str, List[str]]] = {}
        for mod_id, mc in self.by_mod.items():
            out[mod_id] = (mc.level, list(mc.resources))
        return out

    def matrix(self) -> List[ConflictItem]:
        """F-5.2 冲突矩阵：两两列出哪几对相撞。

        这里就是所有「双方都是有名字的 mod」的条目，按严重程度排序。
        UI 侧按 (a_id, b_id) 两两铺成表格即可。
        """
        rows = [i for i in self.items if i.b_name]
        rows.sort(key=lambda i: (-LEVEL_RANK.get(i.level, 0), i.a_name, i.b_name, i.resource))
        return rows

    def matrix_cells(self) -> Dict[Tuple[str, str], str]:
        """矩阵单元格：`(a_id, b_id) -> level`（无序对，按 id 排序做键）。"""
        cells: Dict[Tuple[str, str], str] = {}
        for item in self.matrix():
            key = tuple(sorted((item.a_id, item.b_id)))  # type: ignore[assignment]
            if LEVEL_RANK.get(item.level, 0) > LEVEL_RANK.get(cells.get(key, LEVEL_NONE), 0):
                cells[key] = item.level
        return cells

    def lines(self, limit: int = 200) -> List[str]:
        return [i.line() for i in self.items[:limit]]


# ---------------------------------------------------------------- 资源路径解析
_KIND_EXT = r"(?:uasset|umap)"

_PATTERNS = (
    # /Game/Characters/Player/SK_Wuchang_Body.uasset
    re.compile(rf"/Game/[A-Za-z0-9_\-./]+?\.{_KIND_EXT}\b", re.I),
    # /Game/Characters/Player/SK_Wuchang_Body
    re.compile(r"/Game/[A-Za-z0-9_\-./]+"),
    # Project/Content/Characters/Player/SK_Body.uasset
    re.compile(rf"[A-Za-z0-9_\-./\\]*Project/Content/[A-Za-z0-9_\-./\\]+?\.{_KIND_EXT}\b", re.I),
    # 裸文件名：SK_Wuchang_Body.uasset
    re.compile(rf"[A-Za-z0-9_\-]+\.{_KIND_EXT}\b", re.I),
)

#: 要剥掉的前缀（不同来源的说法不一样，统一成 `/Game/...` 之后的相对路径）
_PREFIX_STRIP = (
    "/game/",
    "project/content/",
)


def normalize_resource(raw: str) -> str:
    """把各种写法的资源路径统一成一个可比较的规范形式。

    `Project/Content/Characters/SK_Body.uasset`、`/Game/Characters/SK_Body`、
    `Characters/SK_Body.uasset` → 全部归一成 `characters/sk_body`。
    大小写不敏感（Windows 的文件系统也不敏感）。
    """
    text = str(raw).strip().strip('`"\'')
    text = text.replace("\\", "/")
    low = text.lower()
    for prefix in _PREFIX_STRIP:
        idx = low.find(prefix)
        if idx >= 0:
            text = text[idx + len(prefix):]
            low = text.lower()
            break
    text = re.sub(rf"\.{_KIND_EXT}$", "", text, flags=re.I)
    text = re.sub(r"/+", "/", text).strip("/")
    return text.lower()


def parse_overrides_from_text(text: str) -> List[str]:
    """从用户粘贴的 mod 描述里抽出它改动的资源路径（3.5 的「粘贴描述自动解析」）。

    N网 的 mod 描述里常见这几种写法，都要认：
        /Game/Characters/Player/SK_Wuchang_Body.uasset
        Project/Content/Characters/Player/SK_Body.uasset
        SK_Wuchang_Body.uasset   ← 只有文件名
    返回**去重后**的规范化路径（用 `/` 分隔、无扩展名、小写）。
    万一一行都没匹配上，返回空列表 —— 由调用方决定是提示「没解析到」还是让用户手填。
    """
    if not text:
        return []
    raw_hits: List[str] = []
    for pattern in _PATTERNS:
        for match in pattern.findall(text):
            norm = normalize_resource(match)
            if norm:
                raw_hits.append(norm)

    # ⚠️ 上面几个正则**故意是重叠的**（`/Game/...` 那条会命中整条路径，
    # 「裸文件名」那条也会把同一行的尾巴再命中一次）。所以这里要合并：
    #   1. 长的优先（路径形态的信息更全）；
    #   2. 已经有了 `a/b/sk_body` 这种全路径，就把同名的裸 `sk_body` 丢掉 ——
    #      裸文件名有歧义，留着只会让冲突清单里冒出重复的两行。
    raw_hits.sort(key=len, reverse=True)
    found: List[str] = []
    seen: set[str] = set()
    claimed_basenames: set[str] = set()
    for norm in raw_hits:
        if norm in seen:
            continue
        basename = norm.rsplit("/", 1)[-1]
        if "/" not in norm and basename in claimed_basenames:
            continue   # 已被同名的全路径覆盖
        seen.add(norm)
        claimed_basenames.add(basename)
        found.append(norm)
    return found


def resource_dir(resource: str) -> str:
    """资源所在的目录（用于 F-5.3 的「同目录」判定）。"""
    norm = normalize_resource(resource)
    return norm.rsplit("/", 1)[0] if "/" in norm else ""


def display_resource(resource: str) -> str:
    """回显用：把 `sk_wuchang_body` 还原成 `SK_Wuchang_Body.uasset` 的样子。

    只做首字母与下划线分词的大写处理；原始大小写已经在规范化时丢了，
    但资源路径不区分大小写，这样显示足够用户定位。
    """
    norm = normalize_resource(resource)
    if not norm:
        return resource
    tail = norm.rsplit("/", 1)[-1]
    return f"{tail}.uasset"


# ---------------------------------------------------------------- 优先级（谁赢）
_NUM_RE = re.compile(r"(\d+)")


def priority_of(pak_name: str) -> int:
    """从 pak 文件名里读加载优先级（F-5.4）。

    UE 的约定是主名里的编号越大越后加载、越覆盖。实测用户就是这么用的：
        Project_Plague-Windows_10000_P.pak
        Project_Plague-Windows_999999_9999999_P.pak   ← 两个编号，取最大的
    没有编号 → 0（例如 `z257_P.pak` 是 257，`MyMod_P.pak` 是 0）。
    """
    stem = pak_name.rsplit(".", 1)[0]
    stem = re.sub(r"_[pP]$", "", stem)
    numbers = [int(n) for n in _NUM_RE.findall(stem)]
    return max(numbers) if numbers else 0


# ---------------------------------------------------------------- 多版本识别
_VERSION_RE = re.compile(r"(?<![A-Za-z0-9])[vV]?(\d+(?:\.\d+)*)(?![A-Za-z0-9])")


def split_name_version(name: str) -> Tuple[str, str]:
    """把 `无摔伤补丁 v1.2` 拆成 `("无摔伤补丁", "1.2")`（F-5.6）。

    找不到版本号时第二个元素为空串 —— 这时**不做**多版本判定，
    否则会把一堆名字里带数字的 mod（`pakchunk1200` 之类）误判成同族。
    """
    text = str(name or "").strip()
    versions = _VERSION_RE.findall(text)
    if not versions:
        return text, ""
    base = _VERSION_RE.sub(" ", text)
    base = re.sub(r"[_\-–—\s]+", " ", base).strip(" _-–—()（）[]【】")
    base = re.sub(r"\s+", " ", base)
    return base, ",".join(versions)


# ---------------------------------------------------------------- 主流程
def detect(
    store: modstore.ModStore,
    lib: Optional[library_mod.Library] = None,
    probe: Optional[gamedir.ProbeResult] = None,
    *,
    only_installed: bool = True,
    mobs_dir: Optional[Path] = None,
) -> ConflictReport:
    """跑一遍冲突检测。

    参数
    ----
    only_installed
        默认 True —— 只有**真的装在游戏目录里**的 mod 才会互相冲突（F-5.2 的口径：
        「装了 N 个 mod 时」）。设成 False 则拿整库比，用来做安装前预警。
    mobs_dir
        目标目录。给了就会额外检查「落点已有非本工具安装的文件」。
    """
    report = ConflictReport()
    metas = {m.id: m for m in store.all()}
    if not metas:
        report.note = "mod 库还是空的，先导入几个 mod 再看冲突。"
        return report

    # ---- 1. 挑出参与比较的 mod ----
    game_dir: Optional[Path] = None
    if lib is not None and probe is not None and probe.game_root:
        game_dir = Path(probe.game_root)
        installed_ids = lib.installed_ids(game_dir)
    else:
        installed_ids = set()

    if only_installed:
        picked = [metas[mid] for mid in installed_ids if mid in metas]
        if not picked:
            report.considered = []
            report.note = "还没有已安装的 mod —— 冲突是「装进游戏之后」才会发生的，先装几个再来看。"
            return report
    else:
        picked = list(metas.values())

    report.considered = [m.id for m in picked]

    # ---- 2. 目标文件名 → 落点撞车（F-4.6 / F-5.1）----
    target_owner: Dict[str, List[modstore.ModMeta]] = {}
    for meta in picked:
        platform = probe.platform if probe and probe.platform else gamedir.PLATFORM_STEAM
        for _f, target in installer.resolve_target_names(meta.name, meta.files, platform):
            target_owner.setdefault(target.lower(), []).append(meta)

    for target_low, owners in target_owner.items():
        if len(owners) < 2:
            continue
        # 多个 mod 抢同一个落点文件 —— 只能有一个物理文件存在，后装的赢
        display = target_low
        winner = _pick_winner(owners, lib, game_dir)
        for i, a in enumerate(owners):
            for b in owners[i + 1:]:
                report.items.append(
                    ConflictItem(
                        a_id=a.id, a_name=a.name, b_id=b.id, b_name=b.name,
                        level=LEVEL_HARD, kind=KIND_TARGET, resource=display,
                        winner_id=winner.id, winner_name=winner.name,
                        winner_reason="文件同名，两个 mod 只能存在一个；后安装的会覆盖先安装的",
                    )
                )

    # ---- 2.5 替换同一件游戏内衣服（KIND_REPLACES）----
    # 用户 2026-10-11 截图报：「这两个都是同一件衣服吧，怎么可以同时安装？」
    #   「全果 明军」 vs 「海洋女仆 大灯版 黑色 明军」—— 两个都 replaces=['明军']，
    #   但 pak 名不同、overrides 都是空的，上面两路都测不出来。
    # `replaces` 是推断出来的（medium 置信度），所以判为**潜在冲突（🟡）**
    # 而不是硬冲突：万一是推断错了，用户看到提示仍可强行同时启用（F-5.5）。
    replaces_owner: Dict[str, List[modstore.ModMeta]] = {}
    for meta in picked:
        for raw in (getattr(meta, "replaces", None) or []):
            key = normalize_resource(raw)
            if key:
                replaces_owner.setdefault(key, []).append(meta)

    for key, owners in replaces_owner.items():
        if len(owners) < 2:
            continue
        winner = _pick_winner(owners, lib, game_dir)
        for i, a in enumerate(owners):
            for b in owners[i + 1:]:
                report.items.append(
                    ConflictItem(
                        a_id=a.id, a_name=a.name, b_id=b.id, b_name=b.name,
                        level=LEVEL_POTENTIAL, kind=KIND_REPLACES,
                        resource=key,
                        winner_id=winner.id, winner_name=winner.name,
                        winner_reason=(
                            "两个 mod 都替换同一件衣服，游戏里只会生效一个"
                            "（识别依据是服装名推断，不是解析 pak 得到的）"
                        ),
                    )
                )

    # ---- 3. overrides 资源级比较（F-5.1 / F-5.3）----
    claimed: Dict[str, List[modstore.ModMeta]] = {}
    for meta in picked:
        for raw in (meta.overrides or []):
            norm = normalize_resource(raw)
            if norm:
                claimed.setdefault(norm, []).append(meta)

    report.used_metadata = bool(claimed)
    for norm, owners in claimed.items():
        if len(owners) < 2:
            continue
        winner = _pick_winner(owners, lib, game_dir)
        for i, a in enumerate(owners):
            for b in owners[i + 1:]:
                report.items.append(
                    ConflictItem(
                        a_id=a.id, a_name=a.name, b_id=b.id, b_name=b.name,
                        level=LEVEL_HARD, kind=KIND_RESOURCE,
                        resource=display_resource(norm),
                        winner_id=winner.id, winner_name=winner.name,
                        winner_reason=_winner_reason(winner, lib, game_dir),
                    )
                )

    # 同目录同类型 → 🟡 潜在（F-5.3）。只在「路径不完全相同」时补报，
    # 避免和上面的 🔴 重复刷屏。
    hard_pairs = {
        tuple(sorted((i.a_id, i.b_id))): set()
        for i in report.items if i.level == LEVEL_HARD
    }
    dir_claimed: Dict[str, List[Tuple[modstore.ModMeta, str]]] = {}
    for meta in picked:
        for raw in (meta.overrides or []):
            norm = normalize_resource(raw)
            directory = resource_dir(norm)
            if directory:
                dir_claimed.setdefault(directory, []).append((meta, norm))

    for directory, entries in dir_claimed.items():
        owners = {m.id: m for m, _r in entries}
        if len(owners) < 2:
            continue
        for i, (a, _ra) in enumerate(entries):
            for b, _rb in entries[i + 1:]:
                if a.id == b.id:
                    continue
                key = tuple(sorted((a.id, b.id)))
                if key in hard_pairs:
                    continue  # 已经报过 🔴 了
                report.items.append(
                    ConflictItem(
                        a_id=a.id, a_name=a.name, b_id=b.id, b_name=b.name,
                        level=LEVEL_POTENTIAL, kind=KIND_DIRECTORY,
                        resource=directory + "/",
                        winner_id="", winner_name="",
                        winner_reason="只改了同一个目录，是否真的同一个资源无法从元数据判断",
                    )
                )

    # ---- 4. 同一 mod 多版本并存（F-5.6）----
    families: Dict[str, List[modstore.ModMeta]] = {}
    for meta in picked:
        base, version = split_name_version(meta.name)
        if not version:
            continue
        families.setdefault(base.lower(), []).append(meta)
    for base, members in families.items():
        if len(members) < 2:
            continue
        # 版本号完全一样的不算「多版本并存」（那更像重复导入）
        versions = {split_name_version(m.name)[1] for m in members}
        if len(versions) < 2:
            continue
        winner = _pick_winner(members, lib, game_dir)
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                report.items.append(
                    ConflictItem(
                        a_id=a.id, a_name=a.name, b_id=b.id, b_name=b.name,
                        level=LEVEL_POTENTIAL, kind=KIND_VERSION, resource=base,
                        winner_id=winner.id, winner_name=winner.name,
                        winner_reason="同一 mod 的多个版本同时启用，通常只会生效一个",
                    )
                )

    # ---- 5. 落点已被非本工具安装的文件占用（F-4.6）----
    if mobs_dir is not None:
        on_disk = installer.scan_deployed(Path(mobs_dir))
        tracked = set()
        if lib is not None:
            for record in lib.all():
                for f in record.deployed_files:
                    if f.dst:
                        tracked.add(Path(f.dst).name.lower())
        for meta in picked:
            platform = probe.platform if probe and probe.platform else gamedir.PLATFORM_STEAM
            for _f, target in installer.resolve_target_names(meta.name, meta.files, platform):
                key = target.lower()
                if key in on_disk and key not in tracked and not installer.is_vanilla_pak(target):
                    report.items.append(
                        ConflictItem(
                            a_id=meta.id, a_name=meta.name, b_id="", b_name="",
                            level=LEVEL_POTENTIAL, kind=KIND_FOREIGN, resource=target,
                            winner_id="", winner_name="",
                            winner_reason="这个文件不是本工具装的，安装时会覆盖它（会自动备份）",
                        )
                    )

    # ---- 6. 汇总到每个 mod ----
    for meta in picked:
        report.by_mod.setdefault(
            meta.id, ModConflict(mod_id=meta.id, mod_name=meta.name)
        )

    for item in report.items:
        for mod_id, mod_name in ((item.a_id, item.a_name), (item.b_id, item.b_name)):
            if not mod_id:
                continue
            mc = report.by_mod.setdefault(
                mod_id, ModConflict(mod_id=mod_id, mod_name=mod_name)
            )
            mc.items.append(item)
            if LEVEL_RANK.get(item.level, 0) > LEVEL_RANK.get(mc.level, 0):
                mc.level = item.level
            if item.resource and item.resource not in mc.resources:
                mc.resources.append(item.resource)
            other = item.b_id if mod_id == item.a_id else item.a_id
            if other and other not in mc.against:
                mc.against.append(other)

    report.items.sort(key=lambda i: (-LEVEL_RANK.get(i.level, 0), i.kind, i.resource))
    return report


def _installed_priority(
    meta: modstore.ModMeta,
    lib: Optional[library_mod.Library],
    game_dir: Optional[Path],
) -> int:
    """这个 mod 在游戏目录里的实际加载优先级（F-5.4）。

    ⚠️ **必须从安装记录里的 `dst` 读，不能拿 mod 名现算。**
    踩过的坑：用户调优先级（F-4.8）传的是 `chunk_id`，这个信息**只落在部署后的
    文件名里**（`Project_Plague-Windows_99999_P.pak`），mod 名本身一个字都没变。
    照 mod 名现算的话两个 mod 都算出 0，分不出谁赢 —— 测出来的表现就是
    「明明给了大编号，判定却是小编号那个赢」。
    """
    best = 0
    if lib is not None and game_dir is not None:
        record = lib.find(meta.id, game_dir)
        if record is not None:
            for f in record.deployed_files:
                if f.dst:
                    best = max(best, priority_of(Path(f.dst).name))
    if best:
        return best
    # 还没装过：只能照默认命名规则估一个（通常都是 0）
    for _f, target in installer.resolve_target_names(
        meta.name, meta.files, gamedir.PLATFORM_STEAM
    ):
        best = max(best, priority_of(target))
    return best


def _pick_winner(
    members: Sequence[modstore.ModMeta],
    lib: Optional[library_mod.Library],
    game_dir: Optional[Path],
) -> modstore.ModMeta:
    """谁赢（F-5.4）：先比 pak 主名里的编号，编号相同再比安装时间（后装的赢）。"""
    if len(members) == 1:
        return members[0]

    def score(meta: modstore.ModMeta) -> Tuple[int, str]:
        installed_at = ""
        if lib is not None and game_dir is not None:
            record = lib.find(meta.id, game_dir)
            if record is not None:
                installed_at = record.installed_at or ""
        return _installed_priority(meta, lib, game_dir), installed_at

    return max(members, key=score)


def _winner_reason(
    winner: modstore.ModMeta,
    lib: Optional[library_mod.Library] = None,
    game_dir: Optional[Path] = None,
) -> str:
    best = _installed_priority(winner, lib, game_dir)
    if best:
        return f"「{winner.name}」的 pak 编号最大（{best}），后加载、覆盖其他"
    return "都没带编号，按安装时间算，后安装的赢"


# ---------------------------------------------------------------- 冲突分组上色
#: 冲突组的描边色（用户 2026-10-11 截图指定的第一组是黄色）。
#: 每组取一个颜色，**同一组的 mod 全部同色** —— 这是用户明确要的语义：
#: 「三个 mod 都替换同一个衣服，那这三个 mod 都变黄；另外三个 mod 又替换了
#:   另一件相同的衣服，那这三个 mod 就同时变蓝。也就是当多个 mod 冲突时，
#:   颜色变成同一个。」
#: ⚠️ 取值要**够深**：用户 2026-10-11 第一版用 2px 的中浅色，截图回原话是
#:   「黄边太窄太浅，一般人注意不到」。所以现在一律 4px 描边，且色值压深
#:   （黄从 #D9A400 压到 #C08800 一带），UI 侧还会把卡片文字区淡染一点。
GROUP_COLORS = (
    "#C88A00",   # 深黄/琥珀（第一组）
    "#2F6FC9",   # 蓝（第二组）
    "#B03024",   # 深红（第三组）
    "#6A41A8",   # 紫（第四组）
    "#0F8577",   # 青（第五组）
    "#C4508A",   # 粉（第六组）
    "#7A5A2E",   # 棕（第七组）
    "#39424A",   # 深灰（第八组）
)


def group_colors(report: "ConflictReport") -> Dict[str, str]:
    """把冲突的 mod 按**连通分量**分组，每组发一个颜色。

    为什么用连通分量而不是"按冲突对象"分组：真实的冲突关系是网状的 ——
    甲与乙撞、乙与丙撞，但甲与丙不一定直接有冲突条目。用户要的是
    「互相牵连的一批 mod 看起来是一伙的」，所以凡是能通过冲突链连到的
    都算同一组。

    ⚠️ **颜色必须稳定**：分组要按**固定顺序**遍历，不能依赖 `set` / `dict` 的
    迭代顺序（那取决于哈希随机化，重启后同一组可能换成别的颜色，用户会以为
    变了）。所以用 `report.items`（已按严重程度 + 资源名排好序）的顺序建图。

    没有冲突的 mod **不出现在返回值里** —— 卡片据此判断"要不要上色"。
    """
    #: mod_id -> 邻接集合（无序对，双向都加）
    adjacency: Dict[str, set] = {}
    #: 出现在冲突条目里的所有 mod（保持出现顺序 = report.items 的顺序，稳定）
    order: List[str] = []
    for item in report.items:
        a, b = item.a_id, item.b_id
        # ⚠️ 单边冲突（b 为空 = 落点已有别人的文件）**不参与分组**：
        #   它没有"同伴"，把它单独算一个组会给它一个专属颜色，
        #   用户看到就会以为"它跟谁是一伙的" —— 而实际上没有那一伙。
        #   这类冲突在详情弹窗里仍然照常列出，只是不进分组配色。
        if not a or not b:
            continue
        if a not in adjacency:
            adjacency[a] = set()
            order.append(a)
        if b not in adjacency:
            adjacency[b] = set()
            order.append(b)
        adjacency[a].add(b)
        adjacency[b].add(a)

    # ---- 并查集：按 order 顺序合并，保证同组永远拿到同一个颜色下标 ----
    parent: Dict[str, str] = {mid: mid for mid in order}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]     # 路径压缩
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx == ry:
            return
        # ⚠️ 固定把 order 靠后的挂到靠前的下面 —— 否则根是谁取决于遍历顺序。
        if order.index(rx) < order.index(ry):
            parent[ry] = rx
        else:
            parent[rx] = ry

    for item in report.items:
        if item.a_id and item.b_id:
            union(item.a_id, item.b_id)

    # ---- 每个连通分量按"第一次出现的顺序"取色 ----
    root_to_color: Dict[str, str] = {}
    out: Dict[str, str] = {}
    for mid in order:
        root = find(mid)
        if root not in root_to_color:
            root_to_color[root] = GROUP_COLORS[len(root_to_color) % len(GROUP_COLORS)]
        out[mid] = root_to_color[root]
    return out


# ---------------------------------------------------------------- 便捷入口
def detect_for_ui(
    store: modstore.ModStore,
    lib: Optional[library_mod.Library] = None,
    probe: Optional[gamedir.ProbeResult] = None,
    *,
    only_installed: bool = True,
) -> Tuple[ConflictReport, Dict[str, Tuple[str, List[str]]]]:
    """一步拿到「报告 + catalog 需要的映射」，界面层直接用这个。"""
    mobs_dir = None
    if probe is not None and probe.paks_dir:
        mobs_dir = gamedir.resolve_mods_dir(
            probe.platform or gamedir.PLATFORM_UNKNOWN, Path(probe.paks_dir)
        )
    report = detect(store, lib, probe, only_installed=only_installed, mobs_dir=mobs_dir)
    return report, report.to_catalog_map()


def conflicts_for_new_mod(
    meta: modstore.ModMeta,
    store: modstore.ModStore,
    lib: Optional[library_mod.Library] = None,
    probe: Optional[gamedir.ProbeResult] = None,
) -> List[ConflictItem]:
    """安装**之前**预警：这个 mod 会和已装的那些撞吗？

    F-5.5 明确「提示而非拦截」，所以这里只返回清单，不返回任何"能否安装"的结论。
    """
    report = detect(store, lib, probe, only_installed=True)
    hits: List[ConflictItem] = []
    for item in report.items:
        if meta.id in (item.a_id, item.b_id):
            hits.append(item)
    # 还要主动跟「还没装但会被这个 mod 影响」的情况比一次目标名
    platform = probe.platform if probe and probe.platform else gamedir.PLATFORM_STEAM
    new_targets = {
        t.lower() for _f, t in installer.resolve_target_names(meta.name, meta.files, platform)
    }
    # 同样要比一次「替换同一件衣服」——
    # ⚠️ 上面的 `detect()` 只在**已装**的 mod 之间两两比对（only_installed=True，
    # F-5.2 的口径），新 mod 自己不在 installed_ids 里，所以这里必须手动补一次，
    # 否则「装一个替换明军的新 mod」不会收到任何预警（实测漏报）。
    new_replaces = {
        normalize_resource(r) for r in (getattr(meta, "replaces", None) or [])
    }
    new_replaces.discard("")
    for other in store.all():
        if other.id == meta.id:
            continue
        if other.id not in report.considered:
            continue
        if new_replaces:
            shared = new_replaces & {
                normalize_resource(r)
                for r in (getattr(other, "replaces", None) or [])
            }
            for key in sorted(shared):
                hits.append(
                    ConflictItem(
                        a_id=meta.id, a_name=meta.name, b_id=other.id, b_name=other.name,
                        level=LEVEL_POTENTIAL, kind=KIND_REPLACES, resource=key,
                        winner_id="", winner_name="",
                        winner_reason=(
                            "两个 mod 都替换同一件衣服，装上去游戏里只会生效一个"
                            "（识别依据是服装名推断，不是解析 pak 得到的）"
                        ),
                    )
                )
        for _f, target in installer.resolve_target_names(other.name, other.files, platform):
            if target.lower() in new_targets:
                hits.append(
                    ConflictItem(
                        a_id=meta.id, a_name=meta.name, b_id=other.id, b_name=other.name,
                        level=LEVEL_HARD, kind=KIND_TARGET, resource=target,
                        winner_id="", winner_name="",
                        winner_reason="目标文件名相同，装上去会顶掉对方",
                    )
                )
                break
    return hits
