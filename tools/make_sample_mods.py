# -*- coding: utf-8 -*-
"""造测试用样例 mod（需求文档 R-7 的靶子）。

覆盖**实测与文档里出现过的全部形态**，每一种都对应一个真实的坑：

| 样例 | 它验证什么 |
|---|---|
| `street_style.zip` | 典型形态：自带 `Project_Plague\\Content\\Paks\\~mods` 层级 + webp 截图 + 说明.txt + .url |
| `street_style_again.zip` | 内容与上一个**完全相同**、只是压缩包名不同 → 必须被判为重复 |
| `logic_mod.zip` | `LogicMods` 层级 → 安装目标目录不同 |
| `flat_no_preview.zip` | `pakchunk1200-Windows.pak` 这类**清洗后一个字都不剩**的名字 + 无截图 → 字母卡 + 兜底名 |
| `with_ucas.zip` | `.pak + .ucas + .utoc` 三件套 → 三个都要搬，缺一不可 |
| `gbk_readme.zip` | readme 是 **GBK 编码** → 按 utf-8 读会抛异常/乱码 |
| `folder_mod/` | 普通文件夹导入 + 文件名带括号 `...(under.pak` |
| `zipslip.zip` | **恶意包**：成员名写成 `..\\evil.txt` → 必须拒绝解压，不得写到目标目录外 |
| `fake.rar` | 只有 rar 魔数、内容是垃圾 → 必须给**中文人话**报错而不是崩栈 |
| `not_a_mod.bin` | 不支持的输入 → 跳过并说明支持哪些格式 |
| `七z格式mod.7z` | 7z 解压路径（需装了 py7zr 才会生成） |

用法：
    python tools/make_sample_mods.py [目标目录]
"""

from __future__ import annotations

import random
import struct
import sys
import zipfile
from pathlib import Path

#: 造一个"看起来像 pak"的假文件（不是真 pak，只用于跑通流程与算哈希）
_PAK_MAGIC = b"\xE1\x12\x6F\x5A"      # UE pak 的魔数就是 0x5A6F12E1，这里按小端写


def fake_pak(seed: int, size: int = 4096) -> bytes:
    """生成确定性的假 pak 内容 —— 同 seed 同内容，便于验证去重。"""
    rng = random.Random(seed)
    body = bytes(rng.getrandbits(8) for _ in range(size))
    return _PAK_MAGIC + struct.pack("<I", 4) + body


def fake_image(kind: str, size: int = 24 * 1024) -> bytes:
    """生成一个"看起来像图片"的字节串（不解析，只用于挑图逻辑）。"""
    rng = random.Random(hash((kind, size)) & 0x7FFFFFFF)
    payload = bytes(rng.getrandbits(8) for _ in range(size))
    if kind == "webp":
        return b"RIFF" + struct.pack("<I", size) + b"WEBPVP8 " + payload
    if kind == "png":
        return b"\x89PNG\r\n\x1a\n" + payload
    return b"\xff\xd8\xff\xe0" + payload


