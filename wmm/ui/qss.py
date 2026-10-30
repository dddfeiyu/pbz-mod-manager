# -*- coding: utf-8 -*-
"""全局样式表（QSS）。

样式集中在这里，不在各个控件里散着写 setStyleSheet —— 散着写的后果是
改一个颜色要翻十个文件，而且很容易出现"弹窗是深色、主窗是浅色"这种不一致。
"""

from __future__ import annotations

from .. import theme


def build_qss() -> str:
    t = theme
    return f"""
QWidget {{
    font-family: {t.FONT_FAMILY};
    font-size: {t.FONT_SIZE_BODY}pt;
    color: {t.TEXT};
}}

QMainWindow, #centralArea {{ background: {t.BG_WINDOW}; }}

/* ------------------------------------------------------------ 顶部栏 */
#topBar {{
    background: {t.BG_BAR};
    border-bottom: 1px solid {t.BORDER};
}}
/* 2026-10-11：程序名 / 版本 / 作者那一行已按用户要求整行删除（只留在系统标题栏），
   对应的 #appTitle / #authorName / #appVersion 规则一并移除以避免死样式。 */

#gameDirPath {{
    color: {t.TEXT_SECONDARY};
    font-size: {t.FONT_SIZE_SMALL}pt;
    background: {t.BG_SUNKEN};
    border: 1px solid {t.BORDER};
    border-radius: 4px;
    padding: 3px 8px;
}}
#gameDirPath[missing="true"] {{
    color: #9A5B00;
    background: #FFF6E5;
    border-color: #F0D8A8;
}}

/* 顶栏正中间的作者徽标（用户 2026-10-11 要求加回来的，
   与「关注作者」「关于与支持」同一行、垂直居中）。
   ⚠️ 它现在是**按钮**了（用户：「改成可以跳转的按钮，点击可以直接跳到B站主页」），
   ⚠️ 并且是**实心**的（用户：「中间这个按钮改成实心的吧，看着有点空」）——
   所以要写 hover / pressed，不能再是纯展示的静态标签。 */
#authorBadge {{
    color: {t.TEXT_ON_PRIMARY};
    font-size: 11pt;
    font-weight: 600;
    background: {t.PRIMARY};
    border: 1px solid {t.PRIMARY};
    border-radius: 4px;
    padding: 4px 12px;
}}
#authorBadge:hover {{ background: {t.PRIMARY_DARK}; border-color: {t.PRIMARY_DARK}; }}
#authorBadge:pressed {{ background: {t.PRIMARY_DARK}; }}

/* 「冲突」筛选页顶部的冲突明细面板（用户 2026-10-11：「放到冲突这一页中」）。 */
#conflictPanel {{
    background: #FFF9EC;
    border: 1px solid #F0DCB4;
    border-radius: 6px;
}}

/* ------------------------------------------------------------ 按钮 */
QPushButton {{
    background: {t.BG_CARD};
    border: 1px solid {t.BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 14px;
    /* ⚠️ 这里**故意不写 min-height**。踩过的坑：QSS 一旦给 QPushButton 指定了
       min-height，它会**覆盖控件代码里的 setMinimumSize()**。实测：
       充电按钮按 F-10.18b 写了 setMinimumSize(180, 44)，被这里的
       `min-height: 20px` 压成了 34px（= 20 + padding 6×2 + border 1×2），
       直接违反「≥ 180×44」的硬要求，而看截图几乎看不出来。
       所以：尺寸一律用代码里的 setMinimumSize 控制，QSS 只负责配色与内边距。 */
}}
QPushButton:hover {{ background: {t.BG_HOVER}; }}
QPushButton:pressed {{ background: {t.BG_SUNKEN}; }}
QPushButton:disabled {{ color: {t.TEXT_TERTIARY}; border-color: {t.BORDER}; background: {t.BG_SUNKEN}; }}

QPushButton#primaryButton {{
    background: {t.PRIMARY};
    border: 1px solid {t.PRIMARY};
    color: {t.TEXT_ON_PRIMARY};
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{ background: {t.PRIMARY_DARK}; border-color: {t.PRIMARY_DARK}; }}
QPushButton#primaryButton:pressed {{ background: {t.PRIMARY_DARK}; }}
/* ⚠️ 禁用态**绝不能**配 `color: #FFFFFF`。踩过的坑（2026-10-10）：这里原本是
   `background: {t.PRIMARY_DISABLED}(#F5C0CF 淡粉); color:#FFFFFF` ——
   白字压淡粉，对比度 ≈ 1.4:1，按钮等于隐形。用户的原话是
   「页面没有安装按钮和卸载按钮」。禁用态一律走「浅灰底 + 中灰字」，
   保证"看得出它在那儿、只是现在不能点"。 */
QPushButton#primaryButton:disabled {{ background: {t.BG_SUNKEN}; border-color: {t.BORDER}; color: {t.TEXT_TERTIARY}; }}

/* 「关于与支持」按钮：用户 2026-10-11「改个颜色，绿色吧，绿色舒服点」。
   与粉色「关注作者」并排时用绿能一眼分清两个入口；取墨绿而非亮绿，
   免得它比「导入 mod…」还抢眼。禁用态同样绝不用白字（同上）。 */
QPushButton#successButton {{
    background: {t.SUCCESS};
    border: 1px solid {t.SUCCESS};
    color: {t.TEXT_ON_PRIMARY};
    font-weight: 600;
}}
QPushButton#successButton:hover {{ background: {t.SUCCESS_DARK}; border-color: {t.SUCCESS_DARK}; }}
QPushButton#successButton:pressed {{ background: {t.SUCCESS_DARK}; }}
QPushButton#successButton:disabled {{ background: {t.BG_SUNKEN}; border-color: {t.BORDER}; color: {t.TEXT_TERTIARY}; }}

/* 「关注作者 · 一键跳转」：用户 2026-10-11 先说「改成黄色」，看了成品又说
   「这个颜色太丑了，换一个」→ 改成沉稳的蓝。
   ⚠️ 顶栏三颗实心按钮：粉=作者徽标 / 绿=关于与支持 / 蓝=关注作者。
   蓝的饱和度天然低于粉，不会抢「导入 mod…」的主色位。 */
QPushButton#accentButton {{
    background: {t.ACCENT};
    border: 1px solid {t.ACCENT};
    color: {t.TEXT_ON_PRIMARY};
    font-weight: 600;
}}
QPushButton#accentButton:hover {{ background: {t.ACCENT_DARK}; border-color: {t.ACCENT_DARK}; }}
QPushButton#accentButton:pressed {{ background: {t.ACCENT_DARK}; }}
QPushButton#accentButton:disabled {{ background: {t.BG_SUNKEN}; border-color: {t.BORDER}; color: {t.TEXT_TERTIARY}; }}

/* 危险动作按钮（工具条「从库中删除所选」）。
   用**描边红字**而不是实心红底 —— 实心红会在视觉权重上跟主色「导入 mod…」
   打平，用户一眼扫过去分不清主次；而删除是次要频次动作，描边足够醒目。
   禁用态同样绝不用白字（见上面 primaryButton 那段注释里的坑）。 */
QPushButton#dangerButton {{
    color: {t.CONFLICT_COLORS['hard']};
    border: 1px solid {t.CONFLICT_COLORS['hard']};
    background: transparent;
}}
QPushButton#dangerButton:hover {{ background: #FDEDEB; }}
/* 前置缺失时的「安装前置」按钮（2026-10-29）：橙色描边，比 dangerButton 温和
   —— 它不是一个危险动作，但必须比普通按钮更扎眼，否则用户根本注意不到。 */
QPushButton#warnButton {{
    color: #A65E00;
    border: 1px solid #E0A030;
    background: #FFF8EC;
    padding: 0 14px;
    font-weight: 600;
}}
QPushButton#warnButton:hover {{ background: #FFF1D6; }}
QPushButton#dangerButton:disabled {{
    color: {t.TEXT_TERTIARY}; border-color: {t.BORDER}; background: {t.BG_SUNKEN};
}}

/* 冲突清单右上角的「收起 / 展开」（用户 2026-10-11：
   「这个冲突清单，无法缩小……可以加个隐藏或者缩小的按钮」）。
   刻意做成**小号描边**：它是版面控制，不是主操作，别跟「导入 mod…」抢视觉权重。 */
QPushButton#toggleButton {{
    color: {t.TEXT_SECONDARY};
    background: {t.BG_WINDOW};
    border: 1px solid {t.BORDER};
    border-radius: 4px;
    padding: 2px 10px;
    font-size: 9pt;
}}
QPushButton#toggleButton:hover {{
    color: {t.PRIMARY}; border-color: {t.PRIMARY}; background: {t.BG_HOVER};
}}
QPushButton#toggleButton:pressed {{ background: {t.PRIMARY_DISABLED}; }}

/* 导入选择框里的「浏览文件… / 选文件夹…」（用户 2026-10-11 要的原生入口）。
   用主色**描边**而不是实心 —— 这一行的主色实心是右下角「导入选中的 N 项」，
   描边足以表达"这是个入口"，又不会让人误以为按它就一定开始导入。 */
QPushButton#browseButton {{
    color: {t.PRIMARY_DARK};
    border: 1px solid {t.PRIMARY};
    background: transparent;
    font-weight: 600;
}}
QPushButton#browseButton:hover {{ background: #FDEBF1; }}

/* 筛选按钮：已选中的用主色描边，不抢「导入」的视觉权重 */
QPushButton#filterButton {{
    border-radius: 14px;
    padding: 4px 14px;
    color: {t.TEXT_SECONDARY};
    background: transparent;
    border: 1px solid transparent;
}}
QPushButton#filterButton:hover {{ background: {t.BG_HOVER}; }}
QPushButton#filterButton:checked {{
    background: #FDEBF1;
    border-color: {t.PRIMARY};
    color: {t.PRIMARY_DARK};
    font-weight: 600;
}}

/* ------------------------------------------------------------ 输入 */
QLineEdit {{
    background: {t.BG_CARD};
    border: 1px solid {t.BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 10px;
    selection-background-color: {t.PRIMARY};
    selection-color: white;
}}
QLineEdit:focus {{ border-color: {t.PRIMARY}; }}

/* ------------------------------------------------------------ 卡片 */
#modCard {{ background: transparent; }}
#modCard:hover {{ background: {t.BG_HOVER}; border-radius: 10px; }}

/* ------------------------------------------------------------ 滚动区 */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t.BORDER_STRONG}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.TEXT_TERTIARY}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.BORDER_STRONG}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ------------------------------------------------------------ 空状态 */
#emptyTitle {{ font-size: 13pt; font-weight: 600; color: {t.TEXT_SECONDARY}; }}
#emptyHint {{ color: {t.TEXT_TERTIARY}; }}

/* ------------------------------------------------------------ 状态栏 */
QStatusBar {{ background: {t.BG_BAR}; border-top: 1px solid {t.BORDER}; color: {t.TEXT_SECONDARY}; }}
QStatusBar::item {{ border: none; }}

/* ------------------------------------------------------------ 列表 / 表格（扫描对话框） */
QListWidget {{
    background: {t.BG_CARD};
    border: 1px solid {t.BORDER};
    border-radius: 6px;
    outline: none;
}}
QListWidget::item {{ padding: 6px 8px; border-bottom: 1px solid {t.BG_SUNKEN}; }}
QListWidget::item:selected {{ background: #FDEBF1; color: {t.TEXT}; }}

QDialog {{ background: {t.BG_WINDOW}; }}
QLabel#dialogTitle {{ font-size: 12pt; font-weight: 600; }}
QLabel#dialogHint {{ color: {t.TEXT_SECONDARY}; }}

QProgressBar {{
    border: 1px solid {t.BORDER};
    border-radius: 6px;
    background: {t.BG_SUNKEN};
    text-align: center;
    height: 18px;
}}
QProgressBar::chunk {{ background: {t.PRIMARY}; border-radius: 5px; }}

QToolTip {{
    background: #2B2F33; color: #FFFFFF; border: none; padding: 4px 8px;
}}
"""
