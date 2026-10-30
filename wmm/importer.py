# -*- coding: utf-8 -*-
"""mod 导入管线（需求文档 3.2 —— 核心交互）。

一次导入的完整链路：

    输入（拖拽的压缩包/文件夹/裸 pak）
      → 分类（按魔数判压缩类型，不看后缀）
      → 解压到临时暂存目录（含 Zip Slip 防护 / 体积上限）
      → 扫描出全部 .pak / .ucas / .utoc（并剥掉 mod 自带的安装层级）
      → 忽略 readme / .url / 说明.txt 等无关文件
      → 逐文件算 sha256，汇总出内容指纹（用于去重）
      → 推导名称（压缩包名 → 包内目录名 → readme 首行 → 兜底编号）
      → 挑预览图（挑不出来就降级字母卡）
      → 搬进 mod 库 + 写 meta.json
      → **无论如何都清理临时目录**

设计上的几个刻意的选择：

- **导入 = 建库，不是安装。** 这一步只把 mod 收进工具自己的库，
  **绝不碰游戏目录**。安装是下一步（阶段 3），只从库里往游戏目录拷。
  这样"导入了一堆但一个都没装"是完全正常的状态。
- **一个文件夹输入 = 一个 mod。** 依据需求文档 F-2.3（"指向一个含 mod 的目录"）
  与 F-2.4（"扫描目录"专门用来处理多 mod 的目录）的分工。
- **失败要能说人话。** 每种失败都带一条中文 `message` + 一条 `detail`，
  因为面向的是没有客服的公开分发场景。
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

from . import archive, costume, gamedir, modstore, nameresolve, pakfiles, preview
from .modstore import ModFile, ModMeta, ModStore

# 导入结果的 kind
KIND_IMPORTED = "imported"
KIND_DUPLICATE = "duplicate"
KIND_SKIPPED = "skipped"
KIND_FAILED = "failed"

#: 扫描目录时最多往下钻几层（防用户拖了个 C:\ 进来）
SCAN_MAX_DEPTH = 3
#: 扫描目录时最多返回多少条候选
SCAN_MAX_RESULTS = 500

#: **拒绝扫描**的目录规模上限（2026-10-11 加，为了防"扫描电脑上的文件夹"闪退）。
#:
#: 真实事故：用户把 `E:\SteamLibrary`（整个 Steam 库根）设成了扫描目录，
#: `scan_candidates` 把它下面每个含 pak 的目录都当成"一个 mod"，
#: 于是 `E:\SteamLibrary\steamapps`（装着几十个游戏！）成了候选 ——
#: 一旦导入就去递归处理上百 GB，必然卡死 / 闪退。
#: 所以这里做**前置拦截**：目录本身大得离谱就直接拒绝，并说明原因，
#: 比"扫到一半把内存吃光"强得多。真正的逐文件刹车在 `pakfiles.scan_folder`。
SCAN_SKIP_DIR_ENTRIES = 400

#: 找预览图时「往外看」**绝不接受**的层 —— 这些是杂物层，里面放什么都说明不了归属。
#: 真实事故（0.1.20）：用户的 mod 放在 `桌面\明末mod整合包\<mod名>\` 下，
#: 那些没自带图的 mod 一路往上看到了**桌面**，把桌面上随手放的一张无关 png
#: 当成了自己的预览图 —— 83 条里 49 条显示同一张与 mod 毫无关系的图。
_NEVER_OUTSIDE_LAYERS = frozenset({
    "desktop", "downloads", "documents", "download",
    "桌面", "下载", "文档", "我的文档",
})


@dataclass
class ImportOutcome:
    """一个输入物的导入结果。"""

    kind: str
    source: str
    message: str = ""
    detail: str = ""
    meta: Optional[ModMeta] = None
    existing: Optional[ModMeta] = None     # 重复时指向库里已有的那条
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kind == KIND_IMPORTED

    @property
    def display_name(self) -> str:
        if self.meta is not None:
            return self.meta.name
        return os.path.basename(self.source.rstrip("\\/")) or self.source


@dataclass
class ImportReport:
    """一次批量导入的总结果。"""

    outcomes: List[ImportOutcome] = field(default_factory=list)

    def add(self, outcome: ImportOutcome) -> None:
        self.outcomes.append(outcome)

    def _of(self, kind: str) -> List[ImportOutcome]:
        return [o for o in self.outcomes if o.kind == kind]

    @property
    def imported(self) -> List[ImportOutcome]:
        return self._of(KIND_IMPORTED)

    @property
    def duplicates(self) -> List[ImportOutcome]:
        return self._of(KIND_DUPLICATE)

    @property
    def skipped(self) -> List[ImportOutcome]:
        return self._of(KIND_SKIPPED)

    @property
    def failed(self) -> List[ImportOutcome]:
        return self._of(KIND_FAILED)

    @property
    def total_files(self) -> int:
        return sum(len(o.meta.files) for o in self.imported if o.meta)

    @property
    def total_bytes(self) -> int:
        return sum(o.meta.total_size for o in self.imported if o.meta)

    def summary(self) -> str:
        bits = []
        if self.imported:
            bits.append(f"成功导入 {len(self.imported)} 个")
        if self.duplicates:
            bits.append(f"重复 {len(self.duplicates)} 个")
        if self.skipped:
            bits.append(f"跳过 {len(self.skipped)} 个")
        if self.failed:
            bits.append(f"失败 {len(self.failed)} 个")
        return "，".join(bits) if bits else "没有可导入的内容"


#: 进度回调：progress(阶段名, 当前序号, 总数, 说明)
ProgressFn = Callable[[str, int, int, str], None]


def _emit(progress: Optional[ProgressFn], stage: str, cur: int, total: int, msg: str) -> None:
    if progress is None:
        return
    try:
        progress(stage, cur, total, msg)
    except Exception:  # noqa: BLE001 - 进度回调出问题不该让导入失败
        pass


# ---------------------------------------------------------------- 输入分类
@dataclass
class ImportUnit:
    """一个待导入的单元。"""

    kind: str          # archive | folder | pak
    path: Path
    label: str         # 用作名称推导第 1 级的原始名
    #: **同系列合并**时同一 mod 的其余目录（见 `_group_candidates`）。
    #: 空 = 这一个单元只含 `path`；非空 = `path` + 这些目录里的 pak 合起来算一个 mod。
    extra_paths: List[Path] = field(default_factory=list)
    #: 合进来的目录名（给卡片显示"同系列 N 个部件"用）
    part_labels: List[str] = field(default_factory=list)

    @property
    def roots(self) -> List[Path]:
        """本单元要扫描的全部根目录。"""
        return [self.path] + list(self.extra_paths)


def classify_input(path: str | os.PathLike[str]) -> Optional[ImportUnit]:
    """判断一个输入物该怎么处理。处理不了返回 None。"""
    p = Path(path)

    if p.is_dir():
        return ImportUnit("folder", p, p.name)

    if not p.is_file():
        return None

    if archive.looks_like_archive(p):
        return ImportUnit("archive", p, p.stem)

    if p.suffix.lower() in pakfiles.PAK_EXT_SET:
        return ImportUnit("pak", p, p.stem)

    return None


def guess_unsupported_reason(path: str | os.PathLike[str]) -> str:
    p = Path(path)
    if p.is_dir():
        return "文件夹里没找到任何 .pak / .ucas / .utoc 文件。"
    suffix = p.suffix or "（无后缀）"
    return (
        f"不支持的输入类型 {suffix}。"
        "可以导入：zip / 7z / rar 压缩包、含 pak 的文件夹、或直接的 .pak 文件。"
    )


# ---------------------------------------------------------------- 名称候选
def _common_top_dir(root: Path, file_rel_paths: Sequence[Sequence[str]]) -> str:
    """若所有文件都在同一个顶层目录下，返回那个目录名（作为"包内目录名"候选）。"""
    tops = {parts[0] for parts in file_rel_paths if len(parts) >= 2}
    if len(tops) == 1:
        top = next(iter(tops))
        # 顶层目录如果本身就是安装层级（Paks / ~mods / LogicMods / Content / 工程名），
        # 它不携带 mod 语义，不能拿来当名字。
        if top.lower() in {
            "paks", "~mods", "logicmods", "content", "project_plague", "sb",
            "binaries", "win64", "files", "mods",
        }:
            return ""
        return top
    return ""


def _readme_text(scan: pakfiles.FolderScan) -> str:
    """从 readme 候选里挑一个最像说明文件的，读出首行。"""
    if not scan.readmes:
        return ""

    def rank(p: Path) -> tuple[int, int]:
        low = p.name.lower()
        score = 0
        if "readme" in low or "read me" in low:
            score -= 3
        if "说明" in low or "安装" in low or "使用" in low:
            score -= 4
        if low.startswith("readme"):
            score -= 1
        # 体积适中优先（太大的是别的文档）
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        return (score, size)

    best = sorted(scan.readmes, key=rank)[0]
    return nameresolve.read_readme_first_line(best)


def _inner_dir_candidates(scan: pakfiles.FolderScan) -> List[str]:
    """从 pak 的**剥层后**路径里，由深到浅摘出有语义的目录名。

    ⚠️ 这是修掉一个真实缺陷后的写法。原来的做法是取「所有 pak 的共同顶层目录」，
    而真实 mod 的顶层目录恰好是安装层级（`Project_Plague` / `Content` / `~mods`），
    被黑名单一挡就**一个候选都不剩**，于是名称一路掉到兜底编号
    —— 实测把「极其丰满情趣mod」导成了「未命名 mod 001」。

    正确做法是**往下钻**：`WCL_Costumes/WCL-XXTB Slingkini/zWCL-XXTB Slingkini_P.pak`
    里，真正有语义的是 `WCL-XXTB Slingkini`（pak 的父目录）。
    越深越靠近 pak，语义越具体，所以按深度倒序返回。
    """
    out: List[str] = []
    for entry in scan.paks:
        parts = [p for p in entry.content_parts[:-1]]      # 去掉文件名本身
        for seg in reversed(parts):                        # 从靠近文件的往外走
            if seg and nameresolve.is_meaningful(seg) and seg not in out:
                out.append(seg)
    return out


def _dir_has_pak(directory: Path, cap: int = 400) -> bool:
    """这个目录（含子孙，抽样）里有没有 pak —— 用来判断它是不是"别人的 mod"。"""
    try:
        for i, p in enumerate(directory.rglob("*")):
            if i > cap:
                break
            if p.is_file() and pakfiles.is_pak_file(p.name):
                return True
    except OSError:
        pass
    return False


def _collect_preview_images(
    unit_root: Path, scan: pakfiles.FolderScan, *, allow_outside: bool
) -> List[Path]:
    """收集预览图候选：**先看单元内，再看单元外**。

    ⚠️ 这里补的是另一个真实缺陷：用户的 mod 常常是
        `极其丰满 情趣内衣mod/`
        ├── 53d45a…png        ← 预览图在**外层**
        └── 极其丰满情趣mod/   ← pak 在内层
    只扫 pak 所在的那一层，图就找不到了，卡片上只剩字母占位。

    但"往外看"必须加上四道闸，否则会捡到**别人的**图：

    1. **只对「用户直接拖进来的文件夹」生效**（`allow_outside`）。
       压缩包 / 单个 pak 的"单元根"是我们的临时暂存目录，它的上一层是系统
       `%TEMP%`，里面什么都有 —— 实测正是这一条让一个「故意不放预览图」的
       测试样例莫名其妙拿到了 tier=1。
    2. 不看盘符根 / 文件系统根（那种位置放什么都说明不了）。
    3. 子目录里的图，如果那个子目录**自己含 pak**，说明它属于另一个 mod，
       不算本单元的预览图。
    4. 🚨 **「合集目录」一刀切（0.1.20 修的真 bug）**：往上看的那一层如果
       **除了我们下来的那个子目录之外还装着别的 mod 子目录**，那它是"一堆 mod
       的容器"，不是本单元的专属包裹层 → **立刻停手**（这一层的散图也不捡，
       更不许再往上走）。
       实测踩过：用户的 `桌面/明末mod整合包/` 里装着 40+ 个 mod，
       每个没有自带图的 mod 都一路看到**桌面**，把桌面上随手放的一个无关 png
       当成了自己的预览图 —— 83 条里 49 条中了同一张。用户原话：
       「这些mod你是从哪弄的预览图，跟mod毫无关系啊」。
    5. 再补一条名字闸：**桌面 / 下载 / 文档**这类"杂物层"永远不看。
    """
    found: List[Path] = list(scan.images)

    def looks_like_preview_dir(name: str) -> bool:
        low = name.lower()
        return any(k in low for k in ("预览", "截图", "效果", "preview", "screenshot", "screen"))

    def is_other_mod_dir(d: Path) -> bool:
        """这一层里的某个子目录，是不是**别的 mod**？

        本单元的预览图夹（名字含「预览/截图/效果」**且自己不含 pak**）不算。
        其余一律按"别的 mod"处理 —— 宁可少捡一张图，也不要错认归属。
        ⚠️ `and` 的顺序别换：先判名字（免费），确认像预览夹了才去走目录（要 IO）。
        """
        return not (looks_like_preview_dir(d.name) and not _dir_has_pak(d))

    # 单元内：优先「预览图」这类子目录里的
    preferred = [p for p in found if looks_like_preview_dir(p.parent.name)]
    others = [p for p in found if p not in preferred]

    outside: List[Path] = []
    if allow_outside:
        child = unit_root
        parent = unit_root.parent
        for _ in range(2):                     # 最多往外看两层
            if parent == parent.parent:        # 盘符根 / 文件系统根
                break
            if parent.name.lower() in _NEVER_OUTSIDE_LAYERS:
                break                          # 桌面/下载/文档：放什么都说明不了
            try:
                entries = list(parent.iterdir())
            except OSError:
                break
            # 🚨 「合集目录」判定 —— 见上面第 4 条。命中就整段停手，
            #   连这一层的散图都不捡（合集夹里的图归属不明）。
            if any(e.is_dir() and e != child and is_other_mod_dir(e) for e in entries):
                break
            for entry in entries:
                if entry.is_dir() and looks_like_preview_dir(entry.name):
                    if _dir_has_pak(entry):    # 别人的 mod，不是本单元的图
                        continue
                    try:
                        outside.extend(
                            p for p in entry.rglob("*")
                            if p.is_file() and pakfiles.is_image_file(p.name)
                        )
                    except OSError:
                        pass
                elif entry.is_file() and pakfiles.is_image_file(entry.name):
                    outside.append(entry)
            child, parent = parent, parent.parent

    # 单元内的画面通常比外面随手放的更贴题，所以排在前面
    seen: set[str] = set()
    ordered: List[Path] = []
    for p in [*preferred, *others, *outside]:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            ordered.append(p)
    return ordered



def _collect_costume_jsons(unit_root: Path | Sequence[Path], scan: pakfiles.FolderScan) -> List[Path]:
    """找服装加载器的配置 JSON（pak 旁边、或单元根目录下的 .json）。

    `unit_root` 接受**多个**根 —— 同系列合并的单元（`ImportUnit.roots`）要把
    每个部件目录的 JSON 都算进来，否则合并后可能认不出"换的是哪件衣服"。
    """
    out: List[Path] = []
    roots = [unit_root] if isinstance(unit_root, Path) else list(unit_root)

    def add(p: Path) -> None:
        if p.is_file() and p.suffix.lower() == ".json" and p not in out:
            out.append(p)

    for entry in scan.paks:
        sibling = entry.abs_path.parent
        try:
            for child in sibling.iterdir():
                add(child)
        except OSError:
            pass
    for root in roots:
        try:
            for child in root.rglob("*.json"):
                # UE4SS 自带的配置目录不是服装配置，跳过
                if "ue4ss" in str(child).lower():
                    continue
                add(child)
        except OSError:
            pass
    return out[:40]        # 上限，避免拖进一个巨型目录时卡住


# ---------------------------------------------------------------- 单次导入
def _import_unit(
    unit: ImportUnit,
    store: ModStore,
    *,
    fallback_index: int,
    progress: Optional[ProgressFn] = None,
    cur: int = 1,
    total: int = 1,
) -> ImportOutcome:
    staging: Optional[Path] = None
    created_staging = False
    try:
        # ---- 1. 取得一个"普通目录"，后面流程对三种输入完全一致 ----
        if unit.kind == "pak":
            staging = archive.make_staging_dir("wmm-pak-")
            created_staging = True
            # 单个 pak 也走"复制进暂存再扫描"，避免为它单开一条分支
            shutil.copy2(unit.path, staging / unit.path.name)
            scan_root = staging
        elif unit.kind == "archive":
            _emit(progress, "解压", cur, total, f"正在解压 {unit.path.name} …")
            staging = archive.make_staging_dir()
            created_staging = True
            archive.extract_archive(unit.path, staging)
            scan_root = staging
        else:
            scan_root = unit.path

        # ---- 2. 扫描 ----
        # ⚠️ `scan_roots` 而非 `scan_folder`：同系列合并的单元（`extra_paths` 非空）
        #   要把多个目录一起扫。见 `_expand_folder` 与 `ImportUnit.roots`。
        _roots = unit.roots if unit.extra_paths else [scan_root]
        _emit(progress, "识别", cur, total, f"正在识别 {unit.label} 里的 mod 文件 …")
        scan = pakfiles.scan_roots(_roots)

        if not scan.paks:
            return ImportOutcome(
                KIND_SKIPPED,
                str(unit.path),
                message=f"「{unit.label}」里没找到 .pak / .ucas / .utoc 文件，已跳过。",
                detail="如果 mod 在压缩包子目录里，请确认压缩包完整；"
                       "也可以先手动解压，再把包含 pak 的那一层文件夹拖进来。",
            )

        # ---- 3. 决定落盘文件名（保留原始名，只做文件系统合法性处理）----
        desired = [pakfiles.safe_pak_filename(e.name, fallback_stem=unit.label) for e in scan.paks]
        final_names = pakfiles.dedupe_names(desired)

        # ---- 4. 逐文件算哈希 ----
        _emit(progress, "校验", cur, total, f"正在计算 {len(scan.paks)} 个文件的指纹 …")
        mod_files: List[ModFile] = []
        staged_pairs: List[Tuple[Path, str]] = []
        for entry, target_name in zip(scan.paks, final_names):
            sha = modstore.hash_file(entry.abs_path)
            mod_files.append(
                ModFile(
                    name=target_name,
                    rel_path="/".join(entry.content_parts),
                    original_rel=entry.original_rel,
                    size=entry.size,
                    sha256=sha,
                )
            )
            staged_pairs.append((entry.abs_path, target_name))

        content_hash = modstore.compute_content_hash(mod_files)

        # ---- 5. 去重 ----
        existing = store.find_by_hash(content_hash)
        if existing is not None:
            return ImportOutcome(
                KIND_DUPLICATE,
                str(unit.path),
                message=f"「{unit.label}」已经导入过了（库里叫「{existing.name}」），已跳过。",
                detail=f"内容指纹一致：{content_hash[:16]}…",
                existing=existing,
            )

        # ---- 6. 名称推导 ----
        # 顺序 = 可信度从高到低：用户拖进来的名字 → 包内目录 → 包内文件名 → readme → 兜底
        # ⚠️ 同系列合并时，`unit.label` 是**第一个部件目录**的名字，可能带
        #   「本体 / update_pack」这类后缀。用户看到的是"Project Lewd 整合包"
        #   而不是"Project_Lewd 本体"，所以先把部件目录名依次作为候选，
        #   让 `nameresolve` 挑到最像"系列名"的那个。
        name_inputs: List[Tuple[str, str]] = [(unit.label, {
            "archive": "压缩包名", "folder": "文件夹名", "pak": "文件名",
        }.get(unit.kind, "名称"))]
        for part in unit.part_labels[1:]:
            name_inputs.append((part, "同系列部件目录名"))
        for seg in _inner_dir_candidates(scan):
            name_inputs.append((seg, "包内目录名"))
        # readme 排在 pak 文件名**之前**：readme 首行是作者写的话，
        # 而 pak 文件名常常是 `TestPak` / `zWCL-XXTB Slingkini_P` 这种半成品。
        readme_text = _readme_text(scan)
        if readme_text:
            name_inputs.append((readme_text, "readme 首行"))
        pak_stems = [Path(e.name).stem for e in scan.paks]
        for stem in pak_stems:
            name_inputs.append((stem, "包内文件名"))
        resolution = nameresolve.resolve_name(
            name_inputs,
            fallback_index=fallback_index,
            extra_readme_text=readme_text,
        )

        # ---- 7. 预览图 ----
        pick = preview.pick_preview_image(
            _collect_preview_images(scan_root, scan, allow_outside=unit.kind == "folder")
        )

        # ---- 7.5 服装识别（换的是哪套衣服 / 动了哪些部位）----
        # 证据链：原始路径片段 + pak 主名 + 说明文本 + 服装配置 JSON。
        # `source_hint` 取原始路径的最后几段（安装层级对识别没意义，但保留也无害，
        # 因为 costume 内部会把 Project_Plague / ~mods 这类词当噪音剔掉）。
        hint_segments: List[str] = []
        if unit.kind == "folder":
            for seg in list(unit.path.parts)[-4:]:
                if seg and seg not in hint_segments and seg not in ("", unit.path.anchor):
                    hint_segments.append(seg)
            # 把 pak 自己的父目录也补进去（用户可能直接拖了含 Project_Plague 的那层）
            for seg in _inner_dir_candidates(scan)[:2]:
                if seg not in hint_segments:
                    hint_segments.append(seg)
        info = costume.analyze(
            path_segments=hint_segments,
            pak_stems=pak_stems,
            text_blobs=[readme_text] if readme_text else [],
            costume_jsons=_collect_costume_jsons(_roots, scan),
        )

        # ---- 8. 落库 ----
        _emit(progress, "入库", cur, total, f"正在把「{resolution.name}」收进 mod 库 …")
        meta = ModMeta(
            id=modstore.make_mod_id(content_hash),
            name=resolution.name,
            name_source=resolution.source,
            source_archive=unit.path.name,
            content_hash=content_hash,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            install_subdir=scan.target_subdir,
            preview_tier=preview.TIER_EXTRACTED if pick.ok else preview.TIER_LETTER_CARD,
            files=mod_files,
            # 服装识别结果
            replaces=list(info.replaces),
            slots=list(info.slots),
            look_name=info.look_name,
            costume_source=info.source,
            costume_confidence=info.confidence,
            source_hint=hint_segments,
            series_parts=list(unit.part_labels),
        )

        warnings: List[str] = []
        if len(unit.part_labels) >= 2:
            # 用户明确要"识别出来后加个标识"（2026-10-11）——
            # 这里既给卡片存一份，也当场告诉他合了什么。
            others = "、".join(unit.part_labels[1:])
            warnings.append(
                f"识别到同系列 mod，已把 {len(unit.part_labels)} 个部件合并成一个："
                f"{unit.part_labels[0]} ＋ {others}。"
            )
        if scan.strip.had_install_layer:
            prefix = "/".join(scan.strip.stripped)
            warnings.append(
                f"这个 mod 自带了安装目录层级「{prefix}」，已自动剥掉 —— "
                "不剥的话复制进游戏会变成嵌套目录，游戏不会加载。"
            )
            meta.import_notes.append(f"剥离安装层级：{prefix}")
        if scan.strip.kind == "logicmods":
            warnings.append("这个 mod 属于 LogicMods 类型，安装目标目录与普通 pak 不同。")
        if resolution.is_fallback:
            warnings.append(
                "没能从压缩包名/目录名/readme 里推导出名字，已用编号占位，"
                "可以在卡片上双击改名。"
            )
        if not pick.ok:
            warnings.append(f"压缩包里没找到可用截图，已用图标占位（{pick.reason}）。")
        bad_names = [f.original_rel for f in mod_files if f.rel_path != f.original_rel]
        if bad_names:
            meta.import_notes.append(f"规范化了 {len(bad_names)} 个文件的落盘名")

        store.commit(meta, staged_pairs, preview_src=pick.path if pick.ok else None)

        return ImportOutcome(
            KIND_IMPORTED,
            str(unit.path),
            message=f"已导入「{resolution.name}」（{len(mod_files)} 个文件）",
            detail=f"名称来自：{resolution.source}；内容指纹 {content_hash[:16]}…",
            meta=store.get(meta.id) or meta,
            warnings=warnings,
        )

    except archive.UnsafeArchiveError as exc:
        return ImportOutcome(
            KIND_FAILED,
            str(unit.path),
            message=f"「{unit.label}」被拒绝解压：压缩包内含可疑路径，可能是恶意构造的包。",
            detail=str(exc),
        )
    except archive.ArchiveError as exc:
        return ImportOutcome(
            KIND_FAILED,
            str(unit.path),
            message=f"「{unit.label}」解压失败。",
            detail=str(exc),
        )
    except PermissionError as exc:
        return ImportOutcome(
            KIND_FAILED,
            str(unit.path),
            message="没有权限读取这个文件或写入 mod 库目录。",
            detail=f"{exc}\n可尝试：把文件复制到桌面再导入；或检查杀毒软件是否锁住了 "
                   f"{store.root}。",
        )
    except Exception as exc:  # noqa: BLE001 - 导入单条失败不该中断整批
        return ImportOutcome(
            KIND_FAILED,
            str(unit.path),
            message=f"「{unit.label}」导入时出现未预期的错误。",
            detail=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if created_staging:
            archive.cleanup_staging(staging)


# ------------------------------------------------------- 同源归并（一个 mod 的多个部件）
# 作者经常把一个 mod 拆成好几个子文件夹分发，每个子目录里各放一个 pak。
# 真实例（2026-10-10 用户报「这是一个 mod 中的多个部位，你给识别成多个 mod 了」）：
#
#     黑红短裙-1.24/
#       ├─ 主文件  衣服+胖腿/TK_SexySkirt_BlackRed_Main_p.pak
#       ├─ 脸部纹身      /TK_SexySkirt_MakeUp_p.pak
#       ├─ 腿部纹身      /TK_SexySkirt_Z_BodyPrint_p.pak
#       └─ 裤子  瘦腿效果/TK_SexySkirt_BlackRed_ThinLeg_p.pak
#
# 而真正的「合集」目录长得**一模一样**（比如作者自己的
# `以往绅士mod合集/NSFW合集/1.衣服 套装、散件/`，每个子目录一件衣服）。
# 结构上分不出来 —— 唯一的区别在 **pak 文件名是否同源**：
# 上例 4 个 pak 都叫 `TK_SexySkirt_*`，合集里则件件不同
# （`pakchunk1400-Windows_P`、`strappy heel black_900_p`、`cloth07_p`…）。
#
# ⚠️ 判据刻意保守：只取主名**前 3 段里长度 ≥5**的词当"作者标识"，
# 且要求候选之间真的交出非空集合。宁可漏合（用户还能右键）也不能误合 ——
# 把两件无关的衣服并成一个 mod，用户会更难办。
_PAK_TOKEN_STOP = {
    "pak", "paks", "pakchunk", "windows", "win64", "win32",
    "wuchang", "content", "mods", "mod", "default", "base",
}


def _pak_signature(stem: str) -> Set[str]:
    """从一个 pak 主名里抽出「作者 / 项目标识」—— 前 3 段中长度 ≥5 的词。

    去掉尾部数字后再判停用词，是为了让 `pakchunk1400` → `pakchunk`（噪声）能被
    正常过滤掉，而不是因为多了个数字就漏进"同源证据"里。
    """
    s = stem.lower()
    if s.endswith(".pak"):
        s = s[:-4]
    tokens = [t for t in re.split(r"[^0-9a-z\u4e00-\u9fff]+", s) if t]
    # 去掉尾部噪声：_P / _p / _u / 纯数字版本号
    while tokens and (tokens[-1] in {"p", "u", "pak"} or tokens[-1].isdigit()):
        tokens.pop()

    out: Set[str] = set()
    for token in tokens[:3]:
        core = token.rstrip("0123456789") or token
        if len(core) >= 5 and core not in _PAK_TOKEN_STOP:
            out.add(core)
    return out


def _candidate_signature(cand: "ScanCandidate") -> Set[str]:
    """一个候选目录里所有 pak 的标识并集。"""
    try:
        scan = pakfiles.scan_folder(cand.path)
    except OSError:
        return set()
    sig: Set[str] = set()
    for entry in scan.paks:
        sig |= _pak_signature(entry.stem)
    return sig


def _all_from_one_mod(cands: Sequence["ScanCandidate"]) -> bool:
    """这些候选是不是**同一个 mod 拆出来的部件**？

    任意两个候选的 pak 标识有交集即视为「同源」；若所有候选由此连成一整块
    （连通），就认为它们属于同一个 mod，不该拆开。
    """
    groups = _group_candidates(cands)
    return len(groups) == 1 and len(cands) >= 2


#: 目录名里表示"这是同一项目的第几部分"的词。见到它们就把该词从名字里剔掉，
#: 这样 `Project_Lewd 本体` 与 `ProjectLewd_20251107_update_pack` 能归一化成同一个键。
_SERIES_PART_WORDS = {
    "本体", "主包", "主体", "本体包", "完整版", "更新", "更新包", "补丁", "修正",
    "补充", "附加", "额外", "附加包", "扩展", "part", "parts", "update", "updates",
    "patch", "patch1", "patch2", "fix", "fixed", "full", "base", "core",
    "main", "extra", "add", "addon", "dlc", "bundle", "pack", "hotfix", "v2",
}


def _series_key(name: str) -> str:
    """从**目录名**里抽"项目标识"。

    为什么必须有它（pak 名不够用）：真实案例 ——
    `绅士内容mod/Project_Lewd 本体/`（46 个 pak）里 pak 叫 `BiXi_FuXi_9_P.pak`、
    `ENV_B_Any_shidui03_Mask_Medres_9_P.pak`，**全是游戏资源名，一个作者标识都没有**；
    同级的 `ProjectLewd_20251107_update_pack/` 也是同理。两边 pak 标识交集为空，
    于是被判成两个无关 mod。**是目录名 `Project_Lewd` / `ProjectLewd` 出卖了它们一伙。**

    做法：去掉分隔符 → 逐词剔除版本号与 `_SERIES_PART_WORDS` 里的部件词 →
    剩下的拼成 key。所以上面两个分别得到 `projectlewd` 与 `projectlewd`。

    额外做一次**包含关系**判定（`key.startswith(other)`），覆盖
    `ProjectLewd` 与 `Project_Lewd_Extended` 这种作者只在一处写全名的情形。
    """
    s = name.lower()
    tokens = [t for t in re.split(r"[^0-9a-z\u4e00-\u9fff]+", s) if t]
    kept = [t for t in tokens if not t.isdigit() and t not in _SERIES_PART_WORDS]
    if not kept:                      # 全是版本号/部件词 → 没什么可当标识
        kept = [t for t in tokens if not t.isdigit()]
    return "".join(kept)


#: 部件目录里出现这些词，说明它描述的是"哪个部位"而不是 mod 的名字。
#: 出现它们就不能拿部件名当 mod 名 —— 真实反例：`黑红短裙/主文件 衣服+胖腿/`，
#: 拿"主文件 衣服 胖腿"当 mod 名毫无意义。
_SERIES_PART_DESC = {
    "主文件", "主体", "主包", "本体", "额外", "附加", "补充", "脸部", "身体",
    "腿部", "脸部纹身", "腿部纹身", "瘦腿", "瘦腿效果", "裤子", "衣服", "胖腿",
    "上衣", "下装", "鞋子", "头发", "饰品", "妆容", "特效", "音效", "脚本",
}


def _series_display_name(part_labels: Sequence[str]) -> str:
    """给"合并出来的 mod"起一个**系列名**。

    真实案例（用户 2026-10-11）：
        part_labels = ["Project_Lewd 本体", "ProjectLewd_20251107_update_pack"]
        →  系列名 "Project Lewd"（去掉「本体」「update pack」等部件后缀，
            把 `Project_Lewd` / `ProjectLewd` 的写法差异抹平）

    为什么不能直接用父目录名：那个目录叫「绅士内容mod 尺度大 内容丰富 11.7更新」，
    是作者写给**人类看的宣传语**（还带日期），当 mod 名毫无意义。
    为什么不能直接用部件名：`Project_Lewd 本体` 里的「本体」是内部结构；
    `主文件 衣服+胖腿` 里的「主文件 / 衣服 / 胖腿」是部位说明，同样不能用。

    做法：取第一个部件名，去掉 `_SERIES_PART_WORDS`（本体 / update / pack…）里的词
    与版本号；再去掉 `_SERIES_PART_DESC`（主文件 / 脸部 / 腿部…）这类部位说明词。
    剩下的就是系列名。多个部件的归一化 key 一致（这正是 `_group_candidates`
    合它们的依据），所以只看第一个就够了。

    ⚠️ 拿不准就返回空串 —— 调用方会退回原来的 label，**绝不硬编一个名字**。
    """
    if not part_labels:
        return ""
    head = part_labels[0]
    # ⚠️ **不要 lower()** —— 归一化 key 时要小写（`Project_Lewd` ≡ `ProjectLewd`），
    #   但显示名要保留作者原来的大小写（`Project Lewd`，不是 `project lewd`）。
    raw = [
        t for t in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", head)
        if t and not t.isdigit()
    ]
    kept = [t for t in raw if t.lower() not in _SERIES_PART_WORDS]
    kept = [t for t in kept if t not in _SERIES_PART_DESC]
    if not kept:
        return ""
    name = " ".join(kept).strip()
    # 剔完还剩全是中文说明词 → 作者没起真名，放弃
    if not name or name.lower() in _SERIES_PART_WORDS or name in _SERIES_PART_DESC:
        return ""
    return name


def _is_part_desc(name: str) -> bool:
    """目录名是不是「部位 / 组件说明」而不是一个真正的 mod 名。

    真实案例（用户 2026-10-10 要求合并的「黑红短裙」）：
        `主文件 衣服+胖腿` / `脸部纹身` / `腿部纹身` / `裤子 瘦腿效果`
    四个目录名全是部位词 —— 它们描述的是**同一件衣服的组成部分**。

    而用户 2026-10-12 要求拆开的「女巫」四件套：
        `白丝连裤袜` / `短款白丝` / `短款黑丝` / `黑丝连裤袜`
    是**款式名**，一个部位词都没有 —— 四套平等的不同衣服。

    这两类在 pak 名层面结构完全相同（公共主名 + 两两交集 2 个词），
    只有这条语义判据能把它们分开。判据 = 分词后**有词命中 `_SERIES_PART_DESC`**。
    """
    tokens = [t for t in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", name) if t]
    return any(t in _SERIES_PART_DESC for t in tokens)


def _group_candidates(cands: Sequence["ScanCandidate"]) -> List[List["ScanCandidate"]]:
    """把候选按「同属一个 mod」分组（连通分量）。

    ⚠️ 为什么是**分组**而不是原来的"全连通才合并成一个"：
    真实案例 `绅士内容mod 尺度大 内容丰富 11.7更新/` 里有 4 个子目录 ——
        Project_Lewd 本体（46 pak）、ProjectLewd_20251107_update_pack（11 pak）
        消除棒子（1 pak）、消除重复的眼部纹理（1 pak）
    前两个该合，后两个是独立小mod。原来的"全连通"判据要求 4 个**全部**两两同源，
    只要有一个不合就整个父目录全拆开 —— 于是 4 个全成了独立 mod，用户看得莫名其妙。

    现在改成：**先分成若干组，每组算一个 mod**。合得该合、分得该分。

    三条同源证据（**或**关系，任意一条成立就算同源）：
      1. **目录名归一化后相同 / 互为前缀** —— 最强信号，见 `_series_key`。
         （`Project_Lewd 本体` ≡ `ProjectLewd_20251107_update_pack`）
      2. **pak 主名标识高度重合** —— Jaccard ≥ 0.5 **且交集 ≥ 3 个词**。
         保留给"目录名毫无信息量（比如全叫 `files`）但 pak 名同源"的情况。
         ⚠️ 绝对交集下限是为「女巫 白丝连裤袜/短款白丝/短款黑丝/黑丝连裤袜」
         这种**同一角色的多套不同衣服**补的：它们 pak 名都含 `witch`，sig 只有
         3 个词、两两交集 2 个就恰好 Jaccard 0.5，没有下限就会四套合成一个 mod。
      3. **部位拆分结构** —— 目录名全是部位/组件说明词（`脸部纹身`/`腿部纹身`/
         `裤子 瘦腿效果`）且 pak 标识有共同主名。这是「同一件衣服的主文件 + 各
         部位」，用户 2026-10-10 明确要求合起来的那种（「黑红短裙」四部件）。
         ⚠️ 它和女巫四件套在 pak 名层面**结构完全相同**，只有目录名语义能区分，
         所以必须同时校验「pak 有共同主名」，否则 `上衣/` + `裤子/` 两个
         不同 mod 光凭部位词就会被误合。

    ⚠️ 仍然是**保守**判据：单元素组就是它自己；分组只用来决定"合还是不合"，
    绝不改变组内的文件集合 —— 合并后仍然是"这些目录里的 pak 一起进库"。
    """
    n = len(cands)
    if n <= 1:
        return [list(cands)]

    keys = [_series_key(c.label) for c in cands]
    sigs = [_candidate_signature(c) for c in cands]

    # 并查集
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union_find(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            # ⚠️ 固定让**小下标**当根 —— 合并顺序不能依赖集合迭代顺序，
            #   否则同样的目录每次跑出来的分组顺序可能不同（哈希随机化）。
                if ri < rj:
                    parent[rj] = ri
                else:
                    parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            same = False
            # 证据 1：**目录名**归一化后相同 / 互为前缀 —— 最强信号，见 `_series_key`。
            ki, kj = keys[i], keys[j]
            if (
                ki and kj and len(ki) >= 3 and len(kj) >= 3
                and (ki == kj or ki.startswith(kj) or kj.startswith(ki))
            ):
                same = True
            # 证据 2：pak 主名标识 —— 必须**高度重合**，不能只看有交集。
            #   ⚠️ 为什么不能用原来的「有交集即同源」：pak 主名多半是 UE 资产名
            #   （`WPN_zaozi_reverse_99_P`、`ENV_B_Any_shijiamoni03_Mask_Medres_9_P`），
            #   **不同 mod 会大量共用同一批游戏资产名**。真实反例：
            #   `消除棒子/WPN_zaozi_reverse_99_P.pak` 与 `Project_Lewd 本体/` 里的
            #   `WPN_zaozi_reverse_99_.pak` 名字只差一个下划线，交集 2 个词，
            #   但前者是作者**故意单独拆出来**的小工具包（内容与本体那份字节相同），
            #   按交集判会被错误合进整合包 → 用户看到「3 个部件」而实际只想合 2 个。
            #   改成 Jaccard ≥ 0.5 后：本体(22 词) ∩ 消除棒子(2 词) / 并集(22) ≈ 0.09，不合；
            #   真正同源的"主包 + 纹身 + 瘦腿"那种标识集合高度重合的照样合。
            #   🚨 还必须加**绝对交集下限**（用户 2026-10-12 实测的真反例）：
            #   「女巫 白丝连裤袜 / 短款白丝 / 短款黑丝 / 黑丝连裤袜」四个目录各 1 个 pak，
            #   pak 名分别是 `witch white panty` / `witch white short stocking` /
            #   `witch black short stocking` / `witch black panty` —— 标识集合都只有
            #   3 个词且**都含 `witch`（角色名）**，两两交集 2 个词、Jaccard 恰好 0.5，
            #   并查集一传全通 → 四套**不同的衣服**被合成一个 mod，用户只能整套装卸。
            #   而 sig 只有 3 个词的集合，Jaccard 0.5 只说明"撞了 2 个词"，毫无说服力；
            #   真正同源的部件（本体 + update）sig 有几十个词、交集以十计数。
            #   → 交集 < 3 个词时一律不算同源证据。
            if not same and sigs[i] and sigs[j]:
                inter = len(sigs[i] & sigs[j])
                union = len(sigs[i] | sigs[j])
                if union and inter >= 3 and inter / union >= 0.5:
                    same = True
            # 证据 3：**部位拆分结构** —— 目录名全是部位/组件说明词
            #   （`脸部纹身` / `腿部纹身` / `裤子 瘦腿效果` / `主文件 衣服+胖腿`），
            #   且 pak 标识有共同主名。这是「同一件衣服的主文件 + 各部位」，
            #   和「同一角色的多套不同衣服」在 pak 名层面**结构完全相同**
            #   （都含一个公共主名、两两交集 2 个词），只有目录名语义能区分：
            #   真实反例（用户 2026-10-12）：「女巫 白丝连裤袜 / 短款白丝 /
            #   短款黑丝 / 黑丝连裤袜」pak 名都含 `witch`、Jaccard 0.5，
            #   但目录名是**款式**不是部位 → 必须拆开；而 10-10 用户明确要求
            #   合起来的「黑红短裙」四部件，目录名全是部位词 → 必须合。
            if not same and _is_part_desc(cands[i].label) and _is_part_desc(cands[j].label):
                if sigs[i] & sigs[j]:
                    same = True
            if same:
                union_find(i, j)

    buckets: dict[int, List["ScanCandidate"]] = {}
    for i, cand in enumerate(cands):
        buckets.setdefault(find(i), []).append(cand)
    # ⚠️ 按**首个成员的原下标**排序，而不是 dict 的插入顺序 ——
    #   保证同一目录每次跑出来的分组顺序完全一致（用户看到的"前两个合成一个"不能变）。
    order = sorted(buckets.values(), key=lambda grp: cands.index(grp[0]))
    return order


# ---------------------------------------------------------------- 批量入口
def _expand_folder(path: Path, store: ModStore, max_depth: int) -> List[ImportUnit]:
    """把一个目录拆成多个 mod 单元。

    ⚠️ 这是为「一个文件夹里其实装着十几个 mod」这种情况补的。
    真实例：`以往绅士mod合集/NSFW合集/1.衣服 套装、散件/明甲/女仆装/xxx.pak`
    —— 用户拖最外层那一下，心里想的是"把这批全导进来"，
    而不是"导进来一个叫「以往绅士mod合集」的巨型 mod"。

    命中判据沿用 `scan_candidates`：目录里（含子孙）有 .pak / .ucas / .utoc
    就算一个单元；命中后不再往下钻，避免同一份文件被算成两个 mod。

    ⚠️ 例外：**同系列**的候选（同一个 mod 的多个部件）会先被合成一个单元。
    判据见 `_group_candidates` 的注释 —— 它把候选分成若干组，每组一个 mod。
    真实案例（用户 2026-10-11）：
        `绅士内容mod 尺度大 内容丰富 11.7更新/`
          ├ Project_Lewd 本体（46 pak）
          ├ ProjectLewd_20251107_update_pack（11 pak）   ← 与上面同系列，合一个
          ├ 消除棒子（1 pak）                            ← 独立
          └ 消除重复的眼部纹理（1 pak）                   ← 独立
        原先的"全连通才合"判据要求 4 个**全部**两两同源，只要有 1 个不合就整个
        父目录全拆开，于是 4 个全成了独立 mod。分组之后 = 3 个 mod，符合用户预期。
    """
    cands = scan_candidates(path, store, max_depth=max_depth)
    if not cands:
        return []

    units: List[ImportUnit] = []
    for group in _group_candidates(cands):
        head = group[0]
        unit = classify_input(head.path)
        if unit is None:
            continue
        if len(group) > 1:
            # 合出来的单元：第一个目录当"主"，其余进 extra_paths。
            # ⚠️ 全部都是 folder 才行 —— 压缩包/单 pak 无法"合"（要么解压要么独立）。
            rest = [c for c in group[1:] if classify_input(c.path) and c.kind == "folder"]
            if len(rest) == len(group) - 1:
                unit.extra_paths = [Path(c.path) for c in rest]
                unit.part_labels = [head.label] + [c.label for c in rest]
                # ⚠️ 名字用**系列名**，不用部件名，也不用父目录名。
                #   用户看到的一直是"黑红短裙"而不是"主文件 衣服+胖腿"，
                #   "Project Lewd"而不是"Project_Lewd 本体" —— 部件名是内部结构。
                #   父目录名往往更糟：Project Lewd 那个案例的父目录叫
                #   「绅士内容mod 尺度大 内容丰富 11.7更新」，是作者写给**人类看的
                #   宣传语**（还带日期），当 mod 名毫无意义。
                #
                #   判据链：部件名归一出的**系列名** → 父目录名 → 第一个部件名。
                #   系列名算不出来（比如部件名全是"主文件/脸部/腿部"这类部位说明）
                #   就退回父目录名 —— 对 `黑红短裙/主文件 衣服+胖腿/` 恰好给出
                #   正确结果「黑红短裙」。
                series = _series_display_name(unit.part_labels)
                if series:
                    unit.label = series
                else:
                    parent = Path(head.path).parent
                    # ⚠️ 父目录 == 用户点进来的那层时**也**用父目录名：
                    #   用户点 `黑红短裙/` 就是想导这一个 mod，名字就该是它。
                    if parent.name:
                        unit.label = parent.name
        units.append(unit)
    return units


def drop_nested_paths(paths: Iterable[str | os.PathLike[str]]) -> List[str]:
    """去掉"被另一个选中路径包住"的子路径，保持原有顺序并去重。

    用户多选文件夹时很容易既选中父目录、又选中它里面的某个 mod 文件夹。
    父目录本来就会把子目录拆成独立 mod 导入，所以子目录必须丢掉 ——
    否则同一个 mod 会被导两遍（第二遍虽会被内容指纹判重挡掉，但白等一轮、
    进度条也会显得莫名其妙）。

    纯路径运算，不碰文件系统，所以不依赖 Qt、可以单独跑测试。
    """
    raws = [str(p) for p in paths if str(p)]
    out: List[str] = []
    for raw in raws:
        self_path = Path(raw)
        shadowed = any(
            self_path != Path(other) and Path(other) in self_path.parents for other in raws
        )
        if shadowed or raw in out:
            continue
        out.append(raw)
    return out


@dataclass
class PrecheckHit:
    """预检命中的一条：某个待导入项在库里已经有了一模一样的副本。"""

    source: str                      # 用户选中的那个路径
    label: str                       # 给人看的名字
    existing: ModMeta                # 库里已有的那条
    reason: str                      # "exact" = 逐字节相同；"name" = 名字撞了


def precheck_duplicates(
    paths: Iterable[str | os.PathLike[str]],
    store: ModStore,
    *,
    expand_dirs: bool = True,
    expand_max_depth: int = 6,
) -> Tuple[List[PrecheckHit], List[str]]:
    """**导入之前**先查一遍：这些路径里，哪些已经在库里了。

    用户 2026-10-11 的原话：「如果有些mod之前已经导入过了，那么二次导入的时候
    你能识别出来吗？能识别到就给出报警提示。」

    为什么非要单独一遍，而不是等 `_import_unit` 跑到第 5 步才报：
      导入一个大合集（几十 GB、几十个 mod）要解压 + 算 sha256，用户能等半分钟，
      结果最后告诉他"这些你上回都导过了"——白等。所以这里**提前**判定，
      让用户在点「导入」之前就知道要白跑一趟。

    判据分两级，**都只是提示、不拦截**：
      · `exact` —— 一组文件的**内容 sha256** 全等（排序后比）。
        这是权威判据，与 `_import_unit` 里的 `find_by_hash` 完全一致。
      · `name`  —— pak 的**文件名集合**与库里某条一致，但内容不同。
        这种情况多半是同一作者重新打包/改了内容，值得提醒但不该断言重复。

    ⚠️🚨 **指纹只看内容、不看路径**（2026-10-11 修的真 bug）：
      真实案例（用户截图）：`全果 明军-11.28/`（文件夹）与 `全果 明军-11.28.zip`
      是同一个 mod，pak 的 sha256 完全相同，但文件夹版剥掉了顶层目录、ZIP 版没剥 →
      两个 `rel_path` 不同 → 旧算法算出两个指纹 → 库里多出一条一模一样的 mod，
      用户截图指出来问「这两个是同一个 mod，导入时为什么没有提示？」。

    ⚠️ **压缩包也判重，不再一律放弃**（同一个 bug 的另一半）：
      之前压缩包全部进 `unknown`，等于 ZIP 导入**完全绕过**了预检。
      现在 ZIP 走 `zipfile.ZipFile` **流式读内容算哈希**（不落盘、不解压）；
      7z / rar 没有纯 Python 读法，仍如实说"没法提前判定"，
      留给导入过程里的 `find_by_hash` 兜底。

    返回 `(命中的列表, 没能判定的路径列表)`。
    """
    hits: List[PrecheckHit] = []
    unknown: List[str] = []

    # 库里按**内容 sha256 集合**建索引 —— 与 compute_content_hash 同一口径。
    # ⚠️ 这里绝不能带 rel_path：带上就退回"路径不同即不同"的老毛病（见上）。
    by_content: dict[tuple, ModMeta] = {}
    by_name_set: dict[frozenset, ModMeta] = {}
    for meta in store.all():
        shas = tuple(sorted(f.sha256 for f in meta.files if f.sha256))
        if shas:
            by_content.setdefault(shas, meta)
        names = frozenset(
            (f.rel_path or f.name).lower() for f in meta.files if f.rel_path or f.name
        )
        if names:
            by_name_set.setdefault(names, meta)

    def _judge(shas: List[str], names: frozenset, label: str, source: str) -> None:
        """给定一组内容哈希与文件名集合，查库并记命中。"""
        if shas:
            existing = by_content.get(tuple(sorted(shas)))
            if existing is not None:
                hits.append(PrecheckHit(source, label, existing, "exact"))
                return
        if names:
            existing = by_name_set.get(names)
            if existing is not None:
                hits.append(PrecheckHit(source, label, existing, "name"))

    def check_one(scan_root: Path, label: str, source: str) -> None:
        try:
            scan = pakfiles.scan_folder(scan_root)
        except Exception:                             # noqa: BLE001 - 预检不该让导入失败
            unknown.append(source)
            return
        if not scan.paks:
            return
        shas: List[str] = []
        names: List[str] = []
        for e in scan.paks:
            try:
                shas.append(modstore.hash_file(e.abs_path))
            except OSError:
                unknown.append(source)
                return
            names.append("/".join(e.content_parts).lower())
        _judge(shas, frozenset(names), label, source)

    def check_archive(path: Path, label: str, source: str) -> None:
        """压缩包判重。ZIP 能流式读；7z / rar 只能按名字给弱提示。"""
        kind = archive.detect_archive_kind(path)
        if kind == archive.KIND_ZIP:
            shas, names, ok = _zip_pak_hashes(path)
            if not ok:
                unknown.append(source)
                return
            if not shas:
                return
            _judge(shas, frozenset(names), label, source)
            return
        # 7z / rar：拿不到内容哈希。**只按 pak 文件名集合**给 name 级提示 ——
        # 宁可只提醒、不断言重复，也别让用户以为"系统没认出来"。
        names = _archive_pak_names(path, kind)
        if names:
            existing = by_name_set.get(frozenset(names))
            if existing is not None:
                hits.append(PrecheckHit(source, label, existing, "name"))
        else:
            unknown.append(source)

    def _display_label(scan_root: Path, unit_label: str) -> str:
        """给用户看的名字 —— 别直接甩目录名。

        库内目录叫 `files` / `preview` 这种内部名，直接显示等于没提示
        （实测第一次就是这么显示的）。有 pak 就用 pak 名，否则退回目录名。
        """
        try:
            paks = pakfiles.scan_folder(scan_root).paks
        except Exception:                             # noqa: BLE001
            return unit_label
        if len(paks) == 1:
            return paks[0].name
        if paks:
            return f"{unit_label}（{len(paks)} 个文件）"
        return unit_label

    def check_one_file(path: Path, source: str) -> None:
        """只判**一个** pak 文件（用户直接选文件、不选文件夹的情形）。"""
        sha = _hash_of_file(path)
        if sha is None:
            unknown.append(source)
            return
        _judge([sha], frozenset({path.name.lower()}), path.name, source)

    for raw in paths:
        p = Path(raw)
        unit = classify_input(p)
        if unit is None:
            continue
        if unit.kind == "archive":
            check_archive(p, unit.label, str(p))
            continue
        if expand_dirs and unit.kind == "folder":
            units = _expand_folder(p, store, expand_max_depth)
            if units:
                for u in units:
                    if u.kind == "folder":
                        check_one(u.path, _display_label(u.path, u.label), str(u.path))
                    else:
                        check_one_file(u.path, str(u.path))
                continue
        if unit.kind == "pak":
            # ⚠️ 裸 pak 要**只看这一个文件**，不能扫它的父目录 ——
            #   导入流程（`_import_unit` 第 1 步）是把它单独复制进暂存再扫的，
            #   判据必须与之一致，否则"选一个 pak"会被判成"选了它旁边所有 pak"。
            check_one_file(p, str(p))
            continue
        check_one(p, _display_label(p, unit.label), str(p))

    return hits, unknown


def _hash_of_file(path: Path) -> Optional[str]:
    """单个文件的 sha256；读不了返回 None。"""
    try:
        return modstore.hash_file(path)
    except OSError:
        return None


def _zip_pak_hashes(path: Path) -> Tuple[List[str], List[str], bool]:
    """流式读 ZIP 里的 pak，算内容哈希 —— **不落盘、不解压**。

    返回 `(sha256 列表, 剥层级后的相对路径列表, 是否成功)`。
    `成功=False` 表示这个 ZIP 读不了（加密、损坏、非 zip）—— 调用方要如实
    告诉用户"没法提前判定"，**不能猜**。

    为什么值得单独写：ZIP 是最常见的 mod 分发格式，而判重的价值恰恰在于
    「同一个 mod 有文件夹版和 ZIP 版」这种最常见的重复（用户 2026-10-11 截图
    实锤：`全果 明军-11.28/` 与 `全果 明军-11.28.zip`）。7z / rar 没有纯
    Python 读法，只能退到"按文件名集合给弱提示"。
    """
    import hashlib
    import zipfile

    shas: List[str] = []
    names: List[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                member = info.filename.replace("\\", "/")
                base = os.path.basename(member)
                if not base or base.startswith("."):
                    continue
                if os.path.splitext(base)[1].lower() not in pakfiles.PAK_EXT_SET:
                    continue
                with zf.open(info) as fh:
                    h = hashlib.sha256()
                    while True:
                        block = fh.read(1024 * 1024)
                        if not block:
                            break
                        h.update(block)
                shas.append(h.hexdigest())
                names.append(base)
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError):
        return [], [], False
    return shas, names, True


def _archive_pak_names(path: Path, kind: str) -> List[str]:
    """7z / rar：只列 pak 的文件名（没有纯 Python 读法，够用于弱提示）。

    ⚠️ 宁可**返回空**（= 没法判定）也不要瞎猜。误报"这是重复"比漏报糟得多。
    """
    if kind == archive.KIND_7Z:
        exe = shutil.which("7z") or shutil.which("7z.exe")
        if not exe:
            return []
        try:
            import subprocess

            proc = subprocess.run(
                [exe, "l", "-slt", str(path)],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if proc.returncode != 0:
            return []
        out: List[str] = []
        for line in proc.stdout.splitlines():
            if line.lower().startswith("path = "):
                base = os.path.basename(line.split("=", 1)[1].strip().replace("\\", "/"))
                if base and os.path.splitext(base)[1].lower() in pakfiles.PAK_EXT_SET:
                    out.append(base)
        return out
    return []


def import_paths(
    paths: Iterable[str | os.PathLike[str]],
    store: ModStore,
    *,
    progress: Optional[ProgressFn] = None,
    expand_dirs: bool = True,
    expand_max_depth: int = 6,
) -> ImportReport:
    """批量导入。**逐条容错** —— 一条失败不影响其余。

    参数
    ----
    expand_dirs : 输入是目录时，是否先把它拆成里面一个个 mod 再导。
                  默认开（用户拖一个合集文件夹时就要这个行为）。
                  **关掉**时一个目录 = 一个 mod（需求文档 F-2.3 的字面语义）。
    """
    report = ImportReport()

    # ---- 先把输入摊平：目录可能被拆成很多个单元 ----
    units: List[ImportUnit] = []
    for raw in paths:
        p = Path(raw)
        if expand_dirs and p.is_dir():
            expanded = _expand_folder(p, store, expand_max_depth)
            if expanded:
                units.extend(expanded)
                continue
        unit = classify_input(p)
        if unit is None:
            report.add(
                ImportOutcome(
                    KIND_SKIPPED,
                    str(p),
                    message=f"「{p.name or p}」无法识别，已跳过。",
                    detail=guess_unsupported_reason(p),
                )
            )
            continue
        units.append(unit)

    total = len(units)
    counter = store.count()

    for i, unit in enumerate(units, start=1):
        before = store.count()
        outcome = _import_unit(
            unit, store, fallback_index=counter + 1, progress=progress, cur=i, total=total
        )
        report.add(outcome)
        if store.count() > before:
            counter = store.count()
    _emit(progress, "完成", total, total, report.summary())
    return report


# ---------------------------------------------------------------- 扫描目录
@dataclass
class ScanCandidate:
    """「扫描目录」发现的一个候选（需求文档 F-2.4）。"""

    path: Path
    kind: str            # archive | folder
    label: str
    size: int = 0
    pak_count: int = 0
    already_likely: bool = False
    hint: str = ""


def scan_candidates(
    root: str | os.PathLike[str],
    store: Optional[ModStore] = None,
    *,
    max_depth: int = SCAN_MAX_DEPTH,
    max_results: int = SCAN_MAX_RESULTS,
    include_bare_paks: bool = False,
) -> List[ScanCandidate]:
    """扫描一个目录，列出里面"看起来是 mod"的东西，供用户一键导入。

    判定"是一个 mod"的两条标准：
      - 是一个压缩包（zip / 7z / rar，按魔数认）
      - 是一个包含 .pak 的目录（且它的上级不是已命中的候选，避免一个 mod 报两次）

    `include_bare_paks=True` 时，额外把**裸 pak 文件**也各算一条候选。
    这是给「自动扫描游戏 mod 目录」用的（2026-10-11）：游戏里的 `~mods`
    下面躺着的就是一堆裸 `.pak`（既不是压缩包、也不在子目录里），
    不带上它们的话，扫 `~mods` 会一条都扫不出来。
    """
    root = Path(root)
    out: List[ScanCandidate] = []
    if not root.is_dir():
        return out

    # ⚠️ 前置拦截"明显不是 mod 下载目录"的大目录（2026-10-11 防闪退）。
    # 用户曾把 `E:\SteamLibrary`（整个 Steam 库根）设成扫描目录 ——
    # 那样连 `steamapps` 都会被当成"一个 mod"，一导入就递归上百 GB。
    try:
        top_level = len(list(root.iterdir()))
    except OSError:
        return out
    if top_level > SCAN_SKIP_DIR_ENTRIES:
        return out

    known_sources: set[str] = set()
    if store is not None:
        known_sources = {m.source_archive.lower() for m in store.all() if m.source_archive}

    def walk(directory: Path, depth: int) -> None:
        if depth > max_depth or len(out) >= max_results:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda x: x.name.lower())
        except OSError:
            return

        for entry in entries:
            if len(out) >= max_results:
                return
            if entry.is_dir():
                if pakfiles.is_ignored_dir(entry.name):
                    continue
                scan = pakfiles.scan_folder(entry)
                if scan.paks:
                    size = sum(e.size for e in scan.paks)
                    out.append(
                        ScanCandidate(
                            path=entry,
                            kind="folder",
                            label=entry.name,
                            size=size,
                            pak_count=len(scan.paks),
                            already_likely=entry.name.lower() in known_sources,
                            hint=scan.strip.target_subdir if scan.strip.kind != "plain" else "",
                        )
                    )
                    continue        # 命中就不再往下钻，避免同一 mod 重复出现
                walk(entry, depth + 1)
            elif entry.is_file():
                if entry.name.lower() in known_sources:
                    already = True
                else:
                    already = False
                if archive.looks_like_archive(entry):
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    out.append(
                        ScanCandidate(
                            path=entry,
                            kind="archive",
                            label=entry.stem,
                            size=size,
                            already_likely=already,
                        )
                    )
                elif include_bare_paks and entry.suffix.lower() in pakfiles.PAK_EXT_SET:
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    out.append(
                        ScanCandidate(
                            path=entry,
                            kind="pak",
                            label=entry.stem,
                            size=size,
                            pak_count=1,
                            already_likely=already,
                        )
                    )

    walk(root, 1)
    return out
