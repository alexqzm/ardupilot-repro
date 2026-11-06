"""Сборка прошивки ArduPilot для платы штатным способом: waf configure --board, затем waf <изделие>.

Сборка всегда чистая: каталог build/ удаляется целиком перед configure. Иначе waf не пересоздаст
шаги, входы которых не изменились (например, сжатие ROMFS другим интерпретатором), а после удаления
каталога одной платы откажется собирать следующую: в общем кэше конфигурации остаются её файлы. Опции сборки —
по умолчанию, как у официального сервера сборки: без --consistent-builds и без --build-dates.
Интерпретатор Python — тот же, что запускает инструмент (в нём стоят закреплённые пакеты из
ci/requirements-build.txt); каталог тулчейна и каталог интерпретатора ставятся первыми в PATH.

Каталог сборки по умолчанию — build/ внутри дерева. Параметр out задаёт его вне дерева (waf
configure --out), как делает Tools/scripts/build_binaries.py, которым собираются официальные
прошивки. От этого зависят пути исходников, которые waf передаёт компилятору: относительные
(../../modules/…), если у дерева и каталога сборки есть общий предок ниже корня, и абсолютные,
если общий предок — только «/» (waflib/Node.py, path_from). Через __FILE__ пути попадают в образ.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from .manifest import VEHICLES
from .util import env_with_path, run


def build(tree: Path, board: str, vehicle: str, toolchain_bin: Path, logdir: Path, jobs: int = None,
          out: Path = None) -> dict:
    tree, logdir = Path(tree), Path(logdir)
    env = env_with_path(toolchain_bin, Path(sys.executable).parent)
    target = VEHICLES[vehicle]["target"]
    clean(tree, out)                        # только чистая сборка
    t0 = time.monotonic()
    cmd = [sys.executable, "./waf", "configure", "--board", board]
    if out is not None:
        cmd += ["--out", str(Path(out).absolute())]
    run(cmd, cwd=tree, env=env, log=logdir / f"{board}-configure.log", timeout=1800)
    t1 = time.monotonic()
    run([sys.executable, "./waf", target, f"-j{jobs or os.cpu_count() or 2}"], cwd=tree, env=env,
        log=logdir / f"{board}-build.log", timeout=7200)
    t2 = time.monotonic()
    root = Path(out).absolute() if out is not None else tree / "build"
    apj = root / board / "bin" / (VEHICLES[vehicle]["binary"] + ".apj")
    return {"apj": apj, "configure_seconds": round(t1 - t0, 1), "build_seconds": round(t2 - t1, 1)}


def clean(tree: Path, out: Path = None) -> None:
    """Удалить каталог сборки целиком (вместе с кэшем конфигурации waf)."""
    shutil.rmtree(Path(tree) / "build", ignore_errors=True)
    if out is not None:
        shutil.rmtree(Path(out), ignore_errors=True)
