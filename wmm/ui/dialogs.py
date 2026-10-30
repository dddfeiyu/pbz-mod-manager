# -*- coding: utf-8 -*-
"""对话框：扫描目录（F-2.4）、关于与支持（10.3）、B 站跳转确认（F-10.24）。

「关于与支持」页做成**一个对话框**而不是独立页面，是因为需求文档 F-10.18f 要求
「关于」与「支持」合在一处、不要再单开一个「关于」页 —— 一个对话框天然满足
"一处"，也不会出现两处信息不一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

from PySide6.QtCore import QDir, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from . import winfolders      # 原生「可多选」文件夹框（Qt 没暴露这个能力）


# ================================================================ 无声对话框
# 用户 2026-10-11 连续两次反馈：
#   ①「点击路径时，有提示音」→「不要出现提示音」
#   ②「右键点击选择查看详情时，直接显示详情，不要二次点击。并且不要出现提示音。」
#
# 两个问题的根源都在 `QMessageBox`：
#   · 提示音 —— QMessageBox 的 **图标** 在 Windows 上会触发系统提示音；
#   · 二次点击 —— `setDetailedText()` 会生成一个「显示详情」按钮，
#     用户必须再点一次才看得到完整内容（本工具原来就是这么写的）。
# 所以这里自绘一个 QDialog：**没有图标**（因而没有提示音）、
# **正文就是完整内容**（因而不需要二次点击），长内容内部滚动且可全选复制。
#
# ⚠️ 以后凡是"给用户看一段文字"，优先用这里两个函数，别再退回 QMessageBox。


class SilentDialog(QDialog):
    """无图标、一次摊开全部内容的对话框（见上方注释，别改回 QMessageBox）。

    三种结局：`choice` ∈ {"yes", "no", "cancel"}。
      · yes   = 主按钮（粉色实心，`#primaryButton` 同款）；
      · no    = 第二按钮（传了 `no_text` 才出现；同样可以是粉色主样式，
                见 delete_selected 的「否，只删库」—— 用户 2026-10-18：
                「大部分人只是删除库，把否做大点醒目点，跟安装所选同样的粉色」）；
      · cancel= 最右边的收尾按钮（reject）。
    `default` 决定回车/初始焦点落在哪颗上（"yes"/"no"/"cancel"）——
    会动用户文件的弹窗一律默认 cancel 或 no，手滑不该造成后果。
    """

    def __init__(
        self,
        parent=None,
        *,
        title: str,
        heading: str = "",
        body: str = "",
        yes_text: str = "",
        no_text: str = "",
        cancel_text: str = "",
        default_yes: bool = False,
        default: str = "cancel",
        yes_primary: bool = False,
        no_primary: bool = True,
    ) -> None:
        super().__init__(parent)
        # ⚠️ 标题保底（用户 2026-10-18 报的事故）：空标题的 QDialog 在
        # Windows 上标题栏会显示 **applicationName**（PyInstaller 环境 =
        # "WuchangModManager"），用户看到的就是一个标题 "Wucha..." 的
        # 来路不明窗口。宁可显示「提示」，也绝不留空标题。
        title = (title or "").strip() or "提示"
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(460, 260)
        self.resize(600, 440)
        # 明确不要 QMessageBox 那套图标语义 —— 也就不会有提示音
        self.choice = "cancel"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        if heading:
            lbl = QLabel(heading)
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.NoTextInteraction)
            f = lbl.font()
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() + 1.0)
            lbl.setFont(f)
            layout.addWidget(lbl)

        self.view = QPlainTextEdit()
        self.view.setPlainText(body)
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        # 可选可复制，但**不带**按下高亮/提示音（那是 TextSelectableByMouse
        # 在 QLabel 上的老问题，QPlainTextEdit 自身是编辑控件，不受影响）
        self.view.setStyleSheet(
            f"QPlainTextEdit {{ background:{theme.BG_SUNKEN}; color:{theme.TEXT};"
            f" border:1px solid {theme.BORDER}; border-radius:6px; padding:6px; }}"
        )
        layout.addWidget(self.view, 1)

        # ---- 按钮行：[主/否（粉）] [次] [取消] ----
        # 顺序按用户 2026-10-18 的要求：最常见、最安全的动作放最左且最大最艳。
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)

        def _style(btn: QPushButton, *, primary: bool) -> None:
            btn.setCursor(Qt.PointingHandCursor)
            if primary:
                # 与工具条「安装所选」完全同款：objectName 走应用级 QSS 的
                # #primaryButton（theme.PRIMARY 粉底白字）。尺寸必须用控件
                # 自身样式表/代码给 —— QSS 的 min-width 会覆盖 setMinimumSize。
                btn.setObjectName("primaryButton")
                btn.setMinimumHeight(36)
                btn.setStyleSheet(
                    "QPushButton#primaryButton { padding: 6px 18px; font-weight: 600; }"
                )
            else:
                btn.setMinimumHeight(36)

        btn_no = None
        btn_yes = None
        if no_text:
            btn_no = QPushButton(no_text)
            _style(btn_no, primary=no_primary)
            btn_no.clicked.connect(self._on_no)
            row.addWidget(btn_no)
        if yes_text:
            btn_yes = QPushButton(yes_text)
            _style(btn_yes, primary=yes_primary)
            btn_yes.clicked.connect(self._on_yes)
            row.addWidget(btn_yes)
        btn_close = QPushButton(cancel_text or "确定")
        _style(btn_close, primary=False)
        btn_close.clicked.connect(self.reject)
        row.addWidget(btn_close)
        layout.addLayout(row)

        # 默认焦点：优先按 default 参数，其次 legacy 的 default_yes
        want = default if default in {"yes", "no", "cancel"} else (
            "yes" if default_yes else "cancel"
        )
        target = {
            "yes": btn_yes,
            "no": btn_no,
            "cancel": btn_close,
        }.get(want) or btn_close
        target.setDefault(True)
        target.setFocus()

    def _on_yes(self) -> None:
        self.choice = "yes"
        self.accept()

    def _on_no(self) -> None:
        self.choice = "no"
        self.accept()


def show_text(parent, title: str, body: str, *, heading: str = "") -> None:
    """给用户看一段文字（替代 `QMessageBox.information`，无提示音、不用二次点击）。"""
    dlg = SilentDialog(parent, title=title, heading=heading, body=body)
    dlg.exec()


def ask(
    parent,
    title: str,
    body: str,
    *,
    heading: str = "",
    yes_text: str = "确定",
    default_yes: bool = False,
) -> bool:
    """问用户是/否（替代 `QMessageBox.question`）。

    ⚠️ 默认按钮是「取消」（`default_yes=False`）—— 这些动作都会动用户的文件，
    回车手滑不该造成后果。
    """
    dlg = SilentDialog(
        parent,
        title=title,
        heading=heading,
        body=body,
        yes_text=yes_text,
        cancel_text="取消",
        default_yes=default_yes,
    )
    dlg.exec()
    return dlg.choice == "yes"

from .. import importer, theme, version


# ---------------------------------------------------------------- B 站跳转
def open_bilibili(parent: QWidget, url: str, what: str = "B站页面") -> bool:
    """打开 B 站链接（点击后**直接跳转**，不弹确认框）。

    ⚠️ 需求变更（用户 2026-10-10，原话）：
      「这个框不要弹，点完按钮直接跳网站。」
    需求文档 F-10.24 原本要求「跳转前必须弹一次确认」，理由是避免用户以为
    程序被劫持、并降低安全软件的告警。**用户明确要求去掉这一步**，
    以用户指示为准 —— 因此这里不再弹 QMessageBox.question。

    保留的部分：
      · **失败回退** —— `openUrl` 返回 False（没有默认浏览器 / 被拦截）时，
        仍把完整 URL 以**可选中纯文本**摆出来让用户手动复制，
        否则用户就彻底没路了；
      · URL 一律来自 `wmm/version.py` 的硬编码常量，不从配置读（F-10.23）。
    """
    opened = QDesktopServices.openUrl(QUrl(url))
    if opened:
        return True

    # 回退：把 URL 变成可选中、可复制的纯文本。
    # ⚠️ 别再退回 QMessageBox + setDetailedText + 手工 addWidget(label, 1, 1) 那套：
    #   那份代码 setTextInteractionFlags 连设两次（后一个覆盖前一个，首行是死代码），
    #   且按 QGridLayout 坐标硬塞控件，Qt 内部布局行列数一变就静默失败或越界。
    #   SilentDialog 的 body 是只读 QPlainTextEdit —— 可选、可复制、不响提示音，
    #   「让用户手动复制网址」这个核心需求一点没丢。
    show_text(
        parent,
        "没能打开浏览器",
        "系统没有成功打开浏览器。请手动复制下面的网址访问：\n\n" + url,
    )
    return False


# ---------------------------------------------------------------- 扫描目录
class ScanDialog(QDialog):
    """需求文档 F-2.4：指定一个目录，列出里面新发现的 mod 供一键导入。

    2026-10-11 起由「扫描 mod…」下拉的两个入口共用（扫游戏里已装的 / 扫电脑上的
    文件夹），所以标题不再写死「扫描目录」。
    """

    def __init__(self, root: Path | str, candidates: Sequence[importer.ScanCandidate], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("扫描 mod")
        self.setMinimumSize(640, 460)
        self._candidates = list(candidates)
        self._selected_paths: List[Path] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel(f"在「{root}」里发现 {len(candidates)} 个可导入的 mod")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        hint = QLabel(
            "勾选要导入的项。标记为「可能已导入」的项，其来源文件名与库里已有的记录相同 —— "
            "导入时会按内容指纹再判一次，重复的会自动跳过。"
        )
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        self.list.setAlternatingRowColors(False)
        for cand in candidates:
            item = QListWidgetItem(self._describe(cand))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked if cand.already_likely else Qt.Checked)
            item.setData(Qt.UserRole, str(cand.path))
            item.setToolTip(str(cand.path))
            self.list.addItem(item)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all(Qt.Checked))
        select_none = QPushButton("全不选")
        select_none.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        buttons.addWidget(select_all)
        buttons.addWidget(select_none)
        buttons.addStretch(1)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("导入选中的项")
        box.button(QDialogButtonBox.Ok).setObjectName("primaryButton")
        box.button(QDialogButtonBox.Cancel).setText("取消")
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        layout.addLayout(buttons)

    def _describe(self, cand: importer.ScanCandidate) -> str:
        kind = {"archive": "压缩包", "pak": "pak 文件"}.get(cand.kind, "文件夹")
        bits = [f"[{kind}] {cand.label}"]
        if cand.pak_count:
            bits.append(f"{cand.pak_count} 个 pak")
        if cand.size:
            bits.append(_human(cand.size))
        if cand.hint:
            bits.append(f"目标 {cand.hint}")
        if cand.already_likely:
            bits.append("可能已导入")
        return "  ·  ".join(bits)

    def _set_all(self, state) -> None:
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def _accept(self) -> None:
        self._selected_paths = [
            Path(self.list.item(i).data(Qt.UserRole))
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.Checked
        ]
        if not self._selected_paths:
            QMessageBox.information(self, "没有选中任何项", "请至少勾选一个要导入的项。")
            return
        self.accept()

    def selected_paths(self) -> List[Path]:
        return self._selected_paths


def _human(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


# ---------------------------------------------------------------- 选文件夹（多选）
class ImportPickerDialog(QDialog):
    """统一的「导入 mod」选择器 —— 顶部栏那个唯一的导入按钮用它。

    一个入口同时覆盖四种输入（用户 2026-10-10 要求「一个按钮既可以导入一个
    也可以导入多个就可以了」）：**一个文件夹 / 多个文件夹 / 单个 pak 或压缩包 /
    多个 pak 或压缩包**，也能混选。

    为什么要自己画一个（而不是用现成的 QFileDialog）：

      1. `QFileDialog.getExistingDirectory()` 走 Windows **原生**对话框，
         签名上就只能返回**一个**目录。用户按住 Ctrl 选中 5 个文件夹再点
         「打开」，Windows 的行为是**进入**其中一个子目录 —— 用户实际踩到的
         就是这个（2026-10-10：「导入多个 mod 时，点击打开无法导入，
         会直接进入其中一个 mod 的子文件夹」）。
      2. 换成 Qt 自带（非原生）对话框 + 把内部视图设成多选同样不行：
         实测 `QFileDialog(Directory)` 的内部 `listView` 把**驱动器列表**
         当内容显示，`selectedFiles()` 拿不回选中的目录（返回的还是当前目录）。

    所以这里用 `QTreeView` + `QFileSystemModel`（只显示目录）+ 扩展选择自拼：

      · 单击选中，**Ctrl 点选多个 / Shift 选一段**
      · 双击目录 = 进入下一层，双击**文件** = 直接导入它
      · 「上一级」回退，右侧有桌面 / 下载快捷入口
      · 一个都没选也允许确定 —— 那就按「导入当前文件夹」处理
        （用户很可能已经双击进到目标文件夹里了）

    `selected_paths()` 返回选中的绝对路径（文件与目录都有，已去重、保持选中顺序）。
    """

    def __init__(self, title: str, start_dir: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(760, 540)

        self._cwd: str = ""
        self._selected: List[str] = []

        # 同时列「文件夹」与「支持的 mod 文件」。
        # ⚠️ setNameFilterDisables(False) = 不匹配的文件**隐藏**（而不是灰掉），
        # 否则一屏灰文件比目录还多，反而看不清能选什么。
        # 目录不受 nameFilters 影响，始终可见 —— 正是我们要的。
        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.model.setNameFilters(["*.pak", "*.ucas", "*.utoc", "*.zip", "*.7z", "*.rar"])
        self.model.setNameFilterDisables(False)
        self.model.setReadOnly(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel("选中一个或多个 mod —— 文件夹 / 压缩包 / pak 文件都行")
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)

        # ⚠️ 这里**不要写 markdown 的 `**加粗**`** —— QLabel 不认，会把星号原样画出来
        #   （离屏渲染实锤）。要强调就用「」，与程序里其他地方一致。
        hint = QLabel(
            "· 选「文件夹」：里面有 pak 就算一个 mod；只选上一级也行，里面的子文件夹会被各自导入\n"
            "· 「选文件夹…」可以在系统框里按住 Ctrl 一次选多个；也可以在下面的列表里按住 Ctrl 点选\n"
            "· 选「文件」：.pak / .zip / .7z / .rar 直接导入，可一次选多个（也可以点「浏览文件…」）\n"
            "· 双击文件夹进入下一层，「上一级」回退"
        )
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ---- 导航行：上一级 + 当前路径 + 快捷入口 ----
        nav = QHBoxLayout()
        nav.setSpacing(6)
        btn_up = QPushButton("上一级")
        btn_up.setCursor(Qt.PointingHandCursor)
        btn_up.clicked.connect(self._go_up)
        nav.addWidget(btn_up)

        self.lbl_cwd = QLabel("")
        self.lbl_cwd.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_cwd.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        self.lbl_cwd.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        nav.addWidget(self.lbl_cwd, 1)

        for label, path in (
            ("桌面", str(Path.home() / "Desktop")),
            ("下载", str(Path.home() / "Downloads")),
        ):
            quick = QPushButton(label)
            quick.setCursor(Qt.PointingHandCursor)
            quick.clicked.connect(lambda _=False, p=path: self._goto(p))
            nav.addWidget(quick)

        # 按钮经历了五轮来回（用户 2026-10-11）：
        #   ①「添加一个浏览文件路径的按钮」→ 加了「浏览文件…」
        #   ②「删除此按钮」→ 删掉它，只留「选文件夹…」
        #   ③「这个按钮只能导入文件夹，让他也能导入 ZIP 和 pak」→ 合并成一个「浏览…」
        #   ④「你这个浏览按钮不能批量导入文件夹了。如果不能兼得的话，
        #      那就像原来那样两个按钮吧。」→ **回到两个**
        #   ⑤「这个按钮必须有批量导入文件夹的功能」→ 「选文件夹…」改成
        #      **原生可多选**（`winfolders.pick_folders()`，见该方法注释）。
        #
        # ③ 的合并方案在 ④ 被否掉，原因值得记下来：原生「打开」框里目录和文件
        #   确实混列，但**它不能多选文件夹** —— Windows 在目录上按 Ctrl 多选后
        #   点确定，只返回**一个**目录。这正是当初放弃原生框、自绘这棵树的原因。
        # ⑤ 从**另一个方向**解掉了这个死结：不去借「打开」框，而是直接调
        #   `IFileOpenDialog` + `FOS_PICKFOLDERS | FOS_ALLOWMULTISELECT`，
        #   于是"选文件夹"这条路上多选也能成立了。两个按钮各管各的、都可多选。
        btn_browse_file = QPushButton("浏览文件…")
        btn_browse_file.setObjectName("browseButton")
        btn_browse_file.setToolTip(
            "打开系统文件浏览器，可多选 .pak / 压缩包（能翻别的盘、直接输入路径）"
        )
        btn_browse_file.setCursor(Qt.PointingHandCursor)
        btn_browse_file.clicked.connect(self._browse_files)
        nav.addWidget(btn_browse_file)

        btn_browse_dir = QPushButton("选文件夹…")
        btn_browse_dir.setObjectName("browseButton")
        btn_browse_dir.setToolTip(
            "用系统对话框选一个或多个文件夹（按住 Ctrl 可多选；能翻别的盘、直接输入路径）"
        )
        btn_browse_dir.setCursor(Qt.PointingHandCursor)
        btn_browse_dir.clicked.connect(self._browse_dir)
        nav.addWidget(btn_browse_dir)

        layout.addLayout(nav)

        # ---- 目录树 ----
        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setUniformRowHeights(True)
        self.view.setHeaderHidden(True)
        # 双击是「进入下一层」，不要再让它顺手展开
        self.view.setExpandsOnDoubleClick(False)
        self.view.doubleClicked.connect(self._on_double_clicked)
        for col in (1, 2, 3):                    # 隐藏 大小/类型/修改时间 三列
            self.view.hideColumn(col)
        layout.addWidget(self.view, 1)

        # ---- 底部：已选计数 + 确定/取消 ----
        footer = QHBoxLayout()
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        footer.addWidget(self.lbl_count)
        footer.addStretch(1)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.btn_ok = box.button(QDialogButtonBox.Ok)
        self.btn_ok.setObjectName("primaryButton")
        self.btn_ok.setCursor(Qt.PointingHandCursor)
        box.button(QDialogButtonBox.Cancel).setText("取消")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        footer.addWidget(box)
        layout.addLayout(footer)

        self.view.selectionModel().selectionChanged.connect(self._refresh_selection)
        self._goto(start_dir or str(Path.home()))

    # ------------------------------------------------------------ 导航
    def _goto(self, path: str) -> None:
        p = Path(path) if path else Path.home()
        while not p.is_dir() and p != p.parent:     # 起点失效时往上找
            p = p.parent
        self._cwd = str(p)
        self.model.setRootPath(self._cwd)           # QFileSystemModel 是异步加载的
        self.view.setRootIndex(self.model.index(self._cwd))
        self.view.clearSelection()
        self.lbl_cwd.setText(self._cwd)
        self.lbl_cwd.setToolTip(self._cwd)
        self._refresh_selection()

    def _go_up(self) -> None:
        parent = Path(self._cwd).parent
        if str(parent) != self._cwd:                # 已经在盘符根就不动
            self._goto(str(parent))

    def _on_double_clicked(self, index) -> None:
        path = self.model.filePath(index)
        if not path:
            return
        if Path(path).is_dir():
            self._goto(path)
        else:
            # 双击一个 mod 文件 = 就是要导它，不用再点确定
            self.accept()

    # ------------------------------------------------------------ 选择
    def _refresh_selection(self) -> None:
        ordered: List[str] = []
        for index in self.view.selectionModel().selectedIndexes():
            path = self.model.filePath(index)
            if path and path not in ordered:        # 一个条目 4 列各有一个 index
                ordered.append(path)
        self._selected = ordered
        n = len(ordered)
        if n:
            n_files = sum(1 for p in ordered if Path(p).is_file())
            n_dirs = n - n_files
            bits = []
            if n_dirs:
                bits.append(f"{n_dirs} 个文件夹")
            if n_files:
                bits.append(f"{n_files} 个文件")
            self.lbl_count.setText("已选 " + " + ".join(bits))
            self.btn_ok.setText(f"导入选中的 {n} 项")
        else:
            self.lbl_count.setText("已选 0 项 —— 将导入当前文件夹")
            self.btn_ok.setText("导入当前文件夹")

    def selected_paths(self) -> List[str]:
        """选中的文件与目录；一个都没选时退化成「当前文件夹」。"""
        if self._selected:
            return list(self._selected)
        return [self._cwd] if self._cwd else []

    # ------------------------------------------------------------ 系统文件框
    def _browse_files(self) -> None:
        """系统原生文件框 → **只选文件**（.pak / .zip / .7z / .rar，可多选）。

        ⚠️ 为什么这个框**只管文件、不管文件夹**：
          Windows 原生「打开」框里目录和文件混列，但**多选目录不成立** ——
          按住 Ctrl 选 5 个文件夹再点确定，它只会**进入**其中一个并返回那一个
          （这正是当初放弃原生框、自绘这棵树的原因）。
          所以目录一律走 `_browse_dir`（单选）或上面的自绘树（多选）。

        ⚠️ 过滤器**故意留空**（"所有文件"）：用户要的就是"能选 ZIP 和 pak"，
          限定扩展名反而会在某些系统上隐藏掉命名不标准的中文 mod 包。
        """
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 mod 文件（可按住 Ctrl 多选）",
            self._cwd,
        )
        files = [f for f in files if f]
        if not files:
            return
        # 原生框一次就给最终答案，直接关闭 —— 让用户再点一次「导入选中的 N 项」
        # 纯属多余的一次点击。
        self._selected = list(dict.fromkeys(files))
        self.accept()

    def _browse_dir(self) -> None:
        """系统原生文件夹框 → **可多选**。

        用户 2026-10-11：「这个按钮必须有批量导入文件夹的功能。」

        为什么不用 Qt 现成的两口子（都会在这里失效）：
          · `getExistingDirectory()` —— 一次只能选**一个**目录，Qt 没暴露多选；
          · `getOpenFileNames()`     —— 能多选**文件**，但在目录上按住 Ctrl
            多选再点确定，Windows 只会「进入」其中一个、**只返回那一个**。
        所以走 `winfolders.pick_folders()`，直调 Windows 的 `IFileOpenDialog`，
        带上 `FOS_PICKFOLDERS | FOS_ALLOWMULTISELECT`。

        ⚠️ 原生多选不可用时（非 Windows / COM 失败 / 解析失败）**必须回退**到
        `getExistingDirectory()` 的单选 —— 宁可退化成"一次一个"，也不能点了没反应。
        ⚠️ 嵌套目录由 `main_window` 的 `importer.drop_nested_paths()` 收口
        （父目录和它下面的子目录被一起选中的情形很常见），这里只去重。
        """
        folders = winfolders.pick_folders(self, "选择一个或多个文件夹（可按住 Ctrl 多选）")
        if folders is None:
            one = QFileDialog.getExistingDirectory(self, "选择一个文件夹", self._cwd)
            folders = [one] if one else []
        folders = [f for f in folders if f]
        if not folders:
            return
        self._selected = list(dict.fromkeys(folders))
        self.accept()


# ---------------------------------------------------------------- 关于与支持
class AboutDialog(QDialog):
    """需求文档 10.3「关于与支持」页。

    分区顺序是硬要求（原文）：作者信息 → 免费声明 → **充电支持卡片** → 免责声明 → 发布渠道。
    充电卡片必须落在**首屏可见区域内**，所以它排在第三区、紧随免费声明之后，
    而不是被免责声明的长段落顶到要滚动才能看到的地方。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(version.ABOUT_PAGE_TITLE)
        self.setMinimumWidth(520)
        # 给定一个**不超过常见笔记本可用高度**的默认尺寸，让下面的滚动区真正起作用。
        # 这样 F-10.18b 的「充电按钮必须在首屏可见区域内」才是一条可检验的要求 ——
        # 如果让对话框被内容撑到无限高，那条要求就自动"通过"了，等于没检查。
        self.resize(560, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        scroll.setWidget(body)

        # ---- 标题 + 定位语（F-10.18e）----
        title = QLabel(version.ABOUT_PAGE_TITLE)
        # ⚠️ 字号/加粗写进**控件自身的样式表** —— 应用级 QSS 里的
        # `QWidget { font-size: 10pt }` 会覆盖 setFont()（见 qss.py 注释）。
        title.setStyleSheet("font-size:15pt; font-weight:700;")
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(title)

        tagline = QLabel(version.ABOUT_PAGE_TAGLINE)
        tagline.setWordWrap(True)
        tagline.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_BODY_FONT_PT}pt;"
        )
        tagline.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(tagline)

        # ---- 区 1：作者信息 ----
        layout.addWidget(_section("作者"))
        layout.addWidget(_kv("作者", version.AUTHOR_ID, bold=True))
        layout.addWidget(_text_row("B站主页", version.BILIBILI_SPACE_URL))
        layout.addWidget(_kv("版本", f"v{version.VERSION}"))
        # 用户 2026-10-11：「关于与支持中可以留个QQ粉丝群号：1057139483」
        # 放"作者"这一区的理由：群号是**找人**的入口，与作者身份同属一块，
        # 塞到"发布渠道"里会和"在哪下载"混在一起（一个是求助、一个是取文件）。
        layout.addWidget(_kv("QQ 粉丝群", version.QQ_GROUP_NUMBER))

        # ---- 区 2：免费声明 ----
        layout.addWidget(_section("免费声明"))
        free = QLabel(version.FREE_STATEMENT)
        free.setWordWrap(True)
        free.setTextInteractionFlags(Qt.TextSelectableByMouse)
        free.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_BODY_FONT_PT}pt;"
        )
        layout.addWidget(free)

        # ---- 区 3：充电支持卡片（首屏可见）----
        layout.addWidget(_section("支持作者"))
        # 用户 2026-10-10：「这个按钮做大点，就写充电支持作者 一键跳转B站。」
        charge = QPushButton(version.CHARGE_BUTTON_TEXT)
        charge.setObjectName("primaryButton")
        charge.setMinimumSize(*version.CHARGE_BUTTON_MIN_SIZE)
        # 字号/内边距**必须**写在控件自身的样式表里：应用级 QSS 的
        # `QWidget {{ font-size: 10pt }}` 会覆盖 setFont()（踩过，见 qss.py 注释）。
        charge.setStyleSheet(
            f"font-size:{version.CHARGE_BUTTON_FONT_PT}pt; font-weight:600; padding:10px 28px;"
        )
        charge.setCursor(Qt.PointingHandCursor)
        charge.setToolTip("一键跳转到作者 B站 主页（充电支持）")
        charge.clicked.connect(
            lambda: open_bilibili(self, version.BILIBILI_CHARGE_URL, "作者 B站 主页（充电）")
        )
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(charge)
        row.addStretch(1)
        layout.addLayout(row)

        # 顺序刻意是「先谢后说明」：感谢语在上，三条免责一句不动地在下面。
        # 用户 2026-10-12：「这里的文字过于严肃，在支持纯属自愿上面加一些感谢的话」。
        # ⚠️ 改这里前先读 `version.CHARGE_THANKS_LINE` 上面那两条红线注释。
        notes = (
            version.CHARGE_THANKS_LINE + "\n\n"
            "· 支持纯属自愿，与任何功能无关，不影响使用。\n"
            "· 支持不构成购买凭据，不承诺任何回报（更新、答疑、mod 支持均不因此产生义务）。\n"
            "· 将跳转到 B站，需要登录 B站账号；充值行为适用 B站平台规则。"
        )
        note_label = QLabel(notes)
        note_label.setWordWrap(True)
        note_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # 用户 2026-10-11：「这一页中的小字，字号太小」→ 从 9pt 提到 11pt
        note_label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_SMALL_FONT_PT}pt;"
        )
        layout.addWidget(note_label)

        # ---- 区 4：免责声明 ----
        layout.addWidget(_section("免责声明"))
        disc = QLabel(version.DISCLAIMER)
        disc.setWordWrap(True)
        disc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        disc.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_BODY_FONT_PT}pt;"
        )
        layout.addWidget(disc)

        # ---- 区 4b：第三方组件（2026-10-29 加）----
        # 内置了签名绕过前置（dsound.dll + UniversalSigBypasser.asi），
        # 其中 .asi 是 CC BY-NC 4.0 —— **保留署名是再分发的硬条件**，
        # 所以这一节不是"补充说明"，是必须出现在用户能看到的地方。
        layout.addWidget(_section("第三方组件"))
        third = QLabel(version.THIRD_PARTY_NOTICE)
        third.setWordWrap(True)
        third.setTextInteractionFlags(Qt.TextSelectableByMouse)
        third.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_SMALL_FONT_PT}pt;"
        )
        layout.addWidget(third)

        # ---- 区 5：发布渠道 ----
        layout.addWidget(_section("官方发布渠道"))
        channel = QLabel(
            "本工具仅通过 " + " / ".join(version.RELEASE_CHANNELS) + " 发布。"
            "其他渠道下载的版本请勿使用 —— 若被替换内容或植入后门，作者无法负责。"
        )
        channel.setWordWrap(True)
        channel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        channel.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_BODY_FONT_PT}pt;"
        )
        layout.addWidget(channel)

        # ---- 区 6：运行环境（F-11.1：系统要求必须在下载页 / 压缩包说明 /
        #      本页 三处之一明示。这里是最稳的一处 —— 下载页会改、说明文档会被删，本页跟着程序走）----
        layout.addWidget(_section("运行环境"))
        sys_label = QLabel(version.SYSTEM_REQUIREMENT_DISPLAY)
        sys_label.setWordWrap(True)
        sys_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sys_label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_BODY_FONT_PT}pt;"
        )
        layout.addWidget(sys_label)
        first_run = QLabel(
            "首次使用点一下顶部的「自动检测游戏目录」即可 —— 工具会自己去 "
            "Steam / Epic / WeGame / Xbox 的安装位置里找，找到就直接用，"
            "不需要你手动翻文件夹。\n"
            "只有实在找不到时，才需要手动指定一次（选到含 Project_Plague 的那一层）。"
            "没有安装游戏也能先用 —— 可以先把 mod 导入库，之后再指定路径。"
        )
        first_run.setWordWrap(True)
        first_run.setTextInteractionFlags(Qt.TextSelectableByMouse)
        first_run.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_BODY_FONT_PT}pt;"
        )
        layout.addWidget(first_run)

        # ---- 区 7：版本校验与防伪（F-10.27 / F-11.8 / F-11.22 / F-11.23）----
        layout.addWidget(_section("版本校验"))
        verify = QLabel(
            f"本版本：v{version.VERSION}。"
            f"官方安装包的 SHA-256 与最新版本号在 "
            f"{' / '.join(version.RELEASE_CHANNELS)} 主页公示，请以那里为准。\n"
            "这类绿色工具最常被人二次打包（替换内容或植入后门），"
            "版本号是你核对「是否为官方版」的唯一凭据 —— 若版本号对不上，请勿使用。"
        )
        verify.setWordWrap(True)
        verify.setTextInteractionFlags(Qt.TextSelectableByMouse)
        verify.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_BODY_FONT_PT}pt;"
        )
        layout.addWidget(verify)

        icon_line = QLabel(version.ICON_STATEMENT)
        icon_line.setWordWrap(True)
        icon_line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        icon_line.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_BODY_FONT_PT}pt;"
        )
        layout.addWidget(icon_line)

        if version.THIRD_PARTY_COMPONENTS:
            # 用户 2026-10-11 把「本工具未随包分发任何第三方程序（v1 不需要任何外部解析工具）」
            # 那句划掉了：括号里那半句是在解释"为什么这里是空的"，属于开发视角的自语，
            # 用户看到的只是一个空列表框加一句没头没尾的话。
            # 所以：**没有第三方组件时整块不显示**，而不是显示一句"没有"。
            comp = QLabel(
                "本工具包含以下第三方开源组件："
                + "；".join(version.THIRD_PARTY_COMPONENTS)
            )
            comp.setWordWrap(True)
            comp.setTextInteractionFlags(Qt.TextSelectableByMouse)
            comp.setStyleSheet(
                f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_SMALL_FONT_PT}pt;"
            )
            layout.addWidget(comp)

        layout.addStretch(1)

        # ---- 底部：只放一个关闭按钮。
        # F-10.16 明确本页不得出现第二个跳转按钮（三连关注在顶部栏）。
        footer = QHBoxLayout()
        footer.setContentsMargins(24, 0, 24, 18)
        footer.addStretch(1)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        outer.addLayout(footer)


