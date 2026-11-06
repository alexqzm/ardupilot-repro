"""Разбор расхождения по файлам ELF: какие символы различаются размером и не в ROMFS ли дело.

ArduPilot встраивает в прошивку файлы ROMFS (загрузчик, параметры по умолчанию, hwdef.dat,
прошивку сопроцессора ввода-вывода), сжимая их при сборке средствами интерпретатора Python
(Tools/ardupilotwaf/embed.py): до 4.5 включительно — gzip.GzipFile(compresslevel=9, mtime=0), то
есть deflate с memLevel=8 в оболочке gzip; с 4.6 — zlib.compressobj(level=9, wbits=-15, memLevel=9),
поток deflate без заголовка. В обоих случаях сжимает библиотека zlib, с которой собран
интерпретатор. Разные реализации (классический zlib и zlib-ng) дают для одного содержимого разный
поток deflate, поэтому прошивка зависит от того, с какой библиотекой собран Python.

Вторая известная причина — пути исходников, попавшие в образ через __FILE__. В 4.5 и 4.6
утверждения lwIP (libraries/AP_Networking/config/lwipopts.h) передают __FILE__. Waf передаёт
компилятору путь исходника относительно каталога сборки, если у них есть общий предок ниже корня
(при сборке в build/ дерева — ../../modules/lwip/…), и абсолютный, если общий предок — только «/»
(waflib/Node.py, path_from). Официальные прошивки собирает Tools/scripts/build_binaries.py с
каталогом сборки вне дерева (waf configure --out), и в образах плат с сетевым стеком (Ethernet,
PPP) — абсолютные пути вида <корень дерева>/modules/lwip/src/core/tcp.c. Строки другой длины
сдвигают всё, что лежит за ними. В 4.7 утверждения lwIP не передают __FILE__, а сборка добавляет
-ffile-prefix-map (Tools/ardupilotwaf/boards.py).

Разбор сравнивает таблицы символов официального и пересобранного ELF и для блоков ap_romfs_N
распаковывает оба потока: если содержимое совпадает, а сжатые байты нет, и только эти символы
различаются размером, — расхождение объясняется сжатием ROMFS. Затем сравниваются корни дерева в
абсолютных путях исходников, встроенных в образы: если они разные (в том числе абсолютные пути в
официальном образе и относительные в пересобранном), а других символов разного размера нет,
расхождение объясняется путями исходников. Это вывод по признакам; подтверждение — пересборка с
деревом в том же каталоге и каталогом сборки вне дерева (aprepro run --src-root … --out-dir …).
"""
from __future__ import annotations

import gzip
import re
import struct
import zlib

_ROMFS = re.compile(r"^(?:_ZL\d+)?(ap_romfs_\d+)$")
STT_OBJECT, STT_FUNC = 1, 2
SHT_NOBITS, SHF_ALLOC = 8, 2
# строка-путь в образе: начинается с «/» после непечатного байта и заканчивается NUL
_ABS_PATH = re.compile(rb"(?<![\x20-\x7e])/[\x21-\x7e]{3,400}?(?=\x00)")
# каталоги верхнего уровня дерева ArduPilot: всё, что левее первого из них, — корень дерева
_TREE_DIR = re.compile(r"/(?:libraries|modules|ArduCopter|ArduPlane|Rover|ArduSub|AntennaTracker|Blimp)/")
_SOURCE = re.compile(r"\.(?:c|cc|cpp|h|hpp|S)$")      # только пути к исходникам (не «/APM/scripts/modules/?.lua»)


class ElfError(ValueError):
    """Файл не является ELF32 little-endian."""


def symbols(elf: bytes) -> dict:
    """{имя: (адрес, размер, байты или None)} для объектов и функций ELF32 little-endian."""
    secs = _sections(elf)
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
            if 0 < shndx < len(secs) and secs[shndx][1] != SHT_NOBITS:
                s_addr, s_off = secs[shndx][3], secs[shndx][4]
                data = elf[s_off + value - s_addr: s_off + value - s_addr + st_size]
            out[name] = (value, st_size, data)
    return out


def _sections(elf: bytes) -> list:
    if elf[:4] != b"\x7fELF" or elf[4] != 1 or elf[5] != 1:
        raise ElfError("ожидался ELF32 little-endian")
    shoff, = struct.unpack_from("<I", elf, 0x20)
    shentsize, shnum = struct.unpack_from("<HH", elf, 0x2E)
    return [struct.unpack_from("<IIIIIIIIII", elf, shoff + i * shentsize) for i in range(shnum)]


def build_paths(elf: bytes) -> dict:
    """Абсолютные пути исходников, встроенные в загружаемые секции образа (через __FILE__).

    tree — пути внутри дерева ArduPilot с корнями дерева (prefixes: корень → число строк);
    other — прочие абсолютные пути (например, исходники newlib из сборки самого тулчейна: они
    одинаковы у всех, кто собирает тем же тулчейном)."""
    prefixes, other = {}, 0
    for sec in _sections(elf):
        _n, typ, flags, _a, off, size = sec[:6]
        if typ == SHT_NOBITS or not flags & SHF_ALLOC:
            continue
        for m in _ABS_PATH.finditer(elf, off, off + size):
            s = m.group().decode("ascii", "replace")
            if not _SOURCE.search(s):
                continue
            d = _TREE_DIR.search(s)
            if d and d.start() > 0:
                prefixes[s[:d.start()]] = prefixes.get(s[:d.start()], 0) + 1
            else:
                other += 1
    return {"tree": sum(prefixes.values()), "prefixes": dict(sorted(prefixes.items())), "other": other}


