# -*- coding: utf-8 -*-
"""卡片视图模型回归测试（需求文档 3.1 / 3.6）。

这一层决定"卡片上显示什么"：状态角标、筛选、排序、预览图降级。
它不依赖 Qt，所以这些最容易出错的判断都能在无界面环境下测。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wmm import catalog, library as library_mod, modstore, preview
from wmm.modstore import ModFile, ModMeta  # noqa: F401  (ModFile 在用例里直接用)


def make_meta(mod_id: str, name: str, *, files: int = 1, subdir: str = "~mods", size: int = 1024) -> ModMeta:
    return ModMeta(
        id=mod_id,
        name=name,
        content_hash=mod_id * 4,
        install_subdir=subdir,
        files=[
            ModFile(name=f"f{i}_P.pak", rel_path=f"f{i}_P.pak", size=size, sha256=f"{i:04d}")
            for i in range(files)
        ],
    )


class TestBuildViews(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = modstore.ModStore(self.root / "mods")
        self.store.ensure()
        self.lib = library_mod.Library.load(self.root / "library.json")
        self.game = self.root / "game"

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_store_gives_no_views(self):
        self.assertEqual(catalog.build_views(self.store, self.lib, game_dir=self.game), [])

    def test_all_not_installed_without_library_records(self):
        self.store.save(make_meta("aaaaaaaaaaaaaaaa", "无摔伤"))
        self.store.save(make_meta("bbbbbbbbbbbbbbbb", "街头少女风"))
        views = catalog.build_views(self.store, self.lib, game_dir=self.game)
        self.assertEqual(len(views), 2)
        self.assertTrue(all(v.status == catalog.STATUS_NOT_INSTALLED for v in views))
        self.assertTrue(all(not v.installed for v in views))

    def test_installed_status_from_library(self):
        meta = make_meta("aaaaaaaaaaaaaaaa", "无摔伤")
        self.store.save(meta)
        self.lib.record_install(
            library_mod.InstalledMod(mod_id=meta.id, mod_name=meta.name, game_dir=str(self.game))
        )
        views = catalog.build_views(self.store, self.lib, game_dir=self.game)
        self.assertEqual(views[0].status, catalog.STATUS_INSTALLED)
        self.assertTrue(views[0].installed)
        self.assertEqual(views[0].status_label, "已装")

    def test_installed_in_another_game_dir_does_not_mark_installed(self):
        """多平台并存：装在 A 目录的记录不能在 B 目录下显示成「已装」。"""
        meta = make_meta("aaaaaaaaaaaaaaaa", "无摔伤")
        self.store.save(meta)
        other = self.root / "game2"
        self.lib.record_install(
            library_mod.InstalledMod(mod_id=meta.id, mod_name=meta.name, game_dir=str(other))
        )
        views = catalog.build_views(self.store, self.lib, game_dir=self.game)
        self.assertEqual(views[0].status, catalog.STATUS_NOT_INSTALLED)
        self.assertEqual(views[0].installed_in, [str(other)])

    def test_no_game_dir_means_nothing_is_installed(self):
        """还没设游戏目录时，不该把「装了」当成已装。"""
        meta = make_meta("aaaaaaaaaaaaaaaa", "无摔伤")
        self.store.save(meta)
        self.lib.record_install(
            library_mod.InstalledMod(mod_id=meta.id, mod_name=meta.name, game_dir=str(self.game))
        )
        views = catalog.build_views(self.store, self.lib, game_dir=None)
        self.assertEqual(views[0].status, catalog.STATUS_NOT_INSTALLED)

    def test_conflict_status_wins_over_installed(self):
        meta = make_meta("aaaaaaaaaaaaaaaa", "衣服包甲")
        self.store.save(meta)
        self.lib.record_install(
            library_mod.InstalledMod(mod_id=meta.id, mod_name=meta.name, game_dir=str(self.game))
        )
        views = catalog.build_views(
            self.store,
            self.lib,
            game_dir=self.game,
            conflicts={meta.id: ("hard", ["/Game/.../SK_Wuchang_Body.uasset"])},
        )
        self.assertEqual(views[0].status, catalog.STATUS_CONFLICT)
        self.assertEqual(views[0].status_label, "冲突")
        self.assertEqual(len(views[0].conflicts), 1)

    def test_filter_by_status(self):
        a = make_meta("aaaaaaaaaaaaaaaa", "甲")
        b = make_meta("bbbbbbbbbbbbbbbb", "乙")
        self.store.save(a)
        self.store.save(b)
        self.lib.record_install(
            library_mod.InstalledMod(mod_id=a.id, mod_name=a.name, game_dir=str(self.game))
        )
        installed = catalog.build_views(
            self.store, self.lib, game_dir=self.game, filter_status=catalog.FILTER_INSTALLED
        )
        self.assertEqual([v.name for v in installed], ["甲"])
        not_installed = catalog.build_views(
            self.store, self.lib, game_dir=self.game, filter_status=catalog.FILTER_NOT_INSTALLED
        )
        self.assertEqual([v.name for v in not_installed], ["乙"])

    def test_query_searches_name_author_and_inner_filename(self):
        meta = make_meta("aaaaaaaaaaaaaaaa", "无摔伤")
        meta.author = "某某"
        meta.files = [ModFile(name="TB_HitData_Player_P.pak", rel_path="x", sha256="a")]
        self.store.save(meta)
        self.store.save(make_meta("bbbbbbbbbbbbbbbb", "街头少女风"))

        by_name = catalog.build_views(self.store, self.lib, game_dir=self.game, query="街头")
        self.assertEqual([v.name for v in by_name], ["街头少女风"])

        by_author = catalog.build_views(self.store, self.lib, game_dir=self.game, query="某某")
        self.assertEqual([v.name for v in by_author], ["无摔伤"])

        by_file = catalog.build_views(self.store, self.lib, game_dir=self.game, query="hitdata")
        self.assertEqual([v.name for v in by_file], ["无摔伤"])

    def test_sort_conflict_first_then_installed_then_by_name(self):
        c = make_meta("cccccccccccccccc", "丙")
        a = make_meta("aaaaaaaaaaaaaaaa", "甲")
        b = make_meta("bbbbbbbbbbbbbbbb", "乙")
        for m in (c, a, b):
            self.store.save(m)
        self.lib.record_install(
            library_mod.InstalledMod(mod_id=c.id, mod_name=c.name, game_dir=str(self.game))
        )
        views = catalog.build_views(
            self.store,
            self.lib,
            game_dir=self.game,
            conflicts={a.id: ("hard", ["x.uasset"])},
        )
        self.assertEqual([v.name for v in views], ["甲", "丙", "乙"])

    def test_preview_degrades_to_letter_card(self):
        self.store.save(make_meta("aaaaaaaaaaaaaaaa", "无摔伤"))
        views = catalog.build_views(self.store, self.lib, game_dir=self.game)
        spec = views[0].preview
        self.assertTrue(spec.is_letter_card)
        self.assertEqual(spec.tier, preview.TIER_LETTER_CARD)
        self.assertEqual(spec.letter, "无")

    def test_preview_uses_stored_image_when_present(self):
        meta = make_meta("aaaaaaaaaaaaaaaa", "无摔伤")
        self.store.save(meta)
        mod_dir = self.store.mod_dir(meta.id)
        img = mod_dir / "preview.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
        meta.preview = "preview.png"
        meta.preview_tier = preview.TIER_EXTRACTED
        self.store.save(meta)

        views = catalog.build_views(self.store, self.lib, game_dir=self.game)
        spec = views[0].preview
        self.assertFalse(spec.is_letter_card)
        self.assertEqual(spec.tier, preview.TIER_EXTRACTED)

    def test_missing_preview_file_degrades_instead_of_crashing(self):
        """用户手删了预览图文件 —— 必须降级，不能崩。"""
        meta = make_meta("aaaaaaaaaaaaaaaa", "无摔伤")
        meta.preview = "preview.png"     # 指向一个不存在的文件
        meta.preview_tier = preview.TIER_EXTRACTED
        self.store.save(meta)
        views = catalog.build_views(self.store, self.lib, game_dir=self.game)
        self.assertTrue(views[0].preview.is_letter_card)

    def test_illegal_mod_id_does_not_break_view_building(self):
        """手改 meta.json 塞进非法 id，界面仍要能刷出来（只是没有预览图）。

        用「带空格的中文目录名」当非法 id —— 它在 Windows 上是合法目录名，
        但过不了 `_SAFE_ID_PATTERN`，正好能触发 mod_dir() 的拒绝分支。
        """
        bad = self.store.root / "野的 id"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "meta.json").write_text(
            '{"id": "野的 id", "name": "野的", "content_hash": "x"}', encoding="utf-8"
        )
        views = catalog.build_views(self.store, self.lib, game_dir=self.game)
        self.assertIn("野的", [v.name for v in views])
        self.assertTrue(views[0].preview.is_letter_card)

    def test_summary_and_subdir_note(self):
        meta = make_meta("aaaaaaaaaaaaaaaa", "蓝图mod", files=3, subdir="LogicMods", size=2048)
        self.store.save(meta)
        view = catalog.build_views(self.store, self.lib, game_dir=self.game)[0]
        self.assertIn("3 个 pak", view.summary)
        self.assertIn("KB", view.summary)
        self.assertEqual(view.subdir_note, "目标：LogicMods")

    def test_summary_counts_companion_files(self):
        """pak + ucas + utoc 是三件套，摘要不能只说「1 个 pak」。"""
        meta = ModMeta(
            id="aaaaaaaaaaaaaaaa",
            name="IoStore三件套",
            content_hash="x" * 16,
            files=[
                ModFile(name="M_P.pak", rel_path="M_P.pak", size=1500, sha256="a"),
                ModFile(name="M_P.ucas", rel_path="M_P.ucas", size=3000, sha256="b"),
                ModFile(name="M_P.utoc", rel_path="M_P.utoc", size=800, sha256="c"),
            ],
        )
        self.store.save(meta)
        view = catalog.build_views(self.store, self.lib, game_dir=self.game)[0]
        self.assertIn("1 个 pak + 2 个附属文件", view.summary)

    def test_filter_counts(self):
        self.store.save(make_meta("aaaaaaaaaaaaaaaa", "甲"))
        self.store.save(make_meta("bbbbbbbbbbbbbbbb", "乙"))
        counts = catalog.filter_counts(self.store, self.lib, self.game)
        self.assertEqual(counts[catalog.FILTER_ALL], 2)
        self.assertEqual(counts[catalog.STATUS_NOT_INSTALLED], 2)
        self.assertEqual(counts[catalog.STATUS_INSTALLED], 0)


class TestPlatformBadge(unittest.TestCase):
    def test_known_platforms(self):
        self.assertEqual(catalog.platform_badge("steam")[0], "Steam")
        self.assertEqual(catalog.platform_badge("gamepass")[0], "Game Pass")
        self.assertEqual(catalog.platform_badge("whatever")[0], "未识别平台")


if __name__ == "__main__":
    unittest.main(verbosity=2)
