# -*- coding: utf-8 -*-
"""打一个**纯源码**的 zip，用于公开审计（GitHub / Nexus 申诉附件）。

为什么单独有这个工具：
  发布包是 PyInstaller + Cython 产出的二进制，别人无法核对里面有什么。
  Nexus 对含 exe 的包会自动隔离，申诉要求提供「能公开查看的源码」，
  所以需要一个**不含任何可执行文件**的源码归档，内容和发布包一一对应。

用法：
    .venv/Scripts/python.exe tools/make_source_zip.py [输出路径]

默认输出 `dist_nexus/PBZ-Mod-Manager-v<版本>-source.zip`。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wmm import version  # noqa: E402

#: 直接放在仓库根的文本文件
ROOT_FILES = [
    "main.py",
    "PhantomBladeZeroManager.spec",
    "requirements.txt",
    "README.md",
    "LICENSE",
    ".gitignore",
]

#: 整目录收进去
DIRS = ["wmm", "tools", "tests"]

#: 单独收的文件（资源里唯一需要公开的）
EXTRA_FILES = ["assets/icon.ico"]

#: 绝不进源码包的东西
SKIP_DIRS = {"__pycache__", ".venv", "dist", "dist_nexus", "build", "_old_dist", "docs"}
SKIP_EXTS = {".pyc", ".pyd", ".exe", ".pak", ".utoc", ".ucas", ".log", ".ico.bak"}

BUILD_TXT = """Build steps
===========

1. Install Python 3.13 (64-bit Windows).
2. python -m venv .venv
3. .venv\\Scripts\\python.exe -m pip install -r requirements.txt
4. Run from source:  .venv\\Scripts\\python.exe main.py
5. Build release:    .venv\\Scripts\\python.exe tools\\build_dist.py
                     .venv\\Scripts\\python.exe tools\\dist_check.py

Set WMM_NO_CYTHON=1 before step 5 to build without the compiled modules
(identical behaviour, plain .pyc). Nothing is downloaded at build time
beyond the pip requirements.

About this archive
------------------
This archive contains source code only: no executables, no compiled
extensions, no game assets, no mods, and none of the third-party
signature-bypass binaries. The distributed release is a PyInstaller
build of exactly this code.
"""


def _iter_dir(rel: str):
    base = ROOT / rel
    if not base.is_dir():
        return
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if set(p.relative_to(ROOT).parts) & SKIP_DIRS:
            continue
        if p.suffix.lower() in SKIP_EXTS:
            continue
        yield p


def build(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    prefix = "PBZ-Mod-Manager-source"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in ROOT_FILES:
            src = ROOT / name
            if src.is_file():
                z.write(src, f"{prefix}/{name}")
        for d in DIRS:
            for p in _iter_dir(d):
                z.write(p, f"{prefix}/{p.relative_to(ROOT).as_posix()}")
        for name in EXTRA_FILES:
            src = ROOT / name
            if src.is_file():
                z.write(src, f"{prefix}/{name}")
        z.writestr(f"{prefix}/BUILD.txt", BUILD_TXT.replace("\n", "\r\n"))
    return out


def main() -> int:
    default = ROOT / "dist_nexus" / f"PBZ-Mod-Manager-v{version.VERSION}-source.zip"
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    built = build(out)
    names = zipfile.ZipFile(built).namelist()
    bad = [
        n for n in names
        if n.lower().endswith((".exe", ".pyd", ".pak", ".utoc", ".ucas"))
        or "enabler/" in n
    ]
    print(f"已生成 {built}")
    print(f"  {built.stat().st_size / 1024:.0f} KB / {len(names)} 个条目")
    print(f"  不该有的东西：{bad or '无'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
