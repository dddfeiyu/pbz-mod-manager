# -*- coding: utf-8 -*-
"""随包分发物料的**文案生成**（需求文档 11.4 / 11.8）。

本模块只产出纯文本，不碰 Qt、不碰文件系统 —— 这样它可以在没有界面、
甚至没有 PySide6 的环境里测试，也能被单测逐条核对验收口径。

产出两类东西：

1. `build_readme()` —— 发布 zip 里的 `说明.txt`（F-11.16 ~ F-11.20）。
   这是**用户自救手册**。公开发布意味着没有客服、没有远程协助，
   用户遇到杀软拦截 / 缺运行库时能翻的就是这一页，所以宁可啰嗦。

2. `build_release_kit()` —— 给作者用的发布物料（视频简介 + 置顶评论 + FAQ），
   一次性写好，避免 F-11.29 说的「稿件发布后反复改稿触发限流」。

⚠️ 一条硬红线贯穿整份文案：**严禁出现「建议关闭杀毒软件」类表述**
   （F-11.17 + 11.7 验收标准）。教用户关杀软是坏建议，且会让用户暴露于真实风险。
   测试里有专门的断言守这一条。

另一处必须解释清楚的设计（否则会被当成 bug）：
   `说明.txt` **不可能**包含「整个 zip 的 SHA-256」—— 它在 zip 里面，
   自指不可能。所以这里放的是**可当场核对的 exe 哈希**，
   而 zip 的哈希走发布页公示（F-11.19 / F-11.8 / 10.27）。
"""

from __future__ import annotations

from typing import Optional, Sequence

from . import apppaths, version
from .games import ACTIVE as GAME, mods_rel_path

#: 微软官方运行库下载页（F-11.18）。不直接给 .exe 直链 —— 直链会随版本变动失效。
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
VC_REDIST_PAGE = "https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist"

#: 微软误报申诉通道（11.2 零成本方案第 2 条）
WDSI_SUBMISSION = "https://www.microsoft.com/wdsi/filesubmission"

_HORIZONTAL = "=" * 72
_THIN = "-" * 72


def zip_name() -> str:
    """发布 zip 的文件名（同时用作解压后的目录名，保持一致的观感）。"""
    return f"{version.EXE_NAME}-v{version.VERSION}-win64"


