# -*- coding: utf-8 -*-
"""字体保障。

需求文档全篇要求中文界面。Windows 10+ 自带「Microsoft YaHei UI / 微软雅黑」，
正常情况下不需要做任何事。但在两类环境下会出问题：

1. **离屏渲染（`QT_QPA_PLATFORM=offscreen`）**：实测字体库是空的（0 个字体），
   所有中文渲染成方框 □。截图/自检脚本会因此看不出任何问题 ——
   这正是"看起来验证过了、其实什么都没验证"的典型陷阱。
2. **精简版 / 定制版 Windows 镜像**：可能把雅黑剔除了。

所以这里提供一个显式加载系统字体文件的兜底。**只在当前字体渲染不出中文时才动手**，
正常环境下一行都不会执行。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter

#: 需要能渲染的代表性字符（拉丁 + 汉字）。
#: 不用全角标点作探针 —— 见下面 `_is_tofu()` 的说明。
_PROBE_CHARS = "A街羽"

#: Unicode 私用区字符：**任何字体都不可能有它的字形**，
#: 于是它渲染出来必定是"找不到字形"的替代方块。用它当参照物。
_REFERENCE_TOFU = "\uE000"

#: 探针图像尺寸（够放下一个汉字即可）
_PROBE_PX = 48

#: 候选字体文件（按优先级）。ttc 是字体集合，Qt 能直接加载。
_CANDIDATE_FILES = (
    "msyh.ttc",      # 微软雅黑
    "msyhbd.ttc",    # 微软雅黑 Bold
    "msyhl.ttc",     # 微软雅黑 Light
    "simhei.ttf",    # 黑体
    "simsun.ttc",    # 宋体
)


def _default_font() -> QFont:
    """当前实际生效的字体。

    ⚠️ 不能直接用 `QFont()` —— 那是一个"未解析"的空字体，
    拿不到应用设置过的字体。
    """
    from PySide6.QtWidgets import QApplication

    inst = QApplication.instance()
    if inst is not None:
        return QFont(inst.font())
    return QFont()


def _render_signature(font: QFont, ch: str) -> bytes:
    image = QImage(_PROBE_PX, _PROBE_PX, QImage.Format_ARGB32)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    painter.setFont(font)
    painter.setPen(QColor(0, 0, 0))
    painter.drawText(image.rect(), Qt.AlignCenter, ch)
    painter.end()
    return bytes(image.constBits())


def _is_tofu(font: QFont, ch: str) -> bool:
    """这个字符是不是渲染成了"找不到字形"的方块。

    **为什么不用 `QFontMetrics.inFont()`**：实测它在离屏平台上不可靠 ——
    对「羽」「，」这类雅黑明明有的字符也返回 False（像素对比可证它们渲染正常）。
    真实 Windows 平台上 inFont 是准的，但既然有平台差异，就不能拿它当判据。

    改成"渲染出来跟私用区字符一模一样"这个**行为级**判据：
    两个不同的真字形不可能像素完全相同，而缺字形的替代方块必然相同。
    """
    return _render_signature(font, ch) == _render_signature(font, _REFERENCE_TOFU)


def missing_probe_chars(font: Optional[QFont] = None) -> str:
    """返回渲染不出来的探针字符（排障用，空串表示全都能渲染）。"""
    target = font if font is not None else _default_font()
    return "".join(ch for ch in _PROBE_CHARS if _is_tofu(target, ch))


def can_render_cjk(font: Optional[QFont] = None) -> bool:
    """当前字体能不能渲染中文。"""
    return not missing_probe_chars(font)


def _font_dirs() -> List[Path]:
    dirs: List[Path] = []
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    dirs.append(Path(windir) / "Fonts")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        # 非管理员装的用户级字体
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return dirs


def ensure_cjk_font() -> Optional[str]:
    """确保中文能渲染。返回实际加载的字体文件名，没动手则返回 None。

    幂等，可重复调用。
    """
    if can_render_cjk():
        return None

    for directory in _font_dirs():
        if not directory.is_dir():
            continue
        for filename in _CANDIDATE_FILES:
            path = directory / filename
            if not path.is_file():
                continue
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id < 0:
                continue
            families = QFontDatabase.applicationFontFamilies(font_id)
            if not families:
                continue
            app_font = QFont(families[0])
            if can_render_cjk(app_font):
                from PySide6.QtWidgets import QApplication

                inst = QApplication.instance()
                if inst is not None:
                    inst.setFont(app_font)
                return filename
    return None


def describe() -> str:
    """一行诊断信息（写日志/自检用）。"""
    families = QFontDatabase.families()
    ok = can_render_cjk()
    missing = missing_probe_chars()
    sample = "、".join(families[:6])
    return (
        f"字体库 {len(families)} 个（{sample}{'…' if len(families) > 6 else ''}）；"
        f"中文可渲染={ok}" + (f"（缺字：{missing}）" if missing else "") +
        f"；当前字体={_default_font().family()}"
    )


if __name__ == "__main__":  # 手工排查用
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    print(describe())
    loaded = ensure_cjk_font()
    print("加载字体：", loaded)
    print(describe())
