"""Standalone Python HTTP server and REST API for AAMAS dashboard.

Serves the single-file HTML dashboard and provides JSON REST API endpoints
for recorded behavior episodes, statistics, and skeleton replay coordinates
stored in `data/outbox.db`.

Usage:
    python -m src.server --port 8000
    python src/server.py --port 8000 --db-path data/outbox.db
"""

import argparse
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from src.env_config import load_environment, print_env_banner
from src.event_storage import EventStorage
from src.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

# Default paths
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for AAMAS dashboard and REST API."""

    server_storage: EventStorage = None  # Bound at server startup

    def _set_headers(
        self, content_type: str = "application/json", status: int = HTTPStatus.OK
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self) -> None:
        self._set_headers(status=HTTPStatus.NO_CONTENT)

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        query_params = parse_qs(parsed_url.query)

        # 1. Root dashboard UI
        if path == "" or path == "/index.html":
            self._serve_file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
            return

        # 2. Static files
        if path.startswith("/static/"):
            rel_path = path[len("/static/") :]
            file_path = os.path.join(STATIC_DIR, rel_path)
            content_type, _ = mimetypes.guess_type(file_path)
            self._serve_file(file_path, content_type or "application/octet-stream")
            return

        # 3. REST API - Statistics
        if path == "/api/stats":
            self._handle_stats()
            return

        # 4. REST API - Episodes list
        if path == "/api/episodes":
            self._handle_episodes_list(query_params)
            return

        # 5. REST API - Single episode with snapshot
        if path.startswith("/api/episodes/"):
            episode_id_str = path[len("/api/episodes/") :]
            try:
                episode_id = int(episode_id_str)
                self._handle_single_episode(episode_id)
            except ValueError:
                self._send_json({"error": "Invalid episode ID"}, status=HTTPStatus.BAD_REQUEST)
            return

        # 404 Not Found
        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def _serve_file(self, file_path: str, content_type: str) -> None:
        if not os.path.isfile(file_path):
            self._send_json({"error": "File Not Found"}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            with open(file_path, "rb") as f:
                content = f.read()
            self._set_headers(content_type=content_type, status=HTTPStatus.OK)
            self.wfile.write(content)
        except Exception as e:
            logger.error("Error serving file %s: %s", file_path, e)
            self._send_json(
                {"error": "Internal Server Error"}, status=HTTPStatus.INTERNAL_SERVER_ERROR
            )

    def _send_json(self, payload: dict | list, status: int = HTTPStatus.OK) -> None:
        try:
            body = json.dumps(payload).encode("utf-8")
            self._set_headers(content_type="application/json", status=status)
            self.wfile.write(body)
        except Exception as e:
            logger.error("Error sending JSON response: %s", e)

    def _handle_stats(self) -> None:
        try:
            stats = self.server_storage.get_stats()
            self._send_json(stats)
        except Exception as e:
            logger.error("Error retrieving stats: %s", e)
            self._send_json(
                {"error": "Failed to retrieve statistics"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_episodes_list(self, params: dict) -> None:
        date = params.get("date", [None])[0]
        from_time = params.get("from", [None])[0]
        to_time = params.get("to", [None])[0]
        b_type = params.get("type", [None])[0]
        source = params.get("source", [None])[0]
        limit_str = params.get("limit", ["100"])[0]

        try:
            limit = int(limit_str)
        except ValueError:
            limit = 100

        try:
            episodes = self.server_storage.get_episodes(
                date=date,
                from_time=from_time,
                to_time=to_time,
                behavior_type=b_type,
                source=source,
                limit=limit,
            )
            self._send_json(episodes)
        except Exception as e:
            logger.error("Error retrieving episodes: %s", e)
            self._send_json(
                {"error": "Failed to retrieve episodes"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_single_episode(self, episode_id: int) -> None:
        try:
            episode = self.server_storage.get_episode(episode_id)
            if not episode:
                self._send_json({"error": "Episode not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(episode)
        except Exception as e:
            logger.error("Error retrieving episode %s: %s", episode_id, e)
            self._send_json(
                {"error": "Failed to retrieve episode"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def log_message(self, format: str, *args) -> None:
        # Suppress noisy standard request logs unless in DEBUG
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)


def run_server(host: str = "127.0.0.1", port: int = 8000, db_path: str = "data/outbox.db") -> None:
    """Start and run the HTTP server."""
    storage = EventStorage(db_path=db_path)
    DashboardRequestHandler.server_storage = storage

    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, DashboardRequestHandler)
    logger.info("AAMAS Dashboard Server running at http://%s:%d/", host, port)
    logger.info("Serving behavior database from: %s", db_path)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down dashboard server...")
    finally:
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(
        description="AAMAS Dashboard Server — single-file UI and REST API for behavior episodes and skeleton replay."
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host interface to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port number to listen on (default: 8000)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/outbox.db",
        help="Path to SQLite database (default: data/outbox.db)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    args = parser.parse_args()
    configure_logging(args.log_level)

    env = load_environment()
    print_env_banner(env)

    run_server(host=args.host, port=args.port, db_path=args.db_path)


if __name__ == "__main__":
    main()
