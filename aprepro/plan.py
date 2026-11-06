"""План исследования: группы «изделие × выпуск» и платы в каждой.

План фиксируется до прогона и хранится в репозитории (plans/*.json). В каждую группу входят
платы постоянной панели (популярные платы на STM32F4, F7 и H7), если выпуск для них есть, и
случайная выборка из остальных плат выпуска. Выборка детерминирована: платы ранжируются по
SHA-256 строки «зерно:изделие:версия:плата», берутся первые k. Так выбор не зависит от версии
Python и проверяется кем угодно.

Группа «из тега» (build_from = tag) собирает те же платы из коммита тега выпуска, а не из коммита,
указанного в официальных файлах, — чтобы показать, что получит проверяющий, если возьмёт тег.
"""
from __future__ import annotations

import hashlib

from . import __version__
from .manifest import VEHICLES, Manifest
from .util import git, now_utc

AP_REPO = "https://github.com/ArduPilot/ardupilot.git"
PANEL = ["CubeOrange", "CubeOrangePlus", "Pixhawk6X", "MatekH743", "Pixhawk4", "CubeYellow", "CubeBlack",
         "MatekF405-Wing"]


def rank(seed: str, vehicle: str, version: str, board: str) -> str:
    return hashlib.sha256(f"{seed}:{vehicle}:{version}:{board}".encode()).hexdigest()


def tag_commits(tags: list) -> dict:
    """Коммиты тегов ArduPilot по git ls-remote (для аннотированных тегов — коммит, на который указывает тег)."""
    out = git("ls-remote", "--tags", AP_REPO, *[f"refs/tags/{t}" for t in tags] + [f"refs/tags/{t}^{{}}" for t in tags])
    found = {}
    for line in out.splitlines():
        sha, ref = line.split("\t")
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            found[name[:-3]] = sha                  # снятый (peeled) тег важнее
        else:
            found.setdefault(name, sha)
    return found


def make(manifest: Manifest, groups: list, *, seed: str, sample: int, panel=PANEL, tag_groups=(),
         toolchain: str = "gcc-arm-none-eabi-10-2020-q4-major", tags: dict = None) -> dict:
    """groups — список (изделие, версия); tag_groups — (изделие, версия), которые собрать ещё и из тега."""
    names = [f"{v}-{ver}" for v, ver in list(groups) + list(tag_groups)]
    tags = tags if tags is not None else tag_commits(sorted(set(names)))
    plan = {"schema": "ardupilot-repro/plan/v1", "created": now_utc(), "tool": f"ardupilot-repro {__version__}",
            "manifest": manifest.meta, "seed": seed, "sample_per_group": sample, "panel": list(panel),
            "toolchain": toolchain, "groups": []}
    for vehicle, version, build_from in [(v, ver, "release") for v, ver in groups] + \
                                        [(v, ver, "tag") for v, ver in tag_groups]:
        if vehicle not in VEHICLES:
            raise ValueError(f"неизвестное изделие {vehicle}")
        boards = manifest.apj(vehicle, version)
        if not boards:
            raise ValueError(f"в манифесте нет стабильного выпуска {vehicle} {version} с файлами .apj")
        tag = f"{vehicle}-{version}"
        chosen = [(b, "panel") for b in panel if b in boards]
        if build_from == "release":
            rest = sorted((rank(seed, vehicle, version, b), b) for b in boards if b not in panel)
            chosen += [(b, "sample") for _, b in rest[:sample]]
        targets = []
        for board, role in chosen:
            e = boards[board]
            commit = tags.get(tag) if build_from == "tag" else e["git-sha"]
            targets.append({"board": board, "role": role, "url": e["url"], "manifest_commit": e["git-sha"],
                            "build_commit": commit})
        plan["groups"].append({
            "id": tag + ("-tag" if build_from == "tag" else ""), "vehicle": vehicle, "version": version,
            "tag": tag, "tag_commit": tags.get(tag), "build_from": build_from, "boards_in_release": len(boards),
            "targets": targets})
    return plan
