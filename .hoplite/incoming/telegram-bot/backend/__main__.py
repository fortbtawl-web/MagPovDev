"""Entry point: python -m backend  (API + background worker)."""
import uvicorn

from backend.config import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run("backend.api.app:app", host=s.api_host, port=s.api_port, proxy_headers=s.trust_forwarded_for, log_level=s.log_level.lower())


if __name__ == "__main__":
    main()
