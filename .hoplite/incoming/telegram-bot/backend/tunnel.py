"""Optional HTTPS for the Mini App without a domain: a Cloudflare quick tunnel to the local API.

Enabled with WEBAPP_TUNNEL=cloudflared. The pinned cloudflared binary is downloaded once into ./bin and verified
by SHA-256 before it is ever executed. A quick tunnel gets a new *.trycloudflare.com address on every start and has
no uptime guarantee, so for a permanent address use your own domain (WEBAPP_URL) instead.
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
import stat
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

VERSION = "2026.10.5"
BINARIES = {
    "x86_64": ("cloudflared-linux-amd64", "e1bc0cf084fd9e590554fe769a86b1db46701c3f847a43d902f7b974a4e16298"),
    "aarch64": ("cloudflared-linux-arm64", "72e0d05a67e09da87cf495e409a28c3cc5994dd23fc66a982eebea6a55923b7d"),
}
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_binary(directory: Path) -> Path:
    arch = platform.machine().lower()
    asset, digest = BINARIES.get({"amd64": "x86_64", "arm64": "aarch64"}.get(arch, arch), (None, None))
    if asset is None or platform.system() != "Linux":
        raise TunnelError(f"cloudflared: платформа {platform.system()}/{arch} не поддерживается")
    directory.mkdir(exist_ok=True)
    target = directory / f"cloudflared-{VERSION}"
    if target.exists() and _sha256(target) == digest:
        return target
    tmp = target.with_suffix(".part")
    url = f"https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/{asset}"
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise TunnelError(f"cloudflared: не удалось скачать ({type(exc).__name__})") from exc
    if _sha256(tmp) != digest:
        tmp.unlink(missing_ok=True)
        raise TunnelError("cloudflared: контрольная сумма не совпала, файл удалён")
    tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR)
    tmp.replace(target)
    return target


def start(port: int, directory: Path, *, timeout: float = 60.0, retries: int = 3, pause: float = 20.0) -> tuple[subprocess.Popen, str]:
    """Start the tunnel and return (process, public https URL).

    Quick tunnels are rate limited per IP (HTTP 429, especially on shared hosting), so a failed start is retried
    a few times with a growing pause before giving up.
    """
    last: TunnelError | None = None
    for attempt in range(retries):
        try:
            return _start_once(port, directory, timeout=timeout)
        except TunnelError as exc:
            last = exc
            # Printed so the host console shows why the Mini App address is late or missing.
            print(f"[run.py] туннель, попытка {attempt + 1} из {retries}: {exc}", flush=True)
            if attempt + 1 < retries:
                time.sleep(pause * (attempt + 1))
    assert last is not None
    raise last


def _start_once(port: int, directory: Path, *, timeout: float) -> tuple[subprocess.Popen, str]:
    binary = ensure_binary(directory)
    proc = subprocess.Popen(
        [str(binary), "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    found: list[str] = []
    tail: list[str] = []
    logs = directory.parent / "logs"
    logs.mkdir(exist_ok=True)

    def pump() -> None:  # keep draining stderr so the process never blocks on a full pipe
        assert proc.stderr is not None
        with (logs / "cloudflared.log").open("w", encoding="utf-8") as log:
            for count, line in enumerate(proc.stderr):
                if count < 300:  # the first lines hold everything needed for diagnostics
                    log.write(line)
                    log.flush()
                tail.append(line.strip()[:300])
                del tail[:-6]
                if not found and (m := URL_RE.search(line)):
                    found.append(m.group(0))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    deadline = time.time() + timeout
    while time.time() < deadline and not found:
        if proc.poll() is not None:
            reader.join(2)
            raise TunnelError(f"cloudflared завершился с кодом {proc.returncode}: " + " | ".join(tail[-3:]))
        time.sleep(0.3)
    if not found:
        proc.terminate()
        raise TunnelError("cloudflared: адрес туннеля не получен за отведённое время: " + " | ".join(tail[-3:]))
    return proc, found[0]
