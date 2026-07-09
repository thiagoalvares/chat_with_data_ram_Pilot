import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── LLM Gateway ───────────────────────────────────────────────────────────
    LITELLM_API_KEY  = os.environ.get("LITELLM_API_KEY", "")
    LITELLM_API_BASE = os.environ.get("LITELLM_API_BASE", "").rstrip("/")
    LLM_MODEL        = os.environ.get("LLM_MODEL", "claude-sonnet-4-5")
    LLM_TIMEOUT      = int(os.environ.get("LLM_TIMEOUT", "60"))
    LLM_MAX_TOKENS   = int(os.environ.get("LLM_MAX_TOKENS", "4096"))

    # ── Flask ─────────────────────────────────────────────────────────────────
    SECRET_KEY       = os.environ.get("SECRET_KEY", os.urandom(24).hex())
    PORT             = int(os.environ.get("PORT", "5000"))
    DEBUG            = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    # ── File Upload ───────────────────────────────────────────────────────────
    MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", "50"))
    MAX_FILE_BYTES   = MAX_FILE_SIZE_MB * 1024 * 1024
    ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
    CSV_ENCODINGS    = ["utf-8-sig", "utf-8", "latin-1", "cp1252", "ascii"]

    # ── Session ───────────────────────────────────────────────────────────────
    SESSION_TIMEOUT_MINUTES = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "120"))

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_DIR          = os.environ.get("LOG_DIR", "logs")
    LOG_LEVEL        = os.environ.get("LOG_LEVEL", "INFO")

    @classmethod
    def validate(cls):
        """Refuse to start if critical config is missing."""
        errors = []
        if not cls.LITELLM_API_KEY:
            errors.append("LITELLM_API_KEY is not set")
        if not cls.LITELLM_API_BASE:
            errors.append("LITELLM_API_BASE is not set")
        if errors:
            raise EnvironmentError(
                "\n  Missing required configuration:\n  - " + "\n  - ".join(errors)
            )
