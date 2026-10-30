# -*- coding: utf-8 -*-
"""明末：渊虚之羽 MOD 管理器 —— 主包。

本包「地基层」刻意不依赖任何第三方库（含 PySide6），
这样跨机验证阶段（需求文档 11.5）可以脱离 GUI 单独跑通，
也避免「开发机能跑、用户机缺依赖」的问题被界面代码掩盖。
"""

from .version import (
    APP_NAME_ZH,
    APP_NAME_EN,
    VERSION,
    BUILD_DATE,
    AUTHOR_ID,
    BILIBILI_SPACE_URL,
    BILIBILI_CHARGE_URL,
)

__all__ = [
    "APP_NAME_ZH",
    "APP_NAME_EN",
    "VERSION",
    "BUILD_DATE",
    "AUTHOR_ID",
    "BILIBILI_SPACE_URL",
    "BILIBILI_CHARGE_URL",
]