def build_readme(
    *,
    exe_sha256: Optional[str] = None,
    release_channels: Optional[Sequence[str]] = None,
    zip_sha256_hint: str = "见发布页公示",
) -> str:
    """生成 `说明.txt` 全文。

    参数：
      exe_sha256       —— 主程序 exe 的 SHA-256；给不出时留占位说明。
      release_channels —— 官方渠道清单，默认取 version 里的。
      zip_sha256_hint  —— zip 哈希的指引文案（默认指向发布页）。
    """
    channels = list(release_channels or version.RELEASE_CHANNELS)
    channel_text = " / ".join(channels)
    exe_hash_line = (
        f"    {exe_sha256}"
        if exe_sha256
        else "    （打包时自动填入；若这里是空的，说明拿到的是非官方构建）"
    )

    _mods_rel = mods_rel_path(GAME).replace("/", _sep())
    _enable_faq = (
        "  Q: mod 装了，但游戏里没生效？\n"
        "  A: 本作需要「签名绕过前置」才能加载 mod。缺了它，mod 完全不生效，\n"
        "     而且游戏不会报错，只会看起来「mod 没起作用」。\n"
        "     本工具已经自带这个前置（随压缩包一起分发）：\n"
        f"        {GAME.enabler_hint}\n"
        "     界面上那个橙色的「安装前置」按钮，点一下就会自动装到\n"
        f"     {GAME.ue_project}{_sep()}Binaries{_sep()}Win64{_sep()}；\n"
        "     装好后按钮会变成绿色的「前置已就位」。\n"
        "     如果按钮已经是绿色的但仍不生效，点它选「重新装一遍」。"
        if GAME.needs_enabler else
        "Q: mod 装了，但游戏里没生效？\n"
        f"A: {GAME.display_name} 不需要任何前置加载器，把 .pak/.utoc/.ucas 三件套\n"
        f"   一起放进 {_mods_rel} 就会生效。若没效果，先检查三件套是否齐全、\n"
        "   文件名是否与下载时完全一致（三件套改名会掉资源）。"
    )
    _update_faq = (
        "  Q: 游戏更新后 mod 全部失效了？\n"
        "  A: 游戏大版本更新通常会重置签名绕过前置，重新装一次即可。\n"
        "     若仍不生效，请关注后续版本。"
        if GAME.needs_enabler else
        "  Q: 游戏更新后 mod 全部失效了？\n"
        "  A: 大版本更新后 mod 失效通常是因为游戏资源变了，mod 本身需要作者更新。\n"
        "     建议关注 mod 原作者的更新说明，或先卸载再等新版本。"
    )

    # 结尾那三个 replace 是刻意的，不是随手写的：
    #   1) `**` —— 说明.txt 是**纯文本**，Markdown 的强调标记在这里只会显示成
    #      一对多余的星号，用户会以为是乱码。强调改由「」与分段承担。
    #   2) 换行统一成 CRLF —— 目标用户里总有人还在用老版本记事本。
    return f"""\
{_HORIZONTAL}
 {version.APP_NAME_ZH}  v{version.VERSION}   使用说明
 作者：{version.AUTHOR_ID}
 {version.QQ_GROUP_LABEL}
{_HORIZONTAL}

本工具用于管理《{GAME.display_name}》的本地 MOD：导入、预览、安装、卸载、
以及**冲突检测**。完全免费，没有任何付费版本。

本工具为非官方第三方工具，与游戏开发商、发行商无任何从属或授权关系。
工具本身不包含任何游戏资源与第三方 mod；mod 由你自行提供。


{_THIN}
 ① 系统要求
{_THIN}

  * Windows 10 1809 及以上版本，或 Windows 11
  * 仅支持 64 位（x64）
  * 不支持 Windows 7 / 8 / 8.1（Qt6 框架不支持），也不提供 32 位版本
  * 不需要管理员权限，不需要安装，解压即用
  * 除「跳转 B站 页面」外，全部功能不需要联网

如果你在 Windows 7 上双击没有反应，这不是程序坏了，是系统版本不满足要求。


{_THIN}
 ② 被杀毒软件拦截怎么办
{_THIN}

本程序未购买代码签名证书，因此首次运行**可能**被 Windows Defender 或其他
杀毒软件提示/拦截。这是未签名程序的普遍现象，与程序内容无关。

正确的处理方式是「把它加进信任名单」，具体步骤（Windows 安全中心）：

  1. 打开「Windows 安全中心」
  2. 点「病毒和威胁防护」
  3. 在「病毒和威胁防护设置」下点「管理设置」
  4. 往下找到「排除项」，点「添加或删除排除项」
  5. 点「添加排除项」→ 选择「文件夹」
  6. 选中你解压出来的这个文件夹（本说明所在的文件夹）

如果是第三方杀毒软件（火绒 / 360 / 腾讯电脑管家等），
请到该软件的「信任区 / 白名单 / 排除目录」里把本文件夹加进去。

{"!" * 72}
 请**不要**为了运行本工具而关闭杀毒软件，也不要关闭实时防护。
 把程序目录加入白名单就够了 —— 关掉防护只会让你暴露在真实的病毒风险里，
 而本工具完全不需要你这么做。
{"!" * 72}


{_THIN}
 ③ 缺少运行库怎么办（提示找不到 vcruntime140.dll）
{_THIN}

本程序已经自带所需的运行库（vcruntime140.dll 就在程序文件夹里），
正常情况下你不需要另装任何东西。
只有极少数精简过的系统仍然提示缺失时，才按下面的方式安装：

如果启动时提示「找不到 vcruntime140.dll」或类似字样，请到微软官方下载并安装：

    下载地址：{VC_REDIST_URL}
    说明页面：{VC_REDIST_PAGE}

文件名是 vc_redist.x64.exe —— 注意要装的是 **x64** 版本，装完重启一次即可。

请不要从第三方下载站获取这个文件，只从上面这个微软官方地址下载。


{_THIN}
 ④ 官方发布渠道与文件校验
{_THIN}

本工具**仅通过 {channel_text} 发布**。其他渠道下载的版本请勿使用 ——
这类绿色工具最常被人二次打包（替换内容、植入广告或后门）后再传播，
而作者无法为别人的改动负责。

校验方式：

  * 主程序 SHA-256（解压后，对 {version.EXE_NAME}.exe 核对）：
{exe_hash_line}

  * 发布 zip 的 SHA-256：{zip_sha256_hint}
    （zip 的哈希没法写在它自己里面，所以放在发布页公示）

核对方法（Windows 自带，不需要装任何工具）：

  1. 在文件所在的文件夹地址栏里输入 powershell 回车
     （或者按住 Shift 右键空白处 → 「在此处打开 PowerShell 窗口」）
  2. 输入：   Get-FileHash "文件名" -Algorithm SHA256
  3. 把结果和上面公示的值逐位对比

哈希对不上 = 文件被人改过，请立刻删除并到官方渠道重新下载。

  * 官方版本号以本说明顶部与程序内「关于与支持」页显示的为准。
    若你下载的版本号与此处对不上，说明不是官方版本。

官方交流群：QQ 粉丝群 {version.QQ_GROUP_NUMBER}
  * 问题反馈、更新通知都在群里；除此群外，不存在任何其他官方 QQ 群 ——
    有人冒充作者拉群请勿相信。
  * 群内同样坚持免费原则，任何人以作者名义收费都是骗子。


{_THIN}
 ⑤ 首次运行提示
{_THIN}

  * 第一次使用需要**手动指定一次游戏目录**：点顶部「更改目录」，
    选中包含 {GAME.ue_project} 这一层文件夹的目录，也就是游戏根目录。
    工具会自动识别 Steam / Epic 并选对安装位置（本作没有 Game Pass 版本）。
  * 还没安装游戏也可以先运行 —— 可以先把 mod 导入库中，之后再指定路径。
  * 你的设置与索引保存在 %APPDATA%{_sep()}{apppaths.DATA_DIR_NAME}{_sep()}，
    不在程序目录里。所以本工具可以放在桌面、U 盘、网盘同步目录里运行，
    也可以放在 C:\\Program Files 下而不需要管理员权限。


{_THIN}
 附：几个常见问题
{_THIN}

{_enable_faq}
  Q: 两个 mod 修改了同一个东西，怎么办？
  A: 工具会检测出来并在卡片上标红，还会告诉你「谁生效」。冲突是**提示不是拦截**，
     你可以选择只装其中一个，或者接受覆盖结果。

{_update_faq}
  Q: 卸载 mod 会不会误删我自己的文件？
  A: 按设计不会。卸载只删除安装记录里明确记下的那几个文件，并且每个路径都会校验
     是否落在当初安装的那份游戏目录下，不符合就拒绝删除并报出来。
     安装时如果覆盖了你原有的同名文件，卸载会把它还原回去。

  Q: 我的 mod 数据会被上传吗？
  A: 除点击 B站 按钮外，本工具不发起任何联网请求，也不做任何统计上报。
     全部数据都在你自己电脑上。

  Q: 支持作者是必须的吗？
  A: 完全自愿，与任何功能无关，不影响使用。工具本体没有任何付费版本。


{_HORIZONTAL}
 本工具不包含任何游戏资源，也不包含任何 mod。mod 由用户自行提供，
 作者不对其内容的合法性负责。

 本工具内置了用于让 mod 生效的第三方组件（版权归原作者）：
   · UniversalSigBypasser.asi
     作者 rm-NoobInCoding，CC BY-NC 4.0（非商业、保留署名）
     https://github.com/rm-NoobInCoding/UniversalSigBypasser
   · dsound.dll（Ultimate ASI Loader）
     作者 ThirteenAG，MIT 许可
     https://github.com/ThirteenAG/Ultimate-ASI-Loader
 这两个文件只会被复制到游戏的 Win64 目录，程序不会修改其内容。
{_HORIZONTAL}
""" .replace("**", "").replace("\r\n", "\n").replace("\n", "\r\n")


