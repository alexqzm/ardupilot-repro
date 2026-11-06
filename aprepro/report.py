"""Сводка результатов: доли воспроизводимых прошивок по группам и по выборке, среда сборки, разбор расхождений."""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from .diagnose import host_dirs, located
from .util import rjson

VERDICTS = ("reproducible", "image-identical", "not-reproducible", "error")
VERDICT_RU = {"reproducible": "совпадает побитово", "image-identical": "совпадает образ",
              "not-reproducible": "не совпадает", "error": "ошибка"}
CAUSE_RU = {"romfs-compression": "сжатие ROMFS другой реализацией zlib",
            "build-path": "пути исходников в образе (__FILE__): другие каталоги дерева или сборки",
            "romfs-compression+build-path": "сжатие ROMFS и пути исходников в образе",
            "unknown": "не установлена", "unavailable": "разбор недоступен"}
# варианты прогона: results/<вариант>/<план>/… — та же сборка с одним изменённым условием
VARIANT_RU = {"zlib-ng": "Python с zlib-ng вместо классического zlib",
              "build-path": "дерево в каталоге сборочного сервера, сборка вне дерева (waf --out)",
              "server-layout": "дерево, каталог сборки и тулчейн в каталогах сборочного сервера"}


def load(results: Path, plan_name: str) -> list:
    root = Path(results) / plan_name
    return [rjson(p) for p in sorted(root.glob("*/*.json"))
            if not p.name.startswith("_") and not p.name.endswith((".intoto.json", ".sigstore.json"))]


def environments(results: Path, plan_name: str) -> dict:
    root = Path(results) / plan_name
    return {p.parent.name: rjson(p) for p in sorted(root.glob("*/_group.json"))}


def variants(results: Path, plan_name: str) -> dict:
    """{вариант: строки} для каталогов results/<вариант>/<план>, кроме основного results/<план>."""
    root = Path(results)
    if not root.is_dir():
        return {}
    return {d.name: load(d, plan_name) for d in sorted(root.iterdir())
            if d.is_dir() and d.name != plan_name and (d / plan_name).is_dir()}


