# -*- coding: utf-8 -*-
"""分发物料测试（需求文档 11.4 / 11.8）。

这些是**验收口径的守门测试**：说明.txt 与发布物料里的每一句约束，
都能在文档 11.7 / F-11.30 / F-11.17 里找到原文。文案是最容易在
后续编辑中被"顺手改坏"的东西（比如有人觉得「不要关闭杀毒软件」太啰嗦就删了），
所以用测试钉住。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wmm import distinfo, version  # noqa: E402
from wmm import gamedir


class TestReadme(unittest.TestCase):
    """说明.txt：F-11.16 ~ F-11.20 的五条，一条都不能少。"""

    def setUp(self) -> None:
        self.text = distinfo.build_readme(exe_sha256="a" * 64)
        self.raw = self.text.encode("utf-8")

    def test_has_all_five_required_sections(self):
        for key in ("① 系统要求", "② 被杀毒软件拦截怎么办",
                    "③ 缺少运行库怎么办", "④ 官方发布渠道与文件校验",
                    "⑤ 首次运行提示"):
            with self.subTest(section=key):
                self.assertIn(key, self.text)

    def test_system_requirement_written_out(self):
        # F-11.16：Win10 1809+ / 64 位
        self.assertIn("Windows 10 1809", self.text)
        self.assertIn("64 位", self.text)
        # 同时要写明不支持什么，否则 Win7 用户会以为是 bug
        self.assertIn("Windows 7", self.text)

    def test_never_tells_user_to_disable_antivirus(self):
        """F-11.17 + 11.7 验收：**严禁**引导用户关闭杀毒软件。

        允许出现的只有「不要关闭杀毒软件」这类**正确引导**，所以这里
        不能简单查关键词，要按行判断有没有"建议关"的语境。
        """
        for word in ("关闭杀毒", "关闭杀软", "关闭实时防护", "退出杀毒"):
            for line in self.text.splitlines():
                if word not in line:
                    continue
                with self.subTest(line=line.strip()):
                    self.assertTrue(
                        any(neg in line for neg in ("不要", "严禁", "无需", "不需要")),
                        f"这一行疑似在引导用户关闭杀毒软件：{line.strip()}",
                    )

    def test_has_explicit_dont_disable_antivirus_warning(self):
        self.assertIn("不要", self.text)
        self.assertIn("关闭杀毒软件", self.text)

    def test_teaches_whitelist_steps(self):
        # F-11.17：要给出「添加到白名单」的具体步骤
        for step in ("Windows 安全中心", "病毒和威胁防护", "排除项"):
            self.assertIn(step, self.text)

    def test_points_to_official_vc_redist(self):
        # F-11.18：指向微软官方 vc_redist.x64.exe
        self.assertIn("vc_redist.x64.exe", self.text)
        self.assertIn("microsoft", self.text.lower())
        self.assertIn("vcruntime140", self.text)

    def test_channel_and_verification_present(self):
        # F-11.19
        for ch in version.RELEASE_CHANNELS:
            self.assertIn(ch, self.text)
        self.assertIn("SHA-256", self.text)
        self.assertIn("Get-FileHash", self.text)
        self.assertIn("a" * 64, self.text, "给了 exe 哈希却没写进文档")

    def test_first_run_hint(self):
        # F-11.20：需要手动指定一次游戏目录；未装游戏也能用
        self.assertIn(gamedir.UE_PROJECT, self.text)
        self.assertIn("还没安装游戏", self.text)
        self.assertIn("APPDATA", self.text)

    def test_no_bom_and_utf8(self):
        # F-11.4：UTF-8 且不要 BOM
        self.assertFalse(self.raw.startswith(b"\xef\xbb\xbf"))

    def test_crlf_line_endings(self):
        """Windows 记事本对 LF 的处理在新版本里已经正常，但 CRLF 更保险 ——
        目标用户里总有人在用老版本。"""
        self.assertIn(b"\r\n", self.raw)
        self.assertNotIn(b"\n\n", self.raw.replace(b"\r\n", b"").replace(b"\r", b""))

    def test_no_banned_word(self):
        # F-11.30：「永久免费」是 B站 敏感词，统一用「完全免费」
        self.assertNotIn("永久免费", self.text)
        self.assertIn("完全免费", self.text)

    def test_no_banned_terms(self):
        for word in ("打赏", "赞助", "破解", "解锁会员", "秒杀同类"):
            with self.subTest(word=word):
                self.assertNotIn(word, self.text)

    def test_mentions_unofficial_status(self):
        # F-11.21
        self.assertIn("非官方", self.text)
        self.assertIn("无任何从属", self.text)

    def test_mentions_no_bundled_mods(self):
        # 第 1 章红线 + F-10.28
        self.assertIn("不包含任何游戏资源与第三方 mod", self.text)

    def test_no_markdown_markers(self):
        """说明.txt 是**纯文本**，残留 Markdown 标记会显示成字面星号/井号 ——
        用户看到的是「**冲突检测**」这种，很像乱码。"""
        self.assertNotIn("**", self.text)
        for line in self.text.splitlines():
            with self.subTest(line=line):
                self.assertFalse(line.lstrip().startswith("# "), f"残留 Markdown 标题：{line!r}")

    def test_no_promise_wording(self):
        """F-11.31：不得出现承诺性表述。

        「永远免费」和「永久免费」意思相同、同样属于承诺 —— 后者还是 B站 敏感词。
        统一下来只用「完全免费」。
        """
        for word in ("永远免费", "永久免费", "绝不收费", "无捆绑", "无广告"):
            with self.subTest(word=word):
                self.assertNotIn(word, self.text)

    def test_missing_hash_gets_placeholder_not_silence(self):
        text = distinfo.build_readme(exe_sha256=None)
        self.assertIn("非官方构建", text)

    def test_zip_name_matches_version(self):
        self.assertEqual(
            distinfo.zip_name(),
            f"{version.APP_NAME_EN}-v{version.VERSION}-win64",
        )


class TestReleaseKit(unittest.TestCase):
    """发布物料：F-11.27 ~ F-11.33。"""

    def setUp(self) -> None:
        self.text = distinfo.build_release_kit()

    def test_warns_about_editing_after_publish(self):
        # F-11.29：发布后反复改稿会触发限流
        self.assertIn("限流", self.text)

    def test_bans_short_links(self):
        # F-11.27
        self.assertIn("短链", self.text)

    def test_no_banned_words_anywhere(self):
        """B站 敏感词（F-11.30）。

        注意：物料里**必须**能出现这些词，否则没法提醒作者避开它们 ——
        所以判据是「凡出现这些词的行，必须带否定/警示语境」。
        """
        for word in ("破解", "永久免费", "解锁会员", "秒杀同类", "一键解决所有问题"):
            for line in self.text.splitlines():
                if word not in line:
                    continue
                with self.subTest(word=word, line=line.strip()):
                    self.assertTrue(
                        any(neg in line for neg in ("不要", "无", "禁", "未出现", "不得")),
                        f"这一行把敏感词写进了正式文案（不是提醒）：{line.strip()}",
                    )

    def test_covers_five_faq(self):
        # F-11.33 的五条
        for key in ("被杀软拦截", "怎么导入", "Mod Enabler", "冲突", "更新后"):
            with self.subTest(faq=key):
                self.assertIn(key, self.text)

    def test_says_do_not_close_antivirus(self):
        self.assertIn("不要关闭杀毒软件", self.text)

    def test_never_asks_for_personal_contact(self):
        # F-11.32：不公开个人邮箱 / 微信 / QQ
        self.assertIn("不公开个人邮箱", self.text)


class TestVersionTuple(unittest.TestCase):
    """exe 版本资源必须是四段数字，而 wmm.version 里写的是三段字符串。"""

    def setUp(self) -> None:
        from tools.build_dist import _version_tuple

        self.f = _version_tuple

    def test_three_part(self):
        self.assertEqual(self.f("0.1.0"), (0, 1, 0, 0))

    def test_two_part(self):
        self.assertEqual(self.f("1.2"), (1, 2, 0, 0))

    def test_four_part(self):
        self.assertEqual(self.f("1.2.3.4"), (1, 2, 3, 4))

    def test_suffix_letter_dropped(self):
        # '1.0.0b2' -> (1,0,0,0)：刻意不把 b 后面的 2 挖出来，
        # 否则会和 '1.0.2' 撞成同一个版本号（版本号是核对官方版的凭据，不能混淆）
        self.assertEqual(self.f("1.0.0b2"), (1, 0, 0, 0))

    def test_prerelease_suffix_component_kept(self):
        self.assertEqual(self.f("1.2.3-beta.4"), (1, 2, 3, 4))

    def test_garbage_becomes_zero(self):
        self.assertEqual(self.f("abc"), (0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
