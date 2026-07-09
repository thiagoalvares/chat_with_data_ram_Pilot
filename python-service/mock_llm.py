"""
Mock LLM gateway — FOR LOCAL/OFFLINE TESTING ONLY.
--------------------------------------------------
Stands in for the GA LiteLLM gateway so the whole app can run on a developer
machine with no network access. It implements the same endpoint the real
gateway exposes (POST /v1/chat/completions) and returns deterministic
responses, so you can exercise every UI feature — answers, charts, the
Query & Calculations panel, Excel export, and PNG export — without a real model.

This does NOT change the application's prompts or pipeline in any way; the
Python service simply points LITELLM_API_BASE at this server instead of the
real gateway:

    LITELLM_API_BASE=http://localhost:9000
    LITELLM_API_KEY=mock

Run:  python mock_llm.py   (listens on :9000)

The numbers returned here are placeholders. For real analysis, point the
service at the actual GA gateway.
"""

import os
import json
from flask import Flask, request, jsonify

app = Flask(__name__)


def _last_user_content(messages) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            return str(m.get("content", ""))
    return ""


def _make_code(prompt_text: str) -> str:
    """
    Call 1 (temperature 0) — return pandas code that always produces a `result`.
    Picks variance vs. standard frames based on what the prompt references.
    """
    if "df_a" in prompt_text or "df_b" in prompt_text:
        return (
            "# mock: return a small preview from the first variance file\n"
            "result = df_a.head(20)"
        )
    return (
        "# mock: return a small preview of the data\n"
        "result = df.head(20)"
    )


def _make_answer() -> str:
    """
    Call 2 (temperature 0.3) — return the answer + optional chart as JSON,
    exactly the shape the real gateway returns and parse_answer_response expects.
    """
    payload = {
        "answer": (
            "**Mock mode.** This response comes from the local mock LLM, so the "
            "numbers below are placeholders for offline UI testing. Connect the "
            "real GA gateway (set LITELLM_API_BASE) for actual analysis. "
            "Everything else — the two-call pipeline, charts, the Query & "
            "Calculations panel, and Excel/PNG export — is exercised end to end."
        ),
        "chart": {
            "type": "bar",
            "title": "Sample chart (mock)",
            "labels": ["Alpha", "Bravo", "Charlie", "Delta"],
            "datasets": [{"label": "Demo values", "data": [12, 19, 7, 15]}],
            "x_label": "Category",
            "y_label": "Value",
        },
    }
    return json.dumps(payload)


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    body        = request.get_json(force=True, silent=True) or {}
    messages    = body.get("messages", [])
    temperature = body.get("temperature", 0)

    # Call 1 = code generation (temp 0); Call 2 = answer formatting (temp > 0).
    if float(temperature) == 0:
        content = _make_code(_last_user_content(messages))
    else:
        content = _make_answer()

    return jsonify({
        "id": "mock-cmpl",
        "object": "chat.completion",
        "model": body.get("model", "mock-model"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "mock-llm"})


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_LLM_PORT", "9000"))
    print(f"Mock LLM gateway listening on http://localhost:{port} (TEST ONLY)")
    app.run(host="0.0.0.0", port=port)
