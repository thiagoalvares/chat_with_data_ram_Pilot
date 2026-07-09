"""
Upload-time insights — PURE PYTHON, no LLM calls.

Three deterministic helpers that run once when a file is uploaded:
  build_profile(df)      -> data profile card (rows, date range, nulls, top values)
  find_anomalies(df)     -> gentle "did you notice?" flags (duplicates, gaps, outliers)
  suggest_questions(df)  -> starter question chips built from the schema

All results are computed with pandas only, so they are fast, cost nothing, and
cannot disagree with the data. None of this touches the two-call pipeline.
"""

from typing import List, Optional

import numpy as np
import pandas as pd

MAX_SUGGESTIONS = 4
MAX_ANOMALIES = 4


# ── column classification ─────────────────────────────────────────────────────

def _numeric_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _date_cols(df: pd.DataFrame) -> List[str]:
    out = []
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            out.append(c)
            continue
        name = str(c).lower()
        if any(k in name for k in ("date", "start", "finish", "period", "month")):
            parsed = pd.to_datetime(df[c], errors="coerce")
            if parsed.notna().mean() > 0.7:
                out.append(c)
    return out


def _categorical_cols(df: pd.DataFrame, max_unique: int = 60) -> List[str]:
    out = []
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_datetime64_any_dtype(df[c]):
            continue
        n = df[c].nunique(dropna=True)
        if 1 < n <= max_unique:
            out.append(c)
    return out


def _money_like(cols: List[str]) -> List[str]:
    keys = ("cost", "amount", "price", "usd", "dollar", "budget", "value", "hours", "hrs", "qty", "quantity", "sales")
    ranked = [c for c in cols if any(k in str(c).lower() for k in keys)]
    return ranked or cols


# ── profile ───────────────────────────────────────────────────────────────────

def build_profile(df: pd.DataFrame) -> dict:
    """Small, instant summary for the sidebar profile card."""
    profile = {
        "rows": int(len(df)),
        "cols": int(len(df.columns)),
        "date_range": None,
        "null_notes": [],
        "top_values": [],
    }

    dcols = _date_cols(df)
    if dcols:
        parsed = pd.to_datetime(df[dcols[0]], errors="coerce").dropna()
        if not parsed.empty:
            profile["date_range"] = {
                "column": str(dcols[0]),
                "from": parsed.min().strftime("%b %Y"),
                "to": parsed.max().strftime("%b %Y"),
            }

    nulls = df.isna().sum()
    for c, n in nulls[nulls > 0].sort_values(ascending=False).head(3).items():
        profile["null_notes"].append({"column": str(c), "count": int(n)})

    for c in _categorical_cols(df)[:2]:
        vc = df[c].value_counts(dropna=True)
        if not vc.empty:
            profile["top_values"].append({"column": str(c), "value": str(vc.index[0]), "count": int(vc.iloc[0])})

    return profile


# ── anomalies ─────────────────────────────────────────────────────────────────

def find_anomalies(df: pd.DataFrame) -> List[str]:
    """Deterministic pandas checks; returns short human sentences."""
    notes: List[str] = []

    # Fully duplicated rows
    dup = int(df.duplicated().sum())
    if dup > 0:
        notes.append(f"{dup} fully duplicated row(s).")

    # Duplicates in an id-like column
    id_cols = [c for c in df.columns if any(k in str(c).lower() for k in ("id", "number", "po", "uid", "key"))]
    for c in id_cols[:1]:
        d = int(df[c].dropna().duplicated().sum())
        if d > 0:
            notes.append(f"{d} duplicate value(s) in {c}.")

    # Gaps in the primary date column
    dcols = _date_cols(df)
    if dcols:
        parsed = pd.to_datetime(df[dcols[0]], errors="coerce").dropna().sort_values()
        if len(parsed) > 10:
            gaps = parsed.diff().dt.days
            biggest = gaps.max()
            if pd.notna(biggest) and biggest >= 21:
                notes.append(f"A {int(biggest)}-day gap in {dcols[0]}.")

    # Negative values in money/hours-like columns
    for c in _money_like(_numeric_cols(df))[:3]:
        neg = int((pd.to_numeric(df[c], errors="coerce") < 0).sum())
        if neg > 0:
            notes.append(f"{neg} negative value(s) in {c}.")
            break

    # Extreme outliers (IQR fence) on the first money-like column
    ncols = _money_like(_numeric_cols(df))
    if ncols:
        s = pd.to_numeric(df[ncols[0]], errors="coerce").dropna()
        if len(s) > 20:
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:
                out = int(((s < q1 - 3 * iqr) | (s > q3 + 3 * iqr)).sum())
                if out > 0:
                    notes.append(f"{out} extreme outlier(s) in {ncols[0]}.")

    return notes[:MAX_ANOMALIES]


# ── starter questions ─────────────────────────────────────────────────────────

def suggest_questions(df: pd.DataFrame, mode: str = "standard",
                      label_a: Optional[str] = None, label_b: Optional[str] = None) -> List[str]:
    """Template-based starter chips built from the schema. No LLM."""
    if mode == "variance":
        a = label_a or "the baseline"
        b = label_b or "the current file"
        return [
            f"What records are new in {b} vs {a}?",
            f"What changed the most between {a} and {b}?",
            "Total variance by the largest category",
            "Any records missing from the newer file?",
        ][:MAX_SUGGESTIONS]

    ncols = _money_like(_numeric_cols(df))
    ccols = _categorical_cols(df)
    dcols = _date_cols(df)
    out: List[str] = []

    if ncols and ccols:
        out.append(f"Total {ncols[0]} by {ccols[0]}")
    if ncols and dcols:
        out.append(f"{ncols[0]} trend by month")
    if ncols and ccols:
        out.append(f"Top 10 {ccols[min(1, len(ccols) - 1)]} by {ncols[0]}")
    if ncols:
        out.append(f"Any negative or missing {ncols[0]} values?")
    if not out:
        out = ["Summarize this data", "How many rows per category?", "Show me the first insights you find"]

    return out[:MAX_SUGGESTIONS]
