import io
import re
import pandas as pd
from typing import Tuple, List, Optional
from config import Config
from logger import logger


class FileHandlerError(Exception):
    pass


def read_file(file_bytes: bytes, filename: str, sheet_name: Optional[str] = None) -> Tuple[pd.DataFrame, str, List[str]]:
    """
    Read CSV or Excel file into a DataFrame.
    Returns (df, encoding_used, warnings)
    """
    ext = _get_extension(filename)

    if ext not in Config.ALLOWED_EXTENSIONS:
        raise FileHandlerError(f"Unsupported file type: {ext}. Allowed: {', '.join(Config.ALLOWED_EXTENSIONS)}")

    if len(file_bytes) > Config.MAX_FILE_BYTES:
        raise FileHandlerError(f"File exceeds maximum size of {Config.MAX_FILE_SIZE_MB}MB")

    if len(file_bytes) == 0:
        raise FileHandlerError("File is empty")

    if ext == ".csv":
        return _read_csv(file_bytes)
    else:
        return _read_excel(file_bytes, filename, sheet_name)


def _read_csv(file_bytes: bytes) -> Tuple[pd.DataFrame, str, List[str]]:
    """Try encodings in order until one works."""
    last_error = None
    for encoding in Config.CSV_ENCODINGS:
        try:
            text = file_bytes.decode(encoding)
            df   = pd.read_csv(io.StringIO(text), encoding_errors="replace")
            logger.info(f"CSV decoded successfully with encoding: {encoding}")
            df, warnings = _sanitize(df)
            return df, encoding, warnings
        except (UnicodeDecodeError, ValueError) as e:
            last_error = e
            continue

    raise FileHandlerError(f"Could not decode CSV with any supported encoding. Last error: {last_error}")


def _read_excel(file_bytes: bytes, filename: str, sheet_name: Optional[str] = None) -> Tuple[pd.DataFrame, str, List[str]]:
    """Read Excel file, optionally selecting a sheet."""
    try:
        xf = pd.ExcelFile(io.BytesIO(file_bytes))
        sheets = xf.sheet_names

        selected = sheet_name if sheet_name and sheet_name in sheets else sheets[0]
        df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=selected)
        logger.info(f"Excel file read: sheet='{selected}', shape={df.shape}")

        df, warnings = _sanitize(df)

        # Convert date columns from strings to datetime (for MS Project files)
        # TEMPORARILY DISABLED - causing JSON serialization issues with NaT values
        # df, date_warnings = _convert_date_columns(df)
        # warnings.extend(date_warnings)

        if len(sheets) > 1 and not sheet_name:
            warnings.append(f"File has {len(sheets)} sheets. Loaded '{selected}'. Other sheets: {', '.join(sheets[1:])}")

        return df, f"excel:{selected}", warnings
    except Exception as e:
        raise FileHandlerError(f"Could not read Excel file: {str(e)}")

def _convert_date_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """
    Detect and convert likely date columns from strings to datetime.
    Uses column name heuristics to identify date columns.
    """
    warnings = []

    # Common date column name patterns
    date_keywords = ['date', 'start', 'finish', 'baseline', 'actual', 'forecast', 'due']

    for col in df.columns:
        # Skip if already datetime
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue

        # Check if column name suggests it's a date
        col_lower = col.lower()
        if any(keyword in col_lower for keyword in date_keywords):
            # Try to convert to datetime
            try:
                original_type = df[col].dtype
                converted = pd.to_datetime(df[col], errors='coerce')

                # Only apply conversion if at least 50% of non-null values converted successfully
                non_null_count = df[col].notna().sum()
                converted_count = converted.notna().sum()

                if non_null_count > 0 and (converted_count / non_null_count) >= 0.5:
                    df[col] = converted
                    logger.info(f"Converted '{col}' from {original_type} to datetime64[ns]")
                    warnings.append(f"Auto-converted date column: '{col}' ({original_type} → datetime)")
            except Exception as e:
                logger.debug(f"Could not convert '{col}' to datetime: {e}")
                pass

    return df, warnings


def _clean_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detect and clean columns that contain accounting-format numbers.
    Handles: (76985) → -76985, $1,234.56 → 1234.56
    """
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna().head(20).astype(str)
            # Detect accounting format: parentheses, dollar signs, commas
            has_parens   = sample.str.contains(r'\([\d,\.]+\)', regex=True).any()
            has_currency = sample.str.contains(r'^\$|^\(?\$', regex=True).any()
            has_commas   = sample.str.contains(r'\d,\d', regex=True).any()

            if has_parens or has_currency or has_commas:
                try:
                    cleaned = (df[col].astype(str)
                        .str.replace(r'[\$,]', '', regex=True)
                        .str.strip()
                    )
                    # Convert (76985) → -76985
                    def parse_accounting(val):
                        val = val.strip()
                        if val.startswith('(') and val.endswith(')'):
                            return -float(val[1:-1])
                        try:
                            return float(val)
                        except:
                            return val
                    converted = cleaned.apply(parse_accounting)
                    # Only replace if majority converted successfully
                    if converted.apply(lambda x: isinstance(x, float)).mean() > 0.7:
                        df[col] = pd.to_numeric(converted, errors='coerce')
                except Exception:
                    pass
    return df


def _sanitize(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """Clean up the DataFrame and return warnings."""
    warnings = []

    # Drop fully empty rows and columns
    before_rows = len(df)
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.loc[:, df.notna().any()]
    if len(df) < before_rows:
        warnings.append(f"Removed {before_rows - len(df)} fully empty rows")

    # Sanitize column names
    original_cols = list(df.columns)
    df.columns = [_sanitize_col_name(str(c)) for c in df.columns]

    # Handle duplicate column names
    seen = {}
    new_cols = []
    for col in df.columns:
        if col in seen:
            seen[col] += 1
            new_cols.append(f"{col}_{seen[col]}")
            warnings.append(f"Duplicate column renamed: '{col}' → '{col}_{seen[col]}'")
        else:
            seen[col] = 0
            new_cols.append(col)
    df.columns = new_cols

    # Warn on fully null columns
    null_cols = [c for c in df.columns if df[c].isna().all()]
    if null_cols:
        warnings.append(f"Columns with no data: {', '.join(null_cols)}")

    if len(df) == 0:
        raise FileHandlerError("File contains no data rows after cleaning")
    
    df = _clean_numeric_columns(df)
    return df, warnings


def _sanitize_col_name(name: str) -> str:
    """Remove characters that break pandas attribute access."""
    name = name.strip()
    name = re.sub(r"[^\w\s]", "_", name)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_") or "column"


def _get_extension(filename: str) -> str:
    parts = filename.rsplit(".", 1)
    return f".{parts[-1].lower()}" if len(parts) > 1 else ""


def get_excel_sheets(file_bytes: bytes) -> List[str]:
    """Return sheet names from an Excel file without loading data."""
    try:
        xf = pd.ExcelFile(io.BytesIO(file_bytes))
        return xf.sheet_names
    except Exception:
        return []
