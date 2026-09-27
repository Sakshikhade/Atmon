"""Clinician log export. The file is a PDF of the shared log. It never contains video."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from src.media_store import MediaError, download_object, record_access, upload_object
from src.postgres_db import connect_postgres

NON_DIAGNOSTIC = (
    "Everything here is an observation. Nothing this product outputs is a diagnosis "
    "or a clinical finding."
)
METHODOLOGY = "on-device-stub"

_NAMES = {
    "flap": "Hand flapping",
    "vocal": "Vocal stereotypy",
    "mand": "Communication attempt",
    "away": "Moving away from caregiver",
    "floor": "Dropping to floor",
}


def export_lines(child: str, removed: int, sessions: list[dict]) -> list[str]:
    lines = [
        "ATMON log export",
        f"Methodology: {METHODOLOGY}",
        "",
        NON_DIAGNOSTIC,
        "This export contains no video and no audio.",
        "",
        f"Child: {child}",
        f"Events in this export: {sum(len(session['events']) for session in sessions)}",
        f"Removed stretches, counted only: {removed}",
    ]
    for session in sessions:
        lines.append("")
        lines.append(f"{session['setting']}, {session['when']}")
        if not session["events"]:
            lines.append("  No events in this export.")
            continue
        for event in session["events"]:
            lines.append(f"  {event['time']}  {event['name']}, {event['duration']}")
            if event["family"]:
                lines.append(f"    Family: {event['family']}")
            if event["clinician"]:
                lines.append(f"    Clinician: {event['clinician']}")
            if event["before"]:
                lines.append(f"    Before: {event['before']}")
    return lines


def pdf_bytes(lines: list[str]) -> bytes:
    wrapped: list[str] = []
    for line in lines:
        text = "".join(ch if 32 <= ord(ch) <= 126 else "?" for ch in line)
        while len(text) > 90:
            wrapped.append(text[:90])
            text = text[90:]
        wrapped.append(text)
    pages: list[list[tuple[int, str]]] = []
    chunk: list[tuple[int, str]] = []
    y = 740
    for line in wrapped:
        if y < 72:
            pages.append(chunk)
            chunk = []
            y = 740
        chunk.append((y, line))
        y -= 14
    if chunk or not pages:
        pages.append(chunk)

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    content_ids: list[int] = []
    for page in pages:
        stream = ["BT", "/F1 11 Tf"]
        for top, line in page:
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.append(f"1 0 0 1 72 {top} Tm ({safe}) Tj")
        stream.append("ET")
        data = "\n".join(stream).encode("latin-1")
        content_ids.append(
            add(b"<< /Length " + str(len(data)).encode("ascii") + b" >>\nstream\n" + data + b"\nendstream")
        )
    for index, _page in enumerate(pages):
        page_ids.append(0)
    # Page objects need the pages parent id, so reserve it first.
    pages_id = add(b"")
    for index, content_id in enumerate(content_ids):
        page_ids[index] = add(
            b"<< /Type /Page /Parent "
            + str(pages_id).encode("ascii")
            + b" 0 R /MediaBox [0 0 612 792] /Contents "
            + str(content_id).encode("ascii")
            + b" 0 R /Resources << /Font << /F1 "
            + str(font_id).encode("ascii")
            + b" 0 R >> >> >>"
        )
    kids_ref = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = (
        b"<< /Type /Pages /Count "
        + str(len(page_ids)).encode("ascii")
        + b" /Kids ["
        + kids_ref.encode("ascii")
        + b"] >>"
    )
    catalog_id = add(b"<< /Type /Catalog /Pages " + str(pages_id).encode("ascii") + b" 0 R >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref}\n%%EOF".encode(
            "ascii"
        )
    )
    return bytes(out)


def pdf_text(payload: bytes) -> str:
    parts = re.findall(r"\((?:\\.|[^)\\])*\)", payload.decode("latin-1"))
    text = "".join(part[1:-1] for part in parts)
    return text.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")


def create_export(grant_id: str, clinician_id: str) -> tuple[str, bytes]:
    if not _uuid(grant_id):
        raise MediaError("Unknown grant")
    child, removed, sessions, household_id, session_ids = _load_grant(grant_id, clinician_id)
    lines = export_lines(child, removed, sessions)
    payload = pdf_bytes(lines)
    if NON_DIAGNOSTIC not in pdf_text(payload):
        raise MediaError("The export was refused because the statement was missing")
    export_id = str(uuid.uuid4())
    key = f"exports/{household_id}/{export_id}.pdf"
    upload_object(key, payload, "application/pdf")
    event_count = sum(len(session["events"]) for session in sessions)
    with connect_postgres("DIRECT_URL") as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into app.exports
                  (id, grant_id, clinician_id, child_id, session_ids, event_count,
                   format, methodology_version, storage_path)
                select %s, g.id, %s, g.child_id, %s::uuid[], %s, 'pdf', %s, %s
                from app.share_grants g
                where g.id = %s
                """,
                (export_id, clinician_id, session_ids, event_count, METHODOLOGY, key, grant_id),
            )
        conn.commit()
    record_access(grant_id, clinician_id, "exported", None, None, export_id)
    return export_id, payload


