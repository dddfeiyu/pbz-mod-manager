# -*- coding: utf-8 -*-
"""地基层回归测试（纯标准库，不需要 pytest）。

跑法：
    python -m unittest discover -s tests -v
或：
    python tests\\test_core.py
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wmm import apppaths, gamedir, jsonstore, library, settings, sysreq
from tools.make_fake_game import build as build_fake_game


class TestPathPolicy(unittest.TestCase):
    """需求文档第 4 章硬约束：程序目录只读，数据写 %APPDATA%。"""

    def test_data_dir_is_not_inside_program_dir(self):
        self.assertFalse(apppaths.is_in_data_dir(apppaths.program_dir()))
        self.assertFalse(apppaths.is_in_program_dir(apppaths.data_dir()))

    def test_write_into_program_dir_is_refused(self):
        target = apppaths.program_dir() / "settings.json"
        with self.assertRaises(apppaths.ProgramDirWriteError):
            apppaths.assert_writable_target(target)

    def test_write_into_data_dir_is_allowed(self):
        apppaths.assert_writable_target(apppaths.data_dir() / "settings.json")

    def test_jsonstore_refuses_program_dir(self):
        with self.assertRaises(apppaths.ProgramDirWriteError):
            jsonstore.save_json(apppaths.program_dir() / "library.json", {})


class TestJsonStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip_utf8(self):
        p = self.dir / "a.json"
        payload = {"名字": "街头少女风 蚰蜒套", "路径": r"D:\游戏\Project_Plague"}
        jsonstore.save_json(p, payload)
        self.assertEqual(jsonstore.load_json(p, default=None), payload)
        # 必须是 UTF-8 且不写 BOM（需求文档 R-21 / F-11.16）
        raw = p.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn("街头少女风".encode("utf-8"), raw)

    def test_missing_file_returns_default(self):
        self.assertEqual(jsonstore.load_json(self.dir / "none.json", default={"x": 1}), {"x": 1})

    def test_broken_file_is_quarantined_not_deleted(self):
        p = self.dir / "s.json"
        p.write_text('{"a": ', encoding="utf-8")
        self.assertEqual(jsonstore.load_json(p, default="fallback"), "fallback")
        self.assertFalse(p.exists())
        self.assertEqual(len(list(self.dir.glob("s.json.broken-*"))), 1)

    def test_schema_validator_rejects_wrong_shape(self):
        p = self.dir / "s.json"
        jsonstore.save_json(p, {"totally": "wrong"})
        got = jsonstore.load_json(p, default="fallback", validator=lambda d: isinstance(d, dict) and "installed" in d)
        self.assertEqual(got, "fallback")


class TestSettings(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "settings.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults(self):
        st = settings.Settings.load(self.path)
        self.assertTrue(st.show_splash)          # F-10.5b 默认 true
        self.assertEqual(st.game_dirs, [])
        self.assertIsNone(st.active_game_dir)

    def test_remember_and_forget_game_dir(self):
        st = settings.Settings.load(self.path)
        probe = gamedir.ProbeResult(
            input_path=Path("X:/fake"),
            game_root=Path(r"C:\Games\Wuchang Fallen Feathers"),
            paks_dir=Path(r"C:\Games\Wuchang Fallen Feathers\Project_Plague\Content\Paks"),
            matched_rule="Project_Plague/Content/Paks",
            platform=gamedir.PLATFORM_STEAM,
        )
        entry = st.remember_game_dir(probe, label="主力机")
        self.assertEqual(entry.platform, gamedir.PLATFORM_STEAM)
        self.assertTrue(str(entry.mods_dir).endswith("~mods"))
        self.assertIsNotNone(st.active_game_dir)

        # 重复记住同一路径 -> 就地更新，不重复添加
        st.remember_game_dir(probe, label="改名了")
        self.assertEqual(len(st.game_dirs), 1)
        self.assertEqual(st.game_dirs[0].label, "改名了")

        st.save()
        st2 = settings.Settings.load(self.path)
        self.assertEqual(len(st2.game_dirs), 1)
        self.assertIsNotNone(st2.active_entry)

        self.assertTrue(st2.forget_game_dir(entry.path))
        self.assertIsNone(st2.active_game_dir)

    def test_remember_rejects_failed_probe(self):
        st = settings.Settings.load(self.path)
        with self.assertRaises(ValueError):
            st.remember_game_dir(gamedir.ProbeResult(input_path=Path("X:/nope")))


class TestLibrary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.lib_path = self.dir / "library.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_uninstall_plan_uses_recorded_dst_only(self):
        lib = library.Library.load(self.lib_path)
        game = self.dir / "game"
        dst = game / "Project_Plague/Content/Paks/~mods/新名字_P.pak"
        lib.record_install(
            library.InstalledMod(
                mod_id="id1",
                mod_name="测试",
                game_dir=str(game),
                platform=gamedir.PLATFORM_STEAM,
                deployed_files=[
                    library.DeployedFile(src=str(self.dir / "src/a.pak"), dst=str(dst), original_name="a.pak"),
                    library.DeployedFile(src=str(self.dir / "src/a.ucas"), dst=str(dst.with_suffix(".ucas")), original_name="a.ucas"),
                ],
            )
        )
        lib.save()
        lib2 = library.Library.load(self.lib_path)
        plan = lib2.uninstall_plan("id1", game)
        self.assertEqual(len(plan), 2)
        self.assertEqual({p.name for p in plan}, {"新名字_P.pak", "新名字_P.ucas"})
        self.assertTrue(lib2.is_installed("id1", game))
        # 缺文件应被报告，且不抛异常
        self.assertEqual(len(lib2.missing_files("id1", game)), 2)

    def test_path_comparison_is_case_insensitive(self):
        lib = library.Library.load(self.lib_path)
        lib.record_install(
            library.InstalledMod(mod_id="id2", game_dir=r"C:\Games\Wuchang", platform=gamedir.PLATFORM_STEAM)
        )
        self.assertTrue(lib.is_installed("id2", r"c:\games\wuchang"))

    def test_record_replaces_same_mod_and_game(self):
        lib = library.Library.load(self.lib_path)
        game = self.dir / "g"
        for name in ("第一版", "第二版"):
            lib.record_install(
                library.InstalledMod(mod_id="same", mod_name=name, game_dir=str(game), platform=gamedir.PLATFORM_STEAM)
            )
        self.assertEqual(len(lib.for_game(game)), 1)
        self.assertEqual(lib.find("same", game).mod_name, "第二版")


class TestGameDir(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.made = build_fake_game(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_all_four_layouts_are_recognised(self):
        expected = {
            "steam": gamedir.PLATFORM_STEAM,
            "gamepass_a": gamedir.PLATFORM_GAMEPASS,
            "gamepass_b_guide_sb": gamedir.PLATFORM_GAMEPASS,
            "gamepass_c_nested": gamedir.PLATFORM_GAMEPASS,
        }
        for name, platform in expected.items():
            with self.subTest(layout=name):
                probe = gamedir.validate_game_dir(self.made[name])
                self.assertTrue(probe.ok, probe.warnings)
                self.assertEqual(probe.platform, platform)
                self.assertIsNotNone(probe.paks_dir)

    def test_sb_rule_reports_sb(self):
        probe = gamedir.validate_game_dir(self.made["gamepass_b_guide_sb"])
        self.assertEqual(probe.matched_rule, "SB/Content/Paks")

    def test_unrelated_folder_is_rejected(self):
        probe = gamedir.validate_game_dir(self.made["__not_game__"])
        self.assertFalse(probe.ok)
        self.assertTrue(probe.warnings)

    def test_pointing_at_mods_dir_climbs_up(self):
        probe = gamedir.validate_game_dir(Path(self.made["steam"]) / "Project_Plague/Content/Paks/~mods")
        self.assertTrue(probe.ok)
        self.assertEqual(probe.paks_dir.name, "Paks")
        self.assertTrue(any("上溯" in w for w in probe.warnings))

    def test_pointing_at_paks_directly(self):
        probe = gamedir.validate_game_dir(Path(self.made["steam"]) / "Project_Plague/Content/Paks")
        self.assertTrue(probe.ok)
        self.assertEqual(probe.paks_dir.name, "Paks")

    def test_nonexistent_path(self):
        probe = gamedir.validate_game_dir(self._tmp.name + "/definitely-missing")
        self.assertFalse(probe.ok)
        self.assertTrue(probe.warnings)

    def test_mods_dir_by_platform(self):
        paks = Path(r"C:\G\Project_Plague\Content\Paks")
        self.assertEqual(gamedir.resolve_mods_dir(gamedir.PLATFORM_STEAM, paks).name, "~mods")
        self.assertEqual(gamedir.resolve_mods_dir(gamedir.PLATFORM_EPIC, paks).name, "~mods")
        # Game Pass：直接放主 Paks，禁止子目录
        self.assertEqual(gamedir.resolve_mods_dir(gamedir.PLATFORM_GAMEPASS, paks), paks)


class TestNaming(unittest.TestCase):
    def test_steam_name_ends_with_p(self):
        self.assertTrue(gamedir.required_pak_name(gamedir.PLATFORM_STEAM, "街头少女风 蚰蜒套").endswith("_P.pak"))

    def test_gamepass_name_is_exact(self):
        for raw in ("任意名", "另一个名字", ""):
            self.assertEqual(
                gamedir.required_pak_name(gamedir.PLATFORM_GAMEPASS, raw), "Project_Plague-WinGDK_4_P.pak"
            )

    def test_gamepass_chunk_id_is_honoured(self):
        self.assertEqual(
            gamedir.required_pak_name(gamedir.PLATFORM_GAMEPASS, "x", chunk_id=9001),
            "Project_Plague-WinGDK_9001_P.pak",
        )

    def test_sanitize_strips_noise(self):
        self.assertEqual(gamedir.sanitize_mod_name("pakchunk1400-Windows_P.pak"), "")
        self.assertEqual(gamedir.sanitize_mod_name("Project_Plague-WinGDK_9001_P.pak"), "")
        self.assertIn("街头少女", gamedir.sanitize_mod_name("街头少女风 蚰蜒套"))

    def test_sanitize_keeps_version_number(self):
        """实测踩过的坑：`无摔伤补丁 v1.2` 曾被截成 `无摔伤补丁 v1`
        —— `os.path.splitext` 把 `.2` 当成扩展名扔了。"""
        self.assertEqual(gamedir.sanitize_mod_name("无摔伤补丁 v1.2"), "无摔伤补丁 v1.2")
        self.assertEqual(gamedir.sanitize_mod_name("补丁 v2.0.1"), "补丁 v2.0.1")
        # 真扩展名仍然要剥掉
        self.assertEqual(gamedir.sanitize_mod_name("街头少女风.zip"), "街头少女风")
        # 名称推导链走的路径（调用方已剥过扩展名）不受影响
        self.assertEqual(
            gamedir.sanitize_mod_name("无摔伤补丁 v1.2", strip_extension=False), "无摔伤补丁 v1.2"
        )

    def test_sanitize_never_returns_illegal_chars(self):
        for raw in (
            'bad:name?.pak',
            "BaiNude_Flat_Clean_p(under.pak",
            "a/b\\c.pak",
            "   ",
            "路*径<>.pak",
        ):
            with self.subTest(raw=raw):
                stem = gamedir.safe_mod_stem(raw)
                self.assertTrue(stem)
                self.assertFalse([c for c in stem if c in '<>:"/\\|?*'])

    def test_fallback_name(self):
        self.assertEqual(gamedir.safe_mod_stem("pakchunk1200-Windows.pak"), gamedir.FALLBACK_MOD_NAME)


class TestSysReq(unittest.TestCase):
    def test_check_does_not_raise(self):
        res = sysreq.check_system()
        self.assertIsInstance(res.ok, bool)
        self.assertTrue(res.details)

    def test_official_requirement_text_present(self):
        self.assertIn("64", sysreq.SUPPORTED_DESC)
        self.assertIn("1809", sysreq.SUPPORTED_DESC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
