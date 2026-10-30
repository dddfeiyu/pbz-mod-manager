"""探针：UE .pak 索引能不能纯 Python 读出来。

目的：读出 mod 里替换了哪些资源路径（/Game/Characters/Player/Cloth/...），
据此判断这个 mod 换的是哪套衣服、哪些部位。
不依赖 repak.exe —— 只做「读索引」，不做「解包」。

用法：
    python tools/probe_pak.py <某个.pak>
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

MAGIC = 0x5A6F12E1


def find_footer(data: bytes) -> tuple[int, int, int, int]:
    """返回 (magic_pos, version, index_offset, index_size)。"""
    needle = struct.pack("<I", MAGIC)
    pos = data.rfind(needle)
    if pos < 0:
        raise SystemExit("找不到 pak footer 魔数")
    version = struct.unpack_from("<I", data, pos + 4)[0]
    index_offset = struct.unpack_from("<q", data, pos + 8)[0]
    index_size = struct.unpack_from("<q", data, pos + 16)[0]
    return pos, version, index_offset, index_size


def read_fstring(buf: bytes, off: int) -> tuple[str, int]:
    """UE FString：int32 长度 + 内容。正数 = ANSI，负数 = UTF-16。"""
    (length,) = struct.unpack_from("<i", buf, off)
    off += 4
    if length == 0:
        return "", off
    if length > 0:
        raw = buf[off:off + length]
        off += length
        return raw.rstrip(b"\x00").decode("utf-8", "replace"), off
    n = -length
    raw = buf[off:off + n * 2]
    off += n * 2
    return raw.decode("utf-16-le", "replace").rstrip("\x00"), off


def main() -> int:
    path = Path(sys.argv[1])
    size = path.stat().st_size
    print(f"文件：{path.name}   {size:,} B")

    with path.open("rb") as fh:
        tail_len = min(2048, size)
        fh.seek(size - tail_len)
        tail = fh.read(tail_len)

        needle = struct.pack("<I", MAGIC)
        pos = tail.rfind(needle)
        if pos < 0:
            print("❌ 末尾 2KB 内找不到 footer 魔数，可能不是标准 pak")
            return 1
        abs_pos = size - tail_len + pos
        version = struct.unpack_from("<I", tail, pos + 4)[0]
        index_offset = struct.unpack_from("<q", tail, pos + 8)[0]
        index_size = struct.unpack_from("<q", tail, pos + 16)[0]
        print(f"footer @ {abs_pos:#x}  version={version}  "
              f"index_offset={index_offset:#x}  index_size={index_size:,}")

        if not (0 < index_size < size):
            print("❌ index_size 不合理")
            return 1

        fh.seek(index_offset)
        raw_index = fh.read(index_size)

    # 索引通常 zlib 压缩（也可能明文）
    index = raw_index
    if raw_index[:1] == b"\x78":
        try:
            index = zlib.decompress(raw_index)
            print(f"索引 zlib 解压：{len(raw_index):,} → {len(index):,} B")
        except zlib.error as exc:
            print(f"⚠️ zlib 解压失败（{exc}），按明文尝试")
    else:
        print("索引未压缩（明文）")

    mount, off = read_fstring(index, 0)
    (count,) = struct.unpack_from("<i", index, off)
    off += 4
    print(f"挂载点：{mount!r}   文件数：{count}")
    print()
    print("--- 索引头部 240 字节 ---")
    head = index[off:off + 240]
    for row in range(0, len(head), 16):
        chunk = head[row:row + 16]
        hexpart = " ".join(f"{b:02X}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {row:04X}  {hexpart:<47}  {text}")
    print()
    print("--- 尝试直接读文件名（UE 紧凑条目格式，仅供参照）---")
    for i in range(min(count, 8)):
        try:
            name, off = read_fstring(index, off)
            print(f"  [{i}] {name!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}] 解析中断：{exc!r}")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
