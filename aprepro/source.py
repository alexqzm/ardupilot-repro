"""Исходный код ArduPilot на заданном коммите со всеми подмодулями.

Коммит забирается по SHA (git fetch --depth 1 origin <sha>), а не по имени тега: проверяется
ровно та ревизия, которую указывают официальные файлы или тег. Все подмодули дерева, включая
вложенные, инициализируются на закреплённых в дереве коммитах, неглубоко: набор нужных сборке
подмодулей меняется от выпуска к выпуску (в 4.4 — modules/uavcan, в 4.6 — modules/littlefs и т. д.).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .plan import AP_REPO
from .util import ToolError, git


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
    git("submodule", "update", "-q", "--init", "--recursive", "--depth", "1", cwd=dest, log=log)
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
