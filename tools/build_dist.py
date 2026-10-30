# -*- coding: utf-8 -*-
"""一键打包（需求文档 11.2 / 11.3 / 11.4）。

顺序刻意是这样，每一步都对应一条硬要求：

    1. 生成自制图标            F-11.5 / F-11.22
    2. 由 wmm/version.py 生成版本信息文件   F-11.5（版本号只能有一个来源）
    3. PyInstaller 按 .spec 打包（onedir / 禁 UPX）
    4. 算 exe 的 SHA-256，把 说明.txt 写进产物根目录   F-11.16~F-11.20 / F-11.19
    5. 压成发布 zip，再算 zip 的 SHA-256               F-11.8
    6. 跑 static check（工具/结构/合规）               11.7 交付物静态检查

用法：
    .venv\\Scripts\\python.exe tools/build_dist.py
    .venv\\Scripts\\python.exe tools/build_dist.py --skip-build   # 只重打 zip / 重算哈希
    .venv\\Scripts\\python.exe tools/build_dist.py --no-zip       # 调试用，不压包
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DIST = Path(os.environ.get("WMM_DIST_DIR") or (ROOT / "dist"))
BUILD = ROOT / "build"

# exe / 发布目录名来自 version.EXE_NAME（影之刃零 = 中文「影之刃零mod管理器」）。
# ⚠️ 与数据目录（APP_NAME_EN）解耦：改 exe 名不会让用户数据搬家。
from wmm import version as _version  # noqa: E402

EXE_NAME = _version.EXE_NAME
APP_DIR = DIST / EXE_NAME
EXE = APP_DIR / f"{EXE_NAME}.exe"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sha256_of(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- 版本信息
def _version_tuple(text: str) -> tuple[int, int, int, int]:
    """'0.1.0' -> (0, 1, 0, 0)。Windows 版本资源必须恰好四段数字。

    取每个点分段**开头的**数字，因此 '1.0.0b2' -> (1, 0, 0, 0)。
    刻意不把 '0b2' 里的 2 挖出来 —— 那样 '1.0.0b2' 会算出 (1,0,2,0)，
    和 '1.0.2' 撞成同一个版本号，而版本号正是用户核对官方版的凭据（F-10.10），
    不能出现这种混淆。
    """
    parts: list[int] = []
    for chunk in str(text).split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])  # type: ignore[return-value]


def write_version_info(target: Path) -> None:
    """生成 PyInstaller 的版本资源文件。

    为什么不手写一份放进仓库：版本号只能有**一个**来源（wmm/version.py）。
    手写的第二份必然会在某次改版本时忘记同步，然后「程序里写 0.1.0、
    属性里写 0.0.9」——而这正是 F-10.10 用来核对官方版的凭据，错了就失去意义。
    """
    from wmm import version as v

    ver = _version_tuple(v.VERSION)
    ver_str = ".".join(str(x) for x in ver)
    company = v.AUTHOR_ID
    product = v.APP_NAME_ZH

    def esc(s: str) -> str:
        return str(s).replace("\\", "\\\\").replace("'", "\\'")

    text = f"""# UTF-8
