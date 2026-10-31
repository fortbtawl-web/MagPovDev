#!/usr/bin/env python3
"""SpirittVPN launcher for Linux hosting (H1Cloud: PY_FILE=run.py) and local Windows testing.

    python run.py            start API (Mini App, admin panel, webhooks, worker) + Telegram bot
    python run.py check      only validate configuration, database and migrations
    python run.py migrate    apply database migrations (make a backup first!)

Environment:
    RUN_COMPONENTS=all|bot|api   which processes to start (default: all)
    AUTO_MIGRATE=true            apply pending migrations on start (an empty database is always initialised)

The API and the bot run as two separate child processes; if either exits, the other is stopped
and the launcher exits with an error so the hosting panel can restart it.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # relative paths (.env, assets/, frontend/dist, website/) resolve from the project root
sys.path.insert(0, str(ROOT))

LOCK_PATH = ROOT / "run.lock"
_lock_handle = None


def say(msg: str) -> None:
    print(f"[run.py] {msg}", flush=True)


def fail(msg: str, code: int = 1) -> None:
    print(f"[run.py] ОШИБКА: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def acquire_single_instance_lock() -> None:
    """Two pollers on one bot token break each other (Telegram 409 Conflict)."""
    global _lock_handle
    _lock_handle = open(LOCK_PATH, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            _lock_handle.seek(0)
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fail("SpirittVPN уже запущен в этой папке (занят run.lock). Остановите предыдущий запуск.", 3)
    _lock_handle.seek(0)
    _lock_handle.truncate()
    _lock_handle.write(str(os.getpid()))
    _lock_handle.flush()


def check_python() -> None:
    if sys.version_info < (3, 12):
        fail(f"Нужен Python 3.12+, сейчас {sys.version.split()[0]}.")
    try:
        import aiogram  # noqa: F401
        import alembic  # noqa: F401
        import asyncpg  # noqa: F401
        import fastapi  # noqa: F401
        import sqlalchemy  # noqa: F401
    except ImportError as exc:
        fail(f"Не установлены зависимости ({exc.name}). Выполните: pip install -r requirements.txt")


def check_config(components: str):
    from backend.config import config_warnings, get_settings, missing_required

    if not (ROOT / ".env").exists() and not os.environ.get("BOT_TOKEN"):
        fail("Нет файла .env. Скопируйте .env.example в .env и заполните его.")
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 - pydantic validation error
        fail(f"Некорректный .env: {exc}")
    problems: list[str] = []
    for comp in (["bot", "api"] if components == "all" else [components]):
        problems += missing_required(settings, comp)
    problems = list(dict.fromkeys(problems))
    if problems:
        fail("Не заданы обязательные переменные в .env:\n  - " + "\n  - ".join(problems), 2)
    for w in config_warnings(settings):
        say(f"предупреждение: {w}")
    return settings


async def _db_state(url: str) -> tuple[str | None, bool]:
    """(current alembic revision, database has any tables)."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            tables = (await conn.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"))).scalar()
            rev = None
            if (await conn.execute(text("SELECT to_regclass('public.alembic_version')"))).scalar():
                rev = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            return rev, bool(tables)
    finally:
        await engine.dispose()


def migration_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()


def run_migrations() -> None:
    say("применяю миграции базы данных (alembic upgrade head)…")
    rc = subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT)
    if rc != 0:
        fail("миграции не применились — смотрите сообщение выше.", 4)


def check_database(settings, *, allow_migrate: bool) -> None:
    try:
        current, has_tables = asyncio.run(_db_state(settings.database_url))
    except Exception as exc:  # noqa: BLE001
        fail(f"нет подключения к PostgreSQL: {type(exc).__name__}: {exc}\nПроверьте DATABASE_URL и что сервер БД запущен.")
    head = migration_head()
    if current == head:
        say(f"база данных актуальна (миграция {head})")
        return
    if current is None and not has_tables:
        say("база данных пустая — создаю таблицы")
        run_migrations()
        return
    if current is None:
        fail("в базе есть таблицы, но нет alembic_version. Это не база SpirittVPN или она повреждена — проверьте DATABASE_URL.", 4)
    if allow_migrate:
        run_migrations()
        return
    fail(
        f"база данных устарела (сейчас {current}, нужно {head}).\n"
        "Сделайте резервную копию (scripts/backup_db.sh), затем выполните: python run.py migrate\n"
        "или один раз запустите с AUTO_MIGRATE=true.",
        4,
    )


