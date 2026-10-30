# -*- coding: utf-8 -*-
"""《影之刃零》（Phantom Blade Zero）配置。

已知事实（2026-10-27 查证，发售后需复核）：
  · 引擎 Unreal Engine 5（5.7.4），S-GAME 开发，2026-10-29 发售（PC/PS5）
  · PC 端目录：<游戏根>\\PkGame\\Content\\Paks\\~mods\\  ← UE5 标准 `~mods` 松散加载
  · mod 形态：.pak / .ucas / .utoc 混放（IoStore 时代的常见组合）
  · 存档：%LOCALAPPDATA%\\PhantomBladeZero\\Saved\\SaveGames
  · 官方没有 mod 工具；社区目前靠手动放 pak（→ 说明**很可能不需要** Mod Enabler，
    但发售后必须实测：若放进去不生效，就要改成 needs_enabler=True 并补检测规则）

⚠️ 待发售后确认的三件事（都只改这个文件）：
  1. Steam AppID（现在填空串，自动检测时不作为筛选条件，只影响证据展示）
  2. 是否需要前置加载器（先按"不需要"处理；如果需要，多半是 UE4SS 类工具，
     检测规则要重写，不是现在的代理 DLL + sig.lua 那一套）
  3. 主 exe 名（现在按 UE 惯例推测，若实际不同就补/改）
"""

from __future__ import annotations

from .base import GameProfile

PROFILE = GameProfile(
    key="phantom_blade",
    display_name="影之刃零",
    app_name_zh="影之刃零MOD管理器",
    app_name_en="PhantomBladeZeroManager",
    # 用户 2026-10-29 要求 exe 直接叫中文名（解压出来一看就知道是什么）
    exe_name="影之刃零mod管理器",
    display_name_en="Phantom Blade Zero",
    # 与明末完全独立的数据目录，两者可以同时装在同一台机器上
    data_dir_name="PhantomBladeZeroManager",
    # ⚠️ 2026-10-29 实测：UE 工程名是 **ProjectPBZ**（网上说的 "PkGame" 是错的）
    ue_project="ProjectPBZ",
    steam_appid="4115450",   # 2026-10-27 从 Steam 商店页确认
    # 实测：主 exe 不在 Binaries\Win64（那里只有 8za_v2.exe / CheckEnv.exe），
    # 而是在 platform\steam\PBZ.exe 与 steam\PBZ.exe
    game_exe_names=(
        "PBZ.exe",
        "platform/steam/PBZ.exe",
        "steam/PBZ.exe",
        "ProjectPBZ/Binaries/Win64/PBZ-Win64-Shipping.exe",
    ),
    # Steam 安装目录就叫 "PBZ"，所以 "pbz" / "projectpbz" 必须进词表，
    # 否则自动检测认不出来（2026-10-29 实测踩到）
    identify_words=("phantomblade", "phantom blade", "影之刃", "影之刃零",
                    "projectpbz", "pbz"),
    mods_folder="~mods",
    # 社区现状：`~mods` 是引擎原生的松散加载，不需要额外前置。
    # 发售后若实测不生效，改 True 并按实际加载器补检测规则。
    # ⚠️ 2026-10-29 实测修正：**需要前置**。用户手上的「天锁斩月」mod 自带
    # dsound.dll(533KB) + UniversalSigBypasser.asi(42KB)，两者都是 PE 文件，
    # 是典型的「代理 DLL + ASI 签名绕过」组合 —— 和明末的 Mod Enabler 同一套路。
    # （此前依据网帖判断"不需要"，已被实物推翻。）
    needs_enabler=True,
    preserve_filenames=True,  # 三件套不改名
    enabler_dll_names=("dsound.dll",),
    enabler_marker="UniversalSigBypasser.asi",
    enabler_markers=(),       # .asi 是二进制，只查文件存在、不查内容
    enabler_hint="dsound.dll + UniversalSigBypasser.asi（签名绕过），随 mod 包一起发布",
    save_rel_paths=("Saved/SaveGames",),
    game_dir_example="选中包含 ProjectPBZ 这一层文件夹的目录，也就是游戏根目录（Steam 里通常叫 PBZ）。",
    extra_notes=(
        "影之刃零的 mod 是 UE5 的三件套（.pak + .utoc + .ucas），三个一起放进 ProjectPBZ/Content/Paks/~mods 才生效。",
        "不需要任何前置加载器（2026-10-29 实测）。",
        "游戏更新后 mod 可能失效或报错，建议每次大版本更新后重新检查一遍已装的 mod。",
    ),
)
