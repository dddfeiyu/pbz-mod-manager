# -*- coding: utf-8 -*-
"""Windows 原生「选文件夹」对话框 —— **可多选**。

为什么手写这一段 ctypes（而不是用 Qt 现成的）：
  · `QFileDialog.getExistingDirectory()` → 一次只能选**一个**目录（Qt 没暴露多选）；
  · `QFileDialog.getOpenFileNames()`     → 能多选**文件**，但在目录上按住 Ctrl
    多选再点确定，Windows 只会「进入」其中一个、**只返回那一个**。
用户 2026-10-11 明确要求「这个按钮必须有批量导入文件夹的功能」，所以只能直接调
Windows 的 `IFileOpenDialog`，带上 `FOS_PICKFOLDERS | FOS_ALLOWMULTISELECT`。

⚠️ 只在 Windows 上有意义。非 Windows、或 COM 任一环节失败 → 返回 `None`，
   调用方回退到 `getExistingDirectory()`（宁可退化成单选，也不能点了没反应）。

⚠️ vtable 下标是硬编码的，**改任何一个都要对照 shobjidl_core.h 重新数一遍**，
   数错不是报错而是**直接崩进程**（调到别的函数上去了）。
   已实测可验证的部分：IUnknown/IModalWindow/IFileDialog 段（Release=2 / Show=3 /
   SetOptions=9 / GetOptions=10 / SetTitle=17）通过"设完再读回"确认无误；
   IShellItem 段（GetDisplayName=5）在**本沙箱里验不了** —— 沙箱会拦 shell 命名空间
   （`SHCreateItemFromParsingName` / `SHParseDisplayName` 一律返 E_INVALIDARG，
   而 `SHGetFolderPathW` 正常），所以那两个下标按 SDK 头文件的标准布局取。
   因此下面特意做了**降级护栏**：点确定却读不出路径 → 抛错 → 调用方退回单选框。
"""

from __future__ import annotations

import ctypes
import os
from ctypes import POINTER, byref, c_uint, c_void_p, c_wchar_p
from typing import List, Optional, Sequence

FOS_PICKFOLDERS = 0x00000020
FOS_FORCEFILESYSTEM = 0x00000040
FOS_ALLOWMULTISELECT = 0x00000200
FOS_PATHMUSTEXIST = 0x00000800

#: SIGDN_FILESYSPATH —— 0x80058000，超过 int32 上限，参数类型必须给 c_uint
SIGDN_FILESYSPATH = 0x80058000
CLSCTX_INPROC_SERVER = 1

_CLSID_FILE_OPEN_DIALOG = "{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}"
_IID_IFILE_OPEN_DIALOG = "{D57C7288-D4AD-4768-BE02-9D969532D960}"

# IUnknown 0-2 → IModalWindow 3 → IFileDialog 4-26 → IFileOpenDialog 27-28
_VT_RELEASE = 2
_VT_SHOW = 3
_VT_SET_OPTIONS = 9
_VT_GET_OPTIONS = 10
_VT_SET_TITLE = 17
_VT_GET_RESULTS = 27
# IShellItemArray：IUnknown 0-2 → 7 GetCount / 8 GetItemAt
_VT_ARR_GET_COUNT = 7
_VT_ARR_GET_ITEM_AT = 8
# IShellItem：IUnknown 0-2 → 3 BindToHandler / 4 GetParent / 5 GetDisplayName
_VT_ITEM_GET_DISPLAY_NAME = 5


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    g = _GUID()
    if ctypes.windll.ole32.CLSIDFromString(c_wchar_p(text), byref(g)) < 0:
        raise OSError(f"CLSIDFromString 失败：{text}")
    return g


_ole32 = None


def _ole32_lib():
    """ole32 的**裸**句柄（不带 `oledll` 的 HRESULT 自动检查）。

    🚨 别换成 `ctypes.oledll.ole32`：`CoUninitialize` / `CoTaskMemFree` 返回 **void**，
       oledll 会把返回寄存器里的垃圾当成 HRESULT 去判断，于是**凭空抛 OSError** ——
       表现是"选好了文件夹却又退回单选框"，还查不出原因（实测踩过）。
       HRESULT 该检查的地方由我们**自己检查**。
    """
    global _ole32
    if _ole32 is None:
        lib = ctypes.WinDLL("ole32")
        lib.CoInitialize.argtypes = [c_void_p]
        lib.CoInitialize.restype = ctypes.c_long
        lib.CoUninitialize.argtypes = []
        lib.CoUninitialize.restype = None
        lib.CoTaskMemFree.argtypes = [c_void_p]
        lib.CoTaskMemFree.restype = None
        lib.CoCreateInstance.argtypes = [
            POINTER(_GUID), c_void_p, ctypes.c_ulong, POINTER(_GUID), POINTER(c_void_p),
        ]
        lib.CoCreateInstance.restype = ctypes.c_long
        _ole32 = lib
    return _ole32


def _call(ptr: c_void_p, index: int, argtypes: Sequence, *args) -> int:
    """按下标调一个 COM 方法（COM 的调用约定 = stdcall，Windows 上就是 WINFUNCTYPE）。"""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])(ptr, *args)


