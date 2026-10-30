# -*- coding: utf-8 -*-
"""把一张方形图片转成程序图标（多尺寸 .ico，内嵌 PNG）。

用途：用户 2026-10-29 指定用《影之刃零》红月武士图作为管理器图标。
⚠️ 该图是游戏官方宣传素材 —— 用于个人分发存在版权风险，已在对话中提示过，
   最终由作者决定；本脚本只负责技术转换。

复用 tools/make_icon.py 的 ICO 打包方式（内嵌 PNG，Windows Vista+ 支持），
不依赖 Pillow。

用法：
    python tools/make_icon_from_image.py <图片路径> [输出 ico 路径]
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tools.make_icon import ICO_SIZES  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: make_icon_from_image.py <图片> [输出.ico]")
        return 2
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "assets" / "icon.ico"
    if not src.is_file():
        print(f"图片不存在：{src}")
        return 1

    app = QApplication.instance() or QApplication([])   # QImage 缩放需要 QGuiApplication
    orig = QImage(str(src))
    if orig.isNull():
        print("图片读不出来")
        return 1

    # 居中裁成正方形（用户给的图本来就是 1:1，这里只是兜底）
    w, h = orig.width(), orig.height()
    side = min(w, h)
    orig = orig.copy((w - side) // 2, (h - side) // 2, side, side)

    entries: list[tuple[int, bytes]] = []
    for size in ICO_SIZES:
        img = QImage(size, size, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(0, 0, orig.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        p.end()
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        entries.append((size, bytes(buf.data())))
        buf.close()

    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = len(header) + 16 * len(entries)
    directory = b""
    blob = b""
    for size, data in entries:
        dim = 0 if size >= 256 else size     # ICO 里 256 用 0 表示
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blob += data
        offset += len(data)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(header + directory + blob)
    print(f"已生成 {dst}（{dst.stat().st_size:,} B，尺寸 {ICO_SIZES}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
