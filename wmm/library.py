# -*- coding: utf-8 -*-
"""library.json —— 工具的索引与**安装记录**（需求文档 3.4 / 第 4 章）。

为什么必须有这份记录（文档 F-4.5 原文）：
  **pak 会被改名以调整优先级，没有记录就无法正确卸载。**
所以每条安装记录要同时记住三样东西：
  - `src`           : mod 库里那份原始文件
  - `dst`           : 实际装到游戏目录的那个路径（可能已改名）
  - `original_name` : 原始文件名

卸载时以 `dst` 为准逐条删除，不去猜、不做任何模糊匹配。

存放位置：%APPDATA%\\WuchangModManager\\library.json（**不是程序目录**）。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import apppaths, gamedir, jsonstore

SCHEMA_VERSION = 1


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class DeployedFile:
    """一个被部署到游戏目录的文件。"""

    src: str = ""             # mod 库里的来源
    dst: str = ""             # 游戏目录里的落点（已改名）
    original_name: str = ""   # 原始文件名（调优先级改名后靠它追溯）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "DeployedFile":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class InstalledMod:
    """一条安装记录。"""

    mod_id: str
    mod_name: str = ""
    game_dir: str = ""
    platform: str = gamedir.PLATFORM_UNKNOWN
    installed_at: str = field(default_factory=_now)
    deployed_files: List[DeployedFile] = field(default_factory=list)
    #: 安装前被覆盖/新增的记录，支持回滚（需求文档 F-4.9）
    backup_dir: str = ""
    #: 预留给 v2 的 pak 清单解析结果（自动算出 overrides，需求文档 3.5 / 第 5 章）
    pak_manifest: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["deployed_files"] = [f.to_dict() for f in self.deployed_files]
        return d

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "InstalledMod":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in raw.items() if k in known}
        files = kwargs.pop("deployed_files", []) or []
        obj = cls(**kwargs)
        obj.deployed_files = [
            DeployedFile.from_dict(f) if isinstance(f, dict) else f for f in files
        ]
        return obj

    @property
    def deployed_paths(self) -> List[Path]:
        return [Path(f.dst) for f in self.deployed_files if f.dst]


def _default_data() -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "installed": []}


class Library:
    """library.json 的门面。"""

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None) -> None:
        self._data = data
        self.path = Path(path) if path else apppaths.library_path()

    # ------------------------------------------------------------ 生命周期
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Library":
        p = Path(path) if path else apppaths.library_path()
        raw = jsonstore.load_json(p, default=None, validator=_looks_like_library)
        if raw is None:
            raw = _default_data()
        else:
            raw.setdefault("schema_version", SCHEMA_VERSION)
            raw.setdefault("installed", [])
        return cls(raw, p)

    def save(self) -> None:
        jsonstore.save_json(self.path, self._data)

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    # ------------------------------------------------------------ 查询
    def all(self) -> List[InstalledMod]:
        out: List[InstalledMod] = []
        for raw in self._data.get("installed") or []:
            if isinstance(raw, dict):
                try:
                    out.append(InstalledMod.from_dict(raw))
                except TypeError:
                    continue
        return out

    def for_game(self, game_dir: str | Path) -> List[InstalledMod]:
        """同一份 mod 库可以对应多个游戏目录（多平台并存），所以按目录过滤。"""
        target = Path(game_dir)
        return [m for m in self.all() if m.game_dir and gamedir.same_path(Path(m.game_dir), target)]

    def find(self, mod_id: str, game_dir: str | Path) -> Optional[InstalledMod]:
        target = Path(game_dir)
        for m in self.all():
            if m.mod_id == mod_id and m.game_dir and gamedir.same_path(Path(m.game_dir), target):
                return m
        return None

    def is_installed(self, mod_id: str, game_dir: str | Path) -> bool:
        return self.find(mod_id, game_dir) is not None

    def installed_ids(self, game_dir: str | Path) -> set[str]:
        return {m.mod_id for m in self.for_game(game_dir)}

    # ------------------------------------------------------------ 写入
    def record_install(self, record: InstalledMod) -> InstalledMod:
        """记录一次安装。同一 (mod_id, game_dir) 已存在则覆盖。不自动落盘。"""
        kept = [
            m.to_dict()
            for m in self.all()
            if not (
                m.mod_id == record.mod_id
                and m.game_dir
                and gamedir.same_path(Path(m.game_dir), Path(record.game_dir))
            )
        ]
        kept.append(record.to_dict())
        self._data["installed"] = kept
        return record

    def remove(self, mod_id: str, game_dir: str | Path) -> bool:
        target = Path(game_dir)
        before = len(self.all())
        self._data["installed"] = [
            m.to_dict()
            for m in self.all()
            if not (m.mod_id == mod_id and m.game_dir and gamedir.same_path(Path(m.game_dir), target))
        ]
        return len(self.all()) != before

    # ------------------------------------------------------------ 卸载
    def uninstall_plan(self, mod_id: str, game_dir: str | Path) -> List[Path]:
        """算出卸载要删除的文件清单。

        **只按记录里的 dst 逐条返回，绝不按通配符或目录整体删除。**
        原因：目标目录（~mods 里）可能同时躺着别的 mod 和用户手工放进去的东西，
              `-Recurse` 或通配符删除会误伤。
        """
        record = self.find(mod_id, game_dir)
        if record is None:
            return []
        return record.deployed_paths

    def missing_files(self, mod_id: str, game_dir: str | Path) -> List[Path]:
        """记录里存在、但磁盘上已经不在的文件（用户手工删过 / 被其他工具动过）。"""
        return [p for p in self.uninstall_plan(mod_id, game_dir) if not p.exists()]


# ---------------------------------------------------------------- 内部
def _looks_like_library(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("installed", []), list)


def count_by_game(library: Library) -> Dict[str, int]:
    """排障用的统计：每个游戏目录下已装多少个 mod。"""
    counts: Dict[str, int] = {}
    for m in library.all():
        counts[m.game_dir] = counts.get(m.game_dir, 0) + 1
    return counts


def iter_deployed_paths(library: Library) -> Iterable[Path]:
    for m in library.all():
        for f in m.deployed_files:
            if f.dst:
                yield Path(f.dst)
