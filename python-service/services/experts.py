"""
Data Experts — admin-published datasets that authorized users can question.

An "expert" is a governed Excel/CSV the admin uploads through the admin console,
stored under python-service/datasets/ (gitignored) with its metadata in SQLite.
Users who pass the access check see it in the Standard sidebar and can Load it —
which feeds the file through the SAME code path as a normal upload, so the
pipeline, insights, tracking, and exports are untouched and unaware.

Access model (server-enforced on list AND load):
  AccessMode 'all'        -> every app user
  AccessMode 'restricted' -> username in AllowedUsers OR any of the caller's AD
                             groups (X-User-Groups header, resolved by .NET from
                             the Windows sign-in badge; Auth:DevGroups simulates
                             them in dev) in AllowedGroups.
Name matching is case-insensitive and tolerant of DOMAIN\\ prefixes; lists
accept commas or newlines (paste a column straight from Excel).
"""

import os
import re
from typing import Dict, List, Any, Optional

from config import Config
from logger import logger
from services.database import get_cursor, now_utc

DATASETS_DIR = os.environ.get(
    "DATASETS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datasets"),
)

MAX_QUESTIONS = 6


# ── name/list matching helpers ────────────────────────────────────────────────

def _bare(name: str) -> str:
    return (name or "").strip().lower().split("\\")[-1]


def parse_list(text: str) -> List[str]:
    """Split a comma- and/or newline-separated list into clean entries."""
    if not text:
        return []
    return [p.strip() for p in re.split(r"[,\n;]+", text) if p.strip()]


def _matches(candidate: str, allowed_entries: List[str]) -> bool:
    cand = {candidate.strip().lower(), _bare(candidate)}
    for entry in allowed_entries:
        if entry.lower() in cand or _bare(entry) in cand:
            return True
    return False


def user_allowed(expert: Dict[str, Any], username: str, groups: List[str]) -> bool:
    if expert.get("AccessMode") == "all":
        return True
    if username and _matches(username, parse_list(expert.get("AllowedUsers", ""))):
        return True
    allowed_groups = parse_list(expert.get("AllowedGroups", ""))
    for g in groups:
        if _matches(g, allowed_groups):
            return True
    return False


def clean_questions(text: str) -> str:
    """Keep at most MAX_QUESTIONS non-empty lines."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return "\n".join(lines[:MAX_QUESTIONS])


def questions_list(expert: Dict[str, Any]) -> List[str]:
    return [l for l in (expert.get("RecommendedQuestions") or "").splitlines() if l.strip()]


# ── file storage ──────────────────────────────────────────────────────────────

def save_expert_file(expert_id: int, original_name: str, file_bytes: bytes) -> str:
    """Store the uploaded bytes as datasets/expert_<id>.<ext>; returns the path."""
    os.makedirs(DATASETS_DIR, exist_ok=True)
    ext = os.path.splitext(original_name or "")[1].lower() or ".xlsx"
    path = os.path.join(DATASETS_DIR, f"expert_{expert_id}{ext}")
    with open(path, "wb") as fh:
        fh.write(file_bytes)
    return path


def file_as_of(expert: Dict[str, Any]) -> Optional[str]:
    """The data-freshness stamp: the stored file's modification time (UTC).
    Query-backed experts have no stored file — they fetch live on every load."""
    if source_type(expert) != "file":
        return None
    try:
        from datetime import datetime
        ts = os.path.getmtime(expert["FilePath"])
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def source_type(expert: Dict[str, Any]) -> str:
    return (expert.get("SourceType") or "file").strip() or "file"


def is_loadable(expert: Dict[str, Any]) -> bool:
    """File experts need their stored file; query experts need their definition."""
    if source_type(expert) == "file":
        return bool(expert.get("FilePath"))
    return bool(expert.get("SourceConfig"))


# ── CRUD ──────────────────────────────────────────────────────────────────────

def create_expert(label, description, sheet_name, access_mode, allowed_users,
                  allowed_groups, questions, rows, cols, created_by) -> int:
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO DataExperts
               (Label, Description, FilePath, OriginalFileName, SheetName, AccessMode,
                AllowedUsers, AllowedGroups, RecommendedQuestions, Rows, Cols,
                IsActive, CreatedBy, CreatedAt, UpdatedBy, UpdatedAt)
               VALUES (?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (label, description, sheet_name, access_mode,
             allowed_users, allowed_groups, clean_questions(questions), rows, cols,
             created_by, now_utc(), created_by, now_utc()),
        )
        return cur.lastrowid


def create_query_expert(label, description, source_type_, source_config_json, sql_query,
                        access_mode, allowed_users, allowed_groups, questions,
                        rows, cols, created_by) -> int:
    """A query-backed expert (azure/sqlserver): no stored file, fetches live on load."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO DataExperts
               (Label, Description, FilePath, OriginalFileName, SheetName, AccessMode,
                AllowedUsers, AllowedGroups, RecommendedQuestions, Rows, Cols,
                IsActive, CreatedBy, CreatedAt, UpdatedBy, UpdatedAt,
                SourceType, SourceConfig, SqlQuery)
               VALUES (?, ?, '', '', NULL, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
            (label, description, access_mode, allowed_users, allowed_groups,
             clean_questions(questions), rows, cols,
             created_by, now_utc(), created_by, now_utc(),
             source_type_, source_config_json, sql_query),
        )
        return cur.lastrowid


