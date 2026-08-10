"""
Per-request context for per-user API keys, model selection, and usage capture.

This is the mechanism that lets the UNCHANGED golden file services/llm.py use a
different API key and model per request: llm.py reads Config.LITELLM_API_KEY and
Config.LLM_MODEL fresh on every call, and config.py resolves those two values
from the context variables below (falling back to the .env values when no
context is set — e.g. tests, scripts, or key-validation calls).

contextvars are thread-safe: with waitress/gunicorn threads, each request sets
its own context and resets it in a finally block, so concurrent users can never
see each other's keys.

Deliberately imports nothing from the app (config.py imports THIS module).
"""

import contextvars

# Set by app.py at the start of /ask and /ask/refine; read by config.py.
user_api_key = contextvars.ContextVar("user_api_key", default=None)
active_model = contextvars.ContextVar("active_model", default=None)

# List that services/usage_capture.py appends one record per gateway call to.
usage_records = contextvars.ContextVar("usage_records", default=None)


def begin(api_key: str, model: str):
    """Enter a user-scoped request. Returns tokens for end()."""
    return (
        user_api_key.set(api_key),
        active_model.set(model),
        usage_records.set([]),
    )


def end(tokens) -> None:
    """Leave the user-scoped request (ALWAYS call in a finally block)."""
    user_api_key.reset(tokens[0])
    active_model.reset(tokens[1])
    usage_records.reset(tokens[2])


def record(rec: dict) -> None:
    """Append one captured gateway-call record (no-op outside a request)."""
    lst = usage_records.get()
    if lst is not None:
        lst.append(rec)


def get_records() -> list:
    return usage_records.get() or []
