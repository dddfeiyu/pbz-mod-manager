# -*- coding: utf-8 -*-
"""mod 名称自动推导链（需求文档 3.7）。

推导优先级（原文）：
  1. mod 文件名
  2. 压缩包内目录名
  3. 包内 readme / 说明文件首行
  4. 兜底：`未命名 mod 001`（按导入顺序编号）

关于第 1 级原文的「mod 文件名」，本项目采用如下判读并在此写明理由：
在 F-2.1~F-2.4 的四种导入方式里，用户手上拿着的东西是**压缩包 / 文件夹 / 裸 pak**，
而「压缩包内目录名」被原文明确列在第 2 级 —— 这说明第 1 级指的是**用户手上那个东西的名字**，
不是包内 pak 的名字。所以：
  - 拖入 `街头少女风 蚰蜒套.zip` → 第 1 级 = `街头少女风 蚰蜒套`
  - 直接导入 `xxx_P.pak`          → 第 1 级 = `xxx`
  - 导入文件夹 `无摔伤/`           → 第 1 级 = `无摔伤`
这与实测高度吻合：**用户自己起的压缩包/文件夹名，信息量通常远高于包内 pak 名**
（实测 24% 的 pak 名清洗后一个字都不剩，如 `pakchunk1200-Windows.pak`）。

⚠️ 关键点：**每一级清洗后可能为空**（实测证明），必须逐级回落，
不能在第 1 级拿到空串就当成"这个 mod 没有名字"直接上兜底。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from .gamedir import sanitize_mod_name

#: 清洗后仍属"无信息量"的名字 —— 出现时必须继续往下一级回落
_MEANINGLESS = frozenset(
    {
        "mod", "mods", "the mod", "new folder", "新建文件夹", "新建 文件夹",
        "download", "downloads", "下载", "data", "files", "file", "content",
        "paks", "pak", "steam", "game", "games", "gamepass", "epic",
        "wuchang", "project plague", "projectplague", "win64", "binaries",
        "logicmods", "~mods", "ue4ss", "bitfix",
        "游戏", "安装包", "压缩包", "未命名", "unnamed", "unknown", "temp",
        "copy", "副本", "backup", "备份", "screenshots", "screenshot",
    }
)

#: readme 首行里要剥掉的装饰性字符
_README_PREFIX_PATTERN = re.compile(r"^[\s#*=\-—>·・\[\]【】\(\)（）:：\"]+")
_README_SUFFIX_PATTERN = re.compile(r"[\s#*=\-—<·・\[\]【】]+$")

#: 说明文字的特征 —— 命中说明这一行是"说明文件自己的标题"，不是 mod 名。
#: 实测 readme 首行常常是 `Readme` / `使用说明` / `Changelog`，拿它当 mod 名毫无意义。
_README_LIKE_PATTERN = re.compile(
    r"(read\s?me|changelog|install(ation)?\s?(guide|note)|使用说明|安装说明|说明文件|注意事项|更新日志)",
    re.IGNORECASE,
)

#: 网址 / 邮箱 / 目录路径 —— readme 首行常常是"原站链接.txt""下载地址"，
#: 拿它当 mod 名会得到 `httpswwwnexusmodscomwuchang…` 这种垃圾。
_URL_LIKE_PATTERN = re.compile(r"(https?://|www\.|\.com\b|\.net\b|\.org\b|\S+@\S+\.\S+|[\\/])", re.IGNORECASE)

#: 形如 `作者：xxx` / `版本：1.2` 的字段行 —— 是元信息标签，不是标题
_LABEL_LINE_PATTERN = re.compile(r"^[^:：\n]{1,8}[:：]\S")

#: 名称最大长度（UI 卡片一行放不下，超过就截断）
MAX_NAME_LENGTH = 60


@dataclass
class NameCandidate:
    """一个候选名及其来路。保留全部候选是为了让用户知道"这名字哪来的"。"""

    raw: str
    cleaned: str
    source: str          # 人类可读来路，如 "压缩包名"
    tier: int
    rejected_reason: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.cleaned) and not self.rejected_reason


@dataclass
class NameResolution:
    """名称推导的完整结果。"""

    name: str
    source: str
    tier: int
    candidates: List[NameCandidate] = field(default_factory=list)
    #: 是否走了兜底编号。
    #: ⚠️ 不能用 `tier >= 4` 判断 —— tier 是位置编号，候选链长度随输入而变：
    #: 只给了 1 个输入时兜底落在 tier 2。兜底是**语义**，必须单独记一个标记。
    is_fallback: bool = False


def _is_meaningful(cleaned: str) -> tuple[bool, str]:
    """判断清洗后的名字是否有信息量。返回 (是否可用, 不可用原因)。"""
    if not cleaned:
        return False, "清洗后为空（原文件名不含 mod 名）"
    low = cleaned.lower().strip()
    if low in _MEANINGLESS:
        return False, "通用词，无区分度"
    # 长度：中文一个字就有意义（真实例：作者把「全果」那档的文件夹直接命名为「果」），
    # 所以单字放行**仅限** CJK；拉丁字母单字（a / x）仍视为无意义。
    if len(cleaned) < 2:
        is_single_cjk = len(cleaned) == 1 and "\u4e00" <= cleaned <= "\u9fff"
        if not is_single_cjk:
            return False, "长度不足 2 字"
    if cleaned.isdigit():
        return False, "纯数字"
    # 纯 pakchunk / 纯编号
    if re.fullmatch(r"[\d\s\-_]+", cleaned):
        return False, "只有数字与分隔符"
    if _README_LIKE_PATTERN.search(cleaned):
        return False, "是说明文件的标题，不是 mod 名"
    if _URL_LIKE_PATTERN.search(cleaned):
        return False, "是网址/邮箱/路径，不是 mod 名"
    return True, ""


def _cap(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > MAX_NAME_LENGTH:
        value = value[: MAX_NAME_LENGTH - 1].rstrip() + "…"
    return value


def is_meaningful(raw: str) -> bool:
    """这个原始名（目录名 / 文件名）有没有资格当 mod 名的候选。

    对导入器暴露的公开版：先清洗再判。用来**往下钻**找包内目录名
    （见 `importer._inner_dir_candidates`）。
    """
    cleaned = sanitize_mod_name(raw, strip_extension=False)
    ok, _reason = _is_meaningful(cleaned)
    return ok


def _clean_first_line(text: str) -> str:
    """从 readme/说明文件正文里抽一行像标题的文字。"""
    for raw_line in text.splitlines()[:25]:
        line = raw_line.strip()
        if not line:
            continue
        if set(line) <= set("-=_*#~ "):     # 纯分隔线
            continue
        if _LABEL_LINE_PATTERN.match(line):  # `作者：xxx` 这类字段行
            continue
        line = _README_PREFIX_PATTERN.sub("", line)
        line = _README_SUFFIX_PATTERN.sub("", line)
        line = _cap(line)
        if len(line) >= 2:
            return line
    return ""


def read_readme_first_line(path: str | os.PathLike[str], *, max_bytes: int = 8192) -> str:
    """读 readme 首行。**必须容错** —— 实测 readme 有 GBK 编码，
    直接 utf-8 读会抛异常甚至把整行变成乱码。"""
    p = Path(path)
    try:
        blob = p.read_bytes()[:max_bytes]
    except OSError:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return _clean_first_line(blob.decode(encoding))
        except (UnicodeDecodeError, LookupError):
            continue
    return ""


def resolve_name(
    inputs: Sequence[tuple[str, str]],
    *,
    fallback_index: Optional[int] = None,
    extra_readme_text: str = "",
) -> NameResolution:
    """按推导链选名字。

    参数
    ----
    inputs
        候选列表，元素是 `(原始值, 来路标签)`。**调用方要按优先级排好**：
        一般是 `[("街头少女风 蚰蜒套.zip", "压缩包名"), ("街头少女风 蚰蜒套", "包内目录名")]`。
        来路标签里的第 1 项会被当作 tier=1，以此类推。
    fallback_index
        兜底编号（按导入顺序）。给了就用 `未命名 mod 001`，否则用 `未命名 mod`。
    extra_readme_text
        调用方已经从 readme 读到的正文；若 inputs 里没带 readme 候选，可从这里追加。
    """
    candidates: List[NameCandidate] = []

    for tier, (raw, source) in enumerate(inputs, start=1):
        # strip_extension=False：传进来的已经是 `Path.stem`（去处过扩展名）或
        # readme 首行。再让 splitext 拆一次会把 `v1.2` 的 `.2` 当扩展名扔掉。
        cleaned = _cap(sanitize_mod_name(raw, strip_extension=False))
        ok, reason = _is_meaningful(cleaned)
        candidates.append(
            NameCandidate(
                raw=str(raw),
                cleaned=cleaned if ok else "",
                source=source,
                tier=tier,
                rejected_reason="" if ok else reason,
            )
        )

    if extra_readme_text:
        tier = len(inputs) + 1
        cleaned = _cap(sanitize_mod_name(extra_readme_text, strip_extension=False)) or _cap(extra_readme_text)
        ok, reason = _is_meaningful(cleaned)
        candidates.append(
            NameCandidate(
                raw=extra_readme_text,
                cleaned=cleaned if ok else "",
                source="readme 首行",
                tier=tier,
                rejected_reason="" if ok else reason,
            )
        )

    for cand in candidates:
        if cand.usable:
            return NameResolution(name=cand.cleaned, source=cand.source, tier=cand.tier, candidates=candidates)

    # 兜底（需求文档 3.7 第 4 级）
    if fallback_index is None:
        fallback = "未命名 mod"
    else:
        fallback = f"未命名 mod {fallback_index:03d}"
    candidates.append(
        NameCandidate(
            raw="",
            cleaned=fallback,
            source="兜底编号",
            tier=len(inputs) + (2 if extra_readme_text else 1),
        )
    )
    return NameResolution(
        name=fallback,
        source="兜底编号",
        tier=candidates[-1].tier,
        candidates=candidates,
        is_fallback=True,
    )
