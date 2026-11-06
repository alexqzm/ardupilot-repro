import base64
import gzip
import json
import struct
import zlib

import pytest

from aprepro import report
from aprepro.apj import ApjError, parse
from aprepro.attest import PREDICATE_TYPE, statement
from aprepro.compare import compare, regions
from aprepro.manifest import Manifest
from aprepro.diagnose import _deflate, build_paths, diagnose, located, unpack
from aprepro.plan import make, python_for, rank
from aprepro.run import _git_version_commit
from aprepro.source import required_toolchain


def apj(image: bytes, **meta) -> bytes:
    d = {"board_id": 140, "magic": "APJFWv1", "description": "Firmware for a STM32H743xx board",
         "image": base64.b64encode(zlib.compress(image, 9)).decode(), "extf_image": base64.b64encode(zlib.compress(b"", 9)).decode(),
         "summary": "CubeOrange", "version": "0.1", "image_size": len(image), "git_identity": "fb773567"}
    d.update(meta)
    return json.dumps(d, indent=4).encode()


IMG = bytes(range(256)) * 64 + b"ArduCopter V4.7.2 (fb773567)\x00" + bytes(1000)


# ---------------- apj ----------------
def test_parse_apj():
    a = parse(apj(IMG))
    assert a.image == IMG and a.extf_image == b"" and a.meta["git_identity"] == "fb773567"
    assert "image" not in a.meta and a.digests()["image_size"] == len(IMG)


def test_parse_extf_only_apj():
    d = json.loads(apj(IMG))
    d["extf_image"], d["image"] = d["image"], base64.b64encode(zlib.compress(b"", 9)).decode()
    a = parse(json.dumps(d).encode())
    assert a.image == b"" and a.extf_image == IMG and a.digests()["extf_size"] == len(IMG)
    res = {"vehicle": "Copter", "version": "4.4.4", "board": "SPRacingH7", "tag": "Copter-4.4.4",
           "build_commit": "d" * 40, "official_url": "https://x/arducopter.apj", "finished": "t",
           **compare(json.dumps(d).encode(), json.dumps(d).encode())}
    st = statement(res, {"name": "tc", "url": "https://tc", "sha256": "a" * 64})
    assert st["predicate"]["candidate"]["sha256"] == a.digests()["extf_sha256"] == st["predicate"]["rebuilt"]["sha256"]


@pytest.mark.parametrize("data", [b"not json", b"[]", json.dumps({"magic": "X"}).encode(),
                                  json.dumps({"magic": "APJFWv1", "image": "!!!"}).encode(),
                                  json.dumps({"magic": "APJFWv1"}).encode()])
def test_parse_rejects(data):
    with pytest.raises(ApjError):
        parse(data)


# ---------------- сверка ----------------
def test_regions():
    a = bytes(100)
    b = bytearray(a)
    b[10] = 1
    b[12] = 1                      # ближе MERGE_GAP — одна область
    b[60] = 1
    assert regions(a, bytes(b)) == [[10, 13], [60, 61]]
    assert regions(a, a + b"xyz") == [[100, 103]]
    big = bytes(10000)
    c = bytearray(big)
    c[9000] = 7                    # различие в дальнем блоке
    assert regions(big, bytes(c)) == [[9000, 9001]]


def test_compare_verdicts():
    same = compare(apj(IMG), apj(IMG))
    assert same["verdict"] == "reproducible" and same["meta_diff"] == {}
    meta = compare(apj(IMG), apj(IMG, build_time=1793990000))
    assert meta["verdict"] == "image-identical" and list(meta["meta_diff"]) == ["build_time"]
    other = IMG.replace(b"fb773567", b"92b0cd78")
    diff = compare(apj(IMG), apj(other, git_identity="92b0cd78"))
    assert diff["verdict"] == "not-reproducible"
    d = diff["image_diff"]
    assert d["regions"] == 1 and d["differing_bytes"] == 8
    assert any("fb773567" in s for s in d["first_regions"][0]["official_strings"])
    assert d["hints"]["git_identity"] == {"official": 1, "rebuilt": 1}


