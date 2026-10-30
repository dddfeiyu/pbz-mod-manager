# -*- coding: utf-8 -*-
"""mod 卡片（需求文档 3.1）。

用**自绘**而不是拼 QLabel 的原因：
  - 状态角标要压在预览图右上角，用布局拼需要额外一层 overlay，容易错位；
  - 预览图要有圆角 + 裁剪，QLabel 做不了（只能靠样式表 hack）；
  - 需求文档 3.6 明确「不允许出现空白」，自绘能保证任何情况下都画出东西。

卡片内容：预览图（或字母卡）+ 名称 + 状态角标 + 一行摘要。
"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QFrame, QMenu, QSizePolicy

from .. import catalog, theme
from . import assets


class ModCard(QFrame):
    """一张 mod 卡片。"""

    clicked = Signal(object)          # 单击（非复选框区域）-> 选中
    activated = Signal(object)        # 双击 -> 改名
    install_requested = Signal(object)
    uninstall_requested = Signal(object)
    detail_requested = Signal(object)     # 看详情 / 冲突清单
    delete_requested = Signal(object)
    reveal_requested = Signal(object)     # 在资源管理器中打开
    preview_change_requested = Signal(object)   # 换一张预览图（自动挑错了的出路）
    #: 手动指定这张卡显示成什么类型（武器/服装部位/其他）-> (view, slots)
    #: 2026-10-29：自动识别对"WW"这种无意义名字无能为力，给用户一条手动纠正的路。
    slot_change_requested = Signal(object, object)
    #: 左上角复选框被切换 -> (view, 是否勾选)
    checked_changed = Signal(object, bool)

    def __init__(self, view: catalog.ModView, parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self._hovered = False
        self._selected = False
        #: 复选框状态。与 `_selected`（单击高亮）是**两件事** ——
        #: 高亮是"我正在看这张卡"，勾选是"我要批量装它"。
        self._checked = False
        self.setObjectName("modCard")
        self.setFixedSize(theme.CARD_WIDTH, theme.CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setToolTip(self._tooltip_text())
        self._pixmap = None
        self._build_pixmap()

    def sizeHint(self) -> QSize:
        return QSize(theme.CARD_WIDTH, theme.CARD_HEIGHT)

    # ------------------------------------------------------------ 预览图
    def _dpr(self) -> float:
        win = self.window()
        try:
            return float(win.devicePixelRatioF()) if win else 1.0
        except AttributeError:  # pragma: no cover
            return 1.0

    def _build_pixmap(self) -> None:
        dpr = self._dpr()
        w, h = theme.CARD_WIDTH, theme.CARD_PREVIEW_HEIGHT
        spec = self.view.preview
        pixmap = None
        if spec is not None and spec.image_path is not None:
            pixmap = assets.load_image(
                spec.image_path, w, h, radius=theme.CARD_CORNER_RADIUS, corners="top", dpr=dpr
            )
        if pixmap is None:
            # 第 2 档：**占位图标卡**。**永远能画出来** —— 这就是"不允许空白"的落点。
            # ⚠️ 用户 2026-10-11 把这一档从「首字字母卡」换成了「按类型画图标」：
            #   原来那个大汉字（光/华/嘿/宫）跟 mod 内容毫无关系，配色还是按名字
            #   哈希的随机色 —— 纯装饰。现在画的是"这是什么"（上衣/武器/技能），
            #   颜色也按类别固定 —— 变成信息位。见 `wmm/placeholder.py`。
            pixmap = assets.make_placeholder_card(
                self.view.placeholder_kind,
                self.view.placeholder_label,
                w,
                h,
                radius=theme.CARD_CORNER_RADIUS,
                corners="top",
                dpr=dpr,
            )
        self._pixmap = pixmap

    def refresh(self, view: catalog.ModView | None = None) -> None:
        if view is not None:
            self.view = view
            self.setToolTip(self._tooltip_text())
        self._build_pixmap()
        self.update()

    # ------------------------------------------------------------ 交互
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            # 点在上/下复选框上 -> 只切换勾选，不触发"选中这张卡"。
            # 复选框热区比视觉尺寸大一圈（±6px），否则 19px 的框很难点中。
            if self._checkbox_rect().adjusted(-6, -6, 6, 6).contains(event.position()):
                self.set_checked(not self._checked)
                self.checked_changed.emit(self.view, self._checked)
                event.accept()
                return
            self.clicked.emit(self.view)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            if self._checkbox_rect().adjusted(-6, -6, 6, 6).contains(event.position()):
                event.accept()
                return
            self.activated.emit(self.view)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """右键菜单。

        ⚠️ **这里刻意不放「安装 / 卸载」。** 用户明确要求这两个动作走
        「卡片左上角勾选 + 工具条上的独立按钮」，因为照着几十张卡片逐个
        右键点安装太低效，而且右键菜单里的危险动作容易被手滑点到。
        右键保留的都是"单张卡片的信息/维护类"动作：勾选、看详情、
        重命名、**更换预览图**（自动挑图挑错了时的唯一出路）、打开位置、删除。
        """
        menu = QMenu(self)
        act_check = menu.addAction("取消勾选" if self._checked else "勾选这个 mod")
        menu.addSeparator()
        act_detail = menu.addAction("查看详情 / 冲突")
        if self.view.conflict_level and self.view.conflict_level != "none":
            act_detail.setText(f"查看详情 / 冲突（{self.view.status_label}）")
        act_rename = menu.addAction("重命名…")
        act_preview = menu.addAction("更换预览图…")
        # 手动指定类型（自动识别不出时用；例如武器 mod 名字里没有"武器"二字）
        act_kind = menu.addMenu("卡片显示为…")
        kind_items = [
            ("武器", ["Weapon"]),
            ("整套", ["Full"]),
            ("上衣", ["Chest"]),
            ("护手", ["Arms"]),
            ("裤子", ["Legs"]),
            ("帽子", ["Helm"]),
            ("头发", ["Hair"]),
            ("其他（重新自动识别）", []),
        ]
        kind_actions = [(act_kind.addAction(label), slots) for label, slots in kind_items]
        act_reveal = menu.addAction("在资源管理器中打开")
        menu.addSeparator()
        act_delete = menu.addAction("从 mod 库删除…")

        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if chosen is act_check:
            self.set_checked(not self._checked)
            self.checked_changed.emit(self.view, self._checked)
        elif chosen is act_detail:
            self.detail_requested.emit(self.view)
        elif chosen is act_rename:
            self.activated.emit(self.view)
        elif chosen is act_preview:
            self.preview_change_requested.emit(self.view)
        elif any(chosen is a for a, _ in kind_actions):
            for a, slots in kind_actions:
                if chosen is a:
                    self.slot_change_requested.emit(self.view, slots)
                    break
        elif chosen is act_reveal:
            self.reveal_requested.emit(self.view)
        elif chosen is act_delete:
            self.delete_requested.emit(self.view)

    def set_selected(self, value: bool) -> None:
        if self._selected != value:
            self._selected = value
            self.update()

    @property
    def checked(self) -> bool:
        return self._checked

    def set_checked(self, value: bool) -> None:
        if self._checked != bool(value):
            self._checked = bool(value)
            self.update()

    def _checkbox_rect(self) -> QRectF:
        """左上角复选框的位置。压在预览图上，所以要点得准 —— 见 mousePressEvent。"""
        size = theme.CHECKBOX_SIZE
        margin = 8.0
        return QRectF(margin, margin, size, size)

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = theme.CARD_CORNER_RADIUS + 2

        # 卡片底
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BG_HOVER if self._hovered else theme.BG_CARD))
        painter.drawPath(path)

        # 裁剪到圆角内画面
        painter.save()
        painter.setClipPath(path)

        # 冲突组：文字区先淡染一层组色，让"这批 mod 有问题"在**缩略图尺度**上
        # 也能看出来。⚠️ 只染文字区、不染预览图 —— 真实预览图大量是黄的
        # （游戏截图、说明图），染上去反而看不出组色。
        group_color = self.view.conflict_color or ""
        if group_color:
            tint = QColor(group_color)
            tint.setAlpha(theme.CONFLICT_TINT_ALPHA)
            painter.fillRect(
                QRectF(0, theme.CARD_PREVIEW_HEIGHT, self.width(), self.height()),
                tint,
            )

        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        else:  # 理论上到不了这里，兜底画个色块，绝不留白
            painter.fillRect(QRectF(0, 0, self.width(), theme.CARD_PREVIEW_HEIGHT), QColor(theme.BG_SUNKEN))

        self._draw_badge(painter)
        self._draw_checkbox(painter)
        self._draw_texts(painter)
        painter.restore()

        # 边框：悬停/选中时用主色，冲突组用**组色**（同组同色），否则淡灰
        # ⚠️ 优先级是「选中 > 悬停 > 冲突组色 > 淡灰」。
        #   冲突组色必须**冷于**选中态的主色 —— 用户点选一张卡是为了操作它，
        #   而不是想知道它冲突；两件事用两种视觉语言，否则点选后看不出这组是谁。
        #   用户 2026-10-11 原话：「当冲突时，直接把这个 mod 变成黄色。三个 mod 都
        #   替换同一个衣服，那这三个 mod 都变黄；另一组三个变蓝。同一组颜色相同。」
        #   然后又补了一句「黄边太窄太浅，一般人注意不到」→ 2px 提到
        #   theme.CONFLICT_BORDER_WIDTH(4px)，并配合上面的文字区淡染。
        if self._selected:
            pen = QPen(QColor(theme.PRIMARY), 3)
        elif self._hovered:
            pen = QPen(QColor(theme.PRIMARY), 2)
        elif group_color:
            pen = QPen(QColor(group_color), theme.CONFLICT_BORDER_WIDTH)
        else:
            pen = QPen(QColor(theme.BORDER), 1)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        # 描边加粗后 path 要内缩，否则外半圈被控件边界裁掉（看起来还是 2px）。
        if pen.widthF() > 2.0:
            w = pen.widthF()
            path = QPainterPath()
            path.addRoundedRect(
                QRectF(w / 2.0, w / 2.0, self.width() - w, self.height() - w), radius, radius
            )
        painter.drawPath(path)

        # 预览图与文字区之间的分隔线
        y = theme.CARD_PREVIEW_HEIGHT
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawLine(1, y, self.width() - 1, y)

        painter.end()

    def _draw_checkbox(self, painter: QPainter) -> None:
        """左上角复选框。勾选 = 批量安装/卸载时的选定。

        为什么要**不透明底**：它压在预览图上，而预览图什么颜色都可能有
        （深色衣服、亮色背景）。半透明底会时隐时现，用户会以为没点上。
        """
        rect = self._checkbox_rect()
        radius = 4.0

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        # 外圈白色描边 + 深色投影，保证在任何底图上都看得见
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 90))
        shadow = QPainterPath()
        shadow.addRoundedRect(rect.adjusted(-1, -1, 1, 1.6), radius + 1, radius + 1)
        painter.drawPath(shadow)

        if self._checked:
            painter.setBrush(QColor(theme.PRIMARY))
            painter.setPen(QPen(QColor("#FFFFFF"), 1.6))
            painter.drawPath(path)
            # 对勾
            pen = QPen(QColor("#FFFFFF"), 2.2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
            painter.drawPolyline(
                [
                    QRectF(x + w * 0.24, y + h * 0.52, 0, 0).topLeft(),
                    QRectF(x + w * 0.43, y + h * 0.72, 0, 0).topLeft(),
                    QRectF(x + w * 0.78, y + h * 0.28, 0, 0).topLeft(),
                ]
            )
        else:
            painter.setBrush(QColor(255, 255, 255, 55 if not self._hovered else 90))
            painter.setPen(QPen(QColor(255, 255, 255, 210), 1.6))
            painter.drawPath(path)

    def _draw_badge(self, painter: QPainter) -> None:
        """右上角状态角标：未装 / 已装 / 冲突。"""
        text = self.view.status_label
        fg, bg = self.view.status_colors
        font = QFont()
        font.setPointSizeF(theme.FONT_SIZE_SMALL - 1)
        font.setBold(True)
        painter.setFont(font)

        metrics = QFontMetrics(font)
        pad_x, pad_y = 8, 3
        w = metrics.horizontalAdvance(text) + pad_x * 2
        h = metrics.height() + pad_y * 2
        x = self.width() - w - 8
        y = 8.0

        rect = QRectF(x, y, w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, h / 2, h / 2)
        painter.setPen(Qt.NoPen)
        #角标底色必须不透明，否则压在预览图上会看不清
        painter.setBrush(QColor(bg))
        painter.drawPath(path)

        painter.setPen(QColor(fg))
        painter.drawText(rect, Qt.AlignCenter, text)

        # ---- 状态角标**下方**的小徽标：同系列部件 / 手装 ----
        # 用描边小徽标而不是实心 —— 实心的会和状态角标抢视觉权重，
        # 而它们只是"这条导入信息 / 这个安装来源"，不是"出事了"。
        # ⚠️ 两个徽标**串行往下排**（同系列与手装可以同时出现），
        #    且各自带自己的配色：橙色系 = 同系列，紫色系 = 手装，含义无关不能混。
        badges: List[tuple] = []
        if self.view.series_badge:
            badges.append((self.view.series_badge, theme.SERIES_BADGE_BORDER, theme.SERIES_BADGE_TEXT))
        if self.view.external_badge:
            badges.append((self.view.external_badge, theme.EXTERNAL_BADGE_BORDER, theme.EXTERNAL_BADGE_TEXT))
        by = y + h + 4
        for text, border, fg in badges:
            sfont = QFont()
            sfont.setPointSizeF(theme.FONT_SIZE_SMALL - 2)
            sfont.setBold(True)
            painter.setFont(sfont)
            sm = QFontMetrics(sfont)
            sw = sm.horizontalAdvance(text) + 12
            sh = sm.height() + 4
            srect = QRectF(self.width() - sw - 8, by, sw, sh)
            spath = QPainterPath()
            spath.addRoundedRect(srect, 3, 3)
            painter.setBrush(QColor(theme.BG_CARD))      # 不透明，压图上也看得清
            painter.setPen(QPen(QColor(border), 1))
            painter.drawPath(spath)
            painter.setPen(QColor(fg))
            painter.drawText(srect, Qt.AlignCenter, text)
            by += sh + 4

    def _draw_texts(self, painter: QPainter) -> None:
        pad = theme.CARD_PADDING
        top = theme.CARD_PREVIEW_HEIGHT + 10
        width = self.width() - pad * 2

        # 名称（最多两行，超出用省略号）
        title_font = QFont()
        title_font.setPointSizeF(theme.FONT_SIZE_CARD_TITLE)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(theme.TEXT))
        title_rect = QRect(pad, top, width, 0)
        used = self._draw_wrapped(painter, self.view.name, title_rect, title_font, 2, QColor(theme.TEXT))

        # ---- 服装行：替换哪套衣服 / 动了哪些部位 ----
        # 这一行是这个工具相对"文件夹拖来拖去"的核心增量，所以给它主色，
        # 而不是混在灰色摘要里。识别不出来时不画，不留一行「未知」占位。
        outfit = self.view.outfit_text
        if outfit:
            outfit_font = QFont()
            outfit_font.setPointSizeF(theme.FONT_SIZE_SMALL - 1)
            outfit_font.setBold(True)
            painter.setFont(outfit_font)
            metrics = QFontMetrics(outfit_font)
            y = top + used + 4
            painter.setPen(QColor(theme.PRIMARY_DARK))
            painter.drawText(
                QRect(pad, y, width, metrics.height()),
                Qt.AlignLeft | Qt.AlignVCenter,
                assets.elide(outfit, outfit_font, width),
            )
            used += metrics.height() + 4

        # 摘要行
        summary = self.view.summary
        if summary:
            sub_font = QFont()
            sub_font.setPointSizeF(theme.FONT_SIZE_SMALL - 1)
            painter.setFont(sub_font)
            y = top + used + 6
            metrics = QFontMetrics(sub_font)
            painter.setPen(QColor(theme.TEXT_TERTIARY))
            painter.drawText(
                QRect(pad, y, width, metrics.height()),
                Qt.AlignLeft | Qt.AlignVCenter,
                assets.elide(summary, sub_font, width),
            )

        # LogicMods 之类的目标目录提示（只在需要时出现，避免噪音）
        note = self.view.subdir_note
        if note:
            sub_font = QFont()
            sub_font.setPointSizeF(theme.FONT_SIZE_SMALL - 1)
            painter.setFont(sub_font)
            metrics = QFontMetrics(sub_font)
            y = self.height() - metrics.height() - pad
            painter.setPen(QColor(theme.CONFLICT_COLORS["potential"]))
            painter.drawText(
                QRect(pad, y, width, metrics.height()),
                Qt.AlignLeft | Qt.AlignVCenter,
                assets.elide(note, sub_font, width),
            )

    def _draw_wrapped(self, painter: QPainter, text: str, rect: QRect, font: QFont, max_lines: int, color: QColor) -> int:
        """按词换行画文字，限制行数，超出加省略号。返回实际占用高度。"""
        metrics = QFontMetrics(font)
        line_h = metrics.height()
        lines: list[str] = []
        current = ""

        for ch in text:
            candidate = current + ch
            if metrics.horizontalAdvance(candidate) <= rect.width():
                current = candidate
            else:
                lines.append(current)
                current = ch
                if len(lines) == max_lines:
                    break
        if len(lines) < max_lines and current:
            lines.append(current)

        # 还有剩余内容 -> 最后一行加省略号
        consumed = sum(len(line) for line in lines)
        if consumed < len(text) and lines:
            tail = lines[-1]
            while tail and metrics.horizontalAdvance(tail + "…") > rect.width():
                tail = tail[:-1]
            lines[-1] = tail + "…"

        painter.setPen(color)
        y = rect.y()
        for line in lines[:max_lines]:
            painter.drawText(QRect(rect.x(), y, rect.width(), line_h), Qt.AlignLeft | Qt.AlignVCenter, line)
            y += line_h
        return y - rect.y()

    # ------------------------------------------------------------ 其他
    def _tooltip_text(self) -> str:
        v = self.view
        lines = [f"<b>{v.name}</b>", f"状态：{v.status_label}"]
        # 用户 2026-10-12：手装的 mod 要在 tooltip 里说清"游戏在加载它，
        # 但本工具没有安装记录"—— 只写「已装」用户会以为能在这里卸载。
        ext_tip = v.external_tooltip
        if ext_tip:
            lines.append(ext_tip)
        outfit = v.outfit_text
        if outfit:
            lines.append(f"服装：{outfit}")
        tip = v.outfit_tooltip
        if tip:
            lines.append(f"<i>{tip}</i>")
        if v.meta.source_archive:
            lines.append(f"来源：{v.meta.source_archive}")
        if v.meta.name_source:
            lines.append(f"名称来自：{v.meta.name_source}")
        if v.meta.content_hash:
            lines.append(f"指纹：{v.meta.content_hash[:16]}…")
        for f in v.meta.files[:6]:
            lines.append(f"· {f.name}")
        if len(v.meta.files) > 6:
            lines.append(f"… 另有 {len(v.meta.files) - 6} 个文件")
        if v.conflicts:
            lines.append("冲突资源：" + "、".join(v.conflicts[:5]))
        lines.append("<i>双击改名 · 右键换预览图 / 看详情</i>")
        return "<br>".join(lines)
