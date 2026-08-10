"""
Query-backed Data Expert sources — Azure blob storage and SQL Server.

A Data Expert can now be backed by a live source instead of an uploaded file:

  azure      one or more blobs (parquet/csv/xlsx) in Azure storage, downloaded
             with the app's service principal and queried locally with DuckDB.
             The admin's SQL references each blob by its alias.
  sqlserver  a SQL statement executed BY the database (SQL Server or Azure
             SQL, same driver). Windows auth uses the identity the Python
             service runs as (the ITS service account); SQL auth stores the
             login with the same Fernet encryption as user API keys.

Both adapters end at the same place: a DataFrame that app.py writes to CSV
bytes and feeds through the EXACT upload path (_handle_upload) — the analytical
core, insights, and access control are untouched and unaware of the source.

The azure/duckdb/pyodbc imports are lazy: the service must start and serve
file-backed experts even when these optional packages (or the ODBC driver)
are not installed on the box.

SourceConfig JSON stored on the DataExperts row:
  azure:     {"blobs": [{"alias": "costs", "url": "https://acct.blob.core.windows.net/container/costs.parquet"}, ...]}
  sqlserver: {"server": "host[,port]", "database": "db", "auth": "windows"|"sql",
              "username": "...", "encrypted_password": "..."}  (sql auth only)

Egress note: the azure adapter talks to login.microsoftonline.com (token) and
the storage account host; the sqlserver adapter talks to the database host.
All are INBOUND data — nothing from user sessions is sent to these hosts.
"""

import json
import os
import re
import shutil
import tempfile
from typing import Any, Dict, List

import pandas as pd

from config import Config
from logger import logger

_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DataSourceError(Exception):
    """Admin-facing error: what went wrong and what to do about it."""


