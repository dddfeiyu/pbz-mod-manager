# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（需求文档 11.2 / 11.3 / 6）。

三条硬要求，全部体现在下面：

  * **onedir，绝不 onefile**（F-11.3）—— onefile 每次启动都要把自身解压到
    %TEMP%，启动慢 5~20 秒、被杀软重点扫描、临时目录被清理时还会异常。
    用户会把"启动慢"直接理解成"这软件有问题"。
  * **不得启用 UPX**（F-11.4）—— UPX 是杀软误报最主要的技术诱因，
    为省几 MB 换来成批误报极不划算。
  * **必须写入版本信息与图标**（F-11.5）—— 「属性 → 详细信息」一片空白的 exe
    在杀软评分中直接扣分。

由 tools/build_dist.py 调用；它负责先生成 assets/icon.ico 与 build/version_info.txt。
"""

import os
from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()          # noqa: F821  PyInstaller 注入
VERSION_INFO = SPEC_DIR / "build" / "version_info.txt"
ICON = SPEC_DIR / "assets" / "icon.ico"

if not VERSION_INFO.is_file():
    raise SystemExit(
        "缺少 build/version_info.txt —— 请用 tools/build_dist.py 打包，"
        "它会先从 wmm/version.py 生成版本信息（避免两处版本号对不上）。"
    )
if not ICON.is_file():
    raise SystemExit("缺少 assets/icon.ico —— 请先运行 tools/make_icon.py 生成自制图标。")

# 随包资源：图标 + 三档预览图之外的东西。
# 注意 PyInstaller 6 会把 datas 放进 _internal/，而 wmm.apppaths.resource_path()
# 用的正是 sys._MEIPASS（= _internal），两边口径一致。
datas = [
    (str(ICON), "assets"),
]

# 前置组件（签名绕过）是否随包分发 —— 2026-10-30 加条件：
#   · B 站版：内置（已署名 + 非商业，符合 CC BY-NC）；
#   · Nexus 版：**必须不带**（原作者 permissions 禁止上传到其它站点，
#     N 网规则也不许把别人的 mod 打包分发）。
# 打包时把 assets/enabler 临时改名即可产出「无前置版」，不必改代码。
_ENABLER_DIR = SPEC_DIR / "assets" / "enabler"
if _ENABLER_DIR.is_dir():
    datas.append((str(_ENABLER_DIR), "assets/enabler"))

# 明确剔掉用不到的重量级模块。少一个模块 = 少一批被杀软扫的二进制、
# 少几十 MB 体积（用户下载更快，蓝奏云 100MB 的单文件限制也更容易过）。
#
# ⚠️ 两条踩过的坑，务必保留这两条注释：
#
#   1) **绝不要排除 `shiboken6.Shiboken`。** 它看着像"内部实现细节"，
#      实际上是 PySide6 所有绑定赖以工作的底座。排除掉的后果是打包产物
#      一启动就 `ModuleNotFoundError: No module named 'shiboken6.Shiboken'`。
#      这个错**在开发机上永远复现不出来**（开发机是直接 import 源码跑的），
#      只有跑打包产物才看得见 —— 正是需求文档 R-20 说的那类问题。
#      现在由 tools/dist_check.py 在真实 exe 上守着。
#
#   2) **PySide6 的子模块一律不排除。** Qt 的 DLL 之间依赖关系不透明，
#      少一个 Qt6Xxx.dll 往往要到运行时某个具体交互才炸。这里只排除
#      纯 Python 的、和 Qt 无关的重量级东西（tkinter / 测试框架等）,
#      体积换稳定性是划算的。
excludes = [
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "test",
    "lib2to3",
]

# Cython 保护层（用户 2026-10-14 拍板的防倒卖方案）：build/cython/ 下的 .pyd
# 存在才启用 —— 由 tools/build_dist.py 的 cythonize_core() 生成，编译失败
# （没装 zig 等）时文件不存在，本 spec 自动回退源码打包，发布链不受影响。
#
# 生效时这两个模块以**机器码**形式进包（.pyd 放 _internal/wmm/），源码 .py
# 被 excludes 挡在 import 图外 —— 解包者拿到的是 Cython 生成的 C 再编出来的
# 原生扩展，没有字节码可反编译。UI 等模块仍是源码（它们没有秘密）。
CYTHON_DIR = SPEC_DIR / "build" / "cython"
core_binaries = []
core_excludes = []
for _name in ("modstore", "installer"):
    _pyd = CYTHON_DIR / f"{_name}.cp313-win_amd64.pyd"
    if _pyd.is_file():
        core_binaries.append((str(_pyd), "wmm"))
        core_excludes.append(f"wmm.{_name}")

a = Analysis(                                  # noqa: F821
    [str(SPEC_DIR / "main.py")],
    pathex=[str(SPEC_DIR)],
    binaries=core_binaries,
    datas=datas,
    hiddenimports=[
        # Qt 的图片格式插件是靠运行时加载的，PyInstaller 的静态分析看不到，
        # 漏掉的话用户机上预览图会**全空白**（真实 mod 的预览图 101 张是 webp）。
        "PySide6.QtPlugins",
        # `wmm.distinfo` 只在 tools/ 里被用到，主程序没有静态 import 它，
        # 不显式声明就会被漏掉 —— 而它是「说明.txt 由程序自身口径生成」的依据，
        # 属于产品的一部分，不是开发期脚本。
        "wmm.distinfo",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes + core_excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)                              # noqa: F821

exe = EXE(                                     # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=os.environ.get("WMM_EXE_NAME") or "影之刃零mod管理器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # F-11.4：**绝不启用 UPX**。即使系统里装了 upx，这里也显式关掉。
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # 无控制台窗口 —— 这是给玩家用的图形工具，弹黑框会吓到人。
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
    version=str(VERSION_INFO),
)

coll = COLLECT(                                # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,                                 # F-11.4
    upx_exclude=[],
    name=os.environ.get("WMM_EXE_NAME") or "影之刃零mod管理器",
)
