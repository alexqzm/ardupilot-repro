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
from .diagnose import ElfError, diagnose, romfs_info
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
              keep: bool = False, log=print) -> list:
    g = group_of(plan, gid)
    work, out = Path(work), Path(results) / plan_name / gid
    targets = [t for t in g["targets"] if not boards or t["board"] in boards]
    if boards and len(targets) != len(set(boards)):
        raise ToolError(f"не все платы {sorted(boards)} есть в группе {gid}")
    tc_name = plan["toolchain"]
    tc_bin = toolchain.ensure(tc_name, work / "toolchain")
    tc = {"name": tc_name, **toolchain.spec(tc_name), "gcc": toolchain.version(tc_bin)}
    env = {"started": now_utc(), "tool": f"ardupilot-repro {__version__}", "python": sys.version.split()[0],
           "python_zlib": zlib.ZLIB_RUNTIME_VERSION, "platform": platform.platform(), "cpus": os.cpu_count(),
           "toolchain": tc,
           "github_run": os.environ.get("GITHUB_RUN_ID"), "sources": {}}
    trees = {}
    for commit in sorted({t["build_commit"] for t in targets}):
        log(f"исходники {commit[:12]}")
        tree = work / "src" / commit[:12]
        desc = prepare(commit, tree, log=work / "logs" / f"source-{commit[:12]}.log")
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
               "build_commit": t["build_commit"], "python_zlib": zlib.ZLIB_RUNTIME_VERSION, "started": now_utc()}
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
            stage = "build"
            b = build(trees[t["build_commit"]], t["board"], g["vehicle"], tc_bin, work / "logs" / gid, jobs)
            res.update(configure_seconds=b["configure_seconds"], build_seconds=b["build_seconds"])
            stage = "compare"
            res.update(compare(official, b["apj"].read_bytes()))
            elf = b["apj"].with_suffix("")
            try:
                res["romfs"] = romfs_info(elf.read_bytes())
            except (ElfError, OSError) as e:
                res["romfs"] = {"error": str(e)[-300:]}
            if res["verdict"] == "not-reproducible":
                stage = "diagnose"
                try:
                    official_elf = fetch(base + f"/{binary}.elf", odir / f"{binary}.elf")
                    res["diagnosis"] = diagnose(official_elf, elf.read_bytes())
                except (ToolError, ElfError, OSError) as e:
                    res["diagnosis"] = {"cause": "unavailable", "error": str(e)[-500:]}
            res["finished"] = now_utc()
            wjson(out / f"{t['board']}.intoto.json", statement(res, tc))
        except (ToolError, ApjError, OSError) as e:
            res.update(verdict="error", stage=stage, error=str(e)[-2000:], finished=now_utc())
        wjson(out / f"{t['board']}.json", res)
        log(f"  → {res['verdict']}")
        done.append(res)
        if not keep:
            for tree in trees.values():
                clean(tree, t["board"])
    env["finished"] = now_utc()
    wjson(out / "_group.json", env)
    return done
