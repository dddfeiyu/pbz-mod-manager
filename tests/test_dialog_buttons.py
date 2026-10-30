# -*- coding: utf-8 -*-
"""三按钮无声对话框的回归测试（用户 2026-10-18 反馈的产物）。

背景：删库时的「还装在游戏里」弹窗原先走 `QMessageBox.question`，
但 `app._silence_message_boxes()` 会把 QMessageBox 换成**两颗按钮**
（是/取消）的 `dialogs.ask` —— 传的 Yes|No + 默认 No 被整段丢掉，
用户界面上根本没有「否」（原话：「没有否」）。终版改成 SilentDialog
三按钮：主按钮「否，只删库」粉色实心（与「安装所选」同款），
默认焦点落在它上面。

这里只做**离屏冒烟**：不 exec()（不阻塞），验证按钮构成、文案、
默认焦点与 choice 初值。运行环境无显示设备，用 offscreen 平台。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_dialog_buttons -v
"""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from wmm.ui import dialogs  # noqa: E402


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _buttons(dlg) -> list:
    return [b for b in dlg.findChildren(QPushButton)]


class TestSilentDialogButtons(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()
        # ⚠️ 弹窗会写 logs/dialogs.log（诊断日志），必须隔离到临时目录，
        # 绝不碰真实 %APPDATA%（2026-10-18 教训：第一次跑忘了隔离，
        # 7 条测试记录写进了用户的 logs 目录）。
        cls._old_env = os.environ.get("WMM_DATA_DIR")
        cls._tmp_dir = tempfile.mkdtemp(prefix="wmm-dlg-test-")
        os.environ["WMM_DATA_DIR"] = cls._tmp_dir

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil

        if cls._old_env is None:
            os.environ.pop("WMM_DATA_DIR", None)
        else:
            os.environ["WMM_DATA_DIR"] = cls._old_env
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def _make(self, **kw):
        base = dict(title="t", body="b")
        base.update(kw)
        dlg = dialogs.SilentDialog(**base)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_two_button_default_unchanged(self):
        """ask() 的老用法：只有 是/取消 两颗，默认取消。

        ⚠️ 两颗都**不能**是粉色主样式 —— 2026-10-18 回归教训：yes_primary
        曾默认 True，导致所有确认框的「确定」都变粉，与改动前不一致。
        """
        dlg = self._make(yes_text="确定", cancel_text="取消")
        texts = sorted(b.text() for b in _buttons(dlg))
        self.assertEqual(texts, ["取消", "确定"])
        self.assertEqual(dlg.choice, "cancel")
        default = [b for b in _buttons(dlg) if b.isDefault()]
        self.assertEqual([b.text() for b in default], ["取消"])
        for b in _buttons(dlg):
            self.assertNotEqual(b.objectName(), "primaryButton")

    def test_three_button_layout(self):
        """删库弹窗：否，只删库 / 是，先卸载再删库 / 取消。"""
        dlg = self._make(
            no_text="否，只删库",
            yes_text="是，先卸载再删库",
            cancel_text="取消",
            default="no",
        )
        texts = [b.text() for b in _buttons(dlg)]
        self.assertEqual(texts, ["否，只删库", "是，先卸载再删库", "取消"])
        self.assertEqual(dlg.choice, "cancel")

    def test_default_focus_on_no(self):
        """默认焦点与默认按钮都必须是「否，只删库」—— 回车手滑也安全。"""
        dlg = self._make(
            no_text="否，只删库",
            yes_text="是，先卸载再删库",
            cancel_text="取消",
            default="no",
        )
        default = [b for b in _buttons(dlg) if b.isDefault()]
        self.assertEqual([b.text() for b in default], ["否，只删库"])

    def test_primary_buttons_are_pink(self):
        """显式开启 primary：主按钮走 #primaryButton（theme.PRIMARY 粉），
        与「安装所选」同款。"""
        from wmm import theme

        dlg = self._make(
            no_text="否，只删库",
            yes_text="是，先卸载再删库",
            cancel_text="取消",
            default="no",
            yes_primary=True,
            no_primary=True,
        )
        by_text = {b.text(): b for b in _buttons(dlg)}
        self.assertEqual(by_text["否，只删库"].objectName(), "primaryButton")
        self.assertEqual(by_text["是，先卸载再删库"].objectName(), "primaryButton")
        # 取消键不是主样式
        self.assertNotEqual(by_text["取消"].objectName(), "primaryButton")
        # 粉色值确实来自主题（安装所选用的就是它）
        self.assertEqual(theme.PRIMARY, "#FB7299")

    def test_yes_can_be_secondary(self):
        """「是，先卸载再删库」是危险动作 → 可以退成描边次按钮（不抢权重）。"""
        dlg = self._make(
            no_text="否，只删库",
            yes_text="是，先卸载再删库",
            cancel_text="取消",
            default="no",
            yes_primary=False,
        )
        by_text = {b.text(): b for b in _buttons(dlg)}
        self.assertEqual(by_text["否，只删库"].objectName(), "primaryButton")
        self.assertNotEqual(by_text["是，先卸载再删库"].objectName(), "primaryButton")

    def test_no_choice_flows(self):
        """点「否」→ choice=no；点「是」→ choice=yes；不点 → cancel。"""
        dlg = self._make(
            no_text="否，只删库",
            yes_text="是，先卸载再删库",
            cancel_text="取消",
            default="no",
        )
        by_text = {b.text(): b for b in _buttons(dlg)}
        by_text["否，只删库"].click()
        self.assertEqual(dlg.choice, "no")
        dlg2 = self._make(
            no_text="否，只删库", yes_text="是，先卸载再删库", cancel_text="取消", default="no"
        )
        by2 = {b.text(): b for b in _buttons(dlg2)}
        by2["是，先卸载再删库"].click()
        self.assertEqual(dlg2.choice, "yes")

    def test_ask_uses_choice_not_legacy_attr(self):
        """ask() 的返回值走 choice（旧属性 _result_yes 已删，别再引用）。"""
        src = open(dialogs.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
        self.assertNotIn("_result_yes", src)
        self.assertIn('return dlg.choice == "yes"', src)


if __name__ == "__main__":
    unittest.main()
