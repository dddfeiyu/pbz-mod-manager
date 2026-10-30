# -*- coding: utf-8 -*-
"""英文说明书的正文（`README-EN.txt`）。

单独成文件而不是塞进 distinfo：英文文本很长，混在中文文案里改起来容易踩串。
措辞按「当前这个包里到底有没有前置组件」自动切换 —— B 站版内置、Nexus 版不带。
"""
from __future__ import annotations

from . import version
from .games import ACTIVE as GAME


def _bar(n: int = 72) -> str:
    return "-" * n


def build_readme_en(*, exe_sha256: str = "", enabler_bundled: bool = False) -> str:
    """生成 `README-EN.txt` 全文。"""
    exe = f"{version.EXE_NAME}.exe"
    game_en = GAME.display_name_en or GAME.display_name
    hash_line = exe_sha256 or "(filled in at build time; if blank, this is not an official build)"
    mods_rel = f"{GAME.ue_project}\\Content\\Paks\\{GAME.mods_folder}"
    bin_rel = f"{GAME.ue_project}\\Binaries\\Win64"

    if enabler_bundled:
        prereq_body = (
            "  The signature-bypass component is BUNDLED with this tool.\n"
            "  Click the orange button in the toolbar (Install prerequisite) and\n"
            f"  the tool copies it into {bin_rel} for you.\n"
            "  Once done the button turns green (Prerequisite ready).\n\n"
            "  Without this component no mod will load, and the game shows no\n"
            "  error at all - it just looks like nothing happened."
        )
        bundled_credit = (
            "  Components bundled with this tool:\n"
            "    U+2022 UniversalSigBypasser.asi - rm-NoobInCoding - CC BY-NC 4.0\n"
            "      https://github.com/rm-NoobInCoding/UniversalSigBypasser\n"
            "    U+2022 dsound.dll (Ultimate ASI Loader) - ThirteenAG - MIT\n"
            "      https://github.com/ThirteenAG/Ultimate-ASI-Loader\n"
            "  They are only copied into the game's Win64 folder; their contents\n"
            "  are never modified."
        )
    else:
        prereq_body = (
            "  This build does NOT include the signature-bypass component.\n"
            "  Get UniversalSigBypasser here:\n"
            "    https://www.nexusmods.com/site/mods/1416\n"
            f"  Copy UniversalSigBypasser.asi and dsound.dll into {bin_rel}\\n\n"
            "  Without it no mod will load, and the game shows no error at all -\n"
            "  it just looks like nothing happened."
        )
        bundled_credit = (
            "  This build does not bundle any third-party component.\n"
            "  UniversalSigBypasser is by rm-NoobInCoding (CC BY-NC 4.0) and is\n"
            "  distributed separately on Nexus Mods."
        )

    return f"""\
========================================================================
 {version.EXE_NAME}  v{version.VERSION}  -  User Guide
 by {version.AUTHOR_ID}   |   QQ group {version.QQ_GROUP_NUMBER}
========================================================================

A mod manager for {game_en}: import, preview, install, uninstall
and conflict-check local mods. Completely free, no paid tier.

Unofficial third-party tool. Not affiliated with or endorsed by the
game's developer or publisher. It contains no game assets and no mods;
you supply the mods yourself.


{_bar()}
 1. Requirements
{_bar()}

  * Windows 10 1809 or newer, or Windows 11
  * 64-bit only (x64)
  * Windows 7 / 8 / 8.1 are NOT supported (Qt 6 requirement)
  * No admin rights, no installation - unzip and run
  * No network access except the optional author link


{_bar()}
 2. Antivirus warnings
{_bar()}

The executable is not code-signed, so Windows Defender or other antivirus
software may flag it on first run. That is normal for unsigned software
and does not mean the file is malicious.

Add the extracted folder to your AV exclusions:
  1. Open Windows Security
  2. Virus & threat protection
  3. Under "Virus & threat protection settings" click Manage settings
  4. Scroll to Exclusions -> Add or remove exclusions
  5. Add an exclusion -> Folder
  6. Pick the folder you extracted this tool into

For third-party AV add the same folder to its trusted/exclusion list.

  Do not disable your antivirus to run this tool. An exclusion for this
  folder is enough; disabling protection only exposes you to real threats,
  and this tool never needs you to do that.


{_bar()}
 3. Missing runtime (vcruntime140.dll)
{_bar()}

The runtime is bundled with the tool, so normally nothing extra is needed.
On unusually stripped Windows installs you may still get a missing-DLL
error; only in that case install the official Microsoft package:
    https://aka.ms/vs/17/release/vc_redist.x64.exe
Get it from Microsoft only - never from third-party download sites.


{_bar()}
 4. Verifying your download
{_bar()}

Main executable SHA-256:
    {hash_line}

The SHA-256 of the release zip is published on the release page (a file
cannot contain its own hash).

To check, open PowerShell in the extracted folder and run:
    Get-FileHash "{exe}" -Algorithm SHA256

A mismatch means the file was modified - delete it and re-download from
the official channel.


{_bar()}
 5. The interface is in Chinese
{_bar()}

Buttons you will need, by position:

  * Top right, pink button .................. Import mods
  * Row under the filter tabs:
      - leftmost ............................ Install selected
      - next ................................ Uninstall selected
      - next ................................ Select all
      - red-outlined, far right ............. Delete from library
  * Toolbar button, orange .................. Install prerequisite
  * Toolbar button, green ................... Prerequisite ready
  * Top row, folder icon + two buttons ...... Auto-detect / Change game folder


{_bar()}
 6. First run
{_bar()}

  1. Unzip anywhere (avoid C:\\Program Files if you dislike permission
     prompts). Run {exe}.
  2. Click the "Auto-detect game folder" button - it reads your Steam
     library. If that fails, point it at the folder that CONTAINS
     {GAME.ue_project}.
  3. Set up the prerequisite (see section 7).
  4. Drag a mod zip or folder into the window, or use Import mods.
  5. Tick the mods you want, click Install selected.

Your settings and mod library live in:
    %APPDATA%\\{GAME.data_dir_name}\\
so the tool itself can sit on a USB stick or in a synced folder.


{_bar()}
 7. Prerequisite (required)
{_bar()}

{prereq_body}


{_bar()}
 8. How mods are installed
{_bar()}

Target folder:
    {mods_rel}\\

A {game_en} mod is usually a set of three files sharing one name:
    something_P.pak
    something_P.utoc
    something_P.ucas

All three must be present, and this tool deliberately KEEPS THEIR ORIGINAL
NAMES - IoStore containers resolve by name, so renaming them silently
breaks assets.

Uninstalling deletes only the files the tool recorded installing, and
restores anything it had overwritten.


{_bar()}
 9. FAQ
{_bar()}

Q: A mod is installed but nothing changed in game.
A: The prerequisite is missing. Click the orange "Install prerequisite"
   button (or install UniversalSigBypasser manually, see section 7).
   No mod loads without it, and the game reports no error.

Q: Two mods change the same thing - what happens?
A: The tool flags it on the card and tells you which one wins. Conflicts
   are warnings, not blocks: install one only, or accept the overwrite.

Q: Everything broke after a game update.
A: Game patches usually reset the prerequisite. Click the green button and
   choose "reinstall". If mods still fail, the mods themselves may need an
   update from their authors.

Q: Will uninstalling delete my own files?
A: No. It deletes only the files it recorded installing, and checks each
   path is inside the game folder you configured. Anything it overwrote is
   restored from backup.

Q: Is my data uploaded anywhere?
A: No. Apart from the optional author link there are no network requests
   and no telemetry of any kind.

Q: Is supporting the author required?
A: No. It is entirely optional and does not affect any feature.


========================================================================
 No game assets and no mods are included. You supply the mods.
{bundled_credit}
========================================================================
"""
