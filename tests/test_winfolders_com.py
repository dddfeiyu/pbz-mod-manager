# -*- coding: utf-8 -*-
"""用**伪造的 COM 对象**把 `winfolders` 的整条链路真跑一遍。

为什么值得这么测：vtable 下标是硬编码的，写错**不是报错而是直接崩进程**
（跳到别的函数上去了）。而原生框一 `Show` 就阻塞，没法在自动测试里
真的点一下。所以这里用 Python 回调拼一个假的 `IFileOpenDialog` /
`IShellItemArray` / `IShellItem`，让 `winfolders` 照常走它自己的
`_call(ptr, index, ...)`，从而逐个验证下标：

    Release=2  Show=3  SetOptions=9  GetOptions=10  SetTitle=17
    GetResults=27 | GetCount=7  GetItemAt=8 | GetDisplayName=5

⚠️ 顺带验证 `CoTaskMemFree` 那一句 —— 假对象返回的字符串是用**真的**
   `CoTaskMemAlloc` 分配的，所以这里释放错了会崩，跑通即证明释放路径正确。
"""

from __future__ import annotations

import ctypes
import os
import sys
import unittest
from ctypes import POINTER, byref, c_uint, c_void_p, c_wchar_p
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wmm.ui import winfolders as wf          # noqa: E402

_S_OK = 0


def _alloc_wstr(text: str) -> c_wchar_p:
    """用 CoTaskMemAlloc 分配一个字符串 —— 和真 COM 的行为一致（由调用方负责释放）。

    ⚠️ 必须返回**指向那块 CoTaskMem 内存**的指针。写成 `c_wchar_p(text)`
       会另外造一块 Python 管理的缓冲区，回头 `CoTaskMemFree` 释放它就崩。
    """
    size = (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar)
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]
    ole32.CoTaskMemAlloc.restype = c_void_p
    buf = c_void_p(ole32.CoTaskMemAlloc(size))
    ctypes.memmove(buf, ctypes.create_unicode_buffer(text), size)
    return ctypes.cast(buf, c_wchar_p)


class _FakeCom:
    """把 Python 回调摆进一个真 vtable，得到一个能被 `wf._call` 接受的假 COM 指针。

    `spec` = {vtable 下标: (参数类型元组, 实现函数)}。
    实现函数的第一个参数是 `this`（我们不关心），之后才是真正的形参。
    """

    def __init__(self, spec: dict, slots: int = 40) -> None:
        self.calls: list = []
        self._spec = spec
        self._keepalive: list = []
        self._vt = (c_void_p * slots)()
        for index, (argtypes, impl) in spec.items():
            proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, *argtypes)
            bound = proto(self._wrap(index, impl))
            self._keepalive.append(bound)          # 不保引用 → 回调被 GC → 崩
            self._vt[index] = ctypes.cast(bound, c_void_p)
        self._holder = type(
            "_Obj", (ctypes.Structure,), {"_fields_": [("vtbl", POINTER(c_void_p))]}
        )()
        self._holder.vtbl = ctypes.cast(self._vt, POINTER(c_void_p))

    def _wrap(self, index, impl):
        def run(this, *args):
            self.calls.append(index)
            return impl(*args)
        return run

    @property
    def ptr(self) -> c_void_p:
        return c_void_p(ctypes.addressof(self._holder))


def _fake_item(path: str, released: list) -> _FakeCom:
    def get_display_name(sigdn, out):
        # SIGDN 是按值传的，必须是 0x80058000（> int32 上限，用 c_uint 传）
        assert sigdn == wf.SIGDN_FILESYSPATH, f"GetDisplayName 收到未知 SIGDN：{sigdn:#x}"
        out[0] = _alloc_wstr(path)
        return _S_OK

    def release():
        released.append(path)
        return 0

    return _FakeCom({
        wf._VT_ITEM_GET_DISPLAY_NAME: ((c_uint, POINTER(c_wchar_p)), get_display_name),
        wf._VT_RELEASE: ((), release),
    })


def _fake_array(paths: list, released: list, release_log: list) -> _FakeCom:
    items = [_fake_item(p, released) for p in paths]

    def get_count(out):
        out[0] = len(items)
        return _S_OK

    def get_item_at(index, out):
        assert index < len(items), f"GetItemAt 越界：{index}"
        out[0] = items[index].ptr
        return _S_OK

    def release():
        release_log.append("array")
        return 0

    com = _FakeCom({
        wf._VT_ARR_GET_COUNT: ((POINTER(c_uint),), get_count),
        wf._VT_ARR_GET_ITEM_AT: ((c_uint, POINTER(c_void_p)), get_item_at),
        wf._VT_RELEASE: ((), release),
    })
    com.items = items                       # 保引用，否则假对象被 GC → 崩
    return com