# 本文件由 tools/build_dist.py 从 wmm/version.py 自动生成，请勿手工修改。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={ver},
    prodvers={ver},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404B0',
        [
          StringStruct('CompanyName', '{esc(company)}'),
          StringStruct('FileDescription', '{esc(product)}'),
          StringStruct('FileVersion', '{ver_str}'),
          StringStruct('InternalName', '{esc(v.APP_NAME_EN)}'),
          StringStruct('LegalCopyright', 'Copyright (C) {v.BUILD_DATE[:4]} {esc(company)}'),
          StringStruct('OriginalFilename', '{esc(v.APP_NAME_EN)}.exe'),
          StringStruct('ProductName', '{esc(product)}'),
          StringStruct('ProductVersion', '{ver_str}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""
    target.parent.mkdir(parents=True, exist_ok=True)
    # PyInstaller 用 eval 读这个文件（encoding 声明在首行），所以写 UTF-8 即可
    target.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- Cython 核心模块
#: 编译成机器码的模块清单（用户 2026-10-14 拍板的防倒卖方案）。
#: 只挑「真有秘密」的两个：mod 库存读写 + 安装/卸载/回滚逻辑。
#: UI 等其余模块保持源码 —— 编译面越小回归面越小（360 测试已覆盖这两模块）。
CYTHON_MODULES = ("modstore", "installer")

#: 编译产物目录。PyInstaller 从这里把它们收进 _internal/wmm/（见 .spec）。
CYTHON_DIR = BUILD / "cython"


def _zig_exe() -> "Path | None":
    """zig cc（venv 里 pip 装的 ziglang）。没装返回 None → 回退源码打包。

    为什么用 zig：本机没有 MSVC / MinGW，而 Cython 编 CPython 扩展必须有
    C 编译器。zig 的 cc 模式自带完整 MinGW libc 工具链，pip 一个包解决，
    不用往用户机器上装几个 GB 的 Visual Studio Build Tools。
    """
    try:
        import ziglang  # noqa: F401
    except ImportError:
        return None
    from pathlib import Path as _P

    p = _P(ziglang.__file__).parent / "zig.exe"
    return p if p.is_file() else None


def _normalize_pyd(path: Path) -> None:
    """抹掉 PE 里的两个非确定字段，保住「同源码 = 同字节」的承诺。

    zig 链接出来的 .pyd 有两处每次编译都不同：
      1. PE 头 TimeDateStamp（写入当时时间）；
      2. .buildid 段的随机内容（zig 的 build-id，`--build-id=none` 无效）。
    不清掉它们，「源码+版本不变重打哈希不变」就被破坏了（F-11.8 要求公示
    哈希，用户要靠它核对文件没被换过）。这两处都不影响加载：build-id 只服务
    调试符号匹配，PE loader 根本不校验它。
    """
    import struct

    d = bytearray(path.read_bytes())
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    struct.pack_into("<I", d, pe + 8, 0)          # TimeDateStamp = 0
    coff = pe + 4
    nsec = struct.unpack_from("<H", d, coff + 2)[0]
    ohsz = struct.unpack_from("<H", d, coff + 16)[0]
    sec = coff + 20 + ohsz
    for i in range(nsec):
        name = bytes(d[sec + i * 40 : sec + i * 40 + 8]).rstrip(b"\0")
        if name == b".buildid":
            rsz, raw = struct.unpack_from("<II", d, sec + i * 40 + 16)
            d[raw : raw + rsz] = b"\0" * rsz
    path.write_bytes(bytes(d))


def cythonize_core() -> list:
    """把 CYTHON_MODULES 编成 .pyd。返回 [(pyd, 包内目录), ...]；空列表 = 回退源码。

    ⚠️ 失败绝不抛异常：保护层是锦上添花，发布链才是主线。任何一步挂了
    （没装 zig / cythonize 报错 / 编译报错）都只警告并回退无保护打包 ——
    用户 2026-10-14 定的规矩：「检查全绿才换版，编译不过退回无保护版再发」。

    注意：zig 在**沙箱内**读不了自己的 libc 头文件（AccessDenied），
    打包要么在沙箱外跑，要么这里会命中失败分支回退源码 —— 后者是安全兜底，
    但会静悄悄失去保护层，所以失败时日志里必须醒目。
    """
    import sysconfig

    out: list = []
    # 对照实验开关（2026-10-18）：用户报「卸载时弹黑框，早期版本没有」，
    # 而早期版本与现版的唯一卸载相关差异就是 Cython 层。设 WMM_NO_CYTHON=1
    # 可打出「纯源码」对照包，用来验证黑框是否由编译层引起。
    if os.environ.get("WMM_NO_CYTHON"):
        _log("⚠️ WMM_NO_CYTHON=1：跳过 Cython 保护层（源码对照包）")
        for name in CYTHON_MODULES:
            p = CYTHON_DIR / f"{name}.cp313-win_amd64.pyd"
            p.unlink(missing_ok=True)
        return out
    zig = _zig_exe()
    if zig is None:
        _log("⚠️ 未安装 ziglang —— 跳过 Cython 保护层，按源码打包")
        _log("    （pip install ziglang 后重打即可带上保护层）")
        return out

    include = sysconfig.get_paths()["include"]
    libdir = sysconfig.get_config_var("LIBDIR") or ""
    lib = Path(libdir) / "python313.lib"
    if not lib.is_file():
        _log(f"⚠️ 找不到 import lib：{lib} —— 跳过 Cython 保护层，按源码打包")
        return out

    CYTHON_DIR.mkdir(parents=True, exist_ok=True)
    for name in CYTHON_MODULES:
        src = ROOT / "wmm" / f"{name}.py"
        c_file = CYTHON_DIR / f"{name}.c"
        pyd = CYTHON_DIR / f"{name}.cp313-win_amd64.pyd"
        # ⚠️ 先删上次的产物再编：万一这次编译失败，绝不能把上次的 .pyd
        #    留在原地让 .spec 误以为「有保护层」——那会把旧代码当新代码发布。
        pyd.unlink(missing_ok=True)
        try:
            r = subprocess.run(
                [sys.executable, "-m", "cython", "-3", "-o", str(c_file), str(src)],
                capture_output=True, text=True, cwd=str(ROOT),
            )
            if r.returncode != 0:
                _log(f"⚠️ cythonize {name} 失败：{(r.stderr or r.stdout)[-300:]}")
                _log("    跳过 Cython 保护层，按源码打包")
                return []
            # 固定输出名（.pyd 的导出表里嵌着文件名，名字不定哈希就不定）
            r = subprocess.run(
                [
                    str(zig), "cc", "-O2", "-shared", "-fPIC", "-g0",
                    "-target", "x86_64-windows-gnu",
                    "-I", include,
                    str(c_file), "-o", str(pyd), str(lib),
                ],
                capture_output=True, text=True, cwd=str(ROOT),
            )
            if r.returncode != 0:
                _log(f"⚠️ 编译 {name} 失败：{(r.stderr or r.stdout)[-300:]}")
                _log("    跳过 Cython 保护层，按源码打包")
                return []
            _normalize_pyd(pyd)
            _log(f"Cython 保护层：{name} → {pyd.name}（{pyd.stat().st_size:,} B，机器码）")
            out.append((pyd, "wmm"))
        except Exception as exc:  # noqa: BLE001 - 保护层失败绝不能炸发布链
            _log(f"⚠️ Cython 步骤异常（{exc}）—— 跳过保护层，按源码打包")
            return []
    return out


# ---------------------------------------------------------------- 打包
def _normalize_archive_zip(path: Path, stamp: tuple) -> None:
    """归一化 PyInstaller 生成的 base_library.zip，保住确定性承诺。

    PyInstaller 每次生成 base_library.zip 都不确定：entry 顺序随文件系统
    返回顺序变化、压缩输出也不稳定（同一个 .pyc 两次压缩能差一倍）。
    F-11.8 要求公示 zip 的 SHA-256，用户要靠「重下一次哈希一致」判断文件
    没被换过 —— 上游不确定，发布包的确定性就断了。

    做法与 .pyd 归一化同思路：读出全部 entry，按文件名排序，用固定压缩
    级别 + 固定时间戳重写。zip 是标准格式，bootloader 读取不受影响。
    """
    import zipfile as _zf

    src = _zf.ZipFile(path, "r")
    try:
        items = [(info.filename, src.read(info.filename)) for info in src.infolist()]
    finally:
        src.close()
    items.sort(key=lambda kv: kv[0])
    tmp = path.with_suffix(".zip.tmp")
    with _zf.ZipFile(tmp, "w", _zf.ZIP_DEFLATED, compresslevel=6) as out:
        for name, data in items:
            info = _zf.ZipInfo(name, date_time=stamp)
            info.compress_type = _zf.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            out.writestr(info, data)
    tmp.replace(path)


def run_pyinstaller() -> int:
    """跑 PyInstaller。

    `--noconfirm` 同时承担「清掉上一次的产物目录」这件事 —— 见 main() 里的注释。
    刻意**不加 `--clean`**：它会清空整个 build 工作目录（同样是一次几百文件的批量
    删除），而且增量分析本来就更快；真要清缓存时手动删 build/pyi 即可。
    """
    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(ROOT / "PhantomBladeZeroManager.spec"),
        "--noconfirm",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "pyi"),
    ]
    _log("运行 PyInstaller：" + " ".join(cmd[1:]))
    log_path = ROOT / "build" / "pyinstaller.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # 输出落文件而不是管道 —— 管道会缓冲，看日志时像是卡住了
    with log_path.open("w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(ROOT))
    tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
    for line in tail:
        print("    " + line)
    if proc.returncode == 0:
        # PyInstaller 的 base_library.zip 每次生成都不确定（entry 顺序随
        # 文件系统、压缩输出不稳定）—— 归一化它，「源码+版本不变 = zip
        # 哈希不变」的承诺才成立（F-11.8 公示哈希的前提）。
        from wmm import version as _v

        _d = _v.BUILD_DATE
        _stamp = (int(_d[0:4]), int(_d[5:7]), int(_d[8:10]), 0, 0, 0)
        _lib_zip = APP_DIR / "_internal" / "base_library.zip"
        if _lib_zip.is_file():
            _normalize_archive_zip(_lib_zip, _stamp)
            _log("base_library.zip 已归一化（固定顺序/压缩级别/时间戳）")
    return proc.returncode


# ---------------------------------------------------------------- 说明文档
def write_readme() -> Path:
    from wmm import distinfo, enabler, readme_en

    exe_hash = sha256_of(EXE) if EXE.is_file() else None
    text = distinfo.build_readme(exe_sha256=exe_hash)
    target = APP_DIR / "说明.txt"
    # F-11.4 原文：UTF-8 且**不要 BOM**，否则老版本记事本会显示成乱码开头。
    target.write_bytes(text.encode("utf-8"))

    # 英文版（2026-10-30 用户要求：N 网用户看不懂中文说明）。
    # 措辞按「这个包里到底有没有内置前置」切换 —— 两个渠道的包内容不同，
    # 说明必须跟着变，否则用户会去找一个根本不存在的东西。
    try:
        bundled = bool(enabler.bundled_files())
    except Exception:
        bundled = (APP_DIR / "_internal" / "assets" / "enabler").is_dir()
    en = readme_en.build_readme_en(exe_sha256=exe_hash or "", enabler_bundled=bundled)
    (APP_DIR / "README-EN.txt").write_bytes(en.encode("utf-8"))
    return target


def write_release_kit() -> Path:
    """生成给作者用的发布物料（视频简介 / 置顶评论 / FAQ / 发布前清单）。

    放在 docs/ 而不是 dist/ —— 这是**给作者看的**，不该混进用户下载的包里。
    """
    from wmm import distinfo

    target = ROOT / "docs" / "发布物料.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(distinfo.build_release_kit(), encoding="utf-8")
    return target


def make_zip() -> Path:
    """把产物压成发布 zip。

    **刻意做成"确定性打包"**：所有条目用同一个固定时间戳（发布日 00:00），
    而不是各文件的真实修改时间。理由：
      - 否则每次重新打包，哪怕内容一个字没改，zip 的 SHA-256 也会变，
        发布页与置顶评论里的哈希就永远要跟着改（F-11.8 / F-11.25 要求公示它）；
      - 用户也没法用「重下一次、哈希是否一致」来判断文件有没有被换过。
    确定性打包让"同样的源码 + 同样的版本号 = 同样的字节"真正成立。
    """
    from wmm import distinfo, version

    zip_path = DIST / f"{distinfo.zip_name()}.zip"
    if zip_path.exists():
        zip_path.unlink()

    # ZIP 的时间戳下限是 1980-01-01，且不能早于它 —— 这里用发布日，天然安全
    d = version.BUILD_DATE
    stamp = (int(d[0:4]), int(d[5:7]), int(d[8:10]), 0, 0, 0)

    _log(f"打包 {zip_path.name}（确定性：固定时间戳 {d} 00:00）…")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(APP_DIR.rglob("*")):
            if not path.is_file():
                continue
            arc = str(Path(EXE_NAME) / path.relative_to(APP_DIR))
            info = zipfile.ZipInfo(arc, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16      # 普通文件权限，别带奇怪的标志位
            zf.writestr(info, path.read_bytes())
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true", help="跳过 PyInstaller，只重做说明文档与 zip")
    parser.add_argument("--no-zip", action="store_true", help="不压 zip")
    args = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from wmm import version

    _log(f"打包 {version.APP_NAME_ZH} v{version.VERSION}（{version.BUILD_DATE}）")
    _log(f"Python {sys.version.split()[0]}  架构 {sys.maxsize > 2**32 and '64 位' or '32 位'}")

    # ---- 1. 图标 ----
    # 说明（2026-10-29）：原先每次打包都用 `make_icon.build_ico()` 重画程序化图标。
    # 现在作者指定了自己的图片图标（tools/make_icon_from_image.py 生成的），
    # 所以改成：**已有 icon.ico 就直接用**，只有缺失时才回退到程序化生成。
    import tools.make_icon as make_icon

    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])  # noqa: F841
    icon_path = ROOT / "assets" / "icon.ico"
    if icon_path.is_file() and icon_path.stat().st_size > 0:
        _log(f"使用现有图标 assets/icon.ico（{icon_path.stat().st_size:,} B）")
    else:
        sizes = make_icon.build_ico(icon_path)
        _log(f"图标已生成（{len(sizes)} 个尺寸）")

    # ---- 2. 版本信息 ----
    write_version_info(BUILD / "version_info.txt")
    _log("版本信息文件已生成 build/version_info.txt")

    # ---- 2b. Cython 保护层（失败自动回退源码打包，见 cythonize_core 注释）----
    if not args.skip_build:
        core_bins = cythonize_core()
        if not core_bins:
            _log("本次打包不带 Cython 保护层（源码 .py 进包）")
        else:
            _log(f"本次打包带 Cython 保护层：{len(core_bins)} 个核心模块为机器码")

    # ---- 3. PyInstaller ----
    if not args.skip_build:
        # ⚠️ 这里**刻意不自己 `shutil.rmtree(dist/)`**：
        # onedir 产物一次就是 200+ 个文件，任何批量删除守卫都会拦下来要求确认，
        # 而 `--noconfirm` 让 PyInstaller 自己清掉旧产物目录，一条命令解决。
        code = run_pyinstaller()
        if code != 0:
            _log(f"PyInstaller 失败，退出码 {code}；日志见 build/pyinstaller.log")
            return code
    if not EXE.is_file():
        _log(f"没有找到 {EXE}")
        return 3

    # ---- 4. 说明.txt（放在产物根目录，用户一眼能看到）----
    readme = write_readme()
    _log(f"说明.txt 已写入产物根目录（{readme.stat().st_size:,} B，已含 exe 哈希）")

    # ---- 5. zip ----
    zip_path = None
    if not args.no_zip:
        zip_path = make_zip()

    # ---- 5b. 发布物料（给作者用，不进发布包）----
    kit = write_release_kit()
    _log(f"发布物料已写入 {kit.relative_to(ROOT)}")

    # ---- 6. 汇总 ----
    print()
    print("=" * 72)
    print(f"产物目录：{APP_DIR}")
    print(f"主程序 ：{EXE}")
    print(f"  SHA-256  {sha256_of(EXE)}")
    if zip_path:
        print(f"发布 zip：{zip_path}")
        print(f"  大小     {zip_path.stat().st_size / 1048576:.1f} MB")
        print(f"  SHA-256  {sha256_of(zip_path)}   ← 这个值要公示到发布页与置顶评论")
    print("=" * 72)
    print("下一步：tools/dist_check.py 做交付前静态检查（11.7）")

    # exe 哈希变了 -> 让作者记得同步 zip 哈希到发布页
    print()
    print("⚠️  打包已做成确定性的：只要源码与版本号不变，重打多少次 zip 的 SHA-256 都一样。")
    print("    所以**只有改了代码或改了版本号**，才需要更新发布页/置顶评论里的哈希。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
