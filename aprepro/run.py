"""Прогон группы плана: тулчейн, исходники, затем для каждой платы — официальные файлы, сборка,
сверка, аттестация пересборки. Результат по плате — results/<план>/<группа>/<плата>.json."""
from __future__ import annotations

import os
import platform
import sys
import zlib
from pathlib import Path

from . import __version__, toolchain
from .apj import ApjError
from .attest import statement
from .build import build, clean
from .compare import compare
from .diagnose import ElfError, build_paths, diagnose, romfs_check, romfs_info
from .manifest import VEHICLES
from .source import prepare, required_toolchain
from .util import ToolError, fetch, now_utc, wjson

SCHEMA = "ardupilot-repro/result/v1"


def group_of(plan: dict, gid: str) -> dict:
    for g in plan["groups"]:
        if g["id"] == gid:
            return g
    raise ToolError(f"в плане нет группы {gid}")


def _git_version_commit(text: str):
    for line in text.splitlines():
        if line.startswith("commit "):
            return line.split()[1]
    return None


def run_group(plan: dict, plan_name: str, gid: str, work: Path, results: Path, boards=None, jobs: int = None,
              keep: bool = False, src_root: Path = None, out_dir: Path = None, toolchain_dir: Path = None,
              log=print) -> list:
    """src_root — каталог дерева исходников (по умолчанию <work>/src/<коммит>); out_dir — каталог
    сборки вне дерева (waf --out; по умолчанию build/ в дереве); toolchain_dir — каталог, в
    который ставится тулчейн (по умолчанию <work>/toolchain). Все три влияют на пути, которые
    попадают в образ (см. diagnose.py)."""
    g = group_of(plan, gid)
    want = g.get("python")
    if want and ".".join(map(str, sys.version_info[:2])) != want:
        raise ToolError(f"группа {gid} собирается интерпретатором Python {want}, запущен {sys.version.split()[0]}")
    work, out = Path(work), Path(results) / plan_name / gid
    targets = [t for t in g["targets"] if not boards or t["board"] in boards]
    if boards and len(targets) != len(set(boards)):
        raise ToolError(f"не все платы {sorted(boards)} есть в группе {gid}")
    tc_name = plan["toolchain"]
    tc_root = (Path(toolchain_dir) if toolchain_dir else work / "toolchain").absolute()
    tc_bin = toolchain.ensure(tc_name, tc_root)
    tc = {"name": tc_name, **toolchain.spec(tc_name), "gcc": toolchain.version(tc_bin), "path": str(tc_bin.parent)}
    env = {"started": now_utc(), "tool": f"ardupilot-repro {__version__}", "python": sys.version.split()[0],
           "python_zlib": zlib.ZLIB_RUNTIME_VERSION, "platform": platform.platform(), "cpus": os.cpu_count(),
           "toolchain": tc,
           "github_run": os.environ.get("GITHUB_RUN_ID"), "sources": {}}
    trees = {}
    commits = sorted({t["build_commit"] for t in targets})
    if src_root and len(commits) > 1:
        raise ToolError(f"--src-root: в группе {gid} несколько коммитов сборки, один каталог на всех невозможен")
    for commit in commits:
        log(f"исходники {commit[:12]}")
        tree = Path(src_root).absolute() if src_root else (work / "src" / commit[:12]).absolute()
        desc = prepare(commit, tree, log=work / "logs" / f"source-{commit[:12]}.log")
        desc["path"] = str(tree)
        req = required_toolchain(tree)
        desc["required_toolchain"] = req
        if req != tc_name:
            desc["toolchain_note"] = f"дерево требует {req}, план — {tc_name}"
        env["sources"][commit] = desc
        trees[commit] = tree
    wjson(out / "_group.json", env)
    binary = VEHICLES[g["vehicle"]]["binary"]
    done = []
    for t in targets:
        res = {"schema": SCHEMA, "plan": plan_name, "group": gid, "vehicle": g["vehicle"], "version": g["version"],
               "tag": g["tag"], "tag_commit": g["tag_commit"], "build_from": g["build_from"], "board": t["board"],
               "role": t["role"], "official_url": t["url"], "manifest_commit": t["manifest_commit"],
               "build_commit": t["build_commit"], "python_zlib": zlib.ZLIB_RUNTIME_VERSION,
               "src_path": str(trees[t["build_commit"]]),
               "out_dir": str(Path(out_dir).absolute()) if out_dir else None,
               "toolchain_dir": str(tc_bin.parent), "started": now_utc()}
        stage = "official"
        log(f"{gid} {t['board']}")
        try:
            odir = work / "official" / gid / t["board"]
            official = fetch(t["url"], odir / f"{binary}.apj")
            base = t["url"].rsplit("/", 1)[0]
            res["git_version_commit"] = _git_version_commit(fetch(base + "/git-version.txt").decode("utf-8", "replace"))
            try:
                res["firmware_version"] = fetch(base + "/firmware-version.txt").decode("utf-8", "replace").strip()
            except ToolError:
                res["firmware_version"] = None
            official_elf = None
            try:
                official_elf = fetch(base + f"/{binary}.elf", odir / f"{binary}.elf")
                res["romfs_official"] = romfs_check(official_elf)
                res["paths_official"] = build_paths(official_elf)
            except (ToolError, ElfError) as e:
                res["romfs_official"] = {"error": str(e)[-300:]}
            stage = "build"
            b = build(trees[t["build_commit"]], t["board"], g["vehicle"], tc_bin, work / "logs" / gid, jobs,
                      out=out_dir)
            res.update(configure_seconds=b["configure_seconds"], build_seconds=b["build_seconds"])
            stage = "compare"
            res.update(compare(official, b["apj"].read_bytes()))
            elf = b["apj"].with_suffix("")
            try:
                rebuilt_elf = elf.read_bytes()
                res["romfs"] = romfs_info(rebuilt_elf)
                res["paths"] = build_paths(rebuilt_elf)
            except (ElfError, OSError) as e:
                res["romfs"] = {"error": str(e)[-300:]}
            if res["verdict"] == "not-reproducible":
                stage = "diagnose"
                try:
                    if official_elf is None:
                        raise ToolError("официальный ELF не загружен")
                    res["diagnosis"] = diagnose(official_elf, elf.read_bytes())
                except (ToolError, ElfError, OSError) as e:
                    res["diagnosis"] = {"cause": "unavailable", "error": str(e)[-500:]}
            res["finished"] = now_utc()
            wjson(out / f"{t['board']}.intoto.json", statement(res, tc))
        except (ToolError, ApjError, OSError) as e:
            res.update(verdict="error", stage=stage, error=str(e)[-2000:], finished=now_utc())
        except Exception as e:                       # непредвиденное: записать и перейти к следующей плате
            res.update(verdict="error", stage=stage, error=f"{type(e).__name__}: {e}"[-2000:], finished=now_utc())
        wjson(out / f"{t['board']}.json", res)
        log(f"  → {res['verdict']}")
        done.append(res)
        if not keep:
            for tree in trees.values():
                clean(tree, out_dir)
    env["finished"] = now_utc()
    wjson(out / "_group.json", env)
    return done
