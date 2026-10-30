# -*- coding: utf-8 -*-
"""服装识别引擎的测试（wmm/costume.py）。

用例全部来自**真实 mod 库里的目录结构**（例如某个 mod 放在「桌面/…/明末mod」这种多层级文件夹里），
不是编出来的字符串 —— 只有这样才能保证规则是照着现实写的。
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wmm import costume
from wmm import gamedir


class TestSlots(unittest.TestCase):
    def test_chinese_keywords(self):
        self.assertEqual(costume.slots_from_text("上衣"), [costume.SLOT_CHEST])
        self.assertEqual(costume.slots_from_text("裤子  瘦腿效果"), [costume.SLOT_LEGS])
        self.assertEqual(costume.slots_from_text("真武鞋"), [costume.SLOT_FEET])

    def test_english_keywords(self):
        self.assertEqual(costume.slots_from_text("XiuPao_Jacket"), [costume.SLOT_CHEST])
        self.assertIn(costume.SLOT_HAIR, costume.slots_from_text("LongTail white"))

    def test_multiple_slots_sorted_by_order(self):
        got = costume.slots_from_text("头发 上衣 裤子")
        self.assertEqual(got, [costume.SLOT_CHEST, costume.SLOT_LEGS, costume.SLOT_HAIR])

    def test_asset_path_class_wins(self):
        self.assertEqual(
            costume.slots_from_asset_path(".../cloth_02/XiuPao_Jacket.XiuPao_Jacket"),
            [costume.SLOT_CHEST],
        )
        self.assertEqual(
            costume.slots_from_asset_path(".../Legs/XXTB_Wuchang_Slingkini_Pants.X"),
            [costume.SLOT_LEGS],
        )


class TestOutfits(unittest.TestCase):
    def test_replace_prefix_in_pak_name(self):
        self.assertIn("明甲", costume.find_outfits("EVEOceanMaid_ReplaceMing_P"))

    def test_pirate_alias(self):
        self.assertIn("海盗", costume.find_outfits("EVEMaidArisa_ReplacePirate_P"))

    def test_haikou_maps_to_pirate(self):
        self.assertIn("海盗", costume.find_outfits("白色长发 海寇和织绣都可以"))

    def test_longer_match_wins(self):
        """`织绣胸甲` 命中后不该再报 `织绣` —— 否则界面把一个东西说成两个。"""
        got = costume.find_outfits("兔女郎 织绣胸甲")
        self.assertIn("织绣胸甲", got)
        self.assertNotIn("织绣", got)

    def test_new_look_name_is_not_treated_as_target(self):
        """踩过的坑：把 `maid → 女仆装`、`EVE` 也放进词表，
        结果 `剑星女仆装 海盗服` 被判成「替换 女仆装、EVE、海盗」。
        女仆装与 EVE 是**新外观**，被替换的只有海盗。"""
        got = costume.find_outfits("剑星女仆装 海盗服 EVEMaidArisa_ReplacePirate_P")
        self.assertIn("海盗", got)
        self.assertNotIn("女仆装", got)
        self.assertNotIn("EVE", got)


class TestCostumeJson(unittest.TestCase):
    def _write(self, tmp: Path, rows) -> Path:
        p = tmp / "WCL-XXTB_Slingkini.json"
        p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return p

    def test_parses_real_shape(self):
        rows = [
            {"RowName": "XXTB Slingkini Chest",
             "CostumeObject": "/Game/Mods/WCL_Costumes/Chest/XXTB_Wuchang_Slingkini_Jacket.X",
             "CostumeType": "Chest", "CostumeName": "XXTB Slingkini[Chest]"},
            {"RowName": "XXTB Slingkini Arms",
             "CostumeObject": "/Game/Mods/WCL_Costumes/Arms/XXTB_Wuchang_Slingkini_Hands.X",
             "CostumeType": "Arms", "CostumeName": "XXTB Slingkini[Arms]"},
            {"RowName": "XXTB Slingkini Legs",
             "CostumeObject": "/Game/Mods/WCL_Costumes/Legs/XXTB_Wuchang_Slingkini_Pants.X",
             "CostumeType": "Legs", "CostumeName": "XXTB Slingkini[Legs]"},
        ]
        with TemporaryDirectory() as tmp:
            p = self._write(Path(tmp), rows)
            got = costume.parse_costume_json(p)
        self.assertIsNotNone(got)
        # 一个包里同时有上衣/护手/裤子 —— 这正是用户问的"一个 mod 里好几件东西"
        self.assertEqual(
            got.slots, [costume.SLOT_CHEST, costume.SLOT_ARMS, costume.SLOT_LEGS]
        )
        self.assertEqual(got.look_names, ["XXTB Slingkini"])
        self.assertEqual(got.rows, 3)

    def test_non_costume_json_returns_none(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "mods.json"
            p.write_text('[{"mod_name": "x", "mod_enabled": true}]', encoding="utf-8")
            self.assertIsNone(costume.parse_costume_json(p))

    def test_broken_json_returns_none(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertIsNone(costume.parse_costume_json(p))

    def test_json_beats_keywords(self):
        """有配置 JSON 时，可信度必须是 high，且部位以 JSON 为准。"""
        rows = [{"CostumeType": "Helm",
                 "CostumeObject": "/Game/Mods/X/Helm/H.H",
                 "CostumeName": "H"}]
        with TemporaryDirectory() as tmp:
            p = self._write(Path(tmp), rows)
            info = costume.analyze(
                path_segments=["上衣文件夹"],       # 关键词想说是上衣
                pak_stems=["x_P"],
                costume_jsons=[p],
            )
        self.assertEqual(info.confidence, costume.CONF_HIGH)
        self.assertEqual(info.slots, (costume.SLOT_HELM,))

    def test_native_asset_row_yields_replaced_outfit(self):
        """JSON 指向**游戏自带**资源（`/Game/Assets/`）时，那一行的 `CostumeName`
        就是被替换的原衣服名，必须进 replaces，而不是被当成新外观名。

        实物来源：服装加载器自带的 `WCL-Default_Embroided.json`（2026-10-10 实测）。
        """
        rows = [{
            "RowName": "Default Chest",
            "CostumeObject": "/Game/Assets/Character/player/player_wuchang_rebined/"
                             "cloth/cloth_02/XiuPao_Jacket.XiuPao_Jacket",
            "CostumeType": "Chest",
            "CostumeName": "Embroided Chest",
            "CostumeIcon": "/Game/Assets/UIAssets/Icon/Outfit/WuC_Base02/"
                           "WuC_Base02_Jacket_S.WuC_Base02_Jacket_S",
        }]
        with TemporaryDirectory() as tmp:
            p = self._write(Path(tmp), rows)
            parsed = costume.parse_costume_json(p)
            info = costume.analyze(pak_stems=["XTG_WuchangCostumeLoader_P"], costume_jsons=[p])
        self.assertEqual(parsed.native_names, ["Embroided Chest"])
        self.assertEqual(parsed.look_names, [])
        self.assertIn("织绣", info.replaces)

    def test_icon_slot_infers_replaced_when_all_mod_resources(self):
        """mod 自己的资源全在 `/Game/Mods/` 下时（Slingkini 这种），靠
        `CostumeIcon` 挂在哪个原套装槽位上反推（WuC_Base02 = 织绣）。

        证据来自 `WCL-Default_Embroided.json`：同一行把 Base02 槽位与
        `cloth_02` / `XiuPao` / `Embroided` 绑在一起。因为是推断，标 medium。
        """
        rows = [{
            "RowName": "XXTB Slingkini Chest",
            "CostumeObject": "/Game/Mods/WCL_Costumes/Chest/XXTB_Wuchang_Slingkini_Jacket.X",
            "CostumeType": "Chest",
            "CostumeName": "XXTB Slingkini[Chest]",
            "CostumeIcon": "/Game/Assets/UIAssets/Icon/Outfit/WuC_Base02/"
                           "WuC_Base02_Jacket_S.WuC_Base02_Jacket_S",
        }]
        with TemporaryDirectory() as tmp:
            p = self._write(Path(tmp), rows)
            info = costume.analyze(pak_stems=["zWCL-XXTB Slingkini_P"], costume_jsons=[p])
        self.assertIn("织绣", info.replaces)
        self.assertEqual(info.look_name, "XXTB Slingkini")
        self.assertEqual(info.confidence, costume.CONF_MEDIUM)

    def test_unknown_icon_slot_does_not_guess(self):
        """编号表里没有的槽位一律不猜 —— 认不出来好过认错。"""
        rows = [{
            "CostumeObject": "/Game/Mods/X/Chest/A.X",
            "CostumeType": "Chest",
            "CostumeName": "whatever",
            "CostumeIcon": "/Game/Assets/UIAssets/Icon/Outfit/WuC_Base77/WuC_Base77_Jacket_S",
        }]
        with TemporaryDirectory() as tmp:
            p = self._write(Path(tmp), rows)
            info = costume.analyze(pak_stems=["x_P"], costume_jsons=[p])
        self.assertEqual(info.replaces, ())


class TestAnalyze(unittest.TestCase):
    def test_author_taxonomy_directories(self):
        """作者按「替换哪套衣服」分的目录，外层就是被替换目标。"""
        info = costume.analyze(
            path_segments=["以往绅士mod合集", "NSFW合集", "1.衣服 套装、散件", "明甲", "女仆装"],
            pak_stems=["EVEOceanMaid_ReplaceMing_P"],
        )
        self.assertIn("明甲", info.replaces)
        self.assertEqual(info.look_name, "女仆装")
        self.assertTrue(info.ok)

    def test_category_dir_does_not_pollute_slots(self):
        """`1.衣服 套装、散件` 同时含「衣服→上衣」和「套装→整套」，
        那是**分类名**的噪音，不该给每个 mod 都盖上「整套 + 上衣」。"""
        info = costume.analyze(
            path_segments=["NSFW合集", "1.衣服 套装、散件", "蚰蜒"],
            pak_stems=["zzzzdyy"],
        )
        self.assertNotIn(costume.SLOT_CHEST, info.slots)
        self.assertNotIn(costume.SLOT_FULL, info.slots)

    def test_install_layer_words_are_noise(self):
        info = costume.analyze(
            path_segments=[gamedir.UE_PROJECT, "Content", "Paks", "~mods", "女仆大码"],
            pak_stems=["pakchunk520-Windows_P"],
        )
        # 安装层级不能被当成外观名
        self.assertEqual(info.look_name, "女仆大码")

    def test_look_name_prefers_innermost_meaningful(self):
        info = costume.analyze(
            path_segments=["明甲", "女仆大码"],
            pak_stems=["pakchunk520-Windows_P"],
        )
        self.assertEqual(info.look_name, "女仆大码")

    def test_confidence_low_when_nothing_matched(self):
        info = costume.analyze(path_segments=["新建文件夹"], pak_stems=["x_P"])
        self.assertEqual(info.confidence, costume.CONF_LOW)
        self.assertFalse(info.ok)

    def test_summary_and_slot_labels(self):
        info = costume.analyze(
            path_segments=["兔女郎 织绣胸甲"], pak_stems=["z257_P"]
        )
        text = info.summary()
        self.assertIn("替换", text)
        self.assertIn("上衣", text)

    def test_meaningless_stems_rejected(self):
        # 注：单字符（如 `x`）不在这里判 —— 那是 nameresolve 的"长度不足"规则管的事，
        # 本模块只管"这个片段是不是安装层级/pakchunk 编号/纯数字"。
        for name in ("pakchunk1200-Windows", "Content", "~mods", "12345", gamedir.UE_PROJECT):
            with self.subTest(name=name):
                self.assertTrue(costume.is_meaningless(name))

    def test_single_cjk_is_meaningful(self):
        # 真实例：作者把「全果」那档的文件夹直接命名为「果」
        self.assertFalse(costume.is_meaningless("果"))


if __name__ == "__main__":
    unittest.main()
