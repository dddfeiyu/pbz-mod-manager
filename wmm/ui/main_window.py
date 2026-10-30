# -*- coding: utf-8 -*-
"""主窗口（需求文档 3.1 主界面 + 10.2 顶部栏）。

布局：

    ┌─ 顶部栏 ─────────────────────────────────────────────────────┐
    │ [关注作者 · 一键跳转]              [关于与支持]                │
    │ 游戏目录：<路径> [自动检测游戏目录]   [更改目录][扫描 mod…][导入 mod…] │
    │                          ↑ 同一行：这是一件事的自动/手动两档      │
    ├─ 工具条 ─────────────────────────────────────────────────────┤
    │ [全部][已装][未装][冲突]        搜索…           共 N 个        │
    │ [安装所选][卸载所选][全选]      Mod Enabler 提示               │
    ├──────────────────────────────────────────────────────────────┤
    │ 卡片网格（流式布局，随宽度自适应列数）                          │
    └──────────────────────────────────────────────────────────────┘

    2026-10-11 两处位置调整（用户按截图指出）：
      1.「自动检测游戏目录」从工具条**挪到游戏目录那一行**（原话：「放到上方箭头处」）
        —— 它和「更改目录」本来就是同一件事（找游戏装在哪）的自动 / 手动两档，
        放在路径旁边才顺；工具条那行只留列表操作。
      2.「自动扫描游戏 mod 目录」+「扫描目录…」**合并成一个「扫描 mod…」下拉按钮**
        （原话：「这两个按钮是不是功能重复了？」）—— 见
        `_build_scan_menu_button()` 与 `version.SCAN_MENU_BUTTON_TEXT` 的注释。

   窗口**系统标题栏**（带最小化按钮那条）：
    明末MOD管理器 v0.1.x - by 诸葛-建国 · 完全免费 · 无付费版 · 官方发布：B站
    （2026-10-11 用户要求：程序名 / 版本 / 作者这一行**从程序内删除**，
      只保留在系统标题栏；免费声明 + 发布渠道也一并挪进系统标题栏。）

窄屏处理（R-18 规定：按钮永不折叠）：
    程序内不再有可折叠的程序名 / 作者文字，两个入口按钮始终完整显示。
    「游戏目录：」那一行里唯一的弹性件是**路径标签**（宽度由
    `_refresh_game_dir_text` 按窗口宽度算出来），所以窗口变窄时先缩短的是
    路径显示，按钮一个都不会被压扁。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFontMetrics, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import (
    catalog,
    conflicts,
    costume,
    enabler,
    gamedir,
    importer,
    installer,
    library as library_mod,
    modstore,
    pakfiles,
    settings as settings_mod,
    theme,
    version,
    wildscan,
)
from . import dialogs
from .card import ModCard
from .flowlayout import FlowLayout, FlowWidget
from .worker import ImportWorker


def _human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def fit_window_to_screen(width: int, height: int, avail_w: int, avail_h: int) -> Tuple[int, int]:
    """把启动窗口尺寸压进屏幕可用区域（2026-10-13 加，R-18 之外的窄屏/高 DPI 兜底）。

    为什么需要：Qt6 的自动 DPI 缩放只保证「界面不变形」，**不保证「窗口在屏幕内」**。
    1366×768 @150% 的逻辑分辨率只有 911×512（扣任务栏后可用高约 464），而
    `version.MIN_WINDOW_WIDTH/HEIGHT`（1000×640）和 `*_HARD`（720×520）都放不进去，
    底部工具栏会落在屏幕外面点不到。

    行为（三个保证，见 tests/test_window_fit.py）：
      1. 正常屏幕下**恒等** —— 四个表达式算出来就等于传进来的 width/height；
      2. 放不下时收缩到可用区域（留 16px 给窗口阴影/边缘）；
      3. 极端小屏允许**临时低于硬最小尺寸**，保证窗口完整可见
         （硬最小是「用户能拖到的最小值」，不是「必须这么大」）。
    """
    room_w = max(1, avail_w - 16)
    room_h = max(1, avail_h - 16)
    out_w = min(int(width), room_w)
    out_h = min(int(height), room_h)
    out_w = max(out_w, min(version.MIN_WINDOW_WIDTH_HARD, room_w))
    out_h = max(out_h, min(version.MIN_WINDOW_HEIGHT_HARD, room_h))
    return out_w, out_h


class MainWindow(QMainWindow):
    def __init__(
        self,
        app_settings: settings_mod.Settings,
        store: modstore.ModStore,
        lib: library_mod.Library,
    ) -> None:
        super().__init__()
        self.settings = app_settings
        self.store = store
        self.library = lib

        self._views: List[catalog.ModView] = []
        self._cards: Dict[str, ModCard] = {}
        #: 已勾选（准备批量安装/卸载）的 mod id。
        #: 用 id 集合而不是"卡片对象是否勾选"——卡片会因为筛选、搜索被整块重建，
        #: 存对象的话勾选状态会在重建时丢掉。
        self._checked_ids: set[str] = set()
        self._filter = catalog.FILTER_ALL
        self._worker: Optional[ImportWorker] = None
        self._progress_dialog: Optional[QProgressDialog] = None
        #: 缓存一次游戏目录探测结果 —— 冲突检测要按平台算目标文件名，
        #: 每次 refresh 都重新探测（改一次筛选就探一次）没必要。
        self._probe: Optional[gamedir.ProbeResult] = None
        self._probe_key: str = ""
        #: 最近一次冲突检测结果（详情对话框和状态栏读它）
        self._conflict_report: Optional[conflicts.ConflictReport] = None
        #: 「手装」识别结果（用户 2026-10-12）：`{mod_id: wildscan.ExternalHit}`。
        #: 只放**缓存**里的数据 —— refresh 可能被筛选/搜索频繁触发，
        #: 在这里扫盘 + 算哈希会把界面卡住。真正的扫描在
        #: `_recognize_external()`（手动触发 / 导入安装卸载后）里做。
        self._external_hits: Dict[str, wildscan.ExternalHit] = {}

        # 用户 2026-10-10：「作者ID…放在最顶部，跟明末MOD管理器 v0.1.3 同一行」——
        # 窗口最上面那一条（系统标题栏）也把作者带上，这是全窗口最高的位置。
        # 注意 app.setApplicationDisplayName() 会被 Qt 自动追加到窗口标题后面，
        # 所以 app.py 里那句已经去掉，标题只由此处决定。
        # 用户 2026-10-11：「（完全免费 · 无付费版 · 官方发布）给我挪到带最小化按钮的
        # 系统标题栏、放在诸葛建国后边」→ 追加 TITLE_BAR_SUFFIX；程序内第 1 行里那段删除。
        self.setWindowTitle(
            f"{version.APP_NAME_ZH} v{version.VERSION} - by {version.AUTHOR_ID}"
            f"{version.TITLE_BAR_SUFFIX}"
        )
        default_w = max(version.MIN_WINDOW_WIDTH, int(self.settings.ui.get("window_width") or 0))
        default_h = max(version.MIN_WINDOW_HEIGHT, int(self.settings.ui.get("window_height") or 0))
        # 小屏 / 高 DPI 缩放保护（2026-10-13 加）：
        # Qt6 的自动 DPI 缩放只保证「界面不变形」，**不保证「窗口在屏幕内」**。
        # 实测 1366×768 @150% 的逻辑分辨率只有 911×512（扣任务栏后可用高约 464），
        # 而启动尺寸 1000×640、硬最小高 520 都放不进去 → 底部工具栏（安装/卸载那一排）
        # 会落在屏幕外面点不到。这里把启动尺寸压进当前屏幕的可用区域。
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            default_w, default_h = fit_window_to_screen(default_w, default_h, avail.width(), avail.height())
        # ⚠️ 硬最小必须**跟着 clamp 结果一起缩**：Qt 的 minimumSize 优先级高于 resize()，
        #    不缩的话 clamp 出来的 496 会被顶回 520，等于没修（2026-10-13 离屏探针实测）。
        #    正常屏下 min(硬最小, 启动尺寸) 恒等于硬最小，行为一个像素都不变。
        self.setMinimumSize(
            min(version.MIN_WINDOW_WIDTH_HARD, default_w),
            min(version.MIN_WINDOW_HEIGHT_HARD, default_h),
        )
        self.resize(default_w, default_h)

        self.setAcceptDrops(True)
        self._build_ui()
        self._build_shortcuts()
        self.refresh()


    # ================================================================ 构建
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("centralArea")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_top_bar())
        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_center(), 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self._set_status("就绪。把 zip / 7z / rar 压缩包或文件夹拖进窗口即可导入。")

    # ------------------------------------------------------------ 顶部栏
    def _build_top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topBar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(16, 10, 16, 10)
        outer.setSpacing(8)

        # --- 第 1 行：左「关注作者」 + 中「B站：诸葛-建国」 + 右「关于与支持」 ---
        # 用户 2026-10-11 的要求（原话）：
        #   「系统标题栏下方的明末mod管理器V0.1.5 BY诸葛建国删掉；
        #    关注作者，一键跳转，从右侧放到页面左侧，大小改成正常大小，不要这么大；
        #    然后右侧的关于与支持就放在右边，但是按钮放大一点」
        #   「（框里）写B站：诸葛-建国。跟关于与支持同一行，居中」
        # 即：程序名 / 版本 / 作者这一行**整行删除**（系统标题栏里已经有了），
        # 这一行三个元素 —— 关注作者靠左、B站 ID **居中**、关于与支持靠右。
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        # 需求文档 F-10.9b：实心「关注作者」。用户要求「正常大小」
        # —— 撤掉原先 196×44 + 11pt 的放大设置，回到 QSS 默认尺寸。
        # 颜色：粉色（primary）→ 黄色 → **蓝色**（用户 2026-10-11「这个颜色太丑了，换一个」）。
        self.btn_support = QPushButton(version.TOP_BAR_SUPPORT_TEXT)
        self.btn_support.setObjectName("accentButton")
        self.btn_support.setCursor(Qt.PointingHandCursor)
        self.btn_support.setToolTip("在浏览器打开作者的 B站 主页（一键跳转，不再弹确认框）")
        self.btn_support.clicked.connect(self._open_author_space)
        row1.addWidget(self.btn_support)

        # 余量分三份 → 作者 ID 恰好落在整行正中间。
        # ⚠️ 两侧各加一条 `addStretch(1)` 而不是只在右边加一条 ——
        #   只有右边有弹性项时它会吃掉全部余量，作者 ID 就贴在「关注作者」右边。
        row1.addStretch(1)

        # 用户 2026-10-11：「（框里）写B站：诸葛-建国。跟关于与支持同一行，居中」
        # 随后又要求：「中间加的作者名称也改成可以跳转的按钮，点击可以直接跳到B站主页。」
        # → 由 QLabel 改成 QPushButton，行为与左侧「关注作者」完全一致。
        #   ⚠️ 用 QPushButton 而不是给 QLabel 挂 mousePressEvent：QLabel 走
        #   TextSelectableByMouse 会响系统提示音，而自定义 mousePressEvent
        #   拿不到 hover 光标与焦点圈。按钮是原生控件，这些都由 Qt 负责。
        self.btn_author_id = QPushButton(version.TOP_BAR_AUTHOR_TEXT)
        self.btn_author_id.setObjectName("authorBadge")
        self.btn_author_id.setCursor(Qt.PointingHandCursor)
        self.btn_author_id.setToolTip("在浏览器打开作者的 B站 主页")
        self.btn_author_id.clicked.connect(self._open_author_space)
        row1.addWidget(self.btn_author_id)

        row1.addStretch(1)

        # 需求文档 F-10.9c：次要样式「关于与支持」。用户要求「按钮放大一点」
        # —— 比默认略大一号（尺寸走控件自身，QSS 的 min-height 会覆盖 setMinimumSize）。
        # 用户 2026-10-11 又要求改色：「关于与支持按钮改个颜色，绿色吧，绿色舒服点。」
        # → 加 `#successButton`（柔和的墨绿），与左侧粉色的「关注作者」拉开距离。
        self.btn_about = QPushButton(version.ABOUT_PAGE_TITLE)
        self.btn_about.setObjectName("successButton")
        self.btn_about.setMinimumSize(*version.ABOUT_BUTTON_MIN_SIZE)
        # ⚠️ 写全选择器而不是光秃秃一句 font-size：控件自身的样式表会**合并**到
        #   应用级 QSS 之上，带选择器才只覆盖这一条属性（见 MEMORY 的 Qt 铁律）。
        self.btn_about.setStyleSheet(
            f"QPushButton#successButton {{ font-size:{version.ABOUT_BUTTON_FONT_PT}pt; }}"
        )
        self.btn_about.setCursor(Qt.PointingHandCursor)
        self.btn_about.clicked.connect(lambda: dialogs.AboutDialog(self).exec())
        row1.addWidget(self.btn_about)

        outer.addLayout(row1)

        # --- 第 2 行：游戏目录 + 导入入口 ---
        row3 = QHBoxLayout()
        row3.setSpacing(8)

        dir_label = QLabel("游戏目录：")
        dir_label.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        row3.addWidget(dir_label)

        # 路径标签。用户 2026-10-11 前后提了三次：
        #  ①「游戏目录显示路径，这个位置太长了」→ 封顶 + 中间省略；
        #  ② 最终定稿：「游戏路径显示太长了，不需要显示这么多，只需要点击路径时，
        #     能显示全部路径即可，平常不需要这么长。」
        # → 所以**平时只显示最后一级文件夹名**（`Wuchang Fallen Feathers`），
        #   悬停出完整路径 tooltip，**点一下弹窗显示完整路径并可复制**。
        # 宽度仍然由代码算（见 `_refresh_game_dir_text`），只是显示的文本变短了，
        # 那一堆"别再用弹性撑满 / 别让它被压成 0"的坑依然成立，别改回去。
        self.lbl_game_dir = QLabel("尚未设置")
        self.lbl_game_dir.setObjectName("gameDirPath")
        # ⚠️ 用户 2026-10-11 对这条来回了四轮，终稿是：
        #   「路径栏缩短，且可以点击，点击后弹出完整路径，**但是不要出现提示音**。」
        # 于是：
        #   · 平时**缩短显示**（中间省略，保留盘符 + 末尾游戏名）—— 见 _refresh_game_dir_text；
        #   · 点一下用**自绘无声对话框**显示完整路径；
        #   · `setTextInteractionFlags` 保持 NoTextInteraction —— 实测给它
        #     `TextSelectableByMouse` 会让 Qt 按下时**响一声系统提示音**
        #     （用户上一轮报过「点击路径时，有提示音」，改掉后他才确认没声音了）。
        #     ⚠️ 所以"能点"要靠自己接 mousePressEvent，**绝不能靠可选中文本**。
        self.lbl_game_dir.setTextInteractionFlags(Qt.NoTextInteraction)
        self.lbl_game_dir.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.lbl_game_dir.setCursor(Qt.PointingHandCursor)
        self.lbl_game_dir.setToolTip("点一下看完整路径")
        self._game_dir_full = ""       # 完整路径（点击时弹出来）
        self._game_dir_short = ""      # 平时显示的缩短文本
        self.lbl_game_dir.mousePressEvent = self._show_game_dir_popup  # type: ignore[method-assign]
        row3.addWidget(self.lbl_game_dir)

        # 用户 2026-10-11：「删除steam这几个字」—— 原来这里有个 [Steam] 平台角标
        # （`btn_platform` QLabel，显示 `catalog.platform_badge` 的结果）。
        # 顶栏不再创建它；平台信息仍在路径 tooltip 与「更改目录」流程里。
        # ⚠️ 别手贱加回来 —— 用户明确要求顶栏不显示平台字样。

        # 用户 2026-10-11：「更改目录挪到自动检测游戏目录旁边」——
        # 自动/手动两档是同一件事，挨在一起才对；右半边留给扫描与导入。
        self.btn_autodetect = QPushButton("自动检测游戏目录")
        self.btn_autodetect.setMinimumWidth(version.ACTION_BUTTON_MIN_WIDTH)
        self.btn_autodetect.setCursor(Qt.PointingHandCursor)
        self.btn_autodetect.setToolTip(
            "在常见的 Steam / Epic / WeGame / Xbox 安装位置与 Windows 卸载登记表里\n"
            "搜索《明末：渊虚之羽》。找到唯一一处会直接应用；搜不到会提示你手动选。"
        )
        self.btn_autodetect.clicked.connect(self.autodetect_game_dir)
        row3.addWidget(self.btn_autodetect)

        btn_change = QPushButton("更改目录")
        btn_change.setMinimumWidth(version.ACTION_BUTTON_MIN_WIDTH)
        btn_change.setCursor(Qt.PointingHandCursor)
        btn_change.setToolTip("手动指定游戏根目录（选到含 Project_Plague 的那一层）")
        btn_change.clicked.connect(self.choose_game_dir)
        row3.addWidget(btn_change)

        # 余量**必须**由这里独占吃掉。踩过的坑（2026-10-11）：多出来的空白若没处去，
        # QHBoxLayout 会把它**平均分摊给每一个控件** —— 实测 1164 宽时「游戏目录：」
        # 标签被拉到 111px、按钮各胖一圈，整行像橡皮糖。
        row3.addStretch(1)
        self._row3 = row3               # 供 _refresh_game_dir_text 量"固定开销"

        # 用户 2026-10-11 先说「这两个按钮功能重复」→ 合成一个「扫描 mod…」；
        # 随后又说「这个按钮有点多余，感觉可以删除」→ 确认后**移进右键菜单**
        #   （原话选的是「移到右键菜单里」）。
        #
        # ⚠️ 功能**没删**，只是不再占用顶栏按钮位：顶栏现在只留「导入 mod…」一个
        #   主入口，更干净。扫描的两个来源（扫游戏里已装的 / 扫电脑上的文件夹）
        #   挪到卡片区右键菜单里，见 `_show_context_menu`。
        #   空列表引导页仍保留一个按钮 —— 那页右键菜单不方便发现。
        self.btn_scan = None                   # 顶栏已无此按钮；自检改查引导页那颗

        row3.addSpacing(12)

        # 全局**唯一**的导入入口（用户 2026-10-10：「为什么做两个导入按钮？
        # 一个按钮既可以导入一个也可以导入多个就可以了」）。
        # 它打开的是一个自绘选择器：文件夹（可多选）+ pak / 压缩包（可多选）都能选。
        btn_import = QPushButton("导入 mod…")
        btn_import.setObjectName("primaryButton")
        # 用户 2026-10-11 两次反馈按钮文字被切：「导入 mod，文字显示不全」
        # → 「这两个按钮，让文字全都显示出来」。见 version.IMPORT_BUTTON_MIN_WIDTH
        # 的注释：加粗字体让默认 sizeHint 只剩 ~2px 余量，必须显式给最小宽度。
        btn_import.setMinimumWidth(version.IMPORT_BUTTON_MIN_WIDTH)
        btn_import.setCursor(Qt.PointingHandCursor)
        btn_import.setToolTip(
            "导入 mod（Ctrl+O）：一个或多个都行\n"
            "· 选文件夹：里面有 pak 就算一个 mod\n"
            "· 选文件：.pak / .zip / .7z / .rar 直接导入"
        )
        btn_import.clicked.connect(self.pick_to_import)
        row3.addWidget(btn_import)
        self.btn_import = btn_import          # 自检要引用

        outer.addLayout(row3)
        return bar

    def _build_scan_menu_button(self) -> QPushButton:
        """「扫描 mod…」下拉按钮：两种扫描来源二选一。

        合并理由见 `version.SCAN_MENU_BUTTON_TEXT` 的注释。两个入口都保留
        （功能没砍），只是收进一个按钮的菜单里 —— 行里省下一个按钮位，
        也让"扫哪里"这个区别变成**选之前**就能看见的文字，而不是要靠 tooltip 猜。
        """
        btn = QPushButton(version.SCAN_MENU_BUTTON_TEXT)
        btn.setMinimumWidth(version.ACTION_BUTTON_MIN_WIDTH)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(
            "扫描 mod：点开后二选一\n"
            f"· {version.SCAN_MENU_GAME_TEXT}：自动定位游戏目录，扫其中的 ~mods / LogicMods（已装进游戏的 mod）\n"
            f"· {version.SCAN_MENU_FOLDER_TEXT}：扫你电脑上的一个文件夹（默认「下载」），找 zip / 7z / rar 与含 pak 的目录"
        )
        menu = QMenu(btn)
        act_game = menu.addAction(version.SCAN_MENU_GAME_TEXT)
        act_game.setToolTip("游戏里已经装了哪些 mod？扫出来可以批量导进本工具的库")
        act_game.triggered.connect(self.scan_game_mods)
        act_folder = menu.addAction(version.SCAN_MENU_FOLDER_TEXT)
        act_folder.setToolTip("例如「下载」文件夹 —— 里面下的 mod 压缩包会被列出来")
        act_folder.triggered.connect(self.scan_dir)
        btn.setMenu(menu)
        # ⚠️ 不要把 menu 存成 self._scan_menu：这个 helper 会被调用两次
        # （顶部栏 + 空列表引导页），后一次会覆盖前一次，自检就会拿到空页面那个。
        # 要拿菜单一律从按钮本身取：`btn.menu().actions()`。QMenu 挂在 btn 上，生命周期安全。
        return btn

    # ------------------------------------------------------------ 右键菜单
    def _show_context_menu(self, pos) -> None:
        """卡片区右键菜单（用户 2026-10-11：「扫描 mod…」按钮移到右键菜单里）。

        为什么放右键而不是直接删：扫描是需求文档 F-2.4 的核心功能 ——
        自动列出你下载目录里所有能导入的 mod，一键批量导。删了用户就只能一个
        文件夹一个文件夹手动挑。放右键后顶栏干净了，功能还在。

        ⚠️ 挂在 `self.scroll`（滚动区）上而不是 `cards_host`：空状态页时
        cards_host 不可见，但在那一页右键也应该能用这些命令。
        """
        menu = QMenu(self)
        act_game = menu.addAction(version.SCAN_MENU_GAME_TEXT)
        act_game.setToolTip("自动定位游戏目录，扫其中的 ~mods / LogicMods（已装进游戏的 mod）")
        act_game.triggered.connect(self.scan_game_mods)
        # 用户 2026-10-12：「手动放了 10 个 mod，其中 5 个与库里的相同，
        # 管理器能识别吗？」→ 就是这一项：按内容哈希把手装文件对到库内 mod。
        act_wild = menu.addAction("识别游戏里已有的 mod")
        act_wild.setToolTip(
            "按文件内容认出游戏 ~mods 里「手工放入、不受本工具管理」的 mod：\n"
            "与库中 mod 内容相同的会标成「已装（手装）」，认不出的会列出来"
        )
        act_wild.triggered.connect(lambda: self._recognize_external(force=True))
        act_folder = menu.addAction(version.SCAN_MENU_FOLDER_TEXT)
        act_folder.setToolTip("例如「下载」文件夹 —— 里面下的 mod 压缩包会被列出来")
        act_folder.triggered.connect(self.scan_dir)
        menu.addSeparator()
        act_import = menu.addAction("导入 mod…（Ctrl+O）")
        act_import.triggered.connect(self.pick_to_import)
        act_refresh = menu.addAction("刷新列表")
        act_refresh.triggered.connect(self.refresh)
        # exec_ 在 PySide6 里已改名 exec —— 用 getattr 兼容，两个名字都试。
        _exec = getattr(menu, "exec", None) or getattr(menu, "exec_")
        _exec(self.scroll.mapToGlobal(pos))

    # ------------------------------------------------------------ 工具条
    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("toolbar")
        # ⚠️⚠️ 选择器**必须**写成 `QFrame#toolbar {{ ... }}`，绝不能省成裸声明。
        # Qt 的层叠顺序是「控件自身的样式表 > 祖先控件样式表 > 应用级样式表」，
        # 而**裸声明会连所有子控件一起命中** —— 实测 2026-10-10：写成
        # `bar.setStyleSheet("background:#F5F6F8; border-bottom:...")` 后，
        # 工具条里每个按钮的背景都被刷成 #F5F6F8，可 `#primaryButton` 的
        # `color:#FFFFFF` 来自应用级样式表**仍然生效** →「安装所选」变成
        # **白字 + 近白底**，整颗按钮肉眼看不见。用户报的就是这条
        # （「页面没有安装按钮和卸载按钮」），而且截图里能看见、控件树里查不出来。
        bar.setStyleSheet(
            f"QFrame#toolbar {{ background:{theme.BG_WINDOW};"
            f" border-bottom:1px solid {theme.BORDER}; }}"
        )
        # ⚠️ 拆成**两行**是必要的，不是审美选择：筛选按钮 5 个 + 批量按钮 4 个 +
        # Mod Enabler 提示 + 搜索框 + 计数 + 冲突提示挤在一行时，
        # 实测 1000px 宽度下筛选按钮会被压成一条竖线（Qt 会把最后一个可压缩的
        # 控件压到极小）。分成两行后每行都宽松。
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(16, 8, 16, 8)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_buttons: Dict[str, QPushButton] = {}
        for key in catalog.FILTER_ORDER:
            btn = QPushButton(catalog.FILTER_LABELS[key])
            btn.setObjectName("filterButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setChecked(key == self._filter)
            btn.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            self._filter_group.addButton(btn)
            row.addWidget(btn)
            self._filter_buttons[key] = btn

        row.addStretch(1)

        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("搜索名称 / 作者 / 文件名…")
        self.edit_search.setClearButtonEnabled(True)
        self.edit_search.setFixedWidth(220)
        self.edit_search.textChanged.connect(lambda _: self.refresh())
        row.addWidget(self.edit_search)

        self.lbl_count = QLabel()
        self.lbl_count.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        row.addWidget(self.lbl_count)

        # ⚠️ 用户 2026-10-11：「冲突不要显示在右上角。」
        #   原来这里挂的是 `lbl_conflict`（「冲突 🔴 3 · 点这里看矩阵」）——
        #   常驻在工具条最右侧，跟右边的搜索框/计数挤在一起，而且它把"有冲突"
        #   这件事讲成了"有个红字要你点"，很吓人。
        #   现在改成：**卡片本身用组色边框表示冲突**（见 card.paintEvent），
        #   冲突明细挪进「冲突」筛选页顶部的面板（`_conflict_panel`）里。
        #   `lbl_conflict` 这个属性**故意不再创建** —— 免得以后有人又把它接回去。
        outer.addLayout(row)

        # ---- 冲突明细面板：只在「冲突」筛选页显示 ----
        # 用户 2026-10-11：「（矩阵）放到冲突这一页中。」
        self._conflict_panel = _ConflictPanel(self)
        outer.addWidget(self._conflict_panel)

        # ---- 第二行：批量操作 + 自动检测 + 前置提示 ----
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)

        self.btn_install_sel = QPushButton("安装所选")
        self.btn_install_sel.setObjectName("primaryButton")
        self.btn_install_sel.setCursor(Qt.PointingHandCursor)
        self.btn_install_sel.setToolTip("勾选卡片左上角的方框，然后点这里批量安装。")
        self.btn_install_sel.clicked.connect(self.install_selected)
        row2.addWidget(self.btn_install_sel)

        self.btn_uninstall_sel = QPushButton("卸载所选")
        self.btn_uninstall_sel.setCursor(Qt.PointingHandCursor)
        self.btn_uninstall_sel.setToolTip("勾选卡片左上角的方框，然后点这里批量卸载。")
        self.btn_uninstall_sel.clicked.connect(self.uninstall_selected)
        row2.addWidget(self.btn_uninstall_sel)

        self.btn_check_all = QPushButton("全选")
        self.btn_check_all.setCursor(Qt.PointingHandCursor)
        self.btn_check_all.clicked.connect(self._toggle_check_all)
        row2.addWidget(self.btn_check_all)

        # 用户 2026-10-11：「这个mod管理器有mod导入没有mod删除功能啊。」
        # → 删除其实**早就有了**（卡片右键「从 mod 库删除…」），但藏在右键里
        #   等于没有。补一个与「安装所选 / 卸载所选 / 全选」并列的显式按钮，
        #   走"勾选 + 批量"这条路（用户已经习惯了这三个按钮的位置）。
        # 危险动作所以用 warn 样式 + 二次确认，绝不静默删。
        self.btn_delete_sel = QPushButton("从库中删除所选")
        self.btn_delete_sel.setObjectName("dangerButton")
        self.btn_delete_sel.setCursor(Qt.PointingHandCursor)
        self.btn_delete_sel.setToolTip(
            "把勾选的 mod 从**本工具的库里**永久删除（含库里那份文件副本）。\n"
            "注意：这不等于从游戏目录卸载 —— 还装在游戏里的 mod 请先用「卸载所选」。"
        )
        self.btn_delete_sel.clicked.connect(self.delete_selected)
        # ⚠️ 2026-10-13（用户要求）：这个按钮原先紧跟在「全选」右边，容易误触
        #   （红描边太醒目，手一抖就是不可逆删除）。改为推到这一行**最右侧** ——
        #   addWidget 延后到 enabler 提示与 addStretch(1) 之后（见下方）。

        # ⚠️ 2026-10-11：「自动检测游戏目录」**已挪到顶部栏游戏目录那一行**
        # （用户要求「放到上方箭头处」），这里不再重复放。工具条这一行只留
        # 列表操作（安装/卸载/全选）与状态提示。

        # Mod Enabler 缺失提示。放在这一行是因为它必须**常驻可见**：
        # 没装 Enabler 时 ~mods 里的 mod 一个都不生效，而游戏不报错 ——
        # 用户会以为是工具坏了。
        # 前置缺失提示（2026-10-29 改成**按钮**）。
        # 之前是一行 QLabel 小字，用户反馈「没看到一键安装前置的按钮」——
        # 文字提示在工具条里太不显眼，而且没设游戏目录时整条会隐藏。
        # 现在做成和「安装所选」同级的实体按钮，前置没就绪时直接出现。
        self.btn_enabler = QPushButton("安装前置")
        self.btn_enabler.setObjectName("warnButton")
        self.btn_enabler.setCursor(Qt.PointingHandCursor)
        self.btn_enabler.setToolTip(
            "本游戏的 mod 需要签名绕过前置才生效（缺失时 mod 完全不加载、游戏也不报错）。\n"
            "点这里把随包的组件安装到游戏的 Win64 目录。"
        )
        self.btn_enabler.clicked.connect(self._show_enabler_detail)
        self.btn_enabler.setVisible(False)
        row2.addWidget(self.btn_enabler)

        # 兼容旧引用（老代码里叫 lbl_enabler，用它当纯文字提示的入口）
        self.lbl_enabler = self.btn_enabler

        row2.addStretch(1)
        # 「从库中删除所选」放在最右端（2026-10-13 用户要求，远离安装/卸载防误触）
        row2.addWidget(self.btn_delete_sel)
        outer.addLayout(row2)
        return bar

    # ------------------------------------------------------------ 中间区
    def _build_center(self) -> QWidget:
        self.stack = QStackedWidget()

        # 页 1：卡片网格
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.flow = FlowLayout(margin=16, h_spacing=theme.CARD_GAP, v_spacing=theme.CARD_GAP)
        self.cards_host = FlowWidget(self.flow)
        self.scroll.setWidget(self.cards_host)
        # ⚠️ 右键菜单挂在**滚动区**上而不是 `cards_host` —— 卡片区只在列表非空时
        #   才可见，用户在空状态页右键也该能用这些命令。
        self.scroll.setContextMenuPolicy(Qt.CustomContextMenu)
        self.scroll.customContextMenuRequested.connect(self._show_context_menu)
        self.stack.addWidget(self.scroll)

        # 页 2：空状态（需求文档 3.6「不允许出现空白」的精神同样适用于整页）
        self.stack.addWidget(self._build_empty_state())
        return self.stack

    def _build_empty_state(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setAlignment(Qt.AlignCenter)
        box.setSpacing(10)

        title = QLabel("还没有导入任何 mod")
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignCenter)
        box.addWidget(title)

        hint = QLabel(
            "把 <b>.zip / .7z / .rar</b> 压缩包或<b>含 mod 的文件夹</b>直接拖进这个窗口，"
            "或点右上角的「导入 mod…」。<br><br>"
            f"<span style='color:{theme.TEXT_TERTIARY}'>本工具不包含任何游戏资源与第三方 mod，"
            "mod 需要你自己下载提供。</span>"
        )
        hint.setObjectName("emptyHint")
        hint.setAlignment(Qt.AlignCenter)
        hint.setTextFormat(Qt.RichText)
        hint.setWordWrap(True)
        box.addWidget(hint)

        actions = QHBoxLayout()
        actions.addStretch(1)
        b1 = QPushButton("导入 mod…")
        b1.setObjectName("primaryButton")
        b1.setCursor(Qt.PointingHandCursor)
        b1.clicked.connect(self.pick_to_import)
        actions.addWidget(b1)
        b2 = self._build_scan_menu_button()
        actions.addWidget(b2)
        actions.addStretch(1)
        box.addLayout(actions)
        return page

    def _build_shortcuts(self) -> None:
        act_open = QAction("导入 mod", self)
        act_open.setShortcut(QKeySequence("Ctrl+O"))
        act_open.triggered.connect(self.pick_to_import)
        self.addAction(act_open)

        act_refresh = QAction("刷新", self)
        act_refresh.setShortcut(QKeySequence("F5"))
        act_refresh.triggered.connect(self.refresh)
        self.addAction(act_refresh)

    # ================================================================ 刷新
    def current_probe(self) -> Optional[gamedir.ProbeResult]:
        """当前游戏目录的探测结果（带缓存）。

        `settings.active_entry` 里的字段是我们自己上次存下来的，理论上够用；
        但冲突检测还要按平台算目标文件名，用**重新探测**的结果最可靠 ——
        用户可能把游戏挪走了、或者手动改了 ~mods 的形态。
        """
        entry = self.settings.active_entry
        if entry is None:
            self._probe = None
            self._probe_key = ""
            return None
        key = str(entry.path)
        if self._probe is None or self._probe_key != key:
            self._probe = gamedir.validate_game_dir(entry.path)
            self._probe_key = key
        return self._probe

    def _detect_conflicts(self) -> dict:
        """跑一遍冲突检测，返回 catalog 要的映射。没设游戏目录就返回空。"""
        probe = self.current_probe()
        if probe is None or not probe.ok:
            self._conflict_report = None
            return {}
        report, mapping = conflicts.detect_for_ui(self.store, self.library, probe)
        self._conflict_report = report
        return mapping

    def refresh(self) -> None:
        """重建卡片。数量多时会整块重建 —— 库最多几百条，代价可接受。"""
        game_dir = self.settings.active_game_dir
        mapping = self._detect_conflicts()
        self._conflict_map = mapping
        # 冲突**组**配色（用户 2026-10-11：「多个 mod 冲突时颜色变成同一个」）。
        # 按连通分量分组：互相牵连的一批 mod 共用一个描边色。
        self._conflict_colors = conflicts.group_colors(self._conflict_report) \
            if self._conflict_report is not None else {}
        # 手装命中（用户 2026-10-12）：从缓存读，不重扫（理由见 __init__ 注释）。
        # 但要先校验文件还在（用户 2026-10-13）：扫完缓存后用户可能手动
        # 删了文件，不校验就会把已经不存在的 mod 显示成「已装·手装」。
        self._external_hits = wildscan.load_cache_hits_validated()
        self._views = catalog.build_views(
            self.store,
            self.library,
            game_dir=game_dir,
            filter_status=self._filter,
            query=self.edit_search.text() if hasattr(self, "edit_search") else "",
            conflicts=mapping,
            conflict_colors=self._conflict_colors,
            external=self._external_hits,
        )
        self._rebuild_cards()
        self._update_counts()
        self._update_game_dir_display()
        self._update_enabler_hint()

        if not self.store.all():
            self.stack.setCurrentIndex(1)
        else:
            self.stack.setCurrentIndex(0)

    def _rebuild_cards(self) -> None:
        self.flow.clear()
        self._cards.clear()
        for view in self._views:
            # ⚠️ 必须把 parent 传进来：不给 parent 时卡片在 addWidget() 之前
            # 是**独立顶层窗口**（Qt 会先建原生窗口，量到 236×311 的白框）。
            # 2026-10-19 黑框事故的另一半原因就在这里。
            card = ModCard(view, self.cards_host)
            card.clicked.connect(self._on_card_clicked)
            card.activated.connect(self.rename_mod)
            card.install_requested.connect(self.install_mod)
            card.uninstall_requested.connect(self.uninstall_mod)
            card.detail_requested.connect(self.show_mod_detail)
            card.delete_requested.connect(self.delete_mod)
            card.reveal_requested.connect(self.reveal_mod)
            card.preview_change_requested.connect(self.change_preview)
            card.slot_change_requested.connect(self.set_mod_kind)
            card.checked_changed.connect(self._on_card_checked)
            # 重建时恢复勾选状态 —— 否则筛选/搜索一次，勾选就全丢了
            card.set_checked(view.id in self._checked_ids)
            self.flow.addWidget(card)
            self._cards[view.id] = card
        # 让容器按当前宽度重算高度 —— 否则卡片多了会被裁掉且没有滚动条
        self.cards_host.relayout()
        self._update_selection_buttons()

    # ------------------------------------------------------------ 批量选择
    def _on_card_checked(self, view: catalog.ModView, checked: bool) -> None:
        if checked:
            self._checked_ids.add(view.id)
        else:
            self._checked_ids.discard(view.id)
        self._update_selection_buttons()

    def _checked_views(self) -> List[catalog.ModView]:
        return [v for v in self._views if v.id in self._checked_ids]

    def _update_selection_buttons(self) -> None:
        """按钮上的数字要实时反映「选了 3 个（其中 2 个没装）」，否则用户不知道点了会发生什么。"""
        # 构建顺序上 _rebuild_cards 可能早于 _build_toolbar 跑一次，这里要能容忍
        if not hasattr(self, "btn_install_sel"):
            return
        n = len(self._checked_ids)
        to_install = sum(1 for v in self._checked_views() if not v.installed)
        to_uninstall = sum(1 for v in self._checked_views() if v.installed)
        self.btn_install_sel.setText(f"安装所选（{to_install}）" if n else "安装所选")
        self.btn_uninstall_sel.setText(f"卸载所选（{to_uninstall}）" if n else "卸载所选")
        # ⚠️ 这两个按钮**刻意不置灰**（2026-10-10 用户反馈后改的）。
        # 原来按 to_install>0 走 setEnabled()，结果：库里两条记录都是"未装"、
        # 用户又没勾选时，「安装所选」是禁用态 —— 再叠上当时那条
        # "淡粉底 + 白字"的禁用样式，按钮等于隐形，用户直接说
        # 「页面没有安装按钮和卸载按钮」。**可点性比状态表达重要**：
        # 永远能点，点了没勾选就给一句人话提示（见 install_selected）。
        self.btn_check_all.setText("全不选" if n and n == len(self._views) else "全选")

    def _toggle_check_all(self) -> None:
        views = self._views
        if not views:
            return
        everyone = all(v.id in self._checked_ids for v in views)
        for v in views:
            if everyone:
                self._checked_ids.discard(v.id)
            else:
                self._checked_ids.add(v.id)
            card = self._cards.get(v.id)
            if card is not None:
                card.set_checked(not everyone)
        self._update_selection_buttons()

    def clear_checked(self) -> None:
        self._checked_ids.clear()
        for card in self._cards.values():
            card.set_checked(False)
        self._update_selection_buttons()

    # ------------------------------------------------------------ 批量安装 / 卸载
    def _nothing_selected_hint(self, action: str, *, has_mods: bool) -> None:
        """点了「安装所选 / 卸载所选」但没勾选任何东西时的提示。

        这两个按钮是**常驻可点**的（不置灰，见 `_update_selection_buttons`），
        所以"什么都没选"是一种正常状态，必须给一句能照着做的说明 ——
        否则用户只会觉得"按钮是坏的"。
        """
        if not has_mods:
            QMessageBox.information(
                self,
                "mod 库还是空的",
                "库里还没有 mod。\n\n"
                "先点右上角的「导入 mod…」选压缩包，或把 mod 文件夹直接拖进窗口。",
            )
            self._set_status("库里还没有 mod：先「导入 mod…」或把文件夹拖进来。")
            return
        QMessageBox.information(
            self,
            f"还没勾选要{action}的 mod",
            f"{action}是按「勾选」来的，一次可以处理多个：\n\n"
            "① 在卡片**左上角的方框**里打勾（也可以点工具条的「全选」）\n"
            f"② 再点「{action}所选」\n\n"
            f"按钮上的数字就是当前会{action}几个。",
        )
        self._set_status(f"还没勾选 mod：先在卡片左上角打勾，再点「{action}所选」。")

    def install_selected(self) -> None:
        """批量安装：**统一确认一次 → 静默逐个装 → 汇总结果**。

        用户 2026-10-11 原话：「多选安装时，不是一次性安装多个mod，
        需要每次mod都要二次确认一下。你这个二次确认有什么意义？」
        → 确认只在开头做一次（把整批要装的东西摊开给你看），
          之后逐个调 `_install_one(confirm=False)` 不再弹框，
          最后弹一次"成功几个 / 失败几个"。中途失败不影响已装好的。
        """
        targets = [v for v in self._checked_views() if not v.installed]
        if not targets:
            self._nothing_selected_hint("安装", has_mods=bool(self._views))
            return

        # 先把整批计划摊开 —— 有阻断项 / 冲突的要在这一步就说清楚
        probe = self.current_probe()
        detail: list[str] = []
        blocked: list[str] = []
        total_files = 0
        overwrite_names: list[str] = []      # 会被覆盖的目标文件名（真的有才记）
        for view in targets:
            meta = self.store.get(view.id)
            if meta is None:
                blocked.append(f"· {view.name}（库里找不到记录）")
                continue
            plan = installer.plan_install(meta, probe, self.store, self.library) if (probe and probe.ok) else None
            if plan is None or not plan.ok:
                reason = "、".join(plan.blockers) if plan is not None else "还没有设置游戏目录"
                blocked.append(f"· {view.name}（{reason}）")
                continue
            total_files += len(plan.files)
            hits = [f.dst.name for f in plan.files if f.is_overwrite]
            overwrite_names.extend(hits)
            flags = []
            if plan.warnings:
                flags.append(f"{len(plan.warnings)} 条提示")
            if hits:
                flags.append(f"覆盖 {len(hits)} 个已存在的同名文件（会先备份）")
            suffix = f"（{'，'.join(flags)}）" if flags else ""
            detail.append(f"· {view.name}：新增 {len(plan.files)} 个文件{suffix}")

        lines = [
            f"将把下列 {len(detail)} 个 mod 装进游戏目录：",
            "",
            *detail,
            "",
            f"合计 {total_files} 个文件。",
        ]
        # ⚠️ 「已存在」这段解释**只在真的有文件会被覆盖时**才出现。
        # 用户 2026-10-11 截图报过：「我的游戏目录中没看到已经安装过这个 mod」——
        # 原因是这段文字原来是无条件拼上去的，哪怕是全新安装也照弹，
        # 用户只能理解成"它在说我装过了"。现在没有覆盖就一个字都不提。
        if overwrite_names:
            shown = "、".join(overwrite_names[:6])
            more = f" 等 {len(overwrite_names)} 个" if len(overwrite_names) > 6 else ""
            lines += [
                "",
                f"其中 {len(overwrite_names)} 个文件在游戏目录里已经有同名的{shown}{more}，"
                "安装时会覆盖掉。",
                "覆盖之前本工具会先把原文件复制一份到备份文件夹，万一新版本有问题可以还原。",
            ]
        if blocked:
            lines += ["", "以下这几个装不了：", *blocked]

        answer = QMessageBox.question(
            self,
            f"安装 {len(detail)} 个 mod",
            "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        ok_names: list[str] = []
        fail: list[str] = []
        for view in targets:
            if self._install_one(view, confirm=False):
                ok_names.append(view.name)
            else:
                fail.append(view.name)

        self.refresh()
        summary = f"批量安装完成：成功 {len(ok_names)} 个"
        if fail:
            summary += f"，失败 {len(fail)} 个（{('、'.join(fail[:3]))}{'…' if len(fail) > 3 else ''}）"
        self.statusBar().showMessage(summary, 10000)
        QMessageBox.information(self, "批量安装结果", summary)
        # 装完重新识别一次「手装」—— 刚装上的 mod 有了安装记录，
        # 卡片上的「手装」徽标该撤掉（用户 2026-10-12）。
        self._recognize_external(announce=False)

    def uninstall_selected(self) -> None:
        targets = [v for v in self._checked_views() if v.installed]
        if not targets:
            self._nothing_selected_hint("卸载", has_mods=bool(self._views))
            return
        names = "\n".join(f"· {v.name}" for v in targets[:12])
        more = f"\n… 另有 {len(targets) - 12} 个" if len(targets) > 12 else ""

        # 卸载前先预告：这批里哪些 mod 会把「被覆盖的原文件」还原回原位。
        # dry_run 是纯读取（只读 library.json 记录 + 备份清单），对每个 target 跑一遍
        # 没有副作用。用户 2026-10-12：批量卸 82 个时，中间发生的还原必须看得见。
        restore_lines: List[str] = []
        probe = self.current_probe()
        game_dir = probe.game_root if (probe and probe.ok) else self.settings.active_game_dir
        # 同源查询（还原文件与库中哪个 mod 内容相同）建一次就够，别每个 mod 建一遍
        lookup = self._same_as_lookup()
        if game_dir:
            for view in targets:
                pv = installer.uninstall(
                    self.library, view.id, str(game_dir), dry_run=True, same_as_lookup=lookup
                )
                if pv.ok:
                    for p in pv.restored:
                        note = pv.restored_notes.get(str(p), "")
                        restore_lines.append(f"{view.name} → {p}" + (f"（{note}）" if note else ""))

        text = (
            f"将从游戏目录里移除下列 mod 自己装的文件：\n\n{names}{more}\n\n"
            "只会删安装记录里记着的那几个文件，不会碰别的东西。是否继续？"
        )
        if restore_lines:
            shown = "\n".join(f"· {line}" for line in restore_lines[:8])
            extra = f"\n· … 另有 {len(restore_lines) - 8} 个" if len(restore_lines) > 8 else ""
            text += (
                f"\n\n⚠ 其中 {len(restore_lines)} 个被覆盖的原文件会被还原回原位：\n"
                f"{shown}{extra}\n"
                "（在装对应 mod 之前就在游戏目录里；没手动放过就是更早的安装留下的）"
            )
        if QMessageBox.question(
            self,
            f"卸载 {len(targets)} 个 mod",
            text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        ok_names: List[str] = []
        fail_names: List[str] = []
        restored_all: List[Tuple[Path, str]] = []
        # 卸载进度框（2026-10-18 加）：批量卸载要逐个做「落点体检」+
        # 删文件 + 还原备份，几十个 mod 时界面会几十秒无反馈，
        # 用户以为卡死。给一个可取消的模态进度框。
        progress = QProgressDialog(
            "正在卸载…", "取消", 0, len(targets), self
        )
        progress.setWindowTitle("正在卸载 mod")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(400)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        try:
            for idx, view in enumerate(targets):
                if progress.wasCanceled():
                    break
                progress.setValue(idx)
                progress.setLabelText(f"正在卸载「{view.name}」…（{idx}/{len(targets)}）")
                result = self._uninstall_one(view, confirm=False)
                if result is not None and result.ok:
                    ok_names.append(view.name)
                    for p in result.restored:
                        restored_all.append((p, result.restored_notes.get(str(p), "")))
                else:
                    fail_names.append(view.name)
            progress.setValue(len(targets))
        finally:
            progress.close()
        self.refresh()
        canceled = progress.wasCanceled()

        # 结果必须**弹出来**（用户 2026-10-19：「卸载成功后没有卸载成功的弹窗」）。
        # 之前只写状态栏，10 秒后自动消失，等于没有反馈；批量安装那边一直
        # 有「批量安装结果」弹窗，卸载这边漏了 —— 现在对齐。
        if len(targets) == 1:
            summary = (
                f"已卸载「{ok_names[0]}」" if ok_names else f"「{fail_names[0]}」没卸成"
            )
        else:
            summary = f"批量卸载完成：成功 {len(ok_names)} 个"
            if fail_names:
                shown = "、".join(fail_names[:3]) + ("…" if len(fail_names) > 3 else "")
                summary += f"，{len(fail_names)} 个没卸成（{shown}）"
        if canceled:
            summary += "（已取消，后面的没有卸）"
        if restored_all:
            summary += f"\n另有 {len(restored_all)} 个被覆盖的原文件已还原回游戏目录。"
        self.statusBar().showMessage(summary.replace("\n", " "), 10000)
        QMessageBox.information(self, "卸载结果", summary)
        if restored_all:
            self._notify_restored(restored_all)
        # 卸完重新识别一次「手装」—— 被还原回游戏目录的原文件
        # 如果与库中某个 mod 内容相同，卡片会变成「已装（手装）」，
        # 用户能直接看出"游戏里还有一份这个"（用户 2026-10-12 的场景）。
        self._recognize_external(announce=False)

    def _notify_restored(self, items: List[Tuple[Path, str]]) -> None:
        """卸载后把「被还原回游戏目录的原文件」点名告诉用户。

        用户 2026-10-12：「复原的时候加个提示吧，告知用户有手动安装的 mod 被复原。」
        还原行为本身是安全网（保护手放的文件不被 mod 永久覆盖掉，属硬红线，
        不能砍），问题只在它以前**悄悄发生** —— 卸完只在状态栏显示一行字，
        用户看到游戏目录里冒出文件还以为软件自己装了东西。

        `items` 是 `(还原路径, 同源说明)`；同源说明非空时一并显示 ——
        内容与库中某个 mod 一致，用户就能知道"它和库里的 X 是同一份东西"。
        """
        lines = [
            f"本次卸载把 {len(items)} 个被覆盖的原文件还原回了游戏目录：",
            "",
        ]
        for p, note in items[:12]:
            lines.append(f"  {p}" + (f"（{note}）" if note else ""))
        if len(items) > 12:
            lines.append(f"  … 另有 {len(items) - 12} 个")
        lines += [
            "",
            "这些文件在装对应 mod 之前就在游戏目录里。",
            "如果你没手动放过它们，可能是更早的安装留下的；不想留可以手动删掉。",
        ]
        QMessageBox.information(self, "有原文件被还原", "\n".join(lines))

    def delete_selected(self) -> None:
        """批量「从 mod 库删除」（对应工具条上的「从库中删除所选」）。

        用户 2026-10-11：「这个mod管理器有mod导入没有mod删除功能啊。」
        —— 删除逻辑本来就有（`delete_mod`，卡片右键可达），但藏在右键菜单里
        等于没有，所以补了这个显式按钮。

        与「卸载所选」的区别（**必须在弹窗里说清，否则用户会误以为只是卸载**）：
          · 卸载 = 从**游戏目录**删掉这个 mod 装出去的文件；
          · 删除 = 从**本工具的库里**永久删掉（含库里那份文件副本），游戏目录不动。
        因此这里先把两类目标分开统计并各自提示；还装在游戏里的那些额外提醒
        建议先卸载，否则游戏目录里会留下"无人认领"的文件。
        """
        targets = self._checked_views()
        if not targets:
            self._nothing_selected_hint("从库中删除", has_mods=bool(self._views))
            return

        probe = self.current_probe()
        game_dir = probe.game_root if (probe and probe.ok) else self.settings.active_game_dir
        still_installed = [
            v for v in targets if game_dir and self.library.is_installed(v.id, str(game_dir))
        ]

        total_bytes = 0
        plan_lines: List[str] = []
        for view in targets:
            try:
                files = self.store.delete(view.id, dry_run=True)
            except Exception:                                  # noqa: BLE001
                files = []
            total_bytes += sum(_safe_size(p) for p in files)
            plan_lines.append(f"· {view.name}（{len(files)} 个文件）")

        if not self._confirm(
            "确认从库中删除",
            f"要从 mod 库里永久删除 {len(targets)} 个 mod 吗？",
            "\n".join(
                [
                    "将从**本工具的库里**删除（库里那份文件副本也一起删掉）：",
                    "",
                    *plan_lines,
                    "",
                    f"合计约 {_human_size(total_bytes)}。",
                    "",
                    "⚠ 这**不是**卸载：默认只删库，游戏目录里已装的文件不会被碰。",
                    "（接下来若提示「还装在游戏里」，点粉色大按钮「否，只删库」即可；",
                    "  另一颗「是，先卸载再删库」才会把游戏目录里的文件也删掉。）",
                ]
            ),
            yes_text="确认删除",
            warn=True,
        ):
            return

        if still_installed:
            names = "\n".join(f"· {v.name}" for v in still_installed[:10])
            more = (
                f"\n… 另有 {len(still_installed) - 10} 个" if len(still_installed) > 10 else ""
            )
            # ⚠️ 用户 2026-10-18：「没有否。一般情况下，大部分人只是删除库，
            # 我建议把否这个按钮做大点，做醒目点，跟安装所选同样的粉色」。
            # 所以这里**不能**用 QMessageBox 的 question 静态方法 —— 它被
            # `app._silence_message_boxes()` 换成了两颗按钮（是/取消），
            # 传 Yes|No + 默认 No 会被整段丢掉，用户界面上根本没有「否」。
            # 改用三按钮自绘对话框：主按钮就是「否，只删库」（粉色实心、
            # 与工具条「安装所选」同款 #primaryButton），默认焦点也落在它上面。
            dlg = dialogs.SilentDialog(
                self,
                title="这些 mod 还装在游戏里",
                heading="只想清理库、不动游戏里的文件 → 点左边的大按钮「否，只删库」",
                body=(
                    f"下列 mod 还装在游戏目录里：\n\n{names}{more}\n\n"
                    "· 「否，只删库」= 游戏目录里已装的文件原样留着\n"
                    "  （代价：它们会变成「无人认领」—— 本工具以后认不出、也没法一键卸载）\n"
                    "· 「是，先卸载再删库」= 游戏目录里这些 mod 的文件也会被删掉\n"
                ),
                no_text="否，只删库",
                yes_text="是，先卸载再删库",
                cancel_text="取消",
                default="no",
                # 只有「否，只删库」是粉色实心主按钮（与「安装所选」同款）；
                # 「是，先卸载再删库」是危险动作，保持普通描边样式，不抢视觉权重
                yes_primary=False,
                no_primary=True,
            )
            dlg.exec()
            if dlg.choice == "yes":
                restored_first: List[Tuple[Path, str]] = []
                for view in still_installed:
                    res = self._uninstall_one(view, confirm=False)
                    if res is not None and res.ok:
                        for p in res.restored:
                            restored_first.append((p, res.restored_notes.get(str(p), "")))
                if restored_first:
                    # 先卸载再删库：卸载这一步同样可能还原被覆盖的原文件
                    self._notify_restored(restored_first)
                # 同上：卸载会改变游戏目录内容，重新识别一次「手装」
                self._recognize_external(announce=False)
            # choice == "no" / "cancel" → 什么都不做，继续往下只删库

        removed_files = 0
        ok_count = 0
        for view in targets:
            try:
                removed = self.store.delete(view.id)
            except Exception as exc:                            # noqa: BLE001
                QMessageBox.warning(self, "删除失败", f"「{view.name}」删不掉：\n{exc}")
                continue
            removed_files += len(removed)
            ok_count += 1

        self.refresh()
        del_summary = f"已从库中删除 {ok_count} 个 mod（共 {removed_files} 个文件）。"
        self._set_status(del_summary)
        # 与安装/卸载对齐：结果要弹出来，不能只写状态栏（用户 2026-10-19）
        QMessageBox.information(self, "删除结果", del_summary)

    # ------------------------------------------------------------ 游戏目录
    def autodetect_game_dir(self) -> None:
        """自动搜索游戏安装位置 —— **点一下就出结果，不要求用户再选文件夹**。

        用户 2026-10-10 的原话：「为什么自动扫描还要自己选文件夹？游戏无非就是
        steam、epic 或者 wegame，你自己不会找吗？」→ 所以这里：

          · 只找到**一个**候选 → 直接应用，只写状态栏，**不弹任何框**；
          · 找到多个 → 列出来让用户挑一个（这是"确实有歧义"才问）；
          · 一个都没有 → 才退到「手动指定」（实在没别的办法了）。

        搜索来源见 `gamedir.autodetect_game()`：Steam 库清单 / Epic 清单 /
        Windows 卸载登记表（含 32 位与当前用户）/ Xbox·Game Pass 目录 /
        各固定磁盘的常见游戏库与盘根第一层。
        """
        self._set_status("正在自动搜索游戏安装位置…（Steam / Epic / WeGame / Xbox / 注册表）")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            hits = gamedir.autodetect_game()
        finally:
            QApplication.restoreOverrideCursor()

        # 去重（同一个目录可能被多个来源各命中一次）
        seen: set[str] = set()
        unique = []
        for hit in hits:
            key = str(hit.path).lower().rstrip("\\/")
            if key not in seen:
                seen.add(key)
                unique.append(hit)

        if not unique:
            self._offer_manual_game_dir()
            return

        if len(unique) == 1:
            hit = unique[0]
            how = hit.label or hit.source
            if gamedir.is_game_root(hit.path):
                self._apply_game_dir(Path(hit.path), quiet=True)
                self._set_status(f"已自动找到并设置游戏目录：{hit.path}（来源：{how}）")
            else:
                # 名字像、但结构不对 —— 交给校验流程去问用户
                self._set_status(f"疑似游戏目录：{hit.path}（来源：{how}）")
                self._apply_game_dir(Path(hit.path))
            return

        items = [f"{h.path}　—　{h.label or h.source}" for h in unique]
        choice, ok = QInputDialog.getItem(
            self, "找到多个游戏目录", "检测到多个安装位置，请选一个：", items, 0, False
        )
        if ok and choice:
            self._apply_game_dir(Path(unique[items.index(choice)].path), quiet=True)

    def _offer_manual_game_dir(self) -> None:
        """一个都没自动找到时的兜底 —— 明确告诉用户「已经找过哪些地方」。"""
        box = QMessageBox(self)
        box.setWindowTitle("没自动找到游戏")
        box.setIcon(QMessageBox.Information)
        box.setText(f"没在常见的游戏平台里自动找到《{GAME.display_name}》。")
        box.setInformativeText(
            "已经自动找过这些地方：\n"
            "· Steam 库（appmanifest 清单，含所有库文件夹）\n"
            "· Epic 清单（ProgramData\\Epic\\…\\Manifests）\n"
            "· WeGame / 其他安装包（Windows 卸载登记表）\n"
            "· Xbox / Game Pass（各盘的 XboxGames 目录）\n"
            "· 各磁盘的常见游戏库目录\n\n"
            "如果游戏装在别处，可以手动指一次 —— 选到「含 Project_Plague 的那一层」目录。"
        )
        manual = box.addButton("手动指定…", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is manual:
            self.choose_game_dir()


    def _update_counts(self) -> None:
        counts = catalog.filter_counts(
            self.store,
            self.library,
            self.settings.active_game_dir,
            conflicts=getattr(self, "_conflict_map", None),
            external=getattr(self, "_external_hits", None),
        )
        for key, btn in self._filter_buttons.items():
            btn.setText(f"{catalog.FILTER_LABELS[key]} {counts.get(key, 0)}")
        total = counts.get(catalog.FILTER_ALL, 0)
        shown = len(self._views)
        if shown == total:
            self.lbl_count.setText(f"共 {total} 个 mod")
        else:
            self.lbl_count.setText(f"显示 {shown} / {total} 个 mod")

        # 冲突明细面板（用户 2026-10-11：「放到冲突这一页中」）：
        # 只在「冲突」筛选页可见，且有冲突条目才显示。
        panel = getattr(self, "_conflict_panel", None)
        if panel is not None:
            report = self._conflict_report
            panel.update_content(
                report,
                visible=self._filter == catalog.FILTER_CONFLICT
                and report is not None
                and bool(report.items),
            )

    def _update_game_dir_display(self) -> None:
        entry = self.settings.active_entry
        if entry is None:
            self.lbl_game_dir.setProperty("missing", "true")
            self._game_dir_full = "尚未设置 —— 点「更改目录」或「自动检测游戏目录」"
            self._game_dir_short = "尚未设置"
            self.lbl_game_dir.setToolTip(
                "还没设置游戏目录。\n"
                "· 「更改目录」= 手动选一次游戏根目录（含 Project_Plague 的那一层）\n"
                "· 「自动检测游戏目录」= 让工具自己去 Steam / Epic / WeGame / Xbox / 注册表 里找"
            )
        else:
            self.lbl_game_dir.setProperty("missing", "false")
            self._game_dir_full = str(entry.path)
            # 平时**缩短显示**（用户 2026-10-11：「路径栏缩短，且可以点击」）——
            # 完整值在点击弹窗与 tooltip 里。这里先塞完整路径，真正的缩短由
            # `_refresh_game_dir_text` 按可用宽度做中间省略。
            self._game_dir_short = str(entry.path)
            self.lbl_game_dir.setToolTip(
                f"点一下看完整路径\n\n{entry.path}\n\n"
                f"pak 目录：{entry.paks_dir}\n安装目标：{entry.mods_dir}\n"
                f"命中规则：{entry.matched_rule}"
            )
        # 改了动态属性要重刷样式（也顺带把 QSS 里的字体解析出来，量文字宽才准）
        self.lbl_game_dir.style().unpolish(self.lbl_game_dir)
        self.lbl_game_dir.style().polish(self.lbl_game_dir)
        self._refresh_game_dir_text()

    def _show_game_dir_popup(self, _event=None) -> None:
        """点游戏目录标签 → 用**无声对话框**显示完整路径。

        用户 2026-10-11：「路径栏缩短，且可以点击，点击后弹出完整路径，
        **但是不要出现提示音**。」
        ⚠️ 两个坑，别再踩：
          1) 别用 `QMessageBox` —— 它的图标在 Windows 上会响系统提示音；
          2) 别把标签设成 `TextSelectableByMouse` 来"让它能点" ——
             Qt 对可选中文本的按下反馈**同样会响一声**（用户上一轮报的就是这个）。
             正确做法是保持 `NoTextInteraction` + 自己接 `mousePressEvent`。
        """
        full = getattr(self, "_game_dir_full", "")
        if not full or self.settings.active_entry is None:
            return
        entry = self.settings.active_entry
        body = (
            f"完整路径：\n{full}\n\n"
            f"pak 目录：\n{entry.paks_dir}\n\n"
            f"mod 安装目标：\n{entry.mods_dir}\n\n"
            f"识别方式：{entry.matched_rule}"
        )
        dialogs.show_text(self, "游戏目录", body, heading="当前设置的游戏目录")

    #: topBar 左右外边距之和（`_build_top_bar` 里 contentsMargins(16, 10, 16, 10)）。
    _TOP_BAR_MARGIN_W = 32
    #: `#gameDirPath` 的 QSS 左右内边距之和（qss.py 里 `padding: 3px 8px`）。
    _GAME_DIR_PAD_W = 16

    def _row3_fixed_width(self) -> int:
        """量出第 2 行**除路径标签之外**的固定开销（px）。

        做法：把这一行所有项（含弹性项 / 间隔项）的 sizeHint 求和，再减掉路径标签
        自己那一份 —— 差额与标签文字无关，所以是个稳定值，可以直接当"固定开销"用。
        **不要硬编常量**：字体、DPI、文案一变，硬编的数就不准，行会溢出或留大缝。
        """
        row = getattr(self, "_row3", None)
        if row is None:
            return 0
        total = 0
        label_cost = 0
        for i in range(row.count()):
            item = row.itemAt(i)
            w = item.sizeHint().width()
            total += w
            if item.widget() is self.lbl_game_dir:
                label_cost = w
        # 去掉标签那一个"项"就少一个项间距
        return total - label_cost + row.spacing() * (row.count() - 2)

    def _refresh_game_dir_text(self) -> None:
        """按可用宽度重算游戏目录标签的显示文本与标签宽度。

        用户 2026-10-11 最终定稿：「游戏路径显示太长了，不需要显示这么多，
        只需要点击路径时，能显示全部路径即可，平常不需要这么长。」
        → 平时显示 `_game_dir_short`（末级目录名），完整路径在 tooltip + 点击弹窗里。

        这里仍然保留"按可用宽度再兜底省略一次"，是为了**极窄窗口**下不把
        标签文字硬切（那才是真的"显示不全"）。宽度依旧由代码算出来 setFixedWidth，
        不交给布局弹性分配 —— 交给布局就会被拉满整行（用户说的"太长"），
        或在有 stretch 时被压成 0（Ignored 策略的老坑）。
        """
        full = getattr(self, "_game_dir_full", "")
        if not full or getattr(self, "_row3", None) is None:
            return
        avail = self.width() - self._TOP_BAR_MARGIN_W - self._row3_fixed_width() - self._GAME_DIR_PAD_W
        avail = max(0, min(avail, version.GAME_DIR_LABEL_MAX_WIDTH - self._GAME_DIR_PAD_W))
        # 安全余量：QFontMetrics 量出的宽度与 QLabel 内部排版偶尔差几像素，
        # 不留余量的话极窄窗口下末尾会被硬切（正是"文字显示不全"那种观感）。
        limit = max(0, avail - 6)
        fm = QFontMetrics(self.lbl_game_dir.font())
        text = fm.elidedText(getattr(self, "_game_dir_short", "") or full, Qt.ElideMiddle, limit)
        # ⚠️ 宽度用**同一套 QFontMetrics** 反推，不要去读 `label.sizeHint()` ——
        # 实测它在 setText 之后会返回**上一次**的旧值（探针里表现为"需要宽度"总慢一拍），
        # 用它算出来的宽度会层层错位。
        need = fm.horizontalAdvance(text) + self._GAME_DIR_PAD_W
        self.lbl_game_dir.setText(text)
        self.lbl_game_dir.setFixedWidth(min(need, avail + self._GAME_DIR_PAD_W))

    def set_filter(self, key: str) -> None:
        self._filter = key
        self.refresh()

    # ================================================================ 交互
    def _set_status(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def _on_card_clicked(self, view: catalog.ModView) -> None:
        for mod_id, card in self._cards.items():
            card.set_selected(mod_id == view.id)
        self._set_status(
            f"{view.name} · {view.status_label} · 文件 {len(view.meta.files)} 个"
            + (f" · 名称来自：{view.meta.name_source}" if view.meta.name_source else "")
        )

    # ================================================================ 安装 / 卸载
    def _update_enabler_hint(self) -> None:
        """前置状态**常驻显示**（2026-10-29 用户反馈后改）。

        以前：没装才显示一行小字，装了就整条隐藏 —— 用户看不到就以为功能不存在
        （原话：「还是没看到安装前置啊」）。而这个状态决定所有 mod 到底会不会
        生效，藏起来等于让用户猜。

        现在：做成常驻按钮，两种状态都显示，只换文案与配色：
          · 未装 → 「安装前置」，橙色醒目，点了能一键装；
          · 已装 → 「前置已就位」，绿色淡底，点了能看详情/重装。
        """
        probe = self.current_probe()
        if not hasattr(self, "btn_enabler"):
            return
        if probe is None or not probe.ok:
            # 连游戏目录都没设，前置无从谈起
            self.btn_enabler.setVisible(False)
            return

        self._enabler_status = enabler.detect(probe.game_root)
        if self._enabler_status.installed:
            self.btn_enabler.setText("前置已就位")
            self.btn_enabler.setStyleSheet(
                "QPushButton { color:#2E7D32; border:1px solid #A5D6A7;"
                " background:#F1F8F1; padding:0 12px; }"
                "QPushButton:hover { background:#E6F4E6; }"
            )
        else:
            self.btn_enabler.setText("安装前置")
            self.btn_enabler.setStyleSheet(
                "QPushButton { color:#A65E00; border:1px solid #E0A030;"
                " background:#FFF8EC; padding:0 12px; font-weight:600; }"
                "QPushButton:hover { background:#FFF1D6; }"
            )
        self.btn_enabler.setToolTip(self._enabler_status.message)
        self.btn_enabler.setVisible(True)

    def _show_enabler_detail(self) -> None:
        status = getattr(self, "_enabler_status", None)
        detail = status.message if status else "没有检测结果，请先设置游戏目录。"
        installed = bool(status and status.installed)

        bundled = enabler.bundled_files()
        if bundled:
            # 本版随包带了前置组件（作者 2026-10-29 决定）：直接给一键安装。
            # 合规前提：组件署名与许可必须显示出来、本软件整体免费。
            names = "、".join(p.name for p in bundled)
            body = (
                f"{detail}\n\n"
                f"本工具随包附带前置组件（{names}），可以复制到：\n"
                f"{status.bin_dir if status else '<游戏根>/ProjectPBZ/Binaries/Win64'}\n\n"
                "组件来源与许可：\n"
                "  · UniversalSigBypasser.asi — 作者 rm-NoobInCoding，"
                "CC BY-NC 4.0（非商业、保留署名）\n"
                "    https://github.com/rm-NoobInCoding/UniversalSigBypasser\n"
                "  · dsound.dll — ThirteenAG 的 Ultimate ASI Loader，MIT 许可\n\n"
                "只会复制这两个文件，不改动游戏本体的任何东西；\n"
                "如果目标目录里已有同名文件，会先备份再覆盖。"
            )
            if installed:
                # 已经装好了：点它只是查看状态，附带一个「重新装一遍」的选项，
                # 不要一上来就问「要装吗」—— 那会让用户以为没装成功。
                body = "前置已经就位，无需再装。\n\n" + body + (
                    "\n\n如果游戏更新后失效了，可以重新装一遍。"
                )
                if dialogs.ask(self, "前置状态", body, yes_text="重新装一遍"):
                    result = enabler.install_enabler(
                        self.current_probe().game_root if self.current_probe() else ""
                    )
                    dialogs.show_text(
                        self, "前置安装结果" if result.ok else "前置没装上", result.message
                    )
                    self.refresh()
                return

            if dialogs.ask(self, "安装前置", body, yes_text="帮我装上前置"):
                result = enabler.install_enabler(
                    self.current_probe().game_root if self.current_probe() else ""
                )
                dialogs.show_text(
                    self, "前置安装结果" if result.ok else "前置没装上", result.message
                )
                self.refresh()
            return

        # 随包没有组件时（老包/被删）退回原来的「告诉你去哪下」说法
        body = (
            "这是游戏侧的前置补丁，缺了它 mod 一律不生效。\n\n"
            f"{detail}\n\n"
            "你需要做什么：\n"
            "  1. 到 Nexus Mods 搜索 UniversalSigBypasser，下载原包；\n"
            "  2. 把里面的 .asi 和 dsound.dll 放到游戏的这个位置：\n"
            f"     {status.bin_dir if status else '<游戏根>/ProjectPBZ/Binaries/Win64'}\n"
            "  3. 回到本工具按 F5 刷新，提示就消失了。\n\n"
            "另外：游戏每次大版本更新后，前置往往也要跟着更新，否则会失效。"
        )
        dialogs.show_text(self, "前置检测", body)

    def install_mod(self, view: catalog.ModView) -> None:
        """F-4.1 安装（**单个**）。先把要发生的事完整摊给用户看，再动手。"""
        if self._install_one(view, confirm=True):
            # 装上后重新识别「手装」—— 刚装的这个有了记录，「手装」徽标该撤
            self._recognize_external(announce=False)

    def _install_one(self, view: catalog.ModView, *, confirm: bool) -> bool:
        """装一个 mod。`confirm=False` 时**不再弹框**（批量安装用）。

        用户 2026-10-11：「多选安装时，不是一次性安装多个mod，需要每次mod都要
        二次确认一下。你这个二次确认有什么意义？」—— 批量的确认已经在
        `install_selected` 开头统一做过了（列清单 + 一次是/否），再逐个弹框
        等于逼用户点 N 次，纯折磨。所以批量路径一律走 `confirm=False`，
        只在**汇总**时弹一次结果。

        返回是否装成功。
        """
        probe = self.current_probe()
        if probe is None or not probe.ok:
            QMessageBox.information(
                self,
                "先设置游戏目录",
                "还没有设置游戏目录。\n"
                "请点顶部栏的「更改目录」，选中游戏根目录（里面有 Project_Plague.exe 的那一层）。",
            )
            return False

        meta = self.store.get(view.id)
        if meta is None:
            QMessageBox.warning(self, "装不了", "这个 mod 的记录找不到了，请按 F5 刷新后重试。")
            return False

        plan = installer.plan_install(meta, probe, self.store, self.library)
        if not plan.ok:
            if confirm:
                QMessageBox.warning(self, "装不了", "\n".join(plan.blockers))
                return False
            return False

        lines = [f"目标目录：{plan.mods_dir}", ""]
        for pf in plan.files:
            mark = "覆盖" if pf.is_overwrite else "新增"
            if pf.foreign:
                # 用户 2026-10-11 问过：「已存在是指存在在哪里？已存在是指已安装吗？」
                # → 这两句话必须把"存在哪儿"讲明，不然用户只能猜。
                note = "（游戏目录里已经有这个同名文件，且不是本工具装的 —— 会被覆盖，原文件先备份）"
            elif pf.is_overwrite:
                note = "（游戏目录里已经有这个同名文件 —— 是本工具之前装的（重装/升级），会被覆盖）"
            else:
                note = ""
            lines.append(f"[{mark}] {pf.dst.name}{note}")
        if plan.kept_in_library:
            lines.append("")
            lines.append("只入库、不放进游戏目录：" + "、".join(plan.kept_in_library))

        warn_lines = list(plan.warnings)
        for item in conflicts.conflicts_for_new_mod(meta, self.store, self.library, probe):
            warn_lines.append(item.line())
        if warn_lines:
            lines.append("")
            lines.append("注意：")
            lines.extend("  " + w for w in warn_lines)

        if confirm and not self._confirm(
            "确认安装",
            f"要把「{view.name}」安装到游戏目录吗？\n{plan.summary()}",
            "\n".join(lines),
            yes_text="继续安装",
            warn=bool(warn_lines),
        ):
            return False

        result = installer.install(meta, probe, self.store, self.library, plan=plan)
        if result.ok:
            if confirm:
                text = result.message
                if result.backed_up:
                    text += f"\n\n已备份 {len(result.backed_up)} 个将被覆盖的文件，随时可以回滚。"
                if result.warnings:
                    text += "\n\n注意：\n" + "\n".join("· " + w for w in result.warnings)
                QMessageBox.information(self, "装好了", text)
            self._set_status(result.message)
        else:
            QMessageBox.warning(self, "安装失败", result.message)
            return False
        return True

    def uninstall_mod(self, view: catalog.ModView) -> None:
        """F-4.4 卸载（**单个**）。**先 dry-run 列出要删的文件给用户过目**，再真删。"""
        result = self._uninstall_one(view, confirm=True)
        if result is not None and result.ok:
            # 卸完重新识别「手装」：还原回游戏目录的原文件如果与库中
            # 某个 mod 内容相同，卡片会显示「已装（手装）」，用户一眼看出
            # "游戏里还有一份这个"（用户 2026-10-12 的场景）。
            self._recognize_external(announce=False)

    def _uninstall_one(
        self, view: catalog.ModView, *, confirm: bool
    ) -> Optional[installer.UninstallResult]:
        """卸一个 mod。`confirm=False` 时**不再弹框**（批量卸载用）。

        用户 2026-10-11 对批量安装提的意见同样适用于这里：批量的确认应该在
        开头统一做一次，而不是每个 mod 弹一次框。

        返回卸载结果；没卸成（无记录 / 用户取消 / 失败）返回 None。
        批量卸载靠 `result.restored` 汇总「有哪些原文件被还原了回去」。
        """
        # 手装的 mod（用户 2026-10-12）：游戏目录里有内容相同的文件、
        # 但没有本工具的安装记录 —— 没有记录就无从卸起，
        # 必须说清"为什么卸不了 + 该怎么办"，不能甩一句「没找到安装记录」。
        if view.external:
            if confirm:
                hit = view.external_hit
                QMessageBox.information(
                    self,
                    "这是手装的 mod",
                    f"「{view.name}」不是本工具安装的，没有安装记录。\n"
                    f"但游戏目录里有内容相同的文件（{hit.text if hit else ''}），"
                    "游戏正在加载它。\n\n"
                    "本工具不代卸没有记录的文件。你可以：\n"
                    "· 在卡片上点「在资源管理器中打开」，过去手动删除；或\n"
                    "· 用右键菜单「扫描游戏里已装的 mod」把它导入管理，之后就能正常装卸。",
                )
            return None

        probe = self.current_probe()
        game_dir = probe.game_root if (probe and probe.ok) else self.settings.active_game_dir
        if not game_dir:
            if confirm:
                QMessageBox.information(self, "先设置游戏目录", "还没有设置游戏目录，无法卸载。")
            return None

        preview = installer.uninstall(
            self.library, view.id, str(game_dir), dry_run=True, same_as_lookup=self._same_as_lookup()
        )
        if not preview.ok:
            if confirm:
                QMessageBox.information(self, "没法卸载", preview.message)
            return None

        lines = [f"游戏目录：{game_dir}", f"将删除 {len(preview.deleted)} 个文件：", ""]
        lines.extend("  " + str(p) for p in preview.deleted)
        if preview.missing:
            lines.append("")
            lines.append(f"另有 {len(preview.missing)} 个文件此前已不在磁盘上：")
            lines.extend("  " + str(p) for p in preview.missing)
        if preview.restored:
            # 用户 2026-10-12：「手动放的 mod 被管理器二次安装后卸载，文件被删一次
            # 再复原一次」—— 卸载前必须说清会还原谁，不能等卸完才让人发现。
            lines.append("")
            lines.append(
                f"⚠ 这个 mod 安装时覆盖过 {len(preview.restored)} 个原文件，"
                "卸载后会把它们还原回原位："
            )
            for p in preview.restored:
                note = preview.restored_notes.get(str(p), "")
                lines.append(f"  {p}" + (f"（{note}）" if note else ""))
            lines.append("  （在装这个 mod 之前就在游戏目录里；没手动放过就是更早的安装留下的）")
        lines.append("")
        if preview.restored:
            lines.append("只会删除上表里这些文件；上面列出的原文件会被还原，别的东西不会被动。")
        else:
            lines.append("只会删除上表里这些文件，目录里其它东西不会被动。")

        if confirm and not self._confirm(
            "确认卸载",
            f"要把「{view.name}」从游戏目录卸载吗？",
            "\n".join(lines),
            yes_text="确认卸载",
        ):
            return None

        result = installer.uninstall(
            self.library, view.id, str(game_dir), same_as_lookup=self._same_as_lookup()
        )
        if result.ok:
            if confirm:
                # 有还原时标题也点明 —— 用户要的就是「被告知有 mod 被复原」
                title = "卸好了（原文件已还原）" if result.restored else "卸好了"
                QMessageBox.information(self, title, result.message)
            self._set_status(result.message)
            return result
        if confirm:
            QMessageBox.warning(self, "卸载失败", result.message)
        return None

    # ================================================================ 详情 / 冲突
    def show_mod_detail(self, view: catalog.ModView) -> None:
        meta = self.store.get(view.id)
        lines = [
            f"显示名：{view.name}",
            f"库里目录：{view.id}",
            f"状态：{view.status_label}",
        ]
        # 用户 2026-10-12：手装的 mod 要在详情里写清"游戏在加载它、
        # 但本工具没有安装记录" —— 否则用户会以为能在这里卸载它。
        if view.external:
            hit = view.external_hit
            lines.append(
                "安装来源：手装（游戏目录里有内容相同的文件，不是本工具安装的，"
                f"没有安装记录{('，' + hit.text) if hit else ''}）"
            )
            lines.append(
                "  → 游戏正在加载这份内容；想纳入管理可用右键「扫描游戏里已装的 mod」"
            )
        if meta is not None:
            lines += [
                f"名称来源：{meta.name_source or '（未记录）'}",
                f"原始来源：{meta.source_archive or '（未记录）'}",
                f"作者：{meta.author or '（未填）'}",
                f"版本：{meta.version or '（未填）'}",
                f"内容指纹：{meta.content_hash[:16] if meta.content_hash else '（无）'}",
                f"安装目标子目录：{meta.install_subdir}",
                "",
                f"文件 {len(meta.files)} 个：",
            ]
            lines.extend(
                f"  {f.name}  （{_human_size(f.size)}，原始路径 {f.rel_path or f.name}）"
                for f in meta.files
            )
            if meta.overrides:
                lines += ["", f"声明改动的资源 {len(meta.overrides)} 项："]
                lines.extend("  " + o for o in meta.overrides)
            else:
                lines += [
                    "",
                    "没有声明 overrides —— 冲突检测只能按目标文件名判，精度有限。",
                    "想提高精度：把 mod 描述里那串 /Game/... 资源路径粘进来即可。",
                ]

        items = []
        if self._conflict_report is not None:
            mc = self._conflict_report.by_mod.get(view.id)
            if mc is not None and mc.items:
                items = mc.lines()
        if items:
            lines += ["", f"冲突 {len(items)} 处："]
            lines.extend("  " + s for s in items)

        # 用户 2026-10-11：「右键点击选择查看详情时，直接显示详情，不要二次点击。
        # 并且不要出现提示音。」→ 用无声对话框，**全部内容一次显示**
        # （原来是 QMessageBox.setDetailedText，清单藏在「显示详情」按钮后面）。
        dialogs.show_text(
            self,
            f"详情 —— {view.name}",
            "\n".join(lines),
            heading=f"{view.name} · {view.status_label}（摘要：{view.summary}）",
        )

    def show_conflict_matrix(self) -> None:
        """F-5.2 冲突矩阵：两两列出哪几对相撞。"""
        report = self._conflict_report
        if report is None or not report.items:
            QMessageBox.information(self, "没有冲突", "当前没有检测到任何冲突。")
            return
        rows = report.matrix()
        lines = [report.summary(), ""]
        if report.used_metadata:
            lines.append("（部分 mod 声明了 overrides，检测精度较高）")
        else:
            lines.append(
                "注意：目前没有任何 mod 声明 overrides，只能按「目标文件名」判冲突 ——\n"
                "也就是只能发现「两个 mod 想往同一个文件里写」，发现不了\n"
                "「两个 mod 改了同一个游戏资源但文件名不同」这种情况。\n"
                "想查后者：在详情里把 mod 描述中的 /Game/... 资源路径填进 overrides（v2 会做成自动解析 pak）。"
            )
        lines.append("")
        if rows:
            lines.append(f"两两清单（{len(rows)} 对）：")
            lines.extend("  " + item.line() for item in rows)
        singles = [i for i in report.items if not i.b_name]
        if singles:
            lines.append("")
            lines.append(f"需要留意（{len(singles)} 项）：")
            lines.extend("  " + item.line() for item in singles)

        # 用户 2026-10-11：「直接显示详情，不要二次点击。并且不要出现提示音。」
        # `lines` 的第一行本来就是 `report.summary()`，所以整份清单一次摊开即可，
        # 不需要再拆成 摘要 / 说明 / 详情 三段。
        # ⚠️ F-5.5：冲突是**提示不是拦截**，这句必须留在用户眼前。
        lines.append("")
        lines.append("这只是提示，不会阻止你安装 —— 你可以看过之后强行同时启用。")
        dialogs.show_text(self, "冲突矩阵", "\n".join(lines))

    # ================================================================ 删除 / 打开位置
    def delete_mod(self, view: catalog.ModView) -> None:
        """从 mod 库删除（不是从游戏目录卸载）。**先列清单再删。**"""
        probe = self.current_probe()
        game_dir = probe.game_root if (probe and probe.ok) else self.settings.active_game_dir
        still_installed = bool(game_dir) and self.library.is_installed(view.id, str(game_dir))

        plan = self.store.delete(view.id, dry_run=True)
        lines = [f"将从 mod 库删除「{view.name}」：", ""]
        lines.extend("  " + str(p) for p in plan)
        lines.append("")
        lines.append(f"（共 {_human_size(sum(_safe_size(p) for p in plan))}）")
        if still_installed:
            lines.append("")
            lines.append(
                "⚠ 这个 mod **还装在游戏目录里**。只从库里删掉的话，游戏目录里那些文件会变成"
                "「无人认领」—— 以后本工具再也认不出它们、也没法一键卸载了。\n"
                "建议：先在游戏目录里卸载，再从库里删除。"
            )

        if not self._confirm(
            "确认从库里删除",
            f"要从 mod 库里永久删除「{view.name}」吗？",
            "\n".join(lines),
            yes_text="确认删除",
            warn=True,
        ):
            return

        removed = self.store.delete(view.id)
        self._set_status(f"已从库中删除「{view.name}」，共 {len(removed)} 个文件。")
        self.refresh()

    def set_mod_kind(self, view: catalog.ModView, slots: List[str]) -> None:
        """手动指定这张卡显示成什么类型（2026-10-29）。

        自动识别只能靠名字/路径猜；像「WW」这种没意义的 pak 名猜不出来，
        就会显示「其他」。给用户一条手动纠正的路，比让他在评论区骂 identification
        更体面。传空列表 = 恢复自动识别。
        """
        meta = view.meta
        if slots:
            meta.slots = list(slots)
            meta.costume_source = "手动指定"
            meta.costume_confidence = "high"
        else:
            # 「重新自动识别」不能只是清空 —— 库里可能存着旧版本认错的结论
            # （2026-10-30：用户库里两个 mod 都被旧版判成「武器」，清空 slots
            #  只会变成「未知」，得重跑一遍分析才能得到正确结果）。
            info = costume.analyze(
                path_segments=[meta.source_archive or meta.name],
                pak_stems=[Path(f.name).stem for f in meta.files],
            )
            meta.slots = list(info.slots)
            meta.costume_source = info.source
            meta.costume_confidence = info.confidence
        try:
            self.store.save(meta)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.information(self, "没保存上", f"写回 meta 失败：{exc}")
            return
        self.refresh()
        label = "自动识别" if not slots else "、".join(
            costume.SLOT_LABELS.get(s, s) for s in slots
        )
        self._set_status(f"「{meta.name}」现在显示为：{label}")

    def reveal_mod(self, view: catalog.ModView) -> None:
        try:
            directory = self.store.mod_dir(view.id)
        except ValueError:
            QMessageBox.information(self, "打不开", "这个 mod 的目录名不合法，没法定位。")
            return
        if not directory.is_dir():
            QMessageBox.information(self, "打不开", f"目录不存在：{directory}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    # ------------------------------------------------------------ 小工具
    def _confirm(
        self,
        title: str,
        text: str,
        details: str,
        *,
        yes_text: str = "确定",
        warn: bool = False,
    ) -> bool:
        """统一的确认框：**默认按钮是「取消」**，完整清单直接摊开给用户看。

        默认落在取消上是刻意的 —— 这些动作都会动用户的文件，
        回车手滑不该造成后果。

        ⚠️ 2026-10-11 换成自绘的无声对话框（`dialogs.ask`），原因见
        `dialogs.SilentDialog` 的注释：QMessageBox 的图标会响提示音，
        而且它把清单藏在「显示细节」按钮后面、逼用户点第二次。
        """
        return dialogs.ask(
            self,
            title,
            details,
            heading=text,
            yes_text=yes_text,
        )

    # ------------------------------------------------------------ 窗口宽度自适应
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_narrow_layout(self.width())

    def _apply_narrow_layout(self, width: int) -> None:
        """R-18 的折叠顺序：文字先让位，按钮永不让位。

        2026-10-11（最新）：程序名 / 版本 / 作者这一行已按用户要求**整行删除**
        （只留在系统标题栏），程序内不再有可折叠的文字，两个入口按钮（关注作者 /
        关于与支持）与工具条按钮始终完整显示 —— 所以这里目前无需折叠任何东西。
        方法保留：resizeEvent 仍会调用，将来若再加可折叠文字在这里处理。
        """
        # 窗口宽度变了 → 游戏目录标签宽度也变了 → 路径的中间省略要重算
        self._refresh_game_dir_text()

    # ------------------------------------------------------------ 选择游戏目录
    def choose_game_dir(self) -> None:
        start = str(self.settings.active_game_dir or Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择游戏根目录（含 Project_Plague 的那一层）", start)
        if not chosen:
            return
        self._apply_game_dir(Path(chosen))

    def _apply_game_dir(self, path: Path, *, force: bool = False, quiet: bool = False) -> None:
        """设置游戏目录。

        `quiet=True` 时**不弹「已设置」提示框**，只写状态栏 —— 自动检测命中时用，
        用户要的就是"点一下就好"，再弹个框等于又拦了一次。
        """
        probe = gamedir.validate_game_dir(path)
        if probe.ok:
            entry = self.settings.remember_game_dir(probe)
            self.settings.save()
            detail = "\n".join(probe.evidence[:4])
            if probe.warnings:
                detail += "\n\n提示：\n" + "\n".join(f"· {w}" for w in probe.warnings)
            self._set_status(f"游戏目录已设置：{entry.path}（{probe.platform_label}）")
            if not quiet:
                QMessageBox.information(
                    self,
                    "游戏目录已设置",
                    f"路径：{entry.path}\n平台：{probe.platform_label}\n"
                    f"pak 目录：{entry.paks_dir}\n安装到：{entry.mods_dir}\n\n{detail}",
                )
            self.refresh()
            return

        # 校验没过 —— 需求文档 F-3.2：「目录可能不对，是否仍要使用」
        reasons = "\n".join(f"· {w}" for w in probe.warnings) or "· 未在该目录下找到 Content\\Paks 层级。"
        answer = QMessageBox.question(
            self,
            "目录可能不对",
            f"在「{path}」下没能定位到 Content\\Paks 层级。\n\n{reasons}\n\n是否仍要使用这个目录？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        probe.game_root = path
        probe.paks_dir = path
        probe.matched_rule = "<用户强制使用>"
        entry = self.settings.remember_game_dir(probe)
        self.settings.save()
        self.refresh()
        self._set_status(f"已强制使用目录：{entry.path}（未通过校验）")

    # ------------------------------------------------------------ B 站
    def _open_author_space(self) -> None:
        dialogs.open_bilibili(self, version.BILIBILI_SPACE_URL, "作者 B站 主页")

    # ------------------------------------------------------------ 导入
    def pick_to_import(self) -> None:
        """导入 mod —— **全局唯一入口**：文件夹 / 压缩包 / pak，一个或多个都行。

        ⚠️ 这里**不能用** `QFileDialog.getExistingDirectory()`：那是 Windows
        原生对话框，一次只能返回一个目录 —— 用户按住 Ctrl 选中 5 个文件夹再点
        「打开」，Windows 只会**进入**其中一个子目录（2026-10-10 用户实际反馈：
        「导入多个 mod 时，点击打开无法导入，会直接进入其中一个 mod 的子文件夹」）。
        Qt 自带（非原生）的框也不行，实测它把驱动器列表当内容显示。
        所以用自己画的 `dialogs.ImportPickerDialog`（见那边的注释）。
        """
        start = self.settings.scan_dirs[0] if self.settings.scan_dirs else ""
        dialog = dialogs.ImportPickerDialog(
            "导入 mod —— 文件夹 / 压缩包 / pak 文件都行", start, self
        )
        if dialog.exec() != dialogs.ImportPickerDialog.Accepted:
            return
        picked = importer.drop_nested_paths(dialog.selected_paths())
        if picked:
            self.import_paths(picked)

    def _precheck_before_import(self, paths: List[str]) -> bool:
        """导入**之前**先查重，命中就报警并让用户决定要不要继续。

        用户 2026-10-11：「如果有些mod之前已经导入过了，那么二次导入的时候
        你能识别出来吗？能识别到就给出报警提示。」

        为什么要**提前**问而不是等导入完再看结果：导入一个大合集要解压 + 算
        sha256，可能跑半分钟，最后只告诉用户"这些你上回都导过了"—— 白等。
        这里在点确定之后、真正开跑之前就问清楚。

        返回 True = 继续导入，False = 用户取消。
        """
        self._set_status("正在核对是否已导入过 …")
        try:
            hits, unknown = importer.precheck_duplicates(paths, self.store)
        except Exception as exc:                      # noqa: BLE001 - 预检不该拦住导入
            self._set_status("")
            # 预检失败**不阻断**导入 —— 真正的判重在导入流程里还有一道。
            print(f"[precheck] 预检异常，已跳过：{exc}")
            return True
        self._set_status("")

        exact = [h for h in hits if h.reason == "exact"]
        namey = [h for h in hits if h.reason == "name"]
        if not hits:
            if unknown:
                self._set_status(
                    f"{len(unknown)} 个压缩包没法提前查重（要解压才知道），导入时会再判一次。"
                )
            return True

        lines: List[str] = []
        if exact:
            lines.append(
                f"下面 {len(exact)} 项**内容指纹与库里完全一致**，就是同一个 mod：\n"
            )
            for h in exact[:12]:
                lines.append(f"· {h.label}  →  库里已有「{h.existing.name}」")
            if len(exact) > 12:
                lines.append(f"… 另有 {len(exact) - 12} 项")
        if namey:
            lines.append("")
            lines.append(f"下面 {len(namey)} 项**文件名一样但内容不同**，可能是作者改版：\n")
            for h in namey[:12]:
                lines.append(f"· {h.label}  →  库里已有「{h.existing.name}」")
            if len(namey) > 12:
                lines.append(f"… 另有 {len(namey) - 12} 项")

        all_dup = bool(exact) and not namey
        if all_dup and len(exact) == len(paths):
            title = f"这 {len(exact)} 个都已经导入过了"
            head = "全部命中已导入过的 mod。"
        else:
            title = f"有 {len(hits)} 项之前已经导入过了"
            head = "再导一次不会覆盖，也不会产生副本。"

        body = "\n".join(lines)
        if unknown:
            body += (
                f"\n\n（另外 {len(unknown)} 个压缩包没法提前判定，"
                "导入时会逐个核对内容指纹。）"
            )
        return dialogs.ask(
            self,
            title,
            body,
            heading=head + "\n要继续导入吗？",
            yes_text="继续导入",
            default_yes=False,
        )

    def scan_dir(self) -> None:
        """扫描 mod 下载目录。

        用户 2026-10-10：「为什么自动扫描还要自己选文件夹？」
        → 不再一上来就弹「选文件夹」。优先用设置里记过的目录，其次自动用
        「下载」文件夹（mod 最常下的地方）。两者都没有时，才让用户选一次。
        ⚠️ 刻意**不**把「桌面」也当默认扫描目录：桌面上常年堆着一堆跟 mod
        无关的文件夹，扫出来是一屏噪音，反而更烦。
        """
        roots: List[Path] = []
        for raw in self.settings.scan_dirs:
            p = Path(raw)
            try:
                if p.is_dir() and p not in roots:
                    roots.append(p)
            except OSError:
                continue
        if not roots:
            download = Path.home() / "Downloads"
            if download.is_dir():
                roots.append(download)
        if not roots:
            self._pick_scan_dir()
            return
        self._scan_roots(roots)

    def _pick_scan_dir(self) -> None:
        """实在没有可扫的默认目录时，才让用户挑一个。"""
        folder = QFileDialog.getExistingDirectory(
            self, "选择要扫描的目录（如「下载」文件夹）", str(Path.home())
        )
        if folder:
            self._scan_roots([Path(folder)])

    def _scan_roots(self, roots: Sequence[Path], *, from_game: bool = False) -> None:
        """扫描一批目录并弹出候选清单。

        `from_game=True`（「扫描游戏里已装的 mod」走这条）：把**裸 pak**
        也算候选，并且**不**把这些目录记进「扫描过的目录」（那是给 mod 下载
        目录用的，游戏目录不属于那里）。

        ⚠️ 整段用 try 包住（2026-10-11）：用户报过「点扫描电脑上的文件夹就闪退」。
        这里的输入是**用户挑的任意文件夹** —— 网络盘、断链的符号链接、
        权限异常的目录、深到没底的层级，任何一处抛异常都不该让整个程序消失。
        兜住之后给一个明确提示，让用户知道"这一批没扫成"而不是"软件崩了"。
        """
        try:
            self._scan_roots_impl(roots, from_game=from_game)
        except Exception as exc:                              # noqa: BLE001
            where = "、".join((p.name or str(p)) for p in roots) or "所选目录"
            QMessageBox.warning(
                self,
                "扫描没成功",
                f"扫描「{where}」时出错了：\n\n{exc}\n\n"
                "这个目录可能太大、太深，或存在无权限/已失效的子目录。\n"
                "试试直接选到装 mod 的那个文件夹（不要选磁盘根或 Steam 库根）。",
            )
            self._set_status("扫描没成功 —— 换一个更具体的文件夹再试。")

    def _scan_roots_impl(self, roots: Sequence[Path], *, from_game: bool = False) -> None:
        candidates: List[importer.ScanCandidate] = []
        for root in roots:
            found = importer_scan(root, self.store, include_bare_paks=from_game)
            if not found:
                continue
            candidates.extend(found)

        if not candidates:
            if from_game:
                QMessageBox.information(
                    self,
                    "游戏里没扫到 mod",
                    "在这些目录里没扫到 mod：\n"
                    + "\n".join(f"· {p}" for p in roots)
                    + "\n\n说明游戏里目前没装 mod，或者装的是本工具认不出的东西。",
                )
                return
            where = "、".join(p.name or str(p) for p in roots)
            answer = QMessageBox.question(
                self,
                "没有发现 mod",
                f"在「{where}」里没找到可导入的 mod。\n\n"
                "扫描会识别：zip / 7z / rar 压缩包，以及内部含 .pak 的文件夹。\n\n"
                "要不要换个文件夹扫？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self._pick_scan_dir()
            return

        where = "、".join(p.name or str(p) for p in roots)
        dialog = dialogs.ScanDialog(where, candidates, self)
        if dialog.exec() != dialogs.ScanDialog.Accepted:
            return
        selected = dialog.selected_paths()
        if not selected:
            return
        if not from_game:
            self.settings.add_scan_dir(str(roots[0]))
            self.settings.save()
        self.import_paths(selected)

    # ------------------------------------------------------------ 扫描游戏里已装的 mod
    def scan_game_mods(self) -> None:
        """自动定位游戏目录 → 扫描游戏里的 mod 安装目录（~mods / LogicMods）。

        入口：「扫描 mod…」下拉里的「扫描游戏里已装的 mod」。
        （2026-10-11：原来是独立按钮「自动扫描游戏 mod 目录」，用户判定它与
        「扫描目录…」功能重复，要求合并成一个下拉按钮 —— 功能没砍，只是收进菜单。）

        先自动定位游戏目录（5 路检测，唯一命中静默应用），再扫其中的 mod
        安装目录，列出来供批量导入。全程不用浏览文件夹。

        和「扫描电脑上的文件夹…」的区别：这里扫的是**已经装进游戏**的 mod
        （`~mods` / `LogicMods` 下躺着的裸 `.pak`，以及用户手建的子目录）。
        """
        entry = self.settings.active_entry
        if entry is None:
            self._set_status("还没设游戏目录，正在自动搜索…（Steam / Epic / WeGame / Xbox / 注册表）")
            self.autodetect_game_dir()          # 唯一命中会静默应用
            entry = self.settings.active_entry
        if entry is None:
            self._set_status("没能自动定位游戏目录 —— 请先用「更改目录」手动指定一次。")
            return

        paks = Path(entry.paks_dir)
        roots: List[Path] = []
        for cand in (Path(entry.mods_dir), paks / pakfiles.MARKER_LOGIC_MODS, paks):
            try:
                if cand.is_dir() and cand not in roots:
                    roots.append(cand)
            except OSError:
                continue

        if not roots:
            # 全新游戏本来就没有 ~mods —— 这是正常状态，不是错误。
            QMessageBox.information(
                self,
                "游戏里还没有 mod 目录",
                f"在游戏目录下没找到 mod 安装目录：\n{paks}\n\n"
                "这是**正常**的 —— 全新的游戏本来就没有 ~mods 文件夹。\n"
                "装第一个 mod 时本工具会自动把它建出来，不需要你手动新建。\n\n"
                f"预期位置：{Path(entry.mods_dir)}",
            )
            return

        self._scan_roots(roots, from_game=True)

    # ------------------------------------------------------------ 识别手装的 mod
    def _same_as_lookup(self):
        """给 `installer.uninstall(same_as_lookup=...)` 的查询函数。

        还原提示里要回答「这个被还原的文件是什么」—— 内容与库中某个 mod
        一致时，至少能说清"它和库里的 X 是同一份东西"（用户 2026-10-12）。

        索引**建一次缓存住**：批量卸载会对每个 mod 调一次 uninstall，
        每次重建索引等于把库里所有 meta.json 读 N 遍。
        失效条件只认「库内 mod 条数变化」（导入/删除都会改）——
        mod 内容不变时 sha256 集合就不变，改名不影响匹配。
        """
        sig = len(self.store.all())
        cached = getattr(self, "_same_as_cache", None)
        if cached is not None and cached[0] == sig:
            index = cached[1]
        else:
            index = wildscan.build_mod_index(self.store)
            self._same_as_cache = (sig, index)
        return lambda sha: wildscan.lookup_mod(index, sha)

    def _wild_dirs(self) -> List[Path]:
        """要识别的目录：`~mods` + `LogicMods`（都没有就空列表）。

        ⚠️ 只扫这两个目录，**绝不扫 Paks 根** —— 那里躺着游戏本体
        （`Project_Plague-Windows*.pak`，~60 GB），扫它既慢又毫无意义。
        """
        probe = self.current_probe()
        if probe is None or not probe.ok or not probe.paks_dir:
            return []
        dirs: List[Path] = []
        for cand in (
            gamedir.resolve_mods_dir(probe.platform, probe.paks_dir),
            probe.paks_dir / pakfiles.MARKER_LOGIC_MODS,
        ):
            try:
                if cand.is_dir() and cand not in dirs:
                    dirs.append(cand)
            except OSError:
                continue
        return dirs

    def _recognize_external(
        self, *, force: bool = False, announce: bool = True
    ) -> Optional[wildscan.WildScan]:
        """识别「游戏目录里躺着、但不受本工具管理」的 mod（用户 2026-10-12）。

        原话：「我手动安装了 10 个 mod，其中 5 个与管理器中的 mod 相同。
        管理器能识别到这五个 mod 吗？并在管理器中显示已经安装？」

        判据是**内容 sha256**（与查重、指纹同一套规矩）：文件名、
        路径都不看。命中的 mod 卡片会显示「已装」+「手装」徽标。

        触发时机：
          · 手动：右键菜单「识别游戏里已有的 mod」（force=True，无视缓存）；
          · 自动：导入完成 / 安装 / 卸载之后（不 force，目录没变就直接用缓存，
            成本只剩一次目录列举；`AUTO_MAX_BYTES` 上限防止哈希几 GB 卡界面）。

        返回 None = 没有可识别的目录（还没设游戏目录 / 游戏里没有 ~mods）。
        """
        dirs = self._wild_dirs()
        if not dirs:
            return None
        index = wildscan.build_mod_index(self.store)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            scan = wildscan.recognize(
                dirs,
                index,
                force=force,
                max_bytes=None if force else wildscan.AUTO_MAX_BYTES,
            )
        finally:
            QApplication.restoreOverrideCursor()
        self.refresh()
        if announce:
            if not scan.scanned_at and not scan.hits:
                # 超过自动上限被跳过（recognize 返回空 scan）
                self._set_status(
                    "游戏目录里的 mod 文件太多，已跳过自动识别 —— "
                    "需要的话请手动点右键「识别游戏里已有的 mod」。"
                )
            else:
                msg = scan.summary()
                if scan.truncated:
                    msg += "（目录太大，只扫了一部分）"
                self._set_status(msg)
        return scan

    def import_paths(self, paths: Sequence[str | Path]) -> None:
        paths = [p for p in paths if p]
        if not paths:
            return
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "正在导入", "上一批导入还没结束，请稍候。")
            return
        # 先查重并报警（用户 2026-10-11 要求「二次导入要能识别出来」）
        if not self._precheck_before_import([str(p) for p in paths]):
            self._set_status("已取消导入。")
            return

        self._progress_dialog = QProgressDialog("正在准备导入…", "取消", 0, len(paths), self)
        self._progress_dialog.setWindowTitle("导入 mod")
        self._progress_dialog.setWindowModality(Qt.WindowModal)
        self._progress_dialog.setMinimumDuration(300)
        self._progress_dialog.setAutoClose(False)
        self._progress_dialog.setAutoReset(False)
        self._progress_dialog.canceled.connect(self._cancel_import)

        self._worker = ImportWorker(paths, self.store, self)
        self._worker.progressed.connect(self._on_import_progress)
        self._worker.report_ready.connect(self._on_import_done)
        self._worker.start()

    def _on_import_progress(self, stage: str, current: int, total: int, message: str) -> None:
        if self._progress_dialog is None:
            return
        self._progress_dialog.setMaximum(max(total, 1))
        self._progress_dialog.setValue(max(0, current - 1))
        self._progress_dialog.setLabelText(f"[{stage}] {message}")

    def _on_import_done(self, report) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.setValue(self._progress_dialog.maximum())
            self._progress_dialog.close()
            self._progress_dialog = None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

        self.refresh()
        self._set_status(report.summary())
        self._show_import_summary(report)
        # 导完认一次「手装」（用户 2026-10-12）：刚导入的 mod 如果和
        # 游戏 ~mods 里躺着的文件内容相同，卡片会直接显示「已装（手装）」。
        # 目录没变时 recognize 只用缓存，成本是一次目录列举。
        self._recognize_external(announce=False)

    def _cancel_import(self) -> None:
        # QThread 无法安全强杀（会留下写了一半的库），这里只提示；实际取消在单条粒度上生效
        self._set_status("已请求取消，正在结束当前这一条…")

    def _show_import_summary(self, report) -> None:
        if not report.outcomes:
            return

        blocks: List[str] = []
        if report.imported:
            lines = [f"· {o.meta.name}（{len(o.meta.files)} 个文件）" for o in report.imported[:20]]
            if len(report.imported) > 20:
                lines.append(f"… 另有 {len(report.imported) - 20} 个")
            blocks.append("【已导入】\n" + "\n".join(lines))

        warned = [o for o in report.imported if o.warnings]
        if warned:
            lines = []
            for o in warned[:8]:
                for w in o.warnings:
                    lines.append(f"· {o.meta.name}：{w}")
            blocks.append("【需要注意】\n" + "\n".join(lines))

        if report.duplicates:
            dup_lines = []
            for o in report.duplicates[:12]:
                dup_lines.append(
                    f"· {o.display_name} → 库里已有「{o.existing.name}」，内容指纹一致"
                    if o.existing
                    else f"· {o.display_name}"
                )
            if len(report.duplicates) > 12:
                dup_lines.append(f"… 另有 {len(report.duplicates) - 12} 项")
            blocks.append(
                "【重复 —— 之前已经导入过了，没有产生副本】\n" + "\n".join(dup_lines)
            )
        if report.skipped:
            blocks.append(
                "【已跳过】\n"
                + "\n".join(f"· {o.source}：{o.message}" for o in report.skipped[:10])
            )
        if report.failed:
            blocks.append(
                "【失败】\n"
                + "\n".join(f"· {o.source}\n  {o.message}\n  {o.detail}" for o in report.failed[:8])
            )

        # ⚠️ 标题按"有没有重复"换口吻 —— 用户 2026-10-11 问「二次导入能识别出来吗」，
        #   答案不能藏在「显示详情」二级按钮后面（用户明确说过「不要二次点击」）。
        if report.duplicates and not report.imported:
            title = f"导入没新增 —— {len(report.duplicates)} 项之前已经导入过"
            head = "没有产生任何副本。"
        elif report.duplicates:
            title = f"导入完成（其中 {len(report.duplicates)} 项之前已经导入过）"
            head = ""
        else:
            title = "导入完成"
            head = ""
        if report.imported:
            head = "导入只是把 mod 收进本工具的库，还没有安装到游戏目录。"

        dialogs.show_text(self, title, "\n\n".join(blocks), heading=head)

    # ------------------------------------------------------------ 改名
    def rename_mod(self, view: catalog.ModView) -> None:
        """F-7.1：改名只写 library 里的 meta.json，**绝不改原始 pak 文件名**。"""
        new_name, ok = QInputDialog.getText(
            self,
            "重命名 mod",
            "新名称（只改本工具显示的名字，不会修改原始 pak 文件）：",
            text=view.name,
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == view.name:
            return

        meta = self.store.get(view.id)
        if meta is None:
            QMessageBox.warning(self, "改不了", "这个 mod 的记录找不到了，请按 F5 刷新后重试。")
            return
        meta.name = new_name
        meta.name_source = "用户手动改名"
        self.store.update_meta(meta)
        self.refresh()
        self._set_status(f"已改名为「{new_name}」（原始文件未改动）")

    def change_preview(self, view: catalog.ModView) -> None:
        """第 3 档预览图：用户自己指一张。

        为什么必须有这个入口：自动挑图只能在**包里已有的图片**里挑。
        作者要是压根没放角色预览图（包里只有游戏内菜单截图 / 操作说明图），
        自动挑出来的就只能是那类图 —— 用户会说"识别的图片不对"。
        这时把选择权交给用户，是唯一的正解。
        """
        meta = self.store.get(view.id)
        if meta is None:
            QMessageBox.warning(self, "换不了", "这个 mod 的记录找不到了，请按 F5 刷新后重试。")
            return

        start = str(Path.home() / "Pictures")
        if not Path(start).is_dir():
            start = str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            f"给「{view.name}」选一张预览图",
            start,
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*.*)",
        )
        if not chosen:
            return

        try:
            self.store.set_user_preview(meta, Path(chosen))
        except Exception as exc:  # noqa: BLE001 - 拷图失败不该让窗口崩
            QMessageBox.warning(self, "换不了", f"这张图没法用：\n{exc}")
            return
        self.refresh()
        self._set_status(f"「{view.name}」的预览图已换成你选的那张。")

    # ------------------------------------------------------------ 拖放
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_status("松开鼠标即可导入")

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths: List[str] = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        if paths:
            event.acceptProposedAction()
            self.import_paths(paths)

    # ------------------------------------------------------------ 关闭
    def closeEvent(self, event) -> None:  # noqa: N802
        self.settings.ui["window_width"] = self.width()
        self.settings.ui["window_height"] = self.height()
        try:
            self.settings.save()
        except Exception:  # noqa: BLE001 - 存不下窗口尺寸不该阻止退出
            pass
        super().closeEvent(event)


def importer_scan(root: Path, store: modstore.ModStore, *, include_bare_paks: bool = False):
    """把 importer.scan_candidates 包一层，方便在这里做异常兜底。

    扫描用户自己选的目录（很可能是 C:\\Users\\xxx\\Downloads），
    里面什么奇怪的东西都有；任何异常都不该让窗口崩掉。

    `include_bare_paks=True` 给「扫描游戏里已装的 mod」用 —— 游戏里的
    `~mods` 下面就是裸 `.pak`，不带这个开关会一条都扫不出来。
    """
    from .. import importer

    try:
        return importer.scan_candidates(root, store, include_bare_paks=include_bare_paks)
    except Exception:  # noqa: BLE001
        return []


# ================================================================ 冲突明细面板
class _ConflictPanel(QFrame):
    """「冲突」筛选页顶部的冲突清单。

    用户 2026-10-11 的要求有两半，都在这里落：
      1.「冲突不要显示在右上角」→ 删掉了工具条右侧那个常驻的
        「冲突 🔴 3 · 点这里看矩阵」标签；**卡片本身用组色边框**表示冲突。
      2.「（矩阵）放到冲突这一页中」→ 明细清单只在「冲突」筛选页出现。

    为什么是清单而不是真·矩阵表：F-5.2 要求"两两列出哪几对相撞"，
    但一屏放不下 N×N 的表格，而且用户真正想知道的是"哪几个 mod 撞了、撞在哪"。
    所以这里按**冲突组**分组列出（组色与卡片边框一致，一眼能对上）。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("conflictPanel")
        self.setFrameShape(QFrame.NoFrame)
        #: 折叠状态必须**跨刷新保留** —— 面板每次装/卸载 mod 都会重填文本，
        #: 状态要是跟着重建走，用户刚收起、一刷新又自己弹开。
        self._collapsed = False
        self._count = 0

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(12, 8, 12, 8)
        self._lay.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self._title = QLabel()
        self._title.setStyleSheet(f"color:{theme.TEXT}; font-weight:600;")
        head.addWidget(self._title)
        head.addStretch(1)
        # 用户 2026-10-11：「这个冲突清单，无法缩小……可以加个隐藏或者缩小的按钮。」
        #   实测 34 个冲突的清单能吃掉大半屏，把卡片全挤到下面去了。
        #   ⚠️ 不要做成"整块隐藏"：收起后必须还留一行标题，否则用户不知道
        #     冲突清单去哪了、也没法再展开。
        self._btn_toggle = QPushButton()
        self._btn_toggle.setObjectName("toggleButton")
        self._btn_toggle.setCursor(Qt.PointingHandCursor)
        self._btn_toggle.clicked.connect(self._toggle_collapsed)
        head.addWidget(self._btn_toggle)
        self._lay.addLayout(head)

        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setTextInteractionFlags(Qt.NoTextInteraction)
        self._body.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
        self._lay.addWidget(self._body)
        self._sync_toggle()
        self.setVisible(False)

    def _toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self._sync_toggle()

    def _sync_toggle(self) -> None:
        """把折叠状态同步到界面（文字 + 可见性 + 标题里的条数）。"""
        self._body.setVisible(not self._collapsed)
        self._btn_toggle.setText("展开" if self._collapsed else "收起")
        self._btn_toggle.setToolTip(
            "展开冲突清单" if self._collapsed
            else "收起冲突清单，把版面让给下面的卡片"
        )
        base = "冲突清单"
        if self._count:
            base += f"（{self._count} 个 mod 相撞）"
        self._title.setText(base)

    def update_content(self, report, *, visible: bool) -> None:
        """填入最新检测结果并决定显不显示。

        ⚠️ 每次都**重新**拼文本 —— 装/卸载 mod 会改变冲突关系，
        沿用旧文本会让面板和卡片边框对不上（卡片变了、清单没变）。
        """
        self.setVisible(bool(visible))
        self._body.setText("")
        self._count = 0
        if not visible or report is None:
            self._sync_toggle()
            return

        colors = conflicts.group_colors(report)
        # 按 mod 聚合：`{mod_id: {本名, 与之相撞的 mod 名, 撞的资源}}`
        groups: dict[str, dict] = {}
        names: dict[str, str] = {}
        for item in report.items:
            if item.a_id:
                names[item.a_id] = item.a_name
            if item.b_id:
                names[item.b_id] = item.b_name
        for item in report.items:
            if not item.a_id:
                continue                                  # 单边冲突（落点已有别人的文件）
            for mid, other_id, other_name in (
                (item.a_id, item.b_id, item.b_name),
                (item.b_id, item.a_id, item.a_name),
            ):
                if not mid:
                    continue
                g = groups.setdefault(mid, {"others": [], "resources": []})
                if other_id and names.get(other_id, other_name) != names.get(mid):
                    other = names.get(other_id, other_name)
                    if other and other not in g["others"]:
                        g["others"].append(other)
                if item.resource and item.resource not in g["resources"]:
                    g["resources"].append(item.resource)

        lines: list[str] = []
        for mid, g in groups.items():
            who = names.get(mid, mid)
            if g["others"]:
                # ⚠️ 用「与」而不是 `⟷`：U+27F7 这类数学箭头在部分中文字体里
                #   **没有字形**，Qt 会画成空心方框（离屏渲染时实测到了）。
                #   「与」是常用汉字，任何环境都一定有。
                who += " 与 " + "、".join(g["others"])
            res = "、".join(g["resources"][:3])
            more = f" 等 {len(g['resources'])} 处" if len(g["resources"]) > 3 else ""
            lines.append(f"· {who} → {res}{more}")
        self._count = len(groups)
        self._body.setText("\n".join(lines))
        self._sync_toggle()
