# -*- coding: utf-8 -*-
"""「识别手装 mod」的回归测试（用户 2026-10-12）。

场景原话：「我手动安装了 10 个 mod，其中 5 个与管理器中的 mod 相同。
那么管理器能识别到这五个 mod 吗？并在管理器中显示已经安装？」

**所有测试都写在临时目录里**，并通过 `WMM_DATA_DIR` 把
`apppaths.data_dir()` 指到临时目录 —— 绝不触碰真实的 %APPDATA%。

跑法：
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from wmm import modstore, wildscan
from wmm.modstore import ModFile, ModMeta


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return modstore.hash_file(path)


class WildCase(unittest.TestCase):
    """公共脚手架：临时数据目录（缓存落这里）+ 临时游戏 ~mods 目录。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.data = self.base / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self._old_env = os.environ.get("WMM_DATA_DIR")
        os.environ["WMM_DATA_DIR"] = str(self.data)
        self.store = modstore.ModStore(self.data / "mods")
        self.store.ensure()
        self.mods = self.base / "game" / "Paks" / "~mods"
        self.mods.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("WMM_DATA_DIR", None)
        else:
            os.environ["WMM_DATA_DIR"] = self._old_env
        self._tmp.cleanup()

    # ---- 造一个库内 mod（文件落盘 + meta 记 sha256）----
    def make_mod(self, mod_id: str, *filenames: str, name: str = "") -> ModMeta:
        meta = ModMeta(id=mod_id, name=name or mod_id, files=[ModFile(name=f) for f in filenames])
        self.store.save(meta)
        files_dir = self.store.files_dir(mod_id)
        files_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for f in filenames:
            p = files_dir / f
            p.write_bytes(f"MOCK-{f}".encode("utf-8"))
            files.append(
                ModFile(name=f, rel_path=f, size=p.stat().st_size, sha256=modstore.hash_file(p))
            )
        meta.files = files
        self.store.save(meta)
        return meta

    def put_in_game(self, rel: str, data: bytes) -> str:
        return _write(self.mods / rel, data)


# ---------------------------------------------------------------- 列举
class TestListGameFiles(WildCase):
    def test_sees_subdirs(self):
        self.put_in_game("a_P.pak", b"a")
        self.put_in_game("sub/b_P.pak", b"b")
        files, truncated = wildscan.list_game_files([self.mods])
        rels = sorted(f.rel for f in files)
        self.assertEqual(rels, ["a_P.pak", "sub/b_P.pak"])
        self.assertFalse(truncated)

    def test_ignores_platform_dirs(self):
        # 平台库目录名整名匹配（pakfiles.IGNORE_DIR_NAMES 的规矩，
        # 2026-10-11 闪退事故的教训）：~mods 下不该有这些，但真出现了也不能跟进去
        self.put_in_game("a_P.pak", b"a")
        self.put_in_game("steamapps/common/x_P.pak", b"x")
        files, _ = wildscan.list_game_files([self.mods])
        self.assertEqual([f.rel for f in files], ["a_P.pak"])

    def test_depth_brake(self):
        # 超过 WILD_MAX_DEPTH 层必须停下并如实标记（绝不静默截断）
        self.put_in_game("a_P.pak", b"a")
        self.put_in_game("l1/l2/l3/deep_P.pak", b"deep")
        files, truncated = wildscan.list_game_files([self.mods])
        rels = sorted(f.rel for f in files)
        self.assertEqual(rels, ["a_P.pak"])
        self.assertTrue(truncated)

    def test_missing_dir_is_skipped(self):
        files, truncated = wildscan.list_game_files([self.base / "nope"])
        self.assertEqual(files, [])
        self.assertFalse(truncated)


# ---------------------------------------------------------------- 签名
class TestSignature(WildCase):
    def test_stable_and_sensitive(self):
        self.put_in_game("a_P.pak", b"a")
        files1, _ = wildscan.list_game_files([self.mods])
        sig1 = wildscan.signature(files1)
        files2, _ = wildscan.list_game_files([self.mods])
        self.assertEqual(sig1, wildscan.signature(files2))
        # 内容变了（大小变）→ 签名必须变，否则缓存不刷新
        self.put_in_game("a_P.pak", b"aa")
        files3, _ = wildscan.list_game_files([self.mods])
        self.assertNotEqual(sig1, wildscan.signature(files3))


