"""Postgres connection using DB_* variables from the environment."""

import os

import psycopg

from src.logging_config import get_logger

logger = get_logger(__name__)

_REQUIRED = ("DB_HOST", "DB_PORT", "DB_DATABASE", "DB_USER", "DB_PASSWORD")


def postgres_settings() -> dict[str, str]:
    """Read the Postgres connection settings from the environment.

    Raises
    ------
    RuntimeError
        When any required variable is missing or blank.
    """
    missing = [name for name in _REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError(
            "Faltan variables de entorno para la base de datos: " + ", ".join(missing)
        )

    port = os.environ["DB_PORT"].strip()
    try:
        int(port)
    except ValueError as exc:
        raise RuntimeError(f"DB_PORT debe ser un número, se recibió {port!r}") from exc

    return {
        "host": os.environ["DB_HOST"].strip(),
        "port": port,
        "dbname": os.environ["DB_DATABASE"].strip(),
        "user": os.environ["DB_USER"].strip(),
        "password": os.environ["DB_PASSWORD"],
    }


def connect_postgres() -> psycopg.Connection:
    """Open a Postgres connection and log that it succeeded.

    Uses SSL, which Supabase requires. The password is passed as its own
    field so characters like ``%`` and ``+`` stay literal.
    """
    settings = postgres_settings()
    try:
        conn = psycopg.connect(
            host=settings["host"],
            port=int(settings["port"]),
            dbname=settings["dbname"],
            user=settings["user"],
            password=settings["password"],
            sslmode="require",
            connect_timeout=10,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception as exc:
        logger.error(
            "No se pudo conectar a la base de datos %s en %s:%s: %s",
            settings["dbname"],
            settings["host"],
            settings["port"],
            exc,
        )
        raise

    logger.info(
        "Conectado a la base de datos %s en %s:%s",
        settings["dbname"],
        settings["host"],
        settings["port"],
    )
    return conn
