# -*- coding: utf-8 -*-
"""应用入口 —— 组装依赖、做启动前自检、开窗。

启动顺序刻意是这样：
  1. 加载 QApplication（Qt 要求任何控件之前必须先有它）
  2. **系统要求检查**（Win10 1809+ / 64 位）—— 不满足时给中文提示再退出，
     而不是让它在后面某个地方以"找不到 DLL"的形式崩掉（需求文档 11.1）
  3. 创建数据目录 / 读取 settings.json 与 library.json
  4. 开主窗

**不做任何联网、不注册任何计划任务、不写注册表**（需求文档 R-13 / F-10.18：纯本地）。
"""

from __future__ import annotations

import sys
import traceback
from typing import List, Optional

from .. import apppaths, library as library_mod, modstore, settings as settings_mod, sysreq, version


def _install_excepthook() -> None:
    """把未捕获异常写进日志文件。

    公开分发意味着**没有客服**。用户报"闪退了"时，如果没有日志，
    唯一的线索就是那句"闪退了"。所以宁可多写一个文件。
    """
    log_path = apppaths.logs_dir() / "error.log"

    def hook(exc_type, exc_value, exc_tb) -> None:
        try:
            apppaths.logs_dir().mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write("\n" + "=" * 70 + "\n")
                fh.write(f"{version.APP_NAME_ZH} v{version.VERSION}\n")
                fh.write(apppaths.describe() + "\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=fh)
        except Exception:  # noqa: BLE001 - 写日志失败不能再抛
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


def _install_qt_translations(app) -> List[object]:
    """把 Qt **自带**的标准按钮文案切到中文。

    不装的话，`QMessageBox.setDetailedText()` 会在全中文界面上摆出一颗
    「Hide Details...」（点开变「Show Details...」），以及「OK」这类英文按钮 ——
    用户 2026-10-10 截图圈出来问「红圈中是什么？」的就是它。

    Qt 自带 `qtbase_zh_CN.qm`（`PySide6/translations/` 下），加载即得
    「确定 / 隐藏详细信息 / 显示详细信息」，不需要自己翻译。

    ⚠️ 两个坑：
      1. `QTranslator` 必须**被持有引用**。函数一返回就没人引用它 → 被 GC →
         翻译立刻失效（界面又变英文，且极难复现）。所以挂在 `app` 上。
      2. 打包时 `PySide6/translations/` 未必会被 PyInstaller 带进去，
         加载失败要**静默降级**（最多是英文按钮），绝不能因此启动不了。
    """
    from PySide6.QtCore import QLibraryInfo, QTranslator

    try:
        base = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    except Exception:  # noqa: BLE001
        return []

    installed: List[object] = []
    for name in ("qtbase_zh_CN", "qt_zh_CN"):
        translator = QTranslator(app)
        if translator.load(name, base):
            app.installTranslator(translator)
            installed.append(translator)
    app._wmm_qt_translators = installed        # 防 GC，见上面的坑 1
    return installed


def _silence_message_boxes() -> None:
    """让**所有** `QMessageBox` 都变得无声 —— 把弹窗统一换成自绘对话框。

    用户 2026-10-11 连续两轮反馈「有提示音」「不要出现提示音」。
    Qt 在 Windows 上按 `QMessageBox` 的**图标类型**播放系统提示音
    （Information 是"星号"声，Warning 是"感叹号"声）。所以只要不出现图标，就静音。

    ⚠️ 实测记录（别再走弯路）：
      1. patch `QApplication.beep` —— **无效**。建三种图标的框，它一次都没被调用，
         说明声音不走 Python 层的 `beep`；
      2. 只 patch 实例方法 `QMessageBox.setIcon` —— **无效**。`QMessageBox.information()`
         这类**静态方法**是 C++ 实现的，内部直接调 C++ 的 setIcon，
         绕过 Python 侧的属性替换（实测：patch 后静态方法建出来的框，图标仍是原样）。

    → 所以这里**直接替换静态方法本身**（information / warning / question / critical），
      把它们重定向到自绘的 `dialogs.show_text` / `dialogs.ask`。
      好处：项目里 60+ 处调用点**一行都不用改**，且以后新写的代码也自动是无声的。
      顺带保留了实例方法 `setIcon` 的兜底 patch，覆盖"直接 new QMessageBox()"的写法。
    """
    from PySide6.QtWidgets import QMessageBox

    if getattr(QMessageBox, "_wmm_silenced", False):
        return

    from . import dialogs

    def _info(parent, title, text, buttons=None, defaultButton=None, *a, **k):
        dialogs.show_text(parent, str(title), str(text))
        return QMessageBox.Ok

    def _question(parent, title, text, buttons=None, defaultButton=None, *a, **k):
        """把标准按钮组合映射成「肯定 / 取消」。

        项目里的用法只有 Yes|No 和 Ok|Cancel 两类，返回值必须与原 API 兼容
        （调用方拿它跟 `QMessageBox.Yes` / `QMessageBox.Ok` 比）。
        """
        raw = int(buttons) if buttons is not None else int(QMessageBox.Yes | QMessageBox.No)
        if raw & int(QMessageBox.Yes):
            yes_text, yes_ret = "是", QMessageBox.Yes
        else:
            yes_text, yes_ret = "确定", QMessageBox.Ok
        ok = dialogs.ask(parent, str(title), str(text), yes_text=yes_text)
        return yes_ret if ok else QMessageBox.No

    QMessageBox.information = staticmethod(_info)          # type: ignore[method-assign]
    QMessageBox.warning = staticmethod(_info)              # type: ignore[method-assign]
    QMessageBox.critical = staticmethod(_info)             # type: ignore[method-assign]
    QMessageBox.question = staticmethod(_question)         # type: ignore[method-assign]

    # 兜底：仍然直接 new QMessageBox() 的地方，图标一律清掉（同样是为了静音）
    _orig_set_icon = QMessageBox.setIcon

    def set_icon(self, _icon):                             # noqa: ANN001, N802
        return _orig_set_icon(self, QMessageBox.NoIcon)

    QMessageBox.setIcon = set_icon                         # type: ignore[method-assign]
    QMessageBox._wmm_silenced = True                       # type: ignore[attr-defined]


def _app_icon():
    """自制图标（F-11.22：不得使用游戏官方 Logo / 宣传图 / 字体）。

    找不到图标文件时返回空 QIcon —— 图标缺失绝不能阻止程序启动。
    """
    from PySide6.QtGui import QIcon

    path = apppaths.resource_path("assets", "icon.ico")
    if path.is_file():
        return QIcon(str(path))
    return QIcon()


def run(argv: Optional[List[str]] = None) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    argv = list(argv or sys.argv)
    app = QApplication(argv)
    app.setApplicationName(version.APP_NAME_EN)
    # ⚠️ **不要**再调 setApplicationDisplayName()。Qt 会把它以「 - 名字」的形式
    # 追加到**每个**窗口标题后面：实测主窗口标题会变成
    # 「明末MOD管理器 v0.1.3 - 明末MOD管理器」这种重复的一长串，
    # 而用户要的是「明末MOD管理器 v0.1.4 - by 诸葛-建国」（见 MainWindow.__init__）。
    app.setApplicationVersion(version.VERSION)
    app.setOrganizationName(version.AUTHOR_ID)
    # Qt6 默认已按系统 DPI 缩放；这里显式声明，避免高 DPI 下卡片被压扁
    app.setStyle("Fusion")

    # 必须在**任何对话框弹出之前**装好，否则第一个框还是英文按钮
    _install_qt_translations(app)

    # 同理，必须在任何对话框弹出之前静音（用户 2026-10-11：「不要出现提示音」）
    _silence_message_boxes()



    _install_excepthook()

    from . import qss

    app.setStyleSheet(qss.build_qss())

    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    # ---- 系统要求检查 ----
    check = sysreq.check()
    if not check.ok:
        QMessageBox.critical(None, f"{version.APP_NAME_ZH} 无法运行", check.message)
        return 2

    # ---- 数据目录与持久化 ----
    apppaths.ensure_data_dirs()
    app_settings = settings_mod.Settings.load()
    lib = library_mod.Library.load()
    store = modstore.ModStore(app_settings.mods_library_dir)
    store.ensure()

    from .main_window import MainWindow

    def build_window() -> "MainWindow":
        window = MainWindow(app_settings, store, lib)
        if not icon.isNull():
            window.setWindowIcon(icon)
        if not app_settings.active_game_dir:
            window.statusBar().showMessage(
                "第一次使用：请点顶部「更改目录」选一次游戏根目录（含 Project_Plague 的那一层）。"
                "不选也能导入 mod，只是无法安装。"
            )
        return window

    # ---- 无弹窗模式：直接开主界面 ----
    if not app_settings.show_splash:
        window = build_window()
        window.show()
        return int(app.exec())

    # ---- 有弹窗模式（F-10.5：主界面在后台并行加载）----
    # 说明：Qt 控件只能在 GUI 线程创建，所以「并行加载」的落地方式是
    # **先把弹窗显示出来，再用 0ms 定时器把主窗构建推回事件循环** ——
    # 弹窗因此有一次真正的绘制机会，用户看到的是立刻出现的弹窗，
    # 而不是「双击后卡 1 秒才出画面」。
    from .splash import SplashDialog

    splash = SplashDialog()
    if not icon.isNull():
        splash.setWindowIcon(icon)
    splash.show()
    splash.raise_()
    splash.activateWindow()

    state: dict = {"window": None, "wants_enter": False}

    def reveal() -> None:
        window = state["window"]
        if window is None:
            return
        window.show()
        window.raise_()
        window.activateWindow()
        if splash.isVisible():
            splash.hide()

    def on_enter() -> None:
        # 主窗还没建好：不是转圈，而是明确告知「正在加载主界面」并禁用按钮。
        if state["window"] is None:
            state["wants_enter"] = True
            splash.set_loading(True)
            return
        reveal()

    def on_ready() -> None:
        state["window"] = build_window()
        # 弹窗被关掉（没点按钮）也要进主界面，否则用户面对的是"什么都没了"
        if state["wants_enter"] or not splash.isVisible():
            reveal()

    splash.enter_requested.connect(on_enter)
    QTimer.singleShot(0, on_ready)

    return int(app.exec())


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
