import pandas as pd
from typing import Optional, Dict, List, Any
from logger import logger


class SessionData:
    """Holds all data for one user session."""
    def __init__(self):
        self.mode: str = "standard"

        # Standard mode
        self.df: Optional[pd.DataFrame] = None
        self.schema: Optional[str] = None
        self.filename: Optional[str] = None

        # Variance mode
        self.df_a: Optional[pd.DataFrame] = None
        self.df_b: Optional[pd.DataFrame] = None
        self.schema_a: Optional[str] = None
        self.schema_b: Optional[str] = None
        self.label_a: str = "File A"
        self.label_b: str = "File B"

        # MS Project mode
        self.df_tasks: Optional[pd.DataFrame] = None
        self.df_resources: Optional[pd.DataFrame] = None
        self.df_assignments: Optional[pd.DataFrame] = None
        self.df_earned_value: Optional[pd.DataFrame] = None
        self.schema_tasks: Optional[str] = None
        self.schema_resources: Optional[str] = None
        self.schema_assignments: Optional[str] = None
        self.schema_earned_value: Optional[str] = None

        # MS Project — Monte Carlo (NEW)
        self.montecarlo_result: Optional[dict] = None
        self.montecarlo_optimistic_pct: float = 0.85
        self.montecarlo_pessimistic_pct: float = 1.25

        # MS Project — Narrative document (NEW)
        self.narrative_text: Optional[str] = None
        self.narrative_filename: Optional[str] = None

        # IMS Health Check
        self.ims_df: Optional[pd.DataFrame] = None
        self.ims_filename: Optional[str] = None
        self.ims_results = None
        self.ims_summary = None

        # Chat history - mode-specific (tab-independent)
        self.history_standard: List[Dict] = []
        self.history_variance: List[Dict] = []
        self.history_msproject: List[Dict] = []

        # Export support — stores last query result for Excel export
        self.last_result: Optional[Any] = None

    def get_history(self, mode: str) -> List[Dict]:
        """Get chat history for specific mode."""
        if mode == "standard":
            return self.history_standard
        elif mode == "variance":
            return self.history_variance
        elif mode == "msproject":
            return self.history_msproject
        else:
            return []

    def clear_history(self, mode: str = None):
        """Clear chat history. If mode specified, clear only that mode."""
        if mode is None:
            # Clear all mode histories
            self.history_standard = []
            self.history_variance = []
            self.history_msproject = []
        elif mode == "standard":
            self.history_standard = []
        elif mode == "variance":
            self.history_variance = []
        elif mode == "msproject":
            self.history_msproject = []
        self.last_result = None

    def clear(self):
        """Full reset - clears everything including file data."""
        self.__init__()


# Session persistence is delegated to a pluggable storage backend
# (memory by default, or disk = SQLite + Parquet for the production pattern).
# See storage.py. Select via the STORAGE_BACKEND env var.
from storage import get_store


def _store():
    return get_store(SessionData)


def get_session(sid: str) -> SessionData:
    """Load the session for `sid` from the configured store."""
    return _store().load(sid)


def save_session(sid: str, sess: SessionData) -> None:
    """
    Persist the session back to the store. A no-op for the memory backend
    (same object is already cached); writes SQLite + Parquet for the disk
    backend, which is what keeps the service stateless/restart-safe.
    """
    _store().save(sid, sess)


def clear_session(sid: str):
    _store().clear(sid)
    logger.info(f"Session cleared: {sid}")


def get_schema_info(df: pd.DataFrame) -> str:
    """Build rich schema string for LLM prompt injection."""
    lines = [f"Shape: {df.shape[0]} rows x {df.shape[1]} columns\n", "Columns:"]
    for col in df.columns:
        dtype    = str(df[col].dtype)
        n_unique = df[col].nunique()
        samples  = df[col].dropna().head(3).tolist()
        lines.append(f"  - {col} ({dtype}) | {n_unique} unique | sample: {samples}")
    lines.append("\nFirst 3 rows:")
    lines.append(df.head(3).to_string(index=False))
    return "\n".join(lines)
