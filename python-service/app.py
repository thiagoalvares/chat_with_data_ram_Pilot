"""
Chat with Data — Python micro-service (Standard + Variance)
-----------------------------------------------------------
Repackages the prototype's proven analytical core as a stateless-style REST API
that the .NET front end calls over the internal network.

The analytical core is UNCHANGED from the prototype:
  - services/llm.py        two-call LLM pipeline (Call 1 code, Call 2 answer)
  - services/executor.py   executes generated pandas code in one namespace
  - services/file_handler.py CSV/Excel reading, schema extraction
  - prompts/standard.py, prompts/variance.py   the exact mode prompts

The only architectural change vs. the prototype's routes/chat.py is that the
session id arrives in the `X-Session-Id` header (managed by the .NET layer)
instead of a Flask cookie. The pipeline itself is reproduced verbatim.

For local validation, session state is held in memory (services/data_manager.py).
In production this service runs under gunicorn with SQL Server + a file share
providing durable, stateless session/data storage (see README / architecture doc).
"""

import io
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests as _requests
from flask import Blueprint, Flask, jsonify, request, send_file

from config import Config
from logger import logger
from models.schemas import DebugEntry
from prompts import standard, variance
from services import llm as llm_service
from services import insights
from services import request_context, usage_capture
from services import database as db
from services.admin_auth import is_admin, require_admin
from services.crypto import CryptoNotConfigured, decrypt_api_key, encrypt_api_key, mask_api_key
from services.data_manager import get_session, save_session, get_schema_info
from services.executor import execute_generated_code
from services.file_handler import FileHandlerError, get_excel_sheets, read_file
from services.model_manager import (
    change_model, get_available_models, get_current_model_config, get_model_history,
)
from services import experts as experts_svc
from services.report_export import build_conversation_docx

api = Blueprint("api", __name__, url_prefix="/api")


# ── Session id (provided by the .NET front end) ───────────────────────────────

class MissingSessionId(Exception):
    pass


def _sid() -> str:
    """
    The .NET presentation layer owns the user session and passes its id in the
    X-Session-Id header. Fall back to a query/form value for direct testing.
    """
    sid = (
        request.headers.get("X-Session-Id")
        or request.values.get("session_id")
    )
    if not sid:
        raise MissingSessionId()
    return sid


def _username() -> str:
    """
    Identity supplied by the .NET layer in X-User: the Windows username when
    Windows Authentication is on, otherwise the .NET host's dev fallback.
    """
    return (request.headers.get("X-User") or "unknown").strip()


def _groups() -> list:
    """
    The caller's AD groups, resolved by .NET from the Windows sign-in badge
    (only groups that Data Experts actually reference are sent). Empty when
    auth is off, unless simulated via the .NET Auth:DevGroups setting.
    """
    raw = request.headers.get("X-User-Groups") or ""
    return [g.strip() for g in raw.replace(";", ",").split(",") if g.strip()]


@api.before_request
def _check_internal_secret():
    """
    Optional shared secret between .NET and Python (INTERNAL_API_SECRET in
    .env). Off by default. When set, every /api request must carry the matching
    X-Internal-Auth header — closes the 'anyone on the network can hit port
    8000 and claim any X-User' gap while Python runs on a separate machine.
    """
    secret = Config.INTERNAL_API_SECRET
    if secret and request.headers.get("X-Internal-Auth") != secret:
        return jsonify({"error": "Unauthorized"}), 401


def _resolve_user_context(username: str):
    """
    Look up the user, decrypt their API key, and read the active model.
    Returns ((user, api_key, model_name), None) on success or (None, flask_response).
    """
    user = db.get_user_by_username(username)
    if not user or not user.get("IsActive", 1):
        return None, (jsonify({
            "error": "No API key on file. Please set up your LiteLLM API key to use the app.",
            "needs_key": True,
        }), 401)
    try:
        api_key = decrypt_api_key(user["APIKey"])
    except CryptoNotConfigured as e:
        return None, (jsonify({"error": str(e)}), 503)
    except Exception:
        logger.error(f"Could not decrypt stored key for {username}")
        return None, (jsonify({
            "error": "Your stored API key could not be read. Please re-enter it.",
            "needs_key": True,
        }), 401)
    model_name = get_current_model_config()["model_name"]
    return (user, api_key, model_name), None


# ── Shared pipeline helpers (reproduced verbatim from prototype chat.py) ───────