def _inflate(b: bytes):
    """Распаковать поток deflate без заголовка; None, если это не полный поток deflate."""
    try:
        d = zlib.decompressobj(-15)
        out = d.decompress(b) + d.flush()
    except zlib.error:
        return None
    return out if d.eof and not d.unused_data else None


def _deflate(b: bytes, mem_level: int) -> bytes:
    z = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=-15, memLevel=mem_level)
    return z.compress(b) + z.flush()


def unpack(b: bytes):
    """Блок ROMFS: (формат, содержимое, поток deflate, memLevel сжатия) или None, если блок не сжат."""
    if b[:3] == b"\x1f\x8b\x08":                       # gzip: ArduPilot до 4.5 включительно
        try:
            content = gzip.decompress(b)
        except (OSError, EOFError, zlib.error):
            return None
        flags, pos = b[3], 10
        if flags & 4:
            pos += 2 + int.from_bytes(b[pos:pos + 2], "little")
        for bit in (8, 16):
            if flags & bit:
                pos = b.index(b"\0", pos) + 1
        if flags & 2:
            pos += 2
        return "gzip", content, b[pos:-8], zlib.DEF_MEM_LEVEL
    content = _inflate(b)                                 # deflate без заголовка: с 4.6
    return None if content is None else ("deflate", content, b, 9)


def diagnose(official_elf: bytes, rebuilt_elf: bytes, limit: int = 30) -> dict:
    a, b = symbols(official_elf), symbols(rebuilt_elf)
    sized = sorted(n for n in set(a) | set(b) if (a.get(n) or (0, -1))[1] != (b.get(n) or (0, -1))[1])
    romfs = []
    for name in sorted(n for n in set(a) & set(b) if _ROMFS.match(n)):
        oa, ob = a[name][2], b[name][2]
        if oa is None or ob is None:
            continue
        ua, ub = unpack(oa), unpack(ob)
        entry = {"name": _ROMFS.match(name).group(1), "official_size": len(oa), "rebuilt_size": len(ob),
                 "compressed_equal": oa == ob, "format": ua[0] if ua else "none",
                 "content_equal": (ua[1] == ub[1]) if ua and ub else oa == ob}
        if ua and ub:
            entry.update(content_size=len(ua[1]),
                         official_is_local_zlib=_deflate(ua[1], ua[3]) == ua[2],
                         rebuilt_is_local_zlib=_deflate(ub[1], ub[3]) == ub[2])
        romfs.append(entry)
    only_romfs = all(_ROMFS.match(n) for n in sized)       # пусто — тоже «только ROMFS»
    by_romfs = bool(sized) and only_romfs and romfs and all(r["content_equal"] for r in romfs) and \
        any(not r["compressed_equal"] for r in romfs)
    paths = {"official": build_paths(official_elf), "rebuilt": build_paths(rebuilt_elf)}
    by_path = only_romfs and paths["official"]["tree"] > 0 and \
        set(paths["official"]["prefixes"]) != set(paths["rebuilt"]["prefixes"])
    causes = [c for c, ok in (("romfs-compression", by_romfs), ("build-path", by_path)) if ok]
    return {"zlib": zlib.ZLIB_RUNTIME_VERSION,
            "size_diff_count": len(sized),
            "size_diff": [{"name": n, "official": (a.get(n) or (0, None))[1], "rebuilt": (b.get(n) or (0, None))[1]}
                          for n in sized[:limit]],
            "romfs": romfs,
            "build_paths": paths,
            "cause": "+".join(causes) or "unknown"}


def romfs_info(elf: bytes) -> dict:
    """Сколько файлов ROMFS встроено в прошивку и сколько из них сжато (зависит от zlib при сборке)."""
    syms = symbols(elf)
    blobs = [v[2] for n, v in syms.items() if _ROMFS.match(n) and v[2] is not None]
    packed = [(b, unpack(b)) for b in blobs]
    compressed = [(b, u) for b, u in packed if u is not None]
    return {"files": len(blobs), "compressed": len(compressed), "compressed_bytes": sum(len(b) for b, _ in compressed),
            "format": sorted({u[0] for _, u in compressed})}


def romfs_check(official_elf: bytes) -> dict:
    """Чувствительность к реализации zlib: пережать содержимое каждого официального блока ROMFS
    zlib интерпретатора и zlib-ng (привязки zlib-ng, если установлены) и сравнить с официальным потоком."""
    try:
        from zlib_ng import zlib_ng as zng
    except ImportError:
        zng = None
    blobs = []
    for name, (_addr, _size, data) in sorted(symbols(official_elf).items()):
        m = _ROMFS.match(name)
        if not m or data is None:
            continue
        u = unpack(data)
        if u is None:
            blobs.append({"name": m.group(1), "format": "none", "size": len(data)})
            continue
        fmt, content, payload, mem = u
        e = {"name": m.group(1), "format": fmt, "size": len(data), "content_size": len(content),
             "local_zlib_equal": _deflate(content, mem) == payload}
        if zng is not None:
            z = zng.compressobj(level=9, method=zng.DEFLATED, wbits=-15, memLevel=mem)
            e["zlib_ng_equal"] = (z.compress(content) + z.flush()) == payload
        blobs.append(e)
    packed = [b for b in blobs if b["format"] != "none"]
    return {"zlib": zlib.ZLIB_RUNTIME_VERSION, "zlib_ng": zng.ZLIBNG_VERSION if zng is not None else None,
            "blobs": blobs, "compressed": len(packed),
            "local_zlib_all_equal": all(b["local_zlib_equal"] for b in packed),
            "zlib_ng_differs": (any(not b["zlib_ng_equal"] for b in packed) if zng is not None and packed else None)}