# ---------------- манифест и план ----------------
def manifest(tmp_path):
    entries = []
    for board in ["CubeOrange", "Pixhawk6X", "Alpha", "Bravo", "Charlie", "Delta", "Echo"]:
        for fmt, name in (("apj", "arducopter.apj"), ("hex", "arducopter_with_bl.hex")):
            entries.append({"vehicletype": "Copter", "platform": board, "format": fmt,
                            "mav-firmware-version-type": "STABLE-4.7.2", "git-sha": "f" * 40,
                            "url": f"https://firmware.ardupilot.org/Copter/stable-4.7.2/{board}/{name}"})
    entries.append({"vehicletype": "Copter", "platform": "CubeOrange-heli", "format": "apj",
                    "mav-firmware-version-type": "STABLE-4.7.2", "git-sha": "f" * 40,
                    "url": "https://firmware.ardupilot.org/Copter/stable-4.7.2/CubeOrange-heli/arducopter-heli.apj"})
    entries.append({"vehicletype": "Copter", "platform": "CubeOrange", "format": "apj",
                    "mav-firmware-version-type": "STABLE-4.6.3", "git-sha": "3" * 40,
                    "url": "https://firmware.ardupilot.org/Copter/stable-4.6.3/CubeOrange/arducopter.apj"})
    p = tmp_path / "manifest.json.gz"
    p.write_bytes(gzip.compress(json.dumps({"format-version": "1.0.0", "firmware": entries}).encode()))
    return Manifest.load(p)


def test_manifest_filters(tmp_path):
    m = manifest(tmp_path)
    assert m.stable_versions("Copter") == ["4.6.3", "4.7.2"]
    boards = m.apj("Copter", "4.7.2")
    assert "CubeOrange-heli" not in boards and len(boards) == 7


def test_plan_is_deterministic(tmp_path):
    m = manifest(tmp_path)
    tags = {"Copter-4.7.2": "f" * 40, "Copter-4.6.3": "9" * 40}
    kw = dict(seed="s1", sample=2, panel=["CubeOrange", "Pixhawk6X", "Missing"], tags=tags)
    p1 = make(m, [("Copter", "4.7.2")], tag_groups=[("Copter", "4.6.3")], **kw)
    p2 = make(m, [("Copter", "4.7.2")], tag_groups=[("Copter", "4.6.3")], **kw)
    g = p1["groups"][0]
    assert [t["board"] for t in g["targets"]] == [t["board"] for t in p2["groups"][0]["targets"]]
    assert [t["role"] for t in g["targets"]] == ["panel", "panel", "sample", "sample"]
    expected = sorted(["Alpha", "Bravo", "Charlie", "Delta", "Echo"], key=lambda b: rank("s1", "Copter", "4.7.2", b))[:2]
    assert sorted(t["board"] for t in g["targets"][2:]) == sorted(expected)
    tg = p1["groups"][1]
    assert tg["id"] == "Copter-4.6.3-tag" and tg["build_from"] == "tag"
    assert [t["board"] for t in tg["targets"]] == ["CubeOrange"]
    assert tg["targets"][0]["build_commit"] == "9" * 40 and tg["targets"][0]["manifest_commit"] == "3" * 40
    with pytest.raises(ValueError):
        make(m, [("Copter", "9.9.9")], **kw)


# ---------------- прочее ----------------
def test_git_version_and_toolchain(tmp_path):
    assert _git_version_commit("commit abc123\nAuthor: x\n") == "abc123"
    assert _git_version_commit("garbage") is None
    d = tmp_path / "Tools" / "environment_install"
    d.mkdir(parents=True)
    (d / "install-prereqs-ubuntu.sh").write_text('  ARM_ROOT="gcc-arm-none-eabi-10-2020-q4-major"\n')
    assert required_toolchain(tmp_path) == "gcc-arm-none-eabi-10-2020-q4-major"


