# -*- coding: utf-8 -*-
"""启动窗口尺寸 clamp 到屏幕可用区域（2026-10-13 加，小屏 / 高 DPI 兜底）。

背景：Qt6 的自动 DPI 缩放只保证「界面不变形」，不保证「窗口在屏幕内」。
1366×768 @150% 的逻辑分辨率只有 911×512（扣任务栏后可用高约 464），
而 version.MIN_WINDOW_WIDTH/HEIGHT（1000×640）与 *_HARD（720×520）都放不进去，
底部工具栏会落在屏幕外面点不到。这些用例把三个保证钉死：
  1. 正常屏幕恒等（零影响，这是能上车的前提）；
  2. 放不下时收缩到可用区域；
  3. 极端小屏允许临时低于硬最小，保证窗口完整可见。
"""

import unittest

from wmm.ui.main_window import fit_window_to_screen


class FitWindowToScreenTest(unittest.TestCase):
    def test_normal_screen_identity(self):
        """大屏：结果必须与输入完全一致（不能悄悄改用户尺寸）。"""
        self.assertEqual(fit_window_to_screen(1233, 897, 2624, 1424), (1233, 897))
        self.assertEqual(fit_window_to_screen(1000, 640, 2624, 1424), (1000, 640))

    def test_1080p_at_150pct_identity(self):
        """1920×1080 @150% → 逻辑 1280×720，扣任务栏可用约 1280×672。

        这是最常见的高分屏缩放档位，启动尺寸与硬最小都必须原样通过。
        """
        self.assertEqual(fit_window_to_screen(1000, 640, 1280, 672), (1000, 640))
        self.assertEqual(fit_window_to_screen(720, 520, 1280, 672), (720, 520))

    def test_1366x768_at_150pct_shrinks(self):
        """1366×768 @150% → 逻辑 911×512：启动尺寸放不下，必须收缩。"""
        self.assertEqual(fit_window_to_screen(1000, 640, 911, 512), (895, 496))

    def test_hard_min_height_also_shrinks(self):
        """同场景下硬最小 720×520：宽放得下、高放不下 → 只降高不降宽。"""
        self.assertEqual(fit_window_to_screen(720, 520, 911, 512), (720, 496))

    def test_extreme_small_screen_goes_below_hard_min(self):
        """极端小屏：允许低于硬最小，保证窗口完整可见（能点到底部工具栏）。"""
        w, h = fit_window_to_screen(1000, 640, 800, 480)
        self.assertEqual((w, h), (784, 464))
        self.assertLess(h, 520, "极端小屏下必须允许低于硬最小高度")

    def test_never_zero_or_negative(self):
        """可用区域退化到 0/1 时也不能返回 0 或负数（Qt 会拒绝这种尺寸）。"""
        for aw, ah in ((16, 16), (10, 10), (1, 1), (0, 0), (-5, -5)):
            w, h = fit_window_to_screen(1000, 640, aw, ah)
            self.assertGreaterEqual(w, 1, f"avail=({aw},{ah}) 得到 w={w}")
            self.assertGreaterEqual(h, 1, f"avail=({aw},{ah}) 得到 h={h}")

    def test_result_always_fits_inside_available(self):
        """任何输入下，结果都不超过「可用区域减 16px 边缘」。"""
        for aw, ah in ((911, 512), (1280, 720), (1920, 1080), (640, 480)):
            w, h = fit_window_to_screen(1600, 1000, aw, ah)
            self.assertLessEqual(w, max(1, aw - 16), f"avail_w={aw}")
            self.assertLessEqual(h, max(1, ah - 16), f"avail_h={ah}")


if __name__ == "__main__":
    unittest.main()