class _FakeDialog:
    """假 `IFileOpenDialog`：记录 Show/SetOptions/SetTitle 被怎么调的。"""

    def __init__(self, array_ptr: c_void_p | None) -> None:
        self.array_ptr = array_ptr
        self.options = None
        self.title = None
        self.shown = 0
        self.released = 0
        self.seen_index: list = []

        def get_options(out):
            out[0] = c_uint(0x1808)            # 系统默认值（含 FORCEFILESYSTEM 等）
            return _S_OK

        def set_options(value):
            self.options = value
            return _S_OK

        def set_title(text):
            self.title = text
            return _S_OK

        def show(hwnd):
            self.shown += 1
            return _S_OK if self.array_ptr is not None else -2147023673   # 取消

        def get_results(out):
            out[0] = self.array_ptr
            return _S_OK

        def release():
            self.released += 1
            return 0

        self._com = _FakeCom({
            wf._VT_SHOW: ((c_void_p,), show),
            wf._VT_SET_OPTIONS: ((c_uint,), set_options),
            wf._VT_GET_OPTIONS: ((POINTER(c_uint),), get_options),
            wf._VT_SET_TITLE: ((c_wchar_p,), set_title),
            wf._VT_GET_RESULTS: ((POINTER(c_void_p),), get_results),
            wf._VT_RELEASE: ((), release),
        })

    @property
    def ptr(self) -> c_void_p:
        return self._com.ptr


@unittest.skipUnless(os.name == "nt", "COM 只在 Windows 上有意义")
class TestFakeComEndToEnd(unittest.TestCase):
    def _dialog_with(self, *paths):
        released: list = []
        release_log: list = []
        array = _fake_array(list(paths), released, release_log) if paths else None
        dlg = _FakeDialog(array.ptr if array is not None else None)
        return dlg, released, release_log, array

    def test_full_path_returns_all_folders(self):
        """点了确定、选了 3 个目录 → 三个路径都要原样拿到。"""
        dlg, released, release_log, _arr = self._dialog_with(
            r"C:\fake\modA", r"C:\fake\modB", r"C:\fake\modC"
        )
        with mock.patch.object(wf, "_create_dialog", return_value=dlg.ptr):
            got = wf._pick(None, "选文件夹")
        self.assertEqual(got, [r"C:\fake\modA", r"C:\fake\modB", r"C:\fake\modC"])
        self.assertEqual(dlg.shown, 1)
        # 每个 IShellItem 都要释放（漏了就是内存泄漏 —— 反复开这个框会越吃越多）
        self.assertEqual(released, [r"C:\fake\modA", r"C:\fake\modB", r"C:\fake\modC"])
        self.assertEqual(release_log, ["array"], "结果集本身也必须释放")
        self.assertEqual(dlg.released, 1, "对话框本身必须释放")

    def test_options_include_pickfolders_and_multiselect(self):
        """两个关键标志必须**叠加**在系统默认值上，不能把默认值冲掉。"""
        dlg, *_rest = self._dialog_with(r"C:\fake\modA")
        with mock.patch.object(wf, "_create_dialog", return_value=dlg.ptr):
            wf._pick(None, "选文件夹")
        self.assertTrue(dlg.options & wf.FOS_PICKFOLDERS, "没设 FOS_PICKFOLDERS")
        self.assertTrue(dlg.options & wf.FOS_ALLOWMULTISELECT, "没设 FOS_ALLOWMULTISELECT")
        self.assertTrue(dlg.options & wf.FOS_FORCEFILESYSTEM)
        self.assertTrue(dlg.options & wf.FOS_PATHMUSTEXIST)
        self.assertTrue(dlg.options & 0x1808, "系统默认值被冲掉了（必须先 GetOptions 再或）")
        self.assertEqual(dlg.title, "选文件夹")

    def test_cancel_returns_empty_list(self):
        """取消 → `[]`（不是 None、不是异常）。"""
        dlg, released, _log, _arr = self._dialog_with()
        with mock.patch.object(wf, "_create_dialog", return_value=dlg.ptr):
            got = wf._pick(None, "选文件夹")
        self.assertEqual(got, [])
        self.assertEqual(dlg.shown, 1)
        self.assertEqual(dlg.released, 1)

    def test_ok_with_empty_result_raises_so_caller_falls_back(self):
        """点确定却读不出东西 → 必须抛异常（调用方据此退回单选框），
        绝不能"点了确定什么都没发生"。"""
        released: list = []
        release_log: list = []
        array = _fake_array([], released, release_log)
        dlg = _FakeDialog(array.ptr)
        with mock.patch.object(wf, "_create_dialog", return_value=dlg.ptr):
            self.assertIsNone(wf.pick_folders(None, "选文件夹"))

    def test_read_items_skips_null_item(self):
        """个别 item 返回空指针时跳过它，不要整体崩掉。"""
        released: list = []
        release_log: list = []
        good = _fake_item(r"C:\fake\ok", released)

        def get_count(out):
            out[0] = c_uint(2)
            return _S_OK

        def get_item_at(index, out):
            out[0] = good.ptr if index == 0 else c_void_p()
            return _S_OK

        array = _FakeCom({
            wf._VT_ARR_GET_COUNT: ((POINTER(c_uint),), get_count),
            wf._VT_ARR_GET_ITEM_AT: ((c_uint, POINTER(c_void_p)), get_item_at),
            wf._VT_RELEASE: ((), lambda: (release_log.append("array"), 0)[1]),
        })
        self.assertEqual(wf._read_items(array.ptr), [r"C:\fake\ok"])
        self.assertEqual(released, [r"C:\fake\ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