def _get_prev_result_meta(raw_result: Any, max_chars: int = 1000) -> str:
    """Truncated string representation of previous result for LLM context."""
    if raw_result is None:
        return None
    try:
        if isinstance(raw_result, pd.DataFrame):
            if raw_result.empty:
                return "Empty DataFrame (0 rows)"
            preview = raw_result.head(5).to_string(index=False)
            result_str = f"DataFrame with {len(raw_result)} rows, {len(raw_result.columns)} columns:\n{preview}"
            if len(raw_result) > 5:
                result_str += f"\n... ({len(raw_result) - 5} more rows)"
        elif isinstance(raw_result, pd.Series):
            preview = raw_result.head(5).to_string()
            result_str = f"Series with {len(raw_result)} values:\n{preview}"
            if len(raw_result) > 5:
                result_str += f"\n... ({len(raw_result) - 5} more values)"
        elif isinstance(raw_result, (list, tuple)):
            preview = "\n".join(str(item) for item in raw_result[:5])
            result_str = f"List with {len(raw_result)} items:\n{preview}"
            if len(raw_result) > 5:
                result_str += f"\n... ({len(raw_result) - 5} more items)"
        elif isinstance(raw_result, dict):
            keys_info = ", ".join(f"{k}: {type(v).__name__}" for k, v in list(raw_result.items())[:5])
            result_str = f"Dict with {len(raw_result)} keys: {keys_info}"
        else:
            result_str = str(raw_result)

        if len(result_str) > max_chars:
            result_str = result_str[:max_chars] + "... [truncated]"
        return result_str
    except Exception as e:
        logger.warning(f"Error building prev_result_meta: {e}")
        return None


def _inject_error(messages: list, error: str) -> list:
    updated  = messages.copy()
    last_msg = updated[-1].copy()
    last_msg["content"] += f"\n\nYour previous code failed:\n{error}\n\nReturn ONLY fixed Python code. No prose."
    updated[-1] = last_msg
    return updated


def _run_pipeline(code_messages: list, answer_messages_fn, debug: list,
                  prev_result: Any = None, **dataframes) -> tuple:
    """
    Shared two-call pipeline with one auto-retry (logic verbatim from prototype).
    Also returns the computed result_str/metadata so the "one sentence / more
    detail" refine feature can re-run Call 2 against the SAME computed result.
    Returns (success, answer, chart, debug, raw_result, result_str, metadata).
    """
    # ── Call 1: generate code ──────────────────────────────────────────────────
    ok, code = llm_service.generate_query_code(code_messages)
    if not ok:
        return False, code, None, debug, None, None, None
    debug.append(DebugEntry("Generated code", code))

    # ── Execute ────────────────────────────────────────────────────────────────
    success, result_str, raw_result, metadata = execute_generated_code(code, prev_result=prev_result, **dataframes)

    if not success:
        debug.append(DebugEntry("Execution error — retrying", result_str))
        retry_messages = _inject_error(code_messages, result_str)
        ok2, code2 = llm_service.generate_query_code(retry_messages)
        if ok2:
            debug.append(DebugEntry("Retry code", code2))
            success, result_str, raw_result, metadata = execute_generated_code(code2, prev_result=prev_result, **dataframes)
        if not success:
            debug.append(DebugEntry("Retry also failed", result_str))
            return False, "I wasn't able to compute an answer for that question. Try rephrasing it.", None, debug, None, None, None

    debug.append(DebugEntry("Query result", result_str))

    # ── Call 2: generate answer ────────────────────────────────────────────────
    answer_messages = answer_messages_fn(result_str, metadata)
    ok, raw_answer  = llm_service.generate_human_answer(answer_messages)
    if not ok:
        return False, raw_answer, None, debug, None, None, None

    answer, chart = llm_service.parse_answer_response(raw_answer)
    debug.append(DebugEntry("Final answer", answer))
    return True, answer, chart, debug, raw_result, result_str, metadata


# ── Upload (Standard + Variance) ──────────────────────────────────────────────

