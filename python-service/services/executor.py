import re
import os
import pandas as pd
import traceback
from typing import Tuple, Dict, Any, Optional, Set
from datetime import datetime, timedelta
from logger import logger

# ── Calendar loader ───────────────────────────────────────────────────────────
_calendar_cache: Optional[Set] = None
_calendar_loaded: bool = False

CALENDAR_FILE_PATH = os.path.join("static", "calendar", "working_days.xlsx")


def _load_calendar() -> Optional[Set]:
    """Load GA-ASI 9-80 working days calendar. Cached after first load."""
    global _calendar_cache, _calendar_loaded
    if _calendar_loaded:
        return _calendar_cache
    try:
        df   = pd.read_excel(CALENDAR_FILE_PATH)
        col  = df.columns[0]
        dates = pd.to_datetime(df[col], errors='coerce').dropna()
        _calendar_cache  = {d.date() for d in dates}
        _calendar_loaded = True
        logger.info(f"GA-ASI 9-80 calendar loaded: {len(_calendar_cache)} working days")
        return _calendar_cache
    except FileNotFoundError:
        _calendar_loaded = True
        logger.warning("GA-ASI 9-80 calendar file not found — date calculations will use calendar days")
        return None
    except Exception as e:
        _calendar_loaded = True
        logger.error(f"Calendar load error: {str(e)}")
        return None


def _build_working_days_between(calendar: Set):
    """
    Build a working_days_between() function using the GA-ASI 9-80 calendar.
    Injected into the LLM exec scope for date difference calculations.
    """
    def working_days_between(date1, date2) -> int:
        """
        Count working days between two dates using the GA-ASI 9-80 calendar.
        Returns positive if date2 > date1, negative if date2 < date1.
        """
        try:
            d1 = pd.Timestamp(date1).date()
            d2 = pd.Timestamp(date2).date()
            if d1 == d2:
                return 0
            sign = 1 if d2 > d1 else -1
            start, end = (d1, d2) if d2 > d1 else (d2, d1)
            count = sum(1 for d in calendar if start < d <= end)
            return sign * count
        except Exception:
            # Fallback to calendar days if dates can't be parsed
            try:
                return int((pd.Timestamp(date2) - pd.Timestamp(date1)).days)
            except Exception:
                return 0
    return working_days_between


def execute_generated_code(code: str, prev_result: Any = None, **dataframes: pd.DataFrame) -> Tuple[bool, str, Any, Dict]:
    """
    Execute LLM-generated pandas code.
    Accepts any named DataFrames (df, df_a, df_b, df_tasks, etc.)
    Optionally accepts prev_result from the previous query for context.
    Injects working_days_between() when GA-ASI 9-80 calendar is available.
    Returns (success, result_string, raw_result, metadata)
    """
    calendar = _load_calendar()

    # Single namespace — used as globals so lambdas/functions can reach everything
    namespace = {"pd": pd, "np": __import__("numpy"), **dataframes}

    # Inject prev_result if available
    if prev_result is not None:
        namespace["prev_result"] = prev_result

    if calendar:
        namespace["working_days_between"] = _build_working_days_between(calendar)
        namespace["calendar_available"]   = True
    else:
        # Calendar missing — still provide working_days_between so LLM code
        # never crashes; it falls back to calendar-day counting.
        def _fallback_working_days_between(date1, date2):
            try:
                return int((pd.Timestamp(date2) - pd.Timestamp(date1)).days)
            except Exception:
                return 0
        namespace["working_days_between"] = _fallback_working_days_between
        namespace["calendar_available"]   = False

    try:
        code = _clean_code(code)
        logger.debug(f"Executing code:\n{code}")

        # Pass namespace as globals ONLY (no separate locals dict).
        # This ensures lambdas and df.apply() callbacks can see pd, np,
        # working_days_between, and all DataFrames.
        exec(code, namespace)

        if "result" not in namespace:
            return False, "Code executed but did not produce a variable named `result`.", None, {}

        raw    = namespace["result"]
        result_str, metadata = _format_result(raw)
        return True, result_str, raw, metadata

    except Exception:
        error = traceback.format_exc()
        logger.warning(f"Code execution failed:\n{error}")
        return False, error, None, {}


def _clean_code(code: str) -> str:
    """Strip markdown fences and any leading prose."""
    code = code.strip()

    # Extract content from ``` blocks
    if "```" in code:
        lines    = code.split("\n")
        inside   = False
        extracted = []
        for line in lines:
            if line.strip().startswith("```"):
                inside = not inside
                continue
            if inside:
                extracted.append(line)
        if extracted:
            return "\n".join(extracted).strip()

    # Strip leading prose lines that aren't Python
    python_starters = ("df", "result", "import", "pd.", "#", "for ", "if ",
                       "try", "with ", "def ", "class ", "import ", "from ")
    lines = code.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and any(stripped.startswith(kw) for kw in python_starters):
            return "\n".join(lines[i:]).strip()

    return code


def _format_result(result: Any) -> Tuple[str, Dict]:
    """
    Convert result to readable string + metadata for LLM Call 2.
    Metadata provides accurate counts/info so LLM doesn't need to calculate.
    """
    if isinstance(result, pd.DataFrame):
        if result.empty:
            return "No records found.", {"type": "dataframe", "rows": 0, "columns": 0}

        result_str = result.to_string(index=False)
        metadata = {
            "type": "dataframe",
            "rows": len(result),
            "columns": len(result.columns),
            "column_names": list(result.columns)
        }
        return result_str, metadata

    elif isinstance(result, pd.Series):
        if result.empty:
            return "No records found.", {"type": "series", "length": 0}

        result_str = result.to_string()
        metadata = {
            "type": "series",
            "length": len(result),
            "name": result.name
        }
        return result_str, metadata

    elif isinstance(result, (list, tuple)):
        result_str = "\n".join(str(item) for item in result)
        metadata = {
            "type": "list",
            "length": len(result)
        }
        return result_str, metadata

    elif isinstance(result, dict):
        # Special handling for dict results containing DataFrames
        # Set pandas display options to show ALL columns (no truncation)
        with pd.option_context('display.max_columns', None,
                               'display.max_rows', None,
                               'display.width', None,
                               'display.max_colwidth', 50):
            result_str_parts = []
            for key, value in result.items():
                if isinstance(value, pd.DataFrame):
                    # Format DataFrame with all columns visible
                    result_str_parts.append(f"{key}:\n{value.to_string(index=False)}")
                elif isinstance(value, pd.Series):
                    result_str_parts.append(f"{key}:\n{value.to_string()}")
                else:
                    result_str_parts.append(f"{key}: {value}")

            result_str = "\n\n".join(result_str_parts)

        metadata = {
            "type": "dict",
            "keys": list(result.keys()),
            "length": len(result)
        }
        return result_str, metadata

    else:
        # Scalar value (number, string, etc.)
        return str(result), {"type": "scalar", "value": str(result)}
