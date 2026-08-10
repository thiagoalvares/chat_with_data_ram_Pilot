"""
Usage capture WITHOUT modifying the golden file services/llm.py.

How it works: llm.py calls `requests.post(...)` through its module-level
`requests` import. install() swaps that reference for a thin proxy that
delegates to the real requests library, and — as each gateway response passes
through — copies the token `usage` block, model, and temperature into the
current request context (services/request_context.py). The response object is
returned untouched, so llm.py behaves EXACTLY as before; llm.py the file is
byte-for-byte unchanged.

The capture is wrapped in try/except and can never fail a gateway call. If
install() is never called, nothing changes at all.

After the pipeline finishes, app.py calls flush() to write the captured
records to SQLite with the user/session/question context. Call type is derived
from the request temperature: 0 => Call 1 (code generation), otherwise Call 2
(answer). Refine requests are labeled 'refine_answer'.
"""

import requests as _real_requests

from logger import logger
from services import request_context
from services.database import log_usage
from services.pricing import get_model_pricing, calculate_cost

_installed = False


def _capture(kwargs, resp):
    body = kwargs.get("json") or {}
    rec = {
        "model": body.get("model", "unknown"),
        "temperature": body.get("temperature"),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "success": bool(resp is not None and resp.ok),
        "error": None if (resp is not None and resp.ok) else (f"HTTP {resp.status_code}" if resp is not None else "no response"),
    }
    try:
        if resp is not None and resp.ok:
            usage = resp.json().get("usage") or {}
            rec["prompt_tokens"] = int(usage.get("prompt_tokens", 0) or 0)
            rec["completion_tokens"] = int(usage.get("completion_tokens", 0) or 0)
            rec["total_tokens"] = int(usage.get("total_tokens", 0) or 0)
    except Exception:
        pass
    request_context.record(rec)


class _RequestsProxy:
    """Delegates to the real requests library; wraps .post to capture usage."""

    exceptions = _real_requests.exceptions

    @staticmethod
    def post(url, **kwargs):
        resp = None
        try:
            resp = _real_requests.post(url, **kwargs)
            return resp
        finally:
            try:
                _capture(kwargs, resp)
            except Exception as e:
                logger.warning(f"usage capture skipped: {e}")

    # Anything else llm.py might ever use falls through to the real library.
    def __getattr__(self, name):
        return getattr(_real_requests, name)


def install():
    """Point services.llm at the capturing proxy. Idempotent."""
    global _installed
    if _installed:
        return
    import services.llm as llm_module
    llm_module.requests = _RequestsProxy()
    _installed = True
    logger.info("Usage capture installed (llm.py unchanged)")


def flush(user_id: int, username: str, session_id: str, request_id: str,
          mode: str, question_text: str, refine: bool = False):
    """Write captured records for the current request to the database."""
    for rec in request_context.get_records():
        try:
            pricing = get_model_pricing(rec["model"])
            p_cost, c_cost, t_cost = calculate_cost(rec["prompt_tokens"], rec["completion_tokens"], pricing)
            if refine:
                call_type = "refine_answer"
            else:
                call_type = "call1_codegen" if rec.get("temperature") == 0 else "call2_answer"
            log_usage(
                user_id=user_id, username=username, session_id=session_id,
                request_id=request_id, mode=mode, question_text=question_text,
                call_type=call_type, model=rec["model"],
                prompt_tokens=rec["prompt_tokens"], completion_tokens=rec["completion_tokens"],
                total_tokens=rec["total_tokens"],
                prompt_cost=p_cost, completion_cost=c_cost, total_cost=t_cost,
                success=rec["success"], error_message=rec.get("error"),
            )
        except Exception as e:
            logger.error(f"Failed to log usage record: {e}")  # never fail the request
