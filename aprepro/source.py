"""Исходный код ArduPilot на заданном коммите с подмодулями, нужными для сборки под ChibiOS.

Коммит забирается по SHA (git fetch --depth 1 origin <sha>), а не по имени тега: проверяется
ровно та ревизия, которую указывают официальные файлы или тег. Подмодули инициализируются на
закреплённых в дереве коммитах, неглубоко; waf при сборке проверяет их и дотягивает вложенные.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .plan import AP_REPO
from .util import ToolError, git

SUBMODULES = ["modules/waf", "modules/mavlink", "modules/ChibiOS", "modules/DroneCAN/DSDL",
              "modules/DroneCAN/pydronecan", "modules/DroneCAN/dronecan_dsdlc", "modules/DroneCAN/libcanard",
              "modules/lwip", "modules/littlefs", "modules/CrashDebug"]
RECURSIVE = {"modules/mavlink", "modules/CrashDebug"}


def prepare(commit: str, dest: Path, log: Path = None) -> dict:
    dest = Path(dest)
    if (dest / ".git").exists() and git("rev-parse", "HEAD", cwd=dest) == commit:
        return describe(dest)
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    git("init", "-q", cwd=dest)
    git("remote", "add", "origin", AP_REPO, cwd=dest)
    git("fetch", "-q", "--depth", "1", "origin", commit, cwd=dest, log=log)
    git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=dest, log=log)
    head = git("rev-parse", "HEAD", cwd=dest)
    if head != commit:
        raise ToolError(f"получен коммит {head}, ожидался {commit}")
    paths = set(git("config", "-f", ".gitmodules", "--get-regexp", r"\.path$", cwd=dest).split()[1::2])
    for sm in SUBMODULES:
        if sm not in paths:
            continue
        args = ["submodule", "update", "-q", "--init", "--depth", "1"]
        if sm in RECURSIVE:
            args.append("--recursive")
        git(*args, "--", sm, cwd=dest, log=log)
    return describe(dest)


def describe(tree: Path) -> dict:
    return {"commit": git("rev-parse", "HEAD", cwd=tree),
            "submodules": git("submodule", "status", "--recursive", cwd=tree).splitlines()}


def required_toolchain(tree: Path) -> str:
    """Тулчейн ARM, который задаёт Tools/environment_install/install-prereqs-ubuntu.sh дерева."""
    script = Path(tree) / "Tools" / "environment_install" / "install-prereqs-ubuntu.sh"
    for line in script.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("ARM_ROOT="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise ToolError(f"в {script} нет ARM_ROOT")
