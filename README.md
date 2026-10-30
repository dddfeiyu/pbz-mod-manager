# Phantom Blade Zero Mod Manager

A portable, offline mod manager for **Phantom Blade Zero** (UE5, `ProjectPBZ`).

This repository is the full source of the tool published on Nexus Mods. It exists
primarily so that the distributed binary can be audited — the release archive is a
PyInstaller build of exactly this code.

---

## What it does

Installing a PBZ mod by hand means creating `~mods`, keeping the `.pak` / `.utoc` /
`.ucas` trio together, and remembering what was there before you overwrote it. This
tool does that, and warns when two mods touch the same asset.

- Import `.pak` / `.utoc` / `.ucas` packages from a zip, a folder, or the files themselves
- Install / uninstall into `ProjectPBZ\Content\Paks\~mods` by one click
- **Keeps original filenames** — IoStore containers resolve by name; renaming silently breaks assets
- Backs up any file it overwrites, restores it on uninstall
- Conflict detection (same target filename / same overridden asset / same costume slot)
- Detects the signature-bypass prerequisite and reports whether it is in place
- Extracts preview images from the package when present
- Says **"Unknown"** instead of guessing when it cannot tell what a mod is

## Security statement

For anyone auditing the binary:

- **Fully offline.** No network calls of any kind. The only URLs in the codebase are
  constants used to open a browser tab when the user clicks an author link — nothing is
  fetched, and no request is ever made on startup.
- **No telemetry, no analytics, no crash reporting, no auto-update.**
- **No account, no licence check.**
- **Writes only to two places:**
  1. `%APPDATA%\PhantomBladeZeroManager\` — the mod library, settings, install records and backups
  2. The game directory the user explicitly selects — only when the user clicks
     *Install* or *Uninstall*, and only the files recorded in `library.json`
- **Never deletes recursively.** Uninstall removes individual recorded files, refuses any
  path outside the recorded game directory, and only removes directories that are empty.
- **The core logic (everything in `wmm/` except `wmm/ui/`) imports nothing but the
  Python standard library** — `dataclasses`, `hashlib`, `json`, `os`, `pathlib`, `re`,
  `shutil`, `subprocess`, `sys`, `tempfile`, `time`, `typing`, `zipfile`.
  Qt is used for the GUI only.

If you believe something in here contradicts the above, please open an issue.

## Repository layout

```
main.py                     entry point (GUI, plus a --selftest mode used by CI checks)
PhantomBladeZeroManager.spec  PyInstaller recipe
wmm/                        application package
  games/                    per-game configuration (project name, appid, mods folder, ...)
  ui/                       Qt widgets (the only part that imports PySide6)
  installer.py              install / uninstall / backup / restore
  modstore.py               mod library on disk (content-addressed)
  conflicts.py, catalog.py, importer.py, pakfiles.py, preview.py, ...
tools/
  build_dist.py             builds dist/ and the release zip
  dist_check.py             post-build verification (runs the built exe)
  selfcheck.py              source-level checks (no Qt needed for most)
  make_icon.py              generates the icon programmatically
tests/                      unittest suite
assets/                     icon
docs/                       release notes
```

## Run from source

Requires **Python 3.13**, 64-bit Windows.

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

## Build the release archive

```bat
.venv\Scripts\python.exe tools\build_dist.py
.venv\Scripts\python.exe tools\dist_check.py
```

`build_dist.py` will:

1. Compile `wmm/installer.py` and `wmm/modstore.py` to native modules with Cython
   (via `ziglang` as the C compiler backend), then remove the `.py` originals from the
   build output. This is the reason the shipped binary trips heuristic antivirus
   detections — see the note below.
2. Run PyInstaller with `PhantomBladeZeroManager.spec`, producing a `onedir` build
   (no installer, no admin rights, no external runtime required).
3. Write `说明.txt` (Chinese) and `README-EN.txt` (English) next to the exe.
4. Produce a deterministic zip.

Set `WMM_NO_CYTHON=1` to build without the compiled modules (pure `.pyc`). That build
behaves identically and is useful when comparing against VirusTotal results.

### Note on antivirus false positives

The released binary is unsigned, and two core modules are compiled to native code.
Unsigned PyInstaller executables combined with compiled Python modules are a well-known
source of heuristic false positives. Nothing in the build process downloads code, phones
home, or modifies anything at runtime beyond what is described above, and the SHA-256 of
every release archive is published alongside it.

## Third-party components

The Bilibili-distributed build bundles a signature-bypass prerequisite. The Nexus build
does **not** — users install it themselves.

| Component | Author | Licence | Bundled in release? |
|---|---|---|---|
| UniversalSigBypasser (`.asi`) | rm-NoobInCoding | CC BY-NC 4.0 | Not in this repository; Nexus build ships without it |
| Ultimate ASI Loader (`dsound.dll`) | ThirteenAG | MIT | Not in this repository |

Neither binary is committed here (`assets/enabler/` is git-ignored). When the tool
installs the prerequisite it only **copies** the two files into
`ProjectPBZ\Binaries\Win64\`, after backing up any same-named file it would replace.
It does not execute, register, patch, or modify anything.

## Licence

The code in this repository is MIT — see `LICENSE`. The third-party components listed
above keep their own licences.

This is an unofficial third-party tool. It is not affiliated with or endorsed by S-GAME
or the publishers of Phantom Blade Zero. It contains no game assets and no mods.

---

## 中文说明

这是《影之刃零》MOD 管理器的完整源码。发布版是用这份代码打出来的 PyInstaller 包，
公开源码是为了让任何人可以核对二进制里到底有什么。

- **完全离线**：不联网、不上报、不自动更新、不需要账号
- **只写两个地方**：`%APPDATA%\PhantomBladeZeroManager\`（mod 库与设置），以及用户
  自己选定的游戏目录（仅在点「安装」「卸载」时，且只动 `library.json` 里记录的文件）
- **不递归删除**：卸载逐个删记录的文件，路径不在记录的 `game_dir` 下就拒删，
  只清空目录
- **核心逻辑零第三方依赖**：`wmm/`（除 `ui/`）只用标准库，Qt 只负责界面

构建方法与上面英文部分一致。加 `WMM_NO_CYTHON=1` 可打出不含机器码的版本
（功能完全相同，用于对照杀软误报）。