def _handle_upload(file, slot: str, sess, sheet_name: str = None) -> dict:
    """Shared upload logic (verbatim from prototype upload.py)."""
    if not file or not file.filename:
        return {"error": "No file provided"}

    file_bytes = file.read()

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext in ("xlsx", "xls") and not sheet_name:
        sheets = get_excel_sheets(file_bytes)
        if len(sheets) > 1:
            return {"needs_sheet_selection": True, "sheets": sheets,
                    "filename": file.filename, "slot": slot}

    try:
        df, encoding, warnings = read_file(file_bytes, file.filename, sheet_name)
    except FileHandlerError as e:
        return {"error": str(e)}

    schema = get_schema_info(df)

    setattr(sess, slot if slot == "df" else f"df_{slot}", df)
    setattr(sess, "schema" if slot == "df" else f"schema_{slot}", schema)
    if slot == "df":
        sess.filename = file.filename

    columns = [{"name": str(col), "type": str(df[col].dtype)} for col in df.columns]
    preview = df.head(5).fillna("").astype(str).replace("nan", "").to_dict(orient="records")

    logger.info(f"File uploaded | slot={slot} | file={file.filename} | shape={df.shape} | encoding={encoding}")

    # Upload-time insights — pure pandas, no LLM calls (services/insights.py).
    mode = "standard" if slot == "df" else "variance"
    try:
        profile     = insights.build_profile(df)
        anomalies   = insights.find_anomalies(df)
        suggestions = insights.suggest_questions(
            df, mode=mode,
            label_a=getattr(sess, "label_a", None),
            label_b=getattr(sess, "label_b", None),
        )
    except Exception as e:
        logger.warning(f"Insights failed (non-fatal): {e}")
        profile, anomalies, suggestions = None, [], []

    return {
        "ok": True, "rows": len(df), "cols": len(df.columns),
        "columns": columns, "preview": preview, "filename": file.filename,
        "encoding": encoding, "warnings": warnings,
        "profile": profile, "anomalies": anomalies, "suggestions": suggestions,
    }


@api.route("/upload/standard", methods=["POST"])
def upload_standard():
    sid  = _sid()
    sess = get_session(sid)
    # Tab-scoped clear: a new Standard file only resets the Standard conversation.
    sess.clear_history("standard")
    result = _handle_upload(request.files.get("file"), "df", sess, request.form.get("sheet_name"))
    if "ok" in result:
        save_session(sid, sess)
    return jsonify(result), (400 if "error" in result else 200)


@api.route("/upload/variance/<slot>", methods=["POST"])
def upload_variance(slot: str):
    if slot not in ("a", "b"):
        return jsonify({"error": "Invalid slot. Use 'a' or 'b'"}), 400
    sid  = _sid()
    sess = get_session(sid)
    # Tab-scoped clear: a new Variance file only resets the Variance conversation.
    sess.clear_history("variance")
    label = request.form.get("label", f"File {slot.upper()}")
    setattr(sess, f"label_{slot}", label)
    result = _handle_upload(request.files.get("file"), slot, sess, request.form.get("sheet_name"))
    if "ok" in result:
        save_session(sid, sess)
    return jsonify(result), (400 if "error" in result else 200)


# ── Ask (the two-call pipeline) ───────────────────────────────────────────────

@api.route("/ask", methods=["POST"])
def ask():
    sid  = _sid()
    sess = get_session(sid)

    body     = request.get_json() or {}
    question = body.get("question", "").strip()
    mode     = body.get("mode", "standard")
    if not question:
        return jsonify({"error": "Empty question"}), 400

    # ── Per-user key + model + usage tracking (additive envelope) ─────────────
    # The pipeline below is UNCHANGED; this wrapper only sets the per-request
    # context that config.py serves to the untouched llm.py, and flushes the
    # captured token usage to the database afterwards.
    username = _username()
    ctx, err = _resolve_user_context(username)
    if err:
        return err
    user, user_key, model_name = ctx
    request_id = uuid.uuid4().hex
    ctx_tokens = request_context.begin(user_key, model_name)
    try:
        return _ask_pipeline(sid, sess, question, mode)
    finally:
        usage_capture.flush(user["UserID"], username, sid, request_id, mode, question)
        request_context.end(ctx_tokens)


def _ask_pipeline(sid, sess, question, mode):
    """The original /ask body, moved verbatim (logic identical to the prototype flow)."""
    debug = []
    logger.info(f"Question received | mode={mode} | sid={sid} | q={question[:80]}")
    history = sess.get_history(mode)

    if mode == "standard":
        if sess.df is None:
            return jsonify({"error": "No data loaded. Please upload a file first."}), 400
        prev_result = getattr(sess, "last_result", None)
        prev_result_meta = _get_prev_result_meta(prev_result, max_chars=1000)
        code_msgs = standard.build_code_gen_prompt(
            sess.schema, question, error_feedback=None,
            history=history, prev_result_meta=prev_result_meta,
        )
        success, answer, chart, debug, raw_result, result_str, metadata = _run_pipeline(
            code_msgs,
            lambda r, m: standard.build_answer_gen_prompt(question, r, history, m),
            debug, prev_result=prev_result, df=sess.df,
        )

    elif mode == "variance":
        if sess.df_a is None or sess.df_b is None:
            return jsonify({"error": "Please upload both files before asking questions."}), 400
        prev_result = getattr(sess, "last_result", None)
        prev_result_meta = _get_prev_result_meta(prev_result, max_chars=1000)
        code_msgs = variance.build_code_gen_prompt(
            sess.schema_a, sess.schema_b, sess.label_a, sess.label_b, question,
            error_feedback=None, history=history, prev_result_meta=prev_result_meta,
        )
        success, answer, chart, debug, raw_result, result_str, metadata = _run_pipeline(
            code_msgs,
            lambda r, m: variance.build_answer_gen_prompt(question, r, sess.label_a, sess.label_b, history, m),
            debug, prev_result=prev_result, df_a=sess.df_a, df_b=sess.df_b,
        )

    else:
        return jsonify({"error": f"Unknown mode: {mode}. This service supports 'standard' and 'variance'."}), 400

    if success:
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        sess.last_result = raw_result
        # Stored so /ask/refine can re-run Call 2 on the SAME computed result.
        sess.last_question   = question
        sess.last_result_str = result_str
        sess.last_metadata   = metadata
        sess.last_mode       = mode
        save_session(sid, sess)

    return jsonify({
        "answer": answer,
        "chart":  chart,
        "debug":  [{"label": d.label, "content": d.content} for d in debug],
    })


