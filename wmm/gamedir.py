# -*- coding: utf-8 -*-
"""游戏目录定位与平台识别（需求文档 2.3 / 2.4 / 3.3）。

⚠️ 这一块的**唯一可靠事实**：来源互相矛盾，不要靠自动猜路径。
  实测到的三套打架写法：
    - Prima Games / 游侠 / 3DM：<游戏>\\Project_Plague\\Content\\Paks\\~mods\\
    - GitHub 官方 Modding Guide：<游戏>\\SB\\Content\\Paks\\~mods\\（写的是 SB）
    - Game Pass 两个来源分别写 C:\\XboxGames\\Wuchang PCGP\\Content\\Project_Plague\\Content\\Paks\\
      与 C:\\XboxGames\\WuchangFallenFeathers\\Content\\Paks\\

  所以设计决策（文档 2.3）：
    **首次启动让用户手动指一次游戏目录，工具校验并记住**；
    自动探测只作为「帮你猜一个默认值」的辅助，绝不作为唯一手段。

  本模块因此把「探测」拆成两件事：
    1. 给定一个用户指定的目录 -> 逐条候选规则去试，命中就报告**证据**和**置信度**；
    2. 全盘自动探测 -> 只在 Steam 侧做有限尝试（注册表 + libraryfolders.vdf），
       失败一律返回空列表，绝不抛异常、绝不阻塞启动。
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .games import ACTIVE as GAME

# ---------------------------------------------------------------- 平台常量
PLATFORM_STEAM = "steam"
PLATFORM_EPIC = "epic"
PLATFORM_GAMEPASS = "gamepass"
PLATFORM_UNKNOWN = "unknown"

PLATFORM_LABELS = {
    PLATFORM_STEAM: "Steam",
    PLATFORM_EPIC: "Epic",
    PLATFORM_GAMEPASS: "Game Pass",
    PLATFORM_UNKNOWN: "未识别",
}

#: UE 工程名，决定全部目录名（文档 2.1）。值来自 wmm/games/ 的游戏配置。
UE_PROJECT = GAME.ue_project

#: 目标 pak 目录的候选相对路径，按「命中概率」从高到低排。
#: 每条都是相对于「用户指定的目录」。
PAKS_CANDIDATES: Sequence[str] = (
    f"{UE_PROJECT}/Content/Paks",                 # Steam/Epic 标准（实测存在）
    "Content/Paks",                               # Game Pass 来源 A
    f"Content/{UE_PROJECT}/Content/Paks",         # Game Pass 来源 B
    f"SB/Content/Paks",                           # 官方 guide 口径（存疑，保留但降权）
    "Paks",                                       # 用户已指到 Content 之下
)

#: 用户可能「指得太深」——直接指到 mod 存放目录的情况（F-3.5 明确要求支持）
MOD_SUBDIR_NAMES = GAME.mods_folder_aliases

#: 游戏主 exe 名（用于校验判据之二：能找到主 exe）
GAME_EXE_CANDIDATES = GAME.game_exe_names

#: 已知 Steam AppID —— **仅作为佐证证据记录**，不用作筛选条件（文档 3.3 / F-3.3）
KNOWN_STEAM_APPID = GAME.steam_appid

#: 平台识别用的路径特征
_STEAM_HINTS = ("steamapps", "steamapps/common")
_EPIC_HINTS = ("epic games", "epicgames")
_GAMEPASS_HINTS = ("xboxgames", "windowsapps", "wingdk")


@dataclass
class ProbeResult:
    """一次游戏目录校验的完整结果。UI 只展示 evidence / warnings，不展示置信度数值。"""

    #: 用户给的原始路径
    input_path: Path
    #: 校验通过时的游戏根目录（= Paks 之上的那个目录）
    game_root: Optional[Path] = None
    #: 定位到的 pak 目录
    paks_dir: Optional[Path] = None
    #: 命中的候选规则（排障用）
    matched_rule: str = ""
    #: 平台
    platform: str = PLATFORM_UNKNOWN
    #: 置信度：high / medium / low / none
    confidence: str = "none"
    #: 支持判断的证据（人类可读）
    evidence: List[str] = field(default_factory=list)
    #: 提示与告警
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """能否定位到 Content\\Paks 层级（需求文档 F-3.2 的校验判据）。"""
        return self.paks_dir is not None

    @property
    def platform_label(self) -> str:
        return PLATFORM_LABELS.get(self.platform, PLATFORM_LABELS[PLATFORM_UNKNOWN])


def _norm_key(p: Path) -> str:
    try:
        s = str(Path(p).resolve())
    except OSError:
        s = str(Path(p).absolute())
    return s.rstrip("\\/").lower()


def same_path(a: Path, b: Path) -> bool:
    """Windows 语义下的路径相等比较（大小写不敏感）。"""
    return _norm_key(a) == _norm_key(b)


# ---------------------------------------------------------------- 校验
def _climb_out_of_mods(path: Path) -> tuple[Path, List[str]]:
    """用户直接指到了 ~mods / LogicMods 时的回退：往上爬到 Paks 目录。"""
    notes: List[str] = []
    cur = path
    for _ in range(2):
        if cur.name in MOD_SUBDIR_NAMES:
            notes.append(f"你指定的是 mod 存放目录（{cur.name}），已自动上溯到它的上级。")
            cur = cur.parent
        else:
            break
    return cur, notes


def detect_platform(path: Path, *, paks_dir: Optional[Path] = None) -> tuple[str, str, List[str]]:
    """按路径特征判断平台。返回 (platform, confidence, evidence)。

    **判据是路径特征，不是猜。** 拿不准就返回 unknown —— 交给用户确认，
    而不是自作主张选一个平台（选错平台会写错目录、用错命名规则）。
    """
    probe = _norm_key(path)
    evidence: List[str] = []

    hits = {
        PLATFORM_STEAM: [h for h in _STEAM_HINTS if h in probe],
        PLATFORM_EPIC: [h for h in _EPIC_HINTS if h in probe],
        PLATFORM_GAMEPASS: [h for h in _GAMEPASS_HINTS if h in probe],
    }

    # Game Pass 的特征优先级最高：WinGDK 命名与 XboxGames 目录是它的独有标志
    if hits[PLATFORM_GAMEPASS]:
        evidence.append(f"路径特征：{hits[PLATFORM_GAMEPASS][0]}")
        return PLATFORM_GAMEPASS, "high", evidence
    if hits[PLATFORM_STEAM]:
        evidence.append(f"路径特征：{hits[PLATFORM_STEAM][0]}")
        return PLATFORM_STEAM, "high", evidence
    if hits[PLATFORM_EPIC]:
        evidence.append(f"路径特征：{hits[PLATFORM_EPIC][0]}")
        return PLATFORM_EPIC, "high", evidence

    # 次级判据：同目录下有没有 Steam 的 appmanifest / steamapps
    root = paks_dir.parent if paks_dir else path
    for up in _walk_up(root, 4):
        if (up / "steamapps").is_dir():
            evidence.append(f"上级目录存在 steamapps：{up}")
            return PLATFORM_STEAM, "medium", evidence

    evidence.append("路径中未发现 Steam / Epic / Game Pass 的标志性目录名。")
    return PLATFORM_UNKNOWN, "low", evidence


def _walk_up(start: Path, levels: int) -> Iterable[Path]:
    cur = start
    for _ in range(levels):
        yield cur
        if cur.parent == cur:
            return
        cur = cur.parent


def validate_game_dir(raw: str | os.PathLike[str]) -> ProbeResult:
    """校验用户指定的游戏目录，并定位 pak 目录。

    命中即返回；一条都不命中时，仍把最像的那个候选记进 warnings，方便排障。
    """
    path = Path(raw).expanduser()
    res = ProbeResult(input_path=path)

    if not path.exists():
        res.warnings.append(f"路径不存在：{path}")
        return res
    if not path.is_dir():
        res.warnings.append(f"这不是一个文件夹：{path}")
        return res

    base, notes = _climb_out_of_mods(path)
    res.warnings.extend(notes)

    tried: List[str] = []
    for rel in PAKS_CANDIDATES:
        candidate = base / rel
        tried.append(rel)
        if candidate.is_dir():
            res.paks_dir = candidate
            res.matched_rule = rel
            # 「游戏根目录」= 用户指定的那个目录本身（Paks 在它下面的某个相对位置）。
            # 记住 base 而不是爬上去，是为了卸载/回滚时能原样还原用户的选择。
            res.game_root = base
            res.evidence.append(f"找到 pak 目录：{candidate}（规则 {rel}）")
            break

    if res.paks_dir is None:
        # 用户指到了 Paks 本身（例如 ...\Content\Paks）
        if base.name.lower() == "paks":
            res.paks_dir = base
            res.matched_rule = "<用户直接指定到 Paks>"
            res.evidence.append(f"你直接指定的就是 pak 目录：{base}")
        else:
            res.warnings.append(
                "在这个目录下依次找过以下位置，都没找到 pak 目录："
                + "、".join(tried)
                + "。可点「手动浏览」直接指到游戏的 Content\\Paks 目录，"
                "或指到 ~mods 目录。"
            )
            return res

    # 主 exe 佐证
    for exe_rel in GAME_EXE_CANDIDATES:
        if (base / exe_rel).is_file():
            res.evidence.append(f"找到游戏主程序：{exe_rel}")
            break

    platform, conf, ev = detect_platform(base, paks_dir=res.paks_dir)
    res.platform = platform
    res.confidence = conf
    res.evidence.extend(ev)
    if platform == PLATFORM_UNKNOWN:
        res.warnings.append(
            "没能自动识别平台，请在界面里手动选一次（Steam / Epic / Game Pass）。"
            "选错会导致装到错误目录、用错命名规则。"
        )
    if platform == PLATFORM_GAMEPASS:
        res.warnings.append(
            "Game Pass 版的目标目录不可建子目录、文件名必须精确为 "
            f"{UE_PROJECT}-WinGDK_4_P.pak，命名不符则游戏不加载。"
        )
    return res


# ---------------------------------------------------------------- 安装目标
def resolve_mods_dir(platform: str, paks_dir: Path) -> Path:
    """按平台算出 mod 文件应该放到哪里（需求文档 2.4 硬约束）。

    - Steam / Epic：<Paks>\\~mods\\（推荐）或 <Paks>\\LogicMods\\
    - Game Pass   ：**直接放主 Paks 目录，禁止建子目录**
    """
    if platform == PLATFORM_GAMEPASS:
        return Path(paks_dir)
    return Path(paks_dir) / GAME.mods_folder


def required_pak_name(platform: str, mod_name: str, chunk_id: int | None = None) -> str:
    """按平台规则算目标文件名（需求文档 F-4.2 / F-4.3）。

    - Steam / Epic ：[任意名称]_P.pak —— 命名灵活，但必须以 _P 结尾
    - Game Pass    ：必须精确为 Project_Plague-WinGDK_<N>_P.pak
    """
    if platform == PLATFORM_GAMEPASS:
        n = 4 if chunk_id is None else int(chunk_id)
        return f"{UE_PROJECT}-WinGDK_{n}_P.pak"
    return f"{safe_mod_stem(mod_name)}_P.pak"


#: Windows 不允许出现在文件名里的字符
_INVALID_FILENAME_CHARS = '<>:"/\\|?*'

#: 名称清洗要剥掉的前后缀（需求文档 3.7）
_STRIP_SUFFIXES = ("_P", "_p", "_WindowsNoEditor", "-WindowsNoEditor", "-Windows", "_Windows")

#: 要整词剔除的标记。剔除后若一字不剩，说明这个文件名里**没有任何信息**
#: （实测大量存在，如 pakchunk1200-Windows.pak、Project_Plague-WinGDK_9001_P.pak），
#: 这时必须回落到名称推导链的下一级（压缩包内目录名 / readme 首行），
#: 而不是硬编一个占位名糊弄过去 —— 见需求文档 3.7 的优先级链。
_STRIP_TOKENS = (
    "WindowsNoEditor",
    "WinGDK",
    "Windows",
    GAME.ue_project,
    GAME.ue_project.replace("_", ""),
    "pakchunk",
)

#: 兜底展示名（文档 3.7 第 4 级：按导入顺序编号，编号在阶段 2 由导入器补）
FALLBACK_MOD_NAME = "未命名mod"


def _looks_like_extension(ext: str) -> bool:
    """这个后缀像不像"真正的文件扩展名"。

    ⚠️ 为什么需要它：`os.path.splitext` 只看最后一个点，于是
        `无摔伤补丁 v1.2` 会被拆成 `("无摔伤补丁 v1", ".2")`，
        把版本号当扩展名扔掉 —— 实测真实 readme 首行里 `v1.2` / `v2.0`
        这类写法遍地都是（样例 `无摔伤补丁 v1.2` 就踩了）。
    判据：**纯数字的后缀不是扩展名**（`.2` / `.0`），长度也要在合理范围内。
    """
    if not ext.startswith(".") or len(ext) < 2 or len(ext) > 7:
        return False
    body = ext[1:]
    if body.isdigit():
        return False
    return all(ch.isalnum() or ch in "_-" for ch in body)


def sanitize_mod_name(name: str, *, strip_extension: bool = True) -> str:
    """把 mod 文件名/目录名清洗成可用名。**可能返回空字符串**（表示此名无信息量）。

    规则来自需求文档 3.7：去掉 _P、pakchunk 前缀、WindowsNoEditor、-vX.Y 版本号，_ 转空格。
    实测补充：真实下载物里 24% 不满足命名规则，文件名还出现过括号
    （如 `BaiNude_Flat_Clean_p(under.pak`），所以非法字符必须一律剔除。

    参数 `strip_extension`
        调用方已经自己处理过扩展名时（如名称推导链拿到的都是 `Path.stem` 或
        readme 首行）传 False，避免把 `v1.2` 里的 `.2` 当成扩展名误删。

    返回值语义："" = 「这个名字里没有可用的 mod 名」，
    调用方应继续走名称推导链（目录名 -> readme 首行 -> FALLBACK_MOD_NAME），
    不要把它当成清洗失败。
    """
    s = str(name).strip()
    stem, ext = os.path.splitext(s)
    if strip_extension and _looks_like_extension(ext):
        s = stem

    # 剥掉 pakchunk1234-Windows 这类前缀
    s = re.sub(r"^pakchunk\d+[-_]?", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\d{3,}[-_]", "", s)
    for suffix in _STRIP_SUFFIXES:
        if s.lower().endswith(suffix.lower()):
            s = s[: -len(suffix)]
    for token in _STRIP_TOKENS:
        s = re.sub(re.escape(token), "", s, flags=re.IGNORECASE)
    # 版本号 -v1.2 / _v1.2，以及 _510002 这类纯编号尾巴
    s = re.sub(r"[-_]v?\d+(\.\d+)*$", "", s)
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip(" -_.()")
    s = "".join(ch for ch in s if ch not in _INVALID_FILENAME_CHARS and ord(ch) >= 32)
    return s.strip()


def safe_mod_stem(name: str, fallback: str = FALLBACK_MOD_NAME) -> str:
    """必定返回非空名称，供落盘/落库使用。"""
    return sanitize_mod_name(name) or fallback


# ---------------------------------------------------------------- 自动探测
def autodetect_steam_libraries() -> List[Path]:
    """尽力而为地找出 Steam 库目录。失败一律返回空列表，绝不抛异常。

    判据（需求文档 F-3.3）：
      - 不要硬编码 AppID，扫 appmanifest_*.acf 的 installdir 字段更稳
      - 读 HKCU\\Software\\Valve\\Steam\\SteamPath
      - 解析 libraryfolders.vdf 支持多库多盘
    """
    if sys.platform != "win32":
        return []

    roots: List[Path] = []
    try:
        import winreg  # noqa: PLC0415 - 只在 Windows 上导入

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            for value_name in ("SteamPath", "SteamExe"):
                try:
                    val, _ = winreg.QueryValueEx(key, value_name)
                except OSError:
                    continue
                p = Path(val)
                if p.name.lower().endswith(".exe"):
                    p = p.parent
                roots.append(p)
                break
    except Exception:  # noqa: BLE001 - 探测失败不是错误，只是没猜到
        pass

    # 常见默认位置兜底
    for guess in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        p = Path(guess)
        if p.is_dir():
            roots.append(p)

    libraries: List[Path] = []
    seen: set[str] = set()
    for root in roots:
        candidates = [root]
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if not vdf.is_file():
            vdf = root / "config" / "libraryfolders.vdf"
        if vdf.is_file():
            try:
                text = vdf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                candidates.append(Path(m.group(1).replace("\\\\", "\\")))
        for c in candidates:
            key = _norm_key(c)
            if key not in seen and c.is_dir():
                seen.add(key)
                libraries.append(c)
    return libraries


@dataclass
class AutodetectHit:
    path: Path
    installdir: str
    appid: str
    source: str
    #: 人类可读的来源说明（Steam 库清单 / Epic 清单 / 卸载登记表 / 盘内扫描…）
    label: str = ""


#: 命中游戏目录的**名称特征**。
#: ⚠️ 刻意不放进单独的 "fallen"：那个词会撞上《星球大战 绝地：陨落的武士团》
#: 之类的一大堆游戏。带上 "feathers" 或中文名才够独特。
_NAME_TOKENS: Tuple[str, ...] = GAME.identify_words


def name_matches(text: str) -> bool:
    """目录名 / 注册表显示名里有没有明末的特征词（大小写、空格无关）。"""
    low = str(text or "").lower().replace(" ", "")
    return any(tok.replace(" ", "") in low for tok in _NAME_TOKENS)


def is_game_root(path: Path | str) -> bool:
    """这个目录里能不能找到 Content\\Paks 层级（= 游戏根目录的定义）。"""
    p = Path(path)
    try:
        if not p.is_dir():
            return False
    except OSError:
        return False
    if p.name.lower() == "paks":
        return True
    for rel in PAKS_CANDIDATES:
        try:
            if (p / rel).is_dir():
                return True
        except OSError:
            continue
    return False


def _safe_iterdir(path: Path, limit: int = 400) -> List[Path]:
    """列目录但不抛异常、不返回隐藏项、限个数（扫描时可能撞上奇怪的东西）。"""
    out: List[Path] = []
    try:
        for child in path.iterdir():
            if len(out) >= limit:
                break
            try:
                if child.name in (".", "..") or child.name.startswith("$"):
                    continue
                out.append(child)
            except OSError:
                continue
    except OSError:
        pass
    return out


def _fixed_drives() -> List[Path]:
    """所有**固定**磁盘的根（C:\\ D:\\ E:\\…）。可移动/网络/光驱一并排除。

    ⚠️ 只取固定盘：网络盘和光驱上做目录遍历可能卡住几十秒，
    而自动检测是一点按钮就该出结果的。
    """
    if sys.platform != "win32":
        return [Path(os.path.abspath(os.sep))]
    drives: List[Path] = []
    try:
        import ctypes  # noqa: PLC0415

        kernel32 = ctypes.windll.kernel32
        bitmask = kernel32.GetLogicalDrives()
        for i in range(26):
            if not (bitmask & (1 << i)):
                continue
            root = f"{chr(ord('A') + i)}:\\"
            try:
                dtype = kernel32.GetDriveTypeW(root)
            except Exception:  # noqa: BLE001
                continue
            if dtype == 3:            # 3 = DRIVE_FIXED
                drives.append(Path(root))
    except Exception:  # noqa: BLE001 - 探测失败不是错误
        pass
    return drives


# ---------------------------------------------------------------- 各来源
def _hits_from_steam() -> List[AutodetectHit]:
    """Steam：扫每个库里 steamapps\\appmanifest_*.acf 的 installdir。"""
    hits: List[AutodetectHit] = []
    for lib in autodetect_steam_libraries():
        steamapps = lib / "steamapps"
        if not steamapps.is_dir():
            continue
        try:
            manifests = list(steamapps.glob("appmanifest_*.acf"))
        except OSError:
            continue
        for acf in manifests:
            try:
                text = acf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = re.search(r'"installdir"\s+"([^"]+)"', text)
            if not m:
                continue
            installdir = m.group(1)
            if not name_matches(installdir):
                continue
            appid_m = re.search(r'"appid"\s+"(\d+)"', text)
            appid = appid_m.group(1) if appid_m else ""
            hits.append(
                AutodetectHit(
                    path=steamapps / "common" / installdir,
                    installdir=installdir,
                    appid=appid,
                    source=str(acf),
                    label=f"Steam（{acf.name}，AppID {appid or '?'}）",
                )
            )
    return hits


def _epic_manifest_dirs() -> List[Path]:
    """Epic 的安装清单目录（每个装过的 Epic 游戏一个 .item JSON）。"""
    base = os.environ.get("ProgramData") or r"C:\ProgramData"
    d = Path(base) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    return [d] if d.is_dir() else []


def _hits_from_epic() -> List[AutodetectHit]:
    hits: List[AutodetectHit] = []
    for d in _epic_manifest_dirs():
        for item in _safe_iterdir(d):
            if item.suffix.lower() != ".item":
                continue
            try:
                data = json.loads(item.read_text(encoding="utf-8", errors="replace"))
            except Exception:  # noqa: BLE001 - 单个清单坏了不影响其他
                continue
            if not isinstance(data, dict):
                continue
            display = str(data.get("DisplayName") or data.get("AppName") or "")
            loc = str(data.get("InstallLocation") or "")
            if not loc:
                continue
            # 名字对不上时，也接受「目录里就是游戏本体」这一条实物证据
            if not name_matches(display) and not name_matches(Path(loc).name):
                continue
            hits.append(
                AutodetectHit(
                    path=Path(loc),
                    installdir=Path(loc).name,
                    appid=str(data.get("AppName") or ""),
                    source=str(item),
                    label=f"Epic（{display or item.name}）",
                )
            )
    return hits


#: WeGame / 各平台常见的游戏库目录（相对磁盘根），按常见度排
_COMMON_LIB_DIRS: Tuple[str, ...] = (
    "SteamLibrary/steamapps/common",
    "Steam/steamapps/common",
    "WeGameApps",
    "WeGame/游戏",
    "WeGame/apps",
    "Program Files (x86)/WeGame/apps",
    "Program Files/WeGame/apps",
    "Epic Games",
    "Games",
    "Game",
    "游戏",
)


def _hits_from_registry() -> List[AutodetectHit]:
    """Windows「卸载」登记表 —— 覆盖面最广的一路。

    Steam / Epic / WeGame / 独立安装包**都会**在这里留一条（DisplayName +
    InstallLocation）。Xbox/Game Pass 是唯一不在这里的（UWP 走另一套键）。
    """
    hits: List[AutodetectHit] = []
    if sys.platform != "win32":
        return hits
    try:
        import winreg  # noqa: PLC0415
    except ImportError:
        return hits

    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
         "卸载登记表(64 位)"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
         "卸载登记表(32 位)"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
         "卸载登记表(当前用户)"),
    )
    for hive, sub, tag in roots:
        try:
            with winreg.OpenKey(hive, sub) as key:
                total = winreg.QueryInfoKey(key)[0]
        except OSError:
            continue
        for i in range(total):
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                continue
            try:
                with winreg.OpenKey(hive, f"{sub}\\{name}") as entry:
                    def _q(value: str) -> str:
                        try:
                            return str(winreg.QueryValueEx(entry, value)[0])
                        except OSError:
                            return ""

                    display = _q("DisplayName")
                    loc = _q("InstallLocation")
                    icon = _q("DisplayIcon")
            except OSError:
                continue

            candidates: List[Path] = []
            if loc:
                candidates.append(Path(loc))
            if icon:
                # DisplayIcon 常见形如 `D:\Games\X\game.exe,0` 或带引号
                first = icon.split(",")[0].strip().strip('"')
                if first:
                    candidates.append(Path(first).parent)

            for cand in candidates:
                # 登记表里常常登记的是「启动器」而不是游戏根：往上爬几层找 Content\Paks
                for up in _walk_up(cand, 4):
                    if not name_matches(str(up)) and not name_matches(display):
                        break
                    if is_game_root(up):
                        hits.append(
                            AutodetectHit(
                                path=up,
                                installdir=up.name,
                                appid="",
                                source=f"{tag}\\{name}",
                                label=f"{tag}：{display or name}",
                            )
                        )
                        break
    return hits


def _hits_from_xbox_dirs() -> List[AutodetectHit]:
    """Xbox / Game Pass：`<盘>:\\XboxGames\\<游戏>` 是它的固定落点。"""
    hits: List[AutodetectHit] = []
    for drive in _fixed_drives():
        xbox = drive / "XboxGames"
        if not xbox.is_dir():
            continue
        for child in _safe_iterdir(xbox):
            if not child.is_dir():
                continue
            if is_game_root(child) or name_matches(child.name):
                hits.append(
                    AutodetectHit(
                        path=child,
                        installdir=child.name,
                        appid="",
                        source=str(child),
                        label=f"Xbox / Game Pass（{child.name}）",
                    )
                )
    return hits


def _hits_from_common_dirs() -> List[AutodetectHit]:
    """兜底：各固定磁盘上的常见游戏库目录 + 磁盘根下面那一层。

    这一路是为了覆盖「用户把游戏装在 D:\\Games\\明末 或 E:\\SteamLibrary 之外」
    这类情况 —— 用户 2026-10-10 的原话是「游戏无非就是 steam、epic 或者 wegame，
    你自己不会找吗」，所以宁可多找一圈，也不要一上来就让用户手选。
    """
    hits: List[AutodetectHit] = []
    seen: set[str] = set()

    def _add(cand: Path, label: str) -> None:
        key = _norm_key(cand)
        if key in seen:
            return
        seen.add(key)
        hits.append(
            AutodetectHit(path=cand, installdir=cand.name, appid="", source=str(cand), label=label)
        )

    for drive in _fixed_drives():
        for rel in _COMMON_LIB_DIRS:
            base = drive / rel
            if not base.is_dir():
                continue
            for child in _safe_iterdir(base):
                if child.is_dir() and (is_game_root(child) or name_matches(child.name)):
                    _add(child, f"磁盘扫描：{child}")
        # 磁盘根下面第一层
        for child in _safe_iterdir(drive):
            if child.is_dir() and name_matches(child.name):
                _add(child, f"磁盘扫描：{child}")
    return hits


def autodetect_game(*, deep: bool = True) -> List[AutodetectHit]:
    """尽量把《明末：渊虚之羽》的安装目录找出来。

    来源（按可靠性排）：
      1. Steam 库清单（appmanifest_*.acf）
      2. Epic 清单（Manifests\\*.item）
      3. Windows 卸载登记表（含 WOW6432Node / 当前用户）—— 覆盖面最广
      4. Xbox / Game Pass 的 XboxGames 目录
      5. deep=True 时再加一圈「各磁盘常见游戏库 + 盘根第一层」的扫描

    返回顺序 = 「确实能定位到 Content\\Paks」的在前，其余在后。
    同一路径只保留一条（后面的来源跳过）。**绝不抛异常**。
    """
    hits: List[AutodetectHit] = []
    for fn in (_hits_from_steam, _hits_from_epic, _hits_from_registry, _hits_from_xbox_dirs):
        try:
            hits.extend(fn())
        except Exception:  # noqa: BLE001 - 单个来源失败不影响其他
            continue
    if deep:
        try:
            hits.extend(_hits_from_common_dirs())
        except Exception:  # noqa: BLE001
            pass

    good: List[AutodetectHit] = []
    weak: List[AutodetectHit] = []
    seen: set[str] = set()
    for hit in hits:
        try:
            if not Path(hit.path).is_dir():
                continue
        except OSError:
            continue
        key = _norm_key(hit.path)
        if key in seen:
            continue
        seen.add(key)
        (good if is_game_root(hit.path) else weak).append(hit)
    return good + weak
