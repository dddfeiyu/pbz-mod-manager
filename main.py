# -*- coding: utf-8 -*-
"""开发期启动入口。

    .venv\\Scripts\\python.exe main.py

打包后的 exe 也走这个文件（PyInstaller 的入口就是它），所以这里同时承担
**发布产物的自检入口** —— 加 `--selftest` 时不开界面，只跑一批能在无 GUI
环境下验证的检查，把结果写进文件再退出。

为什么自检结果要写文件而不是打印：打包时 `console=False`（玩家用的图形工具
不该弹黑框），这种 exe 在 Windows 上**根本没有 stdout**，print 什么都拿不到。
写文件是唯一在两种形态下都可靠的通道。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _selftest(out_path: str) -> int:
    """在**打包后的真实二进制**里跑一遍检查。

    这一条很关键：需求文档 11.5 的前提认知是「开发机上跑通 = 什么都没证明」。
    开发机有 Python、有 PySide6、有 MSVC 运行库，天然不会暴露
    「打包漏了 Qt 插件」「少了某个 DLL」这类问题。只有在 exe 自己身上跑，
    才算真的验过。
    """
    import os
    import platform
    import traceback

    lines: list[str] = []
    failures = 0

    def rec(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        if not ok:
            failures += 1
        lines.append(f"{'[OK]  ' if ok else '[FAIL]'} {name}" + (f"  —— {detail}" if detail else ""))

    lines.append("frozen=%s" % bool(getattr(sys, "frozen", False)))
    lines.append("python=%s %s" % (platform.python_version(), platform.machine()))
    lines.append("qpa=%s" % os.environ.get("QT_QPA_PLATFORM", "(系统默认)"))
    lines.append("executable=%s" % sys.executable)
    lines.append("-" * 60)

    # 1) 打包后还能不能 import 到自己的包
    try:
        from wmm import apppaths, version

        rec("包可导入", True, f"{version.APP_NAME_ZH} v{version.VERSION}")
    except Exception as exc:  # noqa: BLE001
        rec("包可导入", False, repr(exc))
        _write(out_path, lines)
        return 1

    # 2) 数据目录可写，且**不在程序目录下**（第 4 章硬约束）
    try:
        apppaths.ensure_data_dirs()
        rec("数据目录已创建", True, str(apppaths.data_dir()))
        probe = apppaths.data_dir() / ".selftest-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        rec("数据目录可写", True, "真的建了一个探针文件")
    except Exception as exc:  # noqa: BLE001
        rec("数据目录可写", False, repr(exc))
    try:
        leaked = apppaths.is_in_data_dir(apppaths.program_dir())
        rec("数据目录与程序目录分离", not leaked,
            f"program={apppaths.program_dir()}")
        if apppaths.is_in_program_dir(apppaths.data_dir()):
            rec("数据目录落在程序目录内", False, "违反硬约束")
    except Exception as exc:  # noqa: BLE001
        rec("目录分离检查", False, repr(exc))

    # 3) Qt 能起来、能解码 webp（真实 mod 的预览图 101 张是 webp，
    #    打包漏带 webp 插件的话用户机上预览图会全空白）
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QImage, QImageReader
        from PySide6.QtWidgets import QApplication

        qapp = QApplication.instance() or QApplication([])  # noqa: F841
        fmts = {bytes(f).decode("ascii", "replace") for f in QImageReader.supportedImageFormats()}
        rec("Qt 可用", True, f"图片格式 {len(fmts)} 种")
        rec("可解码 webp", "webp" in fmts, "webp 在支持列表里" if "webp" in fmts else "缺 webp 解码插件！")
        rec("可解码 png/jpg", {"png", "jpg"} <= fmts or {"png", "jpeg"} <= fmts)
    except Exception as exc:  # noqa: BLE001
        rec("Qt 可用", False, repr(exc))

    # 4) 中文字体 —— 缺字体的话界面全是方框
    #
    # ⚠️ 这里**必须**先调 ensure_cjk_font()。原因：自检是在
    # `QT_QPA_PLATFORM=offscreen` 下跑的（离屏，不弹窗口，可以在无人值守时跑），
    # 而**离屏平台的字体库实测是空的（0 个字体）** —— 直接判 can_render_cjk()
    # 必然为 False，得出的是"中文渲染不了"的假结论。
    # 正确做法与开发期的 selfcheck 一致：先走一遍兜底加载，再判断。
    # 顺带这也真的验证了「打包产物里字体兜底逻辑还能用」。
    try:
        from wmm.ui import fonts

        before = fonts.describe()
        loaded = fonts.ensure_cjk_font()
        ok = fonts.can_render_cjk()
        rec(
            "中文可渲染",
            ok,
            (f"已兜底加载 {loaded}；" if loaded else "无需兜底；") + fonts.describe(),
        )
        if not ok:
            lines.append(f"        （兜底前状态：{before}）")
    except Exception as exc:  # noqa: BLE001
        rec("中文可渲染", False, repr(exc))

    # 5) 随包资源在不在（图标 / 说明.txt）
    try:
        icon = apppaths.resource_path("assets", "icon.ico")
        rec("随包图标存在", icon.is_file(), str(icon))
    except Exception as exc:  # noqa: BLE001
        rec("随包图标存在", False, repr(exc))
    try:
        readme = apppaths.program_dir() / "说明.txt"
        rec("说明.txt 存在", readme.is_file(), str(readme))
    except Exception as exc:  # noqa: BLE001
        rec("说明.txt 存在", False, repr(exc))

    # 6) 系统要求判定（在真实机器上是可信的）
    try:
        from wmm import sysreq

        chk = sysreq.check()
        rec("系统要求判定可运行", True, chk.detail_text.replace("\n", " · "))
    except Exception as exc:  # noqa: BLE001
        rec("系统要求判定可运行", False, repr(exc))

    # 7) 核心业务模块在打包产物里都能 import（冻结后最容易在这里翻车：
    #    被 exclude 掉、或者用了动态导入 PyInstaller 分析不到）
    for mod in (
        "wmm.archive", "wmm.catalog", "wmm.conflicts", "wmm.enabler",
        "wmm.gamedir", "wmm.importer", "wmm.installer", "wmm.library",
        "wmm.modstore", "wmm.nameresolve", "wmm.pakfiles", "wmm.preview",
        "wmm.settings", "wmm.theme", "wmm.distinfo",
    ):
        try:
            __import__(mod)
            rec(f"import {mod}", True)
        except Exception as exc:  # noqa: BLE001
            rec(f"import {mod}", False, repr(exc))

    # 8) 打包产物里**不得**含任何第三方 pak（第 1 章红线，可人工核查项）
    try:
        root = apppaths.program_dir()
        bad = [
            p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in (".pak", ".ucas", ".utoc")
        ]
        rec("产物内无 pak/ucas/utoc", not bad,
            "干净" if not bad else f"发现 {len(bad)} 个：{[str(b.relative_to(root)) for b in bad[:5]]}")
    except Exception as exc:  # noqa: BLE001
        rec("产物内无 pak 检查", False, repr(exc))

    # 9) Qt 自带的**中文翻译**在不在。
    #    缺了的话 `QMessageBox.setDetailedText()` 会在全中文界面上摆出英文的
    #    「Hide Details...」（用户 2026-10-10 圈出来问「红圈中是什么」）——
    #    这属于"打包漏带数据文件"，开发机上永远复现不出来，只能在这里守。
    try:
        from PySide6.QtCore import QLibraryInfo, QTranslator

        base = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        probe = QTranslator()
        loaded = probe.load("qtbase_zh_CN", base)
        rec(
            "Qt 中文翻译可用（标准按钮不会是英文）",
            loaded,
            f"{base} → " + ("已加载" if loaded else "加载失败，按钮会退回英文"),
        )
    except Exception as exc:  # noqa: BLE001
        rec("Qt 中文翻译可用", False, repr(exc))

    lines.append("-" * 60)
    lines.append(f"失败 {failures} 项")
    _write(out_path, lines)
    return 1 if failures else 0


def _write(out_path: str, lines: list[str]) -> None:
    try:
        Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    argv = sys.argv[1:]
    if "--selftest" in argv:
        idx = argv.index("--selftest")
        out = argv[idx + 1] if len(argv) > idx + 1 else str(Path.home() / "wmm-selftest.txt")
        return _selftest(out)

    from wmm.ui.app import main as run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
