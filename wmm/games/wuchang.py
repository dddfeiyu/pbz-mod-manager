# -*- coding: utf-8 -*-
"""《明末：渊虚之羽》配置 —— 值全部与重构前逐字一致，改这里等于改动线上行为。"""

from __future__ import annotations

from .base import GameProfile

PROFILE = GameProfile(
    key="wuchang",
    display_name="明末：渊虚之羽",
    app_name_zh="明末MOD管理器",
    app_name_en="WuchangModManager",
    # ⚠️ 绝不能改：现有用户的数据都在 %APPDATA%\WuchangModManager 下
    data_dir_name="WuchangModManager",
    ue_project="Project_Plague",
    steam_appid="2277560",
    game_exe_names=(
        "WuchangFallenFeathers.exe",
        "Project_Plague/Binaries/Win64/WuchangFallenFeathers-Win64-Shipping.exe",
        "Project_Plague/Binaries/Win64/WuchangFallenFeathers-WinGDK-Shipping.exe",
    ),
    # 别放单独的 "fallen"（会撞星战绝地），这是刻意的最小词表
    identify_words=("wuchang", "fallenfeather", "明末", "渊虚"),
    mods_folder="~mods",
    needs_enabler=True,
    enabler_dll_names=("d3d9.dll", "dsound.dll"),
    enabler_marker="bitfix/sig.lua",
    enabler_hint="Wuchang Mod Enabler（作者 FrancisLouis），请到 Nexus Mods 原站下载",
    save_rel_paths=(),
    game_dir_example="选中包含 Project_Plague 这一层文件夹的目录，也就是游戏根目录。",
    extra_notes=(
        "明末需要先装一个「Mod Enabler」前置才能加载 mod。程序内会检测它，没装会给出提示。",
        "游戏大版本更新通常会重置 Mod Enabler，重新装一次前置即可。",
    ),
)
