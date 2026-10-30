# -*- coding: utf-8 -*-
"""《影之刃零》配置的端到端冒烟（假游戏目录，不需要游戏本体）。

验证的是「一套内核 + 换配置」这条路走不走得通：
  · 用 `PkGame/Content/Paks` 骨架能被 `validate_game_dir()` 认出
  · mod 目标目录落在 `PkGame/Content/Paks/~mods`
  · 安装 → 文件真的出现在 ~mods；卸载 → 文件被删干净、备份目录正确清理
  · `enabler.detect()` 判「无需前置」，不会误导用户去装东西

⚠️ 必须在**子进程**里跑（`WMM_GAME` 只在 import 时读一次）。

用法：WMM_GAME=phantom_blade .venv\\Scripts\\python.exe tools/probe_phantom.py
     或直接 python tools/probe_phantom.py（脚本内部会自己设好）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 关键：必须在任何 wmm 导入之前设定
os.environ["WMM_GAME"] = "phantom_blade"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="pbz-smoke-"))
os.environ["WMM_DATA_DIR"] = str(TMP / "data")

from wmm import enabler, gamedir, installer, library as library_mod, modstore  # noqa: E402
from wmm.modstore import ModFile, ModMeta  # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def main() -> int:
    # 按真实 Steam 布局造：<根>/steamapps/common/Phantom Blade Zero/PkGame/...
    steam = TMP / "steam"
    game = steam / "steamapps" / "common" / "PBZ"      # 真实 Steam 目录名就叫 PBZ
    paks = game / "ProjectPBZ" / "Content" / "Paks"
    paks.mkdir(parents=True)
    (steam / "steamapps" / "appmanifest_000000.acf").write_text("", encoding="utf-8")
    (paks / "ProjectPBZ-Windows.pak").write_bytes(b"VANILLA")   # 本体 pak 占位
    (game / "platform" / "steam").mkdir(parents=True)
    (game / "platform" / "steam" / "PBZ.exe").write_bytes(b"MZ")
    # 前置检测要看 Binaries\Win64 在不在（真实游戏里存在），造出来才走到
    # 「未装前置」的正确分支，而不是「找不到 Win64 目录」
    (game / "ProjectPBZ" / "Binaries" / "Win64").mkdir(parents=True)

    print("=== 1) 目录校验 ===")
    probe = gamedir.validate_game_dir(game)
    check("ProjectPBZ 骨架被认作游戏根目录", probe.ok, probe.matched_rule)
    check("pak 目录 = ProjectPBZ/Content/Paks", probe.paks_dir == paks, str(probe.paks_dir))
    check("平台识别为 Steam", probe.platform == gamedir.PLATFORM_STEAM, probe.platform)

    print("\n=== 2) mod 目标目录 ===")
    mods_dir = gamedir.resolve_mods_dir(probe.platform, paks)
    check(
        "Steam/Epic 落到 ~mods",
        mods_dir == paks / "~mods" or probe.platform == gamedir.PLATFORM_GAMEPASS,
        f"{probe.platform} → {mods_dir}",
    )

    print("\n=== 3) 前置加载器判定 ===")
    # 2026-10-29 实测修正：影之刃零**需要**签名绕过前置（dsound.dll + .asi）
    st = enabler.detect(game)
    check("未装前置时判为「未就绪」", not st.installed, st.message.splitlines()[0] if st.message else "")
    check("提示里点名本作的前置文件", "UniversalSigBypasser" in (st.message or ""),
          (st.message or "")[:70])

    print("\n=== 4) 安装三件套 → 卸载 ===")
    store = modstore.ModStore(TMP / "mods")
    store.ensure()
    lib = library_mod.Library.load(TMP / "library.json")
    meta = ModMeta(id="pbz001", name="测试皮肤", content_hash="ab" * 8, files=[])
    store.save(meta)
    fd = store.files_dir(meta.id)
    fd.mkdir(parents=True, exist_ok=True)
    # 真实 mod 的形态：同名三件套（IoStore）
    trio = {}
    for name in ("phantombladezero_test_P.pak", "phantombladezero_test_P.utoc",
                 "phantombladezero_test_P.ucas"):
        f = fd / name
        f.write_bytes(b"PBZ-" + name.encode("utf-8"))
        trio[name] = f
    meta.files = [
        ModFile(name=n, rel_path=n, size=f.stat().st_size, sha256=modstore.hash_file(f))
        for n, f in trio.items()
    ]
    store.save(meta)

    res = installer.install(meta, probe, store, lib, backup_root=TMP / "backups")
    check("安装成功", res.ok, res.message)
    installed = {p.name for p in mods_dir.iterdir() if p.is_file()}
    check("三个文件都进了 ~mods", len(installed) == 3, str(sorted(installed)))
    check(
        "文件名保持原样（三件套不能改名）",
        installed == set(trio),
        f"实际={sorted(installed)}",
    )

    res2 = installer.uninstall(lib, meta.id, str(probe.game_root), same_as_lookup=None)
    check("卸载成功", res2.ok, res2.message)
    left = {p.name for p in mods_dir.iterdir() if p.is_file()}
    check("三个文件全删干净", not left, str(sorted(left)))
    # 2026-10-29 用户反馈：卸载把 ~mods 文件夹本身也删了 → 现在必须保留
    check("~mods 目录保留（不连同文件夹一起删）", mods_dir.is_dir(), str(mods_dir))

    print("\n=== 结果 ===")
    if fails:
        print(f"失败 {len(fails)} 项：" + "、".join(fails))
        return 1
    print("PHANTOM_PROFILE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