@api.route("/ask/refine", methods=["POST"])
def ask_refine():
    """
    Re-explain the LAST computed result in a different style ("brief" or
    "detail"). Only Call 2 runs — the pandas result is reused unchanged, so the
    numbers cannot change. Uses the existing prompt builders untouched.
    """
    sid  = _sid()
    sess = get_session(sid)

    body  = request.get_json() or {}
    style = body.get("style", "brief")

    question   = getattr(sess, "last_question", None)
    result_str = getattr(sess, "last_result_str", None)
    metadata   = getattr(sess, "last_metadata", None)
    mode       = getattr(sess, "last_mode", "standard")

    if not question or result_str is None:
        return jsonify({"error": "Nothing to refine yet. Ask a question first."}), 400

    # Per-user key + model + usage tracking (same additive envelope as /ask)
    username = _username()
    ctx, err = _resolve_user_context(username)
    if err:
        return err
    user, user_key, model_name = ctx
    request_id = uuid.uuid4().hex
    ctx_tokens = request_context.begin(user_key, model_name)
    try:
        if style == "detail":
            styled_q = (f"{question}\n\n(Re-explain the result above in MORE detail: "
                        f"cover the notable values, comparisons, and any caveats.)")
        else:
            styled_q = (f"{question}\n\n(Re-explain the result above in ONE short "
                        f"sentence — the single most important takeaway.)")

        history = sess.get_history(mode)
        if mode == "variance":
            messages = variance.build_answer_gen_prompt(styled_q, result_str, sess.label_a, sess.label_b, history, metadata)
        else:
            messages = standard.build_answer_gen_prompt(styled_q, result_str, history, metadata)

        ok, raw_answer = llm_service.generate_human_answer(messages)
        if not ok:
            return jsonify({"error": raw_answer}), 502

        answer, chart = llm_service.parse_answer_response(raw_answer)
        logger.info(f"Refine ({style}) | sid={sid} | mode={mode}")
        return jsonify({"answer": answer, "chart": chart})
    finally:
        usage_capture.flush(user["UserID"], username, sid, request_id, mode, question, refine=True)
        request_context.end(ctx_tokens)


@api.route("/clear", methods=["POST"])
def clear():
    sid  = _sid()
    sess = get_session(sid)
    mode = (request.get_json() or {}).get("mode")
    sess.clear_history(mode)
    save_session(sid, sess)
    logger.info(f"Chat history cleared | sid={sid} | mode={mode or 'all'}")
    return jsonify({"ok": True})


# ── Export (verbatim from prototype export.py) ────────────────────────────────

def _convert_to_dataframe(raw_result) -> pd.DataFrame:
    if isinstance(raw_result, pd.DataFrame):
        return raw_result
    if isinstance(raw_result, pd.Series):
        return raw_result.to_frame(name=raw_result.name or "Value")
    if isinstance(raw_result, dict):
        if raw_result and all(isinstance(v, (list, tuple)) for v in raw_result.values()):
            return pd.DataFrame(raw_result)
        return pd.DataFrame(list(raw_result.items()), columns=["Key", "Value"])
    if isinstance(raw_result, list) and raw_result and isinstance(raw_result[0], dict):
        return pd.DataFrame(raw_result)
    if isinstance(raw_result, list):
        return pd.DataFrame(raw_result, columns=["Value"])
    return pd.DataFrame({"Result": [raw_result]})