# ---------------------------------------------------------------- 匹配
class TestMatch(WildCase):
    def setUp(self):
        super().setUp()
        # 库内三个 mod：甲 = 2 文件，乙 = 1 文件，丙 = 无 sha256（老数据）
        self.jia = self.make_mod("jia", "j1.pak", "j2.pak", name="甲")
        self.yi = self.make_mod("yi", "y1.pak", name="乙")
        self.store.save(ModMeta(id="bing", name="丙", files=[ModFile(name="b1.pak", sha256="")]))
        self.index = wildscan.build_mod_index(self.store)

    def test_index_skips_empty_sha(self):
        self.assertNotIn("bing", self.index)
        self.assertIn("jia", self.index)

    def test_complete_hit(self):
        self.put_in_game("j1.pak", b"MOCK-j1.pak")
        self.put_in_game("j2.pak", b"MOCK-j2.pak")
        files, _ = wildscan.list_game_files([self.mods])
        wildscan._hash_files(files)
        hits, unknown = wildscan.match(files, self.index)
        self.assertIn("jia", hits)
        self.assertTrue(hits["jia"].complete)
        self.assertEqual(hits["jia"].total, 2)
        self.assertEqual(len(hits["jia"].paths), 2)
        self.assertEqual(unknown, [])

    def test_partial_hit(self):
        # 只拖了一个文件进去 —— 游戏加载的是残缺的一套，必须报出来
        self.put_in_game("j1.pak", b"MOCK-j1.pak")
        files, _ = wildscan.list_game_files([self.mods])
        wildscan._hash_files(files)
        hits, unknown = wildscan.match(files, self.index)
        self.assertFalse(hits["jia"].complete)
        self.assertEqual(hits["jia"].matched, 1)

    def test_unknown_files(self):
        self.put_in_game("j1.pak", b"MOCK-j1.pak")
        self.put_in_game("j2.pak", b"MOCK-j2.pak")
        self.put_in_game("野生_P.pak", b"wild")
        self.put_in_game("readme.txt", "说明".encode("utf-8"))
        files, _ = wildscan.list_game_files([self.mods])
        wildscan._hash_files(files)
        hits, unknown = wildscan.match(files, self.index)
        self.assertTrue(hits["jia"].complete)
        unknown_rels = sorted(f.rel for f in unknown)
        self.assertEqual(unknown_rels, ["readme.txt", "野生_P.pak"])
        # 「野生」是否算疑似 mod 文件由 is_pak_like 判（UI 过滤用）
        self.assertTrue(any(f.is_pak_like for f in unknown))

    def test_one_file_shared_by_two_mods(self):
        # 一个 pak 被两个 mod 共用是真实现象：两边都算命中，
        # 但 paths 只列一次（避免界面重复）
        shared = b"shared-pak"
        # 甲追加 shared.pak（库里也要更新 meta，否则它的哈希集合不含这个文件）
        jia_files = self.store.files_dir("jia")
        (jia_files / "shared.pak").write_bytes(shared)
        jia_meta = self.store.get("jia")
        jia_meta.files.append(
            ModFile(name="shared.pak", size=len(shared), sha256=modstore.hash_file(jia_files / "shared.pak"))
        )
        self.store.save(jia_meta)
        # 乙也含同一个文件
        yi_files = self.store.files_dir("yi")
        (yi_files / "s.pak").write_bytes(shared)
        yi_meta = self.store.get("yi")
        yi_meta.files = [ModFile(name="s.pak", size=len(shared), sha256=modstore.hash_file(yi_files / "s.pak"))]
        self.store.save(yi_meta)
        self.index = wildscan.build_mod_index(self.store)

        self.put_in_game("shared.pak", shared)
        files, _ = wildscan.list_game_files([self.mods])
        wildscan._hash_files(files)
        hits, unknown = wildscan.match(files, self.index)
        self.assertIn("jia", hits)
        self.assertIn("yi", hits)
        self.assertTrue(hits["yi"].complete)
        self.assertFalse(hits["jia"].complete)   # 甲还有 j1/j2 没到位
        total_listed = sum(len(h.paths) for h in hits.values())
        self.assertEqual(total_listed, 1)


