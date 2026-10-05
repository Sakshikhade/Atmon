#!/usr/bin/env python3
"""Assert ATMON capability matrix CSV inventory and enum constraints."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "product" / "ATMON_Capability_Matrix.csv"

REQUIRED_COLS = [
    "Capability_ID",
    "Epic",
    "Capability",
    "Use_Case",
    "Acceptance_Criteria",
    "Status",
    "Impl_Owner",
    "Hosting_Owner",
    "QA",
    "QA_Status",
    "Sprint",
    "Planned_Delivery",
    "Notes",
]

STATUS_OK = {"Done", "Partial", "Not started", "Deferred"}
HOSTING_OK = {"Abhishek", "Mainak", ""}
QA_OK = {"Abhishek", "Mainak", "Mateo", "Sakshi", ""}
QA_STATUS_OK = {"Pass", "Pending", "Fail", "N/A", ""}


def expected_b2c_ids() -> list[str]:
    ids: list[str] = []
    for epic, count in [
        (1, 4),
        (2, 8),
        (3, 5),
        (4, 5),
        (5, 5),
        (6, 4),
        (7, 8),
        (8, 5),
    ]:
        if epic == 7:
            for n in (1, 9, 2, 3, 4, 5, 6, 7):
                ids.append(f"F-7.{n}")
        else:
            for n in range(1, count + 1):
                ids.append(f"F-{epic}.{n}")
    return ids


def main() -> int:
    errors: list[str] = []
    if not CSV_PATH.is_file():
        print(f"ERROR: missing {CSV_PATH.relative_to(ROOT)}", file=sys.stderr)
        return 1

    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            print("ERROR: CSV has no header", file=sys.stderr)
            return 1
        missing_cols = [c for c in REQUIRED_COLS if c not in reader.fieldnames]
        if missing_cols:
            errors.append(f"missing columns: {missing_cols}")
        rows = list(reader)

    ids = [r.get("Capability_ID", "").strip() for r in rows]
    id_set = set(ids)

    for req in expected_b2c_ids():
        if req not in id_set:
            errors.append(f"missing required Capability_ID: {req}")

    if "F-9.1" not in id_set and not any(i.startswith("F-9.") for i in id_set):
        errors.append("missing Deferred E-9 rollup row (F-9.1 or F-9.*)")
    if "E-10+" not in id_set and not any(i.startswith("F-10.") for i in id_set):
        errors.append("missing Deferred E-10+ / agency rollup row")

    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        cid = (row.get("Capability_ID") or "").strip()
        if not cid:
            errors.append(f"line {i}: empty Capability_ID")
            continue
        if cid in seen and cid.startswith("F-"):
            errors.append(f"line {i}: duplicate Capability_ID {cid}")
        seen.add(cid)

        status = (row.get("Status") or "").strip()
        if status not in STATUS_OK:
            errors.append(f"line {i} ({cid}): invalid Status {status!r}")

        host = (row.get("Hosting_Owner") or "").strip()
        if host not in HOSTING_OK:
            errors.append(
                f"line {i} ({cid}): Hosting_Owner must be Abhishek|Mainak|blank, got {host!r}"
            )

        qa = (row.get("QA") or "").strip()
        if qa not in QA_OK:
            errors.append(
                f"line {i} ({cid}): QA must be Abhishek|Mainak|Mateo|Sakshi|blank, got {qa!r}"
            )

        qa_st = (row.get("QA_Status") or "").strip()
        if qa_st not in QA_STATUS_OK:
            errors.append(
                f"line {i} ({cid}): QA_Status must be Pass|Pending|Fail|N/A|blank, got {qa_st!r}"
            )

        ac = (row.get("Acceptance_Criteria") or "").strip()
        if len(ac) < 40:
            errors.append(f"line {i} ({cid}): Acceptance_Criteria too short ({len(ac)} chars)")

    if errors:
        print(f"capability matrix audit FAILED ({len(errors)} issue(s)):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    counts = Counter((r.get("Status") or "").strip() for r in rows)
    qa_counts = Counter((r.get("QA_Status") or "").strip() for r in rows)
    print(
        f"capability matrix OK: {len(rows)} rows | "
        f"Done={counts.get('Done', 0)} Partial={counts.get('Partial', 0)} "
        f"Not started={counts.get('Not started', 0)} Deferred={counts.get('Deferred', 0)} | "
        f"QA Pass={qa_counts.get('Pass', 0)} Pending={qa_counts.get('Pending', 0)} "
        f"N/A={qa_counts.get('N/A', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
