# -*- coding: utf-8 -*-
"""无预览图的占位图标选型（`wmm/placeholder.py`）。

用户 2026-10-11 的原话：「这些没有预览图的 mod 怎么处理会更好些？」
定了「按类型画图标」这条路，这些测试钉住的就是**判对**，尤其是几条
"看着像其实不是"的反例。
"""

from __future__ import annotations

import unittest

from wmm import costume, placeholder, theme


def kind_of(name="", stems=(), slots=()):
    return placeholder.classify(name=name, pak_stems=list(stems), slots=list(slots))


class TestPlaceholderClassify(unittest.TestCase):
    def test_costume_slots(self):
        """服装部位直接来自 meta.slots（costume.py 的四路证据算出来的）。"""
        cases = [
            (["Chest"], "上衣"),
            (["Legs"], "裤子/裙"),
            (["Feet"], "鞋靴"),
            (["Hair"], "发型"),
            (["Helm"], "头饰"),
            (["Arms"], "护手"),
            (["Face"], "面部"),
            (["Body"], "身形"),
            (["Full"], "整套"),
        ]
        for slots, expected in cases:
            with self.subTest(slots=slots):
                kind = kind_of(name="随便一个名字", slots=slots)
                self.assertEqual(kind, slots[0])
                self.assertEqual(placeholder.label_for(kind), expected)

    def test_slot_order_is_deterministic(self):
        """一个 mod 命中多个部位时取 SLOT_ORDER 里最靠前的 —— 不能依赖集合顺序。"""
        self.assertEqual(kind_of(slots=["Chest", "Full"]), "Full")
        self.assertEqual(kind_of(slots=["Legs", "Chest"]), "Chest")

    # ---------------------------------------------------------- 武器
    def test_weapon_from_pak_name(self):
        """`WPN_xxx.pak` 是这游戏武器模型的真实命名（实测过 352 个 pak）。"""
        self.assertEqual(kind_of(stems=["WPN_zaozi_reverse_99_P"]), "weapon")
        self.assertEqual(placeholder.label_for("weapon"), "武器")

    def test_weapon_from_name_tail(self):
        """用户点名的那个「长刀」mod。带「模型」这种噪声后缀也要认得。"""
        for name in ("长刀", "长刀 模型", "长刀模型", "青锋剑", "双刀"):
            with self.subTest(name=name):
                self.assertEqual(kind_of(name=name), "weapon", name)

    def test_weapon_keyword_must_be_at_tail_or_token(self):
        """🚨 反向守：名字里**有个武器字但整体不是武器**时必须落到服装。

        真实反例：「剑星黑色冷却服」—— 剑星是那个角色名，它是一套衣服。
        用"包含"判就会给它画一把刀。
        """
        for name, slots in (("剑星黑色冷却服", ["Full"]),
                            ("刀锋战士套装", ["Chest"])):
            with self.subTest(name=name):
                kind = kind_of(name=name, slots=slots)
                self.assertNotEqual(kind, "weapon", f"{name} 不该被判成武器")
                self.assertIn(kind, ("Chest", "Full", "Legs"), name)
        # 独立片段则应该认（`长刀 模型` 里的 `长刀`）
        self.assertEqual(kind_of(name="长刀 模型"), "weapon")

    def test_name_beats_slot_list_when_unambiguous(self):
        """名字里写着部位的，图标要跟名字对得上。

        真实反例：`黑红短裙` 的 `meta.slots` 是 `[Chest, Legs, Face, Body]`，
        按 `SLOT_ORDER` 取第一个会画成"上衣" —— 可名字里明明写着"短裙"。
        """
        self.assertEqual(kind_of(name="黑红短裙", slots=["Chest", "Legs"]), "Legs")
        self.assertEqual(kind_of(name="宫女缎裙 清凉", slots=["Chest", "Legs"]), "Legs")
        self.assertEqual(kind_of(name="LongTail white", slots=["Chest", "Hair"]), "Hair")

    def test_ambiguous_name_falls_back_to_slot_list(self):
        """名字给不出唯一部位（或给不出部位）时，仍按 slots 走，不瞎猜。"""
        self.assertEqual(kind_of(name="方灵 半果", slots=["Chest", "Legs"]), "Chest")
        self.assertEqual(kind_of(name="随便一个名字", slots=["Feet"]), "Feet")

    # ---------------------------------------------------------- 技能
    def test_skill_from_pak_name(self):
        """真实：`改武器技能` 的 pak 叫 `SkillChange_1_P.pak`。"""
        self.assertEqual(kind_of(stems=["SkillChange_1_P"]), "skill")
        self.assertEqual(kind_of(stems=["ItemSkillChange_1_P"]), "skill")
        self.assertEqual(kind_of(stems=["GA_FireBurst"]), "skill")

    def test_skill_pak_name_beats_bogus_slot(self):
        """🚨 反向守：`meta.slots` 里的错误部位**不能压过** pak 名这条硬证据。

        「改武器技能」的 `slots` 里挂着 `Chest`（旧版服装证据的误判），
        但它的 pak 名写着 `Skill` —— 必须判技能，不能给它画件 T 恤。
        """
        self.assertEqual(
            kind_of(name="改武器技能", stems=["SkillChange_1_P"], slots=["Chest"]),
            "skill",
        )

    def test_skill_from_name(self):
        """用户点名的「水球术」：一个「术」字在结尾就够了。"""
        for name in ("水球术", "火焰术", "改技能", "召唤法术"):
            with self.subTest(name=name):
                self.assertEqual(kind_of(name=name), "skill", name)

    def test_bare_shu_is_not_a_skill(self):
        """单独的「术」只在结尾才算，免得「艺术」这种被误伤。"""
        self.assertNotEqual(kind_of(name="艺术图集", slots=["Chest"]), "skill")

    # ---------------------------------------------------------- 界面 / 兜底
    def test_ui(self):
        self.assertEqual(kind_of(name="崩羽界面 绅士贴图"), "ui")
        self.assertEqual(kind_of(name="字体替换包"), "ui")
        self.assertEqual(kind_of(name="UI Overhaul"), "ui")

    def test_unknown_falls_back_to_neutral(self):
        """认不出来**不硬猜** —— 落到中性的「其他」，而不是随便安一个部位。"""
        for name, stem in (("无摔伤", "pakchunk1200-Windows"),
                           ("", "TestPak"),
                           ("随便", "")):
            with self.subTest(name=name, stem=stem):
                self.assertEqual(kind_of(name=name, stems=[stem] if stem else []), "other")

    def test_label_for_unknown_kind_does_not_raise(self):
        self.assertEqual(placeholder.label_for("完全不认识的键"), "其他")

    def test_classify_is_deterministic(self):
        a = kind_of(name="长刀 模型", stems=["WPN_x"], slots=["Chest"])
        b = kind_of(name="长刀 模型", stems=["WPN_x"], slots=["Chest"])
        self.assertEqual(a, b)
        self.assertEqual(a, "weapon")