def update_expert_source(expert_id: int, source_config_json: str, sql_query: str,
                         rows, cols, updated_by: str):
    with get_cursor() as cur:
        cur.execute(
            "UPDATE DataExperts SET SourceConfig=?, SqlQuery=?, Rows=?, Cols=?, "
            "UpdatedBy=?, UpdatedAt=? WHERE ExpertID=?",
            (source_config_json, sql_query, rows, cols, updated_by, now_utc(), expert_id),
        )


def set_expert_file(expert_id: int, file_path: str, original_name: str,
                    sheet_name, rows, cols, updated_by):
    with get_cursor() as cur:
        cur.execute(
            "UPDATE DataExperts SET FilePath=?, OriginalFileName=?, SheetName=?, "
            "Rows=?, Cols=?, UpdatedBy=?, UpdatedAt=? WHERE ExpertID=?",
            (file_path, original_name, sheet_name, rows, cols, updated_by, now_utc(), expert_id),
        )


def update_expert(expert_id: int, label, description, access_mode, allowed_users,
                  allowed_groups, questions, updated_by) -> bool:
    with get_cursor() as cur:
        cur.execute(
            "UPDATE DataExperts SET Label=?, Description=?, AccessMode=?, AllowedUsers=?, "
            "AllowedGroups=?, RecommendedQuestions=?, UpdatedBy=?, UpdatedAt=? WHERE ExpertID=?",
            (label, description, access_mode, allowed_users, allowed_groups,
             clean_questions(questions), updated_by, now_utc(), expert_id),
        )
        return cur.rowcount > 0


def set_expert_active(expert_id: int, active: bool, updated_by: str) -> bool:
    with get_cursor() as cur:
        cur.execute(
            "UPDATE DataExperts SET IsActive=?, UpdatedBy=?, UpdatedAt=? WHERE ExpertID=?",
            (1 if active else 0, updated_by, now_utc(), expert_id),
        )
        return cur.rowcount > 0


def get_expert(expert_id: int) -> Optional[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM DataExperts WHERE ExpertID=?", (expert_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def all_experts() -> List[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM DataExperts ORDER BY Label")
        return [dict(r) for r in cur.fetchall()]


def experts_for_user(username: str, groups: List[str]) -> List[Dict[str, Any]]:
    return [e for e in all_experts() if e["IsActive"] and is_loadable(e)
            and user_allowed(e, username, groups)]


def groups_in_use() -> List[str]:
    """Distinct AD group names referenced by active experts (for the .NET badge check)."""
    seen, out = set(), []
    for e in all_experts():
        if not e["IsActive"]:
            continue
        for g in parse_list(e.get("AllowedGroups", "")):
            key = _bare(g)
            if key not in seen:
                seen.add(key)
                out.append(g)
    return out
