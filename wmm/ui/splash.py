# -*- coding: utf-8 -*-
"""启动弹窗（需求文档 10.1 F-10.1 ~ F-10.8）。

**这一页的职责是「让用户记住作者是谁」，不是介绍软件。** 所以：
作者 ID 是全页最大字号的主色行，程序名反而是小字（甚至可以不写）。

四段垂直排列（F-10.1f），顺序不可换：

    ┌──────────────────────────────────────┐
    │                                      │   ← 顶部留白（把作者区推到上半部）
    │            诸葛-建国                  │   ← ① 作者区：24pt 加粗 主色
    │          明末MOD管理器                 │   ←    程序名：11pt 次要色
    │            v0.1.0 · 2026-10-09        │   ←    版本号 · 发布日期：8pt 三级色
    │                                      │
    │   本工具完全免费，没有任何付费版本。     │   ← ② 声明区：10pt 次要色
    │   如果你是花钱买到的，说明被倒卖了，     │
    │   请到 B站主页 告诉我。                 │
    │   官方发布渠道：B站                     │
    │                                      │
    │            关注支持作者                │   ← ③ 纯文字支持行：12pt 次要色
    │                                      │      （不可点、非链接色、无下划线、无 hover）
    │                        [ 进入工具 ]    │   ← ④ 按钮区：唯一可点元素，主色实心
    └──────────────────────────────────────┘

**全弹窗只有一个可点元素**（F-10.1e）—— 这从根本上排除了「用户把跳转元素当成
关闭按钮」的误点问题。所以这里既没有 B站 链接，也没有任何支持类按钮。

字体大小的一处文档冲突（已按更具体的数值实现，并在此记录）：
  * F-10.1f 说支持行「字号最小」；
  * F-10.1g 说支持行「字号约 12pt」。
  两者不能同时成立。这里取 **F-10.1g 的明确数值 12pt**，把声明段压到 10pt ——
  「支持方式存在」稍微比法律声明显眼一点是可接受的，而 12pt 是可核对的数字。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme, version


def _selectable(label: QLabel, *, wrap: bool = False) -> QLabel:
    """F-10.6：弹窗内所有文字都必须可选中、可复制。

    注意这里**只用 TextSelectableByMouse，不加 TextBrowserInteraction** ——
    后者会让文字看起来像链接、并可能带 hover 效果，那就踩了 F-10.1g。
    """
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setWordWrap(wrap)
    return label


class SplashDialog(QDialog):
    """启动弹窗。

    刻意用**普通顶层窗口**而不是无边框窗口：F-10.3c 明确说 Alt+F4 与标题栏 ×
    属于 Windows 窗口机制、程序无权也不应拦截，而做到这一点的前提就是
    老老实实保留系统标题栏。
    """

    #: 用户要进入工具（点了按钮 / 按了 Esc / 直接关了窗口）—— 三种都算
    enter_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._done = False

        self.setWindowTitle(f"{version.APP_NAME_ZH} v{version.VERSION}")
        # F-10.1：建议宽度 420~480 逻辑像素。固定宽度可以避免高 DPI 下
        # 文字换行位置漂移，高度交给内容决定。
        self.setFixedWidth(version.SPLASH_WIDTH)
        # 非模态：主界面要在后台并行加载（F-10.5）
        self.setWindowModality(Qt.NonModal)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 20)
        outer.setSpacing(0)

        outer.addStretch(1)                       # 顶部留白 -> 作者区落在上半部

        # ---------- ① 作者区（视觉主体，F-10.1b）----------
        # ⚠️ 字号必须写在**控件自己的** setStyleSheet 里，不能用 setFont()：
        #    全局 QSS 有 `QWidget { font-size: 10pt }`，它会覆盖 setFont()，
        #    实测 setFont(24pt) 渲染出来只有 10pt（详见 theme.SPLASH_AUTHOR_PT 注释）。
        author = QLabel(version.SPLASH_AUTHOR_PREFIX + version.AUTHOR_ID)
        author.setStyleSheet(
            f"color:{theme.PRIMARY}; font-size:{theme.SPLASH_AUTHOR_PT}pt; font-weight:700;"
        )
        author.setAlignment(Qt.AlignCenter)
        _selectable(author)
        outer.addWidget(author)

        app_name = QLabel(version.APP_NAME_ZH)
        app_name.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{theme.SPLASH_APPNAME_PT}pt;"
        )
        app_name.setAlignment(Qt.AlignCenter)
        _selectable(app_name)
        outer.addWidget(app_name)

        meta = QLabel(f"v{version.VERSION}")
        meta.setStyleSheet(f"color:{theme.TEXT_TERTIARY}; font-size:{theme.SPLASH_META_PT}pt;")
        meta.setAlignment(Qt.AlignCenter)
        _selectable(meta)
        outer.addWidget(meta)

        outer.addSpacing(22)

        # ---------- ② 声明区（F-10.1c）----------
        free = QLabel(version.FREE_STATEMENT)
        free.setStyleSheet(f"color:{theme.TEXT_SECONDARY}; font-size:{theme.SPLASH_BODY_PT}pt;")
        free.setAlignment(Qt.AlignCenter)
        _selectable(free, wrap=True)
        outer.addWidget(free)

        # 渠道名以**纯文字**展示，不带任何链接（F-10.1e）
        channel = QLabel("官方发布渠道：" + " / ".join(version.RELEASE_CHANNELS))
        channel.setStyleSheet(f"color:{theme.TEXT_SECONDARY}; font-size:{theme.SPLASH_BODY_PT}pt;")
        channel.setAlignment(Qt.AlignCenter)
        _selectable(channel)
        outer.addWidget(channel)

        outer.addSpacing(18)

        # ---------- ③ 纯文字支持行（F-10.1d / F-10.1g）----------
        # 关键：非链接色、无下划线、无 hover、点击无任何响应。
        # 它只负责「告知支持方式存在」，不提供入口 —— 入口在顶部栏与关于页。
        self.lbl_support = QLabel(version.SPLASH_SUPPORT_LINE)
        self.lbl_support.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{theme.SPLASH_SUPPORT_PT}pt;"
            "text-decoration:none;"           # 明确声明无下划线
        )
        self.lbl_support.setAlignment(Qt.AlignCenter)
        _selectable(self.lbl_support)
        outer.addWidget(self.lbl_support)

        outer.addSpacing(16)

        # ---------- ④ 按钮区（唯一可点元素，F-10.3 / F-10.3b）----------
        self.lbl_loading = QLabel("正在加载主界面…")
        self.lbl_loading.setStyleSheet(f"color:{theme.TEXT_TERTIARY}; font-size:9pt;")
        self.lbl_loading.setAlignment(Qt.AlignCenter)
        _selectable(self.lbl_loading)
        self.lbl_loading.hide()
        outer.addWidget(self.lbl_loading)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)

        self.btn_enter = QPushButton("进入工具")
        # 需求文档 F-10.3b：**实心主色**（不得用描边 ghost）、≥ 120×36、文案明确。
        self.btn_enter.setObjectName("primaryButton")
        self.btn_enter.setMinimumSize(*version.SPLASH_BUTTON_MIN_SIZE)
        self.btn_enter.setCursor(Qt.PointingHandCursor)
        self.btn_enter.setDefault(True)          # 回车即可关闭
        self.btn_enter.clicked.connect(self._enter)
        row.addWidget(self.btn_enter)
        outer.addLayout(row)

        # 弹窗一打开就聚焦按钮（F-10.3b）
        self.btn_enter.setFocus()

    # ------------------------------------------------------------ 交互
    def _enter(self) -> None:
        self._finish()

    def _finish(self) -> None:
        """只发一次 enter_requested —— 点按钮、按 Esc、点 × 三条路都汇到这里。"""
        if self._done:
            return
        self._done = True
        self.enter_requested.emit()
        self.close()
        self.hide()

    def set_loading(self, loading: bool) -> None:
        """主界面还没建好时，把按钮临时禁用并给出说明。

        需求文档 F-10.5 要求「关闭后不得出现空白窗口或长时间转圈」，所以
        这里不是转圈，而是一句明确的中文说明 + 一个禁用状态的按钮。
        """
        self.lbl_loading.setVisible(loading)
        self.btn_enter.setEnabled(not loading)
        if not loading:
            self.btn_enter.setFocus()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """F-10.3d：保留 Esc 关闭（明确的关闭意图，误触概率远低于「任意键」）。

        刻意**不实现**「点击任意位置」「按任意键」—— 那两种都是误操作制造机。
        """
        if event.key() == Qt.Key_Escape:
            self._finish()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        """标题栏 × / Alt+F4 / 任务栏关闭都会走到这里。

        F-10.3c：这些是系统级机制，**不拦截**，只是把「窗口被关了」这件事
        转成「用户想进主界面」，免得关掉弹窗后什么也不剩下。
        """
        self._finish()
        super().closeEvent(event)
