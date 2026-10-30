# -*- coding: utf-8 -*-
"""pak 类文件的识别、安装层级剥离与无关文件过滤（需求文档 3.2 / 2.4）。

需求文档 3.2 只写了「自动识别其中的 .pak / .ucas / .utoc」，
但**实测暴露出两件文档没写、不处理就会出错的事**：

1. **真实 mod 自带整串安装层级。**
   实测样本里直接出现：
     `明末mod\\Project_Plague\\Content\\Paks\\~mods\\xxx.pak`
     `...\\Project_Plague\\Content\\Paks\\LogicMods\\yyy.pak`
   若整包直接复制进游戏目录，会变成
     `<Paks>\\~mods\\Project_Plague\\Content\\Paks\\~mods\\xxx.pak`
   游戏根本不会加载（它只扫 `~mods` 这一层）。
   → 所以导入时必须**剥掉** mod 自带的 `Project_Plague/Content/Paks` 前缀。

2. **24% 的真实 mod 不满足命名规则**，且出现过 `BaiNude_Flat_Clean_p(under.pak`
   这种文件名里带括号的。→ 落盘名必须重新规范化，不能直接沿用原文件名。

另外要识别 `~mods` 与 `LogicMods` 的区别：两者是**不同的安装目标**
（前者是普通 pak，后者是蓝图/逻辑 mod），导入时要把这个信息记住。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

# ---------------------------------------------------------------- 文件分类
#: UE IoStore 相关的 pak 类文件。三者同基名时必须一起搬运，缺一不可。
PAK_EXTS = (".pak", ".ucas", ".utoc")
PAK_EXT_SET = frozenset(PAK_EXTS)

#: 图片后缀（用于第 1 档预览图提取）
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
IMAGE_EXT_SET = frozenset(IMAGE_EXTS)

#: 明确要忽略的文件：快捷方式 / 系统垃圾 / 文档
#: ⚠️ 注意 `.txt` / `.md` / `.nfo` **不在**这里 —— 它们是名称推导链第 3 级
#: （「readme 首行」）的数据源，必须被单独收集起来，不能当垃圾丢掉。
IGNORE_EXT_SET = frozenset(
    {".url", ".ini", ".lnk", ".html", ".htm", ".pdf", ".doc", ".docx", ".xls", ".xlsx"}
)

#: 可能承载「说明 / 标题」的文本后缀（供 scan_folder 收集 readme 候选）
README_EXT_SET = frozenset({".txt", ".md", ".nfo", ".rtf"})

#: 明确要忽略的目录名（大小写不敏感）
IGNORE_DIR_NAMES = frozenset(
    {
        "__macosx",
        ".git",
        ".svn",
        "$recycle.bin",
        "system volume information",
        # ---- 游戏平台/库目录（2026-10-11 加，为了防"扫描电脑上的文件夹"闪退）----
        # 真实事故：用户的扫描目录里存着 `E:\SteamLibrary`（整个 Steam 库根），
        # 扫描一路走进 `steamapps\common\` 下几十个游戏，把**黑神话悟空等游戏本体
        # 的 pak** 当成了 mod（实测 pakchunk0~15 全被收进来）。
        # 这些目录名在正常 mod 包里**绝不会出现**，所以直接忽略，零误伤。
        "steamapps",
        "steamvr",
        "common",
        "egstore",
        "epicgameslauncher",
        "wepicgames",
        "xboxgames",
        "games",
        "gamepass",
        "weGameApps",
        "library",
    }
)

#: 明确要忽略的文件名（大小写不敏感）
IGNORE_FILE_NAMES = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})

# ---------------------------------------------------------------- 层级剥离
#: mod 存放目录标记。顺序有意义：`~mods` 之外还有 `LogicMods`（安装目标不同）
MARKER_TILDE_MODS = "~mods"
MARKER_LOGIC_MODS = "LogicMods"

#: 安装目标子目录（Steam/Epic 用；Game Pass 不建子目录，见 gamedir.resolve_mods_dir）
TARGET_SUBDIR_DEFAULT = MARKER_TILDE_MODS
TARGET_SUBDIR_LOGIC = MARKER_LOGIC_MODS

#: 要剥掉的 UE 目录前缀（`Content/Paks` 之上可能还有工程名）
_UE_PREFIX_HEAD = ("project_plague", "sb")


@dataclass
class PakEntry:
    """一个待搬运的 pak 类文件。

    `content_parts` 是**剥掉安装层级之后**的路径片段，
    一般只剩一个文件名；若 mod 内部有子目录（少见），则保留子目录。
    """

    abs_path: Path
    rel_parts: tuple[str, ...]        # 原始相对路径（含层级），排障用
    content_parts: tuple[str, ...]    # 剥掉层级后的路径
    size: int

    @property
    def name(self) -> str:
        return self.content_parts[-1] if self.content_parts else self.abs_path.name

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1].lower()

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0]

    @property
    def original_rel(self) -> str:
        return "/".join(self.rel_parts)


@dataclass
class StripResult:
    """层级剥离的结果。"""

    kind: str                       # "mods" | "logicmods" | "plain"
    content_parts: tuple[str, ...]
    stripped: tuple[str, ...]       # 被剥掉的前缀片段（记日志，方便解释"为什么它装到这儿"）

    @property
    def target_subdir(self) -> str:
        if self.kind == "logicmods":
            return TARGET_SUBDIR_LOGIC
        return TARGET_SUBDIR_DEFAULT

    @property
    def had_install_layer(self) -> bool:
        return bool(self.stripped)


def strip_install_prefix(parts: Sequence[str]) -> StripResult:
    """剥掉 mod 自带的安装层级。

    规则（保守、可解释）：
      1. 从右往左找最后一个 `~mods` / `LogicMods` 标记 —— 它之后的才是内容。
         取「最后一个」而不是第一个，是为了处理 `~mods/LogicMods/x.pak` 这种
         双层写法（以更靠内的为准）。
      2. 没有 mod 标记时，找最后一个 `Paks` 目录 —— 它之后的才是内容。
      3. 都没有：原样返回（用户给的可能就是裸 pak，或一个普通文件夹）。

    注意：**不做任何"猜测性"剥离**。不认识的目录（例如用户自己的分类文件夹
    `我的mod/衣服/x.pak`）一概不剥，宁可保留、让上层去处理。
    """
    parts = tuple(str(p) for p in parts if str(p) not in ("", "."))

    last_marker = -1
    kind = "plain"
    for i, part in enumerate(parts[:-1]):        # 最后一段是文件名，不参与标记判断
        low = part.lower()
        if low == MARKER_TILDE_MODS.lower():
            last_marker = i
            kind = "mods"
        elif low == MARKER_LOGIC_MODS.lower():
            last_marker = i
            kind = "logicmods"
    if last_marker >= 0:
        return StripResult(kind, parts[last_marker + 1:], parts[: last_marker + 1])

    last_paks = -1
    for i, part in enumerate(parts[:-1]):
        if part.lower() == "paks":
            last_paks = i
    if last_paks >= 0:
        return StripResult("plain", parts[last_paks + 1:], parts[: last_paks + 1])

    return StripResult("plain", parts, ())


def is_ignored_dir(name: str) -> bool:
    return name.lower() in IGNORE_DIR_NAMES


def is_ignored_file(name: str) -> bool:
    low = name.lower()
    if low in IGNORE_FILE_NAMES:
        return True
    return os.path.splitext(low)[1] in IGNORE_EXT_SET


def is_pak_file(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in PAK_EXT_SET


def is_image_file(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_EXT_SET


# ---------------------------------------------------------------- 目录扫描
@dataclass
class FolderScan:
    """一次「目录扫描」的结果。"""

    paks: List[PakEntry]
    images: List[Path]
    readmes: List[Path]
    ignored_count: int = 0
    #: 是否在扫描中发现了安装层级（决定 install_subdir）
    strip: StripResult = None  # type: ignore[assignment]
    #: 扫描是否因**规模超限**而提前停下（见 SCAN_MAX_*）。上层必须如实告知用户，
    #: 绝不静默截断 —— 否则用户会以为整个目录都导进去了。
    truncated: bool = False

    @property
    def target_subdir(self) -> str:
        return self.strip.target_subdir if self.strip else TARGET_SUBDIR_DEFAULT


#: `scan_folder` 遍历一个目录时的**硬性规模上限**（2026-10-11 加，为了防闪退）。
#:
#: 踩过的坑（用户报的"点扫描电脑上的文件夹就闪退"）：用户的扫描目录列表里存着
#: `E:\SteamLibrary`（Steam 库根），`os.walk` 一路走进 `steamapps\common\` 下
#: **几十个游戏**里，累计几十万文件、上百 GB —— 走完要几分钟、吃掉几个 G 内存，
#: 界面直接"闪退"。所以给三层刹车：
#:   · 最多扫 `SCAN_MAX_FILES` 个文件；· 最多收 `SCAN_MAX_PAKS` 个 pak；
#:   · 最多递归 `SCAN_MAX_WALK_DEPTH` 层。
#: 触顶时置 `FolderScan.truncated=True`，由上层**如实告诉用户**"这个目录太大，
#: 只看了前一部分"，绝不静默截断。
SCAN_MAX_FILES = 50_000
SCAN_MAX_PAKS = 2_000
SCAN_MAX_WALK_DEPTH = 8


def scan_folder(root: str | os.PathLike[str]) -> FolderScan:
    """扫描一个已完成解压/就是普通目录的文件夹。

    返回**全部** pak 类文件、图片、readme。不做去重、不做筛选 ——
    那是上层导入器的事（这里只负责"看见什么"）。

    ⚠️ 规模超限（见上面三个 `SCAN_MAX_*`）会**提前停下**并置 `truncated=True`。
    """
    root = Path(root)
    paks: List[PakEntry] = []
    images: List[Path] = []
    readmes: List[Path] = []
    ignored = 0
    seen_kinds: List[str] = []
    seen_stripped: List[tuple[str, ...]] = []
    truncated = False
    seen_files = 0

    for dirpath, dirnames, filenames in os.walk(root):
        # ⚠️ 原地过滤 + **深度刹车**：避免走进 __MACOSX，也避免一路递归到
        # 磁盘深处（把 Steam 库当 mod 目录扫就是这么闪退的）。
        depth = 0 if Path(dirpath) == root else len(Path(dirpath).relative_to(root).parts)
        if depth >= SCAN_MAX_WALK_DEPTH:
            truncated = True
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not is_ignored_dir(d)]

        for filename in filenames:
            seen_files += 1
            if seen_files > SCAN_MAX_FILES or len(paks) >= SCAN_MAX_PAKS:
                truncated = True
                dirnames[:] = []
                break
            if is_ignored_file(filename):
                ignored += 1
                continue
            abs_path = Path(dirpath) / filename
            try:
                size = abs_path.stat().st_size
            except OSError:
                size = 0
            rel = abs_path.relative_to(root)

            if is_pak_file(filename):
                strip = strip_install_prefix(rel.parts)
                paks.append(
                    PakEntry(
                        abs_path=abs_path,
                        rel_parts=tuple(rel.parts),
                        content_parts=strip.content_parts,
                        size=size,
                    )
                )
                seen_kinds.append(strip.kind)
                if strip.stripped:
                    seen_stripped.append(strip.stripped)
            elif is_image_file(filename):
                images.append(abs_path)
            elif os.path.splitext(filename)[1].lower() in README_EXT_SET:
                readmes.append(abs_path)
            else:
                ignored += 1
        if truncated:
            break

    # 汇总出一个「整体层级结论」：以出现次数最多的 kind 为准，
    # LogicMods 优先（它更特殊，只要出现过就按它算，避免被普通 pak 稀释）
    kind = "plain"
    if "logicmods" in seen_kinds:
        kind = "logicmods"
    elif "mods" in seen_kinds:
        kind = "mods"
    stripped: tuple[str, ...] = ()
    if seen_stripped:
        stripped = max(seen_stripped, key=len)
    elif kind != "plain":
        stripped = ()

    scan = FolderScan(
        paks=paks,
        images=images,
        readmes=readmes,
        ignored_count=ignored,
        truncated=truncated,
    )
    scan.strip = StripResult(kind, (), stripped)
    return scan


# ---------------------------------------------------------------- 目标文件名
def scan_roots(roots: Sequence[str | os.PathLike[str]]) -> FolderScan:
    """扫**多个**目录，把结果合成一次扫描（用于"同系列合并成一个 mod"）。

    为什么需要它：一个 mod 的多个部件常放在并列的子目录里
    （`Project_Lewd 本体/` + `ProjectLewd_20251107_update_pack/`），
    而 `scan_folder()` 只认一个根。

    ⚠️ 合并语义要**如实**：
      · `truncated` 取「任一根被截断就是截断」，绝不因为合并而把警告吞掉。
      · `rel_path` **不**带"哪个根"的标记 —— 同名 pak 靠 `dedupe_names()` 改名，
        这与"用户把两个目录手动合并后拖进来"的既有行为一致。
      · 规模刹车**按根重置**（每个 `scan_folder` 各算各的）。合系列一般只有
        2~3 个目录，整体仍在可控范围；真超限会被 `truncated` 如实标出。
    """
    roots = [Path(r) for r in roots if r]
    if not roots:
        return FolderScan(paks=[], images=[], readmes=[])
    if len(roots) == 1:
        return scan_folder(roots[0])

    paks: List[PakEntry] = []
    images: List[Path] = []
    readmes: List[Path] = []
    ignored = 0
    truncated = False
    kinds: List[str] = []
    stripped_sets: List[tuple[str, ...]] = []

    for root in roots:
        scan = scan_folder(root)
        paks.extend(scan.paks)
        images.extend(scan.images)
        readmes.extend(scan.readmes)
        ignored += scan.ignored_count
        truncated = truncated or scan.truncated
        kinds.append(scan.strip.kind)
        if scan.strip.stripped:
            stripped_sets.append(scan.strip.stripped)

    kind = "plain"
    if "logicmods" in kinds:
        kind = "logicmods"
    elif "mods" in kinds:
        kind = "mods"
    stripped: tuple[str, ...] = ()
    if stripped_sets:
        stripped = max(stripped_sets, key=len)
    elif kind != "plain":
        stripped = ()

    scan = FolderScan(
        paks=paks,
        images=images,
        readmes=readmes,
        ignored_count=ignored,
        truncated=truncated,
    )
    scan.strip = StripResult(kind, (), stripped)
    return scan


def safe_pak_filename(original_name: str, fallback_stem: str) -> str:
    """把 pak 文件名规范化成可落盘的名字（需求文档 F-4.2 的落盘名部分）。

    ⚠️ 这里**只做文件系统层面的合法性处理**（去非法字符、去路径成分），
    **不改 `_P` 后缀** —— 后缀的规范化是安装那一步的事（gamedir.required_pak_name），
    导入阶段要保持原样，否则用户回头对不上自己下载的东西。
    """
    name = str(original_name).strip()
    name = os.path.basename(name.replace("\\", "/"))
    stem, ext = os.path.splitext(name)

    stem = "".join(ch for ch in stem if ch not in '<>:"/\\|?*' and ord(ch) >= 32)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    if not stem:
        stem = fallback_stem or "mod"

    ext = ext.lower()
    if ext not in PAK_EXT_SET:
        ext = ".pak"
    return f"{stem}{ext}"


def dedupe_names(names: Iterable[str]) -> List[str]:
    """同名文件加 -1 / -2 后缀，保证落盘不互相覆盖（Windows 大小写不敏感）。"""
    used: dict[str, int] = {}
    out: List[str] = []
    for raw in names:
        stem, ext = os.path.splitext(raw)
        key = raw.lower()
        if key not in used:
            used[key] = 0
            out.append(raw)
            continue
        used[key] += 1
        candidate = f"{stem}-{used[key]}{ext}"
        while candidate.lower() in used:
            used[key] += 1
            candidate = f"{stem}-{used[key]}{ext}"
        used[candidate.lower()] = 0
        out.append(candidate)
    return out
