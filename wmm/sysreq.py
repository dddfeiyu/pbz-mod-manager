# -*- coding: utf-8 -*-
"""系统要求检测（需求文档 11.1 / F-11.2）。

要求：Windows 10 1809 及以上、仅 64 位（x64）。Qt6（PySide6）不支持 Win7/8/8.1，
这是选型时就锁死的边界，不是后期可以补的。

行为约定（F-11.2）：**不满足时给出明确的中文提示再退出**，
而不是崩溃或静默无反应 —— Win7 用户下载后打不开，只会留言骂人，而这不是 bug。
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass, field
from typing import List

#: Windows 10 1809 的版本号（build 17763），需求文档 11.1 的下限
MIN_WIN_BUILD = 17763
MIN_WIN_MAJOR = 10

SUPPORTED_DESC = "需要 Windows 10 1809 或更高版本的 64 位系统"
REQUIREMENT_TEXT = f"系统要求：Windows 10 1809+ / 64 位（x64）。{SUPPORTED_DESC}。"


@dataclass
class SysReqResult:
    ok: bool
    #: 面向用户的中文提示（ok 为 False 时用于弹窗，为 True 时可留空）
    message: str = ""
    #: 排障用的机器信息
    details: List[str] = field(default_factory=list)

    @property
    def detail_text(self) -> str:
        return "\n".join(self.details)


def _win_build() -> tuple[int, int, int]:
    """返回 (major, minor, build)。非 Windows 返回 (0, 0, 0)。"""
    if sys.platform != "win32":
        return (0, 0, 0)
    try:
        info = sys.getwindowsversion()
        return (int(info.major), int(info.minor), int(info.build))
    except Exception:  # noqa: BLE001
        return (0, 0, 0)


def is_64bit() -> bool:
    """判断宿主是否 64 位 Windows。"""
    if sys.platform != "win32":
        return platform.machine().lower() in ("amd64", "x86_64", "arm64")
    # 64 位 Windows 上运行 64 位 Python 时：PROCESSOR_ARCHITECTURE = AMD64，
    # 且 32 位兼容层会额外设置 PROCESSOR_ARCHITEW6432。
    arch = os.environ.get("PROCESSOR_ARCHITECTURE", "").upper()
    arch_wow = os.environ.get("PROCESSOR_ARCHITEW6432", "").upper()
    if arch_wow:  # 说明当前进程是 32 位跑在 64 位上 -> 我们的打包产物是 x64，这里判失败
        return False
    if arch in ("AMD64", "ARM64"):
        # ARM64 原生不承诺支持（需求文档 11.1），由调用方按需另行提示
        return arch == "AMD64"
    return platform.machine().lower() in ("amd64", "x86_64")


def is_arm64() -> bool:
    arch = os.environ.get("PROCESSOR_ARCHITECTURE", "").upper()
    return arch == "ARM64" or platform.machine().lower() == "arm64"


def check_system() -> SysReqResult:
    """启动时调用。不满足要求时返回可展示的中文提示。"""
    details = [
        f"platform={sys.platform}",
        f"python={platform.python_version()} {platform.machine()}",
        f"PROCESSOR_ARCHITECTURE={os.environ.get('PROCESSOR_ARCHITECTURE', '')}",
    ]

    if sys.platform != "win32":
        # 非 Windows：这是开发/测试环境，不拦截，但记录明细
        details.append("非 Windows 环境，跳过版本要求校验（仅开发/测试情形）。")
        return SysReqResult(ok=True, details=details)

    major, minor, build = _win_build()
    details.append(f"windows={major}.{minor}.{build}")

    if major < MIN_WIN_MAJOR:
        pretty = {6: "Windows 7 / 8 / 8.1"}.get(major, f"Windows {major}.{minor}")
        return SysReqResult(
            ok=False,
            message=(
                f"本工具不支持 {pretty}。\n\n"
                f"{SUPPORTED_DESC}。\n"
                "如需在更老的系统上使用，只能改用 Qt5 版本，请反馈给作者。"
            ),
            details=details,
        )

    if major == MIN_WIN_MAJOR and build < MIN_WIN_BUILD:
        return SysReqResult(
            ok=False,
            message=(
                f"你的 Windows 10 版本过旧（内部版本 {build}）。\n\n"
                f"{SUPPORTED_DESC}。请先通过 Windows 更新升级系统。"
            ),
            details=details,
        )

    if not is_64bit():
        return SysReqResult(
            ok=False,
            message=(
                "本工具只提供 64 位（x64）版本，无法在当前系统上运行。\n\n"
                f"{SUPPORTED_DESC}。"
            ),
            details=details,
        )

    if is_arm64():
        # 不拦截（可通过仿真运行），但如实提示
        details.append("检测到 ARM64，程序将以仿真方式运行，不在支持承诺范围内。")

    return SysReqResult(ok=True, details=details)


#: 别名：界面层与自检脚本用简写 `sysreq.check()` 更顺手。
#: 保留 `check_system` 作为正式名（老调用点与单测在用），两者是同一个函数。
check = check_system


#: 随包说明文档 说明.txt 里「① 系统要求」那一条的文案（需求文档 F-11.16）
SYSTEM_REQUIREMENT_LINE = "① 系统要求：Windows 10 1809 及以上版本、64 位（x64）。"
