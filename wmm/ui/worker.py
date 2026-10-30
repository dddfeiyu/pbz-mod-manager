# -*- coding: utf-8 -*-
"""后台导入线程。

需求文档 F-10.4 原文：**禁用 time.sleep() 等阻塞 UI 线程的写法**——会冻结界面，
Windows 将判定「未响应」。

导入一个 mod 要解压 + 哈希，实测一批 352 个 pak 的目录会跑几十秒；
放在主线程里界面会直接白屏。所以导入必须走独立线程，
进度用信号回主线程更新（Qt 的规则：**只有主线程能碰控件**）。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

from PySide6.QtCore import QThread, Signal

from .. import importer
from ..modstore import ModStore


class ImportWorker(QThread):
    """在后台线程里跑 `importer.import_paths`。"""

    #: (阶段, 当前, 总数, 说明)
    progressed = Signal(str, int, int, str)
    #: 全部完成，携带 ImportReport
    report_ready = Signal(object)

    def __init__(self, paths: Sequence[str | Path], store: ModStore, parent=None) -> None:
        super().__init__(parent)
        self._paths: List[str] = [str(p) for p in paths]
        self._store = store

    def run(self) -> None:  # noqa: D102 - QThread 约定
        try:
            report = importer.import_paths(
                self._paths, self._store, progress=self._on_progress
            )
        except Exception as exc:  # noqa: BLE001 - 后台线程里抛异常会静默吞掉，必须兜住
            report = importer.ImportReport()
            for path in self._paths:
                report.add(
                    importer.ImportOutcome(
                        importer.KIND_FAILED,
                        path,
                        message="导入过程发生未预期的错误。",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
        self.report_ready.emit(report)

    def _on_progress(self, stage: str, current: int, total: int, message: str) -> None:
        self.progressed.emit(stage, current, total, message)
