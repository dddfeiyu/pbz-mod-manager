# -*- coding: utf-8 -*-
"""识别「游戏目录里躺着、但不受本工具管理」的 mod（用户 2026-10-12）。

场景（用户原话）：手动往 `~mods` 里放了 10 个 mod，其中 5 个与库里的 mod
相同 —— 「管理器能识别到这五个 mod 吗？并在管理器中显示已经安装？」

为什么原来认不出：卡片的「已装」状态只认 `library.json` 的安装记录
（`installed_ids`）。手放的文件没有记录 → 界面显示「未装」，
**尽管游戏确实在加载它们**。用户 2026-10-11 遇到的就是这个：
卸完 82 个后界面全未装、`~mods` 里却有文件，还以为是软件自己装的。

判据只有一条：**内容 sha256**。文件名、路径一律不看 ——
「作者改名重发仍是同一个 mod」是 0.1.17 修内容指纹时定下的规矩
（`modstore.compute_content_hash` 的头注释里有完整事故记录）。

匹配粒度：
  · 完整命中（mod 的每个文件哈希都在游戏目录里出现）→「手装，生效中」；
  · 部分命中 → 也报出来（可能只拖了一部分文件进去，游戏加载是不全的）；
  · 一个都没命中 → 这个文件进 `unknown`（用户手放但库里没有的 mod）。

⚠️ 冲突检测**不**覆盖手装文件（那需要落点信息，属于另一件事）——
   本模块只回答「游戏里有什么、和库里哪个 mod 是同一个」。
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import apppaths, jsonstore, modstore, pakfiles

# ---------------------------------------------------------------- 规模刹车
#: 扫 `~mods` / `LogicMods` 时的文件数上限。这两个目录是**游戏自己扫的**
#: mod 存放层，正常就几十个文件；真到了几千个文件说明用户在拿它当仓库，
#: 那时候宁可提示「太多没扫完」，也不能把界面卡死（0.1.11 闪退事故的规矩）。
WILD_MAX_FILES = 5_000

#: 递归深度上限。`~mods` 下用户可能自建一层分类目录，两层足够看得见。
WILD_MAX_DEPTH = 3

#: 自动识别（安装/卸载后静默触发）时的**总字节上限**。
#: 超过这个量就不自动跑 —— 哈希几十 GB 会把界面冻住好几秒，
#: 那种规模请用户手动点「识别游戏里已有的 mod」（有等待提示）。
AUTO_MAX_BYTES = 10 * 1024 * 1024 * 1024

#: 缓存文件名（落在 %APPDATA%\WuchangModManager\ 下）
CACHE_FILENAME = "wild_scan.json"

#: 缓存结构版本。结构不兼容时整个缓存作废（宁可重扫，不要读错）。
CACHE_SCHEMA = 1


@dataclass
class GameFile:
    """游戏 mod 目录里的一个文件。"""

    path: Path
    #: 相对所扫目录的路径（显示用；Windows 分隔符统一成 `/`）
    rel: str
    size: int
    mtime: float
    sha256: str = ""

    @property
    def is_pak_like(self) -> bool:
        return pakfiles.is_pak_file(self.path.name)


@dataclass
class ExternalHit:
    """一个库内 mod 在游戏目录里的「手装命中」。"""

    mod_id: str
    mod_name: str
    #: 命中的文件数（mod 的哈希有几个在游戏目录里出现）
    matched: int
    #: mod 的文件总数
    total: int
    #: 游戏目录里对应的文件路径（显示用）
    paths: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """是否**完整**命中 —— 每个文件都在，游戏加载的就是这一套。"""
        return self.total > 0 and self.matched >= self.total

    @property
    def text(self) -> str:
        """卡片徽标 / tooltip 用的一行说明。"""
        if self.complete:
            return f"手装：{self.total} 个文件都在游戏里"
        return f"手装：只有 {self.matched}/{self.total} 个文件在游戏里"

    def to_dict(self) -> dict:
        return {
            "name": self.mod_name,
            "matched": self.matched,
            "total": self.total,
            "paths": list(self.paths),
        }

    @classmethod
    def from_dict(cls, mod_id: str, raw: dict) -> "ExternalHit":
        return cls(
            mod_id=mod_id,
            mod_name=str(raw.get("name") or ""),
            matched=int(raw.get("matched") or 0),
            total=int(raw.get("total") or 0),
            paths=[str(p) for p in (raw.get("paths") or [])],
        )


@dataclass
class WildScan:
    """一次识别的结果。"""

    #: 扫的是哪些目录（绝对路径，显示/对账用）
    dirs: List[str] = field(default_factory=list)
    #: 目录签名（`signature()` 的输出）—— 变了才需要重扫
    sig: str = ""
    #: 命中的库内 mod：mod_id -> ExternalHit
    hits: Dict[str, ExternalHit] = field(default_factory=dict)
    #: 没能对应到任何库内 mod 的文件（用户手放但工具不认识的）
    unknown: List[GameFile] = field(default_factory=list)
    #: 全部看到的文件（含已知 + 未知）
    files: List[GameFile] = field(default_factory=list)
    #: 是否因规模超限提前停下（必须如实告知，绝不静默截断）
    truncated: bool = False
    scanned_at: str = ""

    @property
    def complete_hits(self) -> List[ExternalHit]:
        return [h for h in self.hits.values() if h.complete]

    @property
    def partial_hits(self) -> List[ExternalHit]:
        return [h for h in self.hits.values() if not h.complete]

    def summary(self) -> str:
        """状态栏用的一句话。"""
        bits: List[str] = []
        n_full = len(self.complete_hits)
        n_part = len(self.partial_hits)
        if n_full:
            bits.append(f"{n_full} 个手装 mod 与库中相同")
        if n_part:
            bits.append(f"{n_part} 个只对上了一部分文件")
        n_unknown_pak = sum(1 for f in self.unknown if f.is_pak_like)
        if n_unknown_pak:
            bits.append(f"{n_unknown_pak} 个文件库里没有")
        if not bits:
            return "识别完成：游戏目录里没有不受本工具管理的 mod 文件。"
        return "识别完成：" + "，".join(bits) + "。"


# ---------------------------------------------------------------- 库索引
def build_mod_index(
    store: modstore.ModStore,
) -> Dict[str, Tuple[str, set]]:
    """把库变成 `{mod_id: (名称, {sha256, ...})}`。

    sha256 直接读 `meta.json` 里存好的（导入时就算过），**不重算库文件**——
    库里可能几个 GB，每次识别都重算一遍没必要。
    老数据没有 sha256 的条目跳过（它的文件本来就对不上任何东西）。
    """
    index: Dict[str, Tuple[str, set]] = {}
    for meta in store.all():
        hashes = {f.sha256 for f in meta.files if f.sha256}
        if hashes:
            index[meta.id] = (meta.name, hashes)
    return index


def lookup_mod(
    index: Dict[str, Tuple[str, set]], sha256: str
) -> Optional[Tuple[str, str]]:
    """一个 sha256 对应库里的哪个 mod。返回 `(mod_id, 名称)`，没有则 None。

    一个文件被多个 mod 共用时取**字典序最小**的 mod_id —— 纯粹为了结果
    可重现（dict 迭代序在 py3.7+ 虽稳定，但显式排序更不怕改）。
    """
    if not sha256:
        return None
    best: Optional[Tuple[str, str]] = None
    for mod_id in sorted(index):
        name, hashes = index[mod_id]
        if sha256 in hashes:
            best = (mod_id, name)
            break
    return best


# ---------------------------------------------------------------- 扫描
def _iter_dirs(dirs: Sequence[Path]) -> List[Path]:
    """去重 + 只保留存在的目录（顺序保持，不去排序）。"""
    out: List[Path] = []
    seen: set[str] = set()
    for d in dirs:
        try:
            p = Path(d)
            if not p.is_dir():
                continue
            key = str(p).rstrip("\\/").lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def list_game_files(dirs: Sequence[Path]) -> Tuple[List[GameFile], bool]:
    """列出目录里的文件（**只 stat，不算哈希** —— 要快）。

    返回 `(文件列表, 是否被规模刹车截断)`。
    """
    files: List[GameFile] = []
    truncated = False
    for root in _iter_dirs(dirs):
        root_str = str(root).rstrip("\\/")
        for dirpath, dirnames, filenames in os.walk(root):
            try:
                depth = (
                    0
                    if Path(dirpath) == root
                    else len(Path(dirpath).relative_to(root).parts)
                )
            except ValueError:
                depth = WILD_MAX_DEPTH
            if depth >= WILD_MAX_DEPTH:
                truncated = True
                dirnames[:] = []
                continue
            # 平台库目录名整名匹配（pakfiles.IGNORE_DIR_NAMES 的规矩）
            dirnames[:] = [d for d in dirnames if not pakfiles.is_ignored_dir(d)]

            for name in filenames:
                if len(files) >= WILD_MAX_FILES:
                    truncated = True
                    dirnames[:] = []
                    break
                if pakfiles.is_ignored_file(name):
                    continue
                abs_path = Path(dirpath) / name
                try:
                    st = abs_path.stat()
                except OSError:
                    continue
                rel = str(abs_path.relative_to(root)).replace("\\", "/")
                files.append(
                    GameFile(
                        path=abs_path,
                        rel=rel,
                        size=st.st_size,
                        mtime=st.st_mtime,
                    )
                )
            if truncated:
                break
    return files, truncated


def signature(files: Iterable[GameFile]) -> str:
    """目录签名：只看 `(rel, size, mtime)`，不看内容。

    用来判断「要不要重新算哈希」。同路径同大小同 mtime 就当内容没变
    （改文件必改 mtime；用户手动覆盖同名文件也会更新 mtime）。
    ⚠️ 这只是**状态提示**的缓存，不是安全判据 —— 真判据永远是 sha256。
    """
    rows = sorted(
        f"{f.rel}|{f.size}|{f.mtime:.0f}" for f in files
    )
    h = hashlib.sha256()
    h.update(("\n".join(rows)).encode("utf-8"))
    return h.hexdigest()


def _hash_files(files: List[GameFile]) -> None:
    """原地填 sha256。读失败的文件留着空串（匹配时自然落进 unknown）。"""
    for f in files:
        try:
            f.sha256 = modstore.hash_file(f.path)
        except OSError:
            f.sha256 = ""


def match(
    files: List[GameFile], index: Dict[str, Tuple[str, set]]
) -> Tuple[Dict[str, ExternalHit], List[GameFile]]:
    """按内容哈希把游戏文件对到库内 mod 上。

    返回 `(命中, 未知文件)`。一个文件可以同时算进多个 mod 的命中
    （两个 mod 共用同一个 pak 是真实现象），但**只会**在第一个
    （字典序最小 mod_id）mod 的 `paths` 里列出，避免界面重复。
    """
    hits: Dict[str, ExternalHit] = {}
    claimed: set[str] = set()          # 已列入某个 mod paths 的文件 rel
    hash_to_mods: Dict[str, List[str]] = {}
    for mod_id, (_name, hashes) in index.items():
        for h in hashes:
            hash_to_mods.setdefault(h, []).append(mod_id)

    for f in files:
        if not f.sha256:
            continue
        mod_ids = hash_to_mods.get(f.sha256)
        if not mod_ids:
            continue
        for mod_id in mod_ids:
            name = index[mod_id][0]
            hit = hits.get(mod_id)
            if hit is None:
                hit = ExternalHit(mod_id=mod_id, mod_name=name, matched=0, total=0)
                hits[mod_id] = hit
            hit.matched += 1
            if f.rel not in claimed:
                claimed.add(f.rel)
                hit.paths.append(str(f.path))

    for mod_id, (_name, hashes) in index.items():
        if mod_id in hits:
            hits[mod_id].total = len(hashes)

    known_hashes = set(hash_to_mods)
    unknown = [f for f in files if not f.sha256 or f.sha256 not in known_hashes]
    return hits, unknown


# ---------------------------------------------------------------- 缓存
def cache_path() -> Path:
    return apppaths.data_dir() / CACHE_FILENAME


def save_cache(scan: WildScan) -> None:
    """把识别结果落盘。`refresh()` 只读这个文件，不重扫。"""
    payload = {
        "schema": CACHE_SCHEMA,
        "dirs": list(scan.dirs),
        "sig": scan.sig,
        "scanned_at": scan.scanned_at,
        "truncated": scan.truncated,
        "hits": {mid: hit.to_dict() for mid, hit in scan.hits.items()},
        "unknown": [
            {"path": str(f.path), "rel": f.rel, "size": f.size} for f in scan.unknown
        ],
    }
    try:
        jsonstore.save_json(cache_path(), payload)
    except Exception:  # noqa: BLE001 - 缓存写失败不该让识别白做
        pass


def load_cache() -> Optional[WildScan]:
    """读缓存。文件不存在 / 结构不兼容 / 损坏 → None（调用方重扫）。"""
    raw = jsonstore.load_json(cache_path(), default=None)
    if not isinstance(raw, dict) or int(raw.get("schema") or 0) != CACHE_SCHEMA:
        return None
    hits: Dict[str, ExternalHit] = {}
    for mod_id, item in (raw.get("hits") or {}).items():
        if isinstance(item, dict):
            hits[str(mod_id)] = ExternalHit.from_dict(str(mod_id), item)
    unknown = [
        GameFile(
            path=Path(str(u.get("path") or "")),
            rel=str(u.get("rel") or ""),
            size=int(u.get("size") or 0),
            mtime=0.0,
        )
        for u in (raw.get("unknown") or [])
        if isinstance(u, dict) and u.get("path")
    ]
    return WildScan(
        dirs=[str(d) for d in (raw.get("dirs") or [])],
        sig=str(raw.get("sig") or ""),
        hits=hits,
        unknown=unknown,
        truncated=bool(raw.get("truncated")),
        scanned_at=str(raw.get("scanned_at") or ""),
    )


def load_cache_hits() -> Dict[str, ExternalHit]:
    """refresh() 用：只要命中表，不要整个 scan。"""
    cached = load_cache()
    return cached.hits if cached else {}


def load_cache_hits_validated() -> Dict[str, ExternalHit]:
    """启动时用的命中表：先校验文件还在，再给界面。

    为什么要有这一步（用户 2026-10-13）：启动画面直接读上次扫描的缓存，
    但缓存扫完之后用户可能手动删了文件 —— 界面就会显示「已装·手装」，
    游戏目录里其实什么都没有。`recognize()` 的 sig 校验能兜住
    「目录变了就重扫」，但那只发生在安装/卸载/手动触发时，
    **启动路径不扫目录**，过期缓存会一路显示到下一次扫描为止。

    做法：每个命中逐条 `os.path.isfile()`（纯 stat，不读内容，微秒级）。
    paths **全部**消失 → 这个 mod 肯定不在游戏里了，剔除并把修剪结果
    写回缓存（下次启动也不再出现）。部分消失 → 保留（matched 与 paths
    数量不对应 —— 共用文件只列进第一个 mod 的 paths —— 没法安全下调，
    交给下一次完整扫描纠正）。`unknown` 同理修剪。
    """
    cached = load_cache()
    if cached is None:
        return {}
    dropped_hits = False
    for mod_id in list(cached.hits):
        hit = cached.hits[mod_id]
        if hit.paths and not any(os.path.isfile(p) for p in hit.paths):
            del cached.hits[mod_id]
            dropped_hits = True
    kept_unknown = [u for u in cached.unknown if os.path.isfile(str(u.path))]
    if dropped_hits or len(kept_unknown) != len(cached.unknown):
        cached.unknown = kept_unknown
        save_cache(cached)
    return cached.hits


# ---------------------------------------------------------------- 主入口
def recognize(
    dirs: Sequence[Path],
    index: Dict[str, Tuple[str, set]],
    *,
    force: bool = False,
    max_bytes: Optional[int] = None,
) -> WildScan:
    """识别一次。缓存没过期就直接用缓存（不碰磁盘内容）。

    参数
    ----
    force
        True = 无视缓存重新算（用户手动点「识别」时用）。
    max_bytes
        文件总字节上限。超过就**不做**这次识别，返回一个空 scan
        （调用方应提示用户手动触发）。None = 不限制。
    """
    real_dirs = _iter_dirs(dirs)
    if not real_dirs:
        return WildScan()

    files, truncated = list_game_files(real_dirs)
    sig = signature(files)

    if not force:
        cached = load_cache()
        if cached is not None and cached.sig == sig:
            cached.truncated = truncated or cached.truncated
            return cached

    if max_bytes is not None:
        total = sum(f.size for f in files)
        if total > max_bytes:
            scan = WildScan(dirs=[str(d) for d in real_dirs], sig=sig, truncated=truncated)
            return scan

    _hash_files(files)
    hits, unknown = match(files, index)
    scan = WildScan(
        dirs=[str(d) for d in real_dirs],
        sig=sig,
        hits=hits,
        unknown=unknown,
        files=files,
        truncated=truncated,
        scanned_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    save_cache(scan)
    return scan
