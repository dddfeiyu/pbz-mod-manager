# -*- coding: utf-8 -*-
"""导入管线回归测试（需求文档 3.2 / 3.6 / 3.7）。

跑法：
    python -m unittest discover -s tests -v
或（推荐，装了 py7zr 的隔离环境）：
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

**所有测试都写在临时目录里，绝不触碰真实的 %APPDATA%。**
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from wmm import archive, importer, modstore, nameresolve, pakfiles, preview, theme
from tools.make_sample_mods import build as build_samples
from tools.make_sample_mods import have_7z
from wmm import gamedir


class SampleMixin:
    """给每个用例造一套全新的样例，互不干扰。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.samples = build_samples(self.base / "samples")
        self.store = modstore.ModStore(self.base / "mods")
        self.store.ensure()

    def tearDown(self):
        self._tmp.cleanup()

    def import_one(self, key: str):
        report = importer.import_paths([self.samples[key]], self.store)
        self.assertEqual(len(report.outcomes), 1)
        return report.outcomes[0]


class TestStripInstallPrefix(unittest.TestCase):
    """实测教训：真实 mod 自带一整串安装层级，不剥就会装成嵌套目录。"""

    def test_strips_full_steam_layer(self):
        r = pakfiles.strip_install_prefix(
            (gamedir.UE_PROJECT, "Content", "Paks", "~mods", "x_P.pak")
        )
        self.assertEqual(r.kind, "mods")
        self.assertEqual(r.content_parts, ("x_P.pak",))
        self.assertEqual(r.target_subdir, "~mods")
        self.assertTrue(r.had_install_layer)

    def test_strips_tilde_mods_variant(self):
        r = pakfiles.strip_install_prefix(("~mods", "x_P.pak"))
        self.assertEqual(r.content_parts, ("x_P.pak",))

    def test_logicmods_wins_over_outer_mods(self):
        r = pakfiles.strip_install_prefix(("~mods", "LogicMods", "BP_P.pak"))
        self.assertEqual(r.kind, "logicmods")
        self.assertEqual(r.content_parts, ("BP_P.pak",))
        self.assertEqual(r.target_subdir, "LogicMods")

    def test_strips_bare_paks_layer(self):
        r = pakfiles.strip_install_prefix(("Content", "Paks", "x_P.pak"))
        self.assertEqual(r.content_parts, ("x_P.pak",))
        self.assertEqual(r.kind, "plain")

    def test_keeps_unknown_dirs(self):
        """不认识的目录不做猜测性剥离 —— 宁可保留。"""
        r = pakfiles.strip_install_prefix(("我的衣服mod", "x_P.pak"))
        self.assertEqual(r.content_parts, ("我的衣服mod", "x_P.pak"))
        self.assertFalse(r.had_install_layer)

    def test_bare_pak_untouched(self):
        r = pakfiles.strip_install_prefix(("x_P.pak",))
        self.assertEqual(r.content_parts, ("x_P.pak",))


