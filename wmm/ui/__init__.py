# -*- coding: utf-8 -*-
"""界面层（PySide6 / Qt6）。

**只有这个子包依赖 PySide6。** `wmm` 下其余模块都是纯标准库，
这样：
  - 需求文档 11.5 的跨机验证可以在没有 Qt 的环境里单独跑数据层；
  - 单测不需要装 Qt；
  - 「界面把缺依赖问题盖住了」这类事故不会发生。
"""

from __future__ import annotations