def _release(ptr) -> None:
    try:
        _call(ptr, _VT_RELEASE, ())
    except Exception:                     # noqa: BLE001 - 释放失败不该影响结果
        pass


def available() -> bool:
    """本机能不能用原生多选（只判平台，不试跑）。"""
    return os.name == "nt"


def pick_folders(parent=None, title: str = "选择一个或多个文件夹（可按住 Ctrl 多选）") -> Optional[List[str]]:
    """弹出原生文件夹框，**可多选**。

    返回值三态，调用方必须分清：
      · `list[str]` —— 选中的绝对路径（**取消时是空列表 `[]`**）
      · `None`      —— 本机/本次不可用 → 调用方应回退到单选框
    """
    if os.name != "nt":
        return None
    try:
        return _pick(parent, title)
    except Exception:                     # noqa: BLE001 - 原生调用失败交给调用方回退
        return None


def _create_dialog() -> c_void_p:
    """创建 `IFileOpenDialog` 实例。

    ⚠️ 单独提出来当**测试接缝**：测试会替换掉它，塞一个用 Python 回调伪造的
       COM 对象进来，于是能在**不弹窗**的前提下把每个 vtable 下标都真跑一遍
       （见 `tests/test_winfolders.py`）。下标写错是"崩进程"级的错误，光靠肉眼
       对照头文件不够。
    """
    dialog = c_void_p()
    hr = _ole32_lib().CoCreateInstance(
        byref(_guid(_CLSID_FILE_OPEN_DIALOG)),
        None,
        CLSCTX_INPROC_SERVER,
        byref(_guid(_IID_IFILE_OPEN_DIALOG)),
        byref(dialog),
    )
    if hr < 0 or not dialog.value:
        raise OSError(f"CoCreateInstance(FileOpenDialog) 失败：0x{hr & 0xFFFFFFFF:08X}")
    return dialog


def _pick(parent, title: str) -> List[str]:
    ole32 = _ole32_lib()
    if ole32.CoInitialize(None) < 0:
        raise OSError("CoInitialize 失败")
    try:
        dialog = _create_dialog()
        try:
            return _run(dialog, parent, title)
        finally:
            _release(dialog)
    finally:
        ole32.CoUninitialize()


def _run(dialog: c_void_p, parent, title: str) -> List[str]:
    # 先读回默认选项再叠加 —— 直接 SetOptions 会把系统默认值冲掉
    current = c_uint(0)
    _call(dialog, _VT_GET_OPTIONS, (POINTER(c_uint),), byref(current))
    wanted = (
        current.value
        | FOS_PICKFOLDERS
        | FOS_ALLOWMULTISELECT
        | FOS_FORCEFILESYSTEM
        | FOS_PATHMUSTEXIST
    )
    _call(dialog, _VT_SET_OPTIONS, (c_uint,), c_uint(wanted))
    _call(dialog, _VT_SET_TITLE, (c_wchar_p,), c_wchar_p(title))

    hwnd = 0
    if parent is not None:
        try:
            hwnd = int(parent.winId())
        except Exception:                 # noqa: BLE001 - 拿不到父窗口就当无父窗口
            hwnd = 0
    if _call(dialog, _VT_SHOW, (c_void_p,), c_void_p(hwnd)) < 0:
        return []                         # 用户取消（HRESULT_FROM_WIN32(ERROR_CANCELLED)）

    results = c_void_p()
    _call(dialog, _VT_GET_RESULTS, (POINTER(c_void_p),), byref(results))
    if not results.value:
        raise RuntimeError("原生多选文件夹框没有返回结果集")
    try:
        paths = _read_items(results)
    finally:
        _release(results)
    # 走到这里说明用户**点了确定**（取消在上面就返回了），那就至少该有一个选中项。
    # 一个都没有 = 我们这边解析失败 → 抛出去让调用方退回单选目录框，
    # 而不是"点了确定却什么都没发生"。
    if not paths:
        raise RuntimeError("原生多选文件夹框返回了空结果")
    return paths


def _read_items(results: c_void_p) -> List[str]:
    ole32 = _ole32_lib()
    count = c_uint(0)
    _call(results, _VT_ARR_GET_COUNT, (POINTER(c_uint),), byref(count))
    out: List[str] = []
    for i in range(count.value):
        item = c_void_p()
        _call(results, _VT_ARR_GET_ITEM_AT, (c_uint, POINTER(c_void_p)), c_uint(i), byref(item))
        if not item.value:
            continue
        try:
            buf = c_wchar_p()
            _call(
                item,
                _VT_ITEM_GET_DISPLAY_NAME,
                (c_uint, POINTER(c_wchar_p)),
                c_uint(SIGDN_FILESYSPATH),
                byref(buf),
            )
            if buf.value:
                out.append(str(buf.value))
                ole32.CoTaskMemFree(buf)      # 这个字符串是 COM 分配的，得由我们释放
        finally:
            _release(item)
    return out