@api.route("/export/last_result", methods=["GET"])
def export_last_result():
    sid  = _sid()
    sess = get_session(sid)
    if sess.last_result is None:
        return jsonify({"error": "No data available to export. Run a query first."}), 400
    try:
        df = _convert_to_dataframe(sess.last_result)
        if len(df) > 500000:
            return jsonify({"error": f"Result too large to export ({len(df):,} rows). Maximum is 500,000 rows."}), 400
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Query Result", index=False)
        output.seek(0)
        logger.info(f"Excel export | sid={sid} | rows={len(df)} | cols={len(df.columns)}")
        return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name=f"query_result_{sid[:8]}.xlsx")
    except Exception as e:
        logger.error(f"Export failed: {str(e)}")
        return jsonify({"error": f"Export failed: {str(e)}"}), 500


@api.route("/export/debug_result", methods=["GET"])
def export_debug_result():
    sid  = _sid()
    sess = get_session(sid)
    if sess.last_result is None:
        return jsonify({"error": "No data available to export. Run a query first."}), 400
    try:
        raw_result = sess.last_result
        output = io.BytesIO()
        if isinstance(raw_result, dict) and raw_result and all(isinstance(v, pd.DataFrame) for v in raw_result.values()):
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                total_rows = 0
                for sheet_name, df in raw_result.items():
                    df.to_excel(writer, sheet_name=str(sheet_name)[:31], index=False)
                    total_rows += len(df)
                    if total_rows > 500000:
                        return jsonify({"error": f"Result too large to export ({total_rows:,} total rows). Maximum is 500,000 rows."}), 400
            output.seek(0)
            logger.info(f"Debug Excel export (multi-sheet) | sid={sid} | sheets={len(raw_result)} | total_rows={total_rows}")
            return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             as_attachment=True, download_name=f"debug_result_{sid[:8]}.xlsx")
        df = _convert_to_dataframe(raw_result)
        if len(df) > 500000:
            return jsonify({"error": f"Result too large to export ({len(df):,} rows). Maximum is 500,000 rows."}), 400
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Debug Result", index=False)
        output.seek(0)
        logger.info(f"Debug Excel export (single-sheet) | sid={sid} | rows={len(df)} | cols={len(df.columns)}")
        return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name=f"debug_result_{sid[:8]}.xlsx")
    except Exception as e:
        logger.error(f"Debug export failed: {str(e)}")
        return jsonify({"error": f"Export failed: {str(e)}"}), 500


@api.route("/export/conversation", methods=["POST"])
def export_conversation():
    """
    Export the on-screen conversation to a Word document (services/report_export.py).
    The browser sends exactly what it displayed — questions, answers, chart PNGs
    captured from the canvases, and optional debug traces — so the document is
    faithful to the chat. No LLM calls.
    """
    sid = _sid()
    payload = request.get_json() or {}
    messages = payload.get("messages") or []
    if not messages:
        return jsonify({"error": "Nothing to export yet. Ask a question first."}), 400
    try:
        docx_bytes = build_conversation_docx(payload)
        from datetime import datetime as _dt
        name = f"ChatWithData_{payload.get('mode', 'standard')}_{_dt.now().strftime('%Y%m%d_%H%M')}.docx"
        logger.info(f"Conversation export | sid={sid} | messages={len(messages)} | debug={bool(payload.get('include_debug'))}")
        return send_file(
            io.BytesIO(docx_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True, download_name=name,
        )
    except Exception as e:
        logger.error(f"Conversation export failed: {str(e)}")
        return jsonify({"error": f"Export failed: {str(e)}"}), 500


# ── Per-user API keys (rollout batch) ─────────────────────────────────────────

def _validate_key_against_gateway(api_key: str):
    """Test a key with a cheap authenticated call to the gateway."""
    url = f"{Config.LITELLM_API_BASE}/v1/models"
    r = _requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=8, verify=False)
    r.raise_for_status()


@api.route("/user/check_key", methods=["GET"])
def user_check_key():
    username = _username()
    exists = db.user_exists(username)
    if exists:
        db.update_user_last_login(username)
    return jsonify({"has_key": exists, "username": username})


@api.route("/user/save_key", methods=["POST"])
def user_save_key():
    username = _username()
    data = request.get_json() or {}
    api_key = (data.get("api_key") or "").strip()

    if not api_key:
        return jsonify({"error": "API key is required"}), 400
    if not api_key.startswith("sk-"):
        return jsonify({"error": "Invalid API key format (must start with 'sk-')"}), 400

    try:
        _validate_key_against_gateway(api_key)
    except Exception as e:
        logger.warning(f"API key validation failed for {username}: {e}")
        return jsonify({"error": "That key was rejected by the AI gateway. Check it and try again."}), 400

    try:
        encrypted = encrypt_api_key(api_key)
    except CryptoNotConfigured as e:
        return jsonify({"error": str(e)}), 503

    if db.user_exists(username):
        db.update_user_api_key(username, encrypted)
        logger.info(f"API key updated for {username}")
    else:
        db.create_user(username, encrypted)
        logger.info(f"New user registered: {username}")
    return jsonify({"success": True})