def wilson(k: int, n: int, z: float = 1.959964) -> tuple:
    """95-процентный доверительный интервал Уилсона для доли k/n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def ok(r: dict) -> bool:
    return r["verdict"] in ("reproducible", "image-identical")


def summary(plan: dict, rows: list) -> dict:
    by_group = {}
    for g in plan["groups"]:
        rs = [r for r in rows if r["group"] == g["id"]]
        c = Counter(r["verdict"] for r in rs)
        by_group[g["id"]] = {"planned": len(g["targets"]), "done": len(rs), **{v: c.get(v, 0) for v in VERDICTS},
                             "build_from": g["build_from"], "tag_commit": g["tag_commit"],
                             "build_commits": sorted({t["build_commit"] for t in g["targets"]})}
    release = [r for r in rows if r["build_from"] == "release" and r["verdict"] != "error"]
    sample = [r for r in release if r["role"] == "sample"]
    k = sum(ok(r) for r in sample)
    lo, hi = wilson(k, len(sample))
    # официальные файлы считаются по группам выпусков: группа «из тега» сверяется с теми же файлами
    official = [r for r in rows if r["build_from"] == "release"]
    checked = [r["romfs_official"] for r in official if "compressed" in r.get("romfs_official", {})]
    packed = [c for c in checked if c["compressed"]]
    with_paths = [r for r in official if "tree" in r.get("paths_official", {})]
    pathful = [r for r in with_paths if located(r["paths_official"])]
    by_file = [r for r in pathful if any(p.startswith("/") for p in r["paths_official"]["prefixes"])]
    by_debug = [r for r in pathful if host_dirs(r["paths_official"])]
    return {"groups": by_group,
            "release": {"n": len(release), **{v: sum(r["verdict"] == v for r in release) for v in VERDICTS[:3]}},
            "sample": {"n": len(sample), "ok": k, "wilson95": [round(lo, 4), round(hi, 4)]},
            "romfs_official": {"checked": len(checked), "with_compressed": len(packed),
                               "local_zlib_all_equal": sum(c["local_zlib_all_equal"] for c in packed),
                               "zlib_ng_differs": sum(c.get("zlib_ng_differs") is True for c in packed),
                               "zlib": sorted({c["zlib"] for c in packed}), "zlib_ng": sorted({str(c.get("zlib_ng")) for c in packed})},
            "paths_official": {"checked": len(with_paths), "located": len(pathful),
                               "file_macro": {"n": len(by_file), "boards": sorted({r["board"] for r in by_file}),
                                              "groups": sorted({r["group"] for r in by_file}),
                                              "roots": sorted({p for r in by_file for p in r["paths_official"]["prefixes"]
                                                               if p.startswith("/")})},
                               "debug_info": {"n": len(by_debug), "boards": sorted({r["board"] for r in by_debug}),
                                              "groups": sorted({r["group"] for r in by_debug}),
                                              "dirs": sorted({d for r in by_debug for d in host_dirs(r["paths_official"])})},
                               "reproducible": sum(ok(r) for r in pathful)},
            "causes": dict(Counter(r.get("diagnosis", {}).get("cause", "unknown") for r in official
                                   if r["verdict"] == "not-reproducible")),
            "errors": sum(r["verdict"] == "error" for r in rows)}


def _pct(x: float) -> str:
    return f"{100 * x:.1f}".replace(".", ",") + " %"


def _dirs(r: dict, main: dict = None) -> str:
    if not r.get("src_path"):
        return "—"
    out = f"`--out {r['out_dir']}`" if r.get("out_dir") else "build/ в дереве"
    tc = ""
    if r.get("toolchain_dir") and (main or {}).get("toolchain_dir") != r["toolchain_dir"]:
        tc = f"; тулчейн `{r['toolchain_dir']}`"
    return f"`{r['src_path']}`; {out}{tc}"


def markdown(plan: dict, plan_name: str, rows: list, envs: dict = None, varied: dict = None) -> str:
    """varied — варианты прогона {имя: строки} (см. variants)."""
    s = summary(plan, rows)
    L = [f"# Результаты: {plan_name}", "",
         f"План [`plans/{plan_name}.json`](../plans/{plan_name}.json): групп — {len(plan['groups'])}, "
         f"сборок — {sum(len(g['targets']) for g in plan['groups'])}; снимок манифеста firmware.ardupilot.org "
         f"SHA-256 `{plan['manifest']['sha256'][:16]}…`; тулчейн {plan['toolchain']}. Файл создаётся командой "
         f"`aprepro report plans/{plan_name}.json --md docs/results.md --readme README.md`, методика — "
         f"[`docs/methodology.md`](methodology.md).", ""]
    if not rows:
        return "\n".join(L + ["Результатов пока нет: запустите workflow rebuild."]) + "\n"
    rel = s["release"]
    L += ["## Итог", "",
          f"Сборки из коммита, указанного в официальных файлах: {rel['n']}. Побитово совпали файлы .apj — "
          f"{rel['reproducible']}, совпали только образы — {rel['image-identical']}, не совпали — "
          f"{rel['not-reproducible']}. Ошибки сборки или загрузки — {s['errors']}.", ""]
    sm = s["sample"]
    if sm["n"]:
        L += [f"Случайная часть выборки: воспроизводимы {sm['ok']} из {sm['n']}; 95-процентный интервал Уилсона "
              f"для доли воспроизводимых прошивок в выпусках плана — {_pct(sm['wilson95'][0])} … "
              f"{_pct(sm['wilson95'][1])}.", ""]
    ro = s["romfs_official"]
    if ro["checked"]:
        L += [f"Сжатые файлы ROMFS есть в {ro['with_compressed']} из {ro['checked']} проверенных официальных "
              f"прошивок. Официальные блоки ROMFS совпадают с выводом zlib среды прогона "
              f"({', '.join(ro['zlib'])}) в {ro['local_zlib_all_equal']} из них; zlib-ng "
              f"({', '.join(ro['zlib_ng'])}) дал бы другой поток хотя бы в одном блоке в {ro['zlib_ng_differs']} — "
              "собранные с ним прошивки не совпали бы с официальными.", ""]
    po = s["paths_official"]
    if po["checked"]:
        if po["located"]:
            fm, dbg = po["file_macro"], po["debug_info"]
            parts = []
            if fm["n"]:
                parts.append(f"абсолютные пути исходников через `__FILE__` (корень дерева "
                             f"{', '.join(f'`{p}`' for p in fm['roots'])}) — в {fm['n']}: платы "
                             f"{', '.join(fm['boards'])}, группы {', '.join(fm['groups'])}")
            if dbg["n"]:
                parts.append(f"отладочная информация с каталогами сборки и тулчейна — в {dbg['n']}: платы "
                             f"{', '.join(dbg['boards'])}, группы {', '.join(dbg['groups'])}")
            L += [f"Пути каталогов сборочного сервера встроены в {po['located']} из {po['checked']} проверенных "
                  f"официальных прошивок: {'; '.join(parts)}. Из них при штатной пересборке совпали "
                  f"{po['reproducible']}.", ""]
        else:
            L += [f"Путей каталогов сборки нет ни в одной из {po['checked']} проверенных официальных прошивок.", ""]
    L += ["## По группам", "",
          "| Группа | Собрано из | Плат в выпуске | В плане | Побитово | Образ | Не совпало | Ошибка |",
          "|---|---|---|---|---|---|---|---|"]
    for g in plan["groups"]:
        x = s["groups"][g["id"]]
        src = "тега" if g["build_from"] == "tag" else "коммита выпуска"
        commits = ", ".join(f"`{c[:8]}`" for c in x["build_commits"])
        L.append(f"| {g['id']} | {src} {commits} | {g['boards_in_release']} | {x['planned']} | {x['reproducible']} | "
                 f"{x['image-identical']} | {x['not-reproducible']} | {x['error']} |")
    L += ["", "## Выпуск, тег и метаданные", "",
          "Коммит тега выпуска сравнивается с коммитом, из которого собраны официальные файлы (манифест и "
          "git-version.txt), а тип выпуска в firmware-version.txt — с типом, зашитым в образ (AP_FWVersion).", "",
          "| Группа | Коммит тега | Коммит сборки | firmware-version.txt | В образе |", "|---|---|---|---|---|"]
    for g in plan["groups"]:
        if g["build_from"] != "release":
            continue
        r = next((r for r in rows if r["group"] == g["id"] and r.get("official")), None)
        if r is None:
            continue
        fw = (r["official"].get("fwversion") or {})
        built = {r["manifest_commit"], r.get("git_version_commit")} - {None}
        commit = ", ".join(f"`{c[:8]}`" for c in sorted(built))
        mark = "" if built == {g["tag_commit"]} else " ≠ тегу"
        txt = r.get("firmware_version") or "—"
        typ = f"{fw.get('version', '—')} {fw.get('fw_type', '')}".strip()
        mark2 = "" if not fw or txt.endswith("_" + fw.get("fw_type", "")) else " ≠ файлу"
        L.append(f"| {g['id']} | `{(g['tag_commit'] or '—')[:8]}` | {commit}{mark} | {txt} | {typ}{mark2} |")
    if envs:
        L += ["", "## Среда сборки", "", "| Группа | Python | zlib | Компилятор | Ядер | Запуск CI |",
              "|---|---|---|---|---|---|"]
        for gid, e in envs.items():
            run = e.get("github_run") or "—"
            L.append(f"| {gid} | {e.get('python')} | {e.get('python_zlib', '—')} | {e['toolchain'].get('gcc', '—')} | "
                     f"{e.get('cpus')} | {run} |")
    L += ["", "## Платы", "",
          "| Группа | Плата | Роль | Итог | SHA-256 образа | ROMFS сжато/всего | zlib-ng изменит | Сборка, с |",
          "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["group"], r["role"] != "panel", r["board"])):
        off = r.get("official", {})
        # у плат, где вся прошивка во внешней флеш-памяти (SPRacingH7), внутренний образ пуст — показываем extf
        sha = off.get("image_sha256", "")[:12] if off.get("image_size") or "extf_sha256" not in off \
            else "extf " + off["extf_sha256"][:12]
        rf = r.get("romfs") or {}
        romfs = f"{rf['compressed']}/{rf['files']}" if "files" in rf else "—"
        ng = (r.get("romfs_official") or {}).get("zlib_ng_differs")
        ng = "—" if ng is None else ("да" if ng else "нет")
        L.append(f"| {r['group']} | {r['board']} | {'панель' if r['role'] == 'panel' else 'выборка'} | "
                 f"{VERDICT_RU[r['verdict']]} | `{sha}` | {romfs} | {ng} | {r.get('build_seconds', '—')} |")
    bad = [r for r in rows if r["verdict"] == "not-reproducible"]
    if bad:
        L += ["", "## Расхождения", ""]
        for r in bad:
            d = r.get("image_diff") or r.get("extf_diff") or {}
            dg = r.get("diagnosis", {})
            extra = []
            if r["official"].get("git_identity") != r["rebuilt"].get("git_identity"):
                extra.append(f"git_identity {r['official']['git_identity']} → {r['rebuilt']['git_identity']}")
            if r["build_from"] == "tag" and r["build_commit"] != r["manifest_commit"]:
                extra.append(f"собрано из тега: коммит `{r['build_commit'][:8]}` вместо `{r['manifest_commit'][:8]}` "
                             "официальной сборки")
            if dg:
                extra.append(f"разбор: {CAUSE_RU.get(dg.get('cause'), dg.get('cause'))}; символов разного размера "
                             f"{dg.get('size_diff_count', '—')}; zlib сборки {dg.get('zlib', r.get('python_zlib', '—'))}")
                bp = dg.get("build_paths") or {}
                if located(bp.get("official") or {}) or located(bp.get("rebuilt") or {}):
                    fmt = lambda x: ", ".join(f"`{p}`" for p in x["prefixes"]) or "нет"
                    extra.append(f"корень дерева в путях образа: {fmt(bp['official'])} → {fmt(bp['rebuilt'])}")
                    if host_dirs(bp["official"]) or host_dirs(bp["rebuilt"]):
                        extra.append(f"каталогов сборки и тулчейна в образе: {len(host_dirs(bp['official']))} → "
                                     f"{len(host_dirs(bp['rebuilt']))}")
            L.append(f"- **{r['group']} / {r['board']}**: областей различий {d.get('regions', '—')}, байт "
                     f"{d.get('differing_bytes', '—')}; поля .apj: {', '.join(r['meta_diff']) or 'нет'}"
                     + "".join(f"; {x}" for x in extra))
    if varied:
        main = {(r["group"], r["board"]): r for r in rows}
        L += ["", "## Проверка причин повторной сборкой", "",
              "Те же платы, пересобранные с изменённым условием (zlib интерпретатора, каталоги дерева, сборки и "
              "тулчейна); "
              "результаты лежат в `results/<вариант>/`.", "",
              "| Вариант | Группа | Плата | Основной прогон | Вариант | zlib | Каталоги |",
              "|---|---|---|---|---|---|---|"]
        for name, vrows in varied.items():
            for r in sorted(vrows, key=lambda r: (r["group"], r["board"])):
                m = main.get((r["group"], r["board"]))
                L.append(f"| {VARIANT_RU.get(name, name)} | {r['group']} | {r['board']} | "
                         f"{VERDICT_RU[m['verdict']] if m else '—'} | {VERDICT_RU[r['verdict']]} | "
                         f"{r.get('python_zlib', '—')} | {_dirs(r, m)} |")
    errs = [r for r in rows if r["verdict"] == "error"]
    if errs:
        L += ["", "## Ошибки", ""]
        L += [f"- {r['group']} / {r['board']} (этап {r.get('stage')}): {r['error'].splitlines()[0][:200]}" for r in errs]
    return "\n".join(L) + "\n"


README_BEGIN, README_END = "<!-- results:begin -->", "<!-- results:end -->"


def readme_block(plan: dict, rows: list) -> str:
    """Краткая сводка для README: итог и таблица по группам."""
    s = summary(plan, rows)
    if not rows:
        return "Результатов пока нет."
    rel, sm = s["release"], s["sample"]
    L = [f"Официальные прошивки, пересобранные из коммитов выпусков: {rel['n']}; побитово совпали — "
         f"{rel['reproducible']}, совпали только образы — {rel['image-identical']}, не совпали — "
         f"{rel['not-reproducible']}" + (f"; ошибки — {s['errors']}" if s["errors"] else "") + "."]
    if sm["n"]:
        L.append(f"Случайная часть выборки: воспроизводимы {sm['ok']} из {sm['n']}, 95-процентный интервал Уилсона "
                 f"{_pct(sm['wilson95'][0])} … {_pct(sm['wilson95'][1])}.")
    L += ["", "| Группа | Собрано из | Побитово | Образ | Не совпало | Ошибка |", "|---|---|---|---|---|---|"]
    for g in plan["groups"]:
        x = s["groups"][g["id"]]
        src = "тега" if g["build_from"] == "tag" else "коммита выпуска"
        L.append(f"| {g['id']} | {src} | {x['reproducible']} | {x['image-identical']} | {x['not-reproducible']} | "
                 f"{x['error']} |")
    return "\n".join(L)


def readme_with_block(readme: str, block: str) -> str:
    head, sep, rest = readme.partition(README_BEGIN)
    _old, sep2, tail = rest.partition(README_END)
    if not sep or not sep2:
        raise ValueError(f"в README нет меток {README_BEGIN} … {README_END}")
    return head + README_BEGIN + "\n" + block + "\n" + README_END + tail
