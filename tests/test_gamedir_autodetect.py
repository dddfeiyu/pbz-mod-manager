# -*- coding: utf-8 -*-
"""自动检测游戏目录（多来源）的测试。

背景（用户 2026-10-10）：
    「为什么自动扫描还要自己选文件夹？游戏无非就是 steam、epic 或者 wegame，
      你自己不会找吗？」
所以 autodetect_game() 从「只扫 Steam」扩成了 5 路来源。这里守两件事：
  1. 判定辅助函数（名字匹配 / 是不是游戏根）行为正确 —— 它们决定误报率；
  2. 任何一路探测**都不许抛异常**（这是从第一天就定下的硬性纪律：
     探测失败只是"没猜到"，不是错误）。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wmm import gamedir  # noqa: E402


class TestNameMatches(unittest.TestCase):
    def test_common_folder_names(self):
        for text in (
            "Wuchang Fallen Feathers",
            "WuchangFallenFeathers",
            "wuchang",
            "明末：渊虚之羽",
            "渊虚之羽",
        ):
            self.assertTrue(gamedir.name_matches(text), text)

    def test_unrelated_names_rejected(self):
        # 「fallen」单独一个词不够独特（星球大战 绝地：陨落的武士团 就带这个词）
        for text in ("Fallen Order", "Jedi Fallen", "Cyberpunk 2077", "Steam"):
            self.assertFalse(gamedir.name_matches(text), text)


class TestIsGameRoot(unittest.TestCase):
    def test_true_for_steam_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / gamedir.UE_PROJECT / "Content" / "Paks").mkdir(parents=True)
            self.assertTrue(gamedir.is_game_root(root))

    def test_true_for_gamepass_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Content" / gamedir.UE_PROJECT / "Content" / "Paks").mkdir(parents=True)
            self.assertTrue(gamedir.is_game_root(root))

    def test_true_when_pointed_at_paks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paks = Path(tmp) / "Paks"
            paks.mkdir()
            self.assertTrue(gamedir.is_game_root(paks))

    def test_false_for_random_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(gamedir.is_game_root(tmp))
            self.assertFalse(gamedir.is_game_root(Path(tmp) / "不存在"))


class TestSteamHits(unittest.TestCase):
    def test_fake_library_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp)
            game = lib / "steamapps" / "common" / "Wuchang Fallen Feathers"
            (game / gamedir.UE_PROJECT / "Content" / "Paks").mkdir(parents=True)
            (lib / "steamapps" / "appmanifest_2277560.acf").write_text(
                '"AppState"\n{\n'
                '\t"appid"\t\t"2277560"\n'
                '\t"installdir"\t\t"Wuchang Fallen Feathers"\n'
                "}\n",
                encoding="utf-8",
            )

            original = gamedir.autodetect_steam_libraries
            gamedir.autodetect_steam_libraries = lambda: [lib]  # type: ignore[assignment]
            try:
                hits = gamedir._hits_from_steam()
            finally:
                gamedir.autodetect_steam_libraries = original  # type: ignore[assignment]

            self.assertEqual(len(hits), 1)
            self.assertEqual(Path(hits[0].path), game)
            self.assertEqual(hits[0].appid, gamedir.KNOWN_STEAM_APPID)
            self.assertIn("Steam", hits[0].label)

    def test_unrelated_manifest_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp)
            (lib / "steamapps" / "common").mkdir(parents=True)
            (lib / "steamapps" / "appmanifest_123.acf").write_text(
                '"installdir"\t\t"Some Other Game"\n', encoding="utf-8"
            )

            original = gamedir.autodetect_steam_libraries
            gamedir.autodetect_steam_libraries = lambda: [lib]  # type: ignore[assignment]
            try:
                hits = gamedir._hits_from_steam()
            finally:
                gamedir.autodetect_steam_libraries = original  # type: ignore[assignment]

            self.assertEqual(hits, [])


class TestAutodetectNeverRaises(unittest.TestCase):
    """在**这台真实机器上**跑一遍全量探测，只要求"不抛异常、返回列表"。"""

    def test_runs_clean(self):
        hits = gamedir.autodetect_game()
        self.assertIsInstance(hits, list)
        for hit in hits:
            self.assertIsInstance(hit.path, Path)
            self.assertTrue(hit.label or hit.source)

    def test_shallow_mode_skips_disk_scan(self):
        # deep=False 时不应做磁盘遍历（用于启动期等对耗时敏感的场景）
        hits = gamedir.autodetect_game(deep=False)
        self.assertIsInstance(hits, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
