"""Tests for the Postgres connection loaded from DB_* environment variables."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.postgres_db import connect_postgres, postgres_settings

_ENV = {
    "DB_HOST": "db.example.supabase.co",
    "DB_PORT": "5432",
    "DB_DATABASE": "postgres",
    "DB_USER": "postgres",
    "DB_PASSWORD": "p%+secret",
}


def test_postgres_settings_reads_env(monkeypatch):
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)

    assert postgres_settings() == {
        "host": "db.example.supabase.co",
        "port": "5432",
        "dbname": "postgres",
        "user": "postgres",
        "password": "p%+secret",
    }


def test_postgres_settings_reports_missing_keys(monkeypatch):
    for key in _ENV:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError, match="DB_HOST"):
        postgres_settings()


def test_connect_postgres_logs_success(monkeypatch, caplog):
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
        host="db.example.supabase.co",
        port=5432,
        dbname="postgres",
        user="postgres",
        password="p%+secret",
        sslmode="require",
        connect_timeout=10,
    )
    cursor.execute.assert_called_once_with("SELECT 1")
    assert "Conectado a la base de datos postgres en db.example.supabase.co:5432" in caplog.text