@api.route("/user/get_key", methods=["GET"])
def user_get_key():
    username = _username()
    user = db.get_user_by_username(username)
    if not user:
        return jsonify({"error": "No API key on file"}), 404
    try:
        masked = mask_api_key(decrypt_api_key(user["APIKey"]))
    except Exception:
        masked = "(unreadable — please re-enter)"
    return jsonify({"masked_key": masked, "created_at": user["CreatedAt"], "updated_at": user["UpdatedAt"]})


# ── Usage dashboards (rollout batch) ──────────────────────────────────────────

def _period_range():
    """Translate ?period=today|week|month into (start, end) DB timestamps."""
    period = request.args.get("period", "month")
    now = datetime.utcnow()
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = now - timedelta(days=7)
    else:
        start = now - timedelta(days=30)
    return db.fmt_dt(start), db.fmt_dt(now)


@api.route("/user/usage/summary", methods=["GET"])
def user_usage_summary():
    start, end = _period_range()
    return jsonify(db.get_user_usage_summary(_username(), start, end))


@api.route("/user/usage/chart", methods=["GET"])
def user_usage_chart():
    start, end = _period_range()
    return jsonify({"data": db.get_user_usage_chart(_username(), start, end)})


@api.route("/user/usage/history", methods=["GET"])
def user_usage_history():
    limit = min(int(request.args.get("limit", 20)), 100)
    offset = int(request.args.get("offset", 0))
    return jsonify({"history": db.get_user_usage_history(_username(), limit, offset)})


# ── Admin (rollout batch) ─────────────────────────────────────────────────────

def _admin_guard():
    try:
        require_admin(_username())
        return None
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403


@api.route("/admin/check", methods=["GET"])
def admin_check():
    return jsonify({"is_admin": is_admin(_username())})


@api.route("/admin/users/list", methods=["GET"])
def admin_users_list():
    guard = _admin_guard()
    if guard:
        return guard
    users = db.get_all_users()
    start, end = db.fmt_dt(datetime.utcnow() - timedelta(days=30)), db.fmt_dt(datetime.utcnow())
    for u in users:
        u["usage_summary"] = db.get_user_usage_summary(u["Username"], start, end)
    return jsonify({"users": users})


@api.route("/admin/usage/summary", methods=["GET"])
def admin_usage_summary():
    guard = _admin_guard()
    if guard:
        return guard
    start, end = _period_range()
    return jsonify(db.get_all_users_usage_summary(start, end))


@api.route("/admin/usage/by_user", methods=["GET"])
def admin_usage_by_user():
    guard = _admin_guard()
    if guard:
        return guard
    start, end = _period_range()
    limit = min(int(request.args.get("limit", 10)), 100)
    return jsonify({"data": db.get_usage_by_user(start, end, limit)})


@api.route("/admin/usage/by_model", methods=["GET"])
def admin_usage_by_model():
    guard = _admin_guard()
    if guard:
        return guard
    start, end = _period_range()
    return jsonify({"data": db.get_usage_by_model(start, end)})


@api.route("/admin/models/available", methods=["GET"])
def admin_models_available():
    guard = _admin_guard()
    if guard:
        return guard
    username = _username()
    user = db.get_user_by_username(username)
    try:
        api_key = decrypt_api_key(user["APIKey"]) if user else Config._ENV_LITELLM_API_KEY
    except Exception:
        api_key = Config._ENV_LITELLM_API_KEY
    return jsonify({"models": get_available_models(api_key)})


@api.route("/admin/models/current", methods=["GET"])
def admin_models_current():
    # Any signed-in user may see which model is active.
    return jsonify(get_current_model_config())


@api.route("/admin/models/select", methods=["POST"])
def admin_models_select():
    guard = _admin_guard()
    if guard:
        return guard
    data = request.get_json() or {}
    model_name = (data.get("model_name") or "").strip()
    reason = (data.get("reason") or "").strip()
    if not model_name:
        return jsonify({"error": "model_name is required"}), 400
    if change_model(model_name, _username(), reason):
        return jsonify({"success": True, "model_name": model_name})
    return jsonify({"error": "Failed to change model"}), 500


@api.route("/admin/models/history", methods=["GET"])
def admin_models_history():
    guard = _admin_guard()
    if guard:
        return guard
    return jsonify({"history": get_model_history(min(int(request.args.get("limit", 20)), 100))})


# ── Data Experts (rollout batch 2) ────────────────────────────────────────────

