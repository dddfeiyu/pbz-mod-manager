# -*- coding: utf-8 -*-
"""真实启动路径检查（需求文档 F-10.4 / F-10.5 / F-10.5b）。

**每个场景必须单独开一个进程跑**：Qt 每个进程只允许存在一个 QApplication，
在一个进程里连续跑两次 `app.run()` 会抛
`libshiboken: Please destroy the QApplication singleton`。
所以这里自己当调度器，用 `--scenario` 把每个场景丢进子进程。

验的是启动流程里最容易出错、又最难在截图上看出来的三件事：

  1. **弹窗期间主界面已经在后台建好了**（F-10.5）—— 用户点「进入工具」要秒开。
  2. **点完之后主界面真的接管了、弹窗真的退了** —— 不能只是把弹窗藏起来。
  3. **`show_splash=false` 时不再弹窗**（F-10.5b）。

手法：把 `QApplication.exec` 换成一个"只跑几轮事件循环就返回"的替身，
于是既走完真实的启动代码路径（含那次 QTimer 交棒），又不会卡住等人点关闭。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ---------------------------------------------------------------- 子场景
def _pump(app, rounds: int = 12) -> None:
    for _ in range(rounds):
        app.processEvents()


def scenario(name: str, data_dir: Path) -> int:
    from PySide6.QtWidgets import QApplication

    from wmm import apppaths, settings as settings_mod
    from wmm.ui import app as app_mod
    from wmm.ui.splash import SplashDialog

    os.environ["WMM_DATA_DIR"] = str(data_dir)

    try:
        # 目录是全新的 mkdtemp 子目录，不需要（也不该）先删一次
        apppaths.ensure_data_dirs()
        st = settings_mod.Settings.load()
        st.show_splash = name == "splash"
        st.save()
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 准备 settings 失败：{exc!r}")
        return 1

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[FAIL]'} {label}" + (f"  —— {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    holder: dict = {"splash": None, "main": None}

    def fake_exec(self=None) -> int:  # noqa: ANN001
        app = QApplication.instance()
        _pump(app, 6)
        # 把当前看到的窗口状态存下来（exec 返回后 QApplication 还在，
        # 但为了不依赖它，先在事件循环里抓一次）
        holder["after_boot"] = _snapshot(app)
        if name == "splash":
            sp = holder["after_boot"]["splash"]
            if sp is not None:
                # 模拟用户点「进入工具」——这是唯一规定的出口
                sp.btn_enter.click()
                _pump(app, 6)
        holder["final"] = _snapshot(app)
        return 0

    def _snapshot(app) -> dict:
        tops = list(app.topLevelWidgets())
        sp = next((w for w in tops if isinstance(w, SplashDialog) and w.isVisible()), None)
        mw = next(
            (
                w for w in tops
                if w.metaObject().className() == "MainWindow" and w.isVisible()
            ),
            None,
        )
        # 主窗"已建好但目前隐藏"也要能识别出来 -> F-10.5 后台并行加载
        mw_built = next((w for w in tops if w.metaObject().className() == "MainWindow"), None)
        return {"splash": sp, "main": mw, "main_built": mw_built}

    original = QApplication.exec
    QApplication.exec = fake_exec          # type: ignore[assignment]
    try:
        app_mod.run([sys.argv[0]])
    except Exception as exc:  # noqa: BLE001
        QApplication.exec = original       # type: ignore[assignment]
        print(f"[FAIL] 启动路径抛异常：{exc!r}")
        return 1
    finally:
        QApplication.exec = original       # type: ignore[assignment]

    boot = holder.get("after_boot") or {}
    final = holder.get("final") or {}

    if name == "splash":
        check("弹窗出现（show_splash=true）", boot.get("splash") is not None)
        check(
            "主界面已在后台并行建好（F-10.5）",
            boot.get("main_built") is not None,
            "主窗对象已存在" if boot.get("main_built") is not None else "主窗根本没建",
        )
        check(
            "弹窗期间主界面还没抢镜（先让用户看完作者是谁）",
            boot.get("main") is None,
            "主窗此时不可见，符合预期",
        )
        check(
            "点「进入工具」后主界面接管",
            final.get("main") is not None,
            "主窗已可见" if final.get("main") is not None else "主窗始终不可见 —— 用户会以为程序闪退",
        )
        check(
            "点「进入工具」后弹窗退场",
            final.get("splash") is None,
            "弹窗已隐藏" if final.get("splash") is None else "弹窗还赖着不走",
        )
    else:
        check("show_splash=false 时不再弹窗（F-10.5b）", boot.get("splash") is None)
        check(
            "show_splash=false 时直接进主界面",
            boot.get("main") is not None,
            "主窗已可见" if boot.get("main") is not None else "主窗不可见",
        )

    print(f"-- {name}：失败 {len(failures)} 项")
    return 1 if failures else 0


# ---------------------------------------------------------------- 调度
def main() -> int:
    if "--scenario" in sys.argv:
        idx = sys.argv.index("--scenario")
        name = sys.argv[idx + 1]
        d = Path(sys.argv[sys.argv.index("--data") + 1])
        return scenario(name, d)

    root = Path(tempfile.mkdtemp(prefix="wmm-startup-"))
    print(f"临时工作目录：{root}")
    print("=" * 66)
    rc = 0
    for name in ("splash", "nosplash"):
        print(f"【场景 {name}】")
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--scenario", name,
             "--data", str(root / name)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace",
        )
        for line in (proc.stdout or "").splitlines():
            if line.startswith("[OK]") or line.startswith("[FAIL]") or line.startswith("--"):
                print("   " + line)
        if proc.returncode != 0:
            rc = 1
        print()
    print("=" * 66)
    print("全部通过" if rc == 0 else "有失败项")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