def _zip(target: Path, entries: dict[str, bytes]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return target


def have_7z() -> bool:
    try:
        import py7zr  # noqa: F401
    except ImportError:
        return False
    return True


def _7z(target: Path, entries: dict[str, bytes]) -> Path:
    """造 7z。py7zr 在 1.x 里 `writestr` 与 `write` 都存在，但语义有细微差别，
    所以这里显式走 `writestr(data, arcname)` 并留一个 `writeall` 兜底。"""
    import py7zr

    target.parent.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(target, "w") as zf:
        for name, data in entries.items():
            zf.writestr(data, name)
    return target


def build(base: Path) -> dict[str, Path]:
    """在 base 下造出全部样例，返回 {名称: 路径}。"""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    made: dict[str, Path] = {}

    # ---- 1. 典型形态：自带安装层级 + webp 截图 + 说明 + url ----
    pak_a = fake_pak(1001)
    made["street_style"] = _zip(
        base / "街头少女风 蚰蜒套.zip",
        {
            "Project_Plague/Content/Paks/~mods/TK_WuChang_DYY-StreetStyle1511_P.pak": pak_a,
            "Project_Plague/Content/Paks/~mods/预览截图.webp": fake_image("webp"),
            "说明.txt": "把 ~mods 里的东西放进游戏目录即可\r\n".encode("utf-8"),
            "原站链接.url": b"[InternetShortcut]\r\nURL=https://www.nexusmods.com/\r\n",
        },
    )

    # ---- 2. 内容完全一样、压缩包名不同 → 必须判重复 ----
    made["street_style_again"] = _zip(
        base / "StreetStyle_v2修复版.zip",
        {
            "Project_Plague/Content/Paks/~mods/TK_WuChang_DYY-StreetStyle1511_P.pak": pak_a,
            "Project_Plague/Content/Paks/~mods/预览截图.webp": fake_image("webp"),
        },
    )

    # ---- 3. LogicMods ----
    made["logic_mod"] = _zip(
        base / "蓝图逻辑mod.zip",
        {
            "Project_Plague/Content/Paks/~mods/LogicMods/BP_WuChang_ExtraSlot_P.pak": fake_pak(1002, 2048),
        },
    )

    # ---- 4. 名字无信息量 + 无截图 → 字母卡 + 兜底/目录名 ----
    made["flat_no_preview"] = _zip(
        base / "mod.zip",
        {
            "pakchunk1200-Windows_P.pak": fake_pak(1003, 1024),
            "readme.txt": b"just a readme\n",
        },
    )

    # ---- 5. pak + ucas + utoc 三件套 ----
    made["with_ucas"] = _zip(
        base / "IoStore三件套.zip",
        {
            "~mods/MyIoStoreMod_P.pak": fake_pak(1004, 1500),
            "~mods/MyIoStoreMod_P.ucas": fake_pak(1005, 3000),
            "~mods/MyIoStoreMod_P.utoc": fake_pak(1006, 800),
        },
    )

    # ---- 6. GBK readme（压缩包名刻意用无信息量的"下载"，逼它走到 readme 那一级）----
    made["gbk_readme"] = _zip(
        base / "下载.zip",
        {
            "TestPak_P.pak": fake_pak(1007, 1200),
            "说明.txt": "无摔伤补丁 v1.2\r\n作者：某某\r\n".encode("gb18030"),
        },
    )

    # ---- 7. 普通文件夹（文件名带括号）----
    folder = base / "BaiNude 无遮挡修复"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "BaiNude_Flat_Clean_p(under.pak").write_bytes(fake_pak(1008, 2048))
    (folder / "封面.png").write_bytes(fake_image("png"))
    (folder / "Readme.txt").write_bytes(b"BaiNude flat clean\r\n")
    made["folder_mod"] = folder

    # ---- 8. 恶意包：路径穿越 ----
    zipslip = base / "zipslip.zip"
    with zipfile.ZipFile(zipslip, "w") as zf:
        zf.writestr("../evil.txt", b"you have been pwned")
        zf.writestr("~mods/Ok_P.pak", fake_pak(1009, 900))
    made["zipslip"] = zipslip

    # ---- 9. 假 rar（只有魔数）----
    fake_rar = base / "broken.rar"
    fake_rar.write_bytes(b"Rar!\x1a\x07\x00" + b"\x00" * 256)
    made["fake_rar"] = fake_rar

    # ---- 10. 不支持的输入 ----
    other = base / "not_a_mod.bin"
    other.write_bytes(b"\x00\x01\x02\x03" * 64)
    made["not_a_mod"] = other

    # ---- 11. 7z（只有装了 py7zr 才造得出来；需求文档第 6 章把 py7zr 列为依赖）----
    if have_7z():
        made["seven_zip"] = _7z(
            base / "七z格式mod.7z",
            {
                "~mods/SevenZPak_P.pak": fake_pak(1010, 1800),
                "预览图.png": fake_image("png"),
            },
        )

    return made


def main(argv: list[str]) -> int:
    import tempfile

    target = Path(argv[1]) if len(argv) > 1 else Path(tempfile.mkdtemp(prefix="wmm-samples-"))
    made = build(target)
    print(f"样例 mod 已建立在：{target}")
    for name, path in made.items():
        kind = "目录" if path.is_dir() else "文件"
        size = "" if path.is_dir() else f"  {path.stat().st_size} B"
        print(f"  {name:<20} [{kind}] {path.name}{size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
