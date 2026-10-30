# -*- coding: utf-8 -*-
"""冲突检测回归测试（需求文档 3.5 / F-5.1~F-5.6）。

2026-10-11 用户截图报：「这两个都是同一件衣服吧，怎么可以同时安装？」
—— 那一对 mod 的 `overrides` 都是空的（pak 内部读不出来，见项目记忆），
pak 名也不同，所以「目标文件名相同」与「改动同一资源」两路都测不出来。
于是新增了第三路 `KIND_REPLACES`：两个已装 mod 替换**同一件衣服**时报潜在冲突。

设计原则仍是 F-5.5「提示而非拦截」，所以这里只断言"报出来了"，
不涉及任何阻断行为。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wmm import conflicts, gamedir, library as library_mod, modstore
from wmm.library import DeployedFile, InstalledMod
from wmm.modstore import ModFile, ModMeta


def make_meta(
    mod_id: str,
    name: str,
    *,
    files: int = 1,
    replaces: list[str] | None = None,
    overrides: list[str] | None = None,
    pak_stem: str | None = None,
) -> ModMeta:
    stem = pak_stem or mod_id
    return ModMeta(
        id=mod_id,
        name=name,
        content_hash=hash(mod_id) & 0xFFFF,
        install_subdir="~mods",
        files=[
            ModFile(name=f"{stem}{i}_P.pak", rel_path=f"{stem}{i}_P.pak", size=1024, sha256=f"{i:04d}")
            for i in range(files)
        ],
        replaces=list(replaces or []),
        overrides=list(overrides or []),
        costume_source="名称/说明里的衣服名" if replaces else "",
        costume_confidence="medium" if replaces else "",
    )


class TestReplacesConflict(unittest.TestCase):
    """`KIND_REPLACES`：替换同一件衣服的两个 mod。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.game = self.root / "game"
        (self.game / gamedir.UE_PROJECT / "Content" / "Paks" / "~mods").mkdir(parents=True)
        self.store = modstore.ModStore(self.root / "mods")
        self.store.ensure()
        self.lib = library_mod.Library.load(self.root / "library.json")
        self.probe = gamedir.validate_game_dir(self.game)

    def tearDown(self):
        self._tmp.cleanup()

    def _mark_installed(self, meta: ModMeta) -> None:
        """把一个 mod 登记成「已安装」（冲突检测只看已装的）。"""
        self.store.save(meta)
        dst = self.game / gamedir.UE_PROJECT / "Content" / "Paks" / "~mods" / f"{meta.id}_P.pak"
        dst.write_bytes(b"x")
        self.lib.record_install(
            InstalledMod(
                mod_id=meta.id,
                mod_name=meta.name,
                game_dir=str(self.game),
                deployed_files=[DeployedFile(src=f"f{idx}", dst=str(dst)) for idx in range(len(meta.files))],
            )
        )

    # ---- 核心用例：用户截图里的那一对 ----

    def test_two_mods_replacing_same_outfit_are_reported(self):
        """真实案例（用户 2026-10-11 截图）：

        「全果 明军」与「海洋女仆 大灯版 黑色 明军」都 `replaces=['明军']`，
        但 pak 名不同、`overrides` 都是空的 —— 旧版两路判据都测不出来。
        """
        a = make_meta("aaa", "全果 明军", replaces=["明军"], pak_stem="sg_mingjun")
        b = make_meta("bbb", "海洋女仆 大灯版 黑色 明军", replaces=["明军"], pak_stem="maid_dark")
        self._mark_installed(a)
        self._mark_installed(b)

        report = conflicts.detect(self.store, self.lib, self.probe, only_installed=True)
        hits = [i for i in report.items if i.kind == conflicts.KIND_REPLACES]
        self.assertEqual(
            len(hits), 1,
            f"两个替换同一件衣服的 mod 必须报冲突，实际报了 {[i.line() for i in report.items]}",
        )
        self.assertEqual({hits[0].a_id, hits[0].b_id}, {"aaa", "bbb"})
        # 资源名要说清楚是「哪件衣服」
        self.assertIn("明军", hits[0].resource)

    def test_replaces_conflict_is_potential_not_hard(self):
        """`replaces` 是**推断**出来的（medium 置信度），不能当硬冲突 ——
        万一推断错了，用户仍有权强行同时启用（F-5.5）。"""
        self._mark_installed(make_meta("aaa", "A", replaces=["明军"]))
        self._mark_installed(make_meta("bbb", "B", replaces=["明军"]))
        report = conflicts.detect(self.store, self.lib, self.probe, only_installed=True)
        hit = next(i for i in report.items if i.kind == conflicts.KIND_REPLACES)
        self.assertEqual(hit.level, conflicts.LEVEL_POTENTIAL)
        self.assertEqual(hit.emoji, "🟡")

    def test_different_outfits_no_conflict(self):
        """替换**不同**衣服不该误报 —— 否则满库衣服就全是冲突了。"""
        self._mark_installed(make_meta("aaa", "A", replaces=["明军"]))
        self._mark_installed(make_meta("bbb", "B", replaces=["黑珍珠"]))
        report = conflicts.detect(self.store, self.lib, self.probe, only_installed=True)
        self.assertEqual(
            [i for i in report.items if i.kind == conflicts.KIND_REPLACES], [],
            "替换不同衣服不应报冲突",
        )

    def test_three_mods_same_outfit_pairs_all(self):
        """三个都替换同一件 → 三对两两组合都该报出来。"""
        for mid, nm in (("a1", "A"), ("b2", "B"), ("c3", "C")):
            self._mark_installed(make_meta(mid, nm, replaces=["明军"]))
        report = conflicts.detect(self.store, self.lib, self.probe, only_installed=True)
        pairs = {
            tuple(sorted((i.a_id, i.b_id)))
            for i in report.items if i.kind == conflicts.KIND_REPLACES
        }
        self.assertEqual(pairs, {("a1", "b2"), ("a1", "c3"), ("b2", "c3")})

    def test_replaces_normalized_case_and_spacing(self):
        """"明军" 与 " 明军 " / "明 军" 之外的写法（大小写/空格）应归一后比较。"""
        self._mark_installed(make_meta("aaa", "A", replaces=["MingJun"]))
        self._mark_installed(make_meta("bbb", "B", replaces=["mingjun"]))
        report = conflicts.detect(self.store, self.lib, self.probe, only_installed=True)
        self.assertTrue(
            any(i.kind == conflicts.KIND_REPLACES for i in report.items),
            "大小写不同的同一件衣服应归一后判为冲突",
        )

    def test_no_replaces_no_crash(self):
        """大部分 mod 识别不出替换目标（`replaces` 为空）—— 不能因此崩。"""
        self._mark_installed(make_meta("aaa", "A"))
        self._mark_installed(make_meta("bbb", "B"))
        report = conflicts.detect(self.store, self.lib, self.probe, only_installed=True)
        self.assertEqual([i for i in report.items if i.kind == conflicts.KIND_REPLACES], [])

    def test_uninstalled_mod_not_in_conflict(self):
        """只装了其中一个 → 不构成"同时装"（F-5.2 的口径：装了 N 个 mod 时）。"""
        self._mark_installed(make_meta("aaa", "全果 明军", replaces=["明军"]))
        self.store.save(make_meta("bbb", "海洋女仆 黑色 明军", replaces=["明军"]))
        report = conflicts.detect(self.store, self.lib, self.probe, only_installed=True)
        self.assertEqual([i for i in report.items if i.kind == conflicts.KIND_REPLACES], [])

    def test_conflicts_for_new_mod_warns_before_install(self):
        """装之前也要预警（`conflicts_for_new_mod` 走的是同一条 detect）。"""
        self._mark_installed(make_meta("aaa", "全果 明军", replaces=["明军"]))
        new = make_meta("bbb", "海洋女仆 黑色 明军", replaces=["明军"])
        self.store.save(new)
        hits = conflicts.conflicts_for_new_mod(new, self.store, self.lib, self.probe)
        self.assertTrue(
            any(h.kind == conflicts.KIND_REPLACES for h in hits),
            "安装新 mod 前应预警它替换了同一件衣服",
        )

    def test_kind_label_exists(self):
        """新 kind 必须有中文标签，否则详情页会显示裸 kind 名。"""
        self.assertIn(conflicts.KIND_REPLACES, conflicts.KIND_LABELS)
        self.assertEqual(conflicts.KIND_LABELS[conflicts.KIND_REPLACES], "替换同一件衣服")


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestGroupColors(unittest.TestCase):
    """`group_colors()` —— 冲突组配色（用户 2026-10-11 要求）。

    原话：「三个 mod 都替换同一个衣服，那这三个 mod 都变黄。如果另外三个 mod
    又替换了另一件相同的衣服，那这三个 mod 就同时变蓝。也就是当多个 mod 冲突时，
    颜色变成同一个。」
    """

    def _report(self, pairs):
        from wmm import conflicts as C

        rep = C.ConflictReport()
        for a, b, res in pairs:
            rep.items.append(
                C.ConflictItem(
                    a_id=a, a_name=a, b_id=b, b_name=b,
                    level=C.LEVEL_POTENTIAL, kind=C.KIND_REPLACES, resource=res,
                )
            )
        return rep

    def test_same_outfit_all_same_color(self):
        """三个都替换「明军」→ 三个必须是**同一个**颜色（且是第一组的黄色）。"""
        rep = self._report([("a", "b", "明军"), ("b", "c", "明军")])
        colors = conflicts.group_colors(rep)
        self.assertEqual(set(colors.values()), {conflicts.GROUP_COLORS[0]})
        self.assertEqual(colors["a"], colors["b"])
        self.assertEqual(colors["b"], colors["c"])

    def test_two_groups_get_different_colors(self):
        """两组各替换不同衣服 → 两组颜色必须不同（用户明确要"同时变蓝"）。"""
        rep = self._report([
            ("a1", "a2", "明军"), ("a2", "a3", "明军"),
            ("b1", "b2", "黑珍珠"), ("b2", "b3", "黑珍珠"),
        ])
        colors = conflicts.group_colors(rep)
        ca, cb = colors["a1"], colors["b1"]
        self.assertNotEqual(ca, cb, "两组不能同色，否则用户看不出是两批")
        for mid in ("a1", "a2", "a3"):
            self.assertEqual(colors[mid], ca)
        for mid in ("b1", "b2", "b3"):
            self.assertEqual(colors[mid], cb)

    def test_transitive_chain_merges_into_one_group(self):
        """甲⟷乙、乙⟷丙（甲丙**没有**直接条目）→ 三者同组。

        这是用连通分量而不是"按冲突对象"分组的理由：真实冲突是网状的，
        用户要的是"互相牵连的一批看起来是一伙的"。
        """
        rep = self._report([("a", "b", "X"), ("b", "c", "Y")])
        colors = conflicts.group_colors(rep)
        self.assertEqual(colors["a"], colors["b"])
        self.assertEqual(colors["b"], colors["c"])

    def test_non_conflicting_mods_absent(self):
        """没冲突的 mod 不该出现在返回值里 —— 卡片据此判断要不要上色。"""
        rep = self._report([("a", "b", "X")])
        colors = conflicts.group_colors(rep)
        self.assertNotIn("z", colors)
        self.assertEqual(set(colors), {"a", "b"})

    def test_single_sided_conflict_not_in_groups(self):
        """单边冲突（落点已有别人的文件，b_id 为空）不该拉出一个"组"给谁。"""
        from wmm import conflicts as C

        rep = C.ConflictReport()
        rep.items.append(
            C.ConflictItem(
                a_id="a", a_name="A", b_id="", b_name="",
                level=C.LEVEL_POTENTIAL, kind=C.KIND_FOREIGN, resource="x_P.pak",
            )
        )
        colors = conflicts.group_colors(rep)
        self.assertNotIn("a", colors, "只有一方时不该算冲突组")

    def test_color_assignment_is_stable_across_calls(self):
        """⚠️ 颜色必须**可重现** —— 同一份报告反复算要得到同一结果。

        若用 set/dict 的迭代顺序建图，Python 的哈希随机化会让同一组
        重启后变成别的颜色，用户会以为"颜色变了"。
        """
        rep = self._report([
            ("a", "b", "X"), ("c", "d", "Y"),
        ])
        first = conflicts.group_colors(rep)
        for _ in range(20):
            self.assertEqual(conflicts.group_colors(rep), first)

    def test_empty_report_returns_empty(self):
        self.assertEqual(conflicts.group_colors(conflicts.ConflictReport()), {})

    def test_group_colors_all_used_values_are_valid_hex(self):
        """返回的必须是 `#RRGGBB`，否则 QPen 会画不出/画出黑色。"""
        rep = self._report([(f"m{i}", f"m{i+1}", f"R{i}") for i in range(30)])
        for mid, color in conflicts.group_colors(rep).items():
            self.assertRegex(color, r"^#[0-9A-Fa-f]{6}$", f"{mid} 的颜色 {color!r} 不是合法 hex")
