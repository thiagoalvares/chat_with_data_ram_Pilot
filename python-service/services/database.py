"""
SQLite persistence for per-user API keys, usage tracking, and model selection.

Design notes (deviations from the original plan, all deliberate):
- DB path is anchored to the python-service folder (not the process CWD), so it
  works no matter where the service is started from. Override with DB_PATH.
- All timestamps are stored as UTC 'YYYY-MM-DD HH:MM:SS' strings via now_utc(),
  and every query filter uses the same format — consistent comparisons.
- UsageLog has a RequestID (one UUID per question). Questions are counted as
  COUNT(DISTINCT RequestID), which survives repeated question text and retries.
- WAL journal mode: the service runs one process with many threads; WAL gives
  safe concurrent readers alongside writes.
- QuestionText is truncated at 500 chars before storage (usage analytics does
  not need full prompts sitting on disk forever).
"""

import os
import sqlite3
import threading
from datetime import datetime
from typing import Optional, Dict, List, Any
from contextlib import contextmanager

from config import Config
from logger import logger

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chatdata.db")
DB_PATH = Config.DB_PATH or _DEFAULT_DB

QUESTION_MAX_CHARS = 500

_thread_local = threading.local()


def now_utc() -> str:
    """Canonical timestamp format used everywhere in this DB."""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def get_connection() -> sqlite3.Connection:
    if not hasattr(_thread_local, "connection"):
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _thread_local.connection = conn
    return _thread_local.connection


@contextmanager
def get_cursor():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        cursor.close()


def initialize_database():
    """Create tables if they don't exist. Called once at startup."""
    schema = """
    CREATE TABLE IF NOT EXISTS Users (
        UserID INTEGER PRIMARY KEY AUTOINCREMENT,
        Username TEXT UNIQUE NOT NULL,
        APIKey TEXT NOT NULL,
        CreatedAt TEXT,
        UpdatedAt TEXT,
        LastLogin TEXT,
        IsActive INTEGER DEFAULT 1
    );
    CREATE INDEX IF NOT EXISTS idx_users_username ON Users(Username);

    CREATE TABLE IF NOT EXISTS UsageLog (
        LogID INTEGER PRIMARY KEY AUTOINCREMENT,
        UserID INTEGER NOT NULL,
        Username TEXT NOT NULL,
        SessionID TEXT NOT NULL,
        RequestID TEXT NOT NULL,
        Timestamp TEXT NOT NULL,
        Mode TEXT NOT NULL,
        QuestionText TEXT,
        CallType TEXT NOT NULL,
        Model TEXT NOT NULL,
        PromptTokens INTEGER NOT NULL,
        CompletionTokens INTEGER NOT NULL,
        TotalTokens INTEGER NOT NULL,
        PromptCost REAL NOT NULL,
        CompletionCost REAL NOT NULL,
        TotalCost REAL NOT NULL,
        Success INTEGER NOT NULL,
        ErrorMessage TEXT,
        FOREIGN KEY (UserID) REFERENCES Users(UserID)
    );
    CREATE INDEX IF NOT EXISTS idx_usage_username ON UsageLog(Username);
    CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON UsageLog(Timestamp);
    CREATE INDEX IF NOT EXISTS idx_usage_request ON UsageLog(RequestID);
    CREATE INDEX IF NOT EXISTS idx_usage_model ON UsageLog(Model);

    CREATE TABLE IF NOT EXISTS ModelConfig (
        ConfigID INTEGER PRIMARY KEY AUTOINCREMENT,
        ModelName TEXT NOT NULL,
        IsActive INTEGER DEFAULT 0,
        SetBy TEXT,
        SetAt TEXT,
        InputPrice REAL,
        OutputPrice REAL
    );

    CREATE TABLE IF NOT EXISTS ModelChangeLog (
        ChangeID INTEGER PRIMARY KEY AUTOINCREMENT,
        PreviousModel TEXT,
        NewModel TEXT NOT NULL,
        ChangedBy TEXT NOT NULL,
        ChangedAt TEXT NOT NULL,
        Reason TEXT
    );

    CREATE TABLE IF NOT EXISTS DataExperts (
        ExpertID INTEGER PRIMARY KEY AUTOINCREMENT,
        Label TEXT NOT NULL,
        Description TEXT,
        FilePath TEXT NOT NULL,
        OriginalFileName TEXT,
        SheetName TEXT,
        AccessMode TEXT NOT NULL DEFAULT 'restricted',
        AllowedUsers TEXT DEFAULT '',
        AllowedGroups TEXT DEFAULT '',
        RecommendedQuestions TEXT DEFAULT '',
        Rows INTEGER,
        Cols INTEGER,
        IsActive INTEGER DEFAULT 1,
        CreatedBy TEXT,
        CreatedAt TEXT,
        UpdatedBy TEXT,
        UpdatedAt TEXT,
        SourceType TEXT NOT NULL DEFAULT 'file',
        SourceConfig TEXT DEFAULT '',
        SqlQuery TEXT DEFAULT ''
    );
    """
    with get_cursor() as cursor:
        cursor.executescript(schema)
        # Migrate pre-batch-3 databases: add the query-source columns in place.
        cursor.execute("PRAGMA table_info(DataExperts)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        for col, ddl in (
            ("SourceType", "TEXT NOT NULL DEFAULT 'file'"),
            ("SourceConfig", "TEXT DEFAULT ''"),
            ("SqlQuery", "TEXT DEFAULT ''"),
        ):
            if col not in existing_cols:
                cursor.execute(f"ALTER TABLE DataExperts ADD COLUMN {col} {ddl}")
        cursor.execute("SELECT COUNT(*) FROM ModelConfig WHERE IsActive = 1")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO ModelConfig (ModelName, IsActive, SetBy, SetAt, InputPrice, OutputPrice) "
                "VALUES (?, 1, 'system', ?, 2.00, 10.00)",
                (Config._ENV_LLM_MODEL, now_utc()),
            )
    logger.info(f"Database ready at {DB_PATH}")


