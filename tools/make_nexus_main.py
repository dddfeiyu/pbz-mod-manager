# -*- coding: utf-8 -*-
"""生成 Nexus 必填主图（1920×1080）。

要点：N 网列表页会把这张图**居中裁成方块**，所以关键内容必须落在中间
约 1080×1080 的「安全区」里 —— 顶部标题条和底部说明被裁掉也不影响理解。

做法：离屏渲染真实主窗（用用户库里的真数据）→ 放到画布中间 → 加英文标注。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QFontDatabase,
    QImage,
    QPainter,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

W, H = 1920, 1080
OUT = ROOT / "docs" / "nexus-main-1920x1080.png"


def _load_fonts() -> None:
    for name in ("segoeuib.ttf", "segoeui.ttf", "arialbd.ttf", "arial.ttf",
                 "msyh.ttc", "msyhbd.ttc"):
        f = Path("C:/Windows/Fonts") / name
        if f.is_file():
            QFontDatabase.addApplicationFont(str(f))


def render_window() -> QImage:
    """离屏渲染真实主窗，返回整窗截图。

    截图必须**有内容**才像样。这里往临时库里造几组演示 mod（英文名，
    老外看得懂；也顺便演示不同类型卡片的观感）——N 网作者普遍这么干。
    """
    import tempfile

    # 用干净的临时数据目录，绝不碰用户真实库
    data_dir = Path(tempfile.mkdtemp(prefix="pbz-shot-"))
    os.environ["WMM_DATA_DIR"] = str(data_dir)

    from wmm import apppaths, library, modstore, settings
    from wmm.modstore import ModFile, ModMeta
    from wmm.ui import main_window, qss

    app = QApplication.instance()
    app.setStyleSheet(qss.build_qss())

    store = modstore.ModStore(apppaths.mods_dir())
    store.ensure()

    demo = [
        ("Heavenly Lock - Blade Replacer", "Weapon"),
        ("Crimson Robe - Full Outfit", "Full"),
        ("Iron Shoulder Guards", "Arms"),
        ("66 Days Unlocks", ""),
        ("Camera and FOV Tweaks", ""),
        ("Dark Ink ReShade", ""),
        ("Swift Dodge - Animation", ""),
        ("HUD Minimal", ""),
    ]
    for i, (name, slot) in enumerate(demo):
        mid = f"demo{i:02d}"
        meta = ModMeta(id=mid, name=name, content_hash=f"{i:02x}" * 8)
        store.save(meta)
        fdir = store.files_dir(mid)
        fdir.mkdir(parents=True, exist_ok=True)
        files = []
        for ext in (".pak", ".ucas", ".utoc"):
            f = fdir / f"demo{i:02d}_P{ext}"
            f.write_bytes(b"DEMO" * 64)
            files.append(
                ModFile(name=f.name, rel_path=f.name, size=f.stat().st_size,
                        sha256=modstore.hash_file(f))
            )
        meta.files = files
        meta.slots = [slot] if slot else []
        meta.costume_source = "手动指定" if slot else ""
        store.save(meta)

    st = settings.Settings.load(apppaths.settings_path())
    # 让界面显示真实游戏路径与前置状态（这样截图里能看到「前置已就位」这个卖点）。
    # 只读真实目录做校验，settings 本身写在临时目录里，不碰用户数据。
    #
    # 路径从环境变量读，不写死在代码里（本仓库要公开，不能带上个人机器路径）：
    #   WMM_GAME_DIR="E:\SteamLibrary\steamapps\common\PBZ" python tools/make_nexus_main.py
    import os

    game_dir = os.environ.get("WMM_GAME_DIR", "").strip()
    if game_dir:
        try:
            from wmm import gamedir

            probe = gamedir.validate_game_dir(game_dir)
            if probe.ok:
                st.remember_game_dir(probe)
        except Exception:
            pass
    lib = library.Library.load(apppaths.library_path())
    win = main_window.MainWindow(st, store, lib)
    win.resize(1400, 940)
    win.show()
    for _ in range(3):
        app.processEvents()
    pix = win.grab()
    win.close()
    print(f"  截图内 mod 数: {len(win._views)}")
    return pix.toImage()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    _load_fonts()

    shot = render_window()
    if shot.isNull():
        print("主窗渲染失败")
        return 1

    canvas = QImage(W, H, QImage.Format_ARGB32)
    canvas.fill(QColor("#12100F"))
    p = QPainter(canvas)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)

    # ---- 顶部标题条 ----
    p.setPen(QColor("#E8C9A0"))
    f = QFont("Segoe UI")
    f.setPixelSize(26)
    f.setBold(True)
    f.setLetterSpacing(QFont.AbsoluteSpacing, 6)
    p.setFont(f)
    p.drawText(QRectF(0, 40, W, 40), Qt.AlignHCenter | Qt.AlignVCenter,
               "PHANTOM BLADE ZERO   ·   MOD MANAGER")

    # ---- 中间：界面截图（保持在安全区内）----
    # 宽高都要塞得下（上面留给标题条、下面留给那行卖点）
    max_w, max_h = 1500, 880
    scale = min(max_w / shot.width(), max_h / shot.height())
    target_w = int(shot.width() * scale)
    target_h = int(shot.height() * scale)
    x = (W - target_w) // 2
    y = 110
    # 细边框，让截图从深色背景里浮出来
    p.setPen(QColor("#3A3632"))
    p.drawRect(QRectF(x - 1, y - 1, target_w + 2, target_h + 2))
    p.drawImage(QRectF(x, y, target_w, target_h), shot)

    # ---- 底部：一行卖点（会被裁掉，但完整看图时有用）----
    p.setPen(QColor("#9A9188"))
    f2 = QFont("Segoe UI")
    f2.setPixelSize(24)
    p.setFont(f2)
    p.drawText(QRectF(0, y + target_h + 18, W, 40), Qt.AlignHCenter | Qt.AlignVCenter,
               "Works with .pak / .utoc / .ucas   ·   Keeps original filenames"
               "   ·   Auto backup & restore   ·   Conflict detection")

    p.end()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(OUT), "PNG")
    print(f"已生成 {OUT}（{W}×{H}）")
    print(f"  截图区域: {target_w}×{target_h} @ ({x},{y})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
