"""Тулчейн GNU Arm Embedded, которым ArduPilot собирает прошивки для STM32.

Архив берётся с firmware.ardupilot.org (оттуда же его ставит install-prereqs-ubuntu.sh) и
сверяется с закреплённым SHA-256. Контрольные суммы ArduPilot рядом с архивом не публикует,
поэтому значение в таблице измерено при первой загрузке (06.11.2026) и совпадает с тем, что
закреплено в scii-gate (сборка Copter-4.5.7 на раннере GitHub, 31.10.2026).
"""
from __future__ import annotations

import platform
import shutil
from pathlib import Path

from .util import ToolError, fetch, run, sha256_file

TOOLCHAINS = {
    "gcc-arm-none-eabi-10-2020-q4-major": {
        "x86_64": {"url": "https://firmware.ardupilot.org/Tools/STM32-tools/"
                          "gcc-arm-none-eabi-10-2020-q4-major-x86_64-linux.tar.bz2",
                   "sha256": "21134caa478bbf5352e239fbc6e2da3038f8d2207e089efc96c3b55f1edcd618"},
    },
}


def spec(name: str) -> dict:
    arch = platform.machine()
    try:
        return TOOLCHAINS[name][arch]
    except KeyError:
        raise ToolError(f"нет закреплённого тулчейна {name} для {arch}") from None


def ensure(name: str, root: Path) -> Path:
    """Каталог bin тулчейна; скачивает и проверяет архив, если его ещё нет."""
    root = Path(root)
    bindir = root / name / "bin"
    if (bindir / "arm-none-eabi-gcc").exists():
        return bindir
    s = spec(name)
    root.mkdir(parents=True, exist_ok=True)
    archive = root / f"{name}.tar.bz2"
    fetch(s["url"], archive, timeout=600)
    got = sha256_file(archive)
    if got != s["sha256"]:
        archive.unlink()
        raise ToolError(f"SHA-256 архива {name}: {got}, ожидался {s['sha256']}")
    shutil.rmtree(root / name, ignore_errors=True)
    run(["tar", "xjf", archive, "-C", root])
    archive.unlink()
    if not (bindir / "arm-none-eabi-gcc").exists():
        raise ToolError(f"в архиве {name} нет bin/arm-none-eabi-gcc")
    return bindir


def version(bindir: Path) -> str:
    out = run([Path(bindir) / "arm-none-eabi-gcc", "--version"]).stdout
    return out.splitlines()[0].strip() if out else ""