# ── Users ─────────────────────────────────────────────────────────────────────

def user_exists(username: str) -> bool:
    with get_cursor() as cursor:
        cursor.execute("SELECT 1 FROM Users WHERE Username = ?", (username,))
        return cursor.fetchone() is not None


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    with get_cursor() as cursor:
        cursor.execute("SELECT * FROM Users WHERE Username = ?", (username,))
        row = cursor.fetchone()
        return dict(row) if row else None


def create_user(username: str, encrypted_api_key: str) -> int:
    with get_cursor() as cursor:
        ts = now_utc()
        cursor.execute(
            "INSERT INTO Users (Username, APIKey, CreatedAt, UpdatedAt, LastLogin) VALUES (?, ?, ?, ?, ?)",
            (username, encrypted_api_key, ts, ts, ts),
        )
        return cursor.lastrowid


def update_user_api_key(username: str, encrypted_api_key: str) -> bool:
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE Users SET APIKey = ?, UpdatedAt = ? WHERE Username = ?",
            (encrypted_api_key, now_utc(), username),
        )
        return cursor.rowcount > 0


def update_user_last_login(username: str):
    with get_cursor() as cursor:
        cursor.execute("UPDATE Users SET LastLogin = ? WHERE Username = ?", (now_utc(), username))


def get_all_users() -> List[Dict[str, Any]]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT UserID, Username, CreatedAt, UpdatedAt, LastLogin, IsActive FROM Users ORDER BY Username"
        )
        return [dict(row) for row in cursor.fetchall()]


# ── Usage log ─────────────────────────────────────────────────────────────────

def log_usage(user_id: int, username: str, session_id: str, request_id: str,
              mode: str, question_text: str, call_type: str, model: str,
              prompt_tokens: int, completion_tokens: int, total_tokens: int,
              prompt_cost: float, completion_cost: float, total_cost: float,
              success: bool, error_message: str = None):
    with get_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO UsageLog (
                UserID, Username, SessionID, RequestID, Timestamp, Mode, QuestionText,
                CallType, Model, PromptTokens, CompletionTokens, TotalTokens,
                PromptCost, CompletionCost, TotalCost, Success, ErrorMessage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, session_id, request_id, now_utc(), mode,
             (question_text or "")[:QUESTION_MAX_CHARS], call_type, model,
             prompt_tokens, completion_tokens, total_tokens,
             prompt_cost, completion_cost, total_cost,
             1 if success else 0, error_message),
        )


def _date_filter(where: str, params: list, start_date: str, end_date: str):
    if start_date:
        where += " AND Timestamp >= ?"
        params.append(start_date)
    if end_date:
        where += " AND Timestamp <= ?"
        params.append(end_date)
    return where, params


def get_user_usage_summary(username: str, start_date: str = None, end_date: str = None) -> Dict[str, Any]:
    with get_cursor() as cursor:
        where, params = _date_filter("WHERE Username = ? AND Success = 1", [username], start_date, end_date)
        cursor.execute(f"SELECT COUNT(DISTINCT RequestID) FROM UsageLog {where}", params)
        total_questions = cursor.fetchone()[0] or 0
        cursor.execute(f"SELECT SUM(TotalTokens), SUM(TotalCost) FROM UsageLog {where}", params)
        row = cursor.fetchone()
        total_tokens = row[0] or 0
        total_cost = row[1] or 0.0
        avg = (total_cost / total_questions) if total_questions else 0.0
        return {
            "total_questions": total_questions,
            "total_tokens": int(total_tokens),
            "total_cost": round(total_cost, 4),
            "avg_cost_per_question": round(avg, 4),
        }


