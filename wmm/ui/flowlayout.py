# -*- coding: utf-8 -*-
"""流式布局 —— 卡片网格会随窗口宽度自动增减列数。

不用 `QGridLayout` 的原因：网格列数是固定的，窗口变窄时要么出现横向滚动条、
要么卡片被压扁。需求文档 R-18 要求"窄屏优先保留按钮、文字折叠"，
卡片区也应该跟着自适应，所以用流式布局。

在 Qt 官方 FlowLayout 例子的基础上做了两处改动：
  1. **每行居中**（官方版是左对齐）—— 卡片在宽窗口下靠左会显得空；
  2. **按最大项高度对齐每行的垂直间距**，避免出现参差。
"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(
        self,
        parent: QWidget | None = None,
        margin: int = 0,
        h_spacing: int = 14,
        v_spacing: int = 14,
        *,
        center: bool = True,
    ) -> None:
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._center = center
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    # ------------------------------------------------------------ 基本接口
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt 命名
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def clear(self) -> None:
        """清空全部卡片。

        ⚠️ 这里**绝不能** `widget.setParent(None)` —— 2026-10-19 查出的黑框
        真凶（用户报「卸载后弹一个纯白、标题是程序名的窗口」）：
        `setParent(None)` 会把卡片从容器剥离成**顶层窗口**，而 `deleteLater()`
        要等回到事件循环才真正销毁；这中间的半秒到一秒里，几十张卡片就是
        几十个「无标题的白色独立窗口」（实测 236×311、全部叠在同一位置，
        标题因没设置而显示成 applicationName = WuchangModManager）。

        正确做法：**保留 parent**，先 hide 再 deleteLater —— 它从头到尾都
        只是容器里的一个子控件，永远不会变成顶层窗口。
        """
        while self._items:
            item = self._items.pop()
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    # ------------------------------------------------------------ 布局
    def _rows(self, width: int) -> List[List[QLayoutItem]]:
        """把 items 按可用宽度切分成多行。"""
        margins = self.contentsMargins()
        effective = max(1, width - margins.left() - margins.right())
        rows: List[List[QLayoutItem]] = []
        row: List[QLayoutItem] = []
        used = 0
        for item in self._items:
            hint = item.sizeHint()
            need = hint.width() if not row else self._h_spacing + hint.width()
            if row and used + need > effective:
                rows.append(row)
                row = [item]
                used = hint.width()
            else:
                row.append(item)
                used += need
        if row:
            rows.append(row)
        return rows

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        content = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        rows = self._rows(rect.width())

        y = content.y()
        for row in rows:
            row_height = max((item.sizeHint().height() for item in row), default=0)
            total_width = sum(item.sizeHint().width() for item in row) + self._h_spacing * (len(row) - 1)

            if self._center:
                x = content.x() + max(0, (content.width() - total_width) // 2)
            else:
                x = content.x()

            for item in row:
                hint = item.sizeHint()
                if not test_only:
                    item.setGeometry(QRect(QPoint(x, y), hint))
                x += hint.width() + self._h_spacing
            y += row_height + self._v_spacing

        used_height = y - content.y() - (self._v_spacing if rows else 0)
        return max(0, used_height) + margins.top() + margins.bottom()


class FlowWidget(QWidget):
    """承载 `FlowLayout` 的容器。

    **为什么要单独一个类**：Qt 对 `heightForWidth` 的支持在
    `QScrollArea(setWidgetResizable=True)` 这个组合下并不总是被采纳 ——
    容器高度会停在 `sizeHint()`（= 单个卡片的高度 + 边距）上，
    于是卡片一多，**上面两行看得见、下面全被裁掉，而且没有滚动条**。
    这是流式卡片网格最典型的"看起来能跑、实际少了一半内容"的缺陷。

    这里在每次 resize 时按当前宽度重算所需高度并 `setMinimumHeight`，
    让 QScrollArea 的滚动范围始终正确。
    """

    def __init__(self, flow: FlowLayout, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._flow = flow
        self.setLayout(flow)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._flow.heightForWidth(width)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.width() or self._flow.minimumSize().width(), self._flow.heightForWidth(self.width()))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.sync_height()

    def sync_height(self) -> None:
        needed = self._flow.heightForWidth(max(self.width(), 1))
        if needed and self.minimumHeight() != needed:
            self.setMinimumHeight(needed)
            self.updateGeometry()

    def relayout(self) -> None:
        """内容变了之后调用：先让布局失效，再重算容器高度。"""
        self._flow.invalidate()
        self.sync_height()
        self.updateGeometry()