class TestScanFolder(unittest.TestCase):
    def test_ignores_readme_url_and_finds_paks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "~mods").mkdir()
            (root / "~mods" / "a_P.pak").write_bytes(b"\x00" * 100)
            (root / "说明.txt").write_text("hi", encoding="utf-8")
            (root / "原站.url").write_text("[InternetShortcut]", encoding="utf-8")
            (root / "封面.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 4096)
            (root / "__MACOSX").mkdir()
            (root / "__MACOSX" / "._a_P.pak").write_bytes(b"\x00" * 10)

            scan = pakfiles.scan_folder(root)
            self.assertEqual([p.name for p in scan.paks], ["a_P.pak"])
            self.assertEqual([p.name for p in scan.images], ["封面.png"])
            self.assertEqual([p.name for p in scan.readmes], ["说明.txt"])
            self.assertGreaterEqual(scan.ignored_count, 1)

    def test_safe_pak_filename_strips_path_and_illegal_chars(self):
        self.assertEqual(pakfiles.safe_pak_filename("a/b/x_P.pak", "fb"), "x_P.pak")
        self.assertEqual(pakfiles.safe_pak_filename('bad<>:"name.pak', "fb"), "badname.pak")
        # 括号是合法文件名字符，不该被拿掉（实测真实文件名里就有括号）
        self.assertEqual(pakfiles.safe_pak_filename("A_p(under.pak", "fb"), "A_p(under.pak")

    def test_dedupe_names_case_insensitive(self):
        out = pakfiles.dedupe_names(["A_P.pak", "a_p.pak", "A_P.pak"])
        self.assertEqual(out[0], "A_P.pak")
        self.assertEqual(len(set(n.lower() for n in out)), 3)


class TestNameResolution(unittest.TestCase):
    def test_archive_name_wins(self):
        r = nameresolve.resolve_name([("街头少女风 蚰蜒套", "压缩包名")])
        self.assertEqual(r.name, "街头少女风 蚰蜒套")
        self.assertEqual(r.tier, 1)

    def test_falls_through_meaningless_archive_name(self):
        r = nameresolve.resolve_name(
            [("mod", "压缩包名"), ("街头少女风 蚰蜒套", "包内目录名")]
        )
        self.assertEqual(r.name, "街头少女风 蚰蜒套")
        self.assertEqual(r.tier, 2)

    def test_falls_through_to_readme_and_rejects_readme_title(self):
        r = nameresolve.resolve_name([("mod", "压缩包名")], extra_readme_text="Readme")
        self.assertTrue(r.is_fallback)
        self.assertEqual(r.name, "未命名 mod")

    def test_readme_first_line_used(self):
        r = nameresolve.resolve_name([("下载", "压缩包名")], extra_readme_text="无摔伤补丁")
        self.assertEqual(r.name, "无摔伤补丁")
        self.assertEqual(r.source, "readme 首行")

    def test_fallback_index(self):
        r = nameresolve.resolve_name([("mod", "压缩包名")], fallback_index=7)
        self.assertEqual(r.name, "未命名 mod 007")

    def test_gbk_readme_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "说明.txt"
            p.write_bytes("无摔伤补丁 v1.2\r\n作者：某某\r\n".encode("gb18030"))
            line = nameresolve.read_readme_first_line(p)
            self.assertIn("无摔伤", line)

    def test_pakchunk_name_has_no_information(self):
        """实测：pakchunk1200-Windows_P.pak 这类名字清洗后一个字都不剩。"""
        from wmm.gamedir import sanitize_mod_name

        self.assertEqual(sanitize_mod_name("pakchunk1200-Windows_P"), "")


class TestPreview(unittest.TestCase):
    def test_picks_webp_screenshot_over_icon(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            icon = root / "icon.png"
            icon.write_bytes(b"\x89PNG" + b"0" * (6 * 1024))
            shot = root / "预览截图.webp"
            shot.write_bytes(b"RIFF" + b"0" * (40 * 1024))
            pick = preview.pick_preview_image([icon, shot])
            self.assertTrue(pick.ok)
            self.assertEqual(pick.path.name, "预览截图.webp")

    def test_rejects_tiny_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "preview.png"
            p.write_bytes(b"\x89PNG" + b"0" * 100)   # 太小
            self.assertFalse(preview.pick_preview_image([p]).ok)

    def test_no_images_gives_letter_card(self):
        self.assertFalse(preview.pick_preview_image([]).ok)

    def test_letter_card_is_deterministic(self):
        a = preview.letter_card_index("街头少女风 蚰蜒套")
        b = preview.letter_card_index("街头少女风 蚰蜒套")
        self.assertEqual(a, b)
        self.assertLess(a, len(theme.LETTER_CARD_PALETTE))

    def test_letter_for_cjk_and_latin(self):
        self.assertEqual(preview.letter_for("街头少女"), "街")
        self.assertEqual(preview.letter_for("street style"), "S")
        self.assertEqual(preview.letter_for(""), "?")


class TestImportPipeline(SampleMixin, unittest.TestCase):
    def test_import_typical_mod_with_install_layer(self):
        out = self.import_one("street_style")
        self.assertEqual(out.kind, importer.KIND_IMPORTED, out.detail)
        meta = out.meta
        self.assertEqual(meta.name, "街头少女风 蚰蜒套")
        self.assertEqual(meta.install_subdir, "~mods")
        self.assertEqual(meta.pak_count, 1)
        # 安装层级必须被剥掉 —— 库里只应剩一个扁平的文件名
        self.assertEqual([f.name for f in meta.files], ["TK_WuChang_DYY-StreetStyle1511_P.pak"])
        # 预览图走第 1 档
        self.assertEqual(meta.preview_tier, preview.TIER_EXTRACTED)
        self.assertTrue((self.store.mod_dir(meta.id) / meta.preview).is_file())
        # 说明.txt / .url 不该被当成 mod 文件
        self.assertFalse(any("url" in f.name for f in meta.files))

    def test_duplicate_content_detected_across_different_archive_names(self):
        first = self.import_one("street_style")
        self.assertEqual(first.kind, importer.KIND_IMPORTED)
        second = self.import_one("street_style_again")
        self.assertEqual(second.kind, importer.KIND_DUPLICATE, second.detail)
        self.assertIsNotNone(second.existing)
        self.assertEqual(second.existing.id, first.meta.id)
        self.assertEqual(self.store.count(), 1)

    def test_logicmods_target_subdir(self):
        out = self.import_one("logic_mod")
        self.assertEqual(out.kind, importer.KIND_IMPORTED, out.detail)
        self.assertEqual(out.meta.install_subdir, "LogicMods")

    def test_pak_ucas_utoc_all_kept(self):
        out = self.import_one("with_ucas")
        self.assertEqual(out.kind, importer.KIND_IMPORTED, out.detail)
        self.assertEqual(sorted(f.ext for f in out.meta.files), [".pak", ".ucas", ".utoc"])

    def test_no_preview_degrades_to_letter_card_and_fallback_name(self):
        out = self.import_one("flat_no_preview")
        self.assertEqual(out.kind, importer.KIND_IMPORTED, out.detail)
        self.assertEqual(out.meta.preview_tier, preview.TIER_LETTER_CARD)
        self.assertEqual(out.meta.preview, "")
        self.assertTrue(out.meta.name.startswith("未命名 mod"))

    def test_gbk_readme_used_as_name(self):
        out = self.import_one("gbk_readme")
        self.assertEqual(out.kind, importer.KIND_IMPORTED, out.detail)
        self.assertIn("无摔伤", out.meta.name)
        # readme 首行是「无摔伤补丁 v1.2」，版本号里的 `.2` 不能被当扩展名截掉
        self.assertIn("v1.2", out.meta.name)

    def test_folder_import_with_paren_in_name(self):
        out = self.import_one("folder_mod")
        self.assertEqual(out.kind, importer.KIND_IMPORTED, out.detail)
        self.assertEqual(out.meta.name, "BaiNude 无遮挡修复")
        self.assertEqual(len(out.meta.files), 1)

    def test_zipslip_is_refused(self):
        out = self.import_one("zipslip")
        self.assertEqual(out.kind, importer.KIND_FAILED)
        self.assertIn("可疑", out.message)
        self.assertEqual(self.store.count(), 0)

    def test_broken_rar_gives_human_message(self):
        out = self.import_one("fake_rar")
        self.assertEqual(out.kind, importer.KIND_FAILED)
        self.assertIn("rar", out.message.lower() + out.detail.lower())
        self.assertEqual(self.store.count(), 0)

    def test_unsupported_input_is_skipped_with_hint(self):
        out = self.import_one("not_a_mod")
        self.assertEqual(out.kind, importer.KIND_SKIPPED)
        self.assertIn(".pak", out.detail)

    def test_bare_pak_import(self):
        out = importer.import_paths(
            [self.samples["folder_mod"] / "BaiNude_Flat_Clean_p(under.pak"], self.store
        ).outcomes[0]
        self.assertEqual(out.kind, importer.KIND_IMPORTED, out.detail)
        self.assertEqual(out.meta.pak_count, 1)

    def test_staging_dirs_are_cleaned_up(self):
        import glob

        before = set(glob.glob(str(Path(tempfile.gettempdir()) / "wmm-*")))
        for key in ("street_style", "with_ucas", "zipslip", "fake_rar"):
            importer.import_paths([self.samples[key]], self.store)
        after = set(glob.glob(str(Path(tempfile.gettempdir()) / "wmm-*")))
        self.assertEqual(before, after, f"临时目录没清干净：{after - before}")

    def test_reimport_same_mod_replaces_cleanly(self):
        first = self.import_one("street_style")
        mod_dir = self.store.mod_dir(first.meta.id)
        stray = mod_dir / "files" / "stray_P.pak"
        stray.write_bytes(b"leftover")
        # 直接再导一次同 id（模拟 meta.json 被删但文件还在等情形）
        self.store.commit(first.meta, [(self.samples["folder_mod"] / "封面.png", "new_P.pak")])
        self.assertFalse(stray.exists(), "重导应先清掉旧 files 目录，避免残留孤儿文件")

    def test_batch_report_summary(self):
        report = importer.import_paths(
            [
                self.samples["street_style"],
                self.samples["street_style_again"],
                self.samples["not_a_mod"],
            ],
            self.store,
        )
        self.assertEqual(len(report.imported), 1)
        self.assertEqual(len(report.duplicates), 1)
        self.assertEqual(len(report.skipped), 1)
        self.assertIn("成功导入 1 个", report.summary())


class TestScanCandidates(SampleMixin, unittest.TestCase):
    def test_scan_finds_archives_and_folders(self):
        cands = importer.scan_candidates(self.base / "samples", self.store)
        labels = {c.label for c in cands}
        self.assertIn("街头少女风 蚰蜒套", labels)
        self.assertIn("BaiNude 无遮挡修复", labels)
        # 不支持的 .bin 不该出现
        self.assertNotIn("not_a_mod", labels)

    def test_scan_marks_already_imported(self):
        self.import_one("street_style")
        cands = importer.scan_candidates(self.base / "samples", self.store)
        hit = next(c for c in cands if c.label == "街头少女风 蚰蜒套")
        self.assertTrue(hit.already_likely, "已导入过的样例应被标记出来")

    def test_bare_paks_only_when_asked(self):
        """游戏 `~mods` 下面躺着的就是**裸 .pak**（不是压缩包、也不在子目录里）。

        2026-10-11 新增「自动扫描游戏 mod 目录」时踩到的点：默认扫法一条都扫不到，
        必须带 `include_bare_paks=True` 才把裸 pak 也当候选。
        """
        mods = self.base / "fake_mods"
        mods.mkdir()
        (mods / "ReplaceMing_P.pak").write_bytes(b"\x00" * 32)
        (mods / "手建子目录").mkdir()
        (mods / "手建子目录" / "x_P.pak").write_bytes(b"\x00" * 16)

        plain = importer.scan_candidates(mods, self.store)
        asked = importer.scan_candidates(mods, self.store, include_bare_paks=True)

        # 默认：只认"含 pak 的目录"，裸 pak 不出现
        self.assertEqual([c.label for c in plain], ["手建子目录"])
        # 打开开关：裸 pak 与子目录都在
        self.assertEqual(
            sorted(c.label for c in asked), ["ReplaceMing_P", "手建子目录"]
        )
        self.assertEqual({c.kind for c in asked}, {"pak", "folder"})


class TestModStoreSafety(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "mods"
        self.store = modstore.ModStore(self.root)
        self.store.ensure()

    def tearDown(self):
        self._tmp.cleanup()

    def test_illegal_mod_id_rejected(self):
        for bad in ("..", "../..", "a/b", "", "a" * 80, "..\\windows"):
            with self.assertRaises(ValueError, msg=bad):
                self.store.mod_dir(bad)

    def test_delete_removes_only_that_mod(self):
        other = self.root / "keepme"
        other.mkdir(parents=True)
        (other / "meta.json").write_text("{}", encoding="utf-8")

        meta = modstore.ModMeta(id="abc123", name="X", content_hash="abc123" * 6)
        (self.root / "abc123" / "files").mkdir(parents=True, exist_ok=True)
        (self.root / "abc123" / "files" / "x_P.pak").write_bytes(b"x")
        self.store.save(meta)

        dry = self.store.delete("abc123", dry_run=True)
        self.assertTrue(any(p.name == "x_P.pak" for p in dry))
        self.assertTrue((self.root / "abc123").exists(), "dry_run 不应真删")

        self.store.delete("abc123")
        self.assertFalse((self.root / "abc123").exists())
        self.assertTrue(other.exists(), "不该动到别的 mod 目录")

    def test_content_hash_ignores_archive_name(self):
        from wmm.modstore import ModFile, compute_content_hash

        a = [ModFile(name="x_P.pak", rel_path="x_P.pak", sha256="deadbeef")]
        b = [ModFile(name="y_P.pak", rel_path="x_P.pak", sha256="deadbeef")]
        self.assertEqual(compute_content_hash(a), compute_content_hash(b))
        c = [ModFile(name="x_P.pak", rel_path="x_P.pak", sha256="cafebabe")]
        self.assertNotEqual(compute_content_hash(a), compute_content_hash(c))


class TestArchiveDetection(unittest.TestCase):
    def test_magic_beats_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 内容是 zip，后缀却是 .rar —— 必须按 zip 处理
            p = Path(tmp) / "lie.rar"
            with zipfile.ZipFile(p, "w") as zf:
                zf.writestr("a.txt", "x")
            self.assertEqual(archive.detect_archive_kind(p), archive.KIND_ZIP)

    def test_zip_slip_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            for bad in ("../evil.txt", "..\\evil.txt", "a/../../evil.txt", "C:/evil.txt"):
                with self.assertRaises(archive.UnsafeArchiveError, msg=bad):
                    archive._safe_member_target(dest, bad)
            # 正常名不该被拒
            archive._safe_member_target(dest, "~mods/ok_P.pak")

    def test_unknown_format_message_is_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.abc"
            p.write_bytes(b"not an archive at all")
            with self.assertRaises(archive.ArchiveError) as ctx:
                archive.extract_archive(p, Path(tmp) / "out", kind=archive.KIND_UNKNOWN)
            self.assertIn("不认识这个压缩格式", str(ctx.exception))


@unittest.skipUnless(have_7z(), "当前环境没有 py7zr，跳过 7z 解压测试")
class TestSevenZip(SampleMixin, unittest.TestCase):
    """7z 是需求文档第 6 章明确支持的第二大格式，必须真的跑通。"""

    def test_detect_and_extract(self):
        path = self.samples["seven_zip"]
        self.assertEqual(archive.detect_archive_kind(path), archive.KIND_7Z)
        dest = Path(tempfile.mkdtemp(prefix="wmm-7z-out-"))
        try:
            result = archive.extract_archive(path, dest)
            names = sorted(str(p.relative_to(dest)).replace("\\", "/") for p in dest.rglob("*") if p.is_file())
            self.assertIn("~mods/SevenZPak_P.pak", names)
            self.assertEqual(result.kind, archive.KIND_7Z)
            self.assertGreater(result.total_bytes, 0)
        finally:
            archive.cleanup_staging(dest)

    def test_import_seven_zip(self):
        out = self.import_one("seven_zip")
        self.assertEqual(out.kind, importer.KIND_IMPORTED, out.detail)
        # 包名「七z格式mod」清洗后仍可用 -> 用它当名字
        self.assertIn("七z格式mod", out.meta.name)
        self.assertEqual([f.name for f in out.meta.files], ["SevenZPak_P.pak"])
        self.assertEqual(out.meta.preview_tier, preview.TIER_EXTRACTED)

    def test_corrupt_7z_gives_human_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.7z"
            bad.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 64)
            out = importer.import_paths([bad], self.store).outcomes[0]
            self.assertEqual(out.kind, importer.KIND_FAILED)
            self.assertIn("7z", out.message + out.detail)


class TestSameSourceMerge(unittest.TestCase):
    """一个 mod 的多个部件不该被拆成多个 mod。

    真实例（用户 2026-10-10 反馈「这是一个 mod 中的多个部位，你给识别成多个
    mod 了」）：`黑红短裙-1.24/` 下 4 个子目录各放一个 pak，主名都是
    `TK_SexySkirt_*` —— 同一件衣服的主文件 / 脸部纹身 / 腿部纹身 / 瘦腿。

    而真合集（`以往绅士mod合集/NSFW合集/1.衣服 套装、散件/`）结构长得**一模一样**，
    区别只在 pak 名各不相同。所以判据必须落在 pak 名上。
    """

    def _make(self, root: Path, spec) -> Path:
        for sub, pak in spec:
            d = root / sub
            d.mkdir(parents=True)
            (d / pak).write_bytes(b"\x00" * 32)
        return root

    def test_same_mod_parts_are_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make(
                Path(tmp) / "黑红短裙",
                (
                    ("主文件 衣服+胖腿", "TK_SexySkirt_BlackRed_Main_p.pak"),
                    ("脸部纹身", "TK_SexySkirt_MakeUp_p.pak"),
                    ("腿部纹身", "TK_SexySkirt_Z_BodyPrint_p.pak"),
                    ("裤子 瘦腿效果", "TK_SexySkirt_BlackRed_ThinLeg_p.pak"),
                ),
            )
            units = importer._expand_folder(root, None, 6)
            self.assertEqual([u.label for u in units], ["黑红短裙"])

    def test_real_collection_is_still_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make(
                Path(tmp) / "以往绅士mod合集",
                (
                    ("夜夫人", "Mistress_of_the_Night_one_boob_p.pak"),
                    ("宫女", "cloth07_p.pak"),
                    ("新娘", "bridal_corset_reshape_P.pak"),
                ),
            )
            units = importer._expand_folder(root, None, 6)
            self.assertEqual(len(units), 3)

    def test_signature_ignores_platform_noise(self):
        # 尾部 _P / _p / -Windows 与 pakchunk 之类噪声词不该算作"同源证据"
        self.assertEqual(importer._pak_signature("pakchunk1400-Windows_P"), set())
        self.assertIn("sexyskirt", importer._pak_signature("TK_SexySkirt_MakeUp_p"))

    def test_single_subfolder_is_not_treated_as_parts(self):
        # 只有一个子目录时不该"合并"，那本来就是它自己
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make(Path(tmp) / "白色长发", (("LongTail_white", "EmbroideredHeadband_W_longTail_P.pak"),))
            units = importer._expand_folder(root, None, 6)
            self.assertEqual([u.label for u in units], ["LongTail_white"])


class TestDropNestedPaths(unittest.TestCase):
    """多选文件夹导入时的父子去重。

    用户多选时很容易既选中父目录、又选中它里面的某个 mod 文件夹 ——
    父目录本来就会把子目录拆成独立 mod 导入，所以子目录必须丢掉，
    否则同一个 mod 被导两遍（进度条会莫名其妙多走一轮）。
    """

    def test_child_is_dropped_when_parent_selected(self):
        base = r"C:\示例\明末mod"
        got = importer.drop_nested_paths([base, base + r"\夜行服装"])
        self.assertEqual(got, [base])

    def test_duplicates_dropped_but_order_kept(self):
        got = importer.drop_nested_paths([r"D:\a", r"C:\b", r"D:\a"])
        self.assertEqual(got, [r"D:\a", r"C:\b"])

    def test_windows_path_compare_is_case_insensitive(self):
        got = importer.drop_nested_paths([r"C:\Mods", r"c:\mods\夜行服装"])
        self.assertEqual(got, [r"C:\Mods"])

    def test_siblings_are_all_kept(self):
        base = r"C:\示例\明末mod"
        self.assertEqual(len(importer.drop_nested_paths([base + r"\a", base + r"\b"])), 2)

    def test_empty_input(self):
        self.assertEqual(importer.drop_nested_paths([]), [])


# ---------------------------------------------------------------- 防闪退（2026-10-11）
class TestScanCannotWreckHugeDirs(unittest.TestCase):
    """用户报过「点扫描电脑上的文件夹，软件直接闪退」。

    根因：他的扫描目录列表里存着 `E:\\SteamLibrary`（**整个 Steam 库根**），
    扫描一路走进 `steamapps\\common\\` 下几十个游戏 —— 实测把**黑神话悟空等
    游戏本体的 pak**（pakchunk0~15）当成了 mod 候选，一导入就去递归上百 GB。
    这组测试钉住三层刹车，任何一层被改掉都会红。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _make_lib_tree(self, root: Path) -> None:
        """造一个 `steamapps/common/某游戏/b1/Content/Paks/*.pak` 的目录形状。"""
        paks = root / "steamapps" / "common" / "SomeGame" / "b1" / "Content" / "Paks"
        paks.mkdir(parents=True)
        for i in range(6):
            (paks / f"pakchunk{i}-Windows.pak").write_bytes(b"GAME" * 100)

    def test_game_library_dirs_are_never_mod_candidates(self):
        lib = self.tmp / "SteamLibrary"
        self._make_lib_tree(lib)
        got = importer.scan_candidates(lib, None)
        self.assertEqual(
            got, [], f"游戏库目录不该产出任何 mod 候选，实际：{[c.path for c in got]}"
        )

    def test_scan_folder_stops_at_depth_limit(self):
        """深目录必须触顶停下并置 truncated，绝不无限递归。"""
        deep = self.tmp / "deep"
        cur = deep
        for i in range(pakfiles.SCAN_MAX_WALK_DEPTH + 4):
            cur = cur / f"层{i}"
        cur.mkdir(parents=True)
        (cur / "a_P.pak").write_bytes(b"X")
        scan = pakfiles.scan_folder(deep)
        self.assertTrue(scan.truncated, "超出深度上限必须标记 truncated")
        self.assertEqual(scan.paks, [], "超限时不该再收集 pak")

    def test_truncated_is_reported_not_silent(self):
        """截断必须在结果里留痕（上层要如实告知用户，绝不静默截断）。"""
        big = self.tmp / "big"
        big.mkdir()
        # 造超过 SCAN_MAX_PAKS 的 pak 数量太多（2000 个太慢），改用小上限 monkeypatch
        old = pakfiles.SCAN_MAX_PAKS
        pakfiles.SCAN_MAX_PAKS = 3
        try:
            for i in range(10):
                (big / f"m{i:02d}_P.pak").write_bytes(b"Y" * 10)
            scan = pakfiles.scan_folder(big)
        finally:
            pakfiles.SCAN_MAX_PAKS = old
        self.assertTrue(scan.truncated, "达到 pak 数上限必须置 truncated")
        self.assertEqual(len(scan.paks), 3, f"应该在上限处停下，实际收了 {len(scan.paks)} 个")

    def test_normal_mod_dir_still_scanned(self):
        """⚠️ 防呆不能误伤：正常的 mod 目录必须照常扫出候选。"""
        mod = self.tmp / "mods" / "夜行服装"
        (mod / "files").mkdir(parents=True)
        (mod / "files" / "cloth01_P.pak").write_bytes(b"MOD" * 100)
        got = importer.scan_candidates(self.tmp / "mods", None)
        self.assertEqual([c.path for c in got], [mod], f"正常 mod 目录必须能扫到，实际：{got}")

    def test_ignored_dir_names_cover_platform_libs(self):
        for name in ("steamapps", "steamvr", "common", "xboxgames", "egstore", "wepicgames"):
            self.assertTrue(pakfiles.is_ignored_dir(name), f"{name} 应被忽略")


class TestPrecheckDuplicates(SampleMixin, unittest.TestCase):
    """导入**前**的查重（用户 2026-10-11：「如果有些mod之前已经导入过了，
    那么二次导入的时候你能识别出来吗？能识别到就给出报警提示。」）。

    关键点是**导入前**就能报 —— 等 `_import_unit` 跑到第 5 步，用户已经
    白解压 + 白算完 sha256 了。
    """

    def test_same_content_again_is_flagged_exact(self):
        """同一个文件夹再选一次 → 必须 exact 命中，且指向库里的那条。"""
        key = "folder_mod"
        importer.import_paths([self.samples[key]], self.store)
        hits, _unknown = importer.precheck_duplicates([self.samples[key]], self.store)
        self.assertEqual(len(hits), 1, f"应命中 1 条，实际 {[(h.label, h.reason) for h in hits]}")
        self.assertEqual(hits[0].reason, "exact")
        self.assertEqual(len(self.store.all()), 1, "库里仍只有 1 条（预检不会写库）")

    def test_bare_pak_file_is_flagged(self):
        """直接选中一个 .pak 文件也要能判重（用户可能只选文件不选文件夹）。"""
        key = "folder_mod"
        importer.import_paths([self.samples[key]], self.store)
        meta = self.store.all()[0]
        pak = self.store.mod_dir(meta.id) / "files" / meta.files[0].name
        self.assertTrue(pak.is_file(), f"样例 pak 不在预期位置：{pak}")
        hits, _ = importer.precheck_duplicates([str(pak)], self.store)
        self.assertEqual([h.reason for h in hits], ["exact"])

    def test_archive_precheck_is_either_exact_or_unknown_never_a_guess(self):
        """压缩包预检：**要么 exact 判重，要么如实说没法判定，绝不猜**。

        0.1.15 时压缩包一律进 unknown（等于 ZIP 导入完全绕过预检）；
        0.1.17 起 ZIP 走流式哈希能判 `exact` 了（见 `TestFolderThenZipSameMod`），
        但 7z / rar 仍拿不到内容哈希 —— 那时**必须**如实进 unknown，
        绝不能只凭文件名就断言"这是重复"（误报比漏报糟得多）。
        """
        importer.import_paths([self.samples["folder_mod"]], self.store)
        src = self.samples["flat_no_preview"]
        if not src.is_file():
            self.skipTest("样例不是压缩包")
        hits, unknown = importer.precheck_duplicates([src], self.store)
        # 这个样例是 ZIP → 能流式读，所以**判定了**。既然判定了就只可能是两种结果：
        if hits:
            self.assertEqual(
                [h.reason for h in hits],
                ["exact"],
                f"判重必须有内容哈希支撑，实际 {[(h.label, h.reason) for h in hits]}",
            )
        else:
            self.assertNotIn(
                str(src), unknown,
                "ZIP 读得出来就算判定完了：不是重复就不该再进 unknown",
            )

    def test_unreadable_archive_goes_to_unknown(self):
        """⚠️ 7z / rar 这类读不了内容的，必须如实进 unknown，绝不猜。

        误报"这是重复"比漏报糟得多 —— 用户会以为自己的 mod 丢了。
        """
        broken = self.base / "读不了的.7z"
        broken.write_bytes(b"\x37\x7a\xbc\xaf\x27\x1c not really an archive")
        hits, unknown = importer.precheck_duplicates([broken], self.store)
        self.assertEqual(hits, [], "读不了的包绝不能被判成重复")
        self.assertIn(str(broken), unknown, "必须如实说'没法提前判定'")

    def test_renamed_archive_same_content_still_exact(self):
        """换个压缩包名再导入，**内容**一样就该被认出来（判据只看内容指纹）。

        ⚠️ 走的是**导入流程**的判重，不是预检 —— 压缩包要判"一模一样"
        必须解压，而解压正是导入流程本来就会做的事（见 precheck_duplicates
        的文档：压缩包一律只报 unknown，绝不猜）。
        """
        src = Path(self.samples["flat_no_preview"])
        if not src.is_file():
            self.skipTest("样例不是压缩包")
        renamed = src.parent / "改了个名字.zip"
        src.rename(renamed)
        first = importer.import_paths([renamed], self.store)
        second = importer.import_paths([renamed], self.store)
        self.assertEqual(len(first.imported), 1)
        self.assertEqual(
            [o.reason if hasattr(o, "reason") else o.kind for o in second.duplicates],
            [importer.KIND_DUPLICATE],
            "换个包名也要被判成重复",
        )
        self.assertEqual(len(self.store.all()), 1, "库里不该出现副本")

    def test_brand_new_content_is_not_flagged(self):
        """反向守：全新内容绝不能误报成重复。"""
        fresh = self.base / "全新的mod"
        (fresh / "files").mkdir(parents=True)
        (fresh / "files" / "brandnew_P.pak").write_bytes(b"NEVER-SEEN-BEFORE" * 50)
        hits, _ = importer.precheck_duplicates([fresh], self.store)
        self.assertEqual(hits, [], f"全新 mod 被误报成重复：{[(h.label, h.reason) for h in hits]}")

    def test_same_name_different_content_is_soft_hit(self):
        """pak 文件名一样但内容不同 → 只能报 `name` 级提示，不能说"一模一样"。"""
        key = "folder_mod"
        src = Path(self.samples["folder_mod"])
        scan = pakfiles.scan_folder(src)
        pak_name = scan.paks[0].name
        importer.import_paths([src], self.store)

        edited = self.base / "作者的改版"
        (edited / "files").mkdir(parents=True)
        (edited / "files" / pak_name).write_bytes(b"DIFFERENT-CONTENT-ENTIRELY" * 40)
        hits, _ = importer.precheck_duplicates([edited], self.store)
        self.assertEqual([h.reason for h in hits], ["name"], "内容不同只能是 name 级提示")

    def test_empty_library_never_flags(self):
        hits, _ = importer.precheck_duplicates([self.samples["folder_mod"]], self.store)
        self.assertEqual(hits, [], "空库不该有任何命中")


class TestSeriesMerge(unittest.TestCase):
    """**分组合并**（用户 2026-10-11）。

    真实例（用户截图）：`绅士内容mod 尺度大 内容丰富 11.7更新/` 被拆成 4 个 mod
    —— `Project_Lewd 本体`(46 pak)、`ProjectLewd_20251107_update_pack`(11 pak)、
    `消除棒子`(1 pak)、`消除重复的眼部纹理`(1 pak)。
    用户原话：「前两个是不是可以合成一个？同系列的 mod 能否识别？识别出来后，加个标识。」

    期望 = **3 个 mod**：前两个合成 `Project Lewd`(57 pak)，后两个保持独立。
    """

    def _make(self, root: Path, spec) -> Path:
        for sub, paks in spec.items():
            d = root / sub
            d.mkdir(parents=True, exist_ok=True)
            for i, pak in enumerate(paks):
                (d / pak).write_bytes(f"{sub}-{i}".encode() * 8)
        return root

    def _real_case(self, tmp: str) -> Path:
        return self._make(
            Path(tmp) / "绅士内容mod 尺度大 内容丰富 11.7更新",
            {
                # 同系列：pak 名全是 UE 资产名，**一个作者标识都没有**
                "Project_Lewd 本体": [
                    "BiXi_FuXi_9_P.pak", "ENV_B_Any_shidui03_Mask_Medres_9_P.pak",
                ],
                "ProjectLewd_20251107_update_pack": [
                    "ENV_PC_Any_qiaozhong_Nanite_Medres_9_P.pak",
                ],
                # 独立小工具包：pak 名与本体里某个文件高度相似，**不该被合进去**
                "消除棒子": ["WPN_zaozi_reverse_99_P.pak"],
                "消除重复的眼部纹理": ["M_XBSN_rig_9_P.pak"],
            },
        )

    def test_series_parts_merge_but_others_stay_separate(self):
        """核心：4 个子目录 → 3 个 mod，前两个合一。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._real_case(tmp)
            units = importer._expand_folder(root, None, 6)
            self.assertEqual(len(units), 3, f"应为 3 个 mod，实际 {[u.label for u in units]}")
            merged = [u for u in units if u.part_labels]
            self.assertEqual(len(merged), 1, "只该有一组被合并")
            u = merged[0]
            self.assertEqual(
                u.part_labels,
                ["Project_Lewd 本体", "ProjectLewd_20251107_update_pack"],
            )
            self.assertEqual(len(u.extra_paths), 1, "另一个部件目录应进 extra_paths")

    def test_merged_mod_is_named_after_the_series(self):
        """合并后的名字是**系列名**「Project Lewd」，不是部件名也不是宣传语父目录名。

        ⚠️ 这两条都是真实踩过的：
          · 用部件名 → 卡片显示「Project_Lewd 本体」，把内部结构露给用户；
          · 用父目录名 → 卡片显示「绅士内容mod 尺度大 内容丰富 11.7更新」，
            这是作者写给**人类看的宣传语**（还带日期），当 mod 名毫无意义。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._real_case(tmp)
            units = importer._expand_folder(root, None, 6)
            merged = [u for u in units if u.part_labels][0]
            self.assertEqual(merged.label, "Project Lewd")

    def test_merged_unit_imports_all_paks_together(self):
        """合并单元导进库时 pak 数量是**两个部件之和**，不是只导第一个。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._real_case(tmp)
            store = modstore.ModStore(Path(tmp) / "mods")
            store.ensure()
            report = importer.import_paths([root], store)
            self.assertEqual(len(report.imported), 3, f"实际 {[o.display_name for o in report.imported]}")
            merged = [m for m in store.all() if m.series_parts]
            self.assertEqual(len(merged), 1)
            self.assertEqual(
                len(merged[0].files), 3,
                "合并体 = 本体 2 个 pak + update_pack 1 个 pak = 3 个；"
                "「消除棒子」那份是独立 mod，不该混进来",
            )

    def test_series_warning_is_shown_to_user(self):
        """用户要「识别出来后加个标识」—— 导入当场必须说清楚合了什么。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._real_case(tmp)
            store = modstore.ModStore(Path(tmp) / "mods")
            store.ensure()
            report = importer.import_paths([root], store)
            merged = [o for o in report.imported if o.meta and o.meta.series_parts]
            self.assertEqual(len(merged), 1)
            self.assertTrue(
                any("同系列" in w for w in merged[0].warnings),
                f"导入提示里必须说明合并了什么，实际 {merged[0].warnings}",
            )

    def test_parts_named_after_body_parts_fall_back_to_parent_dir(self):
        """⚠️ 反向守：部件名全是部位说明时**不能**拿它当系列名。

        真实例：`黑红短裙/主文件 衣服+胖腿/脸部纹身/腿部纹身/` ——
        拿「主文件 衣服 胖腿」当 mod 名毫无意义，必须退回父目录名「黑红短裙」。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make(
                Path(tmp) / "黑红短裙",
                {
                    "主文件 衣服+胖腿": ["TK_SexySkirt_BlackRed_Main_p.pak"],
                    "脸部纹身": ["TK_SexySkirt_MakeUp_p.pak"],
                    "腿部纹身": ["TK_SexySkirt_Z_BodyPrint_p.pak"],
                },
            )
            units = importer._expand_folder(root, None, 6)
            self.assertEqual(len(units), 1)
            self.assertEqual(units[0].label, "黑红短裙")

    def test_genuine_collection_is_not_merged(self):
        """⚠️ 反向守：真合集（每个子目录一件不同的衣服）仍然必须拆开。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make(
                Path(tmp) / "以往绅士mod合集",
                {
                    "夜夫人": ["Mistress_of_the_Night_one_boob_p.pak"],
                    "宫女": ["cloth07_p.pak"],
                    "新娘": ["bridal_corset_reshape_P.pak"],
                },
            )
            units = importer._expand_folder(root, None, 6)
            self.assertEqual(len(units), 3, f"真合集不能被合，实际 {[u.label for u in units]}")

    def test_grouping_is_deterministic(self):
        """⚠️ 颜色/分组必须**可重现**：同样输入反复跑要得到同样的分组与顺序。

        若用 set/dict 迭代顺序建图，Python 哈希随机化会让同一目录每次结果不同，
        用户会以为"每次导入拆得不一样"。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._real_case(tmp)
            first = [[c.label for c in g] for g in
                     importer._group_candidates(importer.scan_candidates(root, None))]
            for _ in range(10):
                again = [[c.label for c in g] for g in
                         importer._group_candidates(importer.scan_candidates(root, None))]
                self.assertEqual(again, first)

    def test_series_key_normalises_spelling_differences(self):
        """`Project_Lewd` 与 `ProjectLewd` 只是作者写法不同，必须归一化成同一个 key。"""
        self.assertEqual(
            importer._series_key("Project_Lewd 本体"),
            importer._series_key("ProjectLewd_20251107_update_pack"),
        )

    def test_same_character_different_outfits_are_not_merged(self):
        """🚨 用户 2026-10-12 实测反例：同一角色的**多套不同衣服**不能被合成一个 mod。

        「女巫 果 丝袜 适配1.6版本」下 5 个子目录各 1 个 pak：
            女巫 白丝连裤袜 → witch white panty_900_p.pak
            女巫 短款白丝   → witch white short stocking_900_p.pak
            女巫 短款黑丝   → witch black short stocking_900_p.pak
            女巫 黑丝连裤袜 → witch black panty_900_p.pak
            女巫 果 无丝袜 → witch nude_900_p.pak
        每个 pak 的标识集合只有 3 个词且都含 `witch`（角色名），两两交集 2 个、
        Jaccard 恰好 0.5 —— 旧判据下并查集一传全通，四套衣服合成一个 mod，
        用户只能整套装卸（真实库里的 `92cf105260dac17d` 就是这么来的）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make(
                Path(tmp) / "女巫 果 丝袜 适配1.6版本",
                {
                    "女巫 白丝连裤袜": ["witch white panty_900_p.pak"],
                    "女巫 短款白丝": ["witch white short stocking_900_p.pak"],
                    "女巫 短款黑丝": ["witch black short stocking_900_p.pak"],
                    "女巫 黑丝连裤袜": ["witch black panty_900_p.pak"],
                    "女巫 果 无丝袜": ["witch nude_900_p.pak"],
                },
            )
            units = importer._expand_folder(root, None, 6)
            self.assertEqual(
                len(units), 5,
                f"五套衣服必须是五个独立 mod，实际 {[u.label for u in units]}",
            )
            self.assertFalse(any(u.part_labels for u in units), "一个都不该被合并")

    def test_merge_still_requires_genuine_signature_overlap(self):
        """反向守：目录名没信息量、但 pak 标识**大幅**重合时仍然要合。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make(
                Path(tmp) / "files",
                {
                    "part_a": [
                        "TK_SexySkirt_BlackRed_Main_p.pak",
                        "TK_SexySkirt_MakeUp_p.pak",
                        "TK_SexySkirt_Z_BodyPrint_p.pak",
                    ],
                    "part_b": [
                        "TK_SexySkirt_BlackRed_Main_p.pak",
                        "TK_SexySkirt_MakeUp_p.pak",
                        "TK_SexySkirt_Z_BodyPrint_p.pak",
                    ],
                },
            )
            units = importer._expand_folder(root, None, 6)
            self.assertEqual(len(units), 1, f"标识集合高度重合仍应合并，实际 {[u.label for u in units]}")
            self.assertTrue(units[0].part_labels)

    def test_part_desc_dirs_without_shared_pak_stay_separate(self):
        """🚨 反向守：光有部位词不够，pak 标识没有共同主名就**不能**合。

        真合集里的 `上衣/` + `裤子/` 两个目录名都含部位词，但装的是**两件不同
        的 mod**（pak 名毫无关系）。没有「pak 有共同主名」这道 AND，
        整个「衣服 套装、散件」分类目录会被合成一个巨型 mod。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make(
                Path(tmp) / "衣服 套装、散件",
                {
                    "上衣": ["Mistress_of_the_Night_one_boob_p.pak"],
                    "裤子": ["cloth07_p.pak"],
                },
            )
            units = importer._expand_folder(root, None, 6)
            self.assertEqual(
                len(units), 2,
                f"pak 不同主名必须拆开，实际 {[u.label for u in units]}",
            )


class TestContentFingerprintIgnoresPath(unittest.TestCase):
    """🚨 内容指纹**不能**包含 rel_path（用户 2026-10-11 截图实锤的真 bug）。

    用户截图里「全果 明军」出现两条，问「这两个是同一个 mod，导入时为什么没有提示？
    这俩一个是以文件夹的形式导入的，一个是以 ZIP 的形式导入的。」

    实测根因：两条的 pak **sha256 完全相同**，但
        文件夹版 rel_path = `Project_Plague-WinGDK_9001_P.pak`（剥掉了顶层目录）
        ZIP 版    rel_path = `全果 明军-11.28/Project_Plague-WinGDK_9001_P.pak`
    旧算法把 rel_path 一起哈希 → 两个指纹不同 → `find_by_hash` 判不出重复。
    """

    def _f(self, rel, sha="deadbeef"):  # noqa: ANN001
        from wmm.modstore import ModFile

        return ModFile(name=rel.rsplit("/", 1)[-1], rel_path=rel, size=1, sha256=sha)

    def test_same_content_different_rel_path_same_hash(self):
        a = modstore.compute_content_hash([self._f("a_P.pak")])
        b = modstore.compute_content_hash([self._f("全果 明军-11.28/a_P.pak")])
        self.assertEqual(a, b, "同一份内容、路径层级不同 → 指纹必须相同")

    def test_same_content_renamed_file_same_hash(self):
        """作者改名重发同一份内容，也仍是同一个 mod。"""
        a = modstore.compute_content_hash([self._f("Project_Plague-WinGDK_9001_P.pak")])
        b = modstore.compute_content_hash([self._f("项目v2/Project_Plague-WinGDK_9001_P.pak")])
        self.assertEqual(a, b)

    def test_different_content_different_hash(self):
        a = modstore.compute_content_hash([self._f("a_P.pak", sha="1111")])
        b = modstore.compute_content_hash([self._f("a_P.pak", sha="2222")])
        self.assertNotEqual(a, b, "内容不同绝不能撞成同一个")

    def test_file_count_matters(self):
        """⚠️ 一个 pak 的 mod 不能与两个 pak 的 mod 撞成同一个。"""
        one = modstore.compute_content_hash([self._f("a_P.pak", sha="1111")])
        two = modstore.compute_content_hash(
            [self._f("a_P.pak", sha="1111"), self._f("b_P.pak", sha="2222")]
        )
        self.assertNotEqual(one, two)

    def test_order_does_not_matter(self):
        files = [self._f("a_P.pak", sha="1111"), self._f("b_P.pak", sha="2222")]
        self.assertEqual(
            modstore.compute_content_hash(files),
            modstore.compute_content_hash(list(reversed(files))),
        )


class TestFolderThenZipSameMod(SampleMixin, unittest.TestCase):
    """真实场景：同一个 mod 的**文件夹版**与**ZIP 版**依次导入，只能进库一条。"""

    def _make_pair(self, root: Path):
        """造一个「文件夹 + 同名 zip」，两者 pak 内容完全一致。"""
        payload = b"MODPAYLOAD" * 64
        folder = root / "全果 明军-11.28"
        folder.mkdir(parents=True)
        (folder / "Project_Plague-WinGDK_9001_P.pak").write_bytes(payload)
        zpath = root / "全果 明军-11.28.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            # ⚠️ ZIP 里**多一层同名目录** —— 这正是真实情况，也正是旧算法漏判的原因
            zf.writestr("全果 明军-11.28/Project_Plague-WinGDK_9001_P.pak", payload)
        return folder, zpath

    def test_precheck_flags_the_zip_as_exact_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, zpath = self._make_pair(Path(tmp))
            importer.import_paths([folder], self.store)
            hits, unknown = importer.precheck_duplicates([zpath], self.store)
            self.assertTrue(
                any(h.reason == "exact" for h in hits),
                f"ZIP 版应被判为 exact 重复，实际 hits={[(h.label, h.reason) for h in hits]} "
                f"unknown={[Path(u).name for u in unknown]}",
            )

    def test_import_keeps_only_one_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder, zpath = self._make_pair(Path(tmp))
            importer.import_paths([folder], self.store)
            second = importer.import_paths([zpath], self.store)
            self.assertEqual(
                [o.kind for o in second.outcomes],
                [importer.KIND_DUPLICATE],
                "第二次导入必须判重复，不能再往库里塞一条",
            )
            self.assertEqual(len(self.store.all()), 1, "库里只能有一条")

    def test_zip_precheck_reports_unknown_only_when_unreadable(self):
        """⚠️ 坏 ZIP 必须如实进 unknown（不能猜），且**不阻断**导入。"""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "坏了.zip"
            bad.write_bytes(b"this is definitely not a zip file")
            hits, unknown = importer.precheck_duplicates([bad], self.store)
            self.assertEqual(hits, [], "读不了的包不该被判成重复")
            self.assertIn(str(bad), unknown, "必须如实说'没法提前判定'")


class TestPreviewOutsideWalk(unittest.TestCase):
    """预览图「往外看」的边界 —— 0.1.20 真实事故的回归。

    事故：用户的 mod 是 `桌面\\明末mod整合包\\<mod名>\\`，其中没有自带图的那批
    一路往上看到了**桌面**，把桌面上随手放的一张无关 png 当成了自己的预览图
    （83 条里 49 条显示同一张图）。用户原话：「这些mod你是从哪弄的预览图，
    跟mod毫无关系啊」。
    """

    @staticmethod
    def _mod_dir(root: Path, name: str) -> Path:
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "A_P.pak").write_bytes(b"\x00" * 64)
        return d

    @staticmethod
    def _img(p: Path) -> Path:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        return p

    def _outside(self, unit_root: Path) -> list:
        scan = pakfiles.scan_folder(unit_root)
        return importer._collect_preview_images(unit_root, scan, allow_outside=True)

    def test_collection_dir_never_donates_upper_images(self):
        """合集夹里的 mod 不许捡到更上层的散图（真实事故的形状）。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mod = self._mod_dir(base / "明末mod整合包", "拍照模式")
            self._mod_dir(base / "明末mod整合包", "换脸 剑星渡鸦")   # 旁边还有别的 mod
            self._img(base / "无关的桌面截图.png")                  # 更上层的一张散图
            self.assertEqual(self._outside(mod), [])

    def test_collection_dir_own_loose_image_not_used(self):
        """合集夹自己那层的散图也不许用 —— 它是"一堆 mod 的容器"，图归属不明。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            pack = base / "明末mod整合包"
            mod = self._mod_dir(pack, "数值数字显示")
            self._mod_dir(pack, "方灵 半果")
            self._img(pack / "合集封面.png")
            self.assertEqual(self._outside(mod), [])

    def test_desktop_layer_never_used(self):
        """桌面/下载/文档这类杂物层永远不看（哪怕那层只有这一个子目录）。"""
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "桌面"
            mod = self._mod_dir(desktop, "改武器技能")
            self._img(desktop / "junk.png")
            self.assertEqual(self._outside(mod), [])

    def test_wrapper_layer_image_still_used(self):
        """反向守：**专属包裹层**（只有一个子目录）的图仍要能捡到，别修过头。"""
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "极其丰满 情趣内衣mod"
            mod = self._mod_dir(wrapper, "极其丰满情趣mod")
            self._img(wrapper / "效果图.png")
            got = self._outside(mod)
            self.assertEqual([p.name for p in got], ["效果图.png"])

    def test_wrapper_preview_subdir_still_used(self):
        """反向守：包裹层里的「预览图」夹（自己不含 pak）仍是合法来源。"""
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "某某套装"
            mod = self._mod_dir(wrapper, "inner")
            self._img(wrapper / "预览图" / "1.png")
            got = self._outside(mod)
            self.assertEqual([p.name for p in got], ["1.png"])

    def test_outside_disabled_for_archives(self):
        """反向守：压缩包/pak 单元（`allow_outside=False`）一张外面的图都不许捡。"""
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "wrapper"
            mod = self._mod_dir(wrapper, "inner")
            self._img(wrapper / "效果图.png")
            scan = pakfiles.scan_folder(mod)
            self.assertEqual(
                importer._collect_preview_images(mod, scan, allow_outside=False), []
            )

    def test_own_folder_image_always_wins(self):
        """自带图优先，且不受"外面"那几条闸影响。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            pack = base / "明末mod整合包"
            mod = self._mod_dir(pack, "方瑶 透明款")
            self._mod_dir(pack, "另一个 mod")
            mine = self._img(mod / "我的截图.png")
            self._img(base / "桌面上的无关图.png")
            self.assertEqual(self._outside(mod), [mine])


if __name__ == "__main__":
    unittest.main(verbosity=2)
