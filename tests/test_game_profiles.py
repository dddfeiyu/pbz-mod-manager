# -*- coding: utf-8 -*-
"""游戏配置层的回归测试（2026-10-27 为《影之刃零》做的通用化）。

守住两件事：
  1. **明末的配置值不许漂移** —— `%APPDATA%\\WuchangModManager`、UE 工程名
     `Project_Plague`、AppID 2277560、识别词表、Enabler 判据，任何一个变了
     都会让线上用户的数据/安装记录失效。
  2. **影之刃零配置能跑通整条链路** —— 用假 `PkGame` 目录走
     「校验目录 → 落 ~mods → 安装 → 卸载」，在子进程里跑（WMM_GAME 只在
     import 时读一次，同进程切不了）。
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wmm.games import wuchang  # noqa: E402
from wmm import gamedir
from wmm import apppaths


class TestWuchangProfileFrozen(unittest.TestCase):
    """明末配置的「不许动」清单。

    ⚠️ 这里必须写字面量、不能读 gamedir / apppaths —— 因为这两个模块反映的是
    **当前激活的游戏**（本项目 = 影之刃零）。用它们当期望值等于拿自己跟自己比，
    明末那份配置被改坏了也测不出来。
    """

    def test_identity(self):
        p = wuchang.PROFILE
        self.assertEqual(p.key, "wuchang")
        self.assertEqual(p.data_dir_name, "WuchangModManager")   # 线上用户数据目录
        self.assertEqual(p.ue_project, "Project_Plague")
        self.assertEqual(p.steam_appid, "2277560")
        self.assertEqual(p.app_name_zh, "明末MOD管理器")
        self.assertEqual(p.app_name_en, "WuchangModManager")

    def test_detect_words(self):
        # 别放单独的 "fallen"（会撞星战绝地）
        self.assertEqual(
            wuchang.PROFILE.identify_words,
            ("wuchang", "fallenfeather", "明末", "渊虚"),
        )
        self.assertNotIn("fallen", wuchang.PROFILE.identify_words)

    def test_enabler_rules(self):
        p = wuchang.PROFILE
        self.assertTrue(p.needs_enabler)
        self.assertEqual(p.enabler_dll_names, ("d3d9.dll", "dsound.dll"))
        self.assertEqual(p.enabler_marker, "bitfix/sig.lua")

    def test_mods_folder(self):
        self.assertEqual(wuchang.PROFILE.mods_folder, "~mods")


class TestPhantomBladeProfile(unittest.TestCase):
    def test_profile_identity(self):
        from wmm.games import phantom_blade

        p = phantom_blade.PROFILE
        # 2026-10-29 实测确认：UE 工程名是 ProjectPBZ（网上流传的 PkGame 是错的）
        self.assertEqual(p.ue_project, "ProjectPBZ")
        self.assertEqual(p.steam_appid, "4115450")
        self.assertEqual(p.mods_folder, "~mods")
        self.assertEqual(p.data_dir_name, "PhantomBladeZeroManager")
        # 已实测不需要前置加载器；三件套必须保留原名
        self.assertFalse(p.needs_enabler)
        self.assertTrue(p.preserve_filenames)
        # 与明末完全独立：两个管理器可以同机共存
        self.assertNotEqual(p.data_dir_name, wuchang.PROFILE.data_dir_name)

    def test_end_to_end_smoke(self):
        """子进程跑端到端：假 PkGame 目录 → 安装 → 卸载。"""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "probe_phantom.py")],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(ROOT),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, f"probe_phantom 退出码 {proc.returncode}\n{out}")
        self.assertIn("PHANTOM_PROFILE_OK", out, out)


if __name__ == "__main__":
    unittest.main()
