# -*- coding: utf-8 -*-
"""压缩包解压（需求文档 3.2 / 第 6 章技术选型）。

选型（原文）：`zipfile + py7zr；.rar 可回退到系统 tar.exe（libarchive，支持读 RAR4/RAR5）`
—— 理由写得很直白：**用户机器不一定有 WinRAR / 7-Zip**。

本模块补了文档没写的三件事：

1. **按魔数判类型，不看后缀。** 实测下载站把 7z 改成 .zip、把 zip 改成 .rar 是常事，
   按后缀分发到错误解压器会得到「文件已损坏」这种误导性报错。
2. **Zip Slip / 路径穿越防护。** 压缩包内文件名可以写成 `..\\..\\Windows\\System32\\x`，
   不加防护的解压器会直接覆盖系统文件。这里对每条成员名做解析校验。
3. **解压体积上限。** 防 zip bomb：一个 1 KB 的压缩包能解出几十 GB。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

KIND_ZIP = "zip"
KIND_7Z = "7z"
KIND_RAR = "rar"
KIND_UNKNOWN = "unknown"

ARCHIVE_KINDS = (KIND_ZIP, KIND_7Z, KIND_RAR)

#: 常见后缀 -> 类型（仅作**参考**，真实类型以魔数为准）
EXT_HINTS = {
    ".zip": KIND_ZIP,
    ".7z": KIND_7Z,
    ".rar": KIND_RAR,
}

ARCHIVE_EXT_SET = frozenset(EXT_HINTS)

#: 解压总体积上限（字节）。超过就中止 —— 防 zip bomb。
MAX_EXTRACT_BYTES = 20 * 1024 * 1024 * 1024   # 20 GB

#: 单个文件数量上限，防「十万个小文件」拖死界面
MAX_EXTRACT_FILES = 200_000


class ArchiveError(RuntimeError):
    """解压失败。消息面向用户，必须是人话。"""


class UnsafeArchiveError(ArchiveError):
    """压缩包含有路径穿越成分，已拒绝解压。"""


@dataclass
class ExtractResult:
    dest: Path
    kind: str
    file_count: int = 0
    total_bytes: int = 0
    method: str = ""            # 实际用了哪个解压器（排障用）
    notes: List[str] = field(default_factory=list)


def detect_archive_kind(path: str | os.PathLike[str]) -> str:
    """按魔数判断压缩包类型，判不出来才回退到后缀。"""
    p = Path(path)
    try:
        with p.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        head = b""

    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06") or head.startswith(b"PK\x07\x08"):
        return KIND_ZIP
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return KIND_7Z
    if head.startswith(b"Rar!\x1a\x07\x00") or head.startswith(b"Rar!\x1a\x07\x01\x00"):
        return KIND_RAR

    return EXT_HINTS.get(p.suffix.lower(), KIND_UNKNOWN)


def looks_like_archive(path: str | os.PathLike[str]) -> bool:
    p = Path(path)
    return p.is_file() and (
        p.suffix.lower() in ARCHIVE_EXT_SET or detect_archive_kind(p) in ARCHIVE_KINDS
    )


# ---------------------------------------------------------------- 安全检查
def _safe_member_target(dest: Path, member_name: str) -> Path:
    """把压缩包内的成员名解析成 dest 下的目标路径，越界就抛异常。"""
    normalized = member_name.replace("\\", "/").lstrip("/")
    if not normalized or normalized in (".", "./"):
        raise UnsafeArchiveError("压缩包含有空文件名")
    if os.path.isabs(member_name) or (len(member_name) > 1 and member_name[1] == ":"):
        raise UnsafeArchiveError(f"压缩包含有绝对路径成员：{member_name}")
    if any(part == ".." for part in normalized.split("/")):
        raise UnsafeArchiveError(f"压缩包含有路径穿越成员：{member_name}")

    target = (dest / normalized).resolve()
    dest_resolved = dest.resolve()
    try:
        target.relative_to(dest_resolved)
    except ValueError as exc:
        raise UnsafeArchiveError(f"压缩包成员会解压到目标目录之外：{member_name}") from exc
    return target


def _validate_names(names: Sequence[str], dest: Path) -> None:
    for name in names:
        if name.endswith("/") or name.endswith("\\"):
            continue
        _safe_member_target(dest, name)


# ---------------------------------------------------------------- 各类型解压
def _extract_zip(archive: Path, dest: Path) -> ExtractResult:
    result = ExtractResult(dest=dest, kind=KIND_ZIP, method="zipfile")
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        _validate_names([i.filename for i in infos], dest)

        declared = sum(i.file_size for i in infos)
        if declared > MAX_EXTRACT_BYTES:
            raise ArchiveError(
                f"压缩包解压后体积 {declared / 1024**3:.1f} GB，超过上限 "
                f"{MAX_EXTRACT_BYTES / 1024**3:.0f} GB，已中止（防解压炸弹）。"
            )

        for info in infos:
            if info.is_dir():
                continue
            target = _safe_member_target(dest, info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)
            result.file_count += 1
            result.total_bytes += info.file_size
            if result.file_count > MAX_EXTRACT_FILES:
                raise ArchiveError(f"压缩包内文件数超过 {MAX_EXTRACT_FILES}，已中止。")
    return result


def _extract_7z(archive: Path, dest: Path) -> ExtractResult:
    try:
        import py7zr  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 打包时必然带上
        raise ArchiveError(
            "解压 7z 需要 py7zr 组件，当前环境缺失。请手动解压后，把文件夹拖进工具导入。"
        ) from exc

    result = ExtractResult(dest=dest, kind=KIND_7Z, method="py7zr")
    try:
        with py7zr.SevenZipFile(archive, mode="r") as zf:
            names = zf.getnames()
            _validate_names(names, dest)
            zf.extractall(path=str(dest))
    except UnsafeArchiveError:
        raise
    except Exception as exc:  # noqa: BLE001 - py7zr 的异常类型很杂
        raise ArchiveError(f"解压 7z 失败：{exc}。可手动解压后把文件夹拖进工具导入。") from exc

    for dirpath, _dirnames, filenames in os.walk(dest):
        for f in filenames:
            result.file_count += 1
            try:
                result.total_bytes += (Path(dirpath) / f).stat().st_size
            except OSError:
                pass
    return result


def _find_tar() -> Optional[str]:
    exe = shutil.which("tar")
    if exe:
        return exe
    fallback = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if fallback.is_file():
        return str(fallback)
    return None


def _extract_rar(archive: Path, dest: Path) -> ExtractResult:
    """rar 走系统自带 tar.exe（libarchive，Windows 10 1803+ 起内置）。

    不引入 unrar / WinRAR 依赖 —— 需求文档第 6 章明确「用户机器不一定有 WinRAR/7-Zip」，
    而 tar.exe 是系统组件，可以不依赖用户装东西。
    """
    tar_exe = _find_tar()
    if not tar_exe:
        raise ArchiveError(
            "系统里找不到 tar.exe（Windows 10 1803 以下没有）。"
            "请手动用 WinRAR / 7-Zip 解压，再把解压出来的文件夹拖进工具导入。"
        )

    # 先列清单做安全校验，再解压 —— 不要指望解压器自己挡住路径穿越
    list_cmd = [tar_exe, "-tf", str(archive)]
    try:
        listed = subprocess.run(
            list_cmd, capture_output=True, text=False, timeout=180, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ArchiveError(f"读取 rar 目录列表失败：{exc}") from exc

    if listed.returncode != 0:
        detail = listed.stderr.decode("utf-8", "replace").strip() or "未知错误"
        raise ArchiveError(
            f"无法读取这个 rar（{detail}）。"
            "若是 RAR5 或加密压缩包，请手动用 WinRAR / 7-Zip 解压后再导入文件夹。"
        )

    names = [n for n in listed.stdout.decode("utf-8", "replace").splitlines() if n.strip()]
    _validate_names(names, dest)
    if len(names) > MAX_EXTRACT_FILES:
        raise ArchiveError(f"压缩包内条目数超过 {MAX_EXTRACT_FILES}，已中止。")

    cmd = [tar_exe, "-xf", str(archive), "-C", str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=False, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ArchiveError(f"解压 rar 失败：{exc}") from exc

    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or "未知错误"
        raise ArchiveError(f"解压 rar 失败：{detail}")

    result = ExtractResult(dest=dest, kind=KIND_RAR, method="tar.exe (libarchive)")
    for dirpath, _dirnames, filenames in os.walk(dest):
        for f in filenames:
            result.file_count += 1
            try:
                result.total_bytes += (Path(dirpath) / f).stat().st_size
            except OSError:
                pass
    return result


def extract_archive(
    archive: str | os.PathLike[str],
    dest: Optional[str | os.PathLike[str]] = None,
    *,
    kind: Optional[str] = None,
) -> ExtractResult:
    """把压缩包解压到 dest（默认新建临时目录）。

    dest 必须是一台**空目录**：调用方负责清理，本函数不删任何东西。
    """
    archive = Path(archive)
    if not archive.is_file():
        raise ArchiveError(f"压缩包不存在：{archive}")

    if dest is None:
        dest = Path(tempfile.mkdtemp(prefix="wmm-extract-"))
    else:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)

    kind = kind or detect_archive_kind(archive)
    if kind == KIND_ZIP:
        return _extract_zip(archive, dest)
    if kind == KIND_7Z:
        return _extract_7z(archive, dest)
    if kind == KIND_RAR:
        return _extract_rar(archive, dest)

    raise ArchiveError(
        f"不认识这个压缩格式（{archive.name}）。支持 zip / 7z / rar；"
        "其他格式请先用压缩软件解开，再把文件夹拖进工具导入。"
    )


def make_staging_dir(prefix: str = "wmm-import-") -> Path:
    """建一个临时暂存目录。用系统临时目录，**不写进 %APPDATA%**。

    理由：%APPDATA%\\Roaming 在域环境里会被漫游同步，往里塞几百 MB 的解压产物
    会让用户登录/注销变得极慢。系统 Temp 才是这类数据的正确归属。
    """
    return Path(tempfile.mkdtemp(prefix=prefix))


def cleanup_staging(path: Optional[str | os.PathLike[str]]) -> None:
    """清理暂存目录。失败不抛异常 —— 清理不成功不该让导入整体失败。"""
    if not path:
        return
    p = Path(path)
    if not p.exists():
        return
    try:
        shutil.rmtree(p, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
