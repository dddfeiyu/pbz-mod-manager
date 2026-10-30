# -*- coding: utf-8 -*-
"""地基层冒烟测试（不依赖 PySide6，纯标准库）。

跑法：
    python tools/smoke.py

它做的事＝「地基」这一层的验收：
  1. 数据目录策略：证明可变数据不落在程序目录下
  2. 伪造游戏目录（四种互相矛盾的写法）逐个校验 -> 期望结论
  3. 记住游戏目录 -> 存 settings.json -> 读回
  4. 记一条安装 -> 算出卸载清单 -> 存 library.json -> 读回
  5. 损坏的 settings.json 能否容错（不该让程序起不来）

注意：全程写在一个临时数据目录里（WMM_DATA_DIR），不碰你真实的 %APPDATA%。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.make_fake_game import build as build_fake_game  # noqa: E402

from wmm import apppaths, gamedir, library, settings, sysreq, version  # noqa: E402

OK = "[ OK ]"
BAD = "[FAIL]"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wmm-smoke-"))
    os.environ[apppaths.ENV_DATA_DIR_OVERRIDE] = str(tmp / "appdata")
    failures: list[str] = []

    def check(cond: bool, label: str, extra: str = "") -> None:
        print(f"{OK if cond else BAD} {label}" + (f"  -- {extra}" if extra else ""))
        if not cond:
            failures.append(label)

    print("=" * 72)
    print(f"{version.APP_NAME_ZH}  v{version.VERSION}  ({version.BUILD_DATE})")
    print(f"作者：{version.AUTHOR_ID}")
    print("=" * 72)

    print("\n[1] 路径策略（程序目录只读 / 数据写 %APPDATA%）")
    print("    " + apppaths.describe().replace("\n", "\n    "))
    check(not apppaths.is_in_data_dir(apppaths.program_dir()), "数据目录不在程序目录内")
    check(apppaths.is_in_program_dir(apppaths.program_dir() / "settings.json"), "能识别程序目录内的路径")
    try:
        apppaths.assert_writable_target(apppaths.program_dir() / "settings.json")
        check(False, "往程序目录写应被拒绝")
    except apppaths.ProgramDirWriteError:
        check(True, "往程序目录写被正确拒绝")
    apppaths.ensure_data_dirs()
    check(apppaths.data_dir().is_dir(), "数据目录已创建")

    print("\n[2] 系统要求检测")
    req = sysreq.check_system()
    print("    " + req.message.replace("\n", " ") if req.message else "    通过")
    for line in req.details:
        print(f"    - {line}")
    check(True, "系统要求检测未抛异常")

    print("\n[3] 游戏目录校验（四种互相矛盾的真实写法）")
    fake = tmp / "fakegames"
    made = build_fake_game(fake)
    expectations = {
        "steam": (gamedir.PLATFORM_STEAM, True),
        "gamepass_a": (gamedir.PLATFORM_GAMEPASS, True),
        "gamepass_b_guide_sb": (gamedir.PLATFORM_GAMEPASS, True),
        "gamepass_c_nested": (gamedir.PLATFORM_GAMEPASS, True),
    }
    good_probe = None
    for name, (want_platform, want_ok) in expectations.items():
        probe = gamedir.validate_game_dir(made[name])
        got = f"{probe.platform} ok={probe.ok} 规则={'/'.join(probe.matched_rule.split('/'))}"
        label = f"{name} -> 平台={want_platform} 通行={want_ok}"
        check(probe.ok == want_ok and probe.platform == want_platform, label, got)
        if name == "steam":
            good_probe = probe
    # 非游戏目录必须被拒绝
    bad_probe = gamedir.validate_game_dir(made["__not_game__"])
    check(not bad_probe.ok, "无关目录被拒绝（不把普通文件夹当游戏）")

    print("\n[4] 安装目标目录与命名规则")
    if good_probe is not None and good_probe.paks_dir is not None:
        steam_mods = gamedir.resolve_mods_dir(gamedir.PLATFORM_STEAM, good_probe.paks_dir)
        gp_mods = gamedir.resolve_mods_dir(gamedir.PLATFORM_GAMEPASS, good_probe.paks_dir)
        check(steam_mods.name == "~mods", "Steam 目标目录 = <Paks>\\~mods", str(steam_mods))
        check(
            gamedir.same_path(gp_mods, good_probe.paks_dir),
            "Game Pass 目标目录 = 主 Paks（不建子目录）",
            str(gp_mods),
        )
    check(
        gamedir.required_pak_name(gamedir.PLATFORM_STEAM, "街头少女风 蚰蜒套").endswith("_P.pak"),
        "Steam 命名以 _P.pak 结尾",
        gamedir.required_pak_name(gamedir.PLATFORM_STEAM, "街头少女风 蚰蜒套"),
    )
    check(
        gamedir.required_pak_name(gamedir.PLATFORM_GAMEPASS, "任意名") == "Project_Plague-WinGDK_4_P.pak",
        "Game Pass 命名精确为 Project_Plague-WinGDK_4_P.pak",
        gamedir.required_pak_name(gamedir.PLATFORM_GAMEPASS, "任意名"),
    )
    # 实测里的脏样本：24% 不满足命名规则，还有带括号的
    dirty = [
        "BaiNude_Flat_Clean_p(under.pak",
        "pakchunk1200-Windows.pak",
        "TK_WuChang_DYY-StreetStyle1511_P.pak",
        "Project_Plague-Windows_510002_P.pak",
    ]
    for raw in dirty:
        cleaned = gamedir.sanitize_mod_name(raw)
        stem = gamedir.safe_mod_stem(raw)
        check(
            bool(stem) and not any(c in stem for c in '<>:"/\\|?*'),
            f"清洗脏文件名：{raw}",
            f"清洗结果={cleaned!r} 落库名={stem!r}",
        )
    check(
        gamedir.sanitize_mod_name("pakchunk1200-Windows.pak") == "",
        "无信息量的文件名返回空串（交给推导链下一级：目录名 -> readme -> 编号占位）",
    )

    print("\n[5] settings.json 往返")
    st = settings.Settings.load()
    check(st.show_splash is True, "默认开启启动弹窗（F-10.5b）")
    if good_probe is not None:
        entry = st.remember_game_dir(good_probe, label="测试机")
        check(st.active_game_dir is not None, "记住了游戏目录并设为当前", entry.path)
        st.add_scan_dir(tmp / "下载")
        st.save()
        st2 = settings.Settings.load()
        check(len(st2.game_dirs) == 1, "重新读回游戏目录列表")
        check(st2.active_entry is not None and st2.active_entry.platform == gamedir.PLATFORM_STEAM,
              "当前目录的平台被正确持久化")
        check(st2.scan_dirs, "扫描目录被持久化")

    print("\n[6] library.json 安装记录与卸载清单")
    lib = library.Library.load()
    if good_probe is not None and st.active_game_dir is not None:
        target_dir = gamedir.resolve_mods_dir(gamedir.PLATFORM_STEAM, good_probe.paks_dir)
        dst = target_dir / "街头少女风 蚰蜒套_P.pak"
        lib.record_install(
            library.InstalledMod(
                mod_id="deadbeefcafe0001",
                mod_name="街头少女风 蚰蜒套",
                game_dir=str(st.active_game_dir),
                platform=gamedir.PLATFORM_STEAM,
                deployed_files=[
                    library.DeployedFile(
                        src=str(tmp / "mods/x/files/a.pak"),
                        dst=str(dst),
                        original_name="TK_WuChang_DYY-StreetStyle1511_P.pak",
                    )
                ],
            )
        )
        lib.save()
        lib2 = library.Library.load()
        plan = lib2.uninstall_plan("deadbeefcafe0001", st.active_game_dir)
        check(len(plan) == 1 and gamedir.same_path(plan[0], dst), "卸载清单精确指向部署落点", str(plan[0] if plan else ""))
        check(lib2.is_installed("deadbeefcafe0001", st.active_game_dir), "已装状态可查询")
        missing = lib2.missing_files("deadbeefcafe0001", st.active_game_dir)
        check(len(missing) == 1, "能报告「记录里有、磁盘上已不在」的文件")

    print("\n[7] 坏配置文件容错（不该让程序起不来）")
    bad_dir = tmp / "appdata-broken"
    bad_dir.mkdir(parents=True, exist_ok=True)
    bad_settings = bad_dir / "settings.json"
    bad_settings.write_text('{"schema_version": 1, "game_dirs": [', encoding="utf-8")
    st3 = settings.Settings.load(bad_settings)
    check(st3.show_splash is True, "半截 JSON 回落到默认值")
    quarantined = list(bad_dir.glob("settings.json.broken-*"))
    check(len(quarantined) == 1, "坏文件被改名留档（不是删除）", quarantined[0].name if quarantined else "")

    print("\n" + "=" * 72)
    if failures:
        print(f"{BAD} 共 {len(failures)} 项未通过：")
        for f in failures:
            print("   -", f)
    else:
        print(f"{OK} 地基层冒烟测试全部通过")
    print(f"（临时目录：{tmp}）")
    print("=" * 72)
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
