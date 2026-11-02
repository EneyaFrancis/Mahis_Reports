"""Persistent issue tracking for the Data Quality page's Issues tab.

One DuckDB file per route (data/<route>/duckdb/dq_issues.duckdb), matching
the persisted-DuckDB-file pattern already used by data_storage_duckdb.py
(same connect/upsert idiom: ON CONFLICT ... DO UPDATE SET ... = EXCLUDED...)
rather than introducing a second database technology.

Every finding from Duplicates/Validity/Users carries a stable issue_key
(see make_key below) so the same underlying defect maps to the same tracked
row across repeated "Run DQ" scans -- upsert_findings refreshes last_seen
and the read-only descriptive columns on a re-scan, but never touches
status/assigned_to/notes/reviewed_by/reviewed_at, so review progress
survives re-running the checks.
"""
import hashlib
import os
from datetime import datetime, timezone

import duckdb
import pandas as pd

STATUS_OPTIONS = ["new", "under_review", "confirmed", "not_an_issue", "resolved"]
_TABLE = "dq_issues"

_REFRESHED_ON_RESCAN = ["severity", "entity_label", "facility", "district", "programme", "last_seen"]

_COLUMNS = [
    "issue_key", "tab", "rule_id", "severity", "entity_label",
    "facility", "district", "programme",
    "first_seen", "last_seen",
    "status", "assigned_to", "notes", "reviewed_by", "reviewed_at",
]


def make_key(tab: str, rule_id: str, *identity_parts) -> str:
    """Deterministic, stable id for one finding -- same tab+rule+identity
    always hashes to the same key, so a finding seen again on a later scan
    updates the existing tracked row instead of creating a duplicate."""
    raw = "|".join([tab, rule_id] + [str(p) for p in identity_parts])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _db_path(route: str) -> str:
    path = os.path.join(os.getcwd(), f"data/{route}", "duckdb")
    os.makedirs(path, exist_ok=True)
    return os.path.join(path, "dq_issues.duckdb")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_findings(route: str, findings: list[dict]) -> None:
    """findings: [{issue_key, tab, rule_id, severity, entity_label,
    facility, district, programme}, ...]. New keys are inserted with
    status='new'; existing keys get their descriptive columns and
    last_seen refreshed, status/notes/etc left untouched."""
    if not findings:
        return
    now = _now()
    rows = []
    for f in findings:
        rows.append({
            "issue_key": f["issue_key"], "tab": f["tab"], "rule_id": f["rule_id"],
            "severity": f.get("severity"), "entity_label": f.get("entity_label"),
            "facility": f.get("facility"), "district": f.get("district"), "programme": f.get("programme"),
            "first_seen": now, "last_seen": now,
            "status": "new", "assigned_to": None, "notes": None,
            "reviewed_by": None, "reviewed_at": None,
        })
    incoming = pd.DataFrame(rows, columns=_COLUMNS)

    con = duckdb.connect(_db_path(route))
    try:
        con.register("incoming_df", incoming)
        table_exists = con.sql(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            params=[_TABLE],
        ).fetchone()[0] > 0

        if not table_exists:
            # Explicit schema, not CREATE TABLE ... AS SELECT -- the nullable
            # text columns (notes, assigned_to, reviewed_by, reviewed_at) are
            # all-NULL on a first insert, and DuckDB's type inference from an
            # all-NULL pandas column silently picks INTEGER instead of
            # VARCHAR, which then breaks the very first real UPDATE.
            con.execute(f"""
                CREATE TABLE {_TABLE} (
                    issue_key VARCHAR, tab VARCHAR, rule_id VARCHAR, severity VARCHAR,
                    entity_label VARCHAR, facility VARCHAR, district VARCHAR, programme VARCHAR,
                    first_seen VARCHAR, last_seen VARCHAR,
                    status VARCHAR, assigned_to VARCHAR, notes VARCHAR,
                    reviewed_by VARCHAR, reviewed_at VARCHAR
                )
            """)
            con.execute(f"INSERT INTO {_TABLE} SELECT * FROM incoming_df")
            con.execute(f"CREATE UNIQUE INDEX idx_{_TABLE}_key ON {_TABLE} (issue_key)")
            return

        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in _REFRESHED_ON_RESCAN)
        con.execute(f"""
            INSERT INTO {_TABLE}
            SELECT * FROM incoming_df
            ON CONFLICT (issue_key) DO UPDATE SET {set_clause}
        """)
    finally:
        con.close()


def query_issues(route: str) -> pd.DataFrame:
    """All tracked issues for this route, newest first_seen first. Empty
    DataFrame (correct columns, zero rows) when nothing's been tracked yet."""
    db_path = _db_path(route)
    if not os.path.exists(db_path):
        return pd.DataFrame(columns=_COLUMNS)
    con = duckdb.connect(db_path)
    try:
        table_exists = con.sql(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            params=[_TABLE],
        ).fetchone()[0] > 0
        if not table_exists:
            return pd.DataFrame(columns=_COLUMNS)
        return con.execute(f"SELECT * FROM {_TABLE} ORDER BY first_seen DESC").df()
    finally:
        con.close()


def update_issue(route: str, issue_key: str, *, status=None, assigned_to=None, notes=None, reviewed_by=None) -> None:
    """Applies only the fields actually passed (None = leave unchanged).
    Stamps reviewed_at automatically whenever status or notes change."""
    sets, params = [], []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if assigned_to is not None:
        sets.append("assigned_to = ?")
        params.append(assigned_to)
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes)
    if reviewed_by is not None:
        sets.append("reviewed_by = ?")
        params.append(reviewed_by)
    if status is not None or notes is not None:
        sets.append("reviewed_at = ?")
        params.append(_now())
    if not sets:
        return
    params.append(issue_key)

    con = duckdb.connect(_db_path(route))
    try:
        con.execute(f"UPDATE {_TABLE} SET {', '.join(sets)} WHERE issue_key = ?", params)
    finally:
        con.close()
