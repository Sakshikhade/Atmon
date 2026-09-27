"""Tests for Postgres connections loaded from DATABASE_URL and DIRECT_URL."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.postgres_db import connect_postgres, postgres_settings

_ENV = {
    "DATABASE_URL": (
        "postgresql://postgres.example:p%+secret@aws-0-us-west-2.pooler.supabase.com"
        ":6543/postgres?pgbouncer=true"
    ),
    "DIRECT_URL": (
        "postgresql://postgres.example:p%+secret@aws-0-us-west-2.pooler.supabase.com"
        ":5432/postgres"
    ),
}


def test_postgres_settings_reads_both_urls(monkeypatch):
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)

    settings = postgres_settings()

    assert settings["DATABASE_URL"] == {
        "host": "aws-0-us-west-2.pooler.supabase.com",
        "port": "6543",
        "dbname": "postgres",
        "user": "postgres.example",
        "password": "p%+secret",
        "sslmode": "require",
        "pgbouncer": "true",
    }
    assert settings["DIRECT_URL"]["port"] == "5432"
    assert settings["DIRECT_URL"]["pgbouncer"] == "false"
    assert settings["DIRECT_URL"]["password"] == "p%+secret"


def test_postgres_settings_reports_missing_urls(monkeypatch):
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        postgres_settings()


def test_postgres_settings_rejects_a_non_postgres_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://user:secret@localhost/postgres")
    monkeypatch.setenv("DIRECT_URL", _ENV["DIRECT_URL"])

    with pytest.raises(RuntimeError, match="DATABASE_URL debe usar el esquema postgresql"):
        postgres_settings()


def test_connect_postgres_uses_database_url(monkeypatch, caplog):
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)

    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with (
        caplog.at_level(logging.INFO, logger="src.postgres_db"),
        patch("src.postgres_db.psycopg.connect", return_value=conn) as connect,
    ):
        assert connect_postgres() is conn

    connect.assert_called_once_with(
        host="aws-0-us-west-2.pooler.supabase.com",
        port=6543,
        dbname="postgres",
        user="postgres.example",
        password="p%+secret",
        sslmode="require",
        connect_timeout=10,
        prepare_threshold=None,
    )
    cursor.execute.assert_called_once_with("SELECT 1")
    assert (
        "Conectado a la base de datos postgres en "
        "aws-0-us-west-2.pooler.supabase.com:6543 (DATABASE_URL)"
    ) in caplog.text


def test_connect_postgres_can_use_direct_url(monkeypatch):
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)

    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("src.postgres_db.psycopg.connect", return_value=conn) as connect:
        assert connect_postgres("DIRECT_URL") is conn

    assert connect.call_args.kwargs["port"] == 5432
    assert "prepare_threshold" not in connect.call_args.kwargs
