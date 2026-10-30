# -*- coding: utf-8 -*-
"""游戏配置基类 —— 把「跟某一款游戏绑死」的东西集中到这里。

2026-10-27 起因：用户要在《影之刃零》（UE5，PkGame/Content/Paks/~mods）发售后
做一个同款管理器。两作的 **mod 机制完全一致**（UE5 + `~mods` 松散 pak），
差别只在：UE 工程名、Steam AppID、主 exe 名、识别词、要不要前置加载器、
数据目录名、文案品牌。

所以内核（安装/卸载/备份还原/落点体检/冲突检测/手装识别）完全不用改，
只把上面的差异抽成 `GameProfile`，打包时选一份配置即可。

⚠️ 铁律：**默认配置永远是明末（wuchang）**，且明末的每个字段值都必须与
重构前逐字一致 —— 现有用户的 `%APPDATA%\\WuchangModManager` 与安装记录
不能因为这次抽象而有任何变化。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class GameProfile:
    # ---- 身份 ----
    key: str                      # 内部键："wuchang" / "phantom_blade"
    display_name: str             # 中文名，用于文案
    app_name_zh: str              # 程序名（标题栏/说明）
    app_name_en: str              # 英文名（数据目录/进程名派生）
    data_dir_name: str            # %APPDATA%\<这个名字>

    # ---- UE 相关 ----
    ue_project: str               # UE 工程名（目录名）：Project_Plague / PkGame
    steam_appid: str              # Steam AppID（仅作证据记录）；未知填空串
    game_exe_names: Tuple[str, ...]   # 主 exe 名候选（用于目录校验）
    identify_words: Tuple[str, ...]   # 自动检测游戏目录时的特征词（小写、去空格比较）

    # ---- mod 目录 ----
    mods_folder: str = "~mods"    # 放 mod 的目录名（UE 约定是 ~ 前缀）
    mods_folder_aliases: Tuple[str, ...] = ("~mods", "~Mods", "LogicMods")

    # ---- 前置加载器（Mod Enabler）----
    #: False = 该游戏的 `~mods` 原生生效，不需要前置（如影之刃零，已实测）
    needs_enabler: bool = True
    #: True = 安装时**保留 mod 原始文件名**，不做「XX_P.pak」规范化。
    #: IoStore 时代（.pak/.utoc/.ucas 三件套）改主名有风险：utoc 与 ucas
    #: 靠主名匹配、容器内部也可能记着名字，社区 mod 本身命名已规范，
    #: 没必要再动。明末是单 pak、且有 24% 命名不合规，所以那边保持 False。
    preserve_filenames: bool = False
    enabler_dll_names: Tuple[str, ...] = ()
    enabler_marker: str = ""      # 额外的判据文件（相对 Binaries\Win64），如 "bitfix/sig.lua"
    #: 判据文件里应当出现的特征串；**空元组 = 不校验内容**（二进制插件如 .asi
    #: 无法按文本匹配，只看文件在不在）
    enabler_markers: Tuple[str, ...] = ()
    enabler_hint: str = ""        # 没装时给用户的说明

    # ---- 存档（可选，仅用于「装 mod 前建议备份」提示）----
    save_rel_paths: Tuple[str, ...] = ()

    # ---- 文案 ----
    #: 说明.txt 里「选哪一层目录」的举例
    game_dir_example: str = ""
    extra_notes: Tuple[str, ...] = field(default_factory=tuple)

    #: exe 与发布目录的文件名（可含中文）；空串 = 用 app_name_en。
    #: ⚠️ 只影响 exe/文件夹/zip 的名字，**不影响 data_dir_name**（用户数据不搬家）。
    exe_name: str = ""

    #: 英文展示名（英文说明书 / Nexus 文案用）；空串 = 退回 display_name。
    display_name_en: str = ""


def mods_rel_path(profile: GameProfile) -> str:
    """Steam/Epic 下 mod 目录的标准相对路径（相对游戏根）。"""
    return f"{profile.ue_project}/Content/Paks/{profile.mods_folder}"
