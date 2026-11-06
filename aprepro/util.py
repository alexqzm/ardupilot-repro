"""Общие функции: хеши, JSON, загрузки по HTTPS, запуск процессов."""
from __future__ import annotations

import datetime as dt
import hashlib
import http.client
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__

USER_AGENT = f"ardupilot-repro/{__version__} (+https://github.com/alexqzm/ardupilot-repro)"


class ToolError(RuntimeError):
    """Ошибка внешнего инструмента, сети или входных данных."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def wjson(path, obj) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def rjson(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url: str, dest=None, retries: int = 4, timeout: float = 120.0) -> bytes:
    """GET с повторами при сетевых сбоях и ответах 5xx; 4xx — сразу ошибка."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if dest is not None:
                dest = Path(dest)
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".part")
                tmp.write_bytes(data)
                tmp.replace(dest)
            return data
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise ToolError(f"{url}: HTTP {e.code}") from e
            last = e
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:   # обрыв, неполный ответ
            last = e
        time.sleep(2 ** attempt)
    raise ToolError(f"{url}: {last}")


def run(cmd, *, cwd=None, env=None, log=None, timeout=None, check=True) -> subprocess.CompletedProcess:
    """Запустить команду; вывод (stdout и stderr вместе) пишется в log, если он задан."""
    cmd = [str(c) for c in cmd]
    try:
        if log is not None:
            Path(log).parent.mkdir(parents=True, exist_ok=True)
            with open(log, "ab") as f:
                f.write(("$ " + " ".join(cmd) + "\n").encode())
                f.flush()
                r = subprocess.run(cmd, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
        else:
            r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ToolError(f"{Path(cmd[0]).name}: {e}") from e
    if check and r.returncode != 0:
        tail = ""
        if log is not None:
            tail = Path(log).read_text(encoding="utf-8", errors="replace")[-1500:]
        else:
            tail = (r.stderr or r.stdout or "")[-1500:]
        raise ToolError(f"{' '.join(cmd[:4])}: код {r.returncode}\n{tail}")
    return r


def git(*args, cwd=None, log=None, timeout=1800) -> str:
    r = run(["git", "-c", "advice.detachedHead=false", *args], cwd=cwd, log=log, timeout=timeout)
    return "" if log is not None else r.stdout.strip()


def env_with_path(*dirs) -> dict:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([*(str(d) for d in dirs if d), env.get("PATH", "")])
    return env
