# -*- coding: utf-8 -*-
"""交付前检查（需求文档 11.5 干净环境清单 + 11.7 交付物静态检查）。

这个脚本不是「跑一遍日志看看」，它产出的是**一份可以留档的检查报告**
（docs/交付前检查.md）。原因见 11.7 的原文要求：
「11.5 的 14 项验证清单全部通过并留有记录（截图或文字留存，供发布前复核）」。

能力边界要说清楚，别让它给人一种「全自动验收通过」的错觉：

  * **能自动验的**：产物形态（onedir / 无 UPX / 版本信息齐全）、
    包内无第三方 pak、说明文档合规、**在真实 exe 上**跑核心功能自检、
    中文路径、只读目录、写权限落点、exe 哈希。
  * **只能人工验的**：DPI 缩放（100/125/150/200%）、1366×768 小屏、
    双显示器、Defender 实时防护与云交付、断网、全新系统缺运行库、
    超长路径。这些需要真的换环境/换机器，脚本只能把它们列成待办。

所以报告里每一项都标了「自动 / 人工」，人工项不会被伪造为「通过」。

用法：
    .venv\\Scripts\\python.exe tools/dist_check.py
    .venv\\Scripts\\python.exe tools/dist_check.py --no-run   # 只做静态检查
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DIST = Path(os.environ.get("WMM_DIST_DIR") or (ROOT / "dist"))
from wmm import version as _version  # noqa: E402

EXE_NAME = _version.EXE_NAME
APP_DIR = DIST / EXE_NAME
EXE = APP_DIR / f"{EXE_NAME}.exe"

PASS, FAIL, WARN, MANUAL = "通过", "失败", "注意", "待人工"
rows: list[tuple[str, str, str, str]] = []   # (检查项, 结论, 说明, 自动/人工)


def rec(item: str, state: str, detail: str, kind: str = "自动") -> None:
    rows.append((item, state, detail, kind))


def sha256_of(path: Path, *, chunk: int = 1 << 20) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- 静态检查
def _rel(path: Path) -> str:
    """相对仓库根的显示路径。

    ⚠️ 必须两边都 resolve()：`WMM_DIST_DIR=dist_new` 时 EXE 是**相对路径**
    （`dist_new/影之刃零mod管理器/...`）而 ROOT 是绝对路径，直接
    `relative_to` 会抛 ValueError（2026-10-12 打包 0.1.22 时踩到）。
    """
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def check_layout() -> None:
    """F-11.3 / F-11.6：必须是 onedir 目录形态，不是单文件 exe。"""
    if not EXE.is_file():
        rec("产物为主程序 exe", FAIL, f"没找到 {EXE}")
        return
    rec("产物为主程序 exe", PASS, _rel(EXE))
    internal = APP_DIR / "_internal"
    # onedir 的判据：exe 旁边有 _internal（PyInstaller 6 的运行时目录）。
    # 单文件 exe 的目录里只有它自己。
    ok = internal.is_dir() and any(internal.iterdir())
    rec(
        "分发形态 = 免安装 onedir（F-11.3）",
        PASS if ok else FAIL,
        f"_internal/ 存在且非空（{sum(1 for _ in internal.rglob('*') if _.is_file())} 个文件）"
        if ok else "找不到 _internal/，可能是 onefile —— 违反 F-11.3",
    )


def check_upx() -> None:
    """F-11.4：严禁启用 UPX。"""
    try:
        import pefile
    except ImportError:
        rec("未启用 UPX（F-11.4）", WARN, "没装 pefile，无法解析 PE 段名，跳过")
        return

    suspects: list[str] = []
    upx_sig = 0
    for exe in sorted(APP_DIR.rglob("*.exe")):
        try:
            pe = pefile.PE(str(exe), fast_load=True)
            names = [s.Name.rstrip(b"\x00").decode("ascii", "replace") for s in pe.sections]
            pe.close()
        except Exception as exc:  # noqa: BLE001
            rec(f"PE 解析 {exe.name}", WARN, repr(exc))
            continue
        if any(n.upper().startswith("UPX") for n in names):
            suspects.append(f"{exe.name}: {names}")
        if b"UPX!" in exe.read_bytes()[:2_000_000]:
            upx_sig += 1
    ok = not suspects and upx_sig == 0
    rec(
        "未启用 UPX（F-11.4）",
        PASS if ok else FAIL,
        "全部 exe/dll 段名无 UPX 特征，文件内也无 UPX! 标记"
        if ok else f"疑似被 UPX 压过：{suspects or '文件内出现 UPX! 标记'}",
    )


def check_version_info() -> None:
    """F-11.5：exe 必须写入版本信息（产品名 / 版本 / 版权 / 公司名）。"""
    try:
        import pefile
    except ImportError:
        rec("exe 版本信息齐全（F-11.5）", WARN, "没装 pefile，跳过")
        return

    try:
        from wmm import version as v

        pe = pefile.PE(str(EXE), fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )
        entries: dict[str, str] = {}
        for fi in getattr(pe, "FileInfo", []) or []:
            for item in fi:
                if getattr(item, "Key", b"") == b"StringFileInfo":
                    for table in item.StringTable:
                        for k, val in table.entries.items():
                            entries[k.decode("ascii", "replace")] = val.decode("utf-8", "replace")
        pe.close()
    except Exception as exc:  # noqa: BLE001
        rec("exe 版本信息齐全（F-11.5）", FAIL, repr(exc))
        return

    need = ("ProductName", "FileVersion", "LegalCopyright", "CompanyName")
    missing = [k for k in need if not entries.get(k)]

    # 版本号比对要做归一化：Windows 版本资源**必须**是四段数字
    # （我们写的是 0.1.0.0），而 wmm/version.py 里是人读的 '0.1.0'。
    # 直接字符串比较会得出「不一致」的假结论 —— 这正是第一版检查器犯的错。
    from tools.build_dist import _version_tuple

    want = _version_tuple(v.VERSION)
    got = {k: _version_tuple(entries.get(k, "")) for k in ("FileVersion", "ProductVersion")}
    mismatch = any(v2 != want for k, v2 in got.items() if entries.get(k))

    detail = "；".join(f"{k}={entries.get(k, '缺')}" for k in need)
    if mismatch:
        want_s = ".".join(str(x) for x in want)
        rec("exe 版本信息齐全（F-11.5）", FAIL,
            f"版本号与 wmm/version.py 不一致（期望 {want_s}，实际 {dict(got)}）")
    else:
        rec("exe 版本信息齐全（F-11.5）", PASS if not missing else FAIL,
            detail if not missing else f"缺 {missing}")


def check_no_pak() -> None:
    """第 1 章红线 + 9 章验收：**安装包里绝不出现任何第三方 pak**。"""
    bad = [
        p for p in APP_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in (".pak", ".ucas", ".utoc")
    ]
    rec(
        "包内不含任何第三方 pak（第 1 章红线）",
        PASS if not bad else FAIL,
        "干净（0 个 .pak/.ucas/.utoc）"
        if not bad else f"发现 {len(bad)} 个：{[str(b.relative_to(APP_DIR)) for b in bad[:5]]}",
    )


def check_readme() -> None:
    """F-11.16 ~ F-11.20 + 11.7：说明.txt 必须存在、UTF-8 无 BOM、五条齐全、
    且**不含「建议关闭杀毒软件」类表述**。"""
    readme = APP_DIR / "说明.txt"
    if not readme.is_file():
        rec("说明.txt 存在（F-11.16）", FAIL, f"没找到 {readme}")
        return
    raw = readme.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    rec(
        "说明.txt 编码 = UTF-8 无 BOM（F-11.4）",
        PASS if not has_bom else FAIL,
        f"{len(raw):,} B" + ("；**含 BOM**，老记事本会显示乱码" if has_bom else "；无 BOM"),
    )
    text = raw.decode("utf-8", "replace")

    sections = {
        "① 系统要求": "① 系统要求" in text,
        "② 杀软拦截处理": "② 被杀毒软件拦截怎么办" in text,
        "③ 缺运行库处理": "③ 缺少运行库怎么办" in text,
        "④ 发布渠道与校验": "④ 官方发布渠道与文件校验" in text,
        "⑤ 首次运行提示": "⑤ 首次运行提示" in text,
    }
    missing = [k for k, ok in sections.items() if not ok]
    rec(
        "说明.txt 覆盖五条必需内容（F-11.16~F-11.20）",
        PASS if not missing else FAIL,
        "五条齐全" if not missing else f"缺 {missing}",
    )

    banned = ("关闭杀毒", "关闭杀软", "关闭实时防护", "退出杀毒", "暂时关闭")
    hits = [w for w in banned if w in text]
    # 注意：正文里确实会出现「不要为了运行本工具而关闭杀毒软件」——
    # 那是**正确引导**，所以这里要区分「建议关」和「禁止关」。
    wrong: list[str] = []
    for word in hits:
        for line in text.splitlines():
            if word in line and "不要" not in line and "严禁" not in line and "无需" not in line:
                wrong.append(line.strip())
    rec(
        "说明.txt 未引导用户关闭杀毒软件（11.7 验收）",
        PASS if not wrong else FAIL,
        "只有『不要关闭杀毒软件』类的正确引导" if not wrong else f"可疑表述：{wrong[:2]}",
    )

    rec(
        "说明.txt 未出现敏感词「永久免费」（F-11.30）",
        PASS if "永久免费" not in text else FAIL,
        "统一使用「完全免费」",
    )


def check_icon() -> None:
    icon = APP_DIR / "_internal" / "assets" / "icon.ico"
    rec(
        "随包图标存在且为自制（F-11.5 / F-11.22）",
        PASS if icon.is_file() else FAIL,
        f"{icon.relative_to(APP_DIR)}（{icon.stat().st_size:,} B）" if icon.is_file() else "找不到",
    )


def check_zip() -> None:
    """F-11.8 / F-11.25：zip 的 SHA-256 必须能算出来并公示。"""
    zips = sorted(DIST.glob("*.zip"))
    if not zips:
        rec("发布 zip 存在（F-11.6）", FAIL, "dist/ 下没有 zip")
        return
    z = zips[-1]
    rec("发布 zip 存在（F-11.6）", PASS,
        f"{z.name}（{z.stat().st_size / 1048576:.1f} MB）")
    rec("发布 zip 的 SHA-256（F-11.8）", PASS, f"{sha256_of(z)}   ← 公示这个值")


# ---------------------------------------------------------------- 真机自检
def run_binary_selftest(workdir: Path, label: str, *, program_dir: Path | None = None) -> bool:
    """在**真实 exe** 上跑一次自检，返回是否全通过。

    `program_dir` 用于「把程序放到别的位置再跑」的场景（中文路径 / 只读目录）。
    `WMM_DATA_DIR` 指向临时目录，绝不碰真实的 %APPDATA%。
    """
    exe = (program_dir / EXE.name) if program_dir else EXE
    if not exe.is_file():
        rec(f"{label}：运行产物自检", FAIL, f"没找到 {exe}")
        return False

    out = workdir / "selftest.txt"
    data_dir = workdir / "appdata"
    env = dict(os.environ)
    env["WMM_DATA_DIR"] = str(data_dir)
    env["QT_QPA_PLATFORM"] = "offscreen"

    # 跑之前先记录程序目录的文件清单 —— 用来证明「没有往程序目录写东西」
    before = _snapshot(program_dir or APP_DIR)

    try:
        proc = subprocess.run(
            [str(exe), "--selftest", str(out)],
            env=env, cwd=str(exe.parent),
            # R-22：外部程序输出不依赖系统默认编码
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=180,
        )
        code = proc.returncode
    except subprocess.TimeoutExpired:
        rec(f"{label}：运行产物自检", FAIL, "超时 180s（可能是杀软扫描或缺少运行库）")
        return False
    except Exception as exc:  # noqa: BLE001
        rec(f"{label}：运行产物自检", FAIL, f"无法启动：{exc!r}")
        return False

    if not out.is_file():
        rec(f"{label}：运行产物自检", FAIL,
            f"exe 退出码 {code}，但没有产出结果文件（很可能是缺 MSVC 运行库 vcruntime140.dll）")
        return False

    text = out.read_text(encoding="utf-8", errors="replace")
    fails = [ln for ln in text.splitlines() if ln.startswith("[FAIL]")]
    rec(
        f"{label}：运行产物自检",
        PASS if (code == 0 and not fails) else FAIL,
        f"退出码 {code}；{len(fails)} 项失败" + (f"：{fails[:3]}" if fails else ""),
    )

    after = _snapshot(program_dir or APP_DIR)
    new_files = sorted(after - before)
    rec(
        f"{label}：程序目录保持只读（第 4 章硬约束）",
        PASS if not new_files else FAIL,
        "跑完前后程序目录文件清单完全一致" if not new_files
        else f"程序目录里多出了 {len(new_files)} 个文件：{new_files[:5]}",
    )

    # 数据目录确实被用了（证明写到了 %APPDATA% 那一侧）
    made = data_dir.is_dir()
    rec(
        f"{label}：数据写到数据目录",
        PASS if made else FAIL,
        f"{data_dir}" if made else "临时数据目录没有被创建",
    )
    return code == 0 and not fails


def _snapshot(root: Path) -> set[str]:
    try:
        return {
            str(p.relative_to(root)) for p in root.rglob("*")
            if p.is_file() and "_internal" not in p.parts
        }
    except Exception:  # noqa: BLE001
        return set()


def check_chinese_path(workdir: Path) -> None:
    """11.5 第 4 项：**中文用户名 + 中文安装路径**下必须能正常跑。

    做法是真的把整个产物复制到一个含中文的目录再运行 ——
    这类问题（R-21）只在 copy 那一刻才暴露，改代码看不出来。
    """
    cn = workdir / "张三的下载" / "明末MOD管理器 测试版"
    try:
        cn.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(APP_DIR, cn, dirs_exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        rec("中文安装路径可运行（11.5 #4）", FAIL, f"复制失败：{exc!r}")
        return
    ok = run_binary_selftest(workdir / "cn-run", "中文路径", program_dir=cn)
    rec(
        "中文安装路径可运行（11.5 #4 / R-21）",
        PASS if ok else FAIL,
        "在含中文的目录（用户名+目录名都是中文）下自检通过"
        if ok else "中文路径下自检有失败项",
    )


def check_readonly_dir(workdir: Path) -> None:
    """11.5 第 3 项：放在**只读位置**也要能启动使用。

    这里只做「目录属性置只读」这一种模拟（Windows 的只读目录属性对
    新建文件其实拦不住，真正严格的是 ACL 只读）。所以：
      * 程序目录是否被写入 —— 这条上面已经单独验了，且是硬结论；
      * 这里的结论只作参考，标成 WARN 不标 FAIL，避免给人虚假的安心。
    """
    ro = workdir / "ro"
    try:
        if ro.exists():
            # 上一次跑可能加了只读属性，先摘掉，否则 copytree 覆盖会失败
            subprocess.run(["attrib", "-R", str(ro), "/S", "/D"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.copytree(APP_DIR, ro, dirs_exist_ok=True)
        subprocess.run(["attrib", "+R", str(ro), "/S", "/D"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        rec("只读目录可运行（11.5 #3）", WARN, f"准备失败：{exc!r}")
        return
    ok = run_binary_selftest(workdir / "ro-run", "只读目录", program_dir=ro)
    rec(
        "只读目录可运行（11.5 #3）",
        PASS if ok else WARN,
        "程序目录置只读后自检仍通过"
        if ok else "自检有失败项（注意：Windows 目录只读属性拦截力有限，建议再用非管理员账户复核）",
    )


# ---------------------------------------------------------------- 人工项
MANUAL_ITEMS = [
    ("1", "非管理员账户首次启动", "程序正常打开，不弹 UAC、不报权限错误"),
    ("6", "显示缩放 100%", "界面无错位、无控件被裁切"),
    ("7", "显示缩放 150% / 200%", "按钮不被放大出界、文字不重叠"),
    ("8", "1366×768 低分辨率", "窗口不被裁切，关键按钮始终可见可点"),
    ("9", "双显示器（不同缩放）", "窗口在两屏间拖动不错位"),
    ("10", "Defender 实时防护 + 云交付保护全开", "exe 未被删除/隔离，能正常运行"),
    ("11", "断网运行", "除 B站跳转外全部功能可用；跳转失败有纯文本回退"),
    ("12", "全新系统（可能缺 MSVC 运行库）", "能启动；若缺库，提示清晰而非崩溃"),
    ("13", "超长路径（总长 > 260 字符）", "复制成功，或给出明确提示而非静默失败"),
    ("14", "未安装游戏 / 未装 Steam", "能启动、能导入 mod、能手动指定路径"),
]


# ---------------------------------------------------------------- 报告
def write_report(path: Path) -> int:
    from wmm import version

    n_fail = sum(1 for _i, s, _d, _k in rows if s == FAIL)
    n_warn = sum(1 for _i, s, _d, _k in rows if s == WARN)

    lines = [
        f"# {version.APP_NAME_ZH} v{version.VERSION} 交付前检查报告",
        "",
        f"- 生成时间：{__import__('time').strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 产物目录：`{APP_DIR}`",
        f"- 自动检查：{len(rows)} 项，失败 {n_fail} 项，注意 {n_warn} 项",
        "",
        "> 本报告由 `tools/dist_check.py` 生成。需求文档 11.7 要求"
        "「11.5 的 14 项验证清单全部通过并留有记录」，这份文件就是那份记录 —— "
        "**自动项可直接采信，人工项必须真的换环境做完再打勾**。",
        "",
        "## 一、交付物静态检查与产物自检（自动）",
        "",
        "| 检查项 | 结论 | 说明 |",
        "| --- | --- | --- |",
    ]
    for item, state, detail, _kind in rows:
        lines.append(f"| {item} | {state} | {detail} |")

    lines += [
        "",
        "## 二、11.5 干净环境清单里只能人工做的部分",
        "",
        "> 前提认知（R-20）：**开发机上跑通 = 什么都没证明**。"
        "下面每一项都必须在没有装过 Python、没有装过本工具、"
        "非管理员账户的环境里做一遍。推荐顺序：新建本地用户账户 → "
        "Windows 沙盒（仅 Pro/Enterprise/Education）→ 虚拟机 → 另一台真实电脑。",
        "",
        "| # | 验证项 | 通过判据 | 结论 | 备注 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for num, item, judge in MANUAL_ITEMS:
        lines.append(f"| {num} | {item} | {judge} | ☐ 待验 |  |")

    lines += [
        "",
        "## 三、发布前还要做的事（非技术项，见 11.8）",
        "",
        "- [x] ~~至少两个互相独立的下载渠道~~ —— **本次不适用**：作者 2026-10-13 决定"
        "只走 B站 单渠道发布（见 docs/发布物料.md 置顶评论）。若日后增加网盘等第二渠道，"
        "需同步改 `version.RELEASE_CHANNELS`，并保证两边包的 SHA-256 完全一致（F-11.24 / F-11.25）",
        "- [ ] 发布页公示 zip 的 **SHA-256**（见上方自动检查里的哈希值）",
        "- [ ] 发布页与「关于与支持」页都写明"
        "「非官方第三方工具、与开发商/发行商无从属关系」（F-11.21）",
        "- [ ] B站 稿件：简介只留一条**完整**链接（禁短链）、发布后不再改稿（F-11.27 ~ F-11.29）",
        "- [ ] 稿件文案自检：无「破解 / 永久免费 / 解锁会员 / 秒杀同类 / 一键解决所有问题」（F-11.30）",
        "- [ ] 未出现承诺性表述（「无广告」「无捆绑」「绝不收费」，F-11.31）",
        "- [ ] 未出现个人收款码 / 二维码（F-10.13 / R-9 / R-10）",
        "- [ ] FAQ 覆盖五条（F-11.33）；未公开个人邮箱 / 微信 / QQ（F-11.32）",
        "- [ ] 向微软提交误报申诉（若被 Defender 拦截时）",
        "",
        "## 四、本次检查得出的哈希（用于公示）",
        "",
    ]
    if EXE.is_file():
        lines.append(f"- 主程序 `{EXE_NAME}.exe`：`{sha256_of(EXE)}`")
    for z in sorted(DIST.glob("*.zip")):
        lines.append(f"- 发布包 `{z.name}`：`{sha256_of(z)}`")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return n_fail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-run", action="store_true", help="跳过运行产物自检（只做静态检查）")
    parser.add_argument("--report", default=str(ROOT / "docs" / "交付前检查.md"))
    args = parser.parse_args()

    print(f"交付前检查 —— {APP_DIR}")
    print("=" * 72)

    check_layout()
    check_upx()
    check_version_info()
    check_no_pak()
    check_readme()
    check_icon()
    check_zip()

    if not args.no_run:
        # ⚠️ 刻意用**固定**的工作目录并**不删它**：
        # 这里有三次 200+ 文件的整目录复制，用临时目录跑完再 rmtree 的话，
        # 一次就是 600+ 个文件的批量删除 —— 任何批量删除守卫都会拦下来，
        # 而且删了也没有任何好处（这些副本下一轮会被原地覆盖，不会累积）。
        workdir = ROOT / "build" / "distcheck"
        workdir.mkdir(parents=True, exist_ok=True)
        run_binary_selftest(workdir / "base", "默认位置")
        check_chinese_path(workdir)
        check_readonly_dir(workdir)

    for item, state, detail, _kind in rows:
        mark = {PASS: "✓", FAIL: "✗", WARN: "!", MANUAL: "?"}.get(state, "?")
        print(f"{mark} {item}")
        print(f"    {state}：{detail}")
    n_fail = write_report(Path(args.report))
    print("=" * 72)
    print(f"共 {len(rows)} 项，失败 {n_fail} 项；报告已写入 {args.report}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
