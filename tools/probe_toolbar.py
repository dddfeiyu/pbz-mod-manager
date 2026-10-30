# -*- coding: utf-8 -*-
"""探针：把工具条上每个控件的几何/可见性打出来，定位「按钮不见了」的真实原因。

先试「offscreen 平台」（无字体，宽度不可信，但可见性/是否存在可信），
再用系统平台跑一次拿真实几何 —— 用 `--real` 开关。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if "--real" not in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

data_dir = Path(tempfile.mkdtemp(prefix="wmm-probe-toolbar-"))
os.environ["WMM_DATA_DIR"] = str(data_dir)

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from wmm import library as library_mod, modstore, settings as settings_mod  # noqa: E402
from wmm.ui import fonts, qss  # noqa: E402
from wmm.ui.main_window import MainWindow  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setStyleSheet(qss.build_qss())
fonts.ensure_cjk_font()

settings = settings_mod.Settings.load(data_dir / "s.json")
store = modstore.ModStore(data_dir / "mods")
store.ensure()
lib = library_mod.Library.load(data_dir / "lib.json")

win = MainWindow(settings, store, lib)
win.resize(1082, 627)
win.show()
for _ in range(8):
    app.processEvents()

print(f"平台 = {app.platformName()}")
print(f"窗口 = {win.width()}x{win.height()}  最小 = {win.minimumWidth()}x{win.minimumHeight()}")
print()

targets = [
    ("安装所选", win.btn_install_sel),
    ("卸载所选", win.btn_uninstall_sel),
    ("全选", win.btn_check_all),
    ("自动检测游戏目录", win.btn_autodetect),
]
for label, w in targets:
    g = w.geometry()
    tl = w.mapTo(win, g.topLeft() - g.topLeft())  # 相对窗口
    print(
        f"{label:<18} pos=({tl.x():>5},{tl.y():>4}) size=({g.width():>4}x{g.height():>3}) "
        f"visible={w.isVisible()} hidden={w.isHidden()} text={w.text()!r}"
    )

print()
print("---- 工具条里所有 QPushButton（按 y 分行）----")
bar = win.btn_install_sel.parentWidget()
rows: dict[int, list[str]] = {}
for b in bar.findChildren(QPushButton):
    tl = b.mapTo(win, b.rect().topLeft())
    rows.setdefault(tl.y(), []).append(
        f"{b.text()!r}@x={tl.x()} w={b.width()} vis={b.isVisible()}"
    )
for y in sorted(rows):
    print(f"y={y}: " + " | ".join(rows[y]))

print()
print("---- 窗口整体尺寸策略 ----")
print("win.minimumSizeHint =", win.minimumSizeHint())
app.quit()
