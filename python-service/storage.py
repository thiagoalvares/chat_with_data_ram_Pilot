"""
Session storage backends
------------------------
Implements the production persistence pattern from the architecture doc in a way
that runs on a single laptop, with a real SQL Server backend scaffolded for
production.

  Production            Local stand-in / backend
  ------------------    ----------------------------------------------------
  SQL Server            SQLite file (disk backend)  ->  SQL Server (sqlserver backend)
  Network file share    Local folder (DATA_DIR)     ->  UNC share path (set DATA_DIR)
  Parquet conversion    pandas .to_parquet / .read_parquet

Backends, selected with STORAGE_BACKEND:

  memory     (default) — in-process dict; simplest; lost on restart.
  disk                 — SQLite + Parquet on local disk; restart-safe.
  sqlserver            — SQL Server (metadata) + Parquet on DATA_DIR (file share).
                         Production backend. Needs SQL_CONNECTION_STRING and the
                         `pyodbc` package + Microsoft ODBC Driver for SQL Server.

The .NET layer always supplies the session id. With `disk` or `sqlserver` the
Python service holds nothing between requests (load → work → save), which is what
lets production run many stateless instances behind a load balancer.
"""

import os
import json
import pickle
import sqlite3
import threading
from typing import Dict, Optional

import pandas as pd

from logger import logger

# Where local "file share" lives (override via env; point at a UNC path in prod).
STATE_DIR = os.environ.get("STATE_DIR", "local_state")
DATA_DIR  = os.environ.get("DATA_DIR",  os.path.join("local_state", "fileshare"))

# Simple fields persisted as JSON metadata (everything that isn't a big frame/result).
_META_FIELDS = (
    "schema", "schema_a", "schema_b", "schema_c", "schema_d",
    "filename", "label_a", "label_b", "label_c", "label_d",
    "history_standard", "history_variance",
    "join_hints", "manual_mode",
)
# DataFrame slots persisted as Parquet on the "file share".
_DF_FIELDS = ("df", "df_a", "df_b", "df_c", "df_d")


# ── Memory backend ────────────────────────────────────────────────────────────

class MemoryStore:
    """In-process dict — returns the same SessionData instance per id."""

    def __init__(self, session_factory):
        self._factory = session_factory
        self._sessions: Dict[str, object] = {}
        logger.info("Storage backend: memory (in-process)")

    def load(self, sid: str):
        if sid not in self._sessions:
            self._sessions[sid] = self._factory()
        return self._sessions[sid]

    def save(self, sid: str, sess) -> None:
        self._sessions[sid] = sess  # same object already cached

    def clear(self, sid: str) -> None:
        self._sessions.pop(sid, None)


# ── File-share base (data files on disk; subclasses persist metadata) ─────────

class _FileShareStore:
    """
    Shared logic for the persistent backends: DataFrames as Parquet and the last
    result as pickle, under DATA_DIR (the 'file share'). Subclasses implement how
    the small JSON metadata is stored (SQLite vs SQL Server).
    """

    def __init__(self, session_factory):
        self._factory = session_factory
        self._lock = threading.Lock()
        os.makedirs(DATA_DIR, exist_ok=True)

    # — metadata hooks (subclass responsibility) —
    def _load_meta(self, sid: str) -> Optional[dict]:
        raise NotImplementedError

    def _save_meta(self, sid: str, meta: dict) -> None:
        raise NotImplementedError

    def _clear_meta(self, sid: str) -> None:
        raise NotImplementedError

    # — shared file handling —
    def _dir(self, sid: str) -> str:
        d = os.path.join(DATA_DIR, sid)
        os.makedirs(d, exist_ok=True)
        return d

    def load(self, sid: str):
        sess = self._factory()
        with self._lock:
            meta = self._load_meta(sid)
            if meta:
                for f in _META_FIELDS:
                    if f in meta:
                        setattr(sess, f, meta[f])
            d = os.path.join(DATA_DIR, sid)
            for f in _DF_FIELDS:
                path = os.path.join(d, f"{f}.parquet")
                if os.path.exists(path):
                    try:
                        setattr(sess, f, pd.read_parquet(path))
                    except Exception as e:
                        logger.warning(f"Could not read {path}: {e}")
            lr = os.path.join(d, "last_result.pkl")
            if os.path.exists(lr):
                try:
                    with open(lr, "rb") as fh:
                        sess.last_result = pickle.load(fh)
                except Exception as e:
                    logger.warning(f"Could not read last_result for {sid}: {e}")
        return sess

    def save(self, sid: str, sess) -> None:
        with self._lock:
            self._save_meta(sid, {f: getattr(sess, f, None) for f in _META_FIELDS})
            d = self._dir(sid)
            for f in _DF_FIELDS:
                path = os.path.join(d, f"{f}.parquet")
                val = getattr(sess, f, None)
                if isinstance(val, pd.DataFrame):
                    try:
                        val.to_parquet(path, index=False)
                    except Exception as e:
                        logger.warning(f"Could not write {path}: {e}")
                elif os.path.exists(path):
                    os.remove(path)
            lr = os.path.join(d, "last_result.pkl")
            if getattr(sess, "last_result", None) is not None:
                try:
                    with open(lr, "wb") as fh:
                        pickle.dump(sess.last_result, fh)
                except Exception as e:
                    logger.warning(f"Could not write last_result for {sid}: {e}")
            elif os.path.exists(lr):
                os.remove(lr)

    def clear(self, sid: str) -> None:
        with self._lock:
            self._clear_meta(sid)
            d = os.path.join(DATA_DIR, sid)
            if os.path.isdir(d):
                for name in os.listdir(d):
                    try:
                        os.remove(os.path.join(d, name))
                    except OSError:
                        pass


