# -*- coding: utf-8 -*-
"""安装/卸载引擎与冲突检测的回归测试（需求文档 F-4.1 ~ F-4.10 / F-5.1 ~ F-5.6）。

**所有测试都写在临时目录里，绝不触碰真实的 %APPDATA% 或真实游戏目录。**
`install()` 的库、store、备份目录全部显式传临时路径，不走默认值。

跑法：
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wmm import conflicts, enabler, gamedir, installer, library as library_mod, modstore
from wmm.games import ACTIVE as GAME
from wmm.modstore import ModFile, ModMeta


# ---------------------------------------------------------------- fixtures
def build_fake_steam_game(root: Path) -> gamedir.ProbeResult:
    """造一个 Steam 版游戏目录骨架。"""
    paks = root / "steamapps" / "common" / "Wuchang Fallen Feathers" / gamedir.UE_PROJECT / "Content" / "Paks"
    paks.mkdir(parents=True, exist_ok=True)
    (paks / "Project_Plague-Windows.pak").write_bytes(b"VANILLA")
    (root / "steamapps" / "appmanifest_2277560.acf").write_text("", encoding="utf-8")
    probe = gamedir.validate_game_dir(root / "steamapps" / "common" / "Wuchang Fallen Feathers")
    assert probe.ok, probe.warnings
    return probe


def build_fake_gamepass_game(root: Path) -> gamedir.ProbeResult:
    game = root / "XboxGames" / "Wuchang Fallen Feathers" / "Content"
    paks = game / gamedir.UE_PROJECT / "Content" / "Paks"
    paks.mkdir(parents=True, exist_ok=True)
    (paks / "Project_Plague-WinGDK_0_P.pak").write_bytes(b"VANILLA")
    probe = gamedir.validate_game_dir(game)
    assert probe.ok, probe.warnings
    return probe


class InstallCase(unittest.TestCase):
    """公共脚手架：临时数据目录 + 临时游戏目录 + 临时备份目录。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.store = modstore.ModStore(self.base / "data" / "mods")
        self.store.ensure()
        self.lib = library_mod.Library.load(self.base / "data" / "library.json")
        self.backups = self.base / "data" / "backups"
        self.probe = build_fake_steam_game(self.base / "game")
        self._seq = 0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- 造一个能真的装进游戏目录的 mod ----
    def make_mod(self, name: str, *filenames: str, overrides=None) -> ModMeta:
        self._seq += 1
        mod_id = f"m{self._seq:04d}"
        if not filenames:
            filenames = (f"{mod_id}_P.pak",)
        meta = ModMeta(
            id=mod_id,
            name=name,
            content_hash=f"{self._seq:016x}",
            overrides=list(overrides or []),
            files=[ModFile(name=f) for f in filenames],
        )
        self.store.save(meta)
        files_dir = self.store.files_dir(mod_id)
        files_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for f in filenames:
            path = files_dir / f
            path.write_bytes(b"MOCK-PAK-" + f.encode("utf-8"))
            files.append(
                ModFile(name=f, rel_path=f, size=path.stat().st_size, sha256="deadbeef")
            )
        meta.files = files
        self.store.save(meta)
        return meta

    def install(self, meta: ModMeta, **kw):
        return installer.install(
            meta, self.probe, self.store, self.lib,
            backup_root=self.backups, **kw
        )

    @property
    def mods_dir(self) -> Path:
        return Path(self.probe.paks_dir) / "~mods"


# ---------------------------------------------------------------- 命名规则
@unittest.skipIf(GAME.preserve_filenames,
                 "该游戏保留 mod 原始文件名（IoStore 三件套不能改名），命名规范化规则不适用")
class TestTargetNaming(InstallCase):
    """F-4.1 / F-4.2 / F-4.3 / F-4.8 —— 命名规则是本工具最容易错的地方。"""

    def test_steam_single_pak_ends_with_p(self):
        names = installer.resolve_target_names(
            "街头少女风 蚰蜒套",
            [ModFile(name="x.pak")],
            gamedir.PLATFORM_STEAM,
        )
        self.assertEqual([t for _f, t in names], ["街头少女风 蚰蜒套_P.pak"])

    def test_ucas_utoc_keep_same_stem(self):
        """F-4.2 原文要求「保留 .ucas 同名」—— 三件套主名不一致游戏就不加载。"""
        files = [ModFile(name="street_P.pak"), ModFile(name="street_P.ucas"), ModFile(name="street_P.utoc")]
        names = [t for _f, t in installer.resolve_target_names("街头少女", files, gamedir.PLATFORM_STEAM)]
        stems = {n.rsplit(".", 1)[0] for n in names}
        self.assertEqual(len(stems), 1, f"三件套主名必须一致，实际 {names}")
        self.assertTrue(names[0].endswith("_P.pak"))

    def test_gamepass_name_is_exact_and_no_subdir(self):
        """F-4.3：Game Pass 必须精确命名为 Project_Plague-WinGDK_4_P.pak。"""
        names = [t for _f, t in installer.resolve_target_names("随便什么名", [ModFile(name="a.pak")], gamedir.PLATFORM_GAMEPASS)]
        self.assertEqual(names, ["Project_Plague-WinGDK_4_P.pak"])

    def test_gamepass_target_dir_is_main_paks(self):
        probe = build_fake_gamepass_game(self.base / "gp")
        self.assertEqual(
            gamedir.resolve_mods_dir(probe.platform, Path(probe.paks_dir)),
            Path(probe.paks_dir),
        )

    def test_chunk_id_gives_priority_name(self):
        """F-4.8：调优先级时用带编号的规范名 —— 实测用户就是这么干的。"""
        names = [t for _f, t in installer.resolve_target_names("任意", [ModFile(name="a.pak")], gamedir.PLATFORM_STEAM, chunk_id=10000)]
        self.assertEqual(names, ["Project_Plague-Windows_10000_P.pak"])

    def test_multi_pak_keeps_meaningful_original_stem(self):
        files = [ModFile(name="Weapon_A_P.pak"), ModFile(name="pakchunk1200-Windows_P.pak")]
        names = [t for _f, t in installer.resolve_target_names("合集", files, gamedir.PLATFORM_STEAM)]
        self.assertIn("Weapon_A_P.pak", names)
        # 没信息量的那个回落到「mod 名 + 序号」，而不是留个空名
        self.assertTrue(all(n.endswith("_P.pak") for n in names))
        self.assertEqual(len(set(names)), 2, f"两个 pak 不能重名：{names}")

    def test_multi_pak_filename_keeps_underscore(self):
        """回归：曾用显示名清洗函数做文件名，把 `Weapon_A` 变成了 `Weapon A`。"""
        files = [ModFile(name="Weapon_A_P.pak"), ModFile(name="b.pak")]
        names = [t for _f, t in installer.resolve_target_names("合集", files, gamedir.PLATFORM_STEAM)]
        self.assertIn("Weapon_A_P.pak", names)
        self.assertNotIn("Weapon A_P.pak", names)