def _sep() -> str:
    """路径分隔符 —— 说明文档是给 Windows 用户看的，统一用反斜杠。"""
    return "\\"


# ---------------------------------------------------------------- 发布物料
def build_release_kit(*, video_title_hint: str = "") -> str:
    """给作者的发布物料：视频简介 + 置顶评论 + FAQ。

    为什么要生成这个：F-11.29 明确说 **B站 稿件发布后严禁反复修改标题/简介**，
    修改 2~3 次以上会直接触发限流。所以简介必须在发布前一次性写对，
    后续链接变更只能走评论区置顶。这里一次性把文案备好。

    文案已经过 F-11.30 的敏感词清单自检（破解 / 永久免费 / 解锁会员 /
    秒杀同类 / 一键解决所有问题）。
    """
    channels = " / ".join(version.RELEASE_CHANNELS)
    return f"""\
# {version.APP_NAME_ZH} 发布物料（发布前一次性写对，之后不要再改稿）

> 依据：需求文档 F-11.27~F-11.31、F-11.32、F-11.33。
> **发布后反复修改标题/简介会触发限流**，所以下面这些内容请复制粘贴一次到位；
> 以后要换下载链接，改**置顶评论**，不要改简介。

---

## 一、视频标题（备选，均不含敏感词）

{chr(10).join(f"{i}. {t}" for i, t in enumerate([
    f"《{GAME.display_name}》MOD 管理器 —— 中文、免安装、前置一键装好",
    f"{GAME.display_name} 装 mod 太麻烦？一个免费的中文管理器",
    f"全中文界面：给{GAME.display_name}做的免费 MOD 管理器",
], 1))}

> ⚠️ 标题里不要出现：破解、永久免费、解锁会员、秒杀同类、一键解决所有问题。

---

## 二、视频简介（**只留一条链接**，其余走评论区）

```
做了个《{GAME.display_name}》的 MOD 管理器，中文、免安装、完全免费。

它能做的事：
· 拖进压缩包/文件夹就能导入 mod，自动出预览图和名称
· 自动识别游戏目录（Steam 库直接认），装到 ProjectPBZ/Content/Paks/~mods
· mod 是 .pak + .utoc + .ucas 三件套，三个一起装、一起卸，文件名保持原样
· 检测签名绕过前置装没装，界面上有一个按钮可以直接装好
· 一键安装、卸载，卸载只删自己装的文件，覆盖过的会自动还原
· 看出两个 mod 有没有改同一个东西，以及最后谁生效

不做什么：不带任何游戏资源，也不带任何 mod，mod 需要你自己准备。
本工具为非官方第三方工具，与游戏开发商、发行商无任何从属或授权关系。

下载：{channels}（见置顶评论）
系统要求：Windows 10 1809+ / 64 位

作者：{version.AUTHOR_ID}    版本：v{version.VERSION}
```

> ⚠️ 简介里**不要用短链**（t.cn / bit.ly 等，B站 明确禁止）；
> 不要堆砌多条链接，只留一条必要的。

---

## 三、置顶评论（后续所有变更都在这里改）

```
【下载与校验】
本工具仅通过 {channels} 发布，其他渠道的版本请勿使用。

zip 的 SHA-256：<发布时填入>
核对方法：文件所在文件夹按住 Shift 右键 → 在此处打开 PowerShell →
Get-FileHash "文件名" -Algorithm SHA256

【常见问题】
① 打不开 / 被杀软拦截：程序未购买代码签名证书，会被 Defender 提示。
   正确做法是把它加进信任名单（Windows 安全中心 → 病毒和威胁防护 →
   管理设置 → 排除项 → 添加文件夹），不要关闭杀毒软件。
② mod 怎么导入：把 zip / 7z / rar 或解压后的文件夹直接拖进窗口。
③ mod 装了但游戏里没生效：需要先装「Mod Enabler」前置，程序会检测并提示。
④ 两个 mod 冲突了：程序会标红并给出「谁生效」。冲突是提示不是拦截，
   你可以只装其中一个。
⑤ 游戏更新后 mod 全失效：大版本更新通常会重置 Mod Enabler，重装一次前置。

【反馈】
统一在评论区说，我会尽量跟进。随游戏大版本更新视情况适配，不保证时效。
不公开个人邮箱 / 微信；交流请进官方 QQ 粉丝群（群号见说明.txt 与程序内关于页）。
```

---

## 四、发布前检查清单

- [ ] 视频里出现版本号与官方渠道，方便用户核对
- [ ] 简介只有一条链接，且是完整链接（非短链）
- [ ] 全片无「破解 / 永久免费 / 解锁会员 / 秒杀同类 / 一键解决所有问题」
- [ ] 未出现承诺性表述（「无广告」「无捆绑」「绝不收费」等，F-11.31）
- [ ] 未出现个人收款码 / 二维码（F-10.13 / R-9 / R-10）
- [ ] zip 的 SHA-256 已公示（置顶评论）
- [ ] 至少两个互相独立的下载渠道，且两边的包 SHA-256 完全一致（F-11.24 / F-11.25）
- [ ] 发布后**不再改稿**；链接变更只改置顶评论
"""
