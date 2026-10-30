# -*- coding: utf-8 -*-
"""伪造游戏目录树 —— 需求文档 R-7 指定的开发测试靶子。

文档原文：「开发阶段用**伪造的 Project_Plague\\Content\\Paks\\ 空目录树**跑通全部流程；
真机验证由使用者在装有游戏的机器上完成。**不要把「功能跑通」当成「能用」。**」

本脚本把**实测到的全部目录写法**都造出来（它们是互相矛盾的，见需求文档 2.3），
这样校验逻辑必须在四种结构下都能给出正确结论，而不是只对开发机上那一种有效。

用法：
    python tools/make_fake_game.py [目标目录]
不传目标目录时，在系统临时目录下新建一个。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

#: 四种真实存在（或文档明确记载）的目录结构
LAYOUTS = {
    # 实测存在：<游戏根>\Project_Plague\Content\Paks
    "steam": ("steamapps/common/Wuchang Fallen Feathers", "Project_Plague/Content/Paks"),
    # Game Pass 来源 A（Prima Games 等）
    "gamepass_a": ("XboxGames/Wuchang PCGP", "Content/Paks"),
    # Game Pass 来源 B（GitHub 官方 Modding Guide 口径：SB）
    "gamepass_b_guide_sb": ("XboxGames/WuchangFallenFeathers", "SB/Content/Paks"),
    # Game Pass 来源 C：Content\Project_Plague\Content\Paks 这种双层嵌套
    "gamepass_c_nested": ("XboxGames/Wuchang PCGP", "Content/Project_Plague/Content/Paks"),
}


def build(base: Path) -> dict[str, Path]:
    """在 base 下造出全部结构，返回 {名称: 游戏根目录}。"""
    base = Path(base)
    made: dict[str, Path] = {}
    for name, (root_rel, paks_rel) in LAYOUTS.items():
        game_root = base / root_rel
        paks = game_root / paks_rel
        paks.mkdir(parents=True, exist_ok=True)
        # ~mods 与 LogicMods 都造出来（文档 2.4 表格里两种目标目录实测都存在）
        (paks / "~mods").mkdir(exist_ok=True)
        (paks / "LogicMods").mkdir(exist_ok=True)
        made[name] = game_root

    # 顺带造一个「非游戏目录」，用来验证校验会不会把无关目录当游戏
    not_game = base / "not_a_game" / "some_folder"
    not_game.mkdir(parents=True, exist_ok=True)
    made["__not_game__"] = not_game
    return made


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path(tempfile.mkdtemp(prefix="wmm-fakegame-"))
    made = build(target)
    print(f"伪造游戏目录已建立在：{target}")
    for name, path in made.items():
        print(f"  {name:<22} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
