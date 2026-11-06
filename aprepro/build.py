"""Сборка прошивки ArduPilot для платы штатным способом: waf configure --board, затем waf <изделие>.

Сборка всегда чистая: каталог build/<плата> удаляется перед configure, иначе waf не пересоздаст
шаги, входы которых не изменились (например, сжатие ROMFS другим интерпретатором). Опции сборки —
по умолчанию, как у официального сервера сборки: без --consistent-builds и без --build-dates.
Интерпретатор Python — тот же, что запускает инструмент (в нём стоят закреплённые пакеты из
ci/requirements-build.txt); каталог тулчейна и каталог интерпретатора ставятся первыми в PATH.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from .manifest import VEHICLES
from .util import env_with_path, run


def build(tree: Path, board: str, vehicle: str, toolchain_bin: Path, logdir: Path, jobs: int = None) -> dict:
    tree, logdir = Path(tree), Path(logdir)
    env = env_with_path(toolchain_bin, Path(sys.executable).parent)
    target = VEHICLES[vehicle]["target"]
    clean(tree, board)                      # только чистая сборка: waf не пересоздаёт неизменившиеся шаги
    t0 = time.monotonic()
    run([sys.executable, "./waf", "configure", "--board", board], cwd=tree, env=env,
        log=logdir / f"{board}-configure.log", timeout=1800)
    t1 = time.monotonic()
    run([sys.executable, "./waf", target, f"-j{jobs or os.cpu_count() or 2}"], cwd=tree, env=env,
        log=logdir / f"{board}-build.log", timeout=7200)
    t2 = time.monotonic()
    apj = tree / "build" / board / "bin" / (VEHICLES[vehicle]["binary"] + ".apj")
    return {"apj": apj, "configure_seconds": round(t1 - t0, 1), "build_seconds": round(t2 - t1, 1)}


def clean(tree: Path, board: str) -> None:
    shutil.rmtree(Path(tree) / "build" / board, ignore_errors=True)
