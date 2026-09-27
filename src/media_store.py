"""Backblaze B2 copies of family recordings.

The browser never sees the application key. It sends the bytes here with a
Supabase access token. This module checks that token, stores the file, and
writes the object key onto session_segments.storage_path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from src.postgres_db import connect_postgres

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_MAX_BYTES = 80_000_000
_auth_cache: dict | None = None
_auth_until = 0.0


class MediaError(Exception):
    """A safe message for the client. Details stay in the server log."""


def object_key(session_id: str) -> str:
    prefix = os.environ.get("BACKBLAZE_OBJECT_PREFIX", "recordings").strip().strip("/")
    if not prefix:
        prefix = "recordings"
    return f"{prefix}/{session_id}.webm"


def encoded_name(key: str) -> str:
    return urllib.parse.quote(key, safe="/")


def _require_b2() -> tuple[str, str, str]:
    key_id = os.environ.get("BACKBLAZE_KEY_ID", "").strip()
    app_key = os.environ.get("BACKBLAZE_API_KEY", "").strip()
    bucket = os.environ.get("BACKBLAZE_BUCKET_NAME", "").strip()
    if not key_id or not app_key or not bucket:
        raise MediaError("Backblaze is not configured")
    return key_id, app_key, bucket


def _supabase() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip() or os.environ.get("VITE_SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip() or os.environ.get(
        "VITE_SUPABASE_ANON_KEY", ""
    ).strip()
    if not url or not key:
        root = Path(__file__).resolve().parents[1]
        try:
            from dotenv import dotenv_values
        except ImportError:
            dotenv_values = None
        if dotenv_values is not None:
            for path in (root / "apps" / "family" / ".env", root / "apps" / "clinician" / ".env"):
                if not path.is_file():
                    continue
                values = dotenv_values(path)
                url = url or (values.get("VITE_SUPABASE_URL") or "")
                key = key or (values.get("VITE_SUPABASE_ANON_KEY") or "")
    if not url or not key:
        raise MediaError("Supabase is not configured for media")
    return url.rstrip("/"), key


def user_id_from_token(token: str) -> str:
    url, key = _supabase()
    request = urllib.request.Request(
        f"{url}/auth/v1/user",
        headers={"apikey": key, "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise MediaError("Sign in again") from exc
    except urllib.error.URLError as exc:
        raise MediaError("Could not check the sign-in") from exc
    user_id = payload.get("id")
    if not isinstance(user_id, str) or not _UUID.match(user_id):
        raise MediaError("Sign in again")
    return user_id


def _b2_json(url: str, body: dict | None, headers: dict[str, str]) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise MediaError("Backblaze refused the request") from exc
    except urllib.error.URLError as exc:
        raise MediaError("Could not reach Backblaze") from exc


def _account() -> dict:
    global _auth_cache, _auth_until
    now = time.time()
    if _auth_cache is not None and now < _auth_until:
        return _auth_cache
    key_id, app_key, _bucket = _require_b2()
    token = base64.b64encode(f"{key_id}:{app_key}".encode()).decode("ascii")
    auth = _b2_json(
        "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
        None,
        {"Authorization": f"Basic {token}"},
    )
    _auth_cache = auth
    _auth_until = now + 3600
    return auth


def _bucket_id(auth: dict) -> str:
    _key_id, _app_key, name = _require_b2()
    payload = _b2_json(
        f"{auth['apiUrl']}/b2api/v2/b2_list_buckets",
        {"accountId": auth["accountId"], "bucketName": name},
        {"Authorization": auth["authorizationToken"], "Content-Type": "application/json"},
    )
    buckets = payload.get("buckets") or []
    if not buckets:
        raise MediaError("Backblaze bucket was not found")
    return buckets[0]["bucketId"]


def upload_recording(session_id: str, user_id: str, body: bytes, content_type: str) -> str:
    if not _UUID.match(session_id):
        raise MediaError("Unknown session")
    if not body or len(body) > _MAX_BYTES:
        raise MediaError("Recording is empty or too large")
    if not _owns_session(session_id, user_id):
        raise MediaError("This recording is not yours")
    auth = _account()
    bucket_id = _bucket_id(auth)
    upload = _b2_json(
        f"{auth['apiUrl']}/b2api/v2/b2_get_upload_url",
        {"bucketId": bucket_id},
        {"Authorization": auth["authorizationToken"], "Content-Type": "application/json"},
    )
    key = object_key(session_id)
    digest = hashlib.sha1(body).hexdigest()
    request = urllib.request.Request(
        upload["uploadUrl"],
        data=body,
        method="POST",
        headers={
            "Authorization": upload["authorizationToken"],
            "X-Bz-File-Name": encoded_name(key),
            "Content-Type": content_type or "video/webm",
            "X-Bz-Content-Sha1": digest,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        raise MediaError("Backblaze did not store the recording") from exc
    except urllib.error.URLError as exc:
        raise MediaError("Could not reach Backblaze") from exc
    _mark_uploaded(session_id, key)
    return key


def playback_url(session_id: str, user_id: str) -> str:
    if not _UUID.match(session_id):
        raise MediaError("Unknown session")
    key = _playable_key(session_id, user_id)
    if key is None:
        raise MediaError("This clip has not been uploaded")
    auth = _account()
    bucket_id = _bucket_id(auth)
    _key_id, _app_key, bucket = _require_b2()
    granted = _b2_json(
        f"{auth['apiUrl']}/b2api/v2/b2_get_download_authorization",
        {"bucketId": bucket_id, "fileNamePrefix": key, "validDurationInSeconds": 60},
        {"Authorization": auth["authorizationToken"], "Content-Type": "application/json"},
    )
    token = urllib.parse.quote(granted["authorizationToken"], safe="")
    return f"{auth['downloadUrl']}/file/{bucket}/{encoded_name(key)}?Authorization={token}"


def _owns_session(session_id: str, user_id: str) -> bool:
    with connect_postgres("DIRECT_URL") as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from app.sessions where id = %s and recorded_by = %s",
                (session_id, user_id),
            )
            return cur.fetchone() is not None


def _mark_uploaded(session_id: str, key: str) -> None:
    with connect_postgres("DIRECT_URL") as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update app.sessions set storage_location = 'cloud' where id = %s",
                (session_id,),
            )
            cur.execute(
                """
                update app.session_segments
                set storage_path = %s, uploaded_at = now()
                where session_id = %s
                """,
                (key, session_id),
            )
            if cur.rowcount == 0:
                cur.execute(
                    """
                    insert into app.session_segments
                      (id, session_id, seq, start_ms, duration_ms, storage_path, uploaded_at)
                    select gen_random_uuid(), id, 0, 0, duration_ms, %s, now()
                    from app.sessions
                    where id = %s
                    """,
                    (key, session_id),
                )
        conn.commit()


def _playable_key(session_id: str, user_id: str) -> str | None:
    with connect_postgres("DIRECT_URL") as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select seg.storage_path
                from app.sessions s
                join app.session_segments seg on seg.session_id = s.id
                where s.id = %s
                  and seg.storage_path is not null
                  and seg.storage_path not like 'idb:%%'
                  and (
                    s.recorded_by = %s
                    or exists (
                      select 1 from app.household_members m
                      where m.household_id = s.household_id
                        and m.user_id = %s
                        and m.can_view
                    )
                    or exists (
                      select 1
                      from app.events e
                      join app.share_grant_items i on i.event_id = e.id
                      join app.share_grants g on g.id = i.grant_id
                      where e.session_id = s.id
                        and g.clinician_id = %s
                        and g.status = 'active'
                        and g.expires_at > now()
                    )
                    or exists (
                      select 1 from app.share_grants g
                      where g.child_id = s.child_id
                        and g.scope = 'all'
                        and g.clinician_id = %s
                        and g.status = 'active'
                        and g.expires_at > now()
                    )
                  )
                order by seg.seq
                limit 1
                """,
                (session_id, user_id, user_id, user_id, user_id),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return row[0]
