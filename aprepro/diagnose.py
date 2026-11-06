"""Разбор расхождения по файлам ELF: какие символы различаются размером и не в ROMFS ли дело.

ArduPilot встраивает в прошивку файлы ROMFS (загрузчик, параметры по умолчанию, hwdef.dat,
прошивку сопроцессора ввода-вывода), сжимая их при сборке модулем zlib интерпретатора Python
(Tools/ardupilotwaf/embed.py: zlib.compressobj(level=9, wbits=-15, memLevel=9)). Разные
реализации zlib (классический zlib и zlib-ng) дают разный поток deflate для того же содержимого,
поэтому прошивка зависит от того, с какой библиотекой собран интерпретатор.

Разбор сравнивает таблицы символов официального и пересобранного ELF и для блоков ap_romfs_N
распаковывает оба потока: если содержимое совпадает, а сжатые байты нет, и только эти символы
различаются размером, — расхождение объясняется сжатием ROMFS.
"""
from __future__ import annotations

import re
import struct
import zlib

_ROMFS = re.compile(r"^(?:_ZL\d+)?(ap_romfs_\d+)$")
STT_OBJECT, STT_FUNC = 1, 2


class ElfError(ValueError):
    """Файл не является ELF32 little-endian."""


def symbols(elf: bytes) -> dict:
    """{имя: (адрес, размер, байты или None)} для объектов и функций ELF32 little-endian."""
    if elf[:4] != b"\x7fELF" or elf[4] != 1 or elf[5] != 1:
        raise ElfError("ожидался ELF32 little-endian")
    shoff, = struct.unpack_from("<I", elf, 0x20)
    shentsize, shnum = struct.unpack_from("<HH", elf, 0x2E)
    secs = [struct.unpack_from("<IIIIIIIIII", elf, shoff + i * shentsize) for i in range(shnum)]
    out = {}
    for sec in secs:
        if sec[1] != 2:                                   # SHT_SYMTAB
            continue
        _n, _t, _f, _a, off, size, link, _i, _al, entsize = sec
        stroff = secs[link][4]
        for k in range(size // entsize):
            st_name, value, st_size, info, _other, shndx = struct.unpack_from("<IIIBBH", elf, off + k * entsize)
            if (info & 0xF) not in (STT_OBJECT, STT_FUNC) or not st_size:
                continue
            end = elf.index(b"\0", stroff + st_name)
            name = elf[stroff + st_name:end].decode("utf-8", "replace")
            data = None
            if 0 < shndx < len(secs) and secs[shndx][1] != 8:     # не NOBITS
                s_addr, s_off = secs[shndx][3], secs[shndx][4]
                data = elf[s_off + value - s_addr: s_off + value - s_addr + st_size]
            out[name] = (value, st_size, data)
    return out


def _inflate(b: bytes):
    """Распаковать поток deflate без заголовка; None, если это не полный поток deflate."""
    try:
        d = zlib.decompressobj(-15)
        out = d.decompress(b) + d.flush()
    except zlib.error:
        return None
    return out if d.eof and not d.unused_data else None


def _deflate(b: bytes) -> bytes:
    z = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=-15, memLevel=9)
    return z.compress(b) + z.flush()


def diagnose(official_elf: bytes, rebuilt_elf: bytes, limit: int = 30) -> dict:
    a, b = symbols(official_elf), symbols(rebuilt_elf)
    sized = sorted(n for n in set(a) | set(b) if (a.get(n) or (0, -1))[1] != (b.get(n) or (0, -1))[1])
    romfs = []
    for name in sorted(n for n in set(a) & set(b) if _ROMFS.match(n)):
        oa, ob = a[name][2], b[name][2]
        if oa is None or ob is None:
            continue
        ca, cb = _inflate(oa), _inflate(ob)
        romfs.append({"name": _ROMFS.match(name).group(1), "official_size": len(oa), "rebuilt_size": len(ob),
                      "compressed_equal": oa == ob, "content_equal": ca is not None and ca == cb,
                      "content_size": len(ca) if ca is not None else None,
                      "official_is_local_zlib": ca is not None and _deflate(ca) == oa,
                      "rebuilt_is_local_zlib": cb is not None and _deflate(cb) == ob})
    only_romfs = bool(sized) and all(_ROMFS.match(n) for n in sized)
    explained = only_romfs and romfs and all(r["content_equal"] for r in romfs) and \
        any(not r["compressed_equal"] for r in romfs)
    return {"zlib": zlib.ZLIB_RUNTIME_VERSION,
            "size_diff_count": len(sized),
            "size_diff": [{"name": n, "official": (a.get(n) or (0, None))[1], "rebuilt": (b.get(n) or (0, None))[1]}
                          for n in sized[:limit]],
            "romfs": romfs,
            "cause": "romfs-compression" if explained else "unknown"}


def romfs_info(elf: bytes) -> dict:
    """Сколько файлов ROMFS встроено в прошивку и сколько из них сжато (зависит от zlib при сборке)."""
    syms = symbols(elf)
    blobs = [v[2] for n, v in syms.items() if _ROMFS.match(n) and v[2] is not None]
    compressed = [b for b in blobs if _inflate(b) is not None]
    return {"files": len(blobs), "compressed": len(compressed), "compressed_bytes": sum(len(b) for b in compressed)}
