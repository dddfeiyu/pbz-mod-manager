# -*- coding: utf-8 -*-
"""卡片视图模型 —— 把「mod 库」与「安装记录」合成界面要显示的东西。

刻意**不依赖 Qt**：这样"卡片该显示什么"这件事可以在无界面环境下测试，
界面只负责把结果画出来。需求文档里最容易做歪的一类 bug
（状态角标显示错、筛选漏项）都属于这一层，而不是绘制层。

状态定义（需求文档 3.1）：
  - `not_installed` 未装
  - `installed`     已装
  - `conflict`      冲突（阶段 3 的冲突检测填这个值；v1 阶段这里恒为空）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from . import (
    costume,
    gamedir,
    library as library_mod,
    modstore,
    placeholder,
    preview,
    theme,
    wildscan,
)

STATUS_NOT_INSTALLED = "not_installed"
STATUS_INSTALLED = "installed"
STATUS_CONFLICT = "conflict"

FILTER_ALL = "all"
FILTER_INSTALLED = "installed"
FILTER_NOT_INSTALLED = "not_installed"
FILTER_CONFLICT = "conflict"

FILTER_LABELS = {
    FILTER_ALL: "全部",
    FILTER_INSTALLED: "已装",
    FILTER_NOT_INSTALLED: "未装",
    FILTER_CONFLICT: "冲突",
}
FILTER_ORDER = (FILTER_ALL, FILTER_INSTALLED, FILTER_NOT_INSTALLED, FILTER_CONFLICT)


@dataclass
class ModView:
    """一张卡片需要知道的全部信息。"""

    meta: modstore.ModMeta
    status: str = STATUS_NOT_INSTALLED
    installed: bool = False
    #: 已装到哪些游戏目录（多平台并存时可能不止一个）
    installed_in: List[str] = field(default_factory=list)
    #: 冲突的资源路径（阶段 3 填）
    conflicts: List[str] = field(default_factory=list)
    #: 冲突分级 hard / potential / none（阶段 3 填）
    conflict_level: str = "none"
    #: 冲突**组**的颜色（用户 2026-10-11：「多个 mod 冲突时颜色变成同一个」）。
    #: 由 `conflicts.group_colors()` 按连通分量算出；无冲突时为空串。
    #: ⚠️ 这与 `conflict_level` 是两回事：level 说"有多严重"，
    #:    group_color 说"跟谁是一伙的" —— 一个 mod 可能同时和两组都有牵连。
    conflict_color: str = ""
    #: 预览图方案（四档降级的结果）
    preview: Optional[preview.PreviewSpec] = None
    #: **手装命中**（用户 2026-10-12）：游戏目录里躺着内容相同的文件、
    #: 但没有本工具的安装记录（用户手动放进去的 / 更早的别的工具装的）。
    #: `wildscan.recognize()` 的产物，经缓存进这里。
    external_hit: Optional[wildscan.ExternalHit] = None

    @property
    def external(self) -> bool:
        """是否是「手装生效中」。

        ⚠️ 与 `installed`（本工具装的）是两回事，UI 上必须能区分：
        这个 mod 游戏**确实在加载**（所以 status 算「已装」），
        但本工具没有它的安装记录（所以不能代为卸载）。
        """
        return self.external_hit is not None and not self.installed

    @property
    def external_badge(self) -> str:
        """卡片状态角标下方的小徽标文字（空 = 不画）。"""
        return "手装" if self.external else ""

    @property
    def external_tooltip(self) -> str:
        """手装状态的说明（tooltip / 详情用）。"""
        if not self.external or self.external_hit is None:
            return ""
        hit = self.external_hit
        if hit.complete:
            return (
                f"手装：游戏目录里有这份 mod 的 {hit.total} 个文件"
                "（不是本工具安装的，没有安装记录）"
            )
        return (
            f"手装：游戏目录里只有它的 {hit.matched}/{hit.total} 个文件"
            "（不是本工具安装的，没有安装记录）"
        )

    @property
    def series_badge(self) -> str:
        """**同系列合并**的标识文字（用户 2026-10-11：「识别出来后，加个标识」）。

        导入时若把几个并列目录判成同一个 mod 的多个部件（`meta.series_parts`
        有 ≥2 项），这里返回「同系列 N 个部件」；否则空串。

        ⚠️ 与 `conflict_color` 是两回事：那个说"跟谁有冲突"，这个说
        "它本身就是几个部件合起来的" —— 一个 mod 可以同系列又同时有冲突。
        """
        parts = getattr(self.meta, "series_parts", None) or []
        if len(parts) < 2:
            return ""
        return f"同系列 {len(parts)} 个部件"

    @property
    def series_parts_text(self) -> str:
        """同系列各部件的目录名（详情/提示里用）。"""
        parts = getattr(self.meta, "series_parts", None) or []
        return "、".join(parts) if len(parts) >= 2 else ""

    @property
    def placeholder_kind(self) -> str:
        """**没有预览图时画哪个图标**（用户 2026-10-11）。

        原来是「首字字母卡 + 按名字哈希的随机色」—— 用户看完问
        「这些没有预览图的 mod 怎么处理会更好些」，结论是占位图应该
        回答"这是什么"而不是单纯占个位。
        判据见 `placeholder.classify()`：pak 名 > mod 名称 > 服装部位。

        ⚠️ 2026-10-30：`meta.slots` 只在**手动指定**时才直接采信；否则**重跑
        一遍 `costume.analyze`**。原因：识别逻辑改过好几轮，库里存着旧版本
        认错的结论（用户截图里「三面偶 果体mod」被老版本判成「武器」），
        如果永远读 meta 里那个值，改多少次代码用户看到的都还是错的。
        analyze 只做字符串匹配，几十上百个 mod 也不会有可感开销。
        """
        meta = self.meta
        if getattr(meta, "costume_source", "") == "手动指定":
            slots = getattr(meta, "slots", None) or ()
        else:
            info = costume.analyze(
                path_segments=[getattr(meta, "source_archive", "") or self.name],
                pak_stems=[Path(f.name).stem for f in meta.files],
            )
            slots = info.slots
        return placeholder.classify(
            name=self.name,
            pak_stems=[Path(f.name).stem for f in meta.files],
            slots=slots,
        )

    @property
    def placeholder_label(self) -> str:
        """占位图标下面那行小字（「上衣」「武器」「技能」…）。"""
        return placeholder.label_for(self.placeholder_kind)

    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def status_label(self) -> str:
        return theme.STATUS_LABELS.get(self.status, self.status)

    @property
    def status_colors(self) -> tuple[str, str]:
        return theme.STATUS_COLORS.get(self.status, theme.STATUS_COLORS[STATUS_NOT_INSTALLED])

    @property
    def outfit_text(self) -> str:
        """卡片上那一行「替换 XX · 上衣」。

        识别不出来时返回空串 —— 宁可不画，也不要画一行「未知」，
        那既占地方又让用户以为工具坏了。

        ⚠️ `replaces` 是**推断**出来的（costume.py 的四路证据），不是解析 pak 得到的。
        所以这里只写结论，把依据与可信度留给 tooltip / 详情，避免卡片上信息过载。
        """
        bits: List[str] = []
        replaces = getattr(self.meta, "replaces", None) or []
        slots = getattr(self.meta, "slots", None) or []
        if replaces:
            bits.append("替换 " + "、".join(replaces[:2]))
        if slots:
            labels = [costume.SLOT_LABELS.get(s, s) for s in slots]
            bits.append("、".join(labels[:4]))
        return " · ".join(bits)

    @property
    def outfit_tooltip(self) -> str:
        """服装识别的依据说明（给详情弹窗与 tooltip 用）。"""
        meta = self.meta
        source = getattr(meta, "costume_source", "") or ""
        conf = getattr(meta, "costume_confidence", "") or ""
        if not source and not conf:
            return ""
        conf_label = costume.CONF_LABELS.get(conf, conf)
        return f"服装识别依据：{source or '—'}（可信度：{conf_label or '—'}）"

    @property
    def summary(self) -> str:
        """卡片下方那行小字。

        ⚠️ 不能只报 pak 数量：`.pak + .ucas + .utoc` 是**三件套**，缺一不可。
        只显示「1 个 pak」会让人以为只有一个文件，卸载时也容易以为删一个就够。
        """
        bits: List[str] = []
        total_files = len(self.meta.files)
        if self.meta.pak_count:
            if total_files > self.meta.pak_count:
                bits.append(f"{self.meta.pak_count} 个 pak + {total_files - self.meta.pak_count} 个附属文件")
            else:
                bits.append(f"{self.meta.pak_count} 个 pak")
        elif total_files:
            bits.append(f"{total_files} 个文件")
        size = self.meta.total_size
        if size:
            bits.append(_human_size(size))
        if self.meta.author:
            bits.append(self.meta.author)
        return " · ".join(bits)

    @property
    def subdir_note(self) -> str:
        if self.meta.install_subdir and self.meta.install_subdir != "~mods":
            return f"目标：{self.meta.install_subdir}"
        return ""


def _human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def build_views(
    store: modstore.ModStore,
    lib: Optional[library_mod.Library] = None,
    *,
    game_dir: Optional[str | Path] = None,
    filter_status: str = FILTER_ALL,
    query: str = "",
    conflicts: Optional[dict[str, tuple[str, List[str]]]] = None,
    conflict_colors: Optional[dict[str, str]] = None,
    external: Optional[dict[str, wildscan.ExternalHit]] = None,
) -> List[ModView]:
    """把库里所有 mod 变成卡片模型。

    参数
    ----
    lib / game_dir
        给了就能算出「已装」状态。不给则一律显示未装（例如还没设游戏目录时）。
    conflicts
        `{mod_id: (level, [资源路径, ...])}` —— 阶段 3 的冲突检测结果。
        这里做成参数而不是在这里算，是因为冲突检测属于独立能力，
        不该和"卡片显示什么"耦合在一起。
    conflict_colors
        `{mod_id: "#RRGGBB"}` —— 冲突**组**的颜色（`conflicts.group_colors()` 的输出）。
        用户 2026-10-11 要求「多个 mod 冲突时颜色变成同一个」：
        同一组互相牵连的 mod 共用一个描边色，不同组不同色。
    external
        `{mod_id: wildscan.ExternalHit}` —— 「手装」命中（用户 2026-10-12）。
        游戏目录里有内容相同的文件、但本工具没有安装记录。
        ⚠️ 与 `installed` 叠加时的优先级见下面 status 的计算：
        有安装记录 → 已装（本工具）；只有手装命中 → 也算「已装」
        （游戏确实在加载它），但卡片上会多一个「手装」徽标，
        且**不能**走卸载流程（没有记录可卸）。
    """
    conflicts = conflicts or {}
    conflict_colors = conflict_colors or {}
    external = external or {}
    installed_ids: set[str] = set()
    installed_map: dict[str, List[str]] = {}

    if lib is not None:
        for record in lib.all():
            installed_map.setdefault(record.mod_id, []).append(record.game_dir)
        if game_dir:
            installed_ids = lib.installed_ids(game_dir)

    views: List[ModView] = []
    for meta in store.all():
        level, paths = conflicts.get(meta.id, ("none", []))
        installed = meta.id in installed_ids if game_dir else False
        hit = external.get(meta.id)

        # ⚠️ 手装命中**也算「已装」**：游戏确实在加载这份内容，
        #    显示「未装」是在骗用户（2026-10-11 事故的直接原因）。
        #    但 `installed` 仍为 False（没有本工具的安装记录）——
        #    卸载所选、安装所选的计数都看这个，不能混。
        externally = hit is not None and not installed

        if level == "hard":
            status = STATUS_CONFLICT
        elif installed or externally:
            status = STATUS_INSTALLED
        else:
            status = STATUS_NOT_INSTALLED

        # mod_id 可能是用户手改 meta.json 塞进来的非法串；mod_dir() 会抛异常，
        # 但那不该让整个界面刷不出来 —— 退化成"没有预览图"即可。
        try:
            mod_dir = store.mod_dir(meta.id)
        except ValueError:
            mod_dir = None

        views.append(
            ModView(
                meta=meta,
                status=status,
                installed=installed,
                installed_in=installed_map.get(meta.id, []),
                conflicts=list(paths),
                conflict_level=level,
                conflict_color=conflict_colors.get(meta.id, ""),
                external_hit=hit,
                preview=preview.build_preview_spec(
                    mod_name=meta.name,
                    meta_preview=meta.preview,
                    meta_tier=meta.preview_tier,
                    mod_dir=mod_dir,
                ),
            )
        )

    views = _apply_filter(views, filter_status)
    views = _apply_query(views, query)
    views.sort(key=lambda v: _sort_key(v))
    return views


def _sort_key(view: ModView):
    """排序：冲突 → 已装 → 未装，然后按名称。

    刻意**不**按导入时间倒序：用户找 mod 是按名字找的，
    而且 pak 的加载优先级跟名称有关，按名称排能让人一眼看出顺序。
    """
    order = {STATUS_CONFLICT: 0, STATUS_INSTALLED: 1, STATUS_NOT_INSTALLED: 2}
    return (order.get(view.status, 9), view.name.lower())


def _apply_filter(views: Sequence[ModView], filter_status: str) -> List[ModView]:
    if filter_status in ("", FILTER_ALL, None):
        return list(views)
    # ⚠️ 「冲突」页必须**按 conflict_level 筛**，不能按 status 筛。
    #   status 只在 hard 时才置成 STATUS_CONFLICT（那是要顶到列表最前面的排序），
    #   而 potential（潜在冲突 —— 替换同一件衣服全都属于这一类）status 仍是 installed，
    #   按 status 筛的话点「冲突」页会**一张卡都看不到**（用户 2026-10-11 截图就是这个现象：
    #   页签写着「冲突 0」但页面上全是普通卡片）。
    if filter_status == FILTER_CONFLICT:
        return [v for v in views if v.conflict_level and v.conflict_level != "none"]
    return [v for v in views if v.status == filter_status]


def _apply_query(views: Sequence[ModView], query: str) -> List[ModView]:
    q = (query or "").strip().lower()
    if not q:
        return list(views)
    out: List[ModView] = []
    for v in views:
        haystack = " ".join(
            filter(
                None,
                (
                    v.name,
                    v.meta.author,
                    v.meta.source_url,
                    v.meta.notes,
                    v.meta.source_archive,
                    " ".join(f.name for f in v.meta.files),
                ),
            )
        ).lower()
        if q in haystack:
            out.append(v)
    return out


def count_by_status(views: Sequence[ModView]) -> dict[str, int]:
    counts = {FILTER_ALL: len(views), STATUS_INSTALLED: 0, STATUS_NOT_INSTALLED: 0, STATUS_CONFLICT: 0}
    for v in views:
        counts[v.status] = counts.get(v.status, 0) + 1
    # ⚠️ 「冲突」页的数字必须与 `_apply_filter` 的口径**完全一致**（都按 conflict_level 算），
    #   否则页签写着「冲突 3」而点进去一张卡都没有 —— 用户 2026-10-11 截图就是这个现象。
    #   status 只在 hard 时才置 conflict，potential（替换同一件衣服）那批全在 installed 里。
    counts[FILTER_CONFLICT] = sum(
        1 for v in views if v.conflict_level and v.conflict_level != "none"
    )
    return counts


def filter_counts(
    store: modstore.ModStore,
    lib,
    game_dir,
    conflicts: Optional[dict[str, tuple[str, List[str]]]] = None,
    external: Optional[dict[str, wildscan.ExternalHit]] = None,
) -> dict[str, int]:
    """给筛选按钮上的数字用 —— 基于**全部**卡片，不受当前筛选影响。

    ⚠️ `conflicts` **必须**和刷新卡片时传的是同一份。
    踩过的坑：这里原来没接冲突参数，于是「冲突」按钮永远显示 `冲突 0`，
    而右边汇总行却写着 `冲突 🔴 1` —— 同一个界面自相矛盾，
    用户点进「冲突」筛选反而能看到卡片，会以为工具坏了一半。

    `external` 同理：手装命中的 mod 也算「已装」，两处口径必须一致。
    """
    return count_by_status(
        build_views(
            store, lib, game_dir=game_dir, conflicts=conflicts, external=external
        )
    )


# ---------------------------------------------------------------- 平台标签
PLATFORM_BADGE = {
    gamedir.PLATFORM_STEAM: ("Steam", "#1B6BB0"),
    gamedir.PLATFORM_EPIC: ("Epic", "#2A2A2A"),
    gamedir.PLATFORM_GAMEPASS: ("Game Pass", "#107C10"),
    gamedir.PLATFORM_UNKNOWN: ("未识别平台", "#8A9099"),
}


def platform_badge(platform: str) -> tuple[str, str]:
    return PLATFORM_BADGE.get(platform, PLATFORM_BADGE[gamedir.PLATFORM_UNKNOWN])