def get_user_usage_chart(username: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT DATE(Timestamp), SUM(TotalTokens), SUM(TotalCost)
            FROM UsageLog
            WHERE Username = ? AND Success = 1 AND Timestamp >= ? AND Timestamp <= ?
            GROUP BY DATE(Timestamp) ORDER BY DATE(Timestamp)
            """,
            (username, start_date, end_date),
        )
        return [{"date": r[0], "tokens": int(r[1] or 0), "cost": round(r[2] or 0.0, 4)} for r in cursor.fetchall()]


def get_user_usage_history(username: str, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    """One row per question (grouped by RequestID), newest first."""
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT MIN(Timestamp), Mode, MAX(QuestionText), MAX(Model),
                   SUM(TotalTokens), SUM(TotalCost), MIN(Success)
            FROM UsageLog
            WHERE Username = ?
            GROUP BY RequestID
            ORDER BY MIN(Timestamp) DESC
            LIMIT ? OFFSET ?
            """,
            (username, limit, offset),
        )
        return [
            {"timestamp": r[0], "mode": r[1], "question": r[2], "model": r[3],
             "tokens": int(r[4] or 0), "cost": round(r[5] or 0.0, 4), "success": bool(r[6])}
            for r in cursor.fetchall()
        ]


# ── Admin analytics ───────────────────────────────────────────────────────────

def get_all_users_usage_summary(start_date: str = None, end_date: str = None) -> Dict[str, Any]:
    with get_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM Users WHERE IsActive = 1")
        total_users = cursor.fetchone()[0]
        where, params = _date_filter("WHERE Success = 1", [], start_date, end_date)
        cursor.execute(f"SELECT COUNT(DISTINCT Username) FROM UsageLog {where}", params)
        active_users = cursor.fetchone()[0]
        cursor.execute(
            f"SELECT COUNT(DISTINCT RequestID), SUM(TotalTokens), SUM(TotalCost) FROM UsageLog {where}",
            params,
        )
        row = cursor.fetchone()
        return {
            "total_users": total_users,
            "active_users": active_users,
            "total_questions": row[0] or 0,
            "total_tokens": int(row[1] or 0),
            "total_cost": round(row[2] or 0.0, 2),
        }


def get_usage_by_user(start_date: str = None, end_date: str = None, limit: int = 10) -> List[Dict[str, Any]]:
    with get_cursor() as cursor:
        where, params = _date_filter("WHERE Success = 1", [], start_date, end_date)
        params.append(limit)
        cursor.execute(
            f"""
            SELECT Username, COUNT(DISTINCT RequestID), SUM(TotalTokens), SUM(TotalCost)
            FROM UsageLog {where}
            GROUP BY Username ORDER BY SUM(TotalCost) DESC LIMIT ?
            """,
            params,
        )
        return [
            {"username": r[0], "questions": r[1], "tokens": int(r[2] or 0), "cost": round(r[3] or 0.0, 4)}
            for r in cursor.fetchall()
        ]


def get_usage_by_model(start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
    with get_cursor() as cursor:
        where, params = _date_filter("WHERE Success = 1", [], start_date, end_date)
        cursor.execute(
            f"""
            SELECT Model, COUNT(*), SUM(TotalTokens), SUM(TotalCost)
            FROM UsageLog {where}
            GROUP BY Model ORDER BY SUM(TotalCost) DESC
            """,
            params,
        )
        return [
            {"model": r[0], "calls": r[1], "tokens": int(r[2] or 0), "cost": round(r[3] or 0.0, 4)}
            for r in cursor.fetchall()
        ]


# ── Model configuration ───────────────────────────────────────────────────────

def get_current_model() -> Optional[Dict[str, Any]]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT ModelName, InputPrice, OutputPrice, SetBy, SetAt FROM ModelConfig WHERE IsActive = 1"
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def set_current_model(model_name: str, admin_username: str, input_price: float,
                      output_price: float, reason: str = ""):
    with get_cursor() as cursor:
        cursor.execute("SELECT ModelName FROM ModelConfig WHERE IsActive = 1")
        current = cursor.fetchone()
        previous_model = current[0] if current else None

        cursor.execute("UPDATE ModelConfig SET IsActive = 0")
        cursor.execute("SELECT ConfigID FROM ModelConfig WHERE ModelName = ?", (model_name,))
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                "UPDATE ModelConfig SET IsActive = 1, SetBy = ?, SetAt = ?, InputPrice = ?, OutputPrice = ? "
                "WHERE ModelName = ?",
                (admin_username, now_utc(), input_price, output_price, model_name),
            )
        else:
            cursor.execute(
                "INSERT INTO ModelConfig (ModelName, IsActive, SetBy, SetAt, InputPrice, OutputPrice) "
                "VALUES (?, 1, ?, ?, ?, ?)",
                (model_name, admin_username, now_utc(), input_price, output_price),
            )
        cursor.execute(
            "INSERT INTO ModelChangeLog (PreviousModel, NewModel, ChangedBy, ChangedAt, Reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (previous_model, model_name, admin_username, now_utc(), reason),
        )
    logger.info(f"Model changed {previous_model} -> {model_name} by {admin_username}")


def get_model_change_history(limit: int = 20) -> List[Dict[str, Any]]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT PreviousModel, NewModel, ChangedBy, ChangedAt, Reason "
            "FROM ModelChangeLog ORDER BY ChangedAt DESC LIMIT ?",
            (limit,),
        )
        return [
            {"previous_model": r[0], "new_model": r[1], "changed_by": r[2], "changed_at": r[3], "reason": r[4]}
            for r in cursor.fetchall()
        ]