def test_statement_and_report():
    res = {"vehicle": "Copter", "version": "4.7.2", "board": "CubeOrange", "tag": "Copter-4.7.2",
           "build_commit": "f" * 40, "manifest_commit": "f" * 40, "git_version_commit": "f" * 40,
           "firmware_version": "4.7.2-FIRMWARE_VERSION_TYPE_OFFICIAL", "official_url": "https://x/arducopter.apj",
           "finished": "2026-11-06T00:00:00Z", "role": "sample", "group": "Copter-4.7.2", "build_from": "release",
           **compare(apj(IMG), apj(IMG))}
    st = statement(res, {"name": "tc", "url": "https://tc", "sha256": "a" * 64})
    assert st["predicateType"] == PREDICATE_TYPE and st["predicate"]["result"] == "MATCH"
    assert st["predicate"]["rebuilt"]["sha256"] == st["predicate"]["candidate"]["sha256"]
    plan = {"created": "2026-11-06T00:00:00Z", "manifest": {"sha256": "b" * 64}, "toolchain": "tc",
            "groups": [{"id": "Copter-4.7.2", "build_from": "release", "tag_commit": "f" * 40, "boards_in_release": 7,
                        "targets": [{"build_commit": "f" * 40}]}]}
    assert IMG.find(b"apfwver") < 0          # синтетический образ без AP_FWVersion: версия не определяется
    s = report.summary(plan, [res])
    assert s["sample"] == {"n": 1, "ok": 1, "wilson95": [round(report.wilson(1, 1)[0], 4), 1.0]}
    md = report.markdown(plan, "p", [res])
    assert "| Copter-4.7.2 | CubeOrange | выборка | совпадает побитово |" in md
    assert "| Copter-4.7.2 | `ffffffff` | `ffffffff` | 4.7.2-FIRMWARE_VERSION_TYPE_OFFICIAL |" in md
    block = report.readme_block(plan, [res])
    readme = "a\n" + report.README_BEGIN + "\nстарое\n" + report.README_END + "\nb\n"
    new = report.readme_with_block(readme, block)
    assert new.startswith("a\n" + report.README_BEGIN) and "пересобранные из коммитов выпусков: 1;" in new and "старое" not in new


def test_wilson():
    lo, hi = report.wilson(100, 100)
    assert 0.96 < lo < 0.97 and hi == pytest.approx(1.0)
    lo, hi = report.wilson(0, 10)
    assert lo == pytest.approx(0.0, abs=1e-12) and 0.27 < hi < 0.28


def test_romfs_unpack_formats():
    data = bytes(range(256)) * 40 + b"defaults" * 50
    g = gzip.compress(data, compresslevel=9, mtime=0)            # как embed.py до 4.5
    fmt, content, payload, mem = unpack(g)
    assert fmt == "gzip" and content == data and mem == 8 and _deflate(data, 8) == payload
    raw = _deflate(data, 9)                                      # как embed.py с 4.6
    assert unpack(raw)[:2] == ("deflate", data)
    assert unpack(b"plain text, not compressed at all") is None


def test_python_for_release():
    assert python_for("4.4.4") == "3.10" and python_for("4.4.0") == "3.10"
    assert python_for("4.5.7") == "3.11" and python_for("4.7.2") == "3.11"