# ---------------------------------------------------------------- 查询
class TestLookup(WildCase):
    def _mod_with_file(self, mod_id: str, filename: str, data: bytes) -> str:
        """造一个只含一个文件的 mod，返回该文件的 sha256。"""
        fd = self.store.files_dir(mod_id)
        fd.mkdir(parents=True, exist_ok=True)
        (fd / filename).write_bytes(data)
        sha = modstore.hash_file(fd / filename)
        self.store.save(
            ModMeta(
                id=mod_id,
                name=mod_id,
                files=[ModFile(name=filename, size=len(data), sha256=sha)],
            )
        )
        return sha

    def test_lookup_deterministic(self):
        data = b"same"
        h1 = self._mod_with_file("moda", "a.pak", data)
        h2 = self._mod_with_file("modb", "b.pak", data)
        self.assertEqual(h1, h2)
        index = wildscan.build_mod_index(self.store)
        hit = wildscan.lookup_mod(index, h1)
        self.assertEqual(hit, ("moda", "moda"))   # 字典序最小，结果可重现

    def test_lookup_miss(self):
        self.make_mod("solo", "s.pak")
        index = wildscan.build_mod_index(self.store)
        self.assertIsNone(wildscan.lookup_mod(index, "0" * 64))
        self.assertIsNone(wildscan.lookup_mod(index, ""))


# ---------------------------------------------------------------- 缓存
class TestRecognizeCache(WildCase):
    def setUp(self):
        super().setUp()
        self.make_mod("jia", "j1.pak", "j2.pak", name="甲")
        self.put_in_game("j1.pak", b"MOCK-j1.pak")
        self.put_in_game("j2.pak", b"MOCK-j2.pak")
        self.index = wildscan.build_mod_index(self.store)

    def test_second_call_uses_cache(self):
        s1 = wildscan.recognize([self.mods], self.index)
        self.assertTrue(s1.scanned_at)
        # 缓存命中时不再算哈希 —— 用计数证明
        calls = {"n": 0}
        orig = modstore.hash_file

        def counting(path, *a, **kw):
            calls["n"] += 1
            return orig(path, *a, **kw)

        modstore.hash_file = counting
        try:
            s2 = wildscan.recognize([self.mods], self.index)
        finally:
            modstore.hash_file = orig
        self.assertEqual(calls["n"], 0)
        self.assertEqual(s2.sig, s1.sig)
        self.assertIn("jia", s2.hits)

    def test_force_recomputes(self):
        wildscan.recognize([self.mods], self.index)
        s2 = wildscan.recognize([self.mods], self.index, force=True)
        self.assertTrue(s2.scanned_at)
        self.assertIn("jia", s2.hits)

    def test_cache_roundtrip(self):
        s1 = wildscan.recognize([self.mods], self.index)
        self.assertTrue(wildscan.cache_path().is_file())
        loaded = wildscan.load_cache()
        self.assertIsNotNone(loaded)
        self.assertEqual(set(loaded.hits), set(s1.hits))
        self.assertEqual(loaded.hits["jia"].total, 2)
        # refresh() 用的轻量入口
        self.assertIn("jia", wildscan.load_cache_hits())

    def test_new_file_invalidates_cache(self):
        wildscan.recognize([self.mods], self.index)
        self.put_in_game("j3.pak", b"another")
        s2 = wildscan.recognize([self.mods], self.index)
        self.assertTrue(s2.scanned_at)          # 重扫了
        self.assertIn("j3.pak", [f.rel for f in s2.unknown])

    def test_max_bytes_skips(self):
        s = wildscan.recognize([self.mods], self.index, max_bytes=10)
        self.assertFalse(s.hits)
        self.assertFalse(s.scanned_at)          # 明示"没扫"，别装成扫过了

    def test_no_dirs(self):
        s = wildscan.recognize([self.base / "nope"], self.index)
        self.assertEqual(s.hits, {})


