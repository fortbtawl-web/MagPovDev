"""Current public HTTPS address of the Mini App.

A fixed WEBAPP_URL wins. Otherwise run.py's background tunnel publishes its address in logs/webapp_url.txt,
so the bot (a separate process) can pick it up as soon as the tunnel is up, without a restart.
"""
from __future__ import annotations

from pathlib import Path

URL_FILE = Path(__file__).resolve().parents[1] / "logs" / "webapp_url.txt"


def current(settings) -> str:
    fixed = (settings.webapp_url or "").strip()
    if fixed.startswith("https://"):
        return fixed
    try:
        url = URL_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return fixed
    return url if url.startswith("https://") else fixed


def publish(url: str) -> None:
    URL_FILE.parent.mkdir(exist_ok=True)
    tmp = URL_FILE.with_suffix(".tmp")
    tmp.write_text(url, encoding="utf-8")
    tmp.replace(URL_FILE)  # atomic: the bot never reads a half-written address


def clear() -> None:
    URL_FILE.unlink(missing_ok=True)
