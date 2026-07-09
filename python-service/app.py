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
from typing import Any

import pandas as pd
from flask import Blueprint, Flask, jsonify, request, send_file

from config import Config
from logger import logger
from models.schemas import DebugEntry
from prompts import standard, variance
from services import llm as llm_service
from services import insights
from services.data_manager import get_session, save_session, get_schema_info
from services.executor import execute_generated_code
from services.file_handler import FileHandlerError, get_excel_sheets, read_file
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


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_FILE_BYTES
    app.register_blueprint(api)

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