# ---------------------------------------------------------------- 启动校验
class TestStartupValidation(WildCase):
    """启动路径读缓存前必须校验文件还在（用户 2026-10-13）。

    事故：扫完缓存后用户手动删了游戏里的 pak，启动时界面直接读
    旧缓存 → 把已经不存在的 mod 显示成「已装·手装」。
    """

    def setUp(self):
        super().setUp()
        self.make_mod("jia", "j1.pak", name="甲")
        self.make_mod("yi", "y1.pak", "y2.pak", name="乙")
        self.put_in_game("j1.pak", b"MOCK-j1.pak")
        self.put_in_game("y1.pak", b"MOCK-y1.pak")
        self.put_in_game("y2.pak", b"MOCK-y2.pak")
        self.put_in_game("野生_P.pak", b"wild")
        self.index = wildscan.build_mod_index(self.store)
        wildscan.recognize([self.mods], self.index)

    def test_gone_file_drops_hit_and_persists(self):
        (self.mods / "j1.pak").unlink()
        hits = wildscan.load_cache_hits_validated()
        self.assertNotIn("jia", hits)
        self.assertIn("yi", hits)               # 文件还在的不受牵连
        # 修剪结果写回缓存：下次启动也不再出现
        self.assertNotIn("jia", wildscan.load_cache_hits())

    def test_intact_cache_untouched(self):
        before = wildscan.load_cache().scanned_at
        hits = wildscan.load_cache_hits_validated()
        self.assertIn("jia", hits)
        self.assertIn("yi", hits)
        self.assertEqual(wildscan.load_cache().scanned_at, before)  # 没重写

    def test_partial_paths_keep_hit(self):
        # 乙的两个文件删掉一个：paths 没全消失 → 保留（matched 与
        # paths 数量不对应，没法安全下调，交给下次完整扫描纠正）
        (self.mods / "y1.pak").unlink()
        hits = wildscan.load_cache_hits_validated()
        self.assertIn("yi", hits)

    def test_empty_paths_hit_kept(self):
        # paths 为空（共用文件只列进第一个 mod）→ 保守保留
        cached = wildscan.load_cache()
        cached.hits["ghost"] = wildscan.ExternalHit(
            mod_id="ghost", mod_name="幽灵", matched=1, total=1, paths=[]
        )
        wildscan.save_cache(cached)
        self.assertIn("ghost", wildscan.load_cache_hits_validated())

    def test_unknown_pruned(self):
        (self.mods / "野生_P.pak").unlink()
        wildscan.load_cache_hits_validated()
        self.assertEqual(wildscan.load_cache().unknown, [])

    def test_no_cache(self):
        os.remove(wildscan.cache_path())
        self.assertEqual(wildscan.load_cache_hits_validated(), {})


# ---------------------------------------------------------------- 展示
class TestPresentation(unittest.TestCase):
    def test_hit_text(self):
        full = wildscan.ExternalHit(mod_id="x", mod_name="甲", matched=3, total=3)
        self.assertTrue(full.complete)
        self.assertEqual(full.text, "手装：3 个文件都在游戏里")
        part = wildscan.ExternalHit(mod_id="x", mod_name="甲", matched=1, total=3)
        self.assertFalse(part.complete)
        self.assertEqual(part.text, "手装：只有 1/3 个文件在游戏里")

    def test_summary(self):
        scan = wildscan.WildScan()
        self.assertEqual(scan.summary(), "识别完成：游戏目录里没有不受本工具管理的 mod 文件。")
        scan.hits = {
            "a": wildscan.ExternalHit(mod_id="a", mod_name="甲", matched=2, total=2),
            "b": wildscan.ExternalHit(mod_id="b", mod_name="乙", matched=1, total=3),
        }
        scan.unknown = []
        text = scan.summary()
        self.assertIn("1 个手装 mod 与库中相同", text)
        self.assertIn("1 个只对上了一部分文件", text)


if __name__ == "__main__":
    unittest.main()