def elf32(data: bytes, flags: int = 6) -> bytes:
    """Минимальный ELF32 little-endian: одна секция PROGBITS (по умолчанию ALLOC|EXECINSTR) с data."""
    hdr = bytearray(52)
    hdr[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<I", hdr, 0x20, 52 + len(data))
    struct.pack_into("<HH", hdr, 0x2E, 40, 2)
    sh = bytes(40) + struct.pack("<IIIIIIIIII", 0, 1, flags, 0x08000000, 52, len(data), 0, 0, 4, 0)
    return bytes(hdr) + data + sh


def test_build_paths_and_cause():
    tail = b"\0/mnt/workspace/GCC-10-pipeline/src/newlib/libc/stdlib/rand.c\0/APM/scripts/modules/?.lua\0code"
    official = elf32(b"\0/mnt/srv/APM/APM/modules/lwip/src/core/tcp.c\0/mnt/srv/APM/APM/libraries/AP_Networking/"
                     b"lwip_hal/arch/sys_arch.cpp\0" + tail)
    rebuilt = elf32(b"\0/home/runner/src/modules/lwip/src/core/tcp.c\0/home/runner/src/libraries/AP_Networking/"
                    b"lwip_hal/arch/sys_arch.cpp\0" + tail)
    assert build_paths(official) == {"tree": 2, "prefixes": {"/mnt/srv/APM/APM": 2}, "other": 1, "dirs": []}
    assert build_paths(elf32(b"\0/mnt/srv/APM/APM/modules/lwip/src/core/tcp.c\0", flags=0))["tree"] == 0  # не ALLOC
    d = diagnose(official, rebuilt)
    assert d["cause"] == "build-path" and d["build_paths"]["rebuilt"]["prefixes"] == {"/home/runner/src": 2}
    assert diagnose(official, official)["cause"] == "unknown"          # путь тот же — путь не причина
    assert not located({"prefixes": {"../..": 24}, "dirs": []})       # сборка в build/ дерева


def test_debug_info_paths():
    # отладочная информация в .text у плат с прошивкой во внешней флеш-памяти (SPRacingH7)
    official = elf32(b"\0../../../../../../mnt/srv/APM/APM/modules/ChibiOS/os/rt/include\0"
                     b"/home/autotest/build/tmp/binaries.build/SPRacingH7\0"
                     b"/home/autotest/arm-gcc/gcc-arm-none-eabi-10-2020-q4-major/arm-none-eabi/include/sys\0/APM/LOGS\0"
                     b"\x01/HBF/K9F\0")                                     # случайные байты, похожие на путь
    rebuilt = elf32(b"\0../../modules/ChibiOS/os/rt/include\0/home/runner/src/build/SPRacingH7\0"
                    b"/home/runner/tc/gcc-arm-none-eabi-10-2020-q4-major/arm-none-eabi/include/sys\0/APM/LOGS\0")
    bp = build_paths(official)
    assert bp["prefixes"] == {"../../../../../../mnt/srv/APM/APM": 1} and len(bp["dirs"]) == 2 and located(bp)
    assert build_paths(rebuilt)["prefixes"] == {"../..": 1}
    assert diagnose(official, rebuilt)["cause"] == "build-path"


def test_report_variants(tmp_path):
    plan = {"created": "t", "manifest": {"sha256": "b" * 64}, "toolchain": "tc",
            "groups": [{"id": "Copter-4.5.7", "build_from": "release", "tag_commit": "f" * 40, "boards_in_release": 9,
                        "targets": [{"build_commit": "f" * 40}]}]}
    base = {"vehicle": "Copter", "version": "4.5.7", "board": "Pixhawk6X", "tag": "Copter-4.5.7", "group": "Copter-4.5.7",
            "build_commit": "f" * 40, "manifest_commit": "f" * 40, "role": "panel", "build_from": "release",
            "paths_official": {"tree": 24, "prefixes": {"/mnt/srv": 24}, "other": 5, "dirs": []}}
    main = {**base, **compare(apj(IMG), apj(IMG[:-1] + b"x")), "src_path": "/home/runner/src"}
    var = {**base, **compare(apj(IMG), apj(IMG)), "src_path": "/mnt/srv", "out_dir": "/tmp/out", "python_zlib": "1.3"}
    for root, row in ((tmp_path, main), (tmp_path / "build-path", var)):
        (root / "p" / "Copter-4.5.7").mkdir(parents=True)
        (root / "p" / "Copter-4.5.7" / "Pixhawk6X.json").write_text(json.dumps(row))
    rows, extra = report.load(tmp_path, "p"), report.variants(tmp_path, "p")
    assert list(extra) == ["build-path"] and len(rows) == 1
    s = report.summary(plan, rows)
    assert s["paths_official"]["located"] == 1 and s["paths_official"]["file_macro"]["n"] == 1
    assert s["paths_official"]["reproducible"] == 0
    md = report.markdown(plan, "p", rows, None, extra)
    assert "встроены в 1 из 1 проверенных" in md
    assert "| дерево в каталоге сборочного сервера, сборка вне дерева (waf --out) | Copter-4.5.7 | Pixhawk6X | " \
           "не совпадает | совпадает побитово | 1.3 | `/mnt/srv`; `--out /tmp/out` |" in md

