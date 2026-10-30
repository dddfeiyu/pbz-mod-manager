# -*- coding: utf-8 -*-
"""生成**自制**程序图标（需求文档 F-11.5 图标 + F-11.22 禁用游戏官方素材）。

图形完全由代码画出来，不含任何游戏 Logo / 宣传图 / 官方字体：
一个圆角方块 + 三层「叠起来的 pak 层」，中间那层用主色挑出来 ——
寓意「管理叠在一起的 mod 层」，在 16×16 下也能看清（16px 是任务栏和
文件管理器列表视图实际使用的尺寸，图标在那里糊掉等于没做图标）。

同时产出多尺寸 ICO（16/20/24/32/40/48/64/128/256）。
实现说明：ICO 直接**内嵌 PNG**（Windows Vista 起支持），
所以不需要 Pillow 之类的第三方库，也不用手写 BMP 调色板。
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: 需要放进 ICO 的尺寸。前几个是小尺寸（任务栏 / 列表视图），
#: 256 是资源管理器「大图标」与安装包展示用。
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _render(size: int, *, bg: str, layer: str, accent: str):
    """按指定像素尺寸画一张图（返回 QImage，尺寸就是 size×size 像素）。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)

    s = size / 256.0  # 以 256 为设计基准等比缩放

    # --- 圆角方块底 ---
    radius = 56 * s
    body = QRectF(0, 0, size, size)
    path = QPainterPath()
    path.addRoundedRect(body, radius, radius)
    p.fillPath(path, QColor(bg))

    # --- 三层「pak 层」：两条深色 + 一条主色，错开摆放表示「叠加」 ---
    bar_h = 26 * s
    gap = 16 * s
    top = 70 * s
    left_x = 58 * s
    widths = (140 * s, 108 * s, 124 * s)
    colors = (layer, accent, layer)

    for i, (w, c) in enumerate(zip(widths, colors)):
        y = top + i * (bar_h + gap)
        r = bar_h / 2
        bar = QRectF(left_x, y, w, bar_h)
        bp = QPainterPath()
        bp.addRoundedRect(bar, r, r)
        p.fillPath(bp, QColor(c))

    p.end()
    return img


def build_ico(target: Path) -> list[int]:
    """按 ICO_SIZES 渲染并打包成一个 .ico 文件，返回写入的尺寸列表。"""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    import wmm.theme as theme

    # 底色用中性深灰（不用纯黑，纯黑在小尺寸下边框发死）；
    # 主色用项目主色，和界面里的按钮同源，视觉上是一套东西。
    bg = "#2B2F33"
    layer = "#8B939C"
    accent = theme.PRIMARY

    entries: list[tuple[int, bytes]] = []
    for size in ICO_SIZES:
        img = _render(size, bg=bg, layer=layer, accent=accent)
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        data = bytes(buf.data())
        buf.close()
        entries.append((size, data))

    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = len(header) + 16 * len(entries)
    directory = b""
    blob = b""
    for size, data in entries:
        dim = 0 if size >= 256 else size      # ICO 里 256 用 0 表示
        directory += struct.pack(
            "<BBBBHHII",
            dim, dim, 0, 0, 1, 32, len(data), offset,
        )
        blob += data
        offset += len(data)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(header + directory + blob)
    return [s for s, _ in entries]


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)  # noqa: F841  QPainter/QImage 需要先有 QApplication

    root = Path(__file__).resolve().parent.parent
    ico = root / "assets" / "icon.ico"
    sizes = build_ico(ico)
    print(f"图标已生成：{ico}  ({ico.stat().st_size:,} B)")
    print(f"包含尺寸：{', '.join(str(s) for s in sizes)}")

    preview = root / "docs" / "icon-preview.png"
    preview.parent.mkdir(parents=True, exist_ok=True)
    from wmm import theme as _t

    _render(256, bg="#2B2F33", layer="#8B939C", accent=_t.PRIMARY).save(str(preview))
    print(f"预览图：{preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
