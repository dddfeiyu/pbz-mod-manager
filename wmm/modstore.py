# -*- coding: utf-8 -*-
"""mod 库存放（需求文档第 4 章目录结构 + 3.7 元数据）。

库结构（原文）：
    %APPDATA%\\WuchangModManager\\mods\\          ← 或用户在设置里指向的外部目录
      └─ <mod_id>/
         ├─ meta.json      ← 名称/作者/版本/overrides/哈希 等
         ├─ preview.png    ← 第 1 或第 3 档预览图（第 2 档不落盘，运行时画字母卡）
         └─ files/         ← xxxx_P.pak / xxxx_P.ucas …

几条硬约束（不是"尽量"，是"必须"）：
  - 元数据一律写 `%APPDATA%`（或用户指定的库目录），**绝不写程序目录**。
  - 删除 mod 时**只删这条 mod 自己的目录**，且必须先确认它在库根之下
    —— 库里同时躺着别的 mod 和用户手工放进去的东西，绝不能出现
      「删一个 mod 结果把整个 mods 目录端了」。
  - `preview` 字段存的是**相对路径**而不是绝对路径：用户可能整个搬迁库目录，
    存绝对路径会让迁移后所有预览图失效。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import apppaths, jsonstore
from .preview import TIER_LETTER_CARD, TIER_USER

SCHEMA_VERSION = 1

META_FILENAME = "meta.json"
FILES_SUBDIR = "files"

#: mod_id 合法格式：sha256 前 16 位十六进制，或用户自定义的安全串
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class ModFile:
    """mod 内的一个文件（落盘后的样子）。"""

    name: str            # 落盘文件名（扁平化后）
    rel_path: str = ""   # 剥掉安装层级后的原始相对路径（排障用）
    original_rel: str = ""  # 原封不动的相对路径（含层级）
    size: int = 0
    sha256: str = ""

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1].lower()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ModFile":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class ModMeta:
    """`meta.json` 的内容。字段名沿用需求文档第 4 章，扩展字段都有注释。"""

    # ---- 文档规定的字段 ----
    id: str
    name: str = ""
    author: str = ""
    version: str = ""
    source_url: str = ""
    target_platform: str = "any"
    overrides: List[str] = field(default_factory=list)
    notes: str = ""
    created_at: str = ""
    content_hash: str = ""

    # ---- 本项目扩展（文档没写但必须有）----
    #: 名称是从哪一级推导来的（"压缩包名" / "包内目录名" / "readme 首行" / "兜底编号"）
    name_source: str = ""
    #: 原始压缩包 / 目录名（用户回头对账用）
    source_archive: str = ""
    #: 安装目标子目录（"~mods" 或 "LogicMods"）
    install_subdir: str = "~mods"
    #: 预览图相对路径；空串 = 走第 2 档字母卡
    preview: str = ""
    #: 预览图档位（preview.TIER_*）。注意这里不能写 `preview.TIER_LETTER_CARD`
    #: —— 上面的 `preview: str` 字段会在类命名空间里把 `preview` 这个名字占掉。
    preview_tier: int = TIER_LETTER_CARD
    #: 本次导入过程中的提示（例如"剥掉了 3 层安装目录"）
    import_notes: List[str] = field(default_factory=list)
    #: 文件清单
    files: List[ModFile] = field(default_factory=list)
    #: 库内目录名（= id，单独存是为了迁移时能容错）
    dir_name: str = ""

    # ---- 服装识别（costume.py）----
    #: 被替换的游戏原衣服名，例如 ["织绣胸甲"]。空 = 没识别出来。
    replaces: List[str] = field(default_factory=list)
    #: 改动到的部位槽位（costume.SLOT_*），例如 ["Chest", "Legs"]
    slots: List[str] = field(default_factory=list)
    #: 服装 mod 的新外观名（作者给的名字），例如 "XXTB Slingkini"
    look_name: str = ""
    #: 识别依据（"服装配置 JSON" / "目录名/pak 名关键词" / ...）
    costume_source: str = ""
    #: 识别可信度（costume.CONF_*）
    costume_confidence: str = ""
    #: 原始导入位置上的路径片段（从外到内，**不含安装层级**）。
    #: 存下来是为了：① 卡片上能显示「来自 以往绅士mod合集/…/明甲」；
    #: ② 重新识别时不必再让用户找一遍原路径。
    source_hint: List[str] = field(default_factory=list)
    #: **同系列合并**时被合进来的部件目录名（用户 2026-10-11）：
    #: 导入 `绅士内容mod/Project_Lewd 本体/ + ProjectLewd_20251107_update_pack/`
    #: 时这里是 `["Project_Lewd 本体", "ProjectLewd_20251107_update_pack"]`。
    #: 空 = 不是合并来的（普通单目录 mod）。卡片用它显示「同系列 2 个部件」标识。
    series_parts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["files"] = [f.to_dict() for f in self.files]
        return d

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ModMeta":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in raw.items() if k in known}
        files = kwargs.pop("files", []) or []
        if not kwargs.get("id"):
            raise ValueError("meta.json 缺少 id 字段")
        obj = cls(**kwargs)
        obj.files = [ModFile.from_dict(f) if isinstance(f, dict) else f for f in files]
        return obj

    @property
    def pak_count(self) -> int:
        return sum(1 for f in self.files if f.ext == ".pak")

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


# ---------------------------------------------------------------- 哈希
def hash_file(path: str | os.PathLike[str], *, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def compute_content_hash(files: List[ModFile]) -> str:
    """算 mod 的内容指纹 —— **只跟文件内容有关，跟路径、文件名、包名都无关**。

    做法：把每个文件的 **sha256** 排序后整体再 sha256。

    ⚠️🚨 2026-10-11 修掉的真 bug：原来算的是 `(rel_path, sha256)`，
    于是**同一批 pak 只要剥层级的结果不同，指纹就不同** → 判不出重复。
    真实案例（用户截图）：同一个 mod 有 `全果 明军-11.28/`（文件夹）和
    `全果 明军-11.28.zip`（压缩包）两个来源，**pak 的 sha256 完全相同**，
    但文件夹版剥掉了顶层目录、ZIP 版没剥 →
        文件夹版 rel_path = `Project_Plague-WinGDK_9001_P.pak`
        ZIP 版    rel_path = `全果 明军-11.28/Project_Plague-WinGDK_9001_P.pak`
    → 两个指纹不同 → 库里多出两条一模一样的 mod，用户完全看不出来。

    现在指纹**只看内容**。文件名也排掉，是因为同一份内容被作者改名重发
    （`Project_Plague-WinGDK_9001_P.pak` ↔ 别的名字）仍是同一个 mod。

    ⚠️ 顺序无关：排序后再哈希，所以 pak 的列举顺序不影响结果。
    ⚠️ 数量敏感：文件个数不同 → 指纹不同（不会把"一个 pak 的 mod"和
       "两个 pak 的 mod" 认成同一个）。
    """
    parts = sorted(f.sha256 for f in files if f.sha256)
    if not parts:
        # 一个指纹都没有（极端情况：全是 0 字节读不出 sha 的文件）
        # → 退回按文件名，否则所有这类 mod 会撞成同一个指纹。
        parts = sorted((f.rel_path or f.name) for f in files)
        if not parts:
            return ""
    blob = hashlib.sha256()
    blob.update("\x01".join(parts).encode("utf-8"))
    return blob.hexdigest()


def make_mod_id(content_hash: str) -> str:
    """mod_id = 内容哈希前 16 位（需求文档第 4 章）。"""
    return (content_hash or "")[:16] or "unknown"


# ---------------------------------------------------------------- mod 库
class ModStore:
    """`mods/` 目录的门面。"""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------ 生命周期
    @classmethod
    def default(cls) -> "ModStore":
        return cls(apppaths.mods_dir())

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def mod_dir(self, mod_id: str) -> Path:
        """库内某 mod 的目录。带安全校验，防止 mod_id 里塞路径成分。"""
        if not _SAFE_ID_PATTERN.match(mod_id or ""):
            raise ValueError(f"非法的 mod_id：{mod_id!r}")
        return self.root / mod_id

    def files_dir(self, mod_id: str) -> Path:
        return self.mod_dir(mod_id) / FILES_SUBDIR

    def meta_path(self, mod_id: str) -> Path:
        return self.mod_dir(mod_id) / META_FILENAME

    # ------------------------------------------------------------ 读
    def all(self) -> List[ModMeta]:
        out: List[ModMeta] = []
        if not self.root.is_dir():
            return out
        for child in sorted(self.root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            meta = self._load_meta(child)
            if meta is not None:
                out.append(meta)
        return out

    def _load_meta(self, mod_dir: Path) -> Optional[ModMeta]:
        raw = jsonstore.load_json(mod_dir / META_FILENAME, default=None)
        if not isinstance(raw, dict):
            return None
        try:
            meta = ModMeta.from_dict(raw)
        except (TypeError, ValueError):
            return None
        meta.dir_name = mod_dir.name
        return meta

    def get(self, mod_id: str) -> Optional[ModMeta]:
        try:
            mod_dir = self.mod_dir(mod_id)
        except ValueError:
            return None
        if not mod_dir.is_dir():
            return None
        return self._load_meta(mod_dir)

    def find_by_hash(self, content_hash: str) -> Optional[ModMeta]:
        for meta in self.all():
            if meta.content_hash and meta.content_hash == content_hash:
                return meta
        return None

    def count(self) -> int:
        return len(self.all())

    # ------------------------------------------------------------ 写
    def save(self, meta: ModMeta) -> None:
        self.ensure()
        mod_dir = self.mod_dir(meta.id)
        mod_dir.mkdir(parents=True, exist_ok=True)
        meta.dir_name = meta.id
        jsonstore.save_json(self.meta_path(meta.id), meta.to_dict())

    def commit(
        self,
        meta: ModMeta,
        staged_files: List[tuple[Path, str]],
        *,
        preview_src: Optional[Path] = None,
    ) -> ModMeta:
        """把暂存区的文件搬进库里，然后写 meta.json。

        参数
        ----
        staged_files
            `(源文件路径, 目标文件名)` 的列表。目标名已由调用方去重。
        preview_src
            第 1 档预览图的源文件（可选）。会按原后缀拷进 mod 目录。

        ⚠️ 先复制文件、**最后**写 meta.json。这样中途失败不会留下
        "有 meta.json 但没有文件"的坏记录（那种记录在 UI 上表现为一张点不开的卡片）。
        """
        self.ensure()
        mod_dir = self.mod_dir(meta.id)
        files_dir = self.files_dir(meta.id)

        # 重导同一个 mod（同 id）时先清掉旧文件，避免残留上一次的孤儿文件
        if files_dir.exists():
            shutil.rmtree(files_dir, ignore_errors=True)
        files_dir.mkdir(parents=True, exist_ok=True)

        for src, target_name in staged_files:
            dst = files_dir / target_name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        if preview_src is not None and Path(preview_src).is_file():
            ext = Path(preview_src).suffix.lower() or ".png"
            preview_name = f"preview{ext}"
            for stale in mod_dir.glob("preview.*"):
                try:
                    stale.unlink()
                except OSError:
                    pass
            shutil.copy2(preview_src, mod_dir / preview_name)
            meta.preview = preview_name
        else:
            meta.preview = ""

        self.save(meta)
        return meta

    def update_meta(self, meta: ModMeta) -> ModMeta:
        """只改元数据（改名 / 填作者 / 改 overrides 等），不动文件。"""
        self.save(meta)
        return meta

    def set_user_preview(self, meta: ModMeta, src: Path) -> ModMeta:
        """第 3 档：用户手动指定预览图（需求文档 3.6 的第 3 档）。

        什么时候需要它：自动挑图挑"错"了 —— 典型是作者压根没放角色预览图，
        包里只有游戏内菜单截图 / 操作说明图，自动挑只能从中选一张，
        而用户想要的是别的一张（甚至是他自己截的图）。
        只往 `<mod 目录>` 里拷一张 `preview.<后缀>`，**不碰任何别的文件**。
        """
        src = Path(src)
        if not src.is_file():
            raise FileNotFoundError(str(src))
        mod_dir = self.mod_dir(meta.id)
        mod_dir.mkdir(parents=True, exist_ok=True)

        extension = src.suffix.lower() or ".png"
        preview_name = f"preview{extension}"
        for stale in mod_dir.glob("preview.*"):    # 只清 mod 目录里的旧预览图，不递归
            try:
                stale.unlink()
            except OSError:
                pass
        shutil.copy2(src, mod_dir / preview_name)

        meta.preview = preview_name
        meta.preview_tier = TIER_USER
        self.save(meta)
        return meta

    # ------------------------------------------------------------ 删
    def delete(self, mod_id: str, *, dry_run: bool = False) -> List[Path]:
        """删除一条 mod 记录及其文件。返回被删的文件路径清单。

        **安全设计（照用户立的删除红线来）**：
          - 只删 `<root>/<mod_id>/` 这一个目录，不递归删 root；
          - 删之前必须确认目标严格位于 root 之下；
          - `dry_run=True` 只列清单不真删 —— UI 上「确认卸载」前先展示这个清单。
        """
        mod_dir = self.mod_dir(mod_id)
        root_resolved = self.root.resolve()
        try:
            target_resolved = mod_dir.resolve()
        except OSError:
            return []

        if target_resolved == root_resolved or root_resolved not in target_resolved.parents:
            raise ValueError(
                f"拒绝删除：{mod_dir} 不在 mod 库目录 {self.root} 之下。"
                "（这是防止误删整个库目录的保护。）"
            )

        collected: List[Path] = []
        if mod_dir.is_dir():
            for p in sorted(mod_dir.rglob("*")):
                if p.is_file():
                    collected.append(p)

        if dry_run:
            return collected

        if mod_dir.exists():
            shutil.rmtree(mod_dir, ignore_errors=True)
        return collected


# ---------------------------------------------------------------- 扫描重建
def reindex(store: ModStore) -> List[ModMeta]:
    """扫描库目录，把能认出来的 meta.json 全部读回来。

    用途：library.json 丢了 / 被用户手工删了，但 mods 目录还在 ——
    这时工具应该能"认回"自己的库，而不是显示一片空白。
    """
    return store.all()