# ── Disk backend (SQLite metadata) ────────────────────────────────────────────

class DiskStore(_FileShareStore):
    """SQLite for metadata/conversation, Parquet for data — restart-safe, local."""

    def __init__(self, session_factory):
        super().__init__(session_factory)
        os.makedirs(STATE_DIR, exist_ok=True)
        self._db_path = os.path.join(STATE_DIR, "sessions.db")
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                " sid TEXT PRIMARY KEY, meta TEXT,"
                " updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        logger.info(f"Storage backend: disk (SQLite={self._db_path}, fileshare={DATA_DIR})")

    def _load_meta(self, sid):
        with sqlite3.connect(self._db_path) as con:
            row = con.execute("SELECT meta FROM sessions WHERE sid=?", (sid,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save_meta(self, sid, meta):
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                "INSERT INTO sessions (sid, meta, updated_at) VALUES (?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(sid) DO UPDATE SET meta=excluded.meta, updated_at=CURRENT_TIMESTAMP",
                (sid, json.dumps(meta)),
            )

    def _clear_meta(self, sid):
        with sqlite3.connect(self._db_path) as con:
            con.execute("DELETE FROM sessions WHERE sid=?", (sid,))


# ── SQL Server backend (production) ───────────────────────────────────────────

class SqlServerStore(_FileShareStore):
    """
    Production backend: session/conversation metadata in SQL Server, data files as
    Parquet on the network file share (DATA_DIR set to the UNC path).

    Requires:
      - pip install pyodbc
      - Microsoft ODBC Driver for SQL Server installed on the host
      - SQL_CONNECTION_STRING env var, e.g.:
          Driver={ODBC Driver 18 for SQL Server};Server=tcp:HOST,1433;
          Database=ChatWithData;Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=no;
    """

    def __init__(self, session_factory):
        super().__init__(session_factory)
        self._conn_str = os.environ.get("SQL_CONNECTION_STRING", "").strip()
        if not self._conn_str:
            raise RuntimeError(
                "STORAGE_BACKEND=sqlserver requires SQL_CONNECTION_STRING to be set."
            )
        try:
            import pyodbc  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "STORAGE_BACKEND=sqlserver requires the 'pyodbc' package "
                "(pip install pyodbc) and the Microsoft ODBC Driver for SQL Server."
            ) from e
        self._table = os.environ.get("SQL_SESSIONS_TABLE", "dbo.sessions")
        self._ensure_table()
        logger.info(f"Storage backend: sqlserver (table={self._table}, fileshare={DATA_DIR})")

    def _connect(self):
        import pyodbc
        return pyodbc.connect(self._conn_str)

    def _ensure_table(self):
        ddl = (
            f"IF OBJECT_ID(N'{self._table}', N'U') IS NULL "
            f"CREATE TABLE {self._table} ("
            " sid NVARCHAR(128) NOT NULL PRIMARY KEY,"
            " meta NVARCHAR(MAX) NULL,"
            " updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME());"
        )
        with self._connect() as con:
            con.execute(ddl)
            con.commit()

    def _load_meta(self, sid):
        with self._connect() as con:
            cur = con.execute(f"SELECT meta FROM {self._table} WHERE sid = ?", sid)
            row = cur.fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def _save_meta(self, sid, meta):
        payload = json.dumps(meta)
        merge = (
            f"MERGE {self._table} AS t "
            "USING (SELECT ? AS sid, ? AS meta) AS s ON t.sid = s.sid "
            "WHEN MATCHED THEN UPDATE SET meta = s.meta, updated_at = SYSUTCDATETIME() "
            "WHEN NOT MATCHED THEN INSERT (sid, meta) VALUES (s.sid, s.meta);"
        )
        with self._connect() as con:
            con.execute(merge, sid, payload)
            con.commit()

    def _clear_meta(self, sid):
        with self._connect() as con:
            con.execute(f"DELETE FROM {self._table} WHERE sid = ?", sid)
            con.commit()


# ── Backend selection ─────────────────────────────────────────────────────────

_store = None
_store_lock = threading.Lock()

_BACKENDS = {"memory": MemoryStore, "disk": DiskStore, "sqlserver": SqlServerStore}


def get_store(session_factory):
    """Return the configured singleton store (memory by default)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                name = os.environ.get("STORAGE_BACKEND", "memory").strip().lower()
                cls = _BACKENDS.get(name, MemoryStore)
                _store = cls(session_factory)
    return _store