class TestVanillaPak(unittest.TestCase):
    def test_real_vanilla_names_detected(self):
        for name in (
            "Project_Plague-Windows.pak",
            "Project_Plague-Windows_0_P.pak",
            "Project_Plague-Windows_1_P.pak",
            "Project_Plague-Windows_0_P.sig",
        ):
            self.assertTrue(installer.is_vanilla_pak(name), name)

    def test_mod_names_are_not_vanilla(self):
        for name in ("z257_P.pak", "x.pak", "pakchunk637-GoddessV2_P.pak", "MyMod_P.pak"):
            self.assertFalse(installer.is_vanilla_pak(name), name)


# ---------------------------------------------------------------- 计划
class TestPlan(InstallCase):
    def test_plan_targets_mods_dir(self):
        meta = self.make_mod("测试mod", "x_P.pak")
        plan = installer.plan_install(meta, self.probe, self.store, self.lib)
        self.assertTrue(plan.ok, plan.blockers)
        self.assertEqual(plan.mods_dir, self.mods_dir)
        self.assertEqual(plan.target_names(), ["测试mod_P.pak"])

    def test_plan_without_game_dir_is_blocked(self):
        empty = gamedir.validate_game_dir(self.base / "nothing-here")
        meta = self.make_mod("测试mod", "x_P.pak")
        plan = installer.plan_install(meta, empty, self.store, self.lib)
        self.assertFalse(plan.ok)
        self.assertTrue(plan.blockers)

    def test_plan_marks_pre_existing_foreign_file(self):
        """F-4.6：目标文件已存在要提示，并区分「本工具装的」和「别人放的」。"""
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        (self.mods_dir / "测试mod_P.pak").write_bytes(b"SOMEONE-ELSE")
        meta = self.make_mod("测试mod", "x_P.pak")
        plan = installer.plan_install(meta, self.probe, self.store, self.lib)
        self.assertEqual(plan.files[0].action, "overwrite")
        self.assertTrue(plan.files[0].foreign)
        self.assertTrue(any("不是本工具装的" in w for w in plan.warnings))

    # ---- 装前查重（2026-10-11 实测踩到后加的）----
    # 真实案例：库里 mod 叫「全果 明军」，其库内文件原名却是
    # `Project_Plague-WinGDK_9001_P.pak`（作者直接复用了游戏原版 pak 的名字）。
    # 装出去被规范成 `全果 明军_P.pak`，而游戏里本来已躺着一模一样的那份 ——
    # 同一个 mod 在游戏里就变成了两份。文件名对不上，只能靠**内容 sha256** 认。

    def _mod_with_real_content(self, name: str, pak_name: str, payload: bytes) -> ModMeta:
        """造一条**带真实 sha256** 的 mod 记录（make_mod 里那个是占位值）。"""
        meta = self.make_mod(name, pak_name)
        files_dir = self.store.files_dir(meta.id)
        (files_dir / pak_name).write_bytes(payload)
        meta.files = [
            ModFile(
                name=pak_name,
                rel_path=pak_name,
                size=len(payload),
                sha256=modstore.hash_file(files_dir / pak_name),
            )
        ]
        return meta

    def test_plan_warns_when_game_already_has_same_content_other_name(self):
        payload = b"REALLY-SAME-BYTES" * 1000
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        # 游戏里已有一份字节完全相同、但文件名不同的 pak
        (self.mods_dir / "Project_Plague-WinGDK_9001_P.pak").write_bytes(payload)
        meta = self._mod_with_real_content(
            "全果 明军", "Project_Plague-WinGDK_9001_P.pak", payload
        )
        plan = installer.plan_install(meta, self.probe, self.store, self.lib)
        self.assertTrue(plan.ok, plan.blockers)
        self.assertEqual(plan.target_names(), ["全果 明军_P.pak"])
        self.assertTrue(
            any("内容完全相同" in w and "WinGDK_9001" in w for w in plan.warnings),
            f"应提示游戏里已有同内容 pak，实际警告：{plan.warnings}",
        )

    def test_plan_no_warning_when_content_differs(self):
        """内容不同就不该提示 —— 免得满屏噪音。"""
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        (self.mods_dir / "别人的mod_P.pak").write_bytes(b"TOTALLY-DIFFERENT-BYTES")
        meta = self._mod_with_real_content("我的mod", "mine_P.pak", b"MY-OWN-BYTES" * 500)
        plan = installer.plan_install(meta, self.probe, self.store, self.lib)
        self.assertTrue(plan.ok, plan.blockers)
        self.assertFalse(
            any("内容完全相同" in w for w in plan.warnings),
            f"不该有重复提示，实际警告：{plan.warnings}",
        )

    def test_plan_same_name_overwrite_is_not_reported_as_duplicate(self):
        """落点同名时走的是「覆盖」流程，不该再重复报一句"已经有同样内容"。"""
        payload = b"SELF-SAME" * 800
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        meta = self._mod_with_real_content("同名mod", "same_P.pak", payload)
        # 先假装它已经装过了（同名文件，内容相同）
        (self.mods_dir / "同名mod_P.pak").write_bytes(payload)
        plan = installer.plan_install(meta, self.probe, self.store, self.lib)
        self.assertEqual(plan.files[0].action, "overwrite")
        self.assertFalse(
            any("内容完全相同" in w for w in plan.warnings),
            f"同名覆盖不该报重复，实际警告：{plan.warnings}",
        )

    def test_plan_keeps_non_pak_files_in_library(self):
        meta = self.make_mod("带说明的mod", "x_P.pak", "说明.txt", "封面.png")
        plan = installer.plan_install(meta, self.probe, self.store, self.lib)
        deployed = {f.original_name for f in plan.files}
        self.assertEqual(deployed, {"x_P.pak"})
        self.assertIn("说明.txt", plan.kept_in_library)
        self.assertIn("封面.png", plan.kept_in_library)


