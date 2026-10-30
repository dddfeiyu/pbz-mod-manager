# -*- coding: utf-8 -*-
"""安装 / 卸载 / 备份回滚（需求文档 4.4 与 F-4.1 ~ F-4.10）。

⚠️ **这是全项目唯一会写进游戏目录的模块**，所以规矩比别处严，四条必须守住：

  1. **卸载只按 library.json 里记录的 `dst` 逐条删**（F-4.4 / F-4.5）。
     绝不按通配符、绝不递归删目录 —— 目标目录里同时躺着用户手工装的 mod、
     其他工具的产物（本机实测 `~mods` 下有 8 个用户手建的子目录）。
  2. **覆盖别人文件之前先备份**（F-4.9），备份随安装记录一起存，可回滚。
  3. **没写权限时给能看懂的中文**（F-4.10），而不是抛 `WinError 5`。
     Game Pass 版装在受保护位置时最常见。
  4. **安装顺序：先备份 → 再写文件 → 最后落库。** 中途失败不会留下
     「有安装记录但文件没写进去」的坏状态（那种状态点卸载会删不到东西）。

另外，卸载时删空目录用 `os.rmdir()` 而**不是** `shutil.rmtree()` ——
`rmdir` 遇到非空目录会直接抛错，这是**天然的保险**：只要目录里还有别人的东西就删不掉。
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import apppaths, gamedir, jsonstore, library as library_mod, modstore


#: 会被部署到游戏目录的后缀。其余文件（readme、截图等）**只入库、不进游戏目录**
#: —— 往 `~mods` 里丢 txt 除了让目录变乱没有任何作用。
#: `.sig` 也带上：部分 mod 会附签名文件，一起搬过去最省事（Mod Enabler 会跳过校验）。
DEPLOY_EXTS = frozenset({".pak", ".ucas", ".utoc", ".sig"})

BACKUP_MANIFEST = "manifest.json"

#: 游戏自己带的主 pak，不是 mod —— 冲突检测和覆盖检查都要放过它们
VANILLA_PAK_PATTERN = "project_plague-windows"


class GameDirNotWritable(RuntimeError):
    """目标目录写不进去。带一句人话，UI 直接拿去显示。"""


class InstallBlocked(RuntimeError):
    """计划阶段就判定装不了（例如游戏目录还没设）。"""


# ---------------------------------------------------------------- 计划
@dataclass
class PlannedFile:
    """一个待部署文件。"""

    src: Path
    dst: Path
    original_name: str
    action: str = "new"          # new | overwrite
    existing_size: int = 0
    #: 落点已存在、且**不是本工具装的**（用户手工放的 / 别的工具放的）→ 要备份 + 警示
    foreign: bool = False

    @property
    def is_overwrite(self) -> bool:
        return self.action == "overwrite"


@dataclass
class InstallPlan:
    """一次安装要做的事。UI 必须先把它展示给用户看（F-4.6）。"""

    mod_id: str
    mod_name: str
    game_dir: Path
    paks_dir: Path
    mods_dir: Path
    platform: str
    files: List[PlannedFile] = field(default_factory=list)
    #: 只入库、不部署的文件（readme / 截图），给用户解释用
    kept_in_library: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blockers and bool(self.files)

    @property
    def first(self) -> Optional[PlannedFile]:
        return self.files[0] if self.files else None

    def target_names(self) -> List[str]:
        return [f.dst.name for f in self.files]

    def summary(self) -> str:
        new = sum(1 for f in self.files if not f.is_overwrite)
        over = sum(1 for f in self.files if f.is_overwrite)
        bits = [f"新增 {new} 个文件"]
        if over:
            bits.append(f"覆盖 {over} 个已存在文件")
        if self.kept_in_library:
            bits.append(f"{len(self.kept_in_library)} 个附属文件只入库")
        return "，".join(bits)


InstrumentPlan = InstallPlan  # 兼容别名


# ---------------------------------------------------------------- 目标文件名
def _stem_of(name: str) -> str:
    return os.path.splitext(name)[0]


def _group_by_stem(files: Sequence[modstore.ModFile]) -> Dict[str, List[modstore.ModFile]]:
    """把 `.pak/.ucas/.utoc/.sig` 按「去掉后缀的主名」分组。

    F-4.2 要求「保留 .ucas 同名」—— 也就是 `x_P.pak` 改名时，
    同组的 `x_P.ucas` / `x_P.utoc` 必须跟着改成同一个主名，
    否则 IoStore 三件套主名不一致，游戏照样不加载。
    """
    groups: Dict[str, List[modstore.ModFile]] = {}
    for f in files:
        if f.ext not in DEPLOY_EXTS:
            continue
        groups.setdefault(_stem_of(f.name).lower(), []).append(f)
    for bucket in groups.values():
        bucket.sort(key=lambda f: (f.ext != ".pak", f.name.lower()))  # pak 打头
    return groups


def resolve_target_names(
    mod_name: str,
    files: Sequence[modstore.ModFile],
    platform: str,
    *,
    chunk_id: Optional[int] = None,
) -> List[Tuple[modstore.ModFile, str]]:
    """算出每个文件的目标文件名（F-4.1 / F-4.2 / F-4.3 / F-4.8）。

    命名规则：
      * **Game Pass**：必须精确为 `Project_Plague-WinGDK_<N>_P.pak`（F-4.3）。
        多个 pak 时 N 依次递增，绝不建子目录。
      * **Steam / Epic**：`[ModName]_P.pak`（F-4.2）。
      * **给了 chunk_id**（= 用户在第 4 章 F-4.8 里调优先级）：
        改用 `Project_Plague-Windows_<N>_P.pak` —— 本机实测用户就是这么干的
        （`~mods` 里躺着 `Project_Plague-Windows_10000_P.pak`、
        `Project_Plague-Windows_999999_9999999_P.pak`），数字越大越后加载、越赢。
      * 一个 mod 含多个 pak 时：优先沿用**包里原本有意义的主名**（例如
        `Weapon_A_P.pak`），没有意义（`pakchunk1200-Windows_P.pak`）才回落到
        「mod 名 + 序号」—— 与名称推导链同一套思路。
    """
    groups = _group_by_stem(files)
    if not groups:
        return []

    # 该游戏要求**保留原文件名**（IoStore 三件套 .pak/.utoc/.ucas 靠主名匹配，
    # 容器内部也可能记着名字 → 改名有掉资源的风险）。只做同名去重，不做规范化。
    # 明末是单 pak 且社区命名 24% 不合规，所以那边保持 False、继续走下面的规范化。
    # ⚠️ 延迟导入（不是模块级）：Cython 编译成 .pyd 后，模块级的跨包常量导入
    #    会与 modstore.pyd 的初始化打架 → 启动即原生崩溃（2026-10-29 实测）。
    from .games import ACTIVE as GAME

    if GAME.preserve_filenames:
        seen: set[str] = set()
        kept: List[Tuple[modstore.ModFile, str]] = []
        for bucket in groups.values():
            for f in bucket:
                name = f.name
                if name in seen:                      # 极少见：压缩包里重名
                    stem, _, ext = name.rpartition(".")
                    name = f"{stem}_dup.{ext}" if ext else f"{name}_dup"
                seen.add(name)
                kept.append((f, name))
        return kept

    ordered = sorted(groups.values(), key=lambda b: b[0].name.lower())
    out: List[Tuple[modstore.ModFile, str]] = []
    used: set[str] = set()
    multi = len(ordered) > 1

    for index, bucket in enumerate(ordered):
        base = _base_name(
            mod_name, bucket[0].name, platform, index=index, count=len(ordered),
            chunk_id=chunk_id, multi=multi,
        )
        base = _uniquify(base, used)
        for f in bucket:
            out.append((f, f"{base}{f.ext}"))
    return out


def _base_name(
    mod_name: str,
    pak_name: str,
    platform: str,
    *,
    index: int,
    count: int,
    chunk_id: Optional[int],
    multi: bool,
) -> str:
    """算主名（**不带后缀**）。"""
    if platform == gamedir.PLATFORM_GAMEPASS:
        # F-4.3：Game Pass 的名字是死的，只能靠编号区分多个 pak
        n = (chunk_id if chunk_id is not None else 4) + index
        return f"{gamedir.UE_PROJECT}-WinGDK_{n}_P"

    if chunk_id is not None:
        # F-4.8：用户手动调优先级 → 走带编号的规范名
        n = chunk_id + index
        return f"{gamedir.UE_PROJECT}-Windows_{n}_P"

    # 只有一个 pak 时优先用用户给 mod 起的名（信息量最大，实测吻合 3.7 的推导链）
    if not multi:
        return f"{gamedir.safe_mod_stem(mod_name)}_P"

    # 多个 pak：先看包内原主名有没有信息量，没有才回落到「mod 名 + 序号」
    stem = _filename_stem_from_pak(pak_name)
    if stem:
        return f"{stem}_P"
    return f"{gamedir.safe_mod_stem(mod_name)}_{index + 1}_P"


def _filename_stem_from_pak(pak_name: str) -> str:
    """从 pak 原主名里得到「能当文件名用的主名」。没信息量则返回空串。

    ⚠️ 这里**不能**直接用 `gamedir.sanitize_mod_name()` 的结果当文件名 ——
    那个函数是给**显示名**用的，会把下划线换成空格
    （`Weapon_A_P.pak` → `Weapon A`），做成文件名就是 `Weapon A_P.pak`，
    空格在目标目录里既难看又容易在命令行里出岔子。
    所以：**用原主名做文件名（保留下划线），只用 sanitize 的结果判断有没有信息量。**
    """
    stem = re.sub(r"_[pP]$", "", _stem_of(pak_name))
    stem = re.sub(r'[<>:"/\\|?*]', "", stem).strip(" .")
    if not stem:
        return ""
    if not gamedir.sanitize_mod_name(stem):
        return ""   # 清洗后一字不剩（pakchunk1200-Windows）→ 交给调用方回落
    return stem


def _uniquify(base: str, used: set[str]) -> str:
    """同一批里主名撞了（两个 pak 清洗后同名）就加序号，避免自己覆盖自己。"""
    candidate = base
    n = 2
    while candidate.lower() in used:
        candidate = f"{base}_{n}"
        n += 1
    used.add(candidate.lower())
    return candidate


# ---------------------------------------------------------------- 计划构建
def plan_install(
    meta: modstore.ModMeta,
    probe: gamedir.ProbeResult,
    store: Optional[modstore.ModStore] = None,
    lib: Optional[library_mod.Library] = None,
    *,
    chunk_id: Optional[int] = None,
) -> InstallPlan:
    """算出「装这个 mod 会发生什么」，**不做任何写入**。

    F-4.6 要求安装前检查目标文件是否已存在。这里把三种情况分开：
      * 已存在、是本工具装的、属于**别的 mod** → 覆盖会让那个 mod 失效（警示）
      * 已存在、**不是**本工具装的 → 用户手工放的，会覆盖别人的东西（警示 + 备份）
      * 已存在、属于**这个 mod 自己**（重装/升级）→ 正常覆盖
    """
    platform = probe.platform or gamedir.PLATFORM_UNKNOWN
    paks_dir = Path(probe.paks_dir) if probe.paks_dir else Path("")
    mods_dir = gamedir.resolve_mods_dir(platform, paks_dir) if probe.paks_dir else Path("")

    plan = InstallPlan(
        mod_id=meta.id,
        mod_name=meta.name,
        game_dir=Path(probe.game_root) if probe.game_root else Path(""),
        paks_dir=paks_dir,
        mods_dir=mods_dir,
        platform=platform,
    )

    if not probe.ok or not probe.paks_dir:
        plan.blockers.append("还没有设置有效的游戏目录，请先在顶部栏点「选择游戏目录」。")
        return plan
    if not meta.files:
        plan.blockers.append(f"「{meta.name}」的库里没有任何文件，可能导入时出错了。")
        return plan

    if store is None:
        store = modstore.ModStore.default()

    resolved = resolve_target_names(meta.name, meta.files, platform, chunk_id=chunk_id)
    if not resolved:
        plan.blockers.append(
            f"「{meta.name}」里没有任何可安装的 pak 文件"
            f"（只认 {', '.join(sorted(DEPLOY_EXTS))}）。"
        )
        return plan

    # 库里所有已部署文件的落点（小写），用来判「这个落点是别人占着的吗」
    tracked = _tracked_dst_index(lib)
    own_tracked = _own_dst_index(lib, meta.id, plan.game_dir)

    files_dir = store.files_dir(meta.id)
    for f, target_name in resolved:
        src = files_dir / f.name
        dst = mods_dir / target_name
        pf = PlannedFile(src=src, dst=dst, original_name=f.name)
        if dst.exists():
            pf.action = "overwrite"
            try:
                pf.existing_size = dst.stat().st_size
            except OSError:
                pf.existing_size = 0
            if dst not in own_tracked:
                pf.foreign = True
                owner = tracked.get(dst)
                if owner and owner != meta.id:
                    plan.warnings.append(
                        f"「{target_name}」已经被本工具装的另一个 mod（{owner}）占用，"
                        "覆盖后那个 mod 会失效。"
                    )
                else:
                    plan.warnings.append(
                        f"「{target_name}」已存在，但不是本工具装的（可能是你手工放的）。"
                        "覆盖前会自动备份，卸载时可以还原。"
                    )
        plan.files.append(pf)

    deployed = {f.name for f in meta.files if f.ext in DEPLOY_EXTS}
    plan.kept_in_library = [f.name for f in meta.files if f.name not in deployed]

    # ---- 装前查重：游戏里已经躺着一份**字节完全相同**的 pak ----
    # 2026-10-11 实测踩到过：库里叫「全果 明军」的 mod，其库内文件原名是
    # `Project_Plague-WinGDK_9001_P.pak`（游戏原版 pak 的名字，作者直接复用了）。
    # 装的时候按 mod 名规范成 `全果 明军_P.pak`，而游戏里本来就躺着一模一样的
    # `Project_Plague-WinGDK_9001_P.pak`（md5 都是 1cd97a26…）—— 于是同一个 mod
    # 在游戏里出现两份，既浪费空间也可能让加载顺序变得难以预期。
    # 判据用**内容 sha256**（不是文件名、不是大小），所以改名也照样能认出来。
    # 只提示不拦截 —— 冲突检测在需求文档里明确是提示（F-4.6 / R-xx）。
    plan.warnings.extend(_duplicate_content_warnings(meta, resolved, files_dir, mods_dir))

    if not Path(probe.paks_dir).is_dir():
        plan.blockers.append(f"游戏 pak 目录不存在：{probe.paks_dir}")
    return plan


def _same_size_name_index(mods_dir: Path) -> Dict[int, List[Path]]:
    """mods 目录下按文件大小归类（只扫一层，pak 都是平铺的）。"""
    out: Dict[int, List[Path]] = {}
    if not mods_dir.is_dir():
        return out
    try:
        entries = list(mods_dir.iterdir())
    except OSError:
        return out
    for p in entries:
        if not p.is_file() or p.suffix.lower() not in DEPLOY_EXTS:
            continue
        try:
            out.setdefault(p.stat().st_size, []).append(p)
        except OSError:
            continue
    return out


def _duplicate_content_warnings(
    meta: modstore.ModMeta,
    resolved: Sequence[Tuple[modstore.ModFile, str]],
    files_dir: Path,
    mods_dir: Path,
) -> List[str]:
    """找出「游戏里已有同内容 pak、但文件名不同」的情况，返回警告文案。"""
    by_sha = {f.sha256: f.name for f in meta.files if f.sha256}
    if not by_sha or not mods_dir.is_dir():
        return []

    # 先按大小筛一遍，避免对每个 pak 都全量算哈希（pak 动辄上百 MB）
    size_index = _same_size_name_index(mods_dir)
    if not size_index:
        return []

    warned: Dict[str, str] = {}     # 游戏里那份 -> 库里这份
    for f, target_name in resolved:
        try:
            src_size = (files_dir / f.name).stat().st_size
        except OSError:
            continue
        candidates = [
            p for p in size_index.get(src_size, [])
            if p.name.lower() != target_name.lower()
        ]
        if not candidates:
            continue
        src_sha = f.sha256
        if not src_sha:
            try:
                src_sha = modstore.hash_file(files_dir / f.name)
            except OSError:
                continue
        for cand in candidates:
            try:
                if modstore.hash_file(cand) != src_sha:
                    continue
            except OSError:
                continue
            warned.setdefault(cand.name, target_name)

    return [
        f"游戏里已经有一份内容完全相同的 pak：{deployed_name}。"
        f"再装一次会让同一个 mod 在游戏里出现两份（文件名不同而已）。"
        f"不需要装的话可以直接跳过。"
        for deployed_name, target_name in warned.items()
    ]


def _norm(p: Path) -> str:
    try:
        return os.path.normcase(str(p.resolve()))
    except OSError:
        return os.path.normcase(str(p))


def _tracked_dst_index(lib: Optional[library_mod.Library]) -> Dict[Path, str]:
    """落点 → mod_id（全部 mod）。"""
    index: Dict[Path, str] = {}
    if lib is None:
        return index
    for record in lib.all():
        for f in record.deployed_files:
            if f.dst:
                index.setdefault(Path(f.dst), record.mod_id)
    return index


def _own_dst_index(lib, mod_id: str, game_dir: Path) -> set:
    """这个 mod 自己在**当前游戏目录**下已部署的落点集合。"""
    if lib is None:
        return set()
    record = lib.find(mod_id, game_dir)
    if record is None:
        return set()
    return {Path(f.dst) for f in record.deployed_files if f.dst}


# ---------------------------------------------------------------- 写权限
def ensure_writable(directory: Path) -> None:
    """真的去建一个探针文件。

    ⚠️ 不能用 `os.access(dir, os.W_OK)`：Windows 上它对只读目录、
    以及对 `C:\\Program Files`（普通用户无权写）都会返回 **True**，
    于是「检查通过 → 复制时炸」变成静默失败 —— 正是 F-4.10 要避免的。
    """
    directory = Path(directory)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GameDirNotWritable(_perm_message(directory, exc)) from exc

    probe = directory / ".wmm-write-probe.tmp"
    try:
        with open(probe, "wb") as fh:
            fh.write(b"wmm")
        probe.unlink()
    except OSError as exc:
        raise GameDirNotWritable(_perm_message(directory, exc)) from exc


def _perm_message(directory: Path, exc: OSError) -> str:
    winerror = getattr(exc, "winerror", None)
    tail = ""
    if winerror == 5 or isinstance(exc, PermissionError):
        tail = (
            "\n常见原因：游戏装在 C:\\Program Files 之类的受保护位置。\n"
            "解决办法：关掉游戏，右键本工具 →「以管理员身份运行」，再试一次。"
        )
    elif winerror == 19:  # ERROR_WRITE_PROTECT
        tail = "\n目标磁盘处于写保护状态，请检查磁盘属性。"
    return f"目标目录写不进去：\n{directory}{tail}\n（系统提示：{exc}）"


# ---------------------------------------------------------------- 备份
@dataclass
class BackupEntry:
    original: str
    stored: str
    size: int = 0

    def to_dict(self) -> dict:
        return {"original": self.original, "stored": self.stored, "size": self.size}


class BackupSet:
    """一次安装的备份。落在 `%APPDATA%\\WuchangModManager\\backups\\<mod_id>\\`。

    为什么备份要带 manifest 而不是镜像目录结构：目标目录里的文件名可能含括号、
    空格、非 ASCII（实测有 `BaiNude_Flat_Clean_p(under.pak`），镜像路径很容易出岔子。
    扁平存 + 清单记原路径最稳，也方便用户自己看。
    """

    def __init__(self, mod_id: str, root: Optional[Path] = None) -> None:
        self.mod_id = mod_id
        self.root = Path(root) if root else apppaths.backups_dir()
        self.dir = self.root / mod_id
        self.entries: List[BackupEntry] = []
        self.created_at = time.strftime("%Y-%m-%d %H:%M:%S")
        #: 本次安装**新建**的目录（回滚时按「空才删」的原则清理）
        self.created_dirs: List[str] = []

    def note_created_dir(self, path: Path) -> None:
        key = str(path)
        if key not in self.created_dirs:
            self.created_dirs.append(key)

    def capture(self, path: Path) -> bool:
        """把即将被覆盖的文件备份下来。返回是否真的备份了。"""
        path = Path(path)
        if not path.is_file():
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        stored = f"{len(self.entries):04d}{path.suffix}"
        target = self.dir / stored
        shutil.copy2(path, target)
        try:
            size = target.stat().st_size
        except OSError:
            size = 0
        self.entries.append(BackupEntry(original=str(path), stored=stored, size=size))
        return True

    @property
    def used(self) -> bool:
        return bool(self.entries) or bool(self.created_dirs)

    def save(self) -> None:
        if not self.used:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        jsonstore.save_json(
            self.dir / BACKUP_MANIFEST,
            {
                "mod_id": self.mod_id,
                "created_at": self.created_at,
                "entries": [e.to_dict() for e in self.entries],
                "created_dirs": list(self.created_dirs),
            },
        )

    @classmethod
    def load(cls, mod_id: str, root: Optional[Path] = None) -> Optional["BackupSet"]:
        base = Path(root) if root else apppaths.backups_dir()
        path = base / mod_id / BACKUP_MANIFEST
        raw = jsonstore.load_json(path, default=None)
        if not isinstance(raw, dict):
            return None
        obj = cls(mod_id, base)
        obj.created_at = str(raw.get("created_at") or "")
        obj.created_dirs = [str(d) for d in (raw.get("created_dirs") or [])]
        for item in raw.get("entries") or []:
            if isinstance(item, dict) and item.get("original") and item.get("stored"):
                obj.entries.append(
                    BackupEntry(
                        original=str(item["original"]),
                        stored=str(item["stored"]),
                        size=int(item.get("size") or 0),
                    )
                )
        return obj

    def restore(
        self, *, only: Optional[set] = None
    ) -> Tuple[List[Path], List[str]]:
        """把备份还原回原位。返回 (还原成功的路径, 问题描述)。

        only（2026-10-13 新增）：允许还原的原始路径集合（`Path` 集合）。
        给了就只还原其中的条目 —— 其余条目**静默跳过**（不算问题）。
        背景：两个 mod 覆盖同一文件时，先卸载的把文件删了，后卸载的若
        无条件还原，会把 capture 到的「对方副本」还回游戏目录，造成
        「全卸载后 ~mods 仍有残留」。只有当落点上现在还是**本 mod 写入的
        那份**时，还原「装我之前的原文件」才是对的。
        """
        restored: List[Path] = []
        problems: List[str] = []
        for entry in self.entries:
            src = self.dir / entry.stored
            target = Path(entry.original)
            if only is not None and target not in only:
                continue
            if not src.is_file():
                problems.append(f"备份文件缺失，无法还原：{entry.original}")
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                restored.append(target)
            except OSError as exc:
                problems.append(f"还原失败 {entry.original}：{exc}")
        return restored, problems

    def discard(self) -> None:
        """备份已无意义（例如用户确认不要了）时清掉。**

        只删 `<backups>/<mod_id>/` 这一个目录，且要求它严格在 backups 根之下。
        """
        base = self.root.resolve()
        target = self.dir.resolve()
        if target == base or base not in target.parents:
            return
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------- 安装
@dataclass
class InstallResult:
    ok: bool
    message: str
    written: List[Path] = field(default_factory=list)
    backed_up: List[Path] = field(default_factory=list)
    plan: Optional[InstallPlan] = None
    record: Optional[library_mod.InstalledMod] = None
    warnings: List[str] = field(default_factory=list)


def install(
    meta: modstore.ModMeta,
    probe: gamedir.ProbeResult,
    store: Optional[modstore.ModStore] = None,
    lib: Optional[library_mod.Library] = None,
    *,
    plan: Optional[InstallPlan] = None,
    chunk_id: Optional[int] = None,
    backup_root: Optional[Path] = None,
) -> InstallResult:
    """真正把文件搬进游戏目录。

    顺序（这条顺序不能改，见模块开头第 4 条）：
        校验可写 → 建目录 → 备份被覆盖的 → 复制 → 落库
    """
    if store is None:
        store = modstore.ModStore.default()
    if lib is None:
        lib = library_mod.Library.load()
    if plan is None:
        plan = plan_install(meta, probe, store, lib, chunk_id=chunk_id)

    if not plan.ok:
        return InstallResult(
            ok=False,
            message="\n".join(plan.blockers) or "这个 mod 没有可安装的文件。",
            plan=plan,
            warnings=list(plan.warnings),
        )

    # 缺文件要早发现 —— 库里 meta.json 说有、files/ 里却没有，属于坏记录
    missing = [pf.src for pf in plan.files if not pf.src.is_file()]
    if missing:
        listing = "\n".join(f"  · {p.name}" for p in missing[:8])
        return InstallResult(
            ok=False,
            message=(
                "这些文件在 mod 库里找不到了，可能是库目录被移动或手工删过：\n"
                f"{listing}\n建议：把这个 mod 删掉后重新导入。"
            ),
            plan=plan,
            warnings=list(plan.warnings),
        )

    # ⚠️ 必须在 ensure_writable **之前**判断目录是否存在 —— ensure_writable 本身
    # 就会 mkdir 把目录建出来，之后再看就永远"已存在"，于是永远记不下
    # 「这个目录是本次安装新建的」，卸载时就清不掉空目录了（踩过）。
    mods_dir_existed = plan.mods_dir.exists()

    try:
        ensure_writable(plan.mods_dir)
    except GameDirNotWritable as exc:
        return InstallResult(ok=False, message=str(exc), plan=plan, warnings=list(plan.warnings))

    backup = BackupSet(meta.id, backup_root)
    if plan.mods_dir != plan.paks_dir and not mods_dir_existed:
        backup.note_created_dir(plan.mods_dir)

    written: List[Path] = []
    try:
        for pf in plan.files:
            if pf.dst.parent != plan.mods_dir and not pf.dst.parent.exists():
                backup.note_created_dir(pf.dst.parent)
                pf.dst.parent.mkdir(parents=True, exist_ok=True)
            if pf.is_overwrite:
                backup.capture(pf.dst)
            shutil.copy2(pf.src, pf.dst)
            written.append(pf.dst)
    except OSError as exc:
        # 半途失败：把刚写进去的收回来，别留一堆散文件
        for path in written:
            try:
                path.unlink()
            except OSError:
                pass
        return InstallResult(
            ok=False,
            message=f"复制文件时出错，已回退本次写入：\n{exc}",
            plan=plan,
            warnings=list(plan.warnings),
        )

    backup.save()

    record = library_mod.InstalledMod(
        mod_id=meta.id,
        mod_name=meta.name,
        game_dir=str(plan.game_dir),
        platform=plan.platform,
        deployed_files=[
            library_mod.DeployedFile(
                src=str(pf.src), dst=str(pf.dst), original_name=pf.original_name
            )
            for pf in plan.files
        ],
        backup_dir=str(backup.dir) if backup.used else "",
    )
    lib.record_install(record)
    lib.save()

    return InstallResult(
        ok=True,
        message=f"已安装「{meta.name}」：{plan.summary()}。",
        written=written,
        backed_up=[Path(e.original) for e in backup.entries],
        plan=plan,
        record=record,
        warnings=list(plan.warnings),
    )


# ---------------------------------------------------------------- 卸载
@dataclass
class UninstallResult:
    ok: bool
    message: str
    deleted: List[Path] = field(default_factory=list)
    missing: List[Path] = field(default_factory=list)
    restored: List[Path] = field(default_factory=list)
    removed_dirs: List[Path] = field(default_factory=list)
    refused: List[str] = field(default_factory=list)
    #: 还原文件的「同源说明」（用户 2026-10-12）：
    #: `{还原路径: "与你库中的「X」内容相同"}`。空 = 没配 same_as_lookup
    #: 或没匹配上。UI 用它回答用户那句「这文件是我自己放的那个吗」——
    #: 内容与库里某个 mod 一致时，至少能说清"它和库里的 X 是同一份东西"。
    restored_notes: Dict[str, str] = field(default_factory=dict)


def _dst_intact(dst: Path, src: Optional[Path]) -> bool:
    """dst 上现在的文件，是否还是**本 mod 写入的那份**（2026-10-13 新增）。

    依据：dst 与 mod 库内源文件（`DeployedFile.src`）内容一致（先比大小
    再比 sha256）。这是「卸载时是否允许还原备份」的判据 —— 不是本 mod
    的文件（被别的 mod 覆盖过 / 已被别的 mod 的卸载删掉 / 用户换过），
    就不该把 capture 到的旧内容还回去。

    失败方向刻意保守：源文件没了、文件读不了 → 当作 intact（退回旧行为，
    宁可多还原也不能吞掉用户的原文件）。
    """
    if src is None or not src.is_file():
        return True
    if not dst.is_file():
        return False
    try:
        if dst.stat().st_size != src.stat().st_size:
            return False
        return modstore.hash_file(dst) == modstore.hash_file(src)
    except OSError:
        return True


def _intact_dst_paths(record: library_mod.InstalledMod, wanted: set) -> set:
    """卸载前体检：record 里哪些落点仍是本 mod 自己写入的内容。

    只查 `wanted`（备份清单里出现的路径）—— 没被 capture 过的文件无所谓
    还原不还原，不为它们白算哈希。返回 `Path` 集合（与备份清单里的
    `original` 同源，可直接比对）。
    """
    intact: set = set()
    for f in record.deployed_files:
        if not f.dst or Path(f.dst) not in wanted:
            continue
        dst = Path(f.dst)
        src = Path(f.src) if f.src else None
        if _dst_intact(dst, src):
            intact.add(dst)
    return intact


def uninstall(
    lib: library_mod.Library,
    mod_id: str,
    game_dir: str | Path,
    *,
    dry_run: bool = False,
    backup_root: Optional[Path] = None,
    same_as_lookup: Optional[Callable[[str], Optional[Tuple[str, str]]]] = None,
) -> UninstallResult:
    """按安装记录卸载（F-4.4 / F-4.5）。

    🔒 三条硬保险：
      1. 文件清单**只来自 library.json 的 `dst`**，不做任何通配符匹配；
      2. 每条 `dst` 必须落在记录的 `game_dir` 之下，否则**拒绝删**并报出来
         —— 防止用户手改过的 library.json 把删除指到别处；
      3. 删空目录用 `os.rmdir()`，非空就抛错跳过（天然不会误删别人的东西）。

    参数
    ----
    same_as_lookup
        `sha256 -> (mod_id, mod_name)` 的查询函数（`wildscan.lookup_mod`
        包一层即可）。给了就能在还原提示里注明「这个文件与你库中的
        「X」内容相同」—— 用户 2026-10-12：「如何避免这种情况出现」的
        配套改进：还原不再只是个路径，而是能说清"它是什么"。
        不传 = 不加这段（保持旧行为），核心卸载逻辑不受影响。
    """
    record = lib.find(mod_id, game_dir)
    if record is None:
        return UninstallResult(ok=False, message="没有找到这个 mod 在当前游戏目录下的安装记录。")

    root = Path(record.game_dir or game_dir)
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root

    deleted: List[Path] = []
    missing: List[Path] = []
    refused: List[str] = []
    # ⚠️ 下面四个在 Cython 编译（0.1.25 防倒卖）时只能声明**一次** ——
    #    同函数两处带注解的重声明 Cython 直接报 redeclared。语义不变：
    #    dry_run 分支与主路径各赋一次新 dict/list，本来就是两次调用。
    notes: Dict[str, str] = {}
    restored: List[Path] = []
    problems: List[str] = []
    skipped_restore: List[Path] = []

    # 备份清单与「落点体检」必须在**删除之前**做 —— 体检要看的是删除前的
    # 现场状态（2026-10-13 修复跨 mod 覆盖残留）。
    backup = BackupSet.load(mod_id, backup_root)
    intact: set = set()
    if backup is not None and backup.entries:
        wanted = {Path(e.original) for e in backup.entries}
        intact = _intact_dst_paths(record, wanted)

    for f in record.deployed_files:
        if not f.dst:
            continue
        path = Path(f.dst)
        # 保险 2：落点必须在记录的游戏目录之下
        try:
            resolved = path.resolve()
        except OSError:
            refused.append(f"{path}（路径无法解析）")
            continue
        if root_resolved != resolved.parent and root_resolved not in resolved.parents:
            refused.append(f"{path}（不在游戏目录 {root} 之下，已跳过）")
            continue

        if not path.exists():
            missing.append(path)
            continue
        if dry_run:
            deleted.append(path)
            continue
        try:
            path.unlink()
            deleted.append(path)
        except OSError as exc:
            refused.append(f"{path}（{exc}）")

    if dry_run:
        # 预告：这次卸载会把哪些「被覆盖的原文件」还原回原位。
        # 只读备份清单（不落盘、不还原）。
        # 用户 2026-10-12：「手动放的 mod 被管理器二次安装后卸载，文件被删一次
        # 再复原一次， silently 发生最吓人」—— 确认框要能提前说清会还原谁。
        # 2026-10-13：预告同样按「落点体检」过滤 —— 落点已不是本 mod 的文件
        # 的条目，真实卸载时也不会还原，预告里就不能出现（否则吓人+失真）。
        will_restore: List[Path] = []
        notes = {}
        if backup is not None:
            will_restore = [
                Path(e.original) for e in backup.entries if Path(e.original) in intact
            ]
            if same_as_lookup is not None:
                for e in backup.entries:
                    if Path(e.original) not in intact:
                        continue
                    hit = _same_as(backup.dir / e.stored, same_as_lookup)
                    if hit is not None:
                        notes[str(Path(e.original))] = f"与你库中的「{hit[1]}」内容相同"
        message = f"将删除 {len(deleted)} 个文件（预览，未真正删除）。"
        if will_restore:
            message += (
                f"\n\n其中 {len(will_restore)} 个被这个 mod 覆盖过的原文件"
                "会被还原回原位（确认框里有完整清单）。"
            )
        return UninstallResult(
            ok=True,
            message=message,
            deleted=deleted,
            missing=missing,
            refused=refused,
            restored=will_restore,
            restored_notes=notes,
        )

    # 还原被覆盖的别人的文件（2026-10-13 起：只还原「落点仍是本 mod 那份」的条目）
    notes = {}
    restored = []
    problems = []
    skipped_restore = []
    if backup is not None:
        restored, problems = backup.restore(only=intact)
        skipped_restore = [
            Path(e.original)
            for e in backup.entries
            if Path(e.original) not in intact
        ]
        refused.extend(problems)
        # ⚠️ 备份清理分三档（宁可多留也不能吞用户的原文件）：
        #   ① 全部条目都已还原 → 整目录丢弃（原行为）；
        #   ② 有条目被「落点体检」跳过 → 只修剪「已还原的」和「确认与库中
        #      某 mod 相同（可随时从库再生）的」；证明不了再生的**必须留**
        #      —— 那可能是用户唯一的原文件；
        #   ③ 有还原失败（problems 非空）→ 什么都不删（原行为）。
        if not problems:
            restored_set = set(restored)
            still_kept: List = []
            for e in backup.entries:
                if Path(e.original) in restored_set:
                    continue  # 已还原，使命完成
                if same_as_lookup is not None and _same_as(
                    backup.dir / e.stored, same_as_lookup
                ) is not None:
                    continue  # 内容与库中某 mod 相同，随时可再生，丢弃无害
                still_kept.append(e)
            backup.entries = still_kept
            if backup.entries or backup.created_dirs:
                backup.save()
            else:
                backup.discard()
        if same_as_lookup is not None:
            for p in restored:
                hit = _same_as(p, same_as_lookup)
                if hit is not None:
                    notes[str(p)] = f"与你库中的「{hit[1]}」内容相同"

    # 清掉本次安装新建的空目录（rmdir 非空会抛错 —— 这是刻意的保险）
    #
    # ⚠️ 但 **mod 目录本身（~mods）永远不删**（2026-10-29 用户反馈：
    #    「卸载 mod 时，直接把游戏中的 ~mods 文件夹卸载了」）。
    #    ~mods 是第一次装 mod 时由本工具新建的，按「恢复原状」删掉它技术上
    #    没错，但用户看到的画面是「文件夹整个没了」，会以为出了事故；
    #    而且下次往里面拖 mod 还得先手动建目录。它是 mod 的家，留着。
    from .games import ACTIVE as _GAME   # 延迟导入，避免 .pyd 初始化冲突

    _keep_dirs = {_GAME.mods_folder.lower()}
    removed_dirs: List[Path] = []
    if backup is not None:
        for raw in sorted(backup.created_dirs, key=len, reverse=True):
            d = Path(raw)
            if d.name.lower() in _keep_dirs:
                continue
            try:
                d.rmdir()
                removed_dirs.append(d)
            except OSError:
                pass  # 非空 / 不存在 —— 两种情况都不该动它

    lib.remove(mod_id, game_dir)
    lib.save()

    # 卸完的提示必须**点名**还原了谁 —— 用户 2026-10-12 的诉求：
    # 「告知用户有手动安装的 mod 被复原」。只说「还原 N 个文件」等于没说，
    # 用户看到游戏目录里冒出文件 still 不知道是谁。
    # 配上 `restored_notes`（这个文件与库中哪个 mod 内容相同）还能回答
    # 「这是我放进去的那个吗」—— 内容一致至少说明"它和库里的 X 是同一份"。
    message = f"已卸载：删除 {len(deleted)} 个文件。"
    if restored:
        message += (
            f"\n\n有 {len(restored)} 个被这个 mod 覆盖过的原文件已还原回原位：\n"
            + "\n".join(
                f"  {p}" + (f"（{notes[str(p)]}）" if str(p) in notes else "")
                for p in restored
            )
            + "\n这些文件在装这个 mod 之前就在游戏目录里；"
            "如果你没手动放过，可能是更早的安装留下的。"
        )
    if skipped_restore:
        # 2026-10-13：落点体检没过的备份不还原。必须说出口 —— 不还原也
        # 不许悄悄发生（与「还原必须可见」同一原则）。
        message += (
            f"\n\n另有 {len(skipped_restore)} 个旧备份没有还原："
            "这些位置当前已经不是本 mod 写入的文件"
            "（可能被其他 mod 覆盖或已随其他 mod 卸载删除），"
            "原样还原反而会留下残留。"
        )
    if missing:
        message += f"\n\n另有 {len(missing)} 个文件此前已不在磁盘上（可能被手工删过）。"
    if refused:
        message += f"\n\n有 {len(refused)} 项被跳过：\n" + "\n".join(
            f"  {r}" for r in refused[:5]
        )
    return UninstallResult(
        ok=True,
        message=message,
        deleted=deleted,
        missing=missing,
        restored=restored,
        removed_dirs=removed_dirs,
        refused=refused,
        restored_notes=notes,
    )


def _same_as(
    path: Path,
    lookup: Callable[[str], Optional[Tuple[str, str]]],
) -> Optional[Tuple[str, str]]:
    """算一个文件的 sha256，问 lookup「库里的哪个 mod 和它内容相同」。

    读不了（文件被占 / 已消失）就返回 None —— 同源标注是**附加信息**，
    不能因为它让卸载失败。
    """
    try:
        sha = modstore.hash_file(path)
    except OSError:
        return None
    try:
        return lookup(sha)
    except Exception:  # noqa: BLE001 - lookup 是 UI 传进来的，出错不能拖垮卸载
        return None


def rollback(
    lib: library_mod.Library,
    mod_id: str,
    game_dir: str | Path,
    *,
    backup_root: Optional[Path] = None,
) -> UninstallResult:
    """回滚 = 卸载 + 把备份还原回原位（F-4.9）。

    与 `uninstall()` 的差别只是措辞：卸载是「我不想要这个 mod 了」，
    回滚是「装完之后游戏坏了，给我恢复原样」。
    """
    result = uninstall(lib, mod_id, game_dir, backup_root=backup_root)
    if result.ok:
        result.message = result.message.replace("已卸载：", "已回滚：")
    return result


# ---------------------------------------------------------------- 批量
def install_many(
    metas: Iterable[modstore.ModMeta],
    probe: gamedir.ProbeResult,
    store: Optional[modstore.ModStore] = None,
    lib: Optional[library_mod.Library] = None,
    *,
    backup_root: Optional[Path] = None,
) -> List[InstallResult]:
    """批量安装（F-4.7）。逐个装，单个失败不影响后面的 —— 并汇总给用户看。"""
    if store is None:
        store = modstore.ModStore.default()
    if lib is None:
        lib = library_mod.Library.load()
    return [install(m, probe, store, lib, backup_root=backup_root) for m in metas]


def uninstall_many(
    pairs: Iterable[Tuple[str, str | Path]],
    lib: library_mod.Library,
    *,
    backup_root: Optional[Path] = None,
) -> List[UninstallResult]:
    """批量卸载（F-4.7）。"""
    return [
        uninstall(lib, mod_id, game_dir, backup_root=backup_root)
        for mod_id, game_dir in pairs
    ]


# ---------------------------------------------------------------- 辅助
def scan_deployed(mobs_dir: Path) -> Dict[str, int]:
    """扫目标目录，返回 `{文件名(小写): 字节数}`。

    给冲突检测用：判断某个落点是不是已经**不归本工具管**地存在了。
    """
    out: Dict[str, int] = {}
    directory = Path(mobs_dir)
    if not directory.is_dir():
        return out
    for child in directory.rglob("*"):
        if not child.is_file():
            continue
        if child.suffix.lower() not in DEPLOY_EXTS:
            continue
        try:
            out[child.name.lower()] = child.stat().st_size
        except OSError:
            out[child.name.lower()] = 0
    return out


def is_vanilla_pak(name: str) -> bool:
    """游戏自带的主 pak（不是 mod）。

    实测主 Paks 下有 `Project_Plague-Windows.pak`（44.7 GB）、
    `Project_Plague-Windows_0_P.pak`（15.1 GB）等 —— 冲突检测和
    「未托管的文件」提示都必须把它们排除，否则用户会看到一堆噪音。
    """
    stem = os.path.splitext(name)[0].lower()
    if not stem.startswith(VANILLA_PAK_PATTERN):
        return False
    # 只有「Project_Plague-Windows」或「Project_Plague-Windows_<纯数字>[_<纯数字>]」
    rest = stem[len(VANILLA_PAK_PATTERN):].lstrip("-_")
    if not rest:
        return True
    if rest.endswith("_p"):
        rest = rest[:-2]
    parts = [p for p in rest.split("_") if p]
    return bool(parts) and all(p.isdigit() for p in parts)
