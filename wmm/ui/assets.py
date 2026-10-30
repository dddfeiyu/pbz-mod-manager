# -*- coding: utf-8 -*-
"""图片与占位卡的绘制。

需求文档 3.6 要求「四档降级，不允许出现空白」。这一档（占位卡）就是
"不允许出现空白"的兜底实现：哪怕 mod 里一张图都没有，也要画出一张
**看起来是设计过的**卡片，而不是一个灰框或问号。

用户 2026-10-11 把这一档从「首字字母卡」升级成了「按类型画图标」：
原来那个大汉字（光/华/嘿/宫）跟 mod 内容毫无关系、配色还是按名字哈希的
随机色，纯装饰；现在画的是"这是什么"，颜色也按类别固定 —— 变成信息位。
详见 `make_placeholder_card` 与 `wmm/placeholder.py`。

关于 webp：实测真实 mod 的预览图以 webp 为主（webp 101 / png 150 / jpg 8）。
Qt 的 webp 解码在 `qtimageformats` 插件里，PyInstaller 打包时若漏掉
`qwebp.dll`，**开发机上一切正常、用户机上所有 webp 预览图全空白**
（正是需求文档 R-20 说的那类"本地全绿、用户全崩"）。
所以这里提供 `supported_image_formats()` 供启动自检用，见 tools/selfcheck.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImageReader,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from .. import theme


def supported_image_formats() -> list[str]:
    """当前 Qt 能解码的图片格式。启动自检用（重点看有没有 webp）。"""
    return sorted(bytes(f).decode("ascii") for f in QImageReader.supportedImageFormats())


def _rounded_path(rect: QRectF, radius: float, corners: str = "all") -> QPainterPath:
    """构造圆角矩形路径。corners 取 "all" / "top" / "bottom"。"""
    path = QPainterPath()
    if radius <= 0:
        path.addRect(rect)
        return path

    r = min(radius, rect.width() / 2, rect.height() / 2)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    tl = tr = bl = br = r
    if corners == "top":
        bl = br = 0
    elif corners == "bottom":
        tl = tr = 0

    path.moveTo(x + tl, y)
    path.lineTo(x + w - tr, y)
    if tr:
        path.arcTo(x + w - 2 * tr, y, 2 * tr, 2 * tr, 90, -90)
    path.lineTo(x + w, y + h - br)
    if br:
        path.arcTo(x + w - 2 * br, y + h - 2 * br, 2 * br, 2 * br, 0, -90)
    path.lineTo(x + bl, y + h)
    if bl:
        path.arcTo(x, y + h - 2 * bl, 2 * bl, 2 * bl, 270, -90)
    path.lineTo(x, y + tl)
    if tl:
        path.arcTo(x, y, 2 * tl, 2 * tl, 180, -90)
    path.closeSubpath()
    return path


def _pt(r: QRectF, x: float, y: float) -> QPointF:
    """把 0~1 的归一化坐标映射到矩形里。图标一律按归一化画，尺寸无关。"""
    return QPointF(r.left() + x * r.width(), r.top() + y * r.height())


def _poly(p: QPainter, r: QRectF, pts, color: str, w: float) -> None:
    path = QPainterPath()
    for i, (x, y) in enumerate(pts):
        (path.moveTo if i == 0 else path.lineTo)(_pt(r, x, y))
    path.closeSubpath()
    p.setPen(QPen(QColor(color), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def _stroke(p: QPainter, r: QRectF, pts, color: str, w: float) -> None:
    """不闭合的折线。"""
    path = QPainterPath()
    for i, (x, y) in enumerate(pts):
        (path.moveTo if i == 0 else path.lineTo)(_pt(r, x, y))
    p.setPen(QPen(QColor(color), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def draw_kind_icon(p: QPainter, r: QRectF, kind: str, color: str) -> None:
    """在 `r` 里画一个代表 `kind` 的简笔图标。

    ⚠️ 全部用归一化坐标 + 描边（除少数实心点缀），这样同一份代码在
       缩略图尺寸和放大的尺寸下都成立，也不依赖任何字体或图片资源。
    """
    w = max(2.0, r.width() * 0.055)
    solid = QColor(color)
    soft = QColor(color)
    soft.setAlphaF(0.45)

    if kind == "Chest":                       # 上衣：T 恤
        _poly(p, r, [
            (0.32, 0.17), (0.11, 0.31), (0.22, 0.47), (0.30, 0.38),
            (0.30, 0.88), (0.70, 0.88), (0.70, 0.38), (0.78, 0.47),
            (0.89, 0.31), (0.68, 0.17), (0.58, 0.27), (0.42, 0.27),
        ], color, w)
    elif kind == "Legs":                      # 裤子 / 裙
        _poly(p, r, [(0.36, 0.18), (0.64, 0.18), (0.83, 0.84), (0.17, 0.84)], color, w)
        _stroke(p, r, [(0.33, 0.31), (0.67, 0.31)], color, w)
    elif kind == "Feet":                      # 鞋靴
        _poly(p, r, [(0.34, 0.16), (0.59, 0.16), (0.59, 0.58),
                     (0.84, 0.58), (0.84, 0.82), (0.34, 0.82)], color, w)
    elif kind == "Hair":                      # 发型：三缕发丝
        for x0 in (0.30, 0.50, 0.70):
            path = QPainterPath(_pt(r, x0, 0.18))
            path.cubicTo(_pt(r, x0 + 0.09, 0.38), _pt(r, x0 - 0.09, 0.56),
                         _pt(r, x0, 0.84))
            p.setPen(QPen(QColor(color), w, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
    elif kind == "Helm":                      # 头饰：头冠
        _poly(p, r, [(0.19, 0.74), (0.19, 0.42), (0.30, 0.56), (0.40, 0.32),
                     (0.50, 0.56), (0.60, 0.32), (0.70, 0.56), (0.81, 0.42),
                     (0.81, 0.74)], color, w)
    elif kind == "Arms":                      # 护手：手套
        p.setPen(QPen(QColor(color), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        body = QRectF(_pt(r, 0.34, 0.17), _pt(r, 0.66, 0.86))
        p.drawRoundedRect(body, body.width() * 0.35, body.width() * 0.35)
        for x0 in (0.44, 0.52, 0.60):
            p.drawLine(_pt(r, x0, 0.29), _pt(r, x0, 0.45))
    elif kind == "Face":                      # 面部：面具
        p.setPen(QPen(QColor(color), w, Qt.SolidLine))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(_pt(r, 0.23, 0.20), _pt(r, 0.77, 0.82)))
        p.setPen(Qt.NoPen)
        p.setBrush(solid)
        for x0 in (0.38, 0.62):
            p.drawEllipse(QRectF(_pt(r, x0 - 0.045, 0.47), _pt(r, x0 + 0.045, 0.53)))
    elif kind in ("Body", "Full"):            # 身形 / 整套：人形
        p.setPen(QPen(QColor(color), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(_pt(r, 0.41, 0.12), _pt(r, 0.59, 0.30)))
        _poly(p, r, [(0.33, 0.36), (0.67, 0.36), (0.76, 0.88), (0.24, 0.88)], color, w)
    elif kind == "weapon":                    # 武器：一柄长刀（用户点名要"画个刀"）
        # 刀身：一个实心三角形，刀背与刃口在右上角收成刀尖。
        # ⚠️ 第一版画的是"描边细四边形"，缩略图尺寸下细得像支笔 —— 改实心才认得出。
        blade = QPainterPath()
        blade.moveTo(_pt(r, 0.28, 0.72))      # 刀镡（护手）上端
        blade.lineTo(_pt(r, 0.95, 0.17))      # 刀尖
        blade.lineTo(_pt(r, 0.42, 0.88))      # 刀镡下端
        blade.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(solid)
        p.drawPath(blade)
        # 刀镡：横过刀身的粗杠，两端各伸出一截才像"护手"
        _stroke(p, r, [(0.23, 0.67), (0.47, 0.93)], color, w * 2.0)
        # 刀柄：沿刀身反方向伸出的短柄
        _stroke(p, r, [(0.16, 0.97), (0.35, 0.80)], color, w * 2.4)
    elif kind == "skill":                     # 技能：一颗带尾迹的大水球
        # 尾迹：一段渐细的弧（用两笔一粗一细叠出"越来越细"的观感）
        arc = QPainterPath(_pt(r, 0.10, 0.90))
        arc.quadTo(_pt(r, 0.14, 0.58), _pt(r, 0.40, 0.52))
        p.setPen(QPen(soft, w * 1.5, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawPath(arc)
        # 尾迹上的两颗水珠
        p.setPen(Qt.NoPen)
        p.setBrush(soft)
        p.drawEllipse(QRectF(_pt(r, 0.14, 0.68), _pt(r, 0.23, 0.77)))
        p.drawEllipse(QRectF(_pt(r, 0.26, 0.55), _pt(r, 0.33, 0.62)))
        # 水球本体
        ball = QRectF(_pt(r, 0.46, 0.20), _pt(r, 0.84, 0.58))
        p.setBrush(solid)
        p.drawEllipse(ball)
        # 高光：让它看着是"球"而不是"点"
        hl = QColor(theme.TEXT_ON_PRIMARY)
        hl.setAlphaF(0.7)
        p.setBrush(hl)
        p.drawEllipse(QRectF(_pt(r, 0.53, 0.28), _pt(r, 0.63, 0.38)))
        # 溅射：两小段放射线
        _stroke(p, r, [(0.87, 0.18), (0.94, 0.11)], color, w * 0.9)
        _stroke(p, r, [(0.90, 0.60), (0.97, 0.64)], color, w * 0.9)
    elif kind == "ui":                        # 界面：一块面板
        p.setPen(QPen(QColor(color), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        panel = QRectF(_pt(r, 0.16, 0.22), _pt(r, 0.84, 0.78))
        p.drawRoundedRect(panel, r.width() * 0.06, r.width() * 0.06)
        p.setPen(Qt.NoPen)
        p.setBrush(solid)
        bar = QRectF(_pt(r, 0.16, 0.22), _pt(r, 0.84, 0.36))
        p.drawRoundedRect(bar, r.width() * 0.05, r.width() * 0.05)
        p.setPen(QPen(soft, w * 0.9, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(_pt(r, 0.26, 0.50), _pt(r, 0.74, 0.50))
        p.drawLine(_pt(r, 0.26, 0.63), _pt(r, 0.60, 0.63))
    else:                                     # 认不出来：一张"没有图"的纸
        p.setPen(QPen(QColor(color), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        _poly(p, r, [(0.24, 0.14), (0.62, 0.14), (0.78, 0.32),
                     (0.78, 0.86), (0.24, 0.86)], color, w)
        _stroke(p, r, [(0.62, 0.14), (0.62, 0.32), (0.78, 0.32)], color, w)
        p.setPen(QPen(soft, w * 0.9, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(_pt(r, 0.35, 0.52), _pt(r, 0.67, 0.52))
        p.drawLine(_pt(r, 0.35, 0.65), _pt(r, 0.58, 0.65))


def make_placeholder_card(
    kind: str,
    label: str,
    width: int,
    height: int,
    *,
    radius: float = 0,
    corners: str = "all",
    dpr: float = 1.0,
    color: Optional[str] = None,
) -> QPixmap:
    """无预览图时的**占位图标卡**（用户 2026-10-11 定稿）。

    与 `make_letter_card` 的区别：那个画的是"首字 + 随机色"（信息量为零），
    这个画的是"**这是什么**"的图标 + **按类别固定的颜色** —— 占位图从装饰
    变成了信息位。用户库里 83 个 mod 里 68 个没预览图，一屏扫过去能直接
    看出"这堆是换上衣的、那几个是整套"。

    ⚠️ 同样遵守"不允许空白"：任何 `kind` 都有兜底图标，任何异常都退化成
       中性的「其他」卡，绝不留下空框。
    """
    from .. import placeholder as placeholder_mod

    if not color:
        color = theme.PLACEHOLDER_COLORS.get(
            kind, theme.PLACEHOLDER_FALLBACK_COLOR
        )

    px_w, px_h = max(1, int(round(width * dpr))), max(1, int(round(height * dpr)))
    pixmap = QPixmap(px_w, px_h)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    rect = QRectF(0, 0, px_w, px_h)
    painter.setPen(Qt.NoPen)
    # 底色 = 类别色的极淡版。比纯白"有设计感"，又不抢图标。
    tint = QColor(color)
    tint.setAlphaF(0.075)
    painter.setBrush(tint)
    painter.drawPath(_rounded_path(rect, radius * dpr, corners))

    # 图标画在偏上的位置，下面留一行给类别文字
    side = min(px_w * 0.52, px_h * 0.62)
    icon = QRectF(0, 0, side, side)
    icon.moveCenter(QPointF(px_w / 2.0, px_h * 0.46))
    try:
        draw_kind_icon(painter, icon, kind, color)
    except Exception:  # noqa: BLE001 - 占位图绝不能让整个界面挂掉
        draw_kind_icon(painter, icon, placeholder_mod.KIND_OTHER, color)

    font = QFont(theme.FONT_FAMILY.split(",")[0].strip())
    font.setPixelSize(max(9, int(px_h * 0.115)))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(
        QRectF(0, px_h - px_h * 0.26, px_w, px_h * 0.20),
        Qt.AlignCenter,
        label,
    )

    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def load_image(
    path: str | Path | None,
    width: int,
    height: int,
    *,
    radius: float = 0,
    corners: str = "all",
    dpr: float = 1.0,
    background: str = theme.BG_SUNKEN,
    fit: str = "smart",
) -> Optional[QPixmap]:
    """按目标尺寸缩放并裁圆角。读不出来返回 None（由调用方降级）。

    **必须能返回 None**：用户可能手删了预览图文件、或图片损坏、或（R-20）
    用户机缺 webp 解码器。这三种情况都不能崩，而要退到字母卡。

    参数
    ----
    fit
        - `"crop"`：居中裁剪填满（缩略图常规做法，会切掉四周）。
        - `"contain"`：整张缩进去、四周留底色，**一个像素都不切**。
        - `"smart"`（默认）：比取景框**更宽**的图用 contain，其余用 crop。

    ⚠️ 为什么要多加 `"smart"`（2026-10-10 用户反馈后补的）：
    真实 mod 的"预览图"里有大量**游戏内菜单截图 / 横版说明图**
    （例：`9d847f…png` 是一张 800×390 的「Wuchang Costume Loader」菜单截图）。
    对这类横图做居中裁剪会把左右两边的字直接切掉，用户的评价是
    「识别的图片不对，没有识别到主体内容」。而竖版人像照继续用裁剪填充
    （那是缩略图常规画法，硬留黑边反而更难看）。
    """
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None

    reader = QImageReader(str(p))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return None

    target_w, target_h = int(width * dpr), int(height * dpr)
    if target_w <= 0 or target_h <= 0:
        return None

    box_aspect = target_w / float(target_h)
    img_aspect = image.width() / float(max(1, image.height()))
    contain = fit == "contain" or (fit == "smart" and img_aspect > box_aspect)

    if contain:
        scaled = image.scaled(
            target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        ox = (target_w - scaled.width()) // 2      # 居中，四周露底色
        oy = (target_h - scaled.height()) // 2
    else:
        # 预缩放到目标尺寸的 105%，减少内存峰值（大截图直接全尺寸很吃内存）
        scaled = image.scaled(
            int(target_w * 1.05),
            int(target_h * 1.05),
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )

        # 防御：源图可能比目标还小（原图很小、或极端宽高比），那下面的裁剪会越界。
        # 先把它放大到至少覆盖目标尺寸。
        if scaled.width() < target_w or scaled.height() < target_h:
            scaled = scaled.scaled(
                max(target_w, scaled.width()),
                max(target_h, scaled.height()),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
        # 居中裁剪 = 把放大后的图按**负偏移**画，多出来的部分被 clip 吃掉
        ox = -(scaled.width() - target_w) // 2
        oy = -(scaled.height() - target_h) // 2

    canvas = QPixmap(target_w, target_h)
    canvas.fill(QColor(background))
    # 同 make_letter_card：先按设备像素画完，最后再设 DPR（否则坐标会被二次缩放）

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    rect = QRectF(0, 0, target_w, target_h)
    painter.setClipPath(_rounded_path(rect, radius * dpr, corners))

    # ⚠️ 必须用 `drawImage` 而不是 `drawPixmap`：实测 PySide6 的
    # `drawPixmap(x, y, QImage, sx, sy, sw, sh)` 重载会抛
    # `ValueError: called with wrong argument values`（绑定层拒绝 QImage 实参），
    # 而 `drawImage` 的同签名重载正常。
    painter.drawImage(ox, oy, scaled)
    painter.end()
    canvas.setDevicePixelRatio(dpr)
    return canvas


def elide(text: str, font: QFont, width: int, mode: Qt.TextElideMode = Qt.ElideRight) -> str:
    metrics = QFontMetrics(font)
    return metrics.elidedText(text, mode, width)