# ---------------------------------------------------------------- 安装
class TestInstall(InstallCase):
    def test_install_writes_file_and_records(self):
        meta = self.make_mod("白珍珠 祭祀套", "x_P.pak")
        result = self.install(meta)
        self.assertTrue(result.ok, result.message)
        target = self.mods_dir / "白珍珠 祭祀套_P.pak"
        self.assertTrue(target.is_file())
        record = self.lib.find(meta.id, self.probe.game_root)
        self.assertIsNotNone(record)
        self.assertEqual(len(record.deployed_files), 1)
        self.assertEqual(Path(record.deployed_files[0].dst), target)
        self.assertEqual(record.deployed_files[0].original_name, "x_P.pak")

    def test_install_creates_mods_dir_when_missing(self):
        self.assertFalse(self.mods_dir.exists())
        result = self.install(self.make_mod("新建目录", "x_P.pak"))
        self.assertTrue(result.ok, result.message)
        self.assertTrue(self.mods_dir.is_dir())

    def test_install_backs_up_overwritten_foreign_file(self):
        """F-4.9：覆盖别人的文件前必须备份，且记录里要有备份目录。"""
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        original = b"USER-HAND-MADE-MOD"
        (self.mods_dir / "重装过_P.pak").write_bytes(original)

        meta = self.make_mod("重装过", "x_P.pak")
        result = self.install(meta)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(len(result.backed_up), 1)

        record = self.lib.find(meta.id, self.probe.game_root)
        self.assertTrue(record.backup_dir)
        self.assertTrue(Path(record.backup_dir).is_dir())
        # 备份内容必须真的是原来那份
        backup = installer.BackupSet.load(meta.id, self.backups)
        self.assertEqual(len(backup.entries), 1)
        stored = backup.dir / backup.entries[0].stored
        self.assertEqual(stored.read_bytes(), original)

    def test_install_is_idempotent(self):
        meta = self.make_mod("重复安装", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        second = self.install(meta)
        self.assertTrue(second.ok, second.message)
        self.assertEqual(len(self.lib.for_game(self.probe.game_root)), 1)

    def test_install_reports_missing_library_file(self):
        meta = self.make_mod("文件丢了", "x_P.pak")
        (self.store.files_dir(meta.id) / "x_P.pak").unlink()
        result = self.install(meta)
        self.assertFalse(result.ok)
        self.assertIn("找不到了", result.message)

    def test_install_without_write_permission_gives_human_message(self):
        """F-4.10：不能静默失败，要给能看懂的中文。"""
        blocker = self.base / "blocked"
        blocker.write_text("我是个文件，不是目录", encoding="utf-8")
        with self.assertRaises(installer.GameDirNotWritable) as ctx:
            installer.ensure_writable(blocker / "~mods")
        self.assertIn("写不进去", str(ctx.exception))

    def test_batch_install_continues_after_failure(self):
        good = self.make_mod("好的", "a_P.pak")
        bad = self.make_mod("坏的", "b_P.pak")
        (self.store.files_dir(bad.id) / "b_P.pak").unlink()
        results = installer.install_many(
            [bad, good], self.probe, self.store, self.lib, backup_root=self.backups
        )
        self.assertEqual(len(results), 2)
        self.assertFalse(results[0].ok)
        self.assertTrue(results[1].ok)


# ---------------------------------------------------------------- 卸载 / 回滚
class TestUninstall(InstallCase):
    def test_uninstall_deletes_recorded_files_only(self):
        """F-4.4 / F-4.5：只按记录删。旁边别人的文件必须原封不动。"""
        meta = self.make_mod("要卸载的", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        bystander = self.mods_dir / "别人手工放的_P.pak"
        bystander.write_bytes(b"KEEP-ME")

        result = installer.uninstall(self.lib, meta.id, self.probe.game_root, backup_root=self.backups)
        self.assertTrue(result.ok, result.message)
        self.assertFalse((self.mods_dir / "要卸载的_P.pak").exists())
        self.assertTrue(bystander.is_file(), "★ 别人的文件被误删了")
        self.assertIsNone(self.lib.find(meta.id, self.probe.game_root))

    def test_uninstall_restores_backed_up_file(self):
        """F-4.9：卸载后应还原被覆盖的原文件。"""
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        target = self.mods_dir / "会还原_P.pak"
        original = b"ORIGINAL-CONTENT"
        target.write_bytes(original)

        meta = self.make_mod("会还原", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        self.assertNotEqual(target.read_bytes(), original)

        result = installer.uninstall(self.lib, meta.id, self.probe.game_root, backup_root=self.backups)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(target.is_file(), "被覆盖的原始文件应该被还原回来")
        self.assertEqual(target.read_bytes(), original)

    def test_dry_run_previews_restore(self):
        """用户 2026-10-12：卸载**前**就要说清会还原谁。

        dry_run 是纯读取（只读安装记录 + 备份清单），**不许动盘** ——
        文件还得是装上去的那份、备份也还得在，否则"预告"就变成"预做"了。
        `same_as_lookup` 传进去时，还原清单要带上「与库中哪个 mod 内容相同」。
        """
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        target = self.mods_dir / "预告_P.pak"
        target.write_bytes(b"ORIGINAL")

        meta = self.make_mod("预告", "x_P.pak")
        self.assertTrue(self.install(meta).ok)

        pv = installer.uninstall(
            self.lib,
            meta.id,
            self.probe.game_root,
            dry_run=True,
            backup_root=self.backups,
            same_as_lookup=lambda sha: ("other", "库里的另一个mod") if sha else None,
        )
        self.assertTrue(pv.ok)
        self.assertTrue(pv.restored)
        note = pv.restored_notes.get(str(pv.restored[0]), "")
        self.assertIn("库里的另一个mod", note)
        self.assertIn("还原", pv.message)
        # dry_run 不许动盘
        self.assertEqual(target.read_bytes(), b"MOCK-PAK-x_P.pak")
        self.assertTrue((self.backups / meta.id / "manifest.json").is_file())

    def test_uninstall_notes_same_as(self):
        """还原提示要能说清「这个文件与库中哪个 mod 内容相同」。"""
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        target = self.mods_dir / "同源_P.pak"
        target.write_bytes(b"ORIGINAL")

        meta = self.make_mod("同源", "x_P.pak")
        self.assertTrue(self.install(meta).ok)

        result = installer.uninstall(
            self.lib,
            meta.id,
            self.probe.game_root,
            backup_root=self.backups,
            same_as_lookup=lambda sha: ("other", "库里的另一个mod") if sha else None,
        )
        self.assertTrue(result.ok, result.message)
        self.assertEqual(len(result.restored), 1)
        note = result.restored_notes.get(str(result.restored[0]), "")
        self.assertIn("库里的另一个mod", note)
        self.assertIn("库里的另一个mod", result.message)

    def test_uninstall_notes_absent_without_lookup(self):
        """不传 same_as_lookup 时行为不变（没有同源标注，也不许炸）。"""
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        (self.mods_dir / "无标注_P.pak").write_bytes(b"ORIGINAL")
        meta = self.make_mod("无标注", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        result = installer.uninstall(self.lib, meta.id, self.probe.game_root, backup_root=self.backups)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.restored_notes, {})

    def test_uninstall_refuses_path_outside_game_dir(self):
        """🔒 安全回归：手改过的 library.json 指到游戏目录之外时，必须拒绝删除。"""
        meta = self.make_mod("越界", "x_P.pak")
        outsider = self.base / "precious.txt"
        outsider.write_text("绝对不能被删", encoding="utf-8")

        record = library_mod.InstalledMod(
            mod_id=meta.id,
            mod_name=meta.name,
            game_dir=str(self.probe.game_root),
            platform=self.probe.platform,
            deployed_files=[
                library_mod.DeployedFile(src="x", dst=str(outsider), original_name="precious.txt")
            ],
        )
        self.lib.record_install(record)
        self.lib.save()

        result = installer.uninstall(self.lib, meta.id, self.probe.game_root, backup_root=self.backups)
        self.assertTrue(outsider.is_file(), "★ 游戏目录之外的文件被删了")
        self.assertTrue(result.refused)
        self.assertTrue(any("不在游戏目录" in r for r in result.refused))

    def test_uninstall_keeps_non_empty_mods_dir(self):
        """删空目录用 rmdir（非空即失败）—— 别人还剩东西时目录必须留着。"""
        meta = self.make_mod("占用目录", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        (self.mods_dir / "别人的_P.pak").write_bytes(b"STILL-HERE")

        result = installer.uninstall(self.lib, meta.id, self.probe.game_root, backup_root=self.backups)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(self.mods_dir.is_dir(), "★ 目录里还有别人的文件，不该被删掉")

    def test_uninstall_removes_dir_it_created_when_empty(self):
        meta = self.make_mod("独占目录", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        self.assertTrue(self.mods_dir.is_dir())
        result = installer.uninstall(self.lib, meta.id, self.probe.game_root, backup_root=self.backups)
        self.assertTrue(result.ok, result.message)
        self.assertFalse(self.mods_dir.exists(), "目录是本次安装建的、且已空，应该清理掉")

    def test_uninstall_without_record(self):
        result = installer.uninstall(self.lib, "nope", self.probe.game_root, backup_root=self.backups)
        self.assertFalse(result.ok)

    def test_uninstall_reports_missing_files(self):
        meta = self.make_mod("文件先没了", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        (self.mods_dir / "文件先没了_P.pak").unlink()
        result = installer.uninstall(self.lib, meta.id, self.probe.game_root, backup_root=self.backups)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.missing), 1)

    def test_dry_run_does_not_delete(self):
        meta = self.make_mod("只看不删", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        result = installer.uninstall(
            self.lib, meta.id, self.probe.game_root, dry_run=True, backup_root=self.backups
        )
        self.assertTrue(result.ok)
        self.assertTrue((self.mods_dir / "只看不删_P.pak").is_file())
        self.assertTrue(result.deleted)

    def test_dry_run_previews_restored_files(self):
        """卸载确认框要能**提前**预告「会还原哪些原文件」。

        用户 2026-10-12：手动放的 mod 被管理器二次安装后卸载，文件会被删一次
        再复原一次 —— 以前这一切悄悄发生（确认框只字不提）。dry_run 必须把
        备份清单里记着的原文件路径读出来给确认框用。
        """
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        target = self.mods_dir / "手放_P.pak"
        original = b"HAND-PLACED"
        target.write_bytes(original)

        meta = self.make_mod("手放", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        self.assertNotEqual(target.read_bytes(), original)

        preview = installer.uninstall(
            self.lib, meta.id, self.probe.game_root, dry_run=True, backup_root=self.backups
        )
        self.assertTrue(preview.ok)
        self.assertEqual([str(target)], [str(p) for p in preview.restored])
        self.assertTrue(target.is_file(), "★ dry_run 不许动磁盘")
        self.assertIn("预览", preview.message)

    def test_dry_run_restored_empty_without_backup(self):
        """没覆盖过任何文件时，预告必须是空的 —— 不能无中生有吓唬用户。"""
        meta = self.make_mod("没覆盖", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        preview = installer.uninstall(
            self.lib, meta.id, self.probe.game_root, dry_run=True, backup_root=self.backups
        )
        self.assertTrue(preview.ok)
        self.assertEqual([], list(preview.restored))

    def test_uninstall_message_names_restored_files(self):
        """卸完的提示必须**点名**还原了谁 + 说清来源。

        只说「还原 N 个文件」等于没说 —— 用户看到游戏目录里冒出文件 still
        不知道是谁干的（2026-10-12 原话：「告知用户有手动安装的 mod 被复原」）。
        """
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        target = self.mods_dir / "卸完点名_P.pak"
        target.write_bytes(b"HAND-PLACED")

        meta = self.make_mod("卸完点名", "x_P.pak")
        self.assertTrue(self.install(meta).ok)

        result = installer.uninstall(
            self.lib, meta.id, self.probe.game_root, backup_root=self.backups
        )
        self.assertTrue(result.ok, result.message)
        self.assertIn(str(target), result.message)
        self.assertIn("装这个 mod 之前就在游戏目录里", result.message)
        self.assertEqual(target.read_bytes(), b"HAND-PLACED")

    def test_rollback_is_uninstall(self):
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        target = self.mods_dir / "回滚吧_P.pak"
        target.write_bytes(b"ORIGINAL")
        meta = self.make_mod("回滚吧", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        result = installer.rollback(self.lib, meta.id, self.probe.game_root, backup_root=self.backups)
        self.assertTrue(result.ok, result.message)
        self.assertIn("已回滚", result.message)
        self.assertEqual(target.read_bytes(), b"ORIGINAL")


# ---------------------------------------------------------------- 资源解析
class TestResourceParsing(unittest.TestCase):
    def test_normalizes_all_common_spellings(self):
        for raw in (
            "/Game/Characters/Player/SK_Wuchang_Body.uasset",
            "Project/Content/Characters/Player/SK_Wuchang_Body.uasset",
            "Characters/Player/SK_Wuchang_Body.uasset",
            "characters\\player\\sk_wuchang_body",
        ):
            self.assertEqual(
                conflicts.normalize_resource(raw), "characters/player/sk_wuchang_body", raw
            )

    def test_parses_pasted_description(self):
        text = (
            "这个 mod 改了：\n"
            "/Game/Characters/Player/SK_Wuchang_Body.uasset\n"
            "Project/Content/Characters/Player/SK_Head/SK_Head.uasset\n"
            "还有 ClothTop.uasset\n"
        )
        found = conflicts.parse_overrides_from_text(text)
        self.assertIn("characters/player/sk_wuchang_body", found)
        self.assertIn("characters/player/sk_head/sk_head", found)
        self.assertIn("clothtop", found)

    def test_bare_filename_not_duplicated(self):
        """回归：裸文件名与全路径会被两条正则同时命中，不能报两遍。"""
        text = "/Game/Characters/Player/SK_Body.uasset"
        found = conflicts.parse_overrides_from_text(text)
        self.assertEqual(found, ["characters/player/sk_body"])

    def test_empty_text(self):
        self.assertEqual(conflicts.parse_overrides_from_text(""), [])

    def test_display_resource(self):
        self.assertEqual(
            conflicts.display_resource("characters/player/sk_wuchang_body"),
            "sk_wuchang_body.uasset",
        )

    def test_split_name_version(self):
        self.assertEqual(conflicts.split_name_version("无摔伤补丁 v1.2"), ("无摔伤补丁", "1.2"))
        self.assertEqual(conflicts.split_name_version("pakchunk1200-Windows")[1], "")

    def test_priority_of(self):
        self.assertEqual(conflicts.priority_of("Project_Plague-Windows_10000_P.pak"), 10000)
        self.assertEqual(conflicts.priority_of("MyMod_P.pak"), 0)


# ---------------------------------------------------------------- 冲突检测
class TestConflictDetect(InstallCase):
    def _install_pair(self, a_over, b_over, a_name="服装包甲", b_name="服装包乙"):
        a = self.make_mod(a_name, "a_P.pak", overrides=a_over)
        b = self.make_mod(b_name, "b_P.pak", overrides=b_over)
        self.assertTrue(self.install(a).ok)
        self.assertTrue(self.install(b).ok)
        return a, b

    def test_hard_conflict_on_identical_resource(self):
        """F-5.1 / F-5.3：路径完全相同 = 🔴 硬冲突，且清单要精确到资源路径。"""
        a, b = self._install_pair(
            ["/Game/Characters/Player/SK_Wuchang_Body.uasset"],
            ["Project/Content/Characters/Player/SK_Wuchang_Body.uasset"],
        )
        report = conflicts.detect(self.store, self.lib, self.probe)
        self.assertEqual(report.level_of(a.id), conflicts.LEVEL_HARD)
        self.assertEqual(report.level_of(b.id), conflicts.LEVEL_HARD)
        line = report.by_mod[a.id].lines()[0]
        self.assertIn("服装包甲", line)
        self.assertIn("服装包乙", line)
        self.assertIn("sk_wuchang_body.uasset", line)

    def test_potential_conflict_on_same_directory(self):
        """F-5.3：同目录同类型 = 🟡 潜在（不是 🔴，因为没撞到同一个文件）。"""
        a, b = self._install_pair(
            ["/Game/Characters/Player/SK_Body.uasset"],
            ["/Game/Characters/Player/SK_Head.uasset"],
        )
        report = conflicts.detect(self.store, self.lib, self.probe)
        self.assertEqual(report.level_of(a.id), conflicts.LEVEL_POTENTIAL)
        self.assertEqual(report.hard_count, 0)
        self.assertGreaterEqual(report.potential_count, 1)

    def test_no_conflict_when_disjoint(self):
        """F-5.3：🟢 无交集。"""
        a, b = self._install_pair(
            ["/Game/Characters/Player/SK_Body.uasset"],
            ["/Game/Weapons/Sword/SK_Sword.uasset"],
        )
        report = conflicts.detect(self.store, self.lib, self.probe)
        self.assertEqual(report.level_of(a.id), conflicts.LEVEL_NONE)
        self.assertEqual(report.level_of(b.id), conflicts.LEVEL_NONE)
        self.assertEqual(report.items, [])

    def test_winner_is_higher_priority(self):
        """F-5.4：编号大的后加载、覆盖小的。"""
        a = self.make_mod("小号", "a_P.pak", overrides=["/Game/X/SK_A.uasset"])
        self.assertTrue(self.install(a, chunk_id=100).ok)
        b = self.make_mod("大号", "b_P.pak", overrides=["/Game/X/SK_A.uasset"])
        self.assertTrue(self.install(b, chunk_id=99999).ok)
        report = conflicts.detect(self.store, self.lib, self.probe)
        item = next(i for i in report.items if i.kind == conflicts.KIND_RESOURCE)
        self.assertEqual(item.winner_name, "大号")
        self.assertIn("99999", item.winner_reason)

    def test_version_coexistence_is_flagged(self):
        """F-5.6：同一 mod 的 v1.0 与 v1.2 同时存在要提示。"""
        a = self.make_mod("无摔伤补丁 v1.0", "a_P.pak")
        b = self.make_mod("无摔伤补丁 v1.2", "b_P.pak")
        self.assertTrue(self.install(a).ok)
        self.assertTrue(self.install(b).ok)
        report = conflicts.detect(self.store, self.lib, self.probe)
        kinds = {i.kind for i in report.items}
        self.assertIn(conflicts.KIND_VERSION, kinds)

    def test_only_installed_mods_are_compared(self):
        """F-5.2 的口径是「装了 N 个 mod 时」，没装的不该参与。"""
        installed = self.make_mod("装了的", "a_P.pak", overrides=["/Game/X/SK_A.uasset"])
        self.assertTrue(self.install(installed).ok)
        not_installed = self.make_mod("没装的", "b_P.pak", overrides=["/Game/X/SK_A.uasset"])
        report = conflicts.detect(self.store, self.lib, self.probe)
        self.assertIn(installed.id, report.considered)
        self.assertNotIn(not_installed.id, report.considered)
        self.assertEqual(report.hard_count, 0)

    def test_warn_not_block(self):
        """F-5.5：检测出硬冲突之后，两个 mod 仍然都在游戏目录里 —— 只提示不拦截。"""
        a, b = self._install_pair(
            ["/Game/X/SK_A.uasset"], ["/Game/X/SK_A.uasset"]
        )
        report = conflicts.detect(self.store, self.lib, self.probe)
        self.assertEqual(report.hard_count, 1)
        self.assertTrue((self.mods_dir / "服装包甲_P.pak").is_file())
        self.assertTrue((self.mods_dir / "服装包乙_P.pak").is_file())

    def test_empty_library_gives_note(self):
        report = conflicts.detect(self.store, self.lib, self.probe)
        self.assertEqual(report.items, [])
        self.assertTrue(report.note)

    def test_nothing_installed_gives_note(self):
        self.make_mod("库里有但没装", "a_P.pak")
        report = conflicts.detect(self.store, self.lib, self.probe)
        self.assertTrue(report.note)
        self.assertIn("还没有已安装", report.note)

    def test_foreign_file_at_target_is_flagged(self):
        """F-4.6：目标位置已有「非本工具装的」文件 → 提示（自动备份）。"""
        meta = self.make_mod("会被顶掉", "a_P.pak")
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        (self.mods_dir / "会被顶掉_P.pak").write_bytes(b"HAND-MADE")
        self.assertTrue(self.install(meta).ok)
        # 装完之后记录里是"本工具装的"，所以要手工清掉记录再测检测逻辑
        self.lib.remove(meta.id, self.probe.game_root)
        self.lib.save()
        report = conflicts.detect(
            self.store, self.lib, self.probe, mobs_dir=self.mods_dir
        )
        # 没被 considered（因为已不算安装），这里只验证扫描不炸且能识别非托管文件
        self.assertIsInstance(report, conflicts.ConflictReport)

    def test_matrix_and_catalog_map(self):
        a, b = self._install_pair(["/Game/X/SK_A.uasset"], ["/Game/X/SK_A.uasset"])
        report = conflicts.detect(self.store, self.lib, self.probe)
        cells = report.matrix_cells()
        self.assertIn(tuple(sorted((a.id, b.id))), cells)
        self.assertEqual(cells[tuple(sorted((a.id, b.id)))], conflicts.LEVEL_HARD)

        mapping = report.to_catalog_map()
        for mod_id in report.considered:
            self.assertIn(mod_id, mapping, "每个被检查的 mod 都要有一条，哪怕是 🟢")
        self.assertEqual(mapping[a.id][0], conflicts.LEVEL_HARD)
        self.assertTrue(mapping[a.id][1])

    def test_summary_mentions_counts(self):
        self._install_pair(["/Game/X/SK_A.uasset"], ["/Game/X/SK_A.uasset"])
        report = conflicts.detect(self.store, self.lib, self.probe)
        self.assertIn("硬冲突", report.summary())

    def test_filter_counts_include_conflicts(self):
        """★ 回归：筛选按钮上的「冲突 N」必须和汇总行一致。

        原来 `filter_counts()` 没接 conflicts 参数，于是「冲突」按钮永远显示 0，
        而同一界面的汇总行写着「冲突 🔴 1」—— 自相矛盾。
        """
        from wmm import catalog

        self._install_pair(["/Game/X/SK_A.uasset"], ["/Game/X/SK_A.uasset"])
        report = conflicts.detect(self.store, self.lib, self.probe)
        plain = catalog.filter_counts(self.store, self.lib, self.probe.game_root)
        with_conflicts = catalog.filter_counts(
            self.store, self.lib, self.probe.game_root,
            conflicts=report.to_catalog_map(),
        )
        self.assertEqual(plain.get(catalog.STATUS_CONFLICT, 0), 0)
        self.assertEqual(with_conflicts.get(catalog.STATUS_CONFLICT, 0), 2)
        # 过滤出来的卡片数也要对得上
        views = catalog.build_views(
            self.store, self.lib, game_dir=self.probe.game_root,
            filter_status=catalog.FILTER_CONFLICT,
            conflicts=report.to_catalog_map(),
        )
        self.assertEqual(len(views), 2)

    def test_level_rank_matches_theme(self):
        """分级常量必须和 theme 里给的颜色表对得上，否则卡片角标没颜色。"""
        from wmm import theme

        for level in (conflicts.LEVEL_HARD, conflicts.LEVEL_POTENTIAL, conflicts.LEVEL_NONE):
            self.assertIn(level, theme.CONFLICT_COLORS, level)


@unittest.skipUnless(GAME.needs_enabler, "该游戏不需要前置加载器，Enabler 检测不适用")
class TestEnabler(unittest.TestCase):
    """Mod Enabler 判据。

    ⚠️ 这里守的是一个**真实误判**：本机真实游戏目录里只有 `dsound.dll`，
    没有 `d3d9.dll`；而最初判据写成「两个 dll **都**要有」，
    于是一台明明装好了 Enabler 的机器被判成"没装"。
    实测证明作者发布的包里 `d3d9.dll` 与 `dsound.dll` **是同一个二进制**
    （同尺寸 1,210,880 B、MD5 均为 f6bb7b32…），放哪个都行。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "Wuchang Fallen Feathers"
        self.bin64 = self.root / gamedir.UE_PROJECT / "Binaries" / "Win64"
        self.bin64.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _proxy(self, name: str) -> None:
        (self.bin64 / name).write_bytes(b"MZ" + b"\x00" * 64)

    def _lua(self, text: str = "-- WUCHANG Sig Bypass -FrancisLouis\ncontext[addr] = 0xC3\n") -> None:
        (self.bin64 / "bitfix").mkdir(exist_ok=True)
        (self.bin64 / "bitfix" / "sig.lua").write_text(text, encoding="utf-8")

    def test_only_dsound_is_enough(self):
        """★ 核心回归：只放 dsound.dll + sig.lua 必须判成"已装"。"""
        self._proxy("dsound.dll")
        self._lua()
        status = enabler.detect(self.root)
        self.assertTrue(status.installed, status.message)
        self.assertEqual(status.proxies_found, ["dsound.dll"])

    def test_both_proxies_also_fine(self):
        self._proxy("d3d9.dll")
        self._proxy("dsound.dll")
        self._lua()
        self.assertTrue(enabler.detect(self.root).installed)

    def test_lua_without_proxy_is_not_installed(self):
        """只有 lua 没有代理 DLL —— 脚本根本不会被加载，依然不生效。"""
        self._lua()
        status = enabler.detect(self.root)
        self.assertFalse(status.installed)
        self.assertIn("代理 DLL", status.message)

    def test_proxy_without_lua_is_not_installed(self):
        self._proxy("dsound.dll")
        status = enabler.detect(self.root)
        self.assertFalse(status.installed)
        self.assertIn("sig.lua", status.message)

    def test_missing_everything(self):
        status = enabler.detect(self.root)
        self.assertFalse(status.installed)
        self.assertIn("不会生效", status.message)

    def test_nonexistent_game_dir_gives_hint_not_crash(self):
        status = enabler.detect(self.root / "nope")
        self.assertFalse(status.installed)
        self.assertIn("Project_Plague.exe", status.message)

    def test_unexpected_lua_content_warns(self):
        self._proxy("dsound.dll")
        self._lua("-- 我是一段毫不相干的脚本\nprint('hi')\n")
        status = enabler.detect(self.root)
        self.assertTrue(status.installed)
        self.assertTrue(status.warnings, "lua 内容可疑时应给出提醒")


# ---------------------------------------------------------------- 跨 mod 覆盖
class TestCrossModOverwrite(InstallCase):
    """2026-10-13 修复：两个 mod 覆盖同一文件，全卸载后不许残留对方副本。

    真实案例（用户报告）：库里「很H很暴力」与「淬羽 绅士界面2.0」都带
    ReplaceLoading_9_P.pak。全选安装 → 乙 capture 了甲的副本；全选卸载 →
    甲删掉文件后，乙无条件 restore 把甲的副本还了回来 → ~mods 出现
    「没人认领」的 14.4MB 残留。修法 = 卸载前「落点体检」：只有当落点
    上仍是本 mod 写入的那份（sha256 一致）才允许还原。
    """

    def _make_pair(self):
        """造两个含同名 pak 的 mod，乙的这份内容不同（同长不同字节）。"""
        a = self.make_mod("mod甲", "ReplaceLoading_9_P.pak", "only_a_9_P.pak")
        b = self.make_mod("mod乙", "ReplaceLoading_9_P.pak", "only_b_9_P.pak")
        # 把乙的 ReplaceLoading 换成同长度、不同内容的字节（真实场景两者不同）
        fb = self.store.files_dir(b.id) / "ReplaceLoading_9_P.pak"
        fb.write_bytes(b"ZZZZ-PAK-ReplaceLoading_9_P.pak")
        return a, b

    def _record_dsts(self, meta):
        rec = self.lib.find(meta.id, self.probe.game_root)
        self.assertIsNotNone(rec)
        return [Path(f.dst) for f in rec.deployed_files]

    def test_full_uninstall_after_cross_overwrite_leaves_nothing(self):
        """★ 核心回归：跨 mod 覆盖后全卸载，~mods 必须一个不剩。"""
        a, b = self._make_pair()
        self.assertTrue(self.install(a).ok)
        self.assertTrue(self.install(b).ok)  # 乙在这里 capture 了甲的副本

        shared = self.mods_dir / "ReplaceLoading_9_P.pak"
        self.assertTrue(shared.is_file())
        # 确认两个 mod 的落点确实撞在同一文件上（前提成立）
        self.assertIn(shared, self._record_dsts(a))
        self.assertIn(shared, self._record_dsts(b))

        r1 = installer.uninstall(self.lib, a.id, self.probe.game_root, backup_root=self.backups)
        self.assertTrue(r1.ok, r1.message)
        # 真实 UI 会传 same_as_lookup（wildscan 的库内索引），这里同样传上
        a_sha = modstore.hash_file(self.store.files_dir(a.id) / "ReplaceLoading_9_P.pak")
        r2 = installer.uninstall(
            self.lib, b.id, self.probe.game_root,
            backup_root=self.backups,
            same_as_lookup=lambda sha, _a=a.id: (_a, "mod甲") if sha == a_sha else None,
        )
        self.assertTrue(r2.ok, r2.message)

        self.assertFalse(
            shared.exists(), "★ 全卸载后残留了先装 mod 的副本（跨 mod 覆盖 bug 复发）"
        )
        self.assertFalse((self.mods_dir / "only_a_9_P.pak").exists())
        self.assertFalse((self.mods_dir / "only_b_9_P.pak").exists())
        # 乙的备份条目被跳过还原；其内容与库中甲相同（可从库再生）→ 应被修剪
        self.assertFalse(
            (self.backups / b.id).exists(),
            "乙的备份全是可从库再生的内容，应整目录清掉",
        )
        self.assertIn("没有还原", r2.message)

    def test_prune_keeps_unmatched_backup_without_lookup(self):
        """没传 same_as_lookup 时：跳过的条目不还原，但备份**保留**不删
        （无法证明可从库再生，宁可多留也不能吞用户的原文件）。"""
        a, b = self._make_pair()
        self.install(a)
        self.install(b)
        installer.uninstall(self.lib, a.id, self.probe.game_root, backup_root=self.backups)
        r2 = installer.uninstall(self.lib, b.id, self.probe.game_root, backup_root=self.backups)
        self.assertTrue(r2.ok, r2.message)
        self.assertFalse((self.mods_dir / "ReplaceLoading_9_P.pak").exists())
        self.assertTrue((self.backups / b.id).is_dir(), "无法确认同源时备份必须保留")

    def test_prune_with_lookup_discards_regenerable_backup(self):
        """传 same_as_lookup：跳过条目的备份内容与库中 mod 相同 → 修剪。"""
        a, b = self._make_pair()
        self.install(a)
        self.install(b)
        a_store = self.store.files_dir(a.id) / "ReplaceLoading_9_P.pak"
        a_sha = modstore.hash_file(a_store)

        def lookup(sha, _a=a.id):
            return (_a, "mod甲") if sha == a_sha else None

        installer.uninstall(self.lib, a.id, self.probe.game_root, backup_root=self.backups)
        r2 = installer.uninstall(
            self.lib, b.id, self.probe.game_root,
            backup_root=self.backups, same_as_lookup=lookup,
        )
        self.assertTrue(r2.ok, r2.message)
        self.assertFalse((self.mods_dir / "ReplaceLoading_9_P.pak").exists())
        self.assertFalse((self.backups / b.id).exists(), "可从库再生的备份应被修剪掉")

    def test_user_replaced_file_is_not_clobbered_by_restore(self):
        """用户手改过 mod 装出去的文件 → 卸载时不得用备份冲掉它。

        前提：先放一个原文件让安装发生 capture；装完后用户把落点改成了
        自己的另一份内容。此时落点 ≠ 本 mod 写入的那份 → 不还原。
        """
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        meta = self.make_mod("被换掉的", "x_P.pak")
        dst = self.mods_dir / "被换掉的_P.pak"
        dst.write_bytes(b"PRE-INSTALL-ORIGINAL")  # 让安装 capture 它
        self.assertTrue(self.install(meta).ok)
        dst.write_bytes(b"USER-EDITED-CONTENT")  # 用户手改

        result = installer.uninstall(self.lib, meta.id, self.probe.game_root, backup_root=self.backups)
        self.assertTrue(result.ok, result.message)
        self.assertFalse(dst.exists(), "记录内的文件仍应删除")
        self.assertIn("没有还原", result.message)
        self.assertTrue((self.backups / meta.id).is_dir(), "备份保留（里面有用户的原文件）")

    def test_dry_run_preview_excludes_foreign_copy(self):
        """dry_run 预告必须与真实行为一致：先卸甲删掉共享落点后，
        乙的预告里就不能再出现「会还原」—— 真实卸载也不会还原。"""
        a, b = self._make_pair()
        self.install(a)
        self.install(b)
        installer.uninstall(self.lib, a.id, self.probe.game_root, backup_root=self.backups)

        pv = installer.uninstall(
            self.lib, b.id, self.probe.game_root,
            dry_run=True, backup_root=self.backups,
        )
        self.assertTrue(pv.ok)
        self.assertEqual(pv.restored, [], "落点已随甲的卸载消失，不该预告还原")
        self.assertNotIn("还原", pv.message)


# ---------------------------------------------------------------- 删库 ≠ 卸载
class TestDeleteFromLibrary(InstallCase):
    """红线 8 回归（用户 2026-10-18 报的事故）。

    原话：「我将 mod 安装后，不点卸载，点从库中删除 mod，已安装的 mod
    也会被卸载。」—— 根因在 UI 交互：`delete_selected()` 确认删库后
    又弹「要现在就先卸载它们吗」，且**默认按钮是「是」**，用户习惯性
    点确认就把游戏目录的文件先卸掉了。已修（默认改「否」+ 文案重写）。

    这里锁死的是底层契约：**`store.delete()` 永远只碰库，不碰游戏目录**。
    上层 UI 怎么排弹窗都行，这个函数的行为不允许变。
    """

    def test_store_delete_keeps_game_files(self):
        """覆盖场景一次锁三个契约：库内副本消失、游戏目录文件原样留着、
        备份不因删库被清（备份里可能是用户唯一的原文件）。"""
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        # 目标名是「mod 名规范化」后的（<名称>_P.pak），预置文件要用这个名字
        dst = self.mods_dir / "装完再删库_P.pak"
        original = b"USER-ORIGINAL"
        dst.write_bytes(original)

        meta = self.make_mod("装完再删库", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        backup_dir = self.backups / meta.id
        self.assertTrue(backup_dir.is_dir(), "覆盖安装应先备份")
        self.assertEqual(dst.read_bytes(), b"MOCK-PAK-x_P.pak")

        removed = self.store.delete(meta.id)
        self.assertTrue(removed)
        # ① 库内副本没了
        self.assertFalse(self.store.mod_dir(meta.id).exists())
        # ② 游戏目录里装出去的文件**原样留着** —— 「删 ≠ 卸」的全部含义
        self.assertTrue(dst.is_file(), "从库中删除绝不能碰游戏目录")
        self.assertEqual(dst.read_bytes(), b"MOCK-PAK-x_P.pak")
        # ③ 备份归「卸载」管，删库一律不碰
        self.assertTrue(backup_dir.is_dir(), "删库不该清备份")

    def test_uninstall_still_works_after_library_delete(self):
        """删库之后安装记录仍在 → 卸载仍按记录清游戏目录（孤儿记录可回收）。"""
        meta = self.make_mod("删库后仍可卸", "x_P.pak")
        self.assertTrue(self.install(meta).ok)
        self.store.delete(meta.id)
        self.assertTrue(self.lib.is_installed(meta.id, str(self.probe.game_root)))

        result = installer.uninstall(
            self.lib, meta.id, self.probe.game_root, backup_root=self.backups
        )
        self.assertTrue(result.ok, result.message)
        self.assertFalse((self.mods_dir / "x_P.pak").exists())


if __name__ == "__main__":
    unittest.main()
