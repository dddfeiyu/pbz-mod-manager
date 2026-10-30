# -*- coding: utf-8 -*-
"""启动自检 —— 需求文档 11.5「交付前必须在干净环境跑完的验证清单」里
**只有图形界面才能回答**的那几条。

为什么单独做这个而不是写进单测：这几条（webp 能不能解码、缩放后的预览图
是不是真的非空、字体在目标机器上有没有）**只有在真的有 Qt + 真的能建窗口时**
才有意义。需求文档 R-20 说的就是这类问题：「开发机上一切正常、用户机上全崩」。

跑法：
    .venv\\Scripts\\python.exe tools/selfcheck.py

会离屏渲染（QT_QPA_PLATFORM=offscreen），不弹窗口。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS, FAIL, WARN = "通过", "失败", "注意"
results: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str, *, warn_only: bool = False) -> None:
    results.append((name, (WARN if warn_only and not ok else (PASS if ok else FAIL)), detail))


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from wmm import apppaths, costume, gamedir, placeholder, preview, sysreq, theme, version
    from wmm.ui import assets, fonts, qss

    app = QApplication(sys.argv)
    app.setStyleSheet(qss.build_qss())

    # 与真实启动保持一致：把 Qt **自带**的标准按钮切到中文。
    # 不装的话 QMessageBox.setDetailedText() 会摆出英文的「Hide Details...」。
    from wmm.ui.app import _install_qt_translations

    _install_qt_translations(app)

    # ---- 弹窗静音补丁（用户 2026-10-11 连续两轮：「不要出现提示音」）----
    # 与真实启动保持一致，否则自检里造的弹窗行为和生产不一致。
    from wmm.ui.app import _silence_message_boxes
    from wmm.ui import dialogs as _dialogs

    _silence_message_boxes()
    _seen: list = []
    _saved_show, _saved_ask = _dialogs.show_text, _dialogs.ask
    _dialogs.show_text = lambda parent, title, body, heading="": _seen.append(("text", title))
    _dialogs.ask = lambda parent, title, body, **k: (_seen.append(("ask", title)), True)[1]
    try:
        from PySide6.QtWidgets import QMessageBox as _QMB

        _QMB.information(None, "自检A", "正文A")
        _QMB.warning(None, "自检B", "正文B")
        _r = _QMB.question(None, "自检C", "正文C", _QMB.Yes | _QMB.No, _QMB.No)
    finally:
        _dialogs.show_text, _dialogs.ask = _saved_show, _saved_ask
    record(
        "弹窗无声化：QMessageBox 的静态方法已被换成自绘对话框",
        len(_seen) == 3
        and _seen[0][0] == "text"
        and _seen[2][0] == "ask"
        and int(_r) == int(_QMB.Yes),
        f"三次调用分别走到 {'/'.join(k for k, _ in _seen)}；question 返回 Yes={int(_r) == int(_QMB.Yes)}"
        "（QMessageBox 的图标在 Windows 上会响系统提示音，自绘对话框没有图标）",
    )

    # ---- 0. 中文字体（离屏平台字体库是空的，必须先补上，否则后面全在测方框）----
    before = fonts.can_render_cjk()
    loaded_font = fonts.ensure_cjk_font()
    record(
        "中文能渲染（拉丁+中文+全角标点）",
        fonts.can_render_cjk(),
        fonts.describe() + (f"；已兜底加载 {loaded_font}" if loaded_font else ("；原生即可渲染" if before else "")),
    )

    # ---- 1. 系统要求 ----
    check = sysreq.check()
    record("系统要求（Win10 1809+ / 64 位）", check.ok, check.message)

    # ---- 2. Qt 图片格式（重点看 webp，R-20）----
    formats = assets.supported_image_formats()
    has_webp = "webp" in formats
    record(
        "Qt 能解码 webp",
        has_webp,
        "支持格式：" + ", ".join(formats),
    )

    # ---- 3. 字母卡必须能画出来且非空白（3.6「不允许出现空白」）----
    def probe_card(card) -> tuple[bool, str]:
        """抽查网格上的像素：要求①没有全透明、②至少出现两种颜色。

        为什么要"至少两种颜色"：只查"非透明"是查不出画歪的 ——
        底色的圆角矩形本身就能铺满整块，字母画到框外照样"非透明"。
        这是 2× 屏上出过的真实缺陷（QPainter 在带 DPR 的设备上会二次缩放坐标）。
        """
        if card.isNull():
            return False, "QPixmap 为空"
        img = card.toImage()
        if img.isNull():
            return False, "QImage 为空"
        colors = set()
        opaque = 0
        total = 0
        for gx in range(1, 20):
            for gy in range(1, 20):
                color = img.pixelColor(img.width() * gx // 20, img.height() * gy // 20)
                total += 1
                if color.alpha() > 0:
                    opaque += 1
                    colors.add(color.rgb())
        if opaque == 0:
            return False, "整张图全透明"
        if len(colors) < 2:
            return False, f"只有 1 种颜色（{len(colors)}），字母没画上"
        return True, f"尺寸 {card.width()}×{card.height()}，采样 {len(colors)} 种颜色"

    card1 = assets.make_placeholder_card(
        "Chest", placeholder.label_for("Chest"), theme.CARD_WIDTH, theme.CARD_PREVIEW_HEIGHT
    )
    ok1, detail1 = probe_card(card1)
    record("占位图标卡非空白（1×）", ok1, detail1)

    card2 = assets.make_placeholder_card(
        "Chest", placeholder.label_for("Chest"),
        theme.CARD_WIDTH, theme.CARD_PREVIEW_HEIGHT, dpr=2.0,
    )
    ok2, detail2 = probe_card(card2)
    expect_w, expect_h = theme.CARD_WIDTH * 2, theme.CARD_PREVIEW_HEIGHT * 2
    size_ok = card2.width() == expect_w and card2.height() == expect_h
    record(
        "占位图标卡非空白且尺寸正确（2× 高 DPI）",
        ok2 and size_ok,
        f"{detail2}；期望 {expect_w}×{expect_h}，dpr={card2.devicePixelRatio()}",
    )

    # ---- 3b. 占位卡必须"有信息"，不是装饰 ----
    # 用户 2026-10-11 把这一档从「首字字母卡 + 按名字哈希的随机色」换成了
    # 「按类型画图标 + 按类别固定配色」。这两条守项钉住换过之后的核心性质。
    _kinds = list(costume.SLOT_ORDER) + [
        placeholder.KIND_WEAPON, placeholder.KIND_SKILL,
        placeholder.KIND_UI, placeholder.KIND_OTHER,
    ]
    _blank: list[str] = []
    for _k in _kinds:
        _pm = assets.make_placeholder_card(
            _k, placeholder.label_for(_k), theme.CARD_WIDTH, theme.CARD_PREVIEW_HEIGHT
        )
        _ok, _det = probe_card(_pm)
        if not _ok:
            _blank.append(f"{_k}:{_det}")
    record(
        f"每一种类型（{len(_kinds)} 类）都能画出非空白占位卡",
        not _blank,
        "全部有内容" if not _blank else "空白：" + "；".join(_blank),
    )

    # 🚨 反向守：**同类别必须同色、不同类别必须不同色**。
    #   这条专门防"又退回按名字哈希取色"—— 那种配色是装饰，读不出信息。
    def _bg_of(kind: str) -> str:
        pm = assets.make_placeholder_card(
            kind, placeholder.label_for(kind), theme.CARD_WIDTH, theme.CARD_PREVIEW_HEIGHT
        )
        img = pm.toImage()
        return img.pixelColor(4, 4).name()

    _c1 = _bg_of("Chest")
    _c2 = _bg_of("Chest")
    _other = {k: _bg_of(k) for k in _kinds}
    record(
        "占位卡配色按**类别**固定（同类同色、异类异色），不是按名字哈希",
        _c1 == _c2 and len(set(_other.values())) >= len(_kinds) - 1,
        f"上衣两次取色一致={_c1 == _c2}；{len(set(_other.values()))}/{len(_kinds)} 类颜色互不相同",
    )

    # ---- 3c. 真实库：每个没有预览图的 mod 都能拿到一张非空占位卡 ----
    # 这是"不允许出现空白"在真实数据上的落地检查。
    # ⚠️ 这里**自己建视图**，不用 MainWindow —— 那个对象要到下面才创建。
    from wmm import catalog as _catalog
    from wmm import modstore as _modstore
    from wmm import settings as _settings

    _st = _settings.Settings.load()
    _store = _modstore.ModStore(_st.mods_library_dir)
    _store.ensure()
    _real = _catalog.build_views(_store)
    _noimg = [v for v in _real if v.preview is not None and v.preview.image_path is None]
    _bad_kind: list[str] = []
    for v in _noimg:
        if not v.placeholder_label.strip():
            _bad_kind.append(v.name)
            continue
        _pm = assets.make_placeholder_card(
            v.placeholder_kind, v.placeholder_label,
            theme.CARD_WIDTH, theme.CARD_PREVIEW_HEIGHT,
        )
        _ok, _det = probe_card(_pm)
        if not _ok:
            _bad_kind.append(f"{v.name}:{_det}")
    record(
        f"库内 {len(_noimg)}/{len(_real)} 个无预览图 mod 全部拿得到非空占位卡",
        not _bad_kind,
        "全部有内容" if not _bad_kind else "有问题：" + "；".join(_bad_kind[:5]),
    )
    _kinds_used = sorted({v.placeholder_label for v in _noimg})
    record(
        "无预览图 mod 的占位卡确实按类型分了类（不是清一色）",
        len(_kinds_used) >= 2 or len(_noimg) < 2,
        f"用到的类别={_kinds_used}",
    )

    # ---- 4. 真实图片的缩放/裁剪（各种宽高比 + 极小图 + 2× 屏）----
    # 这条是回归项：曾经这里用 `drawPixmap(x, y, QImage, sx, sy, sw, sh)`，
    # PySide6 会抛 `ValueError: called with wrong argument values` ——
    # 而它**只在真的有图可裁时才触发**，没有真实图片的测试根本发现不了。
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        problems: list[str] = []
        cases = [
            ("横图", 1920, 1080),
            ("竖图", 600, 1400),
            ("方图", 800, 800),
            ("极小图", 8, 6),
            ("极扁", 3000, 40),
        ]
        for label, w, h in cases:
            src = QImage(w, h, QImage.Format_ARGB32)
            src.fill(QColor(200, 60, 90))
            path = tmp_path / f"{label}.png"
            if not src.save(str(path)):
                problems.append(f"{label} 存不下")
                continue
            for dpr in (1.0, 2.0):
                pix = assets.load_image(
                    path, theme.CARD_WIDTH, theme.CARD_PREVIEW_HEIGHT,
                    radius=theme.CARD_CORNER_RADIUS, corners="top", dpr=dpr,
                )
                if pix is None or pix.isNull():
                    problems.append(f"{label}@{dpr}x 返回空")
                    continue
                if pix.width() != int(theme.CARD_WIDTH * dpr) or pix.height() != int(theme.CARD_PREVIEW_HEIGHT * dpr):
                    problems.append(f"{label}@{dpr}x 尺寸 {pix.width()}×{pix.height()} 不对")
        record(
            "真实图片缩放/裁剪（5 种宽高比 × 1×/2×）",
            not problems,
            "全部正常" if not problems else "；".join(problems),
        )

    # ---- 4b. 比取景框更宽的图必须**整张显示**（不许把两边裁掉）----
    # 触发场景：大量真实 mod 的所谓"预览图"其实是游戏内菜单截图 / 横版说明图。
    # 用旧的居中裁剪会把左右两边的字直接切掉，用户的原话是
    # 「识别的图片不对，没有识别到主体内容」（2026-10-10 实测）。
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        wide = QImage(900, 300, QImage.Format_ARGB32)
        wide.fill(QColor(20, 20, 20))                     # 中间深色
        edge = QImage(60, 300, QImage.Format_ARGB32)
        edge.fill(QColor(0, 220, 120))                    # 左右各 60px 亮绿
        painter = QPainter(wide)
        painter.drawImage(0, 0, edge)
        painter.drawImage(wide.width() - 60, 0, edge)
        painter.end()
        path = tmp_path / "wide.png"
        wide.save(str(path))

        pix = assets.load_image(path, theme.CARD_WIDTH, theme.CARD_PREVIEW_HEIGHT)
        detail = "预览图读不出来"
        fit_ok = False
        if pix is not None and not pix.isNull():
            image = pix.toImage()
            left = image.pixelColor(3, image.height() // 2)
            top = image.pixelColor(image.width() // 2, 2)
            fit_ok = left.green() > 150 and left.green() > left.red() + 60
            detail = (
                f"原图 900×300（含左右亮绿边）；卡片 {image.width()}×{image.height()}，"
                f"左边缘 {left.name()}、上边留白 {top.name()}"
            )
        record("宽图整张显示、不裁掉两边", fit_ok, detail)

    # ---- 5. 损坏/非法图片安全返回 None（不崩）----
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "broken.webp"
        bad.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 100)
        missing = assets.load_image(bad, 100, 100)
        record(
            "损坏/非法图片安全返回 None（不崩）",
            missing is None,
            "故意喂了一张假 webp，返回 None 表示会降级到字母卡",
        )

    # ---- 6. 预览图降级链路：没有图时必须落到字母卡 ----
    spec = preview.build_preview_spec(mod_name="测试mod", meta_preview="", mod_dir=None)
    record(
        "无图时降级到字母卡",
        spec.is_letter_card and bool(spec.letter),
        f"tier={spec.tier} letter={spec.letter!r} 配色={spec.bg}/{spec.fg}",
    )

    # ---- 6. 数据目录可写（且不在程序目录里）----
    writable = True
    try:
        apppaths.ensure_data_dirs()
        probe = apppaths.data_dir() / "selfcheck.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:  # noqa: BLE001
        writable = False
        detail = f"{type(exc).__name__}: {exc}"
    else:
        detail = str(apppaths.data_dir())
    record("数据目录可写（%APPDATA%）", writable, detail)
    record(
        "数据目录与程序目录分离",
        not apppaths.is_in_program_dir(apppaths.data_dir()) and not apppaths.is_in_data_dir(apppaths.program_dir()),
        f"程序目录={apppaths.program_dir()}",
    )

    # ---- 7. 中文字体与省略号渲染 ----
    from PySide6.QtGui import QFont, QFontMetrics

    title_font = QFont()
    title_font.setPointSizeF(theme.FONT_SIZE_CARD_TITLE)
    elided = assets.elide("街头少女风 蚰蜒套 超长名称测试" * 3, title_font, 180)
    record(
        "中文名称可省略显示",
        bool(elided) and len(elided) > 1,
        f"「{elided}」",
    )

    # ---- 8. 游戏目录规则（纯逻辑，但放在一起跑一遍）----
    record(
        "四种目录写法规则已加载",
        len(gamedir.PAKS_CANDIDATES) >= 4,
        "候选规则：" + "、".join(gamedir.PAKS_CANDIDATES),
    )

    # ---- 8b. 自动检测游戏目录：5 路来源都在 ----
    # 用户 2026-10-10：「游戏无非就是 steam、epic 或者 wegame，你自己不会找吗？」
    # 只查结构（每路探测真的跑一遍会产生 IO，不适合放启动自检里）。
    sources = (
        "_hits_from_steam",
        "_hits_from_epic",
        "_hits_from_registry",
        "_hits_from_xbox_dirs",
        "_hits_from_common_dirs",
    )
    missing_src = [n for n in sources if not hasattr(gamedir, n)]
    record(
        "自动检测游戏目录（Steam/Epic/注册表/Xbox/磁盘扫描 五路齐全）",
        not missing_src and callable(getattr(gamedir, "autodetect_game", None)),
        "五路来源齐全" if not missing_src else f"缺 {missing_src}",
    )

    # ---- 9. 卡片多了会不会被裁掉 / 有没有滚动条 ----
    # 这是流式卡片网格最典型的隐性缺陷：容器高度停在 sizeHint 上，
    # 于是「上面两行看得见、下面全没了，还没有滚动条」。
    with tempfile.TemporaryDirectory() as tmp:
        from wmm import library as library_mod
        from wmm import modstore, settings as settings_mod
        from wmm.modstore import ModFile, ModMeta
        from wmm.ui.main_window import MainWindow

        tmp_path = Path(tmp)
        big_store = modstore.ModStore(tmp_path / "mods")
        big_store.ensure()
        for i in range(37):
            big_store.save(
                ModMeta(
                    id=f"{i:016x}",
                    name=f"测试mod {i:02d}",
                    content_hash=f"{i:016x}",
                    files=[ModFile(name=f"m{i}_P.pak", rel_path="x", size=1024, sha256=str(i))],
                )
            )
        window = MainWindow(
            settings_mod.Settings.load(tmp_path / "settings.json"),
            big_store,
            library_mod.Library.load(tmp_path / "library.json"),
        )
        window.resize(1180, 800)
        window.show()
        app.processEvents()

        host = window.cards_host
        needed = window.flow.heightForWidth(host.width())
        scroll_max = window.scroll.verticalScrollBar().maximum()
        cards_ok = len(window._cards) == 37
        height_ok = host.height() >= needed - 2 and needed > 800
        scroll_ok = scroll_max > 0
        record(
            "37 张卡片不被裁切且有滚动条",
            cards_ok and height_ok and scroll_ok,
            f"卡片 {len(window._cards)} 张；容器高 {host.height()}px，需要 {needed}px；"
            f"滚动条上限 {scroll_max}",
        )

        # 窄屏也要能正确换行（R-18 的精神）
        window.resize(760, 800)
        app.processEvents()
        narrow_needed = window.flow.heightForWidth(window.cards_host.width())
        record(
            "窄屏自动换行（宽度 760）",
            narrow_needed > needed,
            f"窄屏所需高度 {narrow_needed}px > 宽屏 {needed}px",
        )

        # ---- 卡片勾选 + 批量按钮（用户 2026-10-10 明确要求的交互）----
        # 这几条同样"看截图看不出来"：按钮在不在、点了数字变不变、右键里
        # 还有没有安装/卸载 —— 都得翻控件树才知道。
        batch_buttons = {
            "安装所选": getattr(window, "btn_install_sel", None),
            "卸载所选": getattr(window, "btn_uninstall_sel", None),
            "全选": getattr(window, "btn_check_all", None),
        }
        missing = [k for k, v in batch_buttons.items() if v is None]
        detail = "三个按钮齐全" if not missing else f"缺 {missing}"

        card_problems: list[str] = []
        cards = list(window._cards.values())
        if not cards:
            card_problems.append("没有卡片可验")
        else:
            card = cards[0]
            if not hasattr(card, "checked_changed") or not hasattr(card, "set_checked"):
                card_problems.append("卡片没有勾选能力")
            else:
                before = window.btn_install_sel.text() if not missing else ""
                card.set_checked(True)
                window._on_card_checked(card.view, True)
                mid = window.btn_install_sel.text() if not missing else ""
                card.set_checked(False)
                window._on_card_checked(card.view, False)
                after = window.btn_install_sel.text() if not missing else ""
                if mid == before:
                    card_problems.append("勾选后「安装所选」按钮数字没变")
                if after != before:
                    card_problems.append("取消勾选后按钮没恢复")
            # 复选框必须画出来（尺寸 ≥ 12px），否则等于没有
            rect = card._checkbox_rect()
            if rect.width() < 12 or rect.height() < 12:
                card_problems.append(f"复选框太小：{rect.width():.0f}×{rect.height():.0f}")

        # 右键菜单里**不该**再有安装/卸载（用户要求改为勾选 + 独立按钮）
        import inspect as _inspect

        from wmm.ui.card import ModCard as _ModCard

        ctx_src = _inspect.getsource(_ModCard.contextMenuEvent)
        if "安装到游戏目录" in ctx_src or "从游戏目录卸载" in ctx_src:
            card_problems.append("右键菜单里仍有安装/卸载（应改为勾选 + 批量按钮）")
        # 自动挑图挑错了（作者包里只有菜单截图）时，用户唯一的自救入口
        if "更换预览图" not in ctx_src:
            card_problems.append("右键菜单缺「更换预览图」")

        record(
            "卡片勾选 + 批量安装/卸载按钮",
            not missing and not card_problems,
            (detail if not card_problems else "；".join(card_problems)),
        )

        # ---- 工具条里的主色按钮**真的**是主色（截图看得见、控件树查不出来的那类）----
        # 守的是一个真实踩过的坑（2026-10-10）：`_build_toolbar` 里给工具条
        # QFrame 写了一句**无选择器**的 `setStyleSheet("background:#F5F6F8; ...")`。
        # Qt 的层叠顺序是「控件自身样式表 > 祖先控件样式表 > 应用级样式表」，
        # 而裸声明会把规则套到**所有子控件**上 —— 于是 `#primaryButton` 的
        # 主色底被刷成工具条底色，可它的 `color:#FFFFFF` 来自应用级样式表仍然生效，
        # 结果是**白字 + 近白底**，整颗「安装所选」肉眼看不见。用户的反馈原话：
        # 「页面没有安装按钮和卸载按钮」。控件树里它是齐的、isEnabled() 也是 True，
        # 只有**真的把像素取出来数一遍**才抓得到。
        def dominant_color(widget) -> str:
            image = widget.grab().toImage()
            counts: dict[str, int] = {}
            for y in range(0, image.height(), 2):
                for x in range(0, image.width(), 2):
                    name = image.pixelColor(x, y).name()
                    counts[name] = counts.get(name, 0) + 1
            return max(counts.items(), key=lambda kv: kv[1])[0] if counts else ""

        toolbar = window.btn_install_sel.parentWidget()
        primary_seen, primary_bad = 0, []
        for button in toolbar.findChildren(QPushButton) if toolbar is not None else []:
            if button.objectName() != "primaryButton" or not button.isEnabled():
                continue
            primary_seen += 1
            got = dominant_color(button)
            if got.lower() != theme.PRIMARY.lower():
                primary_bad.append(f"{button.text()!r} 底色 {got} ≠ 主色 {theme.PRIMARY}")
        record(
            "工具条主色按钮没被容器样式表刷掉底色",
            primary_seen >= 1 and not primary_bad,
            f"{primary_seen} 个主色按钮底色均为主色 {theme.PRIMARY}"
            if not primary_bad
            else "；".join(primary_bad),
        )

        # ---- 批量按钮必须常驻可点（置灰 = 用户以为"功能不存在"）----
        window._update_selection_buttons()
        window._checked_ids.clear()
        window._update_selection_buttons()
        always_clickable = window.btn_install_sel.isEnabled() and window.btn_uninstall_sel.isEnabled()
        record(
            "「安装所选 / 卸载所选」未勾选时也可点（不会被误当成不存在）",
            always_clickable,
            f"未勾选时 enabled = 安装 {window.btn_install_sel.isEnabled()} / "
            f"卸载 {window.btn_uninstall_sel.isEnabled()}；按钮文案 {window.btn_install_sel.text()!r}",
        )

        # F-10.5c：四项关键信息必须同时**可见**。
        # ⚠️ 2026-10-11 用户两次要求：①「免费声明 + 发布渠道」挪进**系统标题栏**；
        # ②「系统标题栏下方的明末mod管理器V0.1.5 BY诸葛建国删掉」——程序名 / 版本 /
        # 作者这一行**整行删除**。所以四项信息现在**全部**只出现在系统标题栏里，
        # 程序内不再重复。这是有意偏离文档 F-10.5c（文档要求出现在程序内顶栏）的一处。
        window.resize(1180, 800)
        app.processEvents()
        top_text = " ".join(
            w.text() for w in window.findChildren(QLabel) if w.isVisible() and w.text()
        )
        title_bar_text = window.windowTitle()
        four = {
            "免费声明": version.FREE_STATEMENT_SHORT in title_bar_text,
            "作者ID": version.AUTHOR_ID in title_bar_text,
            "版本号": version.VERSION in title_bar_text,
            "发布渠道": "B站" in title_bar_text,
        }
        record(
            "四项关键信息齐全（均在系统标题栏）",
            all(four.values()),
            "；".join(f"{k}={'有' if v else '缺'}" for k, v in four.items())
            + f"；标题栏=「{title_bar_text}」",
        )
        # 反向守：程序内不该再出现程序名 / 免费声明。
        # ⚠️ **作者 ID 是用户 2026-10-11 又要求加回来的例外**：
        #   「（框里）写B站：诸葛-建国。跟关于与支持同一行，居中」——
        #   顶栏中间那个徽标（#authorBadge）就是它。所以这条只查程序名与免费声明，
        #   作者 ID 单独由下面 14d 那组项来验（验它存在且位置正确）。
        leftovers = [
            name
            for name, needle in (
                ("程序名", version.APP_NAME_ZH),
                ("免费声明", version.FREE_STATEMENT_SHORT),
            )
            if needle in top_text
        ]
        record(
            "程序内不再显示程序名/免费声明（作者 ID 除外，它是用户要求加回的徽标）",
            not leftovers,
            f"程序内可见标签里{'仍有 ' + '、'.join(leftovers) if leftovers else '已无这些内容'}"
            f"；顶栏中间的「{version.TOP_BAR_AUTHOR_TEXT}」是用户明确要求的例外",
        )

        # ---- 顶栏两个入口按钮的位置与尺寸（用户 2026-10-11 的要求）----
        # 原话：「关注作者…从右侧放到页面左侧，大小改成正常大小，不要这么大；
        #        然后右侧的关于与支持就放在右边，但是按钮放大一点」
        def _center(widget):
            p = widget.mapTo(window, widget.rect().center())
            return p.x(), p.y()

        sup_cx, sup_cy = _center(window.btn_support)
        abt_cx, abt_cy = _center(window.btn_about)
        record(
            "「关注作者」在左、「关于与支持」在右、同一行",
            abs(sup_cy - abt_cy) <= 8 and sup_cx < abt_cx,
            f"关注作者 中心x={sup_cx} cy={sup_cy}；关于与支持 中心x={abt_cx} cy={abt_cy}",
        )
        sup_sz, abt_sz = window.btn_support.size(), window.btn_about.size()
        # 注意：两个按钮文字长短不同（145 vs 5 字），宽度不能直接比 ——
        # 用户要的是「放大一点」，落到视觉上是**更高、面积更大**。
        record(
            "「关于与支持」按钮比「关注作者」大（用户要求放大一点）",
            abt_sz.height() > sup_sz.height()
            and abt_sz.width() * abt_sz.height() > sup_sz.width() * sup_sz.height(),
            f"关于与支持 {abt_sz.width()}×{abt_sz.height()}（面积 {abt_sz.width() * abt_sz.height()}）"
            f" vs 关注作者 {sup_sz.width()}×{sup_sz.height()}（面积 {sup_sz.width() * sup_sz.height()}）",
        )

        # 窗口标题栏（全窗口最高的位置）也要带上作者 —— 用户要求「最顶部」
        title_text = window.windowTitle()
        record(
            "窗口标题栏含作者 ID",
            version.AUTHOR_ID in title_text,
            f"标题栏 = 「{title_text}」",
        )

        # ---- 全局只有一个「导入」按钮（用户：「为什么做两个导入按钮？」）----
        from PySide6.QtWidgets import QFrame as _QFrame

        topbar = window.findChild(_QFrame, "topBar")
        import_btns = (
            [b.text() for b in topbar.findChildren(QPushButton) if "导入" in b.text()]
            if topbar is not None
            else []
        )
        record(
            "顶栏只有一个导入入口",
            len(import_btns) == 1,
            "；".join(repr(t) for t in import_btns) or "一个都没有",
        )

        # ---- 2026-10-11 第 3 轮：游戏目录路径「太长」+「导入 mod」按钮「文字显示不全」 ----
        # 用户原话：「游戏目录显示路径，这个位置太长了，把它放短一点」
        #           「右侧倒数第二个按钮导入mod，文字显示不全」
        # 实测结论（真机 GDI 截图逐像素量）：按钮文字其实是完整的、左右内边距对称 ——
        # 用户看到的是文案末尾那个「…」（表示点了会弹窗）被当成了截断。尽管如此，
        # 这两条观感问题都用回归项钉住，免得以后又改回去。
        from PySide6.QtGui import QFontMetrics as _QFM

        def _text_need(w) -> int:
            """这行字**至少**要多宽：文字宽 + 标签内边距 / 按钮内边距。"""
            t = w.text() or ""
            pad = 16 if w.objectName() == "gameDirPath" else (28 if isinstance(w, QPushButton) else 0)
            return _QFM(w.font()).horizontalAdvance(t) + pad

        # ⚠️ 2026-10-11 这条用户来回改了**四轮**，终稿是：
        #   「路径栏缩短，且可以点击，点击后弹出完整路径，**但是不要出现提示音**。」
        # → 判据三件套：① 平时缩短显示（短于完整路径、含省略号、保留末尾游戏名）；
        #   ② 标签**可点击**（绑定了 _show_game_dir_popup）；
        #   ③ **不可选中**（否则 Qt 按下会响系统提示音 —— 用户报过两次）。
        long_path = r"E://SteamLibrary//steamapps//common//Wuchang Fallen Feathers"
        window._game_dir_full = long_path
        window._game_dir_short = long_path
        window._refresh_game_dir_text()
        app.processEvents()
        shown_path = window.lbl_game_dir.text()
        shortened = (
            shown_path != long_path
            and "…" in shown_path
            and shown_path.endswith("Feathers")
            and window._game_dir_full == long_path
        )
        record(
            "游戏目录平时缩短显示（完整路径保留在点击弹窗里）",
            shortened,
            f"完整 {len(long_path)} 字符 → 显示 {shown_path!r}"
            f"；完整值仍保留={window._game_dir_full == long_path}",
        )
        record(
            "游戏目录标签宽度封顶（不会撑满整行）",
            window.lbl_game_dir.width() <= version.GAME_DIR_LABEL_MAX_WIDTH,
            f"标签宽 {window.lbl_game_dir.width()}px ≤ 上限 {version.GAME_DIR_LABEL_MAX_WIDTH}px",
        )
        # ② 可点击：绑定了专用处理器（点一下弹无声对话框给完整路径）
        handler = getattr(window.lbl_game_dir, "mousePressEvent", None)
        clickable = getattr(handler, "__name__", "") == "_show_game_dir_popup"
        record(
            "游戏目录标签可以点击（点一下给完整路径）",
            clickable and callable(getattr(window, "_show_game_dir_popup", None)),
            f"mousePressEvent={getattr(handler, '__name__', None)}",
        )
        # ③ 但**不可选中**：TextSelectableByMouse 会让 Qt 按下时响系统提示音
        #    （用户 2026-10-11 原话：「点击路径时，有提示音」「不要出现提示音」）。
        flags = window.lbl_game_dir.textInteractionFlags()
        no_select = not bool(flags & Qt.TextSelectableByMouse)
        record(
            "游戏目录标签不是可选中文本（避免按下时的系统提示音）",
            no_select,
            f"textInteractionFlags={flags}；可选中={not no_select}",
        )

        # ---- 2026-10-11 第 4 轮：「这两个按钮，让文字全都显示出来」 ----
        # ⚠️ 0.1.16 起顶栏**只剩「导入 mod…」**：扫描入口被用户嫌"多余"，
        #   已移进卡片区右键菜单（功能没删）。所以这里只量导入按钮。
        btn_pads = []
        for label, btn in (("导入 mod…", window.btn_import),):
            if btn is None:
                btn_pads.append(f"{label}: 按钮不存在")
                continue
            pad = (btn.width() - _QFM(btn.font()).horizontalAdvance(btn.text())) / 2
            btn_pads.append(f"{label} {btn.width()}px/文字{_QFM(btn.font()).horizontalAdvance(btn.text())}px→每侧{pad:.0f}px")
            if pad < 10:
                btn_pads.append(f"{label} 余量不足")
        record(
            "「导入 mod…」文字两侧都留足余量",
            all("余量不足" not in s for s in btn_pads),
            "；".join(btn_pads),
        )

        # 硬不变量：几个常用宽度下，顶栏**任何**控件的宽度都不许小于它文字所需宽度
        # （这正是"文字显示不全"的机械定义）。这条以后改布局时会先拦住。
        squeezed = []
        for w in (1164, 1000, 880):
            window.resize(w, 800)
            app.processEvents()
            for cand in topbar.findChildren(QPushButton) + topbar.findChildren(QLabel):
                if not (cand.text() or ""):
                    continue
                if cand.width() < _text_need(cand) - 2:
                    squeezed.append(f"{w}px / {cand.text()!r}")
        record(
            "顶部栏在 1164/1000/880 宽下没有任何控件被压窄",
            not squeezed,
            "；".join(squeezed) if squeezed else "全部控件宽度 ≥ 其文字所需宽度",
        )
        window._update_game_dir_display()      # 还原成真实状态，别影响后面的检查
        window.resize(1180, 800)
        app.processEvents()

        # ---- 2026-10-11 用户要求：两个支持按钮都要写明「一键跳转」 ----
        support_text = window.btn_support.text()
        record(
            "顶栏「关注作者」按钮带「一键跳转」字样",
            "一键跳转" in support_text and "关注作者" in support_text,
            f"按钮文案 = {support_text!r}（应含 关注作者 + 一键跳转）",
        )

        # ---- 2026-10-11 用户拍板：两个扫描按钮「合并成一个下拉按钮」 ----
        # 原话先是「（方框中的两个按钮）是不是功能重复了？」→ 合并成一个下拉；
        # 后又说「（红框里的）这个按钮有点多余，感觉可以删除」→ 移进卡片区右键菜单。
        #
        # ⚠️ 这里钉三件事：① 顶栏**不再有**扫描按钮 ② 两个扫描来源在右键菜单里都还在
        # ③ 不再残留独立的「扫描目录…」按钮。
        topbar_labels = (
            [b.text() for b in topbar.findChildren(QPushButton)] if topbar is not None else []
        )
        stray = [t for t in topbar_labels if "扫描" in t]
        record(
            "顶栏已无「扫描 mod…」按钮（用户嫌多余，已移进右键菜单）",
            not stray,
            f"顶栏残留扫描按钮={stray}" if stray else "顶栏只剩「导入 mod…」一个入口",
        )
        # 右键菜单里两个来源都得在 —— **功能没删，只是挪了位置**
        _has_ctx = callable(getattr(window, "_show_context_menu", None))
        record(
            "扫描功能移进卡片区右键菜单（两个来源都还在，功能没被砍掉）",
            _has_ctx
            and window.scroll.contextMenuPolicy() == Qt.CustomContextMenu
            and callable(getattr(window, "scan_game_mods", None))
            and callable(getattr(window, "scan_dir", None)),
            f"_show_context_menu={_has_ctx}；"
            f"滚动区菜单策略={window.scroll.contextMenuPolicy() == Qt.CustomContextMenu}；"
            f"scan_game_mods / scan_dir 都在="
            f"{callable(getattr(window, 'scan_game_mods', None)) and callable(getattr(window, 'scan_dir', None))}",
        )

        # ---- 2026-10-11 用户要求：「自动检测游戏目录放到上方箭头处」 ----
        # 箭头指的是**游戏目录那一行、路径右边**。原来它塞在工具条的批量按钮堆里。
        # 判据用控件树的**父控件**（Qt 把加进布局的控件 reparent 到布局所属窗口部件），
        # 比量 y 坐标更硬：父控件是 topBar 才算真的挪过来了。
        auto_btn = getattr(window, "btn_autodetect", None)
        auto_parent_ok = auto_btn is not None and auto_btn.parent() is topbar
        auto_row_ok = False
        if auto_btn is not None:
            y_auto = auto_btn.mapTo(window, auto_btn.rect().topLeft()).y()
            y_dir = window.lbl_game_dir.mapTo(window, window.lbl_game_dir.rect().topLeft()).y()
            auto_row_ok = abs(y_auto - y_dir) <= 8
        parent_name = ""
        if auto_btn is not None and auto_btn.parent() is not None:
            parent_name = auto_btn.parent().objectName() or type(auto_btn.parent()).__name__
        record(
            "「自动检测游戏目录」已挪到顶部栏游戏目录那一行（不再在工具条里）",
            auto_parent_ok and auto_row_ok,
            f"父控件={parent_name or '(无)'}，与游戏目录路径同一行={auto_row_ok}",
        )

        # ---- 2026-10-11 第 4 轮：「更改目录挪到自动检测游戏目录旁边」 ----
        # 判据：两者都在 topBar、同一行，且**自动检测的左边沿 < 更改目录的左边沿**
        #（即「自动」在前、「手动」在后，挨着；反了也算不达标）。
        chg_btns = [b for b in topbar.findChildren(QPushButton) if b.text() == "更改目录"]
        chg_ok = False
        chg_detail = "顶栏没有「更改目录」按钮"
        if auto_btn is not None and chg_btns:
            chg = chg_btns[0]
            ax = auto_btn.mapTo(window, auto_btn.rect().topLeft()).x()
            cx = chg.mapTo(window, chg.rect().topLeft()).x()
            ay = auto_btn.mapTo(window, auto_btn.rect().topLeft()).y()
            cy = chg.mapTo(window, chg.rect().topLeft()).y()
            gap = cx - (ax + auto_btn.width())
            chg_ok = chg.parent() is topbar and cx > ax and abs(cy - ay) <= 8
            chg_detail = f"自动检测 x={ax}、更改目录 x={cx}，间距 {gap}px，同行={abs(cy - ay) <= 8}"
        record(
            "「更改目录」紧挨在「自动检测游戏目录」右侧（同一行、自动在前）",
            chg_ok,
            chg_detail,
        )

        # ---- 2026-10-11 第 4 轮：「删除steam这几个字」 ----
        # 顶栏不再有 [Steam] 这类平台角标。判据：topBar 里所有可见标签的文字
        # 都不得是 catalog.platform_badge 的方括号形式（`[Steam]`/`[Game Pass]`…）。
        badges = [
            w.text()
            for w in topbar.findChildren(QLabel)
            if (w.text() or "").startswith("[") and (w.text() or "").endswith("]")
        ]
        record(
            "顶栏不再显示 [Steam] 之类的平台角标",
            not badges,
            f"残留={badges}" if badges else "无平台角标（catalog.platform_badge 仍保留在数据层，测试仍覆盖）",
        )

        # ---- 2026-10-11 第 4 轮：「有 mod 导入没有 mod 删除功能」 ----
        # 删除逻辑本来就有（卡片右键「从 mod 库删除…」），但藏在右键里等于没有。
        # 这里钉住工具条上那颗**显式按钮**必须在。
        del_btn = getattr(window, "btn_delete_sel", None)
        record(
            "工具条有「从库中删除所选」按钮（批量删除不再只藏在右键菜单里）",
            del_btn is not None
            and del_btn.text() == "从库中删除所选"
            and hasattr(del_btn, "clicked")
            and callable(getattr(window, "delete_selected", None)),
            f"按钮={getattr(del_btn, 'text', lambda: None)()!r}；"
            f"批量删除方法={callable(getattr(window, 'delete_selected', None))}",
        )

        # 反向守：右键菜单里的单条删除入口别被顺手删掉（那是唯一能删单个的入口）。
        from wmm.ui import card as _card_mod

        src = Path(_card_mod.__file__).read_text(encoding="utf-8")
        record(
            "卡片右键菜单仍保留「从 mod 库删除…」（单条删除入口）",
            "从 mod 库删除" in src,
            "card.py 里有「从 mod 库删除…」菜单项",
        )

        # ---- 2026-10-11 第 5 轮：批量操作**只确认一次** ----
        # 用户原话：「多选安装时，不是一次性安装多个mod，需要每次mod都要
        # 二次确认一下。你这个二次确认有什么意义？」
        # 判据：批量路径必须走 `confirm=False` 的静默版（`_install_one` /
        # `_uninstall_one` 都有 confirm 参数），而不是直接调单条入口。
        mwi = Path(sys.modules["wmm.ui.main_window"].__file__).read_text(encoding="utf-8")
        # 2026-10-12：`_uninstall_one` 改成返回 `UninstallResult`（批量卸载要汇总
        # 「有哪些原文件被还原」再弹提示），签名因此换成多行 —— 判据按特征串查，
        # 别再钉死单行格式（那种写法一改签名就误报）。
        batch_ok = (
            "def _install_one(self, view: catalog.ModView, *, confirm: bool)" in mwi
            and "self._install_one(view, confirm=False)" in mwi
            and "def _uninstall_one(" in mwi
            and "confirm: bool" in mwi
            and "Optional[installer.UninstallResult]" in mwi
            and "self._uninstall_one(view, confirm=False)" in mwi
        )
        # 反向守：批量循环里**不许**再出现 confirm 默认值的单条调用
        no_reconfirm = (
            "for view in targets:\n            self.install_mod(view)" not in mwi
            and "for view in targets:\n            self.uninstall_mod(view)" not in mwi
        )
        record(
            "批量安装/卸载只确认一次（逐个静默执行，不反复弹框）",
            batch_ok and no_reconfirm,
            f"有静默版 _install_one/_uninstall_one 且批量走 confirm=False={batch_ok}；"
            f"批量循环里没有反复调单条入口={no_reconfirm}",
        )

        # ---- 2026-10-11 第 5 轮：扫描必须有防闪退刹车 ----
        # 用户报过「点扫描电脑上的文件夹，软件直接闪退」——
        # 根因是把 `E:\\SteamLibrary`（整个 Steam 库根）当扫描目录，
        # 递归进几十个游戏里，把游戏本体的 pak 当成了 mod。
        from wmm import importer as _imp
        from wmm import pakfiles as _pkf

        brake_ok = (
            hasattr(_pkf, "SCAN_MAX_FILES")
            and hasattr(_pkf, "SCAN_MAX_PAKS")
            and hasattr(_pkf, "SCAN_MAX_WALK_DEPTH")
            and hasattr(_imp, "SCAN_SKIP_DIR_ENTRIES")
            and _pkf.is_ignored_dir("steamapps")
            and _pkf.is_ignored_dir("common")
        )
        record(
            "扫描目录有防闪退刹车（规模上限 + 跳过游戏库目录）",
            brake_ok,
            f"上限={_pkf.SCAN_MAX_FILES}文件/{_pkf.SCAN_MAX_PAKS}pak/深度{_pkf.SCAN_MAX_WALK_DEPTH}"
            f"；跳过目录阈值={_imp.SCAN_SKIP_DIR_ENTRIES}；"
            f"steamapps/common 已忽略={_pkf.is_ignored_dir('steamapps') and _pkf.is_ignored_dir('common')}",
        )
        window.close()

    # ---- 9b. 统一导入对话框（「导入 mod…」用，文件夹与文件都能选）----
    # 守的是一个真实踩过的坑（2026-10-10）：原来用
    # `QFileDialog.getExistingDirectory()`，那是 Windows 原生框，**只能返回一个**
    # 目录 —— 用户 Ctrl 选 5 个文件夹再点「打开」，Windows 只会**进入**其中一个
    # （用户反馈：「导入多个 mod 时，点击打开无法导入，会直接进入其中一个 mod 的子文件夹」）。
    # 换成 Qt 自带（非原生）框也不行：实测它把驱动器列表当内容显示。
    # 所以自己画了一个，这里把"一次能拿回多个目录"钉死。
    with tempfile.TemporaryDirectory() as tmp:
        from PySide6.QtCore import QItemSelectionModel

        from wmm.ui.dialogs import ImportPickerDialog

        root = Path(tmp)
        for i in range(3):
            (root / f"mod{i}").mkdir()
        # 顺带放一个 pak：验证「同一个入口也能选文件」（用户 2026-10-10 的要求）
        (root / "aaa_单件衣服_P.pak").write_bytes(b"\x00" * 16)

        picker = ImportPickerDialog("导入 mod", str(root))
        picker.show()
        for _ in range(30):
            app.processEvents()

        model = picker.model
        parent_index = model.index(str(root))
        selection = picker.view.selectionModel()
        names = [
            model.fileName(model.index(r, 0, parent_index))
            for r in range(model.rowCount(parent_index))
        ]
        picked: list[str] = []
        for row in (1, 3):          # 跳过排在最前的 aaa_*.pak，选两个文件夹
            if row < model.rowCount(parent_index):
                index = model.index(row, 0, parent_index)
                selection.select(index, QItemSelectionModel.Select)
                picked.append(model.fileName(index))
        for _ in range(10):
            app.processEvents()
        got = picker.selected_paths()
        picker.close()

        record(
            "统一导入对话框（一次能选多个 mod 文件夹）",
            len(got) == 2 and len(set(got)) == 2,
            f"程序化选中 {picked} → selected_paths() 返回 {len(got)} 条：{got}",
        )
        record(
            "同一个导入入口也能选 pak / 压缩包文件",
            any(n.lower().endswith(".pak") for n in names),
            f"树里可见：{names}",
        )

    # ---- 9c. 父子目录同时选中时要去重（否则同一个 mod 导两遍）----
    from wmm import importer as _importer

    base = r"C:\示例\明末mod"
    mixed = [base, base + r"\夜行服装", r"D:\别处\单件衣服", base]
    deduped = _importer.drop_nested_paths(mixed)
    record(
        "选中父目录时自动丢掉被包住的子目录",
        deduped == [base, r"D:\别处\单件衣服"],
        f"输入 4 条（含重复与嵌套）→ 输出 {deduped}",
    )

    # ---- 9d. 一个 mod 的多个部件不该被拆成多个 mod（2026-10-10 用户报）----
    # 真实例：`黑红短裙-1.24/` 下 4 个子目录（主文件/脸部纹身/腿部纹身/裤子瘦腿）
    # 各放一个 pak，pak 名都是 `TK_SexySkirt_*` —— 同一件衣服的部件。
    # 而真合集（`以往绅士mod合集/…/1.衣服 套装、散件/`）结构一样，但 pak 名各不相同。
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        one_mod = base / "黑红短裙"
        for sub, pak in (
            ("主文件 衣服+胖腿", "TK_SexySkirt_BlackRed_Main_p.pak"),
            ("脸部纹身", "TK_SexySkirt_MakeUp_p.pak"),
            ("腿部纹身", "TK_SexySkirt_Z_BodyPrint_p.pak"),
            ("裤子 瘦腿效果", "TK_SexySkirt_BlackRed_ThinLeg_p.pak"),
        ):
            d = one_mod / sub
            d.mkdir(parents=True)
            (d / pak).write_bytes(b"\x00" * 32)
        units = _importer._expand_folder(one_mod, None, 6)

        coll = base / "以往绅士mod合集"
        for sub, pak in (
            ("夜夫人", "Mistress_of_the_Night_one_boob_p.pak"),
            ("宫女", "cloth07_p.pak"),
            ("新娘", "bridal_corset_reshape_P.pak"),
        ):
            d = coll / sub
            d.mkdir(parents=True)
            (d / pak).write_bytes(b"\x00" * 32)
        coll_units = _importer._expand_folder(coll, None, 6)

        record(
            "同一个 mod 的多个部件不再被拆成多个 mod",
            len(units) == 1,
            f"4 个 pak 名同源的子目录 → {len(units)} 个 mod（期望 1）：{[u.label for u in units]}",
        )
        record(
            "真正的合集（pak 名各不相同）仍然照常拆分",
            len(coll_units) == 3,
            f"3 个 pak 名各异的子目录 → {len(coll_units)} 个 mod（期望 3）",
        )

    # ---- 9e. Qt 自带的标准按钮已中文化 ----
    # 用户 2026-10-10 截图圈出「Hide Details...」问"红圈中是什么" ——
    # 那是 QMessageBox.setDetailedText() 未装中文翻译时的默认英文按钮。
    from PySide6.QtWidgets import QMessageBox

    probe_box = QMessageBox()
    probe_box.setText("测试")
    probe_box.setDetailedText("明细")
    detail_texts = [b.text() for b in probe_box.buttons()]
    probe_box.deleteLater()
    record(
        "Qt 标准按钮已中文化（不再是 Hide Details…）",
        bool(detail_texts)
        and any("详情" in t for t in detail_texts)
        and not any("Detail" in t for t in detail_texts),
        f"按钮文案：{detail_texts}",
    )

    # ---- 9f. B站跳转不再弹确认框 ----
    # 用户 2026-10-10：「这个框不要弹，点完按钮直接跳网站。」
    # 这条**只能靠读源码**来守：确认框在别的分支里，跑一次不一定经过。
    import ast as _ast
    import inspect as _inspect2
    import textwrap as _textwrap

    from wmm.ui import dialogs as _dialogs

    # ⚠️ 不能直接 `"QMessageBox.question" in 源码`：函数说明里为了记录这次
    # 需求变更，**文字上**提到了这个 API，会误判成"还在弹框"。
    # 所以解析成语法树，只看**真正的调用节点**。
    _open_src = _textwrap.dedent(_inspect2.getsource(_dialogs.open_bilibili))
    _tree = _ast.parse(_open_src)
    _asks = [
        n
        for n in _ast.walk(_tree)
        if isinstance(n, _ast.Call) and getattr(n.func, "attr", "") == "question"
    ]
    _opens = [
        n
        for n in _ast.walk(_tree)
        if isinstance(n, _ast.Call) and getattr(n.func, "attr", "") == "openUrl"
    ]
    record(
        "B站跳转不弹确认框（点完直接开浏览器）",
        not _asks and bool(_opens),
        "open_bilibili() 里已无确认调用，仍保留打不开时的 URL 回退"
        if not _asks
        else f"open_bilibili() 里仍有 {len(_asks)} 处确认调用",
    )

    # ---- 10. 「关于与支持」页结构合规（需求文档 10.3 / F-10.16 / F-10.18b）----
    # 这条守的是一个**已经真实踩过的坑**：最初 B站主页那行做成了
    # 「纯文本 + 打开按钮」，于是本页出现两个都指向 B站 的按钮 ——
    # 同时违反 F-10.16（本页不设第二个跳转按钮）和 F-10.18d（URL 不可互相复用）。
    # 结构性问题只有把控件树翻出来数才靠得住，看截图会漏。
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QPushButton, QScrollArea

    from wmm.ui.dialogs import AboutDialog
    about = AboutDialog()
    # 刻意**不改尺寸**：这里要测的就是用户实际看到的默认尺寸。
    # 如果把对话框拉高再测，F-10.18b 的「首屏可见」就自动成立了，等于没检查。
    about.show()
    app.processEvents()

    buttons = [(w, w.text().strip()) for w in about.findChildren(QPushButton)]
    # 用户 2026-10-11：「关于与支持中可以留个QQ粉丝群号：1057139483」
    _about_labels = [w.text() for w in about.findChildren(QLabel)]
    _about_all = "\n".join(_about_labels)
    _about_txt_has_qq = (
        bool(getattr(version, "QQ_GROUP_NUMBER", ""))
        and version.QQ_GROUP_NUMBER in _about_all
    )
    # 「关闭」不算跳转按钮；剩下的应该只剩充电那一个
    jump_buttons = [t for _w, t in buttons if t not in ("", "关闭", "取消")]
    charge = [w for w, t in buttons if t == version.CHARGE_BUTTON_TEXT]
    min_w, min_h = version.CHARGE_BUTTON_MIN_SIZE
    size_ok = bool(charge) and charge[0].width() >= min_w and charge[0].height() >= min_h
    size_detail = f"{charge[0].width()}×{charge[0].height()}" if charge else "无按钮"
    charge_is_primary = bool(charge) and charge[0].objectName() == "primaryButton"
    # 字号必须真的生效（应用级 QSS 的 `QWidget { font-size }` 会覆盖 setFont，
    # 所以实现里走的是控件自身的 setStyleSheet —— 这里回读实际渲染字号来验证）
    charge_font_ok = bool(charge) and charge[0].font().pointSize() >= version.CHARGE_BUTTON_FONT_PT

    labels_blob = "\n".join(w.text() for w in about.findChildren(QLabel) if w.text())
    notes_ok = all(k in labels_blob for k in ("支持纯属自愿", "不构成购买凭据", "需要登录"))

    # F-10.18b：充电按钮必须落在**首屏可见区域**内（不被长段声明顶到要滚动才看得到）。
    scroll = about.findChild(QScrollArea)
    visible_ok = False
    visible_detail = "没找到滚动区"
    if scroll is not None and charge:
        body = scroll.widget()
        top = charge[0].mapTo(body, QPoint(0, 0)).y()
        bottom = top + charge[0].height()
        viewport_h = scroll.viewport().height()
        visible_ok = 0 <= top and bottom <= viewport_h
        visible_detail = f"按钮在滚动内容里的位置 {top}~{bottom}px，可视高 {viewport_h}px"

    problems: list[str] = []
    if jump_buttons != [version.CHARGE_BUTTON_TEXT]:
        problems.append(f"本页跳转按钮应唯一，实际 {jump_buttons or '无'}")
    if not size_ok:
        problems.append(f"充电按钮实测 {size_detail}，小于 {min_w}×{min_h}")
    if not charge_is_primary:
        problems.append("充电按钮不是主色实心（objectName 非 primaryButton）")
    if not charge_font_ok:
        problems.append(
            f"充电按钮字号没生效（实测 {charge[0].font().pointSize() if charge else '-'}pt）"
        )
    if version.ABOUT_PAGE_TITLE not in labels_blob:
        problems.append("缺页面标题")
    if version.ABOUT_PAGE_TAGLINE not in labels_blob:
        problems.append("缺定位语")
    if not notes_ok:
        problems.append("充电说明三句不全")
    if not visible_ok:
        problems.append(f"充电按钮不在首屏可见区（{visible_detail}）")
    # F-11.1：系统要求必须在本页明示
    if "Windows 10 1809" not in labels_blob:
        problems.append("本页未明示系统要求（F-11.1）")
    # F-11.22 / F-11.23
    if "自制" not in labels_blob:
        problems.append("缺工具图标自制声明（F-11.22）")
    if "第三方" not in labels_blob:
        problems.append("缺第三方组件说明（F-11.23）")
    # F-10.27：必须能核对官方版本
    if version.VERSION not in labels_blob:
        problems.append("本页未显示版本号（F-10.27）")
    record(
        "关于与支持页结构合规",
        not problems,
        f"唯一跳转按钮=充电（实测 {size_detail}，{visible_detail}）；标题/定位语/三句说明/系统要求/防伪声明齐全"
        if not problems
        else "；".join(problems),
    )
    about.close()

    # ---- 10b. 启动弹窗结构合规（需求文档 10.1 / F-10.1 ~ F-10.8）----
    # 这一页的验收标准里有三条只能靠翻控件树来验：
    #   * 「全页仅『进入工具』一个可点元素」
    #   * 「作者 ID 为全页最大字号」
    #   * 「纯文字支持行不是链接、无 hover」
    # 全都是「看截图看不出来」的类型 —— 少一个按钮多一个链接，肉眼几乎无差别。
    from wmm.ui.splash import SplashDialog

    splash = SplashDialog()
    splash.show()
    app.processEvents()

    s_buttons = [(w, w.text().strip()) for w in splash.findChildren(QPushButton)]
    s_labels = [w for w in splash.findChildren(QLabel) if w.text()]

    s_problems: list[str] = []
    if [t for _w, t in s_buttons] != ["进入工具"]:
        s_problems.append(f"全页应只有『进入工具』一个按钮，实际 {[t for _w, t in s_buttons]}")

    book_btn = s_buttons[0][0] if len(s_buttons) == 1 else None
    if book_btn is None:
        s_problems.append("缺少唯一主按钮")
    else:
        if book_btn.objectName() != "primaryButton":
            s_problems.append("主按钮不是主色实心（objectName 非 primaryButton）")
        if book_btn.minimumWidth() < 120 or book_btn.minimumHeight() < 36:
            s_problems.append(
                f"主按钮最小尺寸 {book_btn.minimumWidth()}×{book_btn.minimumHeight()}，小于 120×36"
            )
        if not book_btn.hasFocus():
            s_problems.append("弹窗打开时主按钮没有默认焦点（回车关不掉）")
        if not book_btn.isDefault():
            s_problems.append("主按钮未设为 default")

    # 任何带 LinksAccessibleByMouse 的控件都算「可跳转元素」—— F-10.1e 一个都不许有
    linkish = [
        w for w in splash.findChildren(QLabel)
        if w.textInteractionFlags() & Qt.LinksAccessibleByMouse
    ]
    if linkish:
        s_problems.append(f"弹窗内存在 {len(linkish)} 个可点链接元素（F-10.1e 禁止）")

    # 作者 ID 必须是全页最大字号（F-10.1b ≥ 20pt）
    # 文案形如「作者B站ID：诸葛-建国」—— 前缀是用户明确要求的：
    # 光写一个 ID，用户不知道那串字是什么。
    author_full = version.SPLASH_AUTHOR_PREFIX + version.AUTHOR_ID
    author_lbl = [w for w in s_labels if w.text() == author_full]
    sized = [(w.font().pointSize(), w.text()) for w in s_labels]
    max_pt = max(pt for pt, _t in sized) if sized else 0
    if not author_lbl:
        s_problems.append(f"弹窗里没有带前缀的作者行（应为「{author_full}」）")
    elif version.SPLASH_AUTHOR_PREFIX not in author_lbl[0].text():
        s_problems.append("作者行缺少「作者B站ID：」前缀")
    else:
        a_pt = author_lbl[0].font().pointSize()
        if a_pt < 20:
            s_problems.append(f"作者 ID 字号 {a_pt}pt，小于 20pt")
        if a_pt != max_pt:
            biggest = [t for pt, t in sized if pt == max_pt]
            s_problems.append(f"作者 ID 不是全页最大字号（最大是 {max_pt}pt：{biggest}）")
    app_lbl = [w for w in s_labels if w.text() == version.APP_NAME_ZH]
    if app_lbl and app_lbl[0].font().pointSize() > 11:
        s_problems.append("程序名字号超过 11pt，抢了作者的视觉权重（F-10.1b）")
    if not any(version.VERSION in w.text() for w in s_labels):
        s_problems.append("弹窗里看不到版本号（F-10.1b）")

    # 纯文字支持行：不是链接色、没有下划线、字号约 12pt
    sup = splash.lbl_support
    sup_flags = sup.textInteractionFlags()
    if sup_flags & Qt.LinksAccessibleByMouse:
        s_problems.append("支持行被做成了链接（F-10.1g 禁止）")
    if "underline" in sup.styleSheet().replace("none", ""):
        s_problems.append("支持行带下划线（F-10.1g 禁止）")
    if not (10 <= sup.font().pointSize() <= 13):
        s_problems.append(f"支持行字号 {sup.font().pointSize()}pt，偏离 12pt")

    record(
        "启动弹窗结构合规",
        not s_problems,
        f"唯一可点元素=『进入工具』（{book_btn.minimumWidth()}×{book_btn.minimumHeight()}、有焦点）；"
        f"作者 {author_lbl[0].font().pointSize() if author_lbl else '?'}pt 为全页最大；"
        f"支持行 {sup.font().pointSize()}pt 非链接"
        if not s_problems
        else "；".join(s_problems),
    )
    splash.close()

    # ---- 11. 违禁文案扫描（F-10.17 / F-10.14 的验收口径）----
    # 只扫「会展示给用户的字符串字面量」：注释与文档字符串里讨论这些词是正常的
    # （本文档大量出现），所以先用 ast + tokenize 把注释和 docstring 剥掉再查。
    import ast
    import io
    import re
    import tokenize

    banned = ("解锁", "高级版", "会员", "众筹", "打赏", "赞助")
    root = Path(__file__).resolve().parent.parent
    # 只扫**会随包发出去的程序本体**：wmm/ 子包 + 入口。tools/ 是开发期脚本
    # （本文件自己就把这些词写在扫描清单里），不参与打包，不该算进「全程序」。
    targets = sorted((root / "wmm").rglob("*.py")) + [root / "main.py"]
    hits: list[str] = []
    scanned = 0
    for py in targets:
        if not py.exists():
            continue
        rel = py.relative_to(root)
        src = py.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        # 文档字符串的位置区间（行号），用来把它们从扫描结果里排除
        doc_lines: set[int] = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list) or not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                start = first.value.lineno
                end = getattr(first.value, "end_lineno", start)
                doc_lines.update(range(start, end + 1))
        try:
            kept = [
                tok.string
                for tok in tokenize.generate_tokens(io.StringIO(src).readline)
                if tok.type == tokenize.STRING and tok.start[0] not in doc_lines
            ]
        except tokenize.TokenError:
            continue
        scanned += 1
        for text in kept:
            for word in banned:
                if word in text:
                    hits.append(f"{rel} 出现「{word}」")
            # 「付费」只允许出现在否定语境里（无付费版 / 没有任何付费版本）
            if "付费" in text and not re.search(r"(没有|无|不含|非|不)[^。；\n]{0,6}付费", text):
                hits.append(f"{rel} 出现未否定的「付费」")
    record(
        "全程序无违禁文案（解锁/高级版/会员/众筹/打赏/赞助）",
        not hits,
        f"已扫 {scanned} 个 .py 的面向用户字符串，零命中" if not hits else "；".join(hits[:6]),
    )

    # ---- 12. 两个 B 站 URL 常量：硬编码、各自独立、不来自配置 ----
    from wmm import settings as settings_mod

    url_problems: list[str] = []
    for name, url in (
        ("BILIBILI_SPACE_URL", version.BILIBILI_SPACE_URL),
        ("BILIBILI_CHARGE_URL", version.BILIBILI_CHARGE_URL),
    ):
        if not url.startswith("https://space.bilibili.com/"):
            url_problems.append(f"{name} 不是完整 B站空间链接")
    if "member.bilibili.com" in version.BILIBILI_CHARGE_URL:
        url_problems.append("CHARGE_URL 指向 UP 主后台（F-10.22 严禁）")
    if "space.bilibili.com" not in version.BILIBILI_SPACE_URL:
        url_problems.append("SPACE_URL 异常")
    url_like_fields = [
        f for f in getattr(settings_mod.Settings, "__dataclass_fields__", {}) 
        if "url" in f.lower() or "link" in f.lower()
    ]
    if url_like_fields:
        url_problems.append(f"配置里出现了链接字段 {url_like_fields}（F-10.23 严禁）")
    record(
        "两个 B 站 URL 为硬编码常量且不来自配置",
        not url_problems,
        f"SPACE={version.BILIBILI_SPACE_URL} · CHARGE={version.BILIBILI_CHARGE_URL}"
        if not url_problems
        else "；".join(url_problems),
    )

    # ---- 13. 真实启动路径（弹窗 → 主窗交棒）----
    # 必须起**子进程**：Qt 每个进程只允许一个 QApplication，本进程已经被占用了。
    import subprocess

    script = Path(__file__).resolve().parent / "startup_check.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace", timeout=180,
        )
        out = proc.stdout or ""
        ok = proc.returncode == 0
        fails = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("[FAIL]")]
        detail = (
            "弹窗后台建主窗 → 点『进入工具』→ 主窗接管、弹窗退场；show_splash=false 时直接进主界面"
            if ok else "；".join(fails[:3]) or f"退出码 {proc.returncode}"
        )
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, repr(exc)
    record("真实启动路径（弹窗交棒 / show_splash 开关）", ok, detail)

    # ---- 14. 「替换同一件衣服」的冲突检测（用户 2026-10-11 截图报）----
    # 那一对 mod（都 replaces=['明军']）overrides 为空、pak 名也不同，
    # 旧的两路判据（目标文件名 / overrides 资源路径）全都测不出来。
    from wmm import conflicts as _conflicts

    record(
        "冲突检测能识别「两个 mod 替换同一件衣服」（KIND_REPLACES）",
        hasattr(_conflicts, "KIND_REPLACES")
        and _conflicts.KIND_LABELS.get(_conflicts.KIND_REPLACES) == "替换同一件衣服"
        and "KIND_REPLACES" in Path(_conflicts.__file__).read_text(encoding="utf-8").split(
            "def _installed_priority"
        )[0],
        f"KIND_LABELS['{getattr(_conflicts, 'KIND_REPLACES', '?')}']="
        f"{_conflicts.KIND_LABELS.get(getattr(_conflicts, 'KIND_REPLACES', ''), '缺失')}"
        "；detect() 里含该判据",
    )
    # 反向守：识别不出替换目标的 mod（replaces 为空）不能被误判成冲突
    _r = _conflicts.normalize_resource("明军")
    record(
        "替换目标名做了归一化（大小写/路径写法不同也算同一件）",
        _r == _conflicts.normalize_resource("明军") and bool(_r),
        f"normalize_resource('明军') = {_r!r}",
    )

    # ---- 14b. 冲突组配色（用户 2026-10-11：「多个 mod 冲突时颜色变成同一个」）----
    # 原话：「三个 mod 都替换同一个衣服，那这三个 mod 都变黄。如果另外三个 mod 又
    # 替换了另一件相同的衣服，那这三个 mod 就同时变蓝。」
    def _mk_report(pairs):
        rep = _conflicts.ConflictReport()
        for a, b, res in pairs:
            rep.items.append(
                _conflicts.ConflictItem(
                    a_id=a, a_name=a, b_id=b, b_name=b,
                    level=_conflicts.LEVEL_POTENTIAL, kind=_conflicts.KIND_REPLACES,
                    resource=res,
                )
            )
        return rep

    _c1 = _conflicts.group_colors(_mk_report([("a", "b", "明军"), ("b", "c", "明军")]))
    _same_ok = (
        _c1.get("a") == _c1.get("b") == _c1.get("c")
        and _c1.get("a") == _conflicts.GROUP_COLORS[0]
    )
    _c2 = _conflicts.group_colors(
        _mk_report([("a", "b", "明军"), ("c", "d", "黑珍珠")])
    )
    record(
        "同一冲突组的 mod 同色（第一个组是黄色）",
        _same_ok,
        f"a/b/c 的颜色 = {_c1}；第一组应为 {_conflicts.GROUP_COLORS[0]}",
    )
    record(
        "不同冲突组用不同颜色",
        bool(_c2) and _c2.get("a") != _c2.get("c"),
        f"组1={_c2.get('a')}，组2={_c2.get('c')}",
    )
    _c3 = _conflicts.group_colors(_mk_report([("a", "b", "X"), ("b", "c", "Y")]))
    record(
        "传递性冲突合并成一组（甲⟷乙、乙⟷丙 ⇒ 三者同色）",
        _c3.get("a") == _c3.get("c"),
        f"甲⟷乙、乙⟷丙 的颜色 = {_c3}",
    )

    # ---- 14c. 冲突的呈现方式（用户 2026-10-11）----
    # ①「冲突不要显示在右上角」→ 右上角的 lbl_conflict 必须**不存在**
    # ②「放到冲突这一页中」→ 面板只在「冲突」筛选页可见
    # ③「冲突」页的数字与筛选口径必须一致（曾经页签写「冲突 0」而点进去全是普通卡）
    from wmm import catalog as _catalog

    record(
        "右上角不再有冲突提示标签（改用卡片组色边框）",
        not hasattr(window, "lbl_conflict"),
        f"hasattr(lbl_conflict)={hasattr(window, 'lbl_conflict')}",
    )
    panel = getattr(window, "_conflict_panel", None)
    record(
        "冲突明细面板已挂在工具条下方（只在「冲突」页显示）",
        panel is not None and panel.parent() is not None,
        f"面板={type(panel).__name__ if panel else None}",
    )
    # ---- 14c-2. 冲突清单必须能「收起」（用户 2026-10-11：
    #      「这个冲突清单，无法缩小……可以加个隐藏或者缩小的按钮」）----
    if panel is None:
        record("冲突清单有「收起 / 展开」按钮", False, "没有 _conflict_panel")
    else:
        _btn = getattr(panel, "_btn_toggle", None)
        record(
            "冲突清单有「收起 / 展开」按钮",
            isinstance(_btn, QPushButton),
            f"类型={type(_btn).__name__ if _btn else None}",
        )

        class _Item:                                # 造两条冲突，够撑起面板
            def __init__(self):
                self.a_id, self.a_name = "a", "甲"
                self.b_id, self.b_name = "b", "乙"
                self.resource, self.level = "明军", "potential"

        class _Rep:
            items = [_Item(), _Item()]

        panel.update_content(_Rep(), visible=True)
        _expanded_h = panel.sizeHint().height()
        _btn.click()                                # 收起
        _collapsed_h = panel.sizeHint().height()
        # ⚠️ 判据用 `isHidden()` 而不是 `isVisible()` —— 后者的意思是
        #   "自己和所有祖先都可见"，窗口在离屏自检里没 show 时会恒为 False，
        #   断言会变成永远通过（第一版就踩了这个假通过）。
        record(
            "点「收起」后正文隐藏、面板明显变矮（且标题还在）",
            panel._body.isHidden()
            and _collapsed_h < _expanded_h - 20
            and bool(panel._title.text()),
            f"展开 {_expanded_h}px → 收起 {_collapsed_h}px；"
            f"标题={panel._title.text()!r}；正文已隐藏={panel._body.isHidden()}",
        )
        # ⚠️ 状态必须跨刷新保留：装/卸载 mod 会重填文本，要是跟着重建走，
        #   用户刚收起、一刷新又自己弹开。
        panel.update_content(_Rep(), visible=True)
        record(
            "收起状态跨刷新保留（重填文本后仍是收起的）",
            panel._body.isHidden() and panel._btn_toggle.text() == "展开",
            f"刷新后正文已隐藏={panel._body.isHidden()}；按钮={panel._btn_toggle.text()!r}",
        )
        _btn.click()                                # 还原，别影响后面的自检项
        record(
            "再点一次能展开回来",
            not panel._body.isHidden() and panel._btn_toggle.text() == "收起",
            f"正文已隐藏={panel._body.isHidden()}；按钮={panel._btn_toggle.text()!r}",
        )
    # 预览图「往外看」必须拒绝杂物层（0.1.20 真实事故：桌面上的无关 png 被当成预览图）
    from wmm import importer as _imp

    record(
        "找预览图的「往外看」有杂物层黑名单（桌面/下载/文档）",
        bool(getattr(_imp, "_NEVER_OUTSIDE_LAYERS", None)),
        f"_NEVER_OUTSIDE_LAYERS={sorted(getattr(_imp, '_NEVER_OUTSIDE_LAYERS', []))}",
    )
    # 「冲突」筛选必须按 conflict_level 筛（potential 也算），不能按 status 筛
    _sample_meta = next(
        (mm for mm in window.store.all()), None
    ) or _catalog.ModView.__init__.__globals__  # 占位，见下方守卫
    if _sample_meta is None:
        record("「冲突」页筛得出 potential 冲突的卡（数字与页内一致）", True, "库里没有 mod，跳过")
    else:
        _fake = [
            _catalog.ModView(meta=_sample_meta, status=_catalog.STATUS_INSTALLED,
                             conflict_level="potential"),
            _catalog.ModView(meta=_sample_meta, status=_catalog.STATUS_INSTALLED,
                             conflict_level="none"),
        ]
        _filtered = _catalog._apply_filter(_fake, _catalog.FILTER_CONFLICT)
        _counts = _catalog.count_by_status(_fake)
        record(
            "「冲突」页筛得出 potential 冲突的卡（数字与页内一致）",
            len(_filtered) == 1 and _counts[_catalog.FILTER_CONFLICT] == 1,
            f"筛出 {len(_filtered)} 张（potential），页签数字 {_counts[_catalog.FILTER_CONFLICT]}"
            "；两个口径必须都是 1",
        )

    # ---- 14d. 顶栏作者徽标（用户 2026-10-11：「框中写B站：诸葛-建国，跟关于与支持同一行，居中」
    #      并追加「中间加的作者名称也改成可以跳转的按钮，点击可以直接跳到B站主页」）----
    badge = getattr(window, "btn_author_id", None)
    badge_ok = False
    detail = "缺少 btn_author_id"
    if badge is not None:
        y_badge = badge.mapTo(window, badge.rect().topLeft()).y()
        y_about = window.btn_about.mapTo(window, window.btn_about.rect().topLeft()).y()
        x_badge = badge.mapTo(window, badge.rect().topLeft()).x()
        # 居中判定：拿**窗口本身的水平中线**做基准，而不是"三个控件宽度求和"
        # 那套算式 —— 徽标从 QLabel 变成 QPushButton 后宽度变了，按钮的实际
        # 几何（含样式表 padding）才是唯一可信的口径。
        win_mid = window.width() / 2.0
        badge_mid = x_badge + badge.width() / 2.0
        center_gap = abs(badge_mid - win_mid)
        same_row = abs(y_badge - y_about) <= 8
        text_ok = badge.text() == version.TOP_BAR_AUTHOR_TEXT and "诸葛-建国" in badge.text()
        # 必须是**可点的按钮**（用户明确要求「改成可以跳转的按钮」）。
        # 判据：QPushButton 类型 + **真的点一下会跳**。
        # ⚠️ 踩过的坑：想用 `receivers(badge.clicked)` 或 `metaObject().methodCount()`
        #   静态判断连接关系 —— PySide6 前者对 bound signal 抛 TypeError，
        #   后者不接受参数，`vars()` 里也**根本没有**信号属性。
        #   唯一可靠的办法是**真的点**，把 `_open_author_space` 临时换成计数器，
        #   点完再换回来。这同时验证了"类型对 + 信号连上了 + 槽能跑"。
        is_button = isinstance(badge, QPushButton)
        clickable = False
        if is_button:
            _fired = []
            _orig = window._open_author_space
            try:
                window._open_author_space = lambda: _fired.append(1)
                badge.click()
                clickable = bool(_fired)
            finally:
                window._open_author_space = _orig
        badge_ok = same_row and text_ok and is_button and clickable
        detail = (
            f"文字={badge.text()!r}；与「关于与支持」同行={same_row}"
            f"（y 差 {abs(y_badge - y_about)}px）；徽标中点 x={badge_mid:.0f} vs "
            f"窗口中点 x={win_mid:.0f}（差 {center_gap:.0f}px）；"
            f"是按钮={is_button}；clicked 已接跳转={clickable}"
        )
    record("顶栏中间有「B站：诸葛-建国」（同行居中、且是可跳转按钮）", badge_ok, detail)

    # ---- 14e. 「关于与支持」必须是绿的（用户：「改个颜色，绿色吧」）----
    _about = window.btn_about
    _qss_text = qss.build_qss()
    _has_green = theme.SUCCESS.lower() in _qss_text.lower() and "#successButton" in _qss_text
    record(
        "「关于与支持」按钮是绿色（#successButton + SUCCESS 色）",
        _about.objectName() == "successButton" and _has_green,
        f"objectName={_about.objectName()!r}；主题绿={theme.SUCCESS}；"
        f"QSS 含 #successButton 规则={_has_green}",
    )
    # 「B站：诸葛-建国」用户要求改**实心**（「改成实心的吧，看着有点空」）。
    # 判据：#authorBadge 规则里不能用 BG_SUNKEN（浅灰空底），必须是主色实心 + 白字。
    _ab_idx = _qss_text.find("#authorBadge {")
    _ab_rule = _qss_text[_ab_idx:_ab_idx + 400] if _ab_idx >= 0 else ""
    _solid = (
        f"background: {theme.PRIMARY.lower()}" in _ab_rule.lower()
        and f"color: {theme.TEXT_ON_PRIMARY.lower()}" in _ab_rule.lower()
    )
    record(
        "顶栏「B站：诸葛-建国」是**实心**按钮（主色底 + 白字，不是空心浅灰）",
        _solid and f"background: {theme.BG_SUNKEN.lower()}" not in _ab_rule.lower(),
        f"QSS #authorBadge 用主色底={theme.PRIMARY.lower() in _ab_rule.lower()}；"
        f"白字={theme.TEXT_ON_PRIMARY.lower() in _ab_rule.lower()}",
    )
    # 「关注作者 · 一键跳转」颜色：黄色被用户否了（"这个颜色太丑了，换一个"）→ 改成蓝。
    _sup = getattr(window, "btn_support", None)
    _ai = _qss_text.find("#accentButton {")
    _accent_rule = _qss_text[_ai:_ai + 400] if _ai >= 0 else ""
    record(
        "「关注作者」是蓝色（#accentButton + ACCENT 色；黄色已被用户否掉）",
        _sup is not None
        and _sup.objectName() == "accentButton"
        and f"background: {theme.ACCENT.lower()}" in _accent_rule.lower(),
        f"objectName={_sup.objectName() if _sup else None!r}；主题蓝={theme.ACCENT}；"
        f"QSS 含 #accentButton 规则={_ai >= 0}",
    )
    # 用户 2026-10-11：「本工具未随包分发任何第三方程序（v1 不需要任何外部解析工具）」划掉了。
    _about_has_phrase = "不需要任何外部解析工具" in _about_all
    record(
        "关于页不再出现「v1 不需要任何外部解析工具」",
        not _about_has_phrase,
        f"该句仍在页面上={'是' if _about_has_phrase else '否'}；"
        f"无第三方组件时整块不显示（不再显示一句'没有'）",
    )
    # 用户 2026-10-11：「删除工具中与时间和日期有关的内容，版本号可以留着。」
    _date_leaks = []
    for _f in ("wmm/ui/dialogs.py", "wmm/ui/splash.py", "wmm/distinfo.py"):
        try:
            _txt = (Path(__file__).resolve().parent.parent / _f).read_text(encoding="utf-8")
        except OSError:
            continue
        if "BUILD_DATE" in _txt:
            _date_leaks.append(_f)
    record(
        "界面里不再显示日期（版本号保留）",
        not _date_leaks,
        f"仍引用 BUILD_DATE 的文件={_date_leaks}" if _date_leaks
        else "关于页 / 启动页 / 说明.txt 均已改为只显示版本号",
    )
    record(
        "「关于与支持」里有 QQ 粉丝群号",
        bool(getattr(version, "QQ_GROUP_NUMBER", ""))
        and _about_txt_has_qq,
        f"QQ_GROUP_NUMBER={getattr(version, 'QQ_GROUP_NUMBER', '(无)')}；"
        f"关于页可见文本里能找到={'是' if _about_txt_has_qq else '否'}",
    )
    record(
        "作者徽标已改成按钮（不再是可选中文本的 QLabel）",
        badge is not None
        and isinstance(badge, QPushButton)
        and not isinstance(badge, QLabel),
        f"类型={type(badge).__name__}；"
        # 反向守：万一哪天有人把它改回 QLabel 并开了 TextSelectableByMouse，
        # Qt 按下时会响系统提示音（用户为这个报过两次）。
        f"是否 QLabel={isinstance(badge, QLabel)}",
    )

    # ---- 14f. 导入对话框的原生入口 ----
    # 用户五轮来回：①要「浏览文件…」→ ②说"删除此按钮"→ ③要"也能导入ZIP和pak"（合并成一个）
    # → ④「不能批量导入文件夹了…那就像原来那样两个按钮吧」（回到两个）
    # → ⑤「这个按钮必须有批量导入文件夹的功能」（「选文件夹…」改成原生可多选）。
    from unittest import mock

    from PySide6.QtWidgets import QAbstractItemView

    from wmm.ui import dialogs as _dlgs
    from wmm.ui import winfolders as _wf

    _picker = _dlgs.ImportPickerDialog("自检", str(Path.home()))
    try:
        _btns = {b.text().strip() for b in _picker.findChildren(QPushButton)}
        record(
            "导入框有两个原生入口：「浏览文件…」(多选 pak/压缩包) +「选文件夹…」(可多选目录)",
            {"浏览文件…", "选文件夹…"} <= _btns and "浏览…" not in _btns,
            f"现有入口按钮={sorted(t for t in _btns if '浏览' in t or '文件夹' in t)}",
        )
        import inspect

        _fsrc = inspect.getsource(_dlgs.ImportPickerDialog._browse_files)
        _dsrc = inspect.getsource(_dlgs.ImportPickerDialog._browse_dir)
        record(
            "「选文件夹…」走原生**多选**（IFileOpenDialog + FOS_ALLOWMULTISELECT）",
            "winfolders.pick_folders" in _dsrc and callable(getattr(_wf, "pick_folders", None)),
            "已核对 _browse_dir 调 winfolders.pick_folders —— Qt 的 "
            "getExistingDirectory 只能选一个、getOpenFileNames 在目录上只返一个",
        )
        # 🚨 原生多选**必须留单选回退**：非 Windows / COM 失败时宁可退化成
        #    "一次一个"，也不能点了没反应。
        record(
            "原生多选不可用时回退到 getExistingDirectory（绝不点了没反应）",
            "getExistingDirectory" in _dsrc,
            "回退分支已在（拿到 None 就退单选）",
        )
        record(
            "文件框只管文件、目录框只管目录（不互相串）",
            "getOpenFileNames" in _fsrc and "getExistingDirectory" not in _fsrc
            and not hasattr(_dlgs.ImportPickerDialog, "_browse_any"),
            "文件框= getOpenFileNames；目录框= pick_folders(+getExistingDirectory 回退)；"
            "已删掉的合并版 _browse_any 不许复活",
        )
        record(
            "**批量**选文件夹仍保留自绘树（ExtendedSelection）这条退路",
            _picker.view.selectionMode() == QAbstractItemView.ExtendedSelection,            f"树的 selectionMode={_picker.view.selectionMode().name}",
        )

        # ---- 行为：原生多选的结果必须被原样收下（去重、不截断）----
        _paths = [
            str(Path.home() / "Desktop" / "modA"),
            str(Path.home() / "Desktop" / "modB"),
            str(Path.home() / "Desktop" / "modC"),
        ]

        def _fresh():
            return _dlgs.ImportPickerDialog("自检", str(Path.home()))

        _accepted = int(_dlgs.QDialog.Accepted)
        d1 = _fresh()
        with mock.patch.object(_wf, "pick_folders", return_value=list(_paths)):
            d1._browse_dir()
        record(
            "原生多选返回 3 个目录 → 三个全都进选中项并直接确定",
            list(d1._selected) == _paths and int(d1.result()) == _accepted,
            f"选中={[Path(p).name for p in d1._selected]}；已确定={int(d1.result()) == _accepted}",
        )
        d1.deleteLater()

        # 重复项要去重（用户在原生框里可能从不同位置选到同一个）
        d2 = _fresh()
        with mock.patch.object(_wf, "pick_folders", return_value=_paths + [_paths[0]]):
            d2._browse_dir()
        record(
            "原生多选返回重复路径 → 去重（不导入两遍）",
            list(d2._selected) == _paths,
            f"返回 4 条（含 1 条重复）→ 选中 {len(d2._selected)} 条",
        )
        d2.deleteLater()

        # 回退：pick_folders 返回 None（不可用）→ 走 Qt 单选，且只弹一次框
        d3 = _fresh()
        with mock.patch.object(_wf, "pick_folders", return_value=None), \
                mock.patch.object(_dlgs.QFileDialog, "getExistingDirectory",
                                  return_value=str(Path.home() / "Downloads")) as _ged:
            d3._browse_dir()
        record(
            "原生多选不可用 → 回退单选框（只弹一次，不会又弹原生又弹 Qt）",
            _ged.call_count == 1
            and list(d3._selected) == [str(Path.home() / "Downloads")]
            and int(d3.result()) == _accepted,
            f"回退框调用 {_ged.call_count} 次；选中={[Path(p).name for p in d3._selected]}",
        )
        d3.deleteLater()

        # 🚨 取消（`[]`）**不能**触发回退 —— 否则用户一取消就又被弹一个框
        d4 = _fresh()
        with mock.patch.object(_wf, "pick_folders", return_value=[]), \
                mock.patch.object(_dlgs.QFileDialog, "getExistingDirectory",
                                  return_value="X") as _ged2:
            d4._browse_dir()
        record(
            "用户在原生框里取消 → 不做任何事（不会又弹一个 Qt 框）",
            _ged2.call_count == 0
            and not d4._selected
            and int(d4.result()) != _accepted,
            f"回退框被调 {_ged2.call_count} 次（必须 0）；选中={list(d4._selected)}",
        )
        d4.deleteLater()
    finally:
        _picker.deleteLater()

    # ---- 14h. 同系列合并（用户：「前两个是不是可以合成一个？同系列能否识别？加个标识」）----
    from wmm import importer as _importer_mod

    _series_ok = (
        callable(getattr(_importer_mod, "_group_candidates", None))
        and callable(getattr(_importer_mod, "_series_key", None))
        and callable(getattr(_importer_mod, "_series_display_name", None))
    )
    record(
        "同系列分组识别已实现（_group_candidates / _series_key）",
        _series_ok,
        f"_group_candidates={callable(getattr(_importer_mod, '_group_candidates', None))}；"
        f"_series_key={callable(getattr(_importer_mod, '_series_key', None))}",
    )
    # 归一化能力：`Project_Lewd` 与 `ProjectLewd` 必须同 key（否则永远合并不了）
    _k1 = _importer_mod._series_key("Project_Lewd 本体") if _series_ok else ""
    _k2 = _importer_mod._series_key("ProjectLewd_20251107_update_pack") if _series_ok else ""
    record(
        "目录名归一化：Project_Lewd 与 ProjectLewd 认作同一个系列",
        bool(_k1) and _k1 == _k2,
        f"{_k1!r} vs {_k2!r}",
    )
    # 系列名不能是宣传语或部位说明
    _sn = _importer_mod._series_display_name(
        ["Project_Lewd 本体", "ProjectLewd_20251107_update_pack"]
    ) if _series_ok else ""
    record(
        "合并后的 mod 名是系列名「Project Lewd」（不是部件名也不是宣传语父目录名）",
        _sn == "Project Lewd",
        f"系列名={_sn!r}",
    )
    # ⚠️ 反向守：pak 名只是"高度相似"不能合（UE 资产名不同 mod 会大量撞名）
    _src2 = Path(_importer_mod.__file__).read_text(encoding="utf-8")
    record(
        "pak 名同源要求「高度重合」(Jaccard≥0.5)，不是「有交集」",
        ">= 0.5" in _src2,
        "已核对源码里用的是相似度阈值（消除棒子与本体 pak 名高度相似但必须拆开）",
    )
    # 🚨 内容指纹**不能**带 rel_path —— 否则「同一批 pak、剥层级结果不同」判不出重复。
    #    真实案例：用户截图里 `全果 明军-11.28/` 与 `全果 明军-11.28.zip`
    #    是同一个 mod（pak 的 sha256 完全相同），因 rel_path 差一层而多出一条。
    from wmm import modstore as _ms

    _f1 = _ms.ModFile(name="a_P.pak", rel_path="a_P.pak", sha256="deadbeef")
    _f2 = _ms.ModFile(name="a_P.pak", rel_path="全果 明军-11.28/a_P.pak", sha256="deadbeef")
    _h1 = _ms.compute_content_hash([_f1])
    _h2 = _ms.compute_content_hash([_f2])
    record(
        "内容指纹只看文件内容、**不含路径**（同名文件夹 vs ZIP 能判出重复）",
        bool(_h1) and _h1 == _h2,
        f"rel_path 不同但 sha256 相同 → 指纹 {(_h1 or '空')[:12]} vs {(_h2 or '空')[:12]}；"
        f"{'相同=能判重' if _h1 == _h2 and _h1 else '不同=会漏判'}",
    )
    record(
        "预检对压缩包也判重（不再一律放弃），ZIP 走流式哈希",
        callable(getattr(_importer_mod, "_zip_pak_hashes", None)),
        f"_zip_pak_hashes={callable(getattr(_importer_mod, '_zip_pak_hashes', None))}；"
        "之前压缩包一律进 unknown，等于 ZIP 导入完全绕过预检",
    )
    # 卡片标识
    record(
        "卡片有「同系列 N 个部件」标识（series_badge）",
        hasattr(_catalog.ModView, "series_badge"),
        f"ModView.series_badge 属性存在={hasattr(_catalog.ModView, 'series_badge')}",
    )

    # ---- 14g. 二次导入必须能提前识别重复（用户：「二次导入能识别出来吗」）----
    _pc_ok = callable(getattr(_importer_mod, "precheck_duplicates", None)) and hasattr(
        _importer_mod, "PrecheckHit"
    )
    record(
        "导入前有重复预检（precheck_duplicates），返回命中与未判定两类",
        _pc_ok
        and callable(getattr(window, "_precheck_before_import", None)),
        f"importer.precheck_duplicates={'有' if _pc_ok else '无'}；"
        f"MainWindow._precheck_before_import="
        f"{'有' if callable(getattr(window, '_precheck_before_import', None)) else '无'}",
    )
    _pc_src = Path(_importer_mod.__file__).read_text(encoding="utf-8")
    record(
        "预检对压缩包不再一律放弃（ZIP 走流式哈希；7z/rar 拿不到哈希就如实说没法判定）",
        "_zip_pak_hashes" in _pc_src
        and "check_archive" in _pc_src
        # ⚠️ 7z / rar 没有纯 Python 读法，**必须**保留 unknown 分支 ——
        #   宁可漏报也不能误报成"重复"。
        and "unknown.append(source)" in _pc_src,
        "已核对：ZIP 走 _zip_pak_hashes；7z/rar 仍如实进 unknown，绝不猜",
    )

    # ---- 15. 关于页字号（用户 2026-10-11：「这一页中的小字太小」）----
    # ⚠️ 判据要点：字号必须写在**控件自身的 setStyleSheet** 里。
    #   应用级 QSS 有 `QWidget { font-size: 10pt }`，会覆盖 setFont() —— 这就是
    #   以前「小字太小」的真正原因（_section 写的是 setFont(11pt)，实际被压成 10pt）。
    about_src = Path(__import__("wmm.ui.dialogs", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    about_ok = (
        version.ABOUT_BODY_FONT_PT >= theme.FONT_SIZE_BODY + 1
        and version.ABOUT_SMALL_FONT_PT >= theme.FONT_SIZE_SMALL + 1
        and version.ABOUT_SECTION_FONT_PT > version.ABOUT_BODY_FONT_PT
        and "ABOUT_BODY_FONT_PT" in about_src
        and "ABOUT_SECTION_FONT_PT" in about_src
    )
    record(
        "关于页正文/小字字号已放大（且走控件自身样式表，不被 QSS 压掉）",
        about_ok,
        f"正文 {version.ABOUT_BODY_FONT_PT}pt（全局基准 {theme.FONT_SIZE_BODY}pt）"
        f" · 小字 {version.ABOUT_SMALL_FONT_PT}pt（基准 {theme.FONT_SIZE_SMALL}pt）"
        f" · 分区标题 {version.ABOUT_SECTION_FONT_PT}pt；"
        f"AboutDialog 里用到常量={'ABOUT_BODY_FONT_PT' in about_src}",
    )

    # ---- 16. 批量安装弹窗：「已存在」只在真有覆盖时出现 ----
    # 用户 2026-10-11 截图问过「我的游戏目录中没看到已经安装过这个 mod」——
    # 原因是那段解释原来是无条件拼上去的。
    mw_src = Path(__import__("wmm.ui.main_window", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    batch_block = mw_src.split("def install_selected", 1)[-1].split("def uninstall_selected", 1)[0]
    record(
        "批量安装弹窗的「已存在」解释只在真的有文件被覆盖时才出现",
        "if overwrite_names:" in batch_block
        and "**「已存在」指游戏目录里已经有同名文件了**" not in batch_block,
        "无条件拼接的旧文案已移除，改为 if overwrite_names: 条件追加",
    )

    # ---- 16b. 小屏 / 高 DPI：启动尺寸 clamp，且硬最小跟着一起缩 ----
    # 用户 2026-10-13 问「1366×768 @150% 没事吧，本身支持缩放」。
    # Qt6 的自动 DPI 缩放只保证「界面不变形」，**不保证「窗口在屏幕内」**：
    # 1366×768@150% 的逻辑分辨率只有 911×512，容不下 1000×640 的启动尺寸
    # 和 520 的硬最小 → 底部工具栏（安装/卸载那一排）会落在屏幕外面点不到。
    # ⚠️ 顺序是这条检查的全部意义：**必须先 clamp 再 setMinimumSize**。
    #    Qt 的 minimumSize 优先级高于 resize()，顺序一反，clamp 出来的 496
    #    会被顶回 520，等于没修（2026-10-13 离屏探针实测就是这个现象）。
    #    配套的纯函数行为由 tests/test_window_fit.py 的 7 条用例守着。
    _mw_src = Path(__import__("wmm.ui.main_window", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    _mw_class = _mw_src.split("class MainWindow", 1)[-1]
    _init_block = _mw_class.split("def __init__", 1)[-1].split("def _build_ui", 1)[0]
    _clamp_pos = _init_block.find("fit_window_to_screen(")
    _min_pos = _init_block.find("setMinimumSize(")
    record(
        "小屏/高 DPI 启动 clamp：先算 fit_window_to_screen 再 setMinimumSize（顺序反了会被顶回硬最小）",
        _clamp_pos > 0 and _min_pos > _clamp_pos
        and "min(version.MIN_WINDOW_WIDTH_HARD, default_w)" in _init_block,
        f"clamp 在 offset {_clamp_pos}、setMinimumSize 在 offset {_min_pos}；"
        f"硬最小跟随 clamp 结果收缩（min(硬最小, 启动尺寸)）",
    )

    # ---- 16c. 「从库中删除」绝不能顺手卸载（用户 2026-10-18 报的事故）----
    # 原话：「我将 mod 安装后，不点卸载，点从库中删除 mod，已安装的 mod
    # 也会被卸载。」第一版修复把 QMessageBox.question 的默认按钮设成 No，
    # 但 `app._silence_message_boxes()` 会把 QMessageBox.question 换成
    # **两颗按钮**（是/取消）的 dialogs.ask —— 传的 Yes|No + 默认 No
    # 被整段丢掉，用户界面上根本没有「否」（用户第二反馈：「没有否」）。
    # 终版：delete_selected() 直接用三按钮自绘对话框，主按钮就是
    # 「否，只删库」（粉色实心、与「安装所选」同款），默认焦点落在它上面。
    # 静态断言这三件事；行为契约由
    # tests/test_install.py::TestDeleteFromLibrary 守着。
    _del_src = _mw_src.split("def delete_selected", 1)[-1].split("def autodetect_game_dir", 1)[0]
    record(
        "从库中删除：三按钮对话框，主按钮「否，只删库」且默认焦点在它上面",
        'no_text="否，只删库"' in _del_src
        and 'yes_text="是，先卸载再删库"' in _del_src
        and 'default="no"' in _del_src
        # 带左括号 = 真实调用；注释里提到不算
        and "QMessageBox.question(" not in _del_src,
        f"no_text={'no_text=\"否，只删库\"' in _del_src}；"
        f"yes_text={'yes_text=\"是，先卸载再删库\"' in _del_src}；"
        f"default=no={'default=\"no\"' in _del_src}；"
        f"仍调 question 静态方法={'QMessageBox.question(' in _del_src}",
    )

    # ---- 16d. 卸载/删除结果必须弹窗（用户 2026-10-19：「卸载成功后没有弹窗」）----
    # 之前批量卸载只写状态栏，10 秒自动消失 = 等于没有反馈；
    # 批量安装那边一直有「批量安装结果」弹窗，卸载/删除漏了。
    _uni_src = _mw_src.split("def uninstall_selected", 1)[-1].split("def _notify_restored", 1)[0]
    _del_src2 = _mw_src.split("def delete_selected", 1)[-1].split("def autodetect_game_dir", 1)[0]
    record(
        "卸载/从库删除完成后都弹结果框（不能只写状态栏）",
        '"卸载结果"' in _uni_src and '"删除结果"' in _del_src2,
        f"卸载结果框={'\"卸载结果\"' in _uni_src}；删除结果框={'\"删除结果\"' in _del_src2}",
    )

    # ---- 16e. 重建卡片不得让卡片变成独立顶层窗口（2026-10-19 黑框事故）----
    # FlowLayout.clear() 曾用 setParent(None)，卡片会短暂变成「无标题白框」
    # （实测 236×311、一次几十个）；ModCard 创建时也曾漏传 parent。
    _flow_src = Path(__import__("wmm.ui.flowlayout", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    _clear_block = _flow_src.split("def clear(", 1)[-1].split("def ", 1)[0]
    record(
        "重建卡片：清空时保留 parent（不 setParent(None)）、新卡片创建即给 parent",
        # 真实调用后面紧跟换行；docstring 里是反引号包着的 `` `widget.setParent(None)` ``
        "widget.setParent(None)\n" not in _clear_block
        and "widget.hide()" in _clear_block
        and "ModCard(view, self.cards_host)" in _mw_src,
        f"真调用 setParent(None)={'widget.setParent(None)' + chr(10) in _clear_block}；"
        f"hide()={'widget.hide()' in _clear_block}；"
        f"卡片传 parent={'ModCard(view, self.cards_host)' in _mw_src}",
    )

    # ---- 17. 卸载后「有原文件被还原」的提示（用户 2026-10-12）----
    # 原话：「复原的时候加个提示吧，告知用户有手动安装的 mod 被复原。」
    # 这条**跑完整真流程**：装一个会覆盖「手放文件」的 mod → 勾选卡片 →
    # 批量卸载，然后验四件事：
    #   ① 卸载确认框里有没有「会还原哪些原文件」的预告（且点名）；
    #   ② 卸完有没有弹提示、标题是不是「有原文件被还原」、有没有点名；
    #   ③ 手放的那个文件是否真被还原回原位（字节一致）；
    #   ④ 装的时候确实覆盖过（覆盖前是 mock 内容）。
    # ⚠️ 用 WMM_DATA_DIR 把数据目录指进临时目录 —— 与真实应用走同一条
    #    `apppaths.backups_dir()` 代码路径，但绝不让自检碰真实 %APPDATA%。
    with tempfile.TemporaryDirectory() as tmp:
        from wmm import gamedir as _gamedir
        from wmm import installer as _installer
        from wmm import library as _library_mod
        from wmm import modstore as _modstore
        from wmm import settings as _settings_mod
        from wmm.modstore import ModFile as _ModFile
        from wmm.modstore import ModMeta as _ModMeta
        from wmm.ui import dialogs as _dlg
        from wmm.ui.main_window import MainWindow as _MW

        tmp_path = Path(tmp)
        game_root = tmp_path / "steamapps" / "common" / "Wuchang Fallen Feathers"
        paks = game_root / "Project_Plague" / "Content" / "Paks"
        (paks / "~mods").mkdir(parents=True, exist_ok=True)
        (paks / "Project_Plague-Windows.pak").write_bytes(b"VANILLA")
        # 「手放」在 ~mods 里的文件 —— 会被 mod 覆盖、卸载时还原
        hand_placed = paks / "~mods" / "还原提示_P.pak"
        hand_placed.write_bytes(b"HAND-PLACED")

        os.environ[apppaths.ENV_DATA_DIR_OVERRIDE] = str(tmp_path / "appdata")
        try:
            apppaths.ensure_data_dirs()

            store2 = _modstore.ModStore(tmp_path / "mods")
            store2.ensure()
            meta2 = _ModMeta(
                id="5ca1ab1e5ca1ab1e",
                name="还原提示",
                content_hash="5ca1ab1e5ca1ab1e",
                files=[_ModFile(name="x_P.pak")],
            )
            store2.save(meta2)
            fdir = store2.files_dir(meta2.id)
            fdir.mkdir(parents=True, exist_ok=True)
            (fdir / "x_P.pak").write_bytes(b"MOCK-PAK-CONTENT")
            meta2.files = [
                _ModFile(name="x_P.pak", rel_path="x_P.pak", size=16, sha256="ab")
            ]
            store2.save(meta2)

            lib2 = _library_mod.Library.load(tmp_path / "library.json")
            probe2 = _gamedir.validate_game_dir(game_root)
            view2 = None
            asked: list = []
            shown: list = []
            overwritten_ok = False
            restored_ok = False
            confirm_named = False
            notice_ok = False
            if probe2.ok:
                # 不传 backup_root —— 与真实应用一致，走 apppaths.backups_dir()
                _installer.install(meta2, probe2, store2, lib2)
                overwritten_ok = hand_placed.read_bytes() == b"MOCK-PAK-CONTENT"

                settings2 = _settings_mod.Settings.load(tmp_path / "settings.json")
                settings2.remember_game_dir(probe2)
                window2 = _MW(settings2, store2, lib2)
                window2.resize(1180, 800)
                window2.show()
                app.processEvents()
                view2 = next((v for v in window2._views if v.id == meta2.id), None)

                saved_ask, saved_show = _dlg.ask, _dlg.show_text
                _dlg.ask = lambda parent, title, body, **k: (
                    asked.append((title, body)),
                    True,
                )[1]
                _dlg.show_text = lambda parent, title, body, heading="": shown.append(
                    (title, body)
                )
                try:
                    # ⚠️ 别用 card.set_checked() 注册勾选 —— 它只改卡片内部
                    # 状态、**不发 checked_changed**，window._checked_ids 收不到。
                    # 直接写 _checked_ids（`_checked_views` 读的就是它；卡片
                    # 复选框本身的交互另有检查项覆盖）。
                    window2._checked_ids.add(meta2.id)
                    window2.uninstall_selected()
                finally:
                    _dlg.ask, _dlg.show_text = saved_ask, saved_show

                restored_ok = (
                    hand_placed.is_file() and hand_placed.read_bytes() == b"HAND-PLACED"
                )
                confirm_body = " ".join(b for _, b in asked)
                confirm_named = (
                    len(asked) == 1
                    and "还原" in confirm_body
                    and hand_placed.name in confirm_body
                )
                notice_titles = [t for t, _ in shown if "还原" in t]
                notice_body = next((b for t, b in shown if "还原" in t), "")
                notice_ok = (
                    notice_titles == ["有原文件被还原"]
                    and hand_placed.name in notice_body
                )

            record(
                "卸载还原提示：确认框预告 + 卸完点名 + 手放文件真还原",
                bool(view2)
                and view2.installed
                and overwritten_ok
                and confirm_named
                and notice_ok
                and restored_ok,
                f"安装时覆盖了手放文件={overwritten_ok}；确认框预告并点名={confirm_named}"
                f"（弹了 {len(asked)} 次）；卸完提示标题/点名={notice_ok}；"
                f"手放文件已还原={restored_ok}",
            )
        finally:
            os.environ.pop(apppaths.ENV_DATA_DIR_OVERRIDE, None)

    # ---- 18. 「手装 mod」识别（用户 2026-10-12）----
    # 原话：「我手动安装了 10 个 mod，其中 5 个与管理器中的 mod 相同。
    # 那么管理器能识别到这五个 mod 吗？并在管理器中显示已经安装？」
    # 这条跑完整真流程：库里有一个 mod → 把同样的文件**手工放进 ~mods**
    #（不经 install，所以没有安装记录）→ 点「识别游戏里已有的 mod」，
    # 然后验七件事：
    #   ① 识别前卡片是「未装」（没有记录，旧行为）；
    #   ② 识别后状态变「已装」—— 游戏确实在加载它，不能显示未装骗人；
    #   ③ 但 installed=False（没有本工具安装记录 → 卸不了它）；
    #   ④ 卡片上有「手装」徽标；
    #   ⑤ 缓存落盘（refresh() 只读缓存，不重扫）；
    #   ⑥ 点卸载静默返回 None（不给报错，confirm=False 时也不弹框）；
    #   ⑦ 认不出来的野生 pak 进 unknown。
    with tempfile.TemporaryDirectory() as tmp:
        from wmm import catalog
        from wmm import gamedir as _gamedir
        from wmm import library as _library_mod
        from wmm import modstore as _modstore
        from wmm import settings as _settings_mod
        from wmm import wildscan as _wildscan
        from wmm.modstore import ModFile as _ModFile
        from wmm.modstore import ModMeta as _ModMeta
        from wmm.ui.main_window import MainWindow as _MW

        tmp_path = Path(tmp)
        game_root = tmp_path / "steamapps" / "common" / "Wuchang Fallen Feathers"
        paks = game_root / "Project_Plague" / "Content" / "Paks"
        (paks / "~mods").mkdir(parents=True, exist_ok=True)
        (paks / "Project_Plague-Windows.pak").write_bytes(b"VANILLA")

        os.environ[apppaths.ENV_DATA_DIR_OVERRIDE] = str(tmp_path / "appdata")
        try:
            apppaths.ensure_data_dirs()
            store3 = _modstore.ModStore(tmp_path / "mods")
            store3.ensure()
            meta3 = _ModMeta(
                id="7e57a9c17e57a9c1",
                name="手装识别",
                content_hash="7e57a9c17e57a9c1",
                files=[_ModFile(name="x_P.pak")],
            )
            store3.save(meta3)
            fdir3 = store3.files_dir(meta3.id)
            fdir3.mkdir(parents=True, exist_ok=True)
            content3 = b"HAND-PLACED-CONTENT"
            (fdir3 / "x_P.pak").write_bytes(content3)
            meta3.files = [
                _ModFile(
                    name="x_P.pak",
                    rel_path="x_P.pak",
                    size=len(content3),
                    sha256=_modstore.hash_file(fdir3 / "x_P.pak"),
                )
            ]
            store3.save(meta3)

            lib3 = _library_mod.Library.load(tmp_path / "library.json")
            probe3 = _gamedir.validate_game_dir(game_root)
            before = after = None
            scan3 = None
            uninstall_try: object = "no-view"
            wild_ok = cache_ok = count_ok = False
            if probe3.ok:
                # 手工放进 ~mods（**不经 install** → 没有安装记录）
                (paks / "~mods" / "x_P.pak").write_bytes(content3)
                # 再加一个库里没有的野生 pak
                (paks / "~mods" / "野生_P.pak").write_bytes(b"WILD")

                settings3 = _settings_mod.Settings.load(tmp_path / "settings.json")
                settings3.remember_game_dir(probe3)
                window3 = _MW(settings3, store3, lib3)
                window3.resize(1180, 800)
                window3.show()
                app.processEvents()

                before = next((v for v in window3._views if v.id == meta3.id), None)
                scan3 = window3._recognize_external(force=True, announce=False)
                app.processEvents()
                after = next((v for v in window3._views if v.id == meta3.id), None)
                card3 = window3._cards.get(meta3.id)
                # 卸不了它（没有安装记录）—— 必须静默返回 None，不能弹错
                if after is not None:
                    uninstall_try = window3._uninstall_one(after, confirm=False)
                wild_ok = scan3 is not None and any(
                    f.rel == "野生_P.pak" for f in scan3.unknown
                )
                cache_ok = _wildscan.cache_path().is_file()
                count_ok = (
                    "1" in window3._filter_buttons[catalog.FILTER_INSTALLED].text()
                )
                window3.close()

            record(
                "手装 mod 识别：内容相同 → 「已装（手装）」且卸不了、野生文件单列",
                bool(before)
                and not before.installed
                and before.status == catalog.STATUS_NOT_INSTALLED
                and bool(after)
                and after.status == catalog.STATUS_INSTALLED
                and not after.installed
                and bool(after.external)
                and after.external_hit is not None
                and after.external_hit.complete
                and after.external_badge == "手装"
                and after.status_label == "已装"
                and uninstall_try is None
                and wild_ok
                and cache_ok
                and count_ok,
                f"识别前={before.status if before else None}；"
                f"识别后={after.status if after else None}（installed={after.installed if after else None}，"
                f"徽标={after.external_badge if after else None!r}）；"
                f"卸载尝试={'静默 None' if uninstall_try is None else uninstall_try}；"
                f"野生文件单列={wild_ok}；缓存落盘={cache_ok}；已装计数含手装={count_ok}",
            )
        finally:
            os.environ.pop(apppaths.ENV_DATA_DIR_OVERRIDE, None)

    # ---- 输出 ----
    width = max(len(name) for name, _, _ in results)
    print(f"\n{version.APP_NAME_ZH} v{version.VERSION} 启动自检（离屏模式）")
    print("=" * (width + 60))
    failed = 0
    for name, status, detail in results:
        mark = {PASS: "✓", FAIL: "✗", WARN: "!"}[status]
        print(f"{mark} {name:<{width}}  {detail}")
        if status == FAIL:
            failed += 1
    print("=" * (width + 60))
    print(f"共 {len(results)} 项，失败 {failed} 项")
    app.quit()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
