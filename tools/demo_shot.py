# -*- coding: utf-8 -*-
"""给界面截图（开发期用）。

**绝不触碰真实的 `%APPDATA%\\WuchangModManager\\`** —— 通过 `WMM_DATA_DIR`
环境变量把数据目录指向临时目录，跑完就删。所以这个脚本可以在开发机上随便跑，
不会污染用户的数据、也不会让"真实环境"和"演示环境"互相干扰。

它同时是**一次真实的端到端验收**：
    伪造游戏目录树 → 校验并记住 → 导入全部样例 mod（含 7z / 恶意包 / 无图）
    → 模拟安装两条 → 建主窗 → 截图

用法：
    .venv\\Scripts\\python.exe tools/demo_shot.py
    .venv\\Scripts\\python.exe tools/demo_shot.py --real "C:\\path\\to\\某个mod文件夹"
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT_DIR = Path(__file__).resolve().parent.parent / "docs"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="append", default=[], help="额外导入的真实 mod 文件夹（可多次）")
    parser.add_argument("--headless", action="store_true", default=True, help="离屏渲染（默认开）")
    parser.add_argument("--show", action="store_true", help="真正弹窗（需要你能看到屏幕）")
    args = parser.parse_args()

    if args.headless and not args.show:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    data_dir = Path(tempfile.mkdtemp(prefix="wmm-demo-data-"))
    os.environ["WMM_DATA_DIR"] = str(data_dir)
    print(f"临时数据目录：{data_dir}")

    from PySide6.QtWidgets import QApplication

    from wmm import apppaths, gamedir, library as library_mod, modstore, settings as settings_mod
    from wmm.ui import fonts, qss
    from tools.make_fake_game import build as build_fake_game
    from tools.make_sample_mods import build as build_samples

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(qss.build_qss())
    # 离屏平台没有字体库（实测 0 个字体），不显式加载的话中文全是方框 □，
    # 截图就完全没法看。见 wmm/ui/fonts.py。
    loaded_font = fonts.ensure_cjk_font()
    print(f"字体：{fonts.describe()}" + (f"（已加载 {loaded_font}）" if loaded_font else ""))
    apppaths.ensure_data_dirs()

    # ---- 1. 伪造游戏目录并"校验通过" ----
    game_tmp = Path(tempfile.mkdtemp(prefix="wmm-demo-game-"))
    made = build_fake_game(game_tmp)
    probe = gamedir.validate_game_dir(made["steam"])
    assert probe.ok, probe.warnings

    app_settings = settings_mod.Settings.load()
    app_settings.remember_game_dir(probe)
    app_settings.save()
    print(f"游戏目录（伪造）：{probe.game_root}  平台={probe.platform_label}  pak 目录={probe.paks_dir}")

    # ---- 2. 导入样例 + 真实 mod ----
    store = modstore.ModStore(app_settings.mods_library_dir)
    store.ensure()

    samples_tmp = Path(tempfile.mkdtemp(prefix="wmm-demo-samples-"))
    samples = build_samples(samples_tmp)
    to_import = [
        samples[k]
        for k in ("street_style", "logic_mod", "flat_no_preview", "with_ucas", "folder_mod", "gbk_readme", "seven_zip")
        if k in samples
    ]

    from wmm import importer

    report = importer.import_paths(to_import, store)
    print("导入样例：", report.summary())
    for outcome in report.outcomes:
        if outcome.kind != importer.KIND_IMPORTED:
            print(f"  [{outcome.kind}] {outcome.display_name}：{outcome.message}")

    for raw in args.real:
        p = Path(raw)
        if not p.exists():
            print(f"跳过不存在的真实路径：{p}")
            continue
        rep = importer.import_paths([p], store)
        print(f"导入真实 mod「{p.name}」：", rep.summary())

    # ---- 3. 故意跑一遍失败路径，确认提示是人话 ----
    for key in ("zipslip", "fake_rar", "not_a_mod"):
        rep = importer.import_paths([samples[key]], store)
        o = rep.outcomes[0]
        print(f"失败路径 [{key}] -> {o.kind}：{o.message}")

    # ---- 4. 模拟"已安装"两条，让状态角标能同时看到 已装 / 未装 ----
    from wmm import installer
    from wmm.modstore import ModFile, ModMeta

    lib = library_mod.Library.load()
    all_metas = store.all()
    for meta in all_metas[:2]:
        lib.record_install(
            library_mod.InstalledMod(
                mod_id=meta.id,
                mod_name=meta.name,
                game_dir=str(probe.game_root),
                platform=probe.platform,
                installed_at="2026-10-09 21:30:00",
                deployed_files=[
                    library_mod.DeployedFile(
                        src=str(store.files_dir(meta.id) / f.name),
                        dst=str(Path(probe.paks_dir) / "~mods" / f.name),
                        original_name=f.name,
                    )
                    for f in meta.files
                ],
            )
        )
    lib.save()

    # ---- 4b. 造一对**真冲突**的 mod 并真的装进去，验证角标 / 冲突计数 / 谁赢 ----
    data_root = Path(os.environ["WMM_DATA_DIR"])
    clash_specs = [
        ("c001", "夜行服装包 v1.0", 100,
         ["/Game/Characters/Player/SK_Wuchang_Body.uasset",
          "/Game/Characters/Player/SK_Wuchang_Head.uasset"]),
        ("c002", "夜行服装包 v2.0 重制", 90000,
         ["/Game/Characters/Player/SK_Wuchang_Body.uasset"]),
    ]
    for mod_id, name, chunk, overrides in clash_specs:
        meta = ModMeta(
            id=mod_id,
            name=name,
            content_hash=mod_id * 4,
            overrides=overrides,
            files=[ModFile(name=f"{mod_id}_P.pak", rel_path=f"{mod_id}_P.pak")],
        )
        store.save(meta)
        fdir = store.files_dir(mod_id)
        fdir.mkdir(parents=True, exist_ok=True)
        payload = b"MOCK-PAK" + mod_id.encode()
        (fdir / f"{mod_id}_P.pak").write_bytes(payload)
        meta.files = [ModFile(name=f"{mod_id}_P.pak", rel_path=f"{mod_id}_P.pak",
                              size=len(payload), sha256="x")]
        store.save(meta)
        res = installer.install(meta, probe, store, lib, chunk_id=chunk,
                                backup_root=data_root / "backups")
        print(f"冲突样例「{name}」安装：{res.message}")
        if res.warnings:
            for w in res.warnings:
                print(f"   注意：{w}")

    from wmm import conflicts

    report = conflicts.detect(store, lib, probe)
    print("冲突检测：", report.summary())
    for line in report.lines(limit=6):
        print("   ", line)

    print(f"库里共 {store.count()} 个 mod")

    # ---- 5. 开窗截图 ----
    from wmm.ui.dialogs import AboutDialog
    from wmm.ui.main_window import MainWindow

    window = MainWindow(app_settings, store, lib)
    window.resize(1180, 800)
    window.show()
    app.processEvents()
    for _ in range(6):
        app.processEvents()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shot = OUT_DIR / "main-window.png"
    window.grab().save(str(shot))
    print(f"主界面截图：{shot}  ({shot.stat().st_size // 1024} KB)")

    about = AboutDialog(window)
    # 刻意不 resize —— 截的就是用户实际看到的默认尺寸，
    # 否则「充电卡片是否落在首屏」这条根本测不出来。
    about.show()
    app.processEvents()
    about_shot = OUT_DIR / "about-page.png"
    about.grab().save(str(about_shot))
    print(f"关于与支持截图：{about_shot}  ({about_shot.stat().st_size // 1024} KB)")

    # ---- 5b. 启动弹窗截图 ----
    from wmm.ui.splash import SplashDialog

    splash = SplashDialog()
    splash.show()
    app.processEvents()
    splash_shot = OUT_DIR / "splash.png"
    splash.grab().save(str(splash_shot))
    print(f"启动弹窗截图：{splash_shot}  ({splash_shot.stat().st_size // 1024} KB)")
    splash.close()

    # ---- 6. 空状态截图（另起一个空库）----
    empty_store = modstore.ModStore(data_dir / "empty-mods")
    empty_store.ensure()
    empty_lib = library_mod.Library.load(data_dir / "empty-library.json")
    empty_settings = settings_mod.Settings.load(data_dir / "empty-settings.json")
    empty_window = MainWindow(empty_settings, empty_store, empty_lib)
    empty_window.resize(1180, 800)
    empty_window.show()
    app.processEvents()
    empty_shot = OUT_DIR / "empty-state.png"
    empty_window.grab().save(str(empty_shot))
    print(f"空状态截图：{empty_shot}  ({empty_shot.stat().st_size // 1024} KB)")

    print(f"\n临时数据目录（会自动留在临时目录，不会进 %APPDATA%）：{data_dir}")
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
