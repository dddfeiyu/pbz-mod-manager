# -*- coding: utf-8 -*-
"""`wmm.ui.winfolders`（原生「可多选」文件夹框）的纯逻辑测试。

⚠️ 这里**不弹窗** —— 原生框一 `Show` 就阻塞，没法在自动测试里跑。
   `Show` 之后的取路径分支（`GetResults` → `IShellItemArray` → `IShellItem`）
   在**本沙箱里也验不了**：沙箱拦 shell 命名空间
   （`SHCreateItemFromParsingName` / `SHParseDisplayName` 一律返 E_INVALIDARG，
   而 `SHGetFolderPathW` 正常）。所以这里只钉住最要紧的一条性质：
   **任何一环失败都必须优雅返回 None，让调用方退回去用单选框**，
   绝不能"点了没反应"或者把异常抛到 UI 线程上。
"""

from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wmm.ui import winfolders as wf          # noqa: E402


class TestWinFoldersDegrade(unittest.TestCase):
    """失败路径必须降级（不抛异常、不装死）。"""

    def test_pick_returns_none_when_com_raises(self):
        """COM 任一环节抛异常 → 返回 None（= 调用方应回退到单选）。"""
        with mock.patch.object(wf, "_pick", side_effect=OSError("COM 挂了")):
            self.assertIsNone(wf.pick_folders(None, "t"))

    def test_pick_returns_none_on_non_windows(self):
        """非 Windows → 返回 None，不能硬走 ctypes.windll。"""
        fake = types.SimpleNamespace(name="posix")
        with mock.patch.object(wf, "os", fake):
            self.assertIsNone(wf.pick_folders(None, "t"))

    def test_available_is_bool(self):
        self.assertIsInstance(wf.available(), bool)

    def test_cancelled_returns_empty_list_not_none(self):
        """取消（`Show` 返回负 HRESULT）必须是 `[]` 而**不是** `None`。

        这两者语义完全不同：`[]` = 用户自己取消了（什么都别做），
        `None` = 我们这边不可用（要回退到单选框）。搞混会让"取消"变成
        "弹完原生框又弹一个 Qt 框"，用户会以为按钮坏了。
        """
        with mock.patch.object(wf, "_run", return_value=[]):
            self.assertEqual(wf.pick_folders(None, "t"), [])


class TestWinFoldersWiring(unittest.TestCase):
    """接线常量与模块纯净性 —— 写错了**不报错、直接崩**，所以值得钉死。"""

    def test_fos_flags_match_sdk(self):
        self.assertEqual(wf.FOS_PICKFOLDERS, 0x20)
        self.assertEqual(wf.FOS_FORCEFILESYSTEM, 0x40)
        self.assertEqual(wf.FOS_ALLOWMULTISELECT, 0x200)
        self.assertEqual(wf.FOS_PATHMUSTEXIST, 0x800)
        # SIGDN_FILESYSPATH 超过 int32 上限 —— 参数类型必须是 c_uint，否则变负数
        self.assertEqual(wf.SIGDN_FILESYSPATH, 0x80058000)
        self.assertGreater(wf.SIGDN_FILESYSPATH, 2 ** 31 - 1)

    def test_vtable_indices_match_sdk(self):
        """vtable 下标（IUnknown 0-2 → IModalWindow 3 → IFileDialog 4-26 → IFileOpenDialog 27+）。"""
        self.assertEqual(wf._VT_RELEASE, 2)
        self.assertEqual(wf._VT_SHOW, 3)
        self.assertEqual(wf._VT_SET_OPTIONS, 9)
        self.assertEqual(wf._VT_GET_OPTIONS, 10)
        self.assertEqual(wf._VT_SET_TITLE, 17)
        self.assertEqual(wf._VT_GET_RESULTS, 27)
        self.assertEqual(wf._VT_ARR_GET_COUNT, 7)
        self.assertEqual(wf._VT_ARR_GET_ITEM_AT, 8)
        self.assertEqual(wf._VT_ITEM_GET_DISPLAY_NAME, 5)

    def test_guid_rejects_garbage(self):
        self.assertRaises(OSError, wf._guid, "这不是 GUID")

    def test_module_does_not_import_qt(self):
        """这个模块必须能脱离 Qt 导入（`wmm/` 的既有约定），否则测试跑不起来。"""
        src = Path(wf.__file__).read_text(encoding="utf-8")
        self.assertNotIn("PySide6", src)
        self.assertNotIn("QtWidgets", src)

    @unittest.skipUnless(os.name == "nt", "只在 Windows 上有意义")
    def test_guid_roundtrip(self):
        """CLSIDFromString 的结果能被 StringFromGUID2 还原（证明 GUID 结构体没写错）。"""
        import ctypes
        from ctypes import byref

        text = "{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}"
        buf = ctypes.create_unicode_buffer(80)
        ctypes.oledll.ole32.StringFromGUID2(byref(wf._guid(text)), buf, 80)
        self.assertEqual(buf.value, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
