"""Postgres connections from DATABASE_URL and DIRECT_URL."""

import os
from urllib.parse import parse_qs, unquote, urlparse

import psycopg

from src.logging_config import get_logger

logger = get_logger(__name__)

_URLS = ("DATABASE_URL", "DIRECT_URL")


def postgres_settings() -> dict[str, dict[str, str]]:
    """Parse DATABASE_URL and DIRECT_URL from the environment.

    DATABASE_URL is the application pooler connection. DIRECT_URL is the
    session connection. The password is taken out of the URL so characters
    such as ``%`` and ``+`` stay literal.

    Raises
    ------
    RuntimeError
        When a variable is missing or the URL is not a usable Postgres URL.
    """
    missing = [name for name in _URLS if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError(
            "Faltan variables de entorno para la base de datos: " + ", ".join(missing)
        )

    return {name: _parse_postgres_url(name, os.environ[name]) for name in _URLS}


def connect_postgres(which: str = "DATABASE_URL") -> psycopg.Connection:
    """Open a Postgres connection and log that it succeeded.

    ``which`` selects ``DATABASE_URL`` or ``DIRECT_URL``. SSL is required
    unless the URL sets ``sslmode``. Transaction-mode pooler URLs
    (``pgbouncer=true`` or port 6543) disable prepared statements.
    """
    if which not in _URLS:
        raise RuntimeError(
            f"Conexión desconocida {which!r}. Usa DATABASE_URL o DIRECT_URL"
        )

    settings = postgres_settings()[which]
    kwargs: dict[str, object] = {
        "host": settings["host"],
        "port": int(settings["port"]),
        "dbname": settings["dbname"],
        "user": settings["user"],
        "password": settings["password"],
        "sslmode": settings["sslmode"],
        "connect_timeout": 10,
    }
    if settings["pgbouncer"] == "true":
        kwargs["prepare_threshold"] = None

    try:
        conn = psycopg.connect(**kwargs)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception as exc:
        logger.error(
            "No se pudo conectar a la base de datos %s en %s:%s (%s): %s",
            settings["dbname"],
            settings["host"],
            settings["port"],
            which,
            exc,
        )
        raise

    logger.info(
        "Conectado a la base de datos %s en %s:%s (%s)",
        settings["dbname"],
        settings["host"],
        settings["port"],
        which,
    )
    return conn


def _parse_postgres_url(name: str, url: str) -> dict[str, str]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError(f"{name} debe usar el esquema postgresql")

    dbname = unquote(parsed.path.lstrip("/"))
    password = unquote(parsed.password) if parsed.password else ""
    if not parsed.hostname or not parsed.username or not password or not dbname:
        raise RuntimeError(
            f"{name} está incompleta: hace falta usuario, contraseña, host y base de datos"
        )

    query = parse_qs(parsed.query)
    sslmode = query.get("sslmode", ["require"])[0].strip() or "require"
    pgbouncer = query.get("pgbouncer", ["false"])[0].strip().lower()
    port = parsed.port or 5432
    transaction_pool = pgbouncer in {"1", "true", "yes"} or port == 6543

    return {
        "host": parsed.hostname,
        "port": str(port),
        "dbname": dbname,
        "user": unquote(parsed.username),
        "password": password,
        "sslmode": sslmode,
        "pgbouncer": "true" if transaction_pool else "false",
    }