class _DiskFile:
    """Wraps a stored dataset file so the EXISTING upload handler can consume it."""

    def __init__(self, path: str, filename: str):
        self._path = path
        self.filename = filename

    def read(self) -> bytes:
        with open(self._path, "rb") as fh:
            return fh.read()


def _validate_expert_file(file_storage, sheet_name):
    """
    Parse an admin-uploaded expert file with the same reader users' uploads use.
    Returns (df, error_response). Multi-sheet workbooks require a sheet name.
    """
    ext = os.path.splitext(file_storage.filename or "")[1].lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        return None, None, (jsonify({"error": "Only .xlsx, .xls or .csv files are supported."}), 400)
    file_bytes = file_storage.read()
    if ext in (".xlsx", ".xls") and not sheet_name:
        sheets = get_excel_sheets(file_bytes)
        if len(sheets) > 1:
            return None, None, (jsonify({
                "error": f"This workbook has multiple sheets ({', '.join(sheets)}). "
                         f"Enter the sheet to use in the Sheet field and try again.",
                "sheets": sheets,
            }), 400)
    try:
        df, _enc, _warn = read_file(file_bytes, file_storage.filename, sheet_name or None)
    except FileHandlerError as e:
        return None, None, (jsonify({"error": str(e)}), 400)
    return df, file_bytes, None


@api.route("/experts/list", methods=["GET"])
def experts_list():
    """Experts the CURRENT user may use (server-side access filter)."""
    username = _username()
    groups = _groups()
    out = []
    for e in experts_svc.experts_for_user(username, groups):
        out.append({
            "id": e["ExpertID"],
            "label": e["Label"],
            "description": e["Description"] or "",
            "data_as_of": experts_svc.file_as_of(e),
        })
    return jsonify({"experts": out})


@api.route("/experts/load", methods=["POST"])
def experts_load():
    """
    Load an expert into this user's Standard slot — identical downstream
    behavior to uploading the file (pipeline/insights/tracking untouched).
    """
    sid = _sid()
    sess = get_session(sid)
    username = _username()
    body = request.get_json() or {}
    expert = experts_svc.get_expert(int(body.get("expert_id", 0)))

    if not expert or not expert["IsActive"] or not expert["FilePath"]:
        return jsonify({"error": "Data expert not found."}), 404
    if not experts_svc.user_allowed(expert, username, _groups()):
        logger.warning(f"Expert access denied | user={username} | expert={expert['Label']}")
        return jsonify({"error": "You don't have access to this data expert."}), 403
    if not os.path.exists(expert["FilePath"]):
        return jsonify({"error": "The expert's data file is missing on the server. Contact the admin."}), 500

    sess.clear_history("standard")
    disk_file = _DiskFile(expert["FilePath"], expert["OriginalFileName"] or "expert.xlsx")
    result = _handle_upload(disk_file, "df", sess, expert["SheetName"] or None)
    if "error" in result or result.get("needs_sheet_selection"):
        return jsonify({"error": result.get("error", "The expert file could not be read. Contact the admin.")}), 500

    # Present as the expert, not as a raw file
    result["filename"] = expert["Label"]
    result["expert"] = {
        "id": expert["ExpertID"],
        "label": expert["Label"],
        "description": expert["Description"] or "",
        "data_as_of": experts_svc.file_as_of(expert),
    }
    custom_q = experts_svc.questions_list(expert)
    if custom_q:
        result["suggestions"] = custom_q       # owner-authored chips win
    sess.filename = expert["Label"]
    save_session(sid, sess)
    logger.info(f"Expert loaded | user={username} | expert={expert['Label']} | shape=({result['rows']},{result['cols']})")
    return jsonify(result)


@api.route("/experts/groups_in_use", methods=["GET"])
def experts_groups_in_use():
    """For the .NET layer: which AD group names should be checked on the badge."""
    return jsonify({"groups": experts_svc.groups_in_use()})


@api.route("/admin/experts/list", methods=["GET"])
def admin_experts_list():
    guard = _admin_guard()
    if guard:
        return guard
    out = []
    for e in experts_svc.all_experts():
        out.append({
            "id": e["ExpertID"], "label": e["Label"], "description": e["Description"] or "",
            "file": e["OriginalFileName"] or "", "sheet": e["SheetName"] or "",
            "rows": e["Rows"], "cols": e["Cols"],
            "access_mode": e["AccessMode"],
            "allowed_users": e["AllowedUsers"] or "", "allowed_groups": e["AllowedGroups"] or "",
            "user_count": len(experts_svc.parse_list(e["AllowedUsers"] or "")),
            "questions": e["RecommendedQuestions"] or "",
            "active": bool(e["IsActive"]),
            "updated_by": e["UpdatedBy"], "updated_at": e["UpdatedAt"],
            "data_as_of": experts_svc.file_as_of(e),
        })
    return jsonify({"experts": out})


