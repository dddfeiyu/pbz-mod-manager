# -*- coding: utf-8 -*-
"""JSON 持久化：原子写 + 损坏容错。

为什么不用 `json.dump(f)` 直接写：
  用户机器可能断电、被杀软中断、被网盘同步进程读走半截文件。
  直接覆盖写会在崩溃时留下「半截 JSON」——下次启动读不出，表现为「设置全没了」。
  所以一律走：写临时文件 -> flush + fsync -> os.replace 原子替换。

损坏的文件**不删除**，改名成 `.broken-<时间戳>` 留档（用户数据不可随手丢）。

所有写操作都会经过 apppaths.assert_writable_target()，
从根上保证「数据只写 %APPDATA%，不写程序目录」（需求文档第 4 章硬约束）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import apppaths

#: 读写一律显式 UTF-8（需求文档 R-21 / R-22：不依赖系统默认编码）
ENCODING = "utf-8"


class JsonStoreError(RuntimeError):
    """读写 JSON 失败，且无法自动恢复。"""


def load_json(
    path: Path,
    default: Any,
    *,
    validator: Optional[Callable[[Any], bool]] = None,
) -> Any:
    """读 JSON。任何异常都不抛给调用方，一律退回 default，保证程序能起来。

    - 文件不存在 -> default
    - 内容为空 / 解析失败 -> 把原文件改名留档，返回 default
    - 结构不合法（validator 返回 False）-> 同样留档并返回 default

    理由：这类工具最差的表现是「因为一个坏配置文件就打不开」。
    配置坏了应该是「回到默认值」，不是「程序崩了」。
    """
    path = Path(path)
    if not path.exists():
        return default

    try:
        raw = path.read_text(encoding=ENCODING)
    except OSError:
        return default

    if not raw.strip():
        _quarantine(path, reason="empty")
        return default

    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        _quarantine(path, reason="parse")
        return default

    if validator is not None:
        try:
            ok = bool(validator(data))
        except Exception:  # noqa: BLE001 - 校验器自身出错也不能拖垮启动
            ok = False
        if not ok:
            _quarantine(path, reason="schema")
            return default

    return data


def save_json(path: Path, data: Any) -> None:
    """原子写 JSON。写程序目录会直接抛 ProgramDirWriteError。"""
    path = Path(path)
    apppaths.assert_writable_target(path)

    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)

    try:
        with open(tmp, "w", encoding=ENCODING, newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        _silent_unlink(tmp)
        raise JsonStoreError(f"写入失败：{path}（{exc}）") from exc


def _quarantine(path: Path, *, reason: str) -> None:
    """把坏文件改名留档，绝不删除用户数据。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.broken-{reason}-{stamp}")
    try:
        os.replace(path, target)
    except OSError:
        pass


def _silent_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
