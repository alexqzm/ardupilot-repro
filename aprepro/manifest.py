"""Манифест прошивок firmware.ardupilot.org: какие прошивки опубликованы и из какого коммита.

Манифест (manifest.json.gz) перечисляет файлы прошивок с изделием, платой, форматом, типом
выпуска (STABLE-x.y.z, BETA, DEV, OFFICIAL) и SHA коммита, из которого они собраны. Для
исследования берутся файлы .apj стабильных выпусков; варианты с другим именем файла (например,
arducopter-heli.apj) не входят в выборку изделия.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from .util import fetch, now_utc, sha256_bytes

MANIFEST_URL = "https://firmware.ardupilot.org/manifest.json.gz"
VEHICLES = {
    "Copter": {"target": "copter", "binary": "arducopter"},
    "Plane": {"target": "plane", "binary": "arduplane"},
    "Rover": {"target": "rover", "binary": "ardurover"},
}


class Manifest:
    def __init__(self, entries: list, meta: dict):
        self.entries, self.meta = entries, meta

    @classmethod
    def load(cls, path) -> "Manifest":
        data = Path(path).read_bytes()
        doc = json.loads(gzip.decompress(data))
        return cls(doc["firmware"], {"sha256": sha256_bytes(data), "size": len(data),
                                     "format_version": doc.get("format-version")})

    @classmethod
    def download(cls, dest) -> "Manifest":
        fetch(MANIFEST_URL, dest)
        m = cls.load(dest)
        m.meta.update(url=MANIFEST_URL, retrieved=now_utc())
        return m

    def stable_versions(self, vehicle: str) -> list:
        vs = {e["mav-firmware-version-type"][len("STABLE-"):] for e in self.entries
              if e.get("vehicletype") == vehicle and str(e.get("mav-firmware-version-type", "")).startswith("STABLE-")}
        return sorted(vs, key=lambda v: tuple(int(x) for x in v.split(".")))

    def apj(self, vehicle: str, version: str) -> dict:
        """Платы стабильного выпуска с файлом <бинарник>.apj: {плата: запись манифеста}."""
        name = "/" + VEHICLES[vehicle]["binary"] + ".apj"
        out = {}
        for e in self.entries:
            if (e.get("vehicletype") == vehicle and e.get("format") == "apj"
                    and e.get("mav-firmware-version-type") == f"STABLE-{version}" and e["url"].endswith(name)):
                out[e["platform"]] = e
        return dict(sorted(out.items()))
