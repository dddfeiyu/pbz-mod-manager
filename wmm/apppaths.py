# -*- coding: utf-8 -*-
"""路径策略 —— 需求文档第 4 章「硬约束·必读」。

铁律：**程序目录只读，所有可变数据写 %APPDATA%\\WuchangModManager\\。**

原因（文档原文）：用户很可能把工具解压到 C:\\Program Files\\ 或
C:\\Program Files (x86)\\，这两个目录普通用户无写权限，写入必然抛
PermissionError —— 在别人电脑上表现为「双击就崩」「设置存不住」「装完重启全没了」，
而在开发机上（放在桌面或工作区）完全复现不出来。

附带好处：工具可以整体放在 U 盘、只读介质或网盘同步目录里运行而不出错。

本模块是唯一允许拼「要写入的路径」的地方；jsonstore 会再校验一次，
确保没有任何代码绕过这条约束往程序目录里写东西。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: %APPDATA% 下的子目录名。值来自游戏配置（wmm/games/），
#: 明末保持 "WuchangModManager" 不变 —— 现有用户的数据都在那下面。
from .games import ACTIVE as _GAME  # noqa: E402

DATA_DIR_NAME = _GAME.data_dir_name

#: 允许覆盖数据目录的环境变量名（仅用于测试 / 排障，正常使用不设置）
ENV_DATA_DIR_OVERRIDE = "WMM_DATA_DIR"

#: 允许覆盖程序目录的环境变量名（仅用于测试）
ENV_PROGRAM_DIR_OVERRIDE = "WMM_PROGRAM_DIR"


class ProgramDirWriteError(RuntimeError):
    """试图往程序目录写数据。这是设计缺陷，必须修代码，不能靠改配置绕过。"""


def program_dir() -> Path:
    """程序所在目录（只读）。

    - PyInstaller onedir 打包后：exe 所在目录（sys.frozen 为真）。
    - 源码运行：仓库根目录（本文件的上上级）。

    这个目录里的东西**一律不许写**。只有 `说明.txt` 这类静态文件放这里。
    """
    override = os.environ.get(ENV_PROGRAM_DIR_OVERRIDE)
    if override:
        return Path(override).expanduser().resolve()

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    # wmm/apppaths.py -> wmm/ -> 仓库根
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """**只读**的随包资源目录（图标、模板等静态文件）。

    与 `program_dir()` 的区别：
      - onedir 打包后，`program_dir()` 是 exe 所在目录（用户看得见、会去点），
        而 PyInstaller 把随包资源放在 `_internal/` 下，即 `sys._MEIPASS`；
      - 源码运行时两者是同一个目录（仓库根）。

    这里只读，不写；写操作一律走 `data_dir()`。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return program_dir()


def resource_path(*parts: str) -> Path:
    """拼一个随包资源的绝对路径（例如 resource_path("assets", "icon.ico")）。"""
    return bundle_dir().joinpath(*parts)


def data_dir() -> Path:
    """用户数据目录：%APPDATA%\\WuchangModManager\\。

    必须用 os.environ['APPDATA']，不要拼死路径 —— 用户可能把 AppData 重定向过。
    取不到 APPDATA 时（极少数情况）退回 ~/AppData/Roaming。
    """
    override = os.environ.get(ENV_DATA_DIR_OVERRIDE)
    if override:
        return Path(override).expanduser().resolve()

    base = os.environ.get("APPDATA")
    if base:
        root = Path(base)
    else:
        root = Path.home() / "AppData" / "Roaming"
    return root / DATA_DIR_NAME


def mods_dir() -> Path:
    """默认的 mod 库存目录（用户可在设置里指向外部目录）。"""
    return data_dir() / "mods"


def logs_dir() -> Path:
    return data_dir() / "logs"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def library_path() -> Path:
    return data_dir() / "library.json"


def backups_dir() -> Path:
    """安装前备份目录（需求文档 F-4.9：支持回滚）。"""
    return data_dir() / "backups"


def _norm(p: Path) -> str:
    """把路径规范成可比较的字符串形式（大小写不敏感，Windows 语义）。"""
    try:
        s = str(Path(p).resolve())
    except OSError:
        s = str(Path(p).absolute())
    return s.rstrip("\\/").lower()


def is_in_program_dir(target: Path) -> bool:
    """target 是否落在程序目录内（含程序目录自身）。"""
    prog = _norm(program_dir())
    tgt = _norm(target)
    return tgt == prog or tgt.startswith(prog + os.sep)


def is_in_data_dir(target: Path) -> bool:
    data = _norm(data_dir())
    tgt = _norm(target)
    return tgt == data or tgt.startswith(data + os.sep)


def assert_writable_target(target: Path) -> None:
    """写操作前的守卫：拒绝任何对程序目录的写入。

    这是「程序目录只读」这条硬约束的最后一道闸，任何绕过 jsonstore
    直接开文件的代码只要调一次它就安全了。
    """
    if is_in_program_dir(target):
        raise ProgramDirWriteError(
            f"拒绝写入程序目录：{target}\n"
            f"程序目录（{program_dir()}）只读，可变数据必须写 %APPDATA%。\n"
            f"（需求文档第 4 章硬约束：Program Files 下无写权限，"
            f"写这里会在用户机器上表现为「设置存不住 / 双击就崩」。）"
        )


def ensure_data_dirs() -> None:
    """创建数据目录骨架。幂等，可重复调用。

    只建我们需要写的目录，不碰程序目录。
    """
    for p in (data_dir(), mods_dir(), logs_dir(), backups_dir()):
        p.mkdir(parents=True, exist_ok=True)


def describe() -> str:
    """给排障用的一行摘要（会写进日志，不写进 UI 文案）。"""
    writable = True
    try:
        assert_writable_target(data_dir())
    except ProgramDirWriteError:
        writable = False
    return (
        f"program_dir={program_dir()} (readonly)\n"
        f"data_dir={data_dir()} (writable={writable})\n"
        f"frozen={bool(getattr(sys, 'frozen', False))}"
    )