class TunnelKeeper(threading.Thread):
    """Runs the Mini App tunnel in the background and keeps it alive.

    Cloudflare quick tunnels are rate limited per IP (HTTP 429, common on shared hosting), so the bot must not wait
    for them: it starts at once, this thread retries with growing pauses, and the address reaches the bot through
    logs/webapp_url.txt (backend/webapp_url.py) as soon as one is issued. If the tunnel dies later, it is restarted.
    """

    def __init__(self, port: int):
        super().__init__(daemon=True, name="tunnel")
        self.port = port
        self.stop_event = threading.Event()
        self.proc = None

    def run(self) -> None:
        from backend import tunnel, webapp_url

        failures = 0
        while not self.stop_event.is_set():
            try:
                self.proc, url = tunnel._start_once(self.port, ROOT / "bin", timeout=60)
            except tunnel.TunnelError as exc:
                failures += 1
                pause = min(300, 30 * failures)
                say(f"туннель Mini App не поднялся (попытка {failures}): {str(exc)[-160:]} — повтор через {pause} с, бот работает")
                self.stop_event.wait(pause)
                continue
            failures = 0
            webapp_url.publish(url)
            say(f"Mini App доступен по временному HTTPS-адресу: {url}")
            while not self.stop_event.is_set() and self.proc.poll() is None:
                self.stop_event.wait(5)
            webapp_url.clear()
            if not self.stop_event.is_set():
                say("туннель Mini App оборвался — поднимаю заново")
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()


def start_tunnel(settings) -> "TunnelKeeper | None":
    if settings.webapp_tunnel.strip().lower() != "cloudflared" or settings.webapp_url.startswith("https://"):
        return None
    from backend import webapp_url

    webapp_url.clear()  # an address from the previous run is dead
    keeper = TunnelKeeper(settings.api_port)
    keeper.start()
    say("туннель для Mini App запускается в фоне (бот стартует сразу)")
    return keeper


def supervise(components: str, settings=None) -> int:
    targets = {"api": [sys.executable, "-m", "backend"], "bot": [sys.executable, "-m", "bot"]}
    names = ["api", "bot"] if components == "all" else [components]
    procs: dict[str, subprocess.Popen] = {}
    stopping = False

    def stop(*_args) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    env = os.environ.copy()
    if components == "bot":
        # Background jobs normally live in the API process; without it the bot runs them.
        env["SPIRITT_WORKER_IN_BOT"] = "1"
    keeper = start_tunnel(settings) if settings is not None and components != "bot" else None
    for name in names:
        say(f"запускаю {name}: {' '.join(targets[name][1:])}")
        procs[name] = subprocess.Popen(targets[name], cwd=ROOT, env=env)
        if name == "api":
            time.sleep(2)  # let the API bind its port before the bot starts
    exit_code = 0
    while not stopping:
        for name, proc in procs.items():
            rc = proc.poll()
            if rc is not None:
                say(f"процесс {name} завершился с кодом {rc} — останавливаю остальные")
                exit_code = rc or 1
                stopping = True
                break
        time.sleep(1)
    if keeper is not None:
        keeper.stop_event.set()
        keeper.join(5)  # lets it terminate cloudflared before we exit
    for proc in procs.values():
        if proc.poll() is None:
            proc.terminate()  # SIGTERM: aiogram/uvicorn shut down gracefully
    deadline = time.time() + 25
    for name, proc in procs.items():
        try:
            proc.wait(timeout=max(1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            say(f"{name} не остановился вовремя — завершаю принудительно")
            proc.kill()
    say("остановлено")
    return exit_code


def adopt_host_port() -> None:
    """Panel hosting (Pterodactyl, H1Cloud) exposes only the allocated port, given as SERVER_PORT.

    Binding the API there makes the Mini App reachable from outside; on H1Cloud it is then served over HTTPS as
    https://s-<port>.<node>.h1clayd.click. An explicit API_PORT (env or .env) always wins.
    """
    port = os.environ.get("SERVER_PORT", "").strip()
    if not port.isdigit() or os.environ.get("API_PORT"):
        return
    if (ROOT / ".env").exists():
        from dotenv import dotenv_values

        if dotenv_values(ROOT / ".env").get("API_PORT"):
            return
    os.environ["API_PORT"] = port
    say(f"API и Mini App слушают выделенный хостингом порт {port} (SERVER_PORT)")
    say(f"постоянный HTTPS-адрес на H1Cloud: https://s-{port}.<нода>.h1clayd.click — впишите его в WEBAPP_URL")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "start"
    if command not in ("start", "check", "migrate"):
        fail("использование: python run.py [start|check|migrate]")
    components = os.environ.get("RUN_COMPONENTS", "all").strip().lower() or "all"
    if components not in ("all", "bot", "api"):
        fail("RUN_COMPONENTS должен быть all, bot или api")
    check_python()
    adopt_host_port()
    settings = check_config(components)
    if command == "migrate":
        check_database(settings, allow_migrate=True)
        return
    auto = os.environ.get("AUTO_MIGRATE", "").strip().lower() in ("1", "true", "yes", "on")
    check_database(settings, allow_migrate=auto)
    if command == "check":
        say("проверка пройдена")
        return
    acquire_single_instance_lock()
    sys.exit(supervise(components, settings))


if __name__ == "__main__":
    main()
