# -*- coding: utf-8 -*-
"""当前激活的游戏配置（本项目 = 影之刃零专用）。

这个项目是从明末管理器（`D:\\work缓存\\WuchangModManager`）复制出来的独立副本，
两者**互不影响**：各自的数据目录、品牌名、打包产物都是分开的。

默认激活影之刃零；保留 `WMM_GAME` 环境变量只是为了内部对照调试
（比如想用明末配置跑一遍看内核有没有退化）。
"""

from __future__ import annotations

import os

from . import phantom_blade, wuchang
from .base import GameProfile, mods_rel_path

_PROFILES: dict[str, GameProfile] = {
    "wuchang": wuchang.PROFILE,
    "phantom_blade": phantom_blade.PROFILE,
}

#: 本项目默认就是影之刃零
DEFAULT_GAME = "phantom_blade"

_ACTIVE_KEY = os.environ.get("WMM_GAME", DEFAULT_GAME).strip().lower()

#: 当前进程使用的游戏配置
ACTIVE: GameProfile = _PROFILES.get(_ACTIVE_KEY, phantom_blade.PROFILE)

__all__ = ["ACTIVE", "DEFAULT_GAME", "GameProfile", "mods_rel_path", "_PROFILES"]