def fetch_export(export_id: str, clinician_id: str) -> bytes:
    if not _uuid(export_id):
        raise MediaError("Unknown export")
    with connect_postgres("DIRECT_URL") as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select storage_path from app.exports where id = %s and clinician_id = %s",
                (export_id, clinician_id),
            )
            row = cur.fetchone()
    if row is None or not row[0]:
        raise MediaError("Unknown export")
    return download_object(row[0])


def _load_grant(grant_id: str, clinician_id: str) -> tuple[str, int, list[dict], str, list[str]]:
    with connect_postgres("DIRECT_URL") as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select g.child_id::text, g.household_id::text, c.display_name, g.scope::text
                from app.share_grants g
                join app.children c on c.id = g.child_id
                where g.id = %s
                  and g.clinician_id = %s
                  and g.status = 'active'
                  and g.expires_at > now()
                  and g.export_allowed
                """,
                (grant_id, clinician_id),
            )
            grant = cur.fetchone()
            if grant is None:
                raise MediaError("This grant does not include export")
            child_id, household_id, display_name, scope = grant
            child = (display_name or "Child").split()[0]
            cur.execute(
                """
                select e.id::text, e.session_id::text, e.class_key, e.onset_ms, e.duration_ms,
                       e.status, e.media_suppressed, s.started_at, s.setting
                from app.events e
                join app.sessions s on s.id = e.session_id
                where e.child_id = %s
                  and (
                    %s = 'all'
                    or exists (
                      select 1 from app.share_grant_items i
                      where i.grant_id = %s and i.event_id = e.id
                    )
                  )
                order by s.started_at, e.onset_ms
                """,
                (child_id, scope, grant_id),
            )
            rows = cur.fetchall()
            event_ids = [row[0] for row in rows if row[5] != "rejected" and not row[6]]
            family = {}
            clinician = {}
            before: dict[str, str] = {}
            if event_ids:
                cur.execute(
                    """
                    select event_id::text, actor_kind, decision
                    from app.event_verifications
                    where event_id::text = any(%s)
                    order by created_at desc
                    """,
                    (event_ids,),
                )
                for event_id, kind, decision in cur.fetchall():
                    target = family if kind == "family" else clinician if kind == "clinician" else None
                    if target is not None and event_id not in target:
                        target[event_id] = decision
                cur.execute(
                    """
                    select event_id::text, text
                    from app.event_antecedents
                    where event_id::text = any(%s)
                    """,
                    (event_ids,),
                )
                before = {event_id: text for event_id, text in cur.fetchall()}
    removed = sum(1 for row in rows if row[6])
    grouped: dict[str, dict] = {}
    for event_id, session_id, class_key, onset_ms, duration_ms, status, suppressed, started_at, setting in rows:
        if suppressed or status == "rejected":
            continue
        bucket = grouped.setdefault(
            session_id,
            {"when": _when(started_at), "setting": _setting(setting), "events": []},
        )
        seconds = max(0, int(duration_ms or 0) // 1000)
        bucket["events"].append(
            {
                "time": _clock(int(onset_ms or 0)),
                "name": _NAMES.get(class_key, class_key),
                "duration": f"{seconds}s",
                "family": _decision(family.get(event_id)),
                "clinician": _decision(clinician.get(event_id)),
                "before": before.get(event_id) or "",
            }
        )
    return child, removed, list(grouped.values()), household_id, list(grouped.keys())


def _decision(value: str | None) -> str:
    labels = {"confirm": "confirmed", "correct": "corrected", "reject": "said not this"}
    return labels.get(value or "", "")


def _setting(value: str | None) -> str:
    labels = {
        "home": "Home",
        "shop": "Shop",
        "therapy": "Therapy",
        "transition": "Transition",
        "playground": "Playground",
        "other": "Other",
    }
    if not value:
        return "Session"
    return labels.get(value, value)


def _when(value: datetime) -> str:
    return value.strftime("%-d %b %Y")


def _clock(ms: int) -> str:
    total = max(0, ms // 1000)
    return f"{total // 60}:{total % 60:02d}"


def _uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
