"""Файл прошивки ArduPilot .apj: JSON с образом, сжатым zlib и закодированным base64.

Образ (поле image) — то, что загрузчик пишет во внутреннюю флеш-память; extf_image — образ для
внешней флеш-памяти, если она есть у платы. Остальные поля — метаданные: идентификатор платы,
размеры, короткий хеш коммита (git_identity) и, если сборка включала даты, время сборки
(build_time). По умолчанию ArduPilot build_time не пишет, чтобы .apj одной ревизии и одного
компилятора совпадали.
"""
from __future__ import annotations

import base64
import json
import struct
import zlib
from dataclasses import dataclass

from .util import sha256_bytes

MAGIC = "APJFWv1"
IMAGE_KEYS = ("image", "extf_image")
# AP_FWVersion (libraries/AP_Common/AP_FWVersion.h): заголовок «apfwver» и контрольный байт, затем поля версии
FWVER_HEADER = struct.pack("<Q", 0x61706677766572FB)
FW_TYPES = {0: "DEV", 64: "ALPHA", 128: "BETA", 192: "RC", 255: "OFFICIAL"}


class ApjError(ValueError):
    """Файл не является прошивкой .apj."""


@dataclass
class Apj:
    meta: dict
    image: bytes
    extf_image: bytes
    raw: bytes

    def digests(self) -> dict:
        d = {"apj_sha256": sha256_bytes(self.raw), "image_sha256": sha256_bytes(self.image),
             "image_size": len(self.image), "fwversion": fwversion(self.image)}
        if self.extf_image:
            d.update(extf_sha256=sha256_bytes(self.extf_image), extf_size=len(self.extf_image))
        return d


def fwversion(image: bytes):
    """Версия, зашитая в образ: major.minor.patch и тип выпуска (FIRMWARE_VERSION_TYPE)."""
    off = image.find(FWVER_HEADER)
    if off < 0 or off + 20 > len(image):
        return None
    _hv, _ps, _r, vt, bt, _bst, maj, mnr, pat, fwt = struct.unpack_from("<HBBBBHBBBB", image, off + 8)
    return {"version": f"{maj}.{mnr}.{pat}", "fw_type": FW_TYPES.get(fwt, str(fwt)), "vehicle_type": vt,
            "board_type": bt}


def _image(d: dict, key: str) -> bytes:
    v = d.get(key)
    if not v:
        return b""
    try:
        return zlib.decompress(base64.b64decode(v))
    except (ValueError, zlib.error) as e:
        raise ApjError(f"поле {key} не распаковывается: {e}") from e


def parse(data: bytes) -> Apj:
    try:
        d = json.loads(data)
    except ValueError as e:
        raise ApjError(f"не JSON: {e}") from e
    if not isinstance(d, dict) or d.get("magic") != MAGIC:
        raise ApjError(f"нет признака {MAGIC}")
    image = _image(d, "image")
    if not image:
        raise ApjError("в файле нет образа")
    meta = {k: v for k, v in d.items() if k not in IMAGE_KEYS}
    return Apj(meta=meta, image=image, extf_image=_image(d, "extf_image"), raw=data)
