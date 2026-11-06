"""Аттестация пересборки: утверждение in-toto v1 с предикатом пересборки SCII.

Предикат urn:scii:predicate:rebuild:v1 — тот, что гейт scii-gate проверяет у свидетельств s23
(воспроизводимая сборка) и s24 (независимая контрольная пересборка): исходный коммит, SHA-256
тулчейна, кто пересобирал, дайджест пересобранного образа, дайджест проверяемого (кандидата) и
итог MATCH или MISMATCH. Субъекты утверждения — официальные файлы: .apj и образ из него.

Подпись утверждения делает тот, кто пересобирал: в CI этого репозитория — бесключевой подписью
Sigstore (actions/attest), если при запуске включена публикация аттестаций.
"""
from __future__ import annotations

import os

from .manifest import VEHICLES

PREDICATE_TYPE = "urn:scii:predicate:rebuild:v1"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"


def builder_id() -> str:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ['GITHUB_REPOSITORY']}"
                f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '?')}")
    return "urn:ardupilot-repro:local"


def statement(result: dict, toolchain: dict) -> dict:
    binary = VEHICLES[result["vehicle"]]["binary"]
    off, reb = result["official"], result["rebuilt"]
    # кандидат — образ внутренней флеш-памяти, а у плат, где прошивка целиком во внешней, — образ extf
    key = "image_sha256" if off["image_size"] else "extf_sha256"
    return {
        "_type": STATEMENT_TYPE,
        "subject": [{"name": f"{result['board']}/{binary}.apj", "digest": {"sha256": off["apj_sha256"]}},
                    {"name": f"{result['board']}/{binary}.bin", "digest": {"sha256": off[key]}}],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "source": {"uri": f"git+https://github.com/ArduPilot/ardupilot@{result['build_commit']}",
                       "commit": result["build_commit"], "tag": result["tag"]},
            "toolchain": {"uri": toolchain["url"], "sha256": toolchain["sha256"], "name": toolchain["name"]},
            "builder": {"id": builder_id()},
            # от чего, кроме исходников и тулчейна, зависит результат: zlib интерпретатора (сжатие ROMFS)
            # и каталоги дерева, сборки и тулчейна (пути в образе: lwIP в 4.5–4.6, отладочная
            # информация у плат с прошивкой во внешней флеш-памяти)
            "environment": {"pythonZlib": result.get("python_zlib"), "srcPath": result.get("src_path"),
                            "outDir": result.get("out_dir"), "toolchainDir": result.get("toolchain_dir")},
            "board": result["board"], "vehicle": result["vehicle"], "version": result["version"],
            "candidate": {"sha256": off[key], "apj_sha256": off["apj_sha256"], "url": result["official_url"]},
            "rebuilt": {"sha256": reb.get(key), "apj_sha256": reb["apj_sha256"]},
            "result": "MATCH" if result["verdict"] in ("reproducible", "image-identical") else "MISMATCH",
            "verdict": result["verdict"],
            "finishedOn": result["finished"],
        },
    }
