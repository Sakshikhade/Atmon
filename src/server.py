"""Standalone Python HTTP server and REST API for AAMAS dashboard and family app.

Serves the single-file HTML dashboard and provides JSON REST API endpoints
for recorded behavior episodes, statistics, skeleton replay, and the family app
(accounts, consent, recordings, clinician shares, privacy settings).

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

from src.clinical_export import create_export, fetch_export
from src.env_config import load_environment, print_env_banner
from src.event_storage import EventStorage
from src.family_store import FEATURE_HOME_CAMERA_PHASE2, FamilyStore
from src.logging_config import configure_logging, get_logger
from src.media_store import (
    MediaError,
    playback_still_open,
    playback_url,
    upload_recording,
    user_id_from_token,
)
from src.postgres_db import connect_postgres

logger = get_logger(__name__)

# Default paths
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for AAMAS dashboard, REST API, and family app."""

    server_storage: EventStorage = None  # Bound at server startup
    family_store: FamilyStore = None     # Bound at server startup

    def _set_headers(
        self,
        content_type: str = "application/json",
        status: int = HTTPStatus.OK,
        extra_headers: dict | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()

    def do_OPTIONS(self) -> None:
        self._set_headers(status=HTTPStatus.NO_CONTENT)

    # ── GET ──────────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        query_params = parse_qs(parsed_url.query)

        # 1. Root dashboard UI
        if path == "" or path == "/index.html":
            self._serve_file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
            return

        # 2. Family app UI
        if path == "/family" or path == "/family.html":
            self._serve_file(os.path.join(STATIC_DIR, "family.html"), "text/html; charset=utf-8")
            return

        # 3. Static files
        if path.startswith("/static/"):
            rel_path = path[len("/static/"):]
            file_path = os.path.join(STATIC_DIR, rel_path)
            content_type, _ = mimetypes.guess_type(file_path)
            self._serve_file(file_path, content_type or "application/octet-stream")
            return

        # 4. REST API - Statistics
        if path == "/api/stats":
            self._handle_stats()
            return

        # 5. REST API - Episodes list
        if path == "/api/episodes":
            self._handle_episodes_list(query_params)
            return

        # 6. REST API - Single episode with snapshot
        if path.startswith("/api/episodes/"):
            episode_id_str = path[len("/api/episodes/"):]
            try:
                episode_id = int(episode_id_str)
                self._handle_single_episode(episode_id)
            except ValueError:
                self._send_json({"error": "Invalid episode ID"}, status=HTTPStatus.BAD_REQUEST)
            return

        # 7. Family API (GET)
        if path.startswith("/api/family/"):
            self._route_family_get(path, query_params)
            return

        if path.startswith("/media/sessions/"):
            self._handle_media_play(path[len("/media/sessions/"):], query_params)
            return
        if path.startswith("/exports/"):
            self._handle_export_download(path[len("/exports/"):])
            return

        # 404 Not Found
        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    # ── POST / PUT / DELETE ───────────────────────────────────────────────────

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        if path.startswith("/api/family/"):
            self._route_family_post(path)
            return
        if path.startswith("/media/sessions/"):
            self._handle_media_upload(path[len("/media/sessions/"):])
            return
        if path == "/exports":
            self._handle_export_create()
            return
        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        if path.startswith("/api/family/"):
            self._route_family_put(path)
            return
        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        if path.startswith("/api/family/"):
            self._route_family_delete(path)
            return
        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    # ── Family routing ───────────────────────────────────────────────────────

    def _route_family_get(self, path: str, params: dict) -> None:
        fs = self.family_store
        if fs is None:
            self._send_json({"error": "Family store not initialised"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return

        # /api/family/auth/me
        if path == "/api/family/auth/me":
            account = self._require_auth()
            if account:
                self._send_json(account)
            return

        # /api/family/household
        if path == "/api/family/household":
            account = self._require_auth()
            if not account:
                return
            hh = fs.get_household(account["household_id"])
            self._send_json(hh or {})
            return

        # /api/family/consent
        if path == "/api/family/consent":
            account = self._require_auth()
            if not account:
                return
            self._send_json(fs.get_consent(account["household_id"]))
            return

        # /api/family/recordings
        if path == "/api/family/recordings":
            account = self._require_auth()
            if not account:
                return
            self._send_json(fs.get_recordings(account["household_id"]))
            return

        # /api/family/recordings/{id}
        if path.startswith("/api/family/recordings/"):
            account = self._require_auth()
            if not account:
                return
            rec_id = self._parse_int_segment(path, "/api/family/recordings/")
            if rec_id is None:
                return
            rec = fs.get_recording(rec_id, account["household_id"])
            if not rec:
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                return
            # Attach behavior events from event_storage if available
            events = []
            if self.server_storage and rec.get("event_ids"):
                for eid in rec["event_ids"]:
                    ev = self.server_storage.get_episode(eid)
                    if ev:
                        events.append(ev)
            rec["events"] = events
            self._send_json(rec)
            return

        # /api/family/shares
        if path == "/api/family/shares":
            account = self._require_auth()
            if not account:
                return
            self._send_json(fs.get_shares(account["household_id"]))
            return

        # /api/family/clinician/review/{token}  — no auth required, token is the credential
        if path.startswith("/api/family/clinician/review/"):
            token = path[len("/api/family/clinician/review/"):]
            share = fs.get_share_by_token(token)
            if not share:
                self._send_json({"error": "Invalid or expired share link"}, status=HTTPStatus.NOT_FOUND)
                return
            result: dict = {"share": share}
            if share.get("recording_id") and fs:
                rec = fs.get_recording(share["recording_id"])
                if rec:
                    events = []
                    if self.server_storage and rec.get("event_ids"):
                        for eid in rec["event_ids"]:
                            ev = self.server_storage.get_episode(eid)
                            if ev:
                                events.append(ev)
                    rec["events"] = events
                    result["recording"] = rec
            self._send_json(result)
            return

        # /api/family/privacy
        if path == "/api/family/privacy":
            account = self._require_auth()
            if not account:
                return
            self._send_json(fs.get_privacy_settings(account["household_id"]) or {})
            return

        # /api/family/features
        if path == "/api/family/features":
            self._send_json({"home_camera_phase2": FEATURE_HOME_CAMERA_PHASE2})
            return

        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def _route_family_post(self, path: str) -> None:
        fs = self.family_store
        if fs is None:
            self._send_json({"error": "Family store not initialised"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return

        body = self._read_json_body()

        # /api/family/auth/login
        if path == "/api/family/auth/login":
            if not body:
                self._send_json({"error": "Missing body"}, status=HTTPStatus.BAD_REQUEST)
                return
            result = fs.login(body.get("email", ""), body.get("pin", ""))
            if not result:
                self._send_json({"error": "Invalid credentials"}, status=HTTPStatus.UNAUTHORIZED)
                return
            self._send_json(result, status=HTTPStatus.OK)
            return

        # /api/family/auth/logout
        if path == "/api/family/auth/logout":
            token = self._extract_token()
            if token:
                fs.logout(token)
            self._send_json({"ok": True})
            return

        # /api/family/consent
        if path == "/api/family/consent":
            account = self._require_auth()
            if not account:
                return
            if not body:
                self._send_json({"error": "Missing body"}, status=HTTPStatus.BAD_REQUEST)
                return
            updated = fs.update_consent(
                account["household_id"],
                body.get("type", ""),
                body.get("status", ""),
                account["id"],
            )
            if not updated:
                self._send_json({"error": "Invalid consent type or status"}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(updated, status=HTTPStatus.OK)
            return

        # /api/family/capture/start
        if path == "/api/family/capture/start":
            account = self._require_auth()
            if not account:
                return
            if not body:
                body = {}
            from src.event_storage import utcnow_iso
            started_at = body.get("started_at") or utcnow_iso()
            label = body.get("label")
            rec_id = fs.create_recording(account["household_id"], label, started_at)
            self._send_json({"recording_id": rec_id, "started_at": started_at}, status=HTTPStatus.CREATED)
            return

        # /api/family/capture/stop
        if path == "/api/family/capture/stop":
            account = self._require_auth()
            if not account:
                return
            if not body:
                self._send_json({"error": "Missing body"}, status=HTTPStatus.BAD_REQUEST)
                return
            rec_id = body.get("recording_id")
            if not isinstance(rec_id, int):
                self._send_json({"error": "recording_id required"}, status=HTTPStatus.BAD_REQUEST)
                return
            from src.event_storage import utcnow_iso
            ended_at = body.get("ended_at") or utcnow_iso()
            event_ids = list(body.get("event_ids") or [])

            # Auto-link any events from EventStorage that occurred during this session window
            existing_rec = fs.get_recording(rec_id, account["household_id"])
            if existing_rec and self.server_storage:
                started_at = existing_rec.get("started_at")
                if started_at:
                    detected = self.server_storage.get_episodes(
                        from_time=started_at, to_time=ended_at, limit=1000
                    )
                    auto_ids = [e["id"] for e in detected]
                    # Merge client-supplied IDs with auto-detected IDs, deduplicated
                    event_ids = list(dict.fromkeys(event_ids + auto_ids))

            ok = fs.stop_recording(rec_id, ended_at, event_ids)
            if not ok:
                self._send_json({"error": "Recording not found"}, status=HTTPStatus.NOT_FOUND)
                return
            rec = fs.get_recording(rec_id, account["household_id"])
            self._send_json(rec or {}, status=HTTPStatus.OK)
            return

        # /api/family/shares
        if path == "/api/family/shares":
            account = self._require_auth()
            if not account:
                return
            if not body:
                self._send_json({"error": "Missing body"}, status=HTTPStatus.BAD_REQUEST)
                return
            clinician_name = body.get("clinician_name", "").strip()
            clinician_email = body.get("clinician_email", "").strip()
            if not clinician_name or not clinician_email:
                self._send_json({"error": "clinician_name and clinician_email required"}, status=HTTPStatus.BAD_REQUEST)
                return
            share = fs.create_share(
                household_id=account["household_id"],
                recording_id=body.get("recording_id"),
                clinician_name=clinician_name,
                clinician_email=clinician_email,
                account_id=account["id"],
                notes=body.get("notes"),
                expires_days=body.get("expires_days"),
            )
            self._send_json(share, status=HTTPStatus.CREATED)
            return

        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def _route_family_put(self, path: str) -> None:
        fs = self.family_store
        if fs is None:
            self._send_json({"error": "Family store not initialised"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return

        body = self._read_json_body()

        # /api/family/privacy
        if path == "/api/family/privacy":
            account = self._require_auth()
            if not account:
                return
            if not body:
                self._send_json({"error": "Missing body"}, status=HTTPStatus.BAD_REQUEST)
                return
            updated = fs.update_privacy_settings(
                account["household_id"],
                retention_days=body.get("retention_days"),
                auto_delete_enabled=body.get("auto_delete_enabled"),
                allow_clinician_replay=body.get("allow_clinician_replay"),
            )
            self._send_json(updated or {}, status=HTTPStatus.OK)
            return

        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def _route_family_delete(self, path: str) -> None:
        fs = self.family_store
        if fs is None:
            self._send_json({"error": "Family store not initialised"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return

        # /api/family/recordings/{id}
        if path.startswith("/api/family/recordings/"):
            account = self._require_auth()
            if not account:
                return
            rec_id = self._parse_int_segment(path, "/api/family/recordings/")
            if rec_id is None:
                return
            ok = fs.delete_recording(rec_id, account["household_id"])
            if not ok:
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"ok": True})
            return

        # /api/family/shares/{id}
        if path.startswith("/api/family/shares/"):
            account = self._require_auth()
            if not account:
                return
            share_id = self._parse_int_segment(path, "/api/family/shares/")
            if share_id is None:
                return
            ok = fs.revoke_share(share_id, account["household_id"])
            if not ok:
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"ok": True})
            return

        self._send_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def _media_user(self) -> str | None:
        token = self._extract_token()
        if not token:
            self._send_json({"error": "Sign in again"}, status=HTTPStatus.UNAUTHORIZED)
            return None
        try:
            return user_id_from_token(token)
        except MediaError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.UNAUTHORIZED)
            return None

    def _handle_media_upload(self, session_id: str) -> None:
        user_id = self._media_user()
        if user_id is None:
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 80_000_000:
            self._send_json({"error": "Recording is empty or too large"}, status=HTTPStatus.BAD_REQUEST)
            return
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "video/webm").split(";")[0].strip()
        try:
            key = upload_recording(session_id, user_id, body, content_type or "video/webm")
        except MediaError as exc:
            status = HTTPStatus.FORBIDDEN if "not yours" in str(exc) else HTTPStatus.BAD_GATEWAY
            if "not been" in str(exc) or "Unknown" in str(exc) or "empty" in str(exc):
                status = HTTPStatus.BAD_REQUEST
            logger.warning("Media upload failed: %s", exc)
            self._send_json({"error": str(exc)}, status=status)
            return
        self._send_json({"storage_path": key}, status=HTTPStatus.CREATED)

    def _handle_media_play(self, rest: str, query_params: dict) -> None:
        if rest.endswith("/status"):
            self._handle_media_status(rest[: -len("/status")])
            return
        user_id = self._media_user()
        if user_id is None:
            return
        event_id = (query_params.get("event") or [None])[0]
        try:
            url = playback_url(rest, user_id, event_id)
        except MediaError as exc:
            self._send_media_error(rest, exc)
            return
        logger.info("Media play %s -> link", rest)
        self._send_json({"url": url, "expires_in": 60})

    def _handle_media_status(self, session_id: str) -> None:
        user_id = self._media_user()
        if user_id is None:
            return
        try:
            playback_still_open(session_id, user_id)
        except MediaError as exc:
            self._send_media_error(session_id, exc)
            return
        self._send_json({"open": True})

    def _send_media_error(self, session_id: str, exc: MediaError) -> None:
        message = str(exc)
        code = HTTPStatus.BAD_GATEWAY
        if "not been uploaded" in message or "Unknown" in message:
            code = HTTPStatus.NOT_FOUND
        elif "ended" in message or "not yours" in message or "does not include" in message:
            code = HTTPStatus.FORBIDDEN
        elif "Sign in" in message:
            code = HTTPStatus.UNAUTHORIZED
        logger.info("Media play %s -> %s", session_id, exc)
        self._send_json({"error": message}, status=code)

    def _handle_export_create(self) -> None:
        user_id = self._media_user()
        if user_id is None:
            return
        body = self._read_json_body() or {}
        grant_id = body.get("grantId") if isinstance(body, dict) else None
        if not isinstance(grant_id, str):
            self._send_json({"error": "Unknown grant"}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            export_id, payload = create_export(grant_id, user_id)
        except MediaError as exc:
            self._send_media_error(grant_id, exc)
            return
        self._send_bytes(payload, "application/pdf", f"atmon-log-{export_id}.pdf")

    def _handle_export_download(self, export_id: str) -> None:
        user_id = self._media_user()
        if user_id is None:
            return
        try:
            payload = fetch_export(export_id, user_id)
        except MediaError as exc:
            self._send_media_error(export_id, exc)
            return
        self._send_bytes(payload, "application/pdf", f"atmon-log-{export_id}.pdf")

    def _send_bytes(self, payload: bytes, content_type: str, filename: str) -> None:
        self._set_headers(
            content_type=content_type,
            extra_headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(payload)),
            },
        )
        self.wfile.write(payload)

    # ── Auth helpers ─────────────────────────────────────────────────────────

    def _extract_token(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    def _require_auth(self) -> dict | None:
        token = self._extract_token()
        if not token:
            self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
            return None
        account = self.family_store.resolve_session(token)
        if not account:
            self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
            return None
        return account

    # ── Utility helpers ───────────────────────────────────────────────────────

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            logger.warning("Failed to parse request body: %s", e)
            return None

    def _parse_int_segment(self, path: str, prefix: str) -> int | None:
        segment = path[len(prefix):]
        try:
            return int(segment)
        except ValueError:
            self._send_json({"error": "Invalid ID"}, status=HTTPStatus.BAD_REQUEST)
            return None

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


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    db_path: str = "data/outbox.db",
    family_db_path: str = "data/family.db",
) -> None:
    """Start and run the HTTP server."""
    storage = EventStorage(db_path=db_path)
    family_store = FamilyStore(db_path=family_db_path)
    DashboardRequestHandler.server_storage = storage
    DashboardRequestHandler.family_store = family_store

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
        "--family-db-path",
        type=str,
        default="data/family.db",
        help="Path to family app SQLite database (default: data/family.db)",
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

    pg_conn = connect_postgres()
    try:
        run_server(
            host=args.host,
            port=args.port,
            db_path=args.db_path,
            family_db_path=args.family_db_path,
        )
    finally:
        pg_conn.close()


if __name__ == "__main__":
    main()