def parse_source_config(expert: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(expert.get("SourceConfig") or "{}")
    except Exception:
        return {}


def fetch_dataframe(expert: Dict[str, Any]) -> pd.DataFrame:
    """
    Run a query-backed expert's definition and return the result DataFrame.
    `expert` needs SourceType, SourceConfig (JSON) and SqlQuery keys — either a
    DataExperts row or an ad-hoc dict from the admin console's Test query.
    """
    source_type = (expert.get("SourceType") or "file").strip()
    config = parse_source_config(expert)
    sql = (expert.get("SqlQuery") or "").strip()

    if source_type == "azure":
        df = _fetch_azure(config, sql)
    elif source_type == "sqlserver":
        df = _fetch_sqlserver(config, sql)
    else:
        raise DataSourceError(f"Unknown source type: {source_type}")

    return _guard(df)


def _guard(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        raise DataSourceError("The query returned no rows. Check the SQL (and any WHERE filters).")
    if len(df.columns) == 0:
        raise DataSourceError("The query returned no columns.")
    max_rows = Config.DATASOURCE_MAX_ROWS
    if len(df) > max_rows:
        raise DataSourceError(
            f"The query returned {len(df):,} rows — more than the {max_rows:,} row limit. "
            f"Tighten the WHERE clause or aggregate in the SQL."
        )
    # Column labels must be strings for the downstream schema/prompt machinery.
    df.columns = [str(c) for c in df.columns]
    return df


# ── Azure blob + local DuckDB SQL ─────────────────────────────────────────────

def _azure_credential():
    if not (Config.AZURE_TENANT_ID and Config.AZURE_CLIENT_ID and Config.AZURE_CLIENT_SECRET):
        raise DataSourceError(
            "Azure is not configured on the server. Set AZURE_TENANT_ID, AZURE_CLIENT_ID "
            "and AZURE_CLIENT_SECRET (the app's service principal) in python-service/.env."
        )
    try:
        from azure.identity import ClientSecretCredential
    except ImportError:
        raise DataSourceError(
            "The Azure packages are not installed on the server. "
            "Run: pip install azure-identity azure-storage-blob"
        )
    return ClientSecretCredential(
        tenant_id=Config.AZURE_TENANT_ID,
        client_id=Config.AZURE_CLIENT_ID,
        client_secret=Config.AZURE_CLIENT_SECRET,
    )


def _validate_blobs(config: Dict[str, Any]) -> List[Dict[str, str]]:
    blobs = config.get("blobs") or []
    if not blobs:
        raise DataSourceError("Add at least one blob (alias = URL).")
    seen = set()
    for b in blobs:
        alias, url = (b.get("alias") or "").strip(), (b.get("url") or "").strip()
        if not _ALIAS_RE.match(alias):
            raise DataSourceError(
                f"'{alias or '(empty)'}' is not a valid alias — use letters, digits and _ "
                f"(it becomes the table name in your SQL)."
            )
        if alias.lower() in seen:
            raise DataSourceError(f"Duplicate alias '{alias}'.")
        seen.add(alias.lower())
        if not url.lower().startswith("https://"):
            raise DataSourceError(f"Blob URL for '{alias}' must start with https://")
        ext = os.path.splitext(url.split("?")[0])[1].lower()
        if ext not in (".parquet", ".csv", ".xlsx", ".xls"):
            raise DataSourceError(
                f"Blob for '{alias}' must be a .parquet, .csv or .xlsx file (got '{ext or 'no extension'}')."
            )
    return blobs


def _fetch_azure(config: Dict[str, Any], sql: str) -> pd.DataFrame:
    blobs = _validate_blobs(config)
    if len(blobs) > 1 and not sql:
        raise DataSourceError("With more than one blob, a SQL statement is required to combine them.")

    credential = _azure_credential()
    try:
        from azure.storage.blob import BlobClient
    except ImportError:
        raise DataSourceError(
            "The Azure packages are not installed on the server. "
            "Run: pip install azure-identity azure-storage-blob"
        )

    tempdir = tempfile.mkdtemp(prefix="cwd_azure_")
    try:
        local_files = {}
        for b in blobs:
            alias, url = b["alias"].strip(), b["url"].strip()
            ext = os.path.splitext(url.split("?")[0])[1].lower()
            path = os.path.join(tempdir, f"{alias}{ext}")
            try:
                blob_client = BlobClient.from_blob_url(url, credential=credential)
                with open(path, "wb") as fh:
                    blob_client.download_blob().readinto(fh)
            except Exception as e:
                raise DataSourceError(_friendly_azure_error(alias, e))
            local_files[alias] = path
            logger.info(f"Azure blob fetched | alias={alias} | bytes={os.path.getsize(path)}")
        return query_local_files(local_files, sql)
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def _friendly_azure_error(alias: str, e: Exception) -> str:
    msg = str(e)
    if "AuthenticationFailed" in msg or "invalid_client" in msg or "AADSTS" in msg:
        return (f"Azure rejected the app's credentials while fetching '{alias}'. "
                f"The service principal secret may be wrong or expired — contact the admin/ITS.")
    if "AuthorizationFailure" in msg or "AuthorizationPermissionMismatch" in msg:
        return (f"The app's Azure identity is not allowed to read '{alias}'. "
                f"ITS must grant it the 'Storage Blob Data Reader' role on that container.")
    if "BlobNotFound" in msg or "ContainerNotFound" in msg:
        return f"The blob for '{alias}' was not found — check the URL."
    return f"Could not fetch '{alias}' from Azure: {msg[:300]}"


def query_local_files(files_by_alias: Dict[str, str], sql: str) -> pd.DataFrame:
    """
    Run the admin's SQL over local data files with DuckDB, each file exposed as
    a view named by its alias. Split out from _fetch_azure so it can be
    exercised without an Azure account.
    """
    try:
        import duckdb
    except ImportError:
        raise DataSourceError("DuckDB is not installed on the server. Run: pip install duckdb")

    con = duckdb.connect()
    try:
        for alias, path in files_by_alias.items():
            if not _ALIAS_RE.match(alias):
                raise DataSourceError(f"'{alias}' is not a valid alias.")
            ext = os.path.splitext(path)[1].lower()
            safe_path = path.replace("'", "''")
            if ext == ".parquet":
                con.execute(f"CREATE VIEW {alias} AS SELECT * FROM read_parquet('{safe_path}')")
            elif ext == ".csv":
                con.execute(f"CREATE VIEW {alias} AS SELECT * FROM read_csv_auto('{safe_path}')")
            else:  # .xlsx/.xls — first sheet, via pandas
                con.register(alias, pd.read_excel(path))
        if not sql:
            sql = f"SELECT * FROM {next(iter(files_by_alias))}"
        try:
            return con.execute(sql).df()
        except Exception as e:
            raise DataSourceError(f"The SQL failed: {str(e)[:400]}")
    finally:
        con.close()


# ── SQL Server / Azure SQL ────────────────────────────────────────────────────

def _fetch_sqlserver(config: Dict[str, Any], sql: str) -> pd.DataFrame:
    server = (config.get("server") or "").strip()
    database = (config.get("database") or "").strip()
    auth = (config.get("auth") or "windows").strip()
    if not server or not database:
        raise DataSourceError("Server and database are required.")
    if not sql:
        raise DataSourceError("A SQL statement is required for a SQL Server expert.")

    try:
        import pyodbc
    except ImportError:
        raise DataSourceError("pyodbc is not installed on the server. Run: pip install pyodbc")

    parts = [
        f"Driver={{{Config.ODBC_DRIVER}}}",
        f"Server={server}",
        f"Database={database}",
        "Encrypt=yes",
        "TrustServerCertificate=yes",
    ]
    if auth == "windows":
        parts.append("Trusted_Connection=yes")
    else:
        username = (config.get("username") or "").strip()
        encrypted = config.get("encrypted_password") or ""
        if not username or not encrypted:
            raise DataSourceError("SQL login authentication needs a username and password.")
        from services.crypto import decrypt_api_key
        try:
            password = decrypt_api_key(encrypted)
        except Exception:
            raise DataSourceError("The stored SQL password could not be read — re-enter it in the expert's settings.")
        parts.append(f"Uid={username}")
        parts.append(f"Pwd={password}")

    try:
        conn = pyodbc.connect(";".join(parts), timeout=20)
    except Exception as e:
        raise DataSourceError(_friendly_sql_error(server, auth, e))
    try:
        try:
            return pd.read_sql(sql, conn)
        except Exception as e:
            raise DataSourceError(f"The SQL failed: {str(e)[:400]}")
    finally:
        conn.close()


def _friendly_sql_error(server: str, auth: str, e: Exception) -> str:
    msg = str(e)
    if "Login failed" in msg:
        if auth == "windows":
            return (f"SQL Server rejected the app's Windows identity on {server}. "
                    f"ITS must grant the service account the Python service runs as read access (db_datareader).")
        return f"SQL Server rejected the login on {server}. Check the username/password."
    if "IM002" in msg or "driver" in msg.lower():
        return (f"The ODBC driver '{Config.ODBC_DRIVER}' is not installed on this machine "
                f"(or set ODBC_DRIVER in .env to the installed driver's name).")
    if "timeout" in msg.lower() or "08001" in msg:
        return f"Could not reach SQL Server at {server} — check the server name and network/firewall."
    return f"SQL Server connection failed: {msg[:300]}"
