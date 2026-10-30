# -*- coding: utf-8 -*-
"""Mod Enabler 已装检测（需求文档 2.5 / R-3）。

## 这个文件是干什么的

明末的 mod 要生效，必须有一个「前置补丁」绕过 pak 签名校验，通常叫
**Wuchang Mod Enabler**（作者 FrancisLouis）。没有它，`~mods` 里的 pak
**一个都不会加载**，而且游戏不会报错 —— 这是最坑的一种失败：
用户以为装好了，进游戏发现没变化。

所以工具必须在启动时检测一下，没装就明确告诉用户「你得先装这个」。

## 判据（实测校准过，别照抄网上说法）

Mod Enabler 的实物长这样：
    <游戏根>\\Project_Plague\\Binaries\\Win64\\
        d3d9.dll            ← 代理 DLL
        dsound.dll          ← 代理 DLL
        bitfix\\sig.lua      ← 把签名校验函数 patch 成直接 RET（写 0xC3）

⚠️ **本机实测的关键纠正（2026-10-09）**：
    作者发布的包里 `d3d9.dll` 与 `dsound.dll` **是同一个二进制**
    （都是 1,210,880 字节，MD5 均为 `f6bb7b32e61870a7bc71b247ddad56c7`），
    **不需要两个都放，放哪一个都行** —— 取决于游戏实际加载哪个代理名。
    本机真实游戏目录里就只有 `dsound.dll`，没有 `d3d9.dll`。

    所以判据**不能**写成「d3d9.dll 与 dsound.dll **都**存在」——
    那样会把一台明明装好了的机器判成"没装"（本机就是这么被误判的）。
    正确判据是：**任一代理 DLL 存在** 且 `bitfix\\sig.lua` 存在。

    另外注意区分：RTX 帧生成解锁工具用的是 `winmm.dll` 代理，与 Mod Enabler 无关，
    不能拿它当证据。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import gamedir
from .games import ACTIVE as GAME

#: Mod Enabler 会用的代理 DLL 名（实测同一个二进制，放哪个都行）
#: 值来自游戏配置：不需要前置的游戏这里是空元组。
PROXY_NAMES = GAME.enabler_dll_names

#: 它放的补丁脚本（配置为空时视为没有该判据）
SIG_LUA_RELPATH = tuple(GAME.enabler_marker.split("/")) if GAME.enabler_marker else ()

#: `sig.lua` 里必然出现的特征串 —— 用来确认这个 lua 真的是签名绕过，
#: 而不是用户随手放进去的别的脚本。两个特征都不出现才判可疑。
#: 特征串来自游戏配置。**空元组 = 该游戏的判据是二进制插件，不校验内容**
SIG_LUA_MARKERS = GAME.enabler_markers

#: 下载页（只做提示 + 让用户自己去下；**工具不内置、不代下载** —— 分发红线）
DOWNLOAD_HINT = GAME.enabler_hint


@dataclass
class EnablerStatus:
    installed: bool
    bin_dir: Path
    proxies_found: List[str] = field(default_factory=list)
    sig_lua: Optional[Path] = None
    sig_lua_is_bypass: bool = False
    message: str = ""
    warnings: List[str] = field(default_factory=list)
    #: True = 这个游戏的 ~mods 原生生效，根本不需要前置加载器
    not_required: bool = False

    @property
    def emoji(self) -> str:
        return "✓" if self.installed else "✗"


def bin64_dir(game_root: str | os.PathLike[str]) -> Path:
    return Path(game_root) / gamedir.UE_PROJECT / "Binaries" / "Win64"


def _sig_lua_looks_right(path: Path) -> bool:
    """读判据文件的前 4 KB 找特征串。读不出来（权限/编码）就当"看着没问题"。

    ⚠️ `SIG_LUA_MARKERS` 为空 = 该游戏的判据是**二进制插件**（如 .asi），
    没法按文本匹配，此时只看文件在不在 —— 否则会对每个正常安装都报假警告。
    """
    if not SIG_LUA_MARKERS:
        return True
    try:
        head = path.read_bytes()[:4096].decode("utf-8", errors="ignore")
    except OSError:
        return True
    return any(marker in head for marker in SIG_LUA_MARKERS)


def detect(game_root: str | os.PathLike[str]) -> EnablerStatus:
    """检测游戏目录里 Mod Enabler 装了没。

    注意：这个函数**只读**，不下载、不安装、不修改任何东西。
    """
    root = Path(game_root)
    directory = bin64_dir(root)
    status = EnablerStatus(installed=False, bin_dir=directory)

    # 该游戏的 mod 原生生效（如《影之刃零》的 ~mods），不需要前置加载器：
    # 直接判"无需安装"，UI 就不该再提示用户去装什么东西。
    if not GAME.needs_enabler:
        status.not_required = True
        status.installed = True          # 对上层而言"前置就绪"，不拦任何操作
        status.message = f"{GAME.display_name} 的 mod 直接放到 ~mods 即可，不需要额外前置。"
        return status

    if not directory.is_dir():
        status.message = (
            f"找不到游戏的可执行文件目录：\n{directory}\n"
            "请确认选的是游戏根目录（里面有 Project_Plague.exe 那一层）。"
        )
        return status

    for name in PROXY_NAMES:
        if (directory / name).is_file():
            status.proxies_found.append(name)

    lua = directory.joinpath(*SIG_LUA_RELPATH)
    if lua.is_file():
        status.sig_lua = lua
        status.sig_lua_is_bypass = _sig_lua_looks_right(lua)

    has_proxy = bool(status.proxies_found)
    has_lua = status.sig_lua is not None

    _marker_name = "/".join(SIG_LUA_RELPATH) if SIG_LUA_RELPATH else "签名绕过插件"
    if has_proxy and has_lua:
        status.installed = True
        status.message = (
            f"签名绕过前置已就位（{'、'.join(status.proxies_found)} + {_marker_name}），"
            "mod 可以正常加载。"
        )
        if not status.sig_lua_is_bypass:
            status.warnings.append(
                f"{_marker_name} 里没找到签名绕过的特征代码，可能被替换成了别的内容，建议核对一下。"
            )
        return status

    if has_lua and not has_proxy:
        status.message = (
            f"只找到了 {_marker_name}，但没有代理 DLL（"
            + " 或 ".join(PROXY_NAMES)
            + "）—— 这种情况下插件不会被加载，mod 依然不会生效。\n"
            f"请把前置包里的 {' 或 '.join(PROXY_NAMES)} 放到：\n{directory}"
        )
        return status

    if has_proxy and not has_lua:
        status.message = (
            f"找到了代理 DLL（{'、'.join(status.proxies_found)}），但缺少 {_marker_name}，"
            "签名校验没有被绕过，mod 依然不会生效。\n"
            "注意：代理 DLL 也可能是别的工具装的，有 DLL 不等于装好了前置。"
        )
        return status

    status.message = (
        "没有检测到签名绕过前置 —— 这时候 ~mods 里所有 mod 都不会生效，"
        "而且游戏不会报错，只会看起来「mod 没起作用」。\n"
        f"需要放到：{directory}\n"
        f"（{DOWNLOAD_HINT}）"
    )
    return status


def short_line(status: EnablerStatus) -> str:
    """给顶部栏 / 状态栏用的一行话。"""
    if status.installed:
        return "Mod Enabler 已装"
    return "未装 Mod Enabler（mod 不会生效）"


# ================================================================ 一键安装前置
# 2026-10-29：作者决定**随包分发**前置组件（此前是「不内置、只提示」）。
# 允许这么做的依据见 assets/enabler/来源与许可.txt：
#   · UniversalSigBypasser.asi —— CC BY-NC 4.0（可非商业再分发，须署名）
#   · dsound.dll（Ultimate ASI Loader）—— MIT
# 所以这里只做「复制到游戏的 Win64 目录」，不改内容、不改名、不注入；
# 界面上必须同时显示作者与许可，且本软件整体免费 —— 这两条是合规前提。


@dataclass
class EnablerInstallResult:
    ok: bool
    message: str = ""
    copied: List[Path] = field(default_factory=list)
    backup_dir: Optional[Path] = None
    skipped: List[str] = field(default_factory=list)


def bundled_dir() -> Optional[Path]:
    """随包前置文件所在目录（assets/enabler）；没有就返回 None。"""
    from . import apppaths

    d = apppaths.resource_path("assets", "enabler")
    try:
        return d if d.is_dir() else None
    except OSError:
        return None


def bundled_files() -> List[Path]:
    """随包前置里真正要复制的那几个文件（按扩展名挑，忽略说明文件）。"""
    d = bundled_dir()
    if d is None:
        return []
    exts = {".dll", ".asi"}
    try:
        return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts)
    except OSError:
        return []


def install_enabler(game_root: str | os.PathLike[str]) -> EnablerInstallResult:
    """把随包的前置组件复制到 `<游戏根>/<UE工程>/Binaries/Win64`。

    ⚠️ 安全约定：
      · 只在**目标目录已存在**时动手（说明选的是正确的游戏目录）；
      · 目标已有同名文件且内容不同 → 先备份到
        `%APPDATA%\\<数据目录>\\enabler_backup\\<时间戳>\\`，绝不静默覆盖；
      · 只复制，不执行、不注册、不改游戏本体任何文件。
    """
    import shutil
    import time as _time

    root = Path(game_root)
    target = bin64_dir(root)
    if not target.is_dir():
        return EnablerInstallResult(
            ok=False,
            message=f"找不到游戏的 Win64 目录：\n{target}\n请先确认游戏目录选对了。",
        )

    files = bundled_files()
    if not files:
        return EnablerInstallResult(
            ok=False,
            message="随包的组件缺失（assets/enabler 里没有 .dll/.asi），请重新下载完整压缩包。",
        )

    from . import apppaths

    backup_dir: Optional[Path] = None
    copied: List[Path] = []
    skipped: List[str] = []
    for src in files:
        dst = target / src.name
        try:
            if dst.is_file():
                try:
                    same = dst.stat().st_size == src.stat().st_size
                except OSError:
                    same = False
                if not same:
                    if backup_dir is None:
                        backup_dir = apppaths.data_dir() / "enabler_backup" / _time.strftime("%Y%m%d-%H%M%S")
                        backup_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst, backup_dir / dst.name)
                else:
                    skipped.append(src.name)
                    continue
            shutil.copy2(src, dst)
            copied.append(dst)
        except OSError as exc:
            return EnablerInstallResult(
                ok=False,
                message=f"复制 {src.name} 失败：{exc}",
                copied=copied,
                backup_dir=backup_dir,
            )

    if not copied and skipped:
        return EnablerInstallResult(ok=True, message="前置已经是这个版本了，无需重复安装。",
                                    skipped=skipped, backup_dir=backup_dir)
    lines = [f"已把 {len(copied)} 个前置文件放到：", str(target)]
    if skipped:
        lines.append(f"（{('、'.join(skipped))} 本来就是同一份，没重复复制）")
    if backup_dir is not None:
        lines.append(f"\n原有同名文件已备份到：{backup_dir}")
    lines.append("\n组件来源：UniversalSigBypasser by rm-NoobInCoding（CC BY-NC 4.0）；"
                 "dsound.dll = Ultimate ASI Loader by ThirteenAG（MIT）。")
    return EnablerInstallResult(ok=True, message="\n".join(lines), copied=copied,
                                backup_dir=backup_dir, skipped=skipped)
