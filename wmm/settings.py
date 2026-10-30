# -*- coding: utf-8 -*-
"""settings.json —— 游戏路径与界面偏好（需求文档 3.3 / 4 / 10.5b）。

存放位置：%APPDATA%\\WuchangModManager\\settings.json（**不是程序目录**）。

设计取舍：
  - 结构变化靠 `schema_version` + `_migrate()`，不做「读不出来就重置」这种粗暴处理。
  - 这里**故意没有**任何遥测 / 上报开关。需求文档 R-13 / F-10.18 明确要求纯本地、
    不做任何统计上报——加一个「是否允许上报」的开关本身就是隐性上报意愿的表达，
    所以不设该字段。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import apppaths, gamedir, jsonstore

SCHEMA_VERSION = 1


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class GameDirEntry:
    """一个已记住的游戏目录（需求文档 F-3.6：支持多平台/多版本并存）。"""

    path: str
    platform: str = gamedir.PLATFORM_UNKNOWN
    paks_dir: str = ""
    mods_dir: str = ""
    matched_rule: str = ""
    label: str = ""
    added_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "GameDirEntry":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known})

    @classmethod
    def from_probe(cls, probe: gamedir.ProbeResult, label: str = "") -> "GameDirEntry":
        assert probe.game_root is not None and probe.paks_dir is not None
        return cls(
            path=str(probe.game_root),
            platform=probe.platform,
            paks_dir=str(probe.paks_dir),
            mods_dir=str(gamedir.resolve_mods_dir(probe.platform, probe.paks_dir)),
            matched_rule=probe.matched_rule,
            label=label,
        )


def _default_data() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "show_splash": True,           # 需求文档 F-10.5b
        "ui": {
            "layout": "grid",          # grid | list（F-1.4）
            "status_filter": "all",    # all | installed | not_installed | conflict（F-1.5）
            "window_width": None,      # None = 用 wmm.version 里的最小尺寸
            "window_height": None,
        },
        "mods_library_dir": str(apppaths.mods_dir()),
        "scan_dirs": [],               # 需求文档 F-2.4「扫描目录」记住的来源
        "game_dirs": [],
        "active_game_dir": "",
    }


class Settings:
    """settings.json 的门面。所有变更改内存副本，显式调 save() 落盘。"""

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None) -> None:
        self._data = data
        self.path = Path(path) if path else apppaths.settings_path()

    # ------------------------------------------------------------ 生命周期
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        p = Path(path) if path else apppaths.settings_path()
        raw = jsonstore.load_json(p, default=None, validator=_looks_like_settings)
        if raw is None:
            raw = _default_data()
        else:
            raw = _merge_defaults(_default_data(), raw)
            _migrate(raw)
        return cls(raw, p)

    def save(self) -> None:
        self._data["schema_version"] = SCHEMA_VERSION
        jsonstore.save_json(self.path, self._data)

    # ------------------------------------------------------------ 通用访问
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    # ------------------------------------------------------------ 界面偏好
    @property
    def ui(self) -> Dict[str, Any]:
        self._data.setdefault("ui", _default_data()["ui"])
        return self._data["ui"]

    @property
    def show_splash(self) -> bool:
        return bool(self._data.get("show_splash", True))

    @show_splash.setter
    def show_splash(self, value: bool) -> None:
        self._data["show_splash"] = bool(value)

    # ------------------------------------------------------------ mod 库位置
    @property
    def mods_library_dir(self) -> Path:
        raw = self._data.get("mods_library_dir") or str(apppaths.mods_dir())
        return Path(raw)

    @mods_library_dir.setter
    def mods_library_dir(self, value: str | Path) -> None:
        self._data["mods_library_dir"] = str(value)

    @property
    def scan_dirs(self) -> List[str]:
        return list(self._data.get("scan_dirs") or [])

    def add_scan_dir(self, value: str | Path) -> None:
        s = str(value)
        if s not in self.scan_dirs:
            self._data.setdefault("scan_dirs", []).append(s)

    # ------------------------------------------------------------ 游戏目录
    @property
    def game_dirs(self) -> List[GameDirEntry]:
        out: List[GameDirEntry] = []
        for raw in self._data.get("game_dirs") or []:
            if isinstance(raw, dict):
                try:
                    out.append(GameDirEntry.from_dict(raw))
                except TypeError:
                    continue
        return out

    def find_game_dir(self, path: str | Path) -> Optional[GameDirEntry]:
        target = Path(path)
        for entry in self.game_dirs:
            if gamedir.same_path(Path(entry.path), target):
                return entry
        return None

    def remember_game_dir(
        self,
        probe: gamedir.ProbeResult,
        label: str = "",
        *,
        make_active: bool = True,
    ) -> GameDirEntry:
        """把一次成功的校验结果记进设置。同一路径重复校验则就地更新，不重复添加。"""
        if not probe.ok:
            raise ValueError("校验未通过的游戏目录不应被记住")

        entry = GameDirEntry.from_probe(probe, label=label)
        existing = self.find_game_dir(entry.path)
        if existing is not None:
            entry.added_at = existing.added_at  # 保留首次添加时间
            self._data["game_dirs"] = [
                entry.to_dict() if gamedir.same_path(Path(e.path), Path(entry.path)) else e.to_dict()
                for e in self.game_dirs
            ]
        else:
            self._data.setdefault("game_dirs", []).append(entry.to_dict())

        if make_active:
            self._data["active_game_dir"] = entry.path
        return entry

    def forget_game_dir(self, path: str | Path) -> bool:
        target = Path(path)
        before = len(self.game_dirs)
        self._data["game_dirs"] = [
            e.to_dict() for e in self.game_dirs if not gamedir.same_path(Path(e.path), target)
        ]
        removed = len(self.game_dirs) != before
        if removed and self._data.get("active_game_dir") and gamedir.same_path(
            Path(self._data["active_game_dir"]), target
        ):
            entries = self.game_dirs
            self._data["active_game_dir"] = entries[0].path if entries else ""
        return removed

    @property
    def active_entry(self) -> Optional[GameDirEntry]:
        raw = self._data.get("active_game_dir") or ""
        if not raw:
            return None
        return self.find_game_dir(raw)

    @property
    def active_game_dir(self) -> Optional[Path]:
        entry = self.active_entry
        return Path(entry.path) if entry else None

    def set_active_game_dir(self, path: str | Path) -> bool:
        if self.find_game_dir(path) is None:
            return False
        self._data["active_game_dir"] = str(Path(path))
        return True


# ---------------------------------------------------------------- 内部
def _looks_like_settings(data: Any) -> bool:
    return isinstance(data, dict) and "schema_version" in data


def _merge_defaults(defaults: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
    """用默认值补齐缺失键，保留用户已有值（含未知键，便于版本回退）。"""
    merged = dict(defaults)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_defaults(merged[key], value)
        else:
            merged[key] = value
    return merged


def _migrate(data: Dict[str, Any]) -> None:
    """老版本 settings.json 的升级入口。当前只有 v1，占位。"""
    version = int(data.get("schema_version") or 1)
    if version < SCHEMA_VERSION:
        data["schema_version"] = SCHEMA_VERSION