class TestPlaceholderColors(unittest.TestCase):
    def test_every_slot_has_a_color(self):
        """theme 里用了字面量键（不 import costume）—— 靠这条守项钉住不许漏。"""
        missing = [s for s in costume.SLOT_ORDER if s not in theme.PLACEHOLDER_COLORS]
        self.assertEqual(missing, [], f"这些槽位在 theme.PLACEHOLDER_COLORS 里没有配色：{missing}")

    def test_every_kind_has_a_color(self):
        kinds = [
            placeholder.KIND_WEAPON, placeholder.KIND_SKILL,
            placeholder.KIND_UI, placeholder.KIND_OTHER,
        ]
        missing = [k for k in kinds if k not in theme.PLACEHOLDER_COLORS]
        self.assertEqual(missing, [], f"这几类没有配色：{missing}")

    def test_colors_are_hex(self):
        for key, val in theme.PLACEHOLDER_COLORS.items():
            with self.subTest(key=key):
                self.assertRegex(val, r"^#[0-9A-Fa-f]{6}$")

    def test_labels_exist_for_every_color_key(self):
        """每个能画出来的 kind 都得有中文小字（图标下面那行），不能是空串。"""
        for key in theme.PLACEHOLDER_COLORS:
            with self.subTest(key=key):
                self.assertTrue(placeholder.label_for(key).strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