def _section(text: str) -> QLabel:
    label = QLabel(text)
    # ⚠️ 字号/加粗必须写进**控件自身的样式表**：应用级 QSS 里有
    #   `QWidget { font-size: 10pt }`，它会覆盖 setFont()（见 qss.py 注释）。
    # 原来这里用 setFont(11pt)，实际渲染出来被压成了 10pt —— 用户 2026-10-11
    # 报「这一页中的小字太小」时一并修掉。
    label.setStyleSheet(
        f"font-size:{version.ABOUT_SECTION_FONT_PT}pt; font-weight:700;"
    )
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


def _kv(key: str, value: str, *, bold: bool = False) -> QLabel:
    label = QLabel(f"{key}：{value}")
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    # 同 _section：走自身样式表，别用 setFont（会被应用级 QSS 压掉）
    label.setStyleSheet(
        f"font-size:{version.ABOUT_KV_FONT_PT}pt;" + ("font-weight:700;" if bold else "")
    )
    return label


def _text_row(key: str, url: str) -> QWidget:
    """一行「键：可复制纯文本」——**故意不加「打开」按钮**。

    ⚠️ 这里踩过一个坑，记下来别再犯：最初这行是「纯文本 + 打开按钮」，
    但那样会同时踩两条硬要求：

      * **F-10.16**：「关于与支持」页**不设第二个跳转按钮**。三连关注按钮在
        顶部栏；本页如需提及，**只允许一行纯文字，不加按钮** —— 目的是
        避免本页出现两个都指向 B站 的按钮。验收标准里也写着
        「该页不出现第二个跳转按钮」。
      * **F-10.18d**：本页充电按钮用 BILIBILI_CHARGE_URL、顶部栏按钮用
        BILIBILI_SPACE_URL，**不可互相复用**。本行若用 SPACE_URL 就属复用。

    所以：地址照 F-10.3 区 1 的要求显示出来（文字 + 链接地址），但做成
    可选中复制的纯文本。用户想访问就复制走 —— 本页唯一可点的跳转元素只有
    下面那个充电按钮。
    """
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)

    label = QLabel(f"{key}：")
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    row.addWidget(label)

    url_label = QLabel(url)
    url_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    url_label.setStyleSheet(
        f"color:{theme.TEXT_SECONDARY}; font-size:{version.ABOUT_KV_FONT_PT}pt;"
    )
    row.addWidget(url_label)
    row.addStretch(1)
    return widget
