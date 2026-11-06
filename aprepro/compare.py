"""Сверка официальной прошивки с пересобранной.

Вердикты:
  reproducible     — файлы .apj побитово совпадают;
  image-identical  — образы (и образ внешней флеш-памяти) совпадают, различаются метаданные .apj;
  not-reproducible — образы различаются.

Для различающихся образов отчёт перечисляет области различий и печатные строки вокруг них —
по ним обычно видно причину (другой хеш коммита, путь, дата, версия компилятора).
"""
from __future__ import annotations

import re

from .apj import parse

MERGE_GAP = 16          # области различий ближе этого числа байт объединяются
CONTEXT = 48            # байт контекста вокруг области для поиска строк
MAX_REGIONS = 25
_PRINTABLE = re.compile(rb"[\x20-\x7e]{4,}")


def regions(a: bytes, b: bytes, block: int = 4096) -> list:
    """Области различий [начало, конец) по общей длине; хвост более длинного — отдельной областью."""
    out, start, last = [], None, None
    n = min(len(a), len(b))
    for off in range(0, n, block):
        end = min(off + block, n)
        x, y = a[off:end], b[off:end]
        if x == y:
            continue
        for i in range(end - off):
            if x[i] != y[i]:
                pos = off + i
                if start is None:
                    start = pos
                elif pos - last > MERGE_GAP:
                    out.append([start, last + 1])
                    start = pos
                last = pos
    if start is not None:
        out.append([start, last + 1])
    if len(a) != len(b):
        out.append([n, max(len(a), len(b))])
    return out


def _strings(data: bytes, lo: int, hi: int) -> list:
    chunk = data[max(0, lo - CONTEXT):min(len(data), hi + CONTEXT)]
    return [m.group().decode("ascii") for m in _PRINTABLE.finditer(chunk)][:6]


def image_diff(official: bytes, rebuilt: bytes, hints: dict = None) -> dict:
    regs = regions(official, rebuilt)
    differing = sum(min(e, len(official), len(rebuilt)) - s for s, e in regs if s < min(len(official), len(rebuilt)))
    shown = [{"offset": s, "length": e - s, "official_strings": _strings(official, s, e),
              "rebuilt_strings": _strings(rebuilt, s, e)} for s, e in regs[:MAX_REGIONS]]
    res = {"official_size": len(official), "rebuilt_size": len(rebuilt), "regions": len(regs),
           "differing_bytes": differing, "first_regions": shown}
    for name, (x, y) in (hints or {}).items():       # где в образах встречаются известные строки
        res.setdefault("hints", {})[name] = {"official": official.count(x.encode()), "rebuilt": rebuilt.count(y.encode())}
    return res


def compare(official_apj: bytes, rebuilt_apj: bytes) -> dict:
    a, b = parse(official_apj), parse(rebuilt_apj)
    keys = sorted(set(a.meta) | set(b.meta))
    meta_diff = {k: {"official": a.meta.get(k), "rebuilt": b.meta.get(k)} for k in keys if a.meta.get(k) != b.meta.get(k)}
    res = {"apj_equal": official_apj == rebuilt_apj, "image_equal": a.image == b.image,
           "extf_equal": a.extf_image == b.extf_image, "meta_diff": meta_diff,
           "official": {**a.digests(), "git_identity": a.meta.get("git_identity"), "board_id": a.meta.get("board_id")},
           "rebuilt": {**b.digests(), "git_identity": b.meta.get("git_identity"), "board_id": b.meta.get("board_id")}}
    if res["apj_equal"]:
        res["verdict"] = "reproducible"
    elif res["image_equal"] and res["extf_equal"]:
        res["verdict"] = "image-identical"
    else:
        res["verdict"] = "not-reproducible"
        hints = {}
        ga, gb = a.meta.get("git_identity"), b.meta.get("git_identity")
        if ga and gb and ga != gb:
            hints["git_identity"] = (ga, gb)
        if a.image != b.image:
            res["image_diff"] = image_diff(a.image, b.image, hints)
        if a.extf_image != b.extf_image:
            res["extf_diff"] = image_diff(a.extf_image, b.extf_image, hints)
    return res