@api.route("/admin/experts/create", methods=["POST"])
def admin_experts_create():
    guard = _admin_guard()
    if guard:
        return guard
    username = _username()
    f = request.files.get("file")
    label = (request.form.get("label") or "").strip()
    if not label:
        return jsonify({"error": "The expert needs a name."}), 400
    if not f or not f.filename:
        return jsonify({"error": "A data file is required."}), 400

    sheet_name = (request.form.get("sheet_name") or "").strip() or None
    df, file_bytes, err = _validate_expert_file(f, sheet_name)
    if err:
        return err

    expert_id = experts_svc.create_expert(
        label=label,
        description=(request.form.get("description") or "").strip(),
        sheet_name=sheet_name,
        access_mode=(request.form.get("access_mode") or "restricted").strip(),
        allowed_users=(request.form.get("allowed_users") or "").strip(),
        allowed_groups=(request.form.get("allowed_groups") or "").strip(),
        questions=request.form.get("questions") or "",
        rows=len(df), cols=len(df.columns),
        created_by=username,
    )
    path = experts_svc.save_expert_file(expert_id, f.filename, file_bytes)
    experts_svc.set_expert_file(expert_id, path, f.filename, sheet_name, len(df), len(df.columns), username)
    logger.info(f"Expert created | '{label}' by {username} | {len(df)}x{len(df.columns)}")
    return jsonify({"success": True, "id": expert_id})


@api.route("/admin/experts/update", methods=["POST"])
def admin_experts_update():
    guard = _admin_guard()
    if guard:
        return guard
    body = request.get_json() or {}
    expert = experts_svc.get_expert(int(body.get("expert_id", 0)))
    if not expert:
        return jsonify({"error": "Expert not found"}), 404
    label = (body.get("label") or "").strip()
    if not label:
        return jsonify({"error": "The expert needs a name."}), 400
    experts_svc.update_expert(
        expert["ExpertID"], label,
        (body.get("description") or "").strip(),
        (body.get("access_mode") or "restricted").strip(),
        (body.get("allowed_users") or "").strip(),
        (body.get("allowed_groups") or "").strip(),
        body.get("questions") or "",
        _username(),
    )
    return jsonify({"success": True})


@api.route("/admin/experts/replace_file", methods=["POST"])
def admin_experts_replace_file():
    guard = _admin_guard()
    if guard:
        return guard
    username = _username()
    expert = experts_svc.get_expert(int(request.form.get("expert_id", 0)))
    if not expert:
        return jsonify({"error": "Expert not found"}), 404
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "A data file is required."}), 400
    sheet_name = (request.form.get("sheet_name") or "").strip() or None
    df, file_bytes, err = _validate_expert_file(f, sheet_name)
    if err:
        return err
    path = experts_svc.save_expert_file(expert["ExpertID"], f.filename, file_bytes)
    experts_svc.set_expert_file(expert["ExpertID"], path, f.filename, sheet_name, len(df), len(df.columns), username)
    logger.info(f"Expert file replaced | '{expert['Label']}' by {username} | {len(df)}x{len(df.columns)}")
    return jsonify({"success": True, "rows": len(df), "cols": len(df.columns)})


@api.route("/admin/experts/toggle", methods=["POST"])
def admin_experts_toggle():
    guard = _admin_guard()
    if guard:
        return guard
    body = request.get_json() or {}
    ok = experts_svc.set_expert_active(int(body.get("expert_id", 0)), bool(body.get("active")), _username())
    return jsonify({"success": ok})


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_FILE_BYTES
    app.register_blueprint(api)

    # Rollout batch: usage DB + token-capture shim (llm.py itself is untouched).
    try:
        db.initialize_database()
    except Exception as e:
        logger.error(f"Database initialization failed (key/usage features disabled): {e}")
    usage_capture.install()

    @app.route("/health")
    @app.route("/api/health")
    def health():
        return jsonify({
            "ok": True,
            "service": "chat-with-data-python",
            "modes": ["standard", "variance"],
            "llm_gateway_configured": bool(Config.LITELLM_API_KEY and Config.LITELLM_API_BASE),
        })

    @app.errorhandler(MissingSessionId)
    def _missing_sid(_e):
        return jsonify({"error": "Missing X-Session-Id header (provided by the front end)."}), 400

    @app.errorhandler(413)
    def _too_large(_e):
        return jsonify({"error": f"File too large. Max {Config.MAX_FILE_SIZE_MB} MB."}), 413

    return app


app = create_app()

if __name__ == "__main__":
    port = int(__import__("os").environ.get("SERVICE_PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=Config.DEBUG)
