import os
from dotenv import load_dotenv

from services import request_context

load_dotenv()


class _ConfigMeta(type):
    """
    Makes Config.LITELLM_API_KEY and Config.LLM_MODEL resolve dynamically.

    services/llm.py (golden file, unchanged) reads these two attributes on every
    call. When a request has set a per-user key / admin-selected model in
    services/request_context.py, that value is returned; otherwise the .env
    value is returned exactly as before. Thread-safe via contextvars.
    """

    @property
    def LITELLM_API_KEY(cls):
        return request_context.user_api_key.get() or cls._ENV_LITELLM_API_KEY

    @property
    def LLM_MODEL(cls):
        return request_context.active_model.get() or cls._ENV_LLM_MODEL


class Config(metaclass=_ConfigMeta):
    # ── LLM Gateway ───────────────────────────────────────────────────────────
    # NOTE: LITELLM_API_KEY and LLM_MODEL are dynamic properties (see
    # _ConfigMeta above). The raw .env fallbacks live in the _ENV_* names.
    _ENV_LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
    _ENV_LLM_MODEL       = os.environ.get("LLM_MODEL", "claude-sonnet-4-5")
    LITELLM_API_BASE = os.environ.get("LITELLM_API_BASE", "").rstrip("/")
    LLM_TIMEOUT      = int(os.environ.get("LLM_TIMEOUT", "60"))
    LLM_MAX_TOKENS   = int(os.environ.get("LLM_MAX_TOKENS", "4096"))

    # ── Per-user keys / usage tracking / admin (rollout batch) ───────────────
    ENCRYPTION_KEY      = os.environ.get("ENCRYPTION_KEY", "").strip()
    ADMIN_USERS         = [u.strip() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()]
    INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET", "").strip()
    DB_PATH             = os.environ.get("DB_PATH", "").strip()

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
