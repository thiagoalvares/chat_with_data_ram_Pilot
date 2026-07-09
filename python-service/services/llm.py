import json
import time
import requests
from typing import Tuple, Optional
from config import Config
from logger import logger


def _call(messages: list, temperature: float, max_tokens: int = None) -> Tuple[bool, str]:
    """
    Raw HTTP POST to the LiteLLM proxy.
    No external library — uses requests only.
    """
    url     = f"{Config.LITELLM_API_BASE}/v1/chat/completions"
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {Config.LITELLM_API_KEY}",
    }
    body = {
        "model":       Config.LLM_MODEL,
        "messages":    messages,
        "max_tokens":  max_tokens or Config.LLM_MAX_TOKENS,
        "temperature": temperature,
    }
    # Log message details for debugging
    num_messages = len(messages)
    total_chars = sum(len(str(m.get('content', ''))) for m in messages)
    logger.info(f"LLM request | temp={temperature} | max_tokens={body['max_tokens']} | num_messages={num_messages} | total_chars={total_chars:,}")
    start = time.time()
    try:
        r = requests.post(url, headers=headers, json=body, timeout=Config.LLM_TIMEOUT, verify=False)
        elapsed = round(time.time() - start, 2)

        r.raise_for_status()

        # Parse response
        try:
            response_json = r.json()
        except json.JSONDecodeError as e:
            logger.error(f"LLM response is not valid JSON | {elapsed}s | Response text: {r.text[:500]}")
            return False, f"API returned non-JSON response: {r.text[:200]}"

        # Check if response has expected structure
        if "choices" not in response_json:
            logger.error(f"LLM response missing 'choices' | {elapsed}s | Response: {json.dumps(response_json)[:500]}")
            return False, f"Invalid API response format: {json.dumps(response_json)[:200]}"

        if not response_json["choices"] or len(response_json["choices"]) == 0:
            logger.error(f"LLM response has empty choices | {elapsed}s | Response: {json.dumps(response_json)[:500]}")
            return False, "API returned empty choices array"

        content = response_json["choices"][0]["message"]["content"].strip()
        logger.info(f"LLM call success | temp={temperature} | {elapsed}s | {len(content)} chars")
        return True, content

    except requests.exceptions.HTTPError:
        elapsed = round(time.time() - start, 2)
        try:
            error_json = r.json()
            logger.error(f"LLM HTTP error | {r.status_code} | {elapsed}s | {json.dumps(error_json)[:300]}")
            return False, f"HTTP {r.status_code}: {error_json.get('error', {}).get('message', r.text[:200])}"
        except:
            logger.error(f"LLM HTTP error | {r.status_code} | {elapsed}s | {r.text[:300]}")
            return False, f"HTTP {r.status_code}: {r.text[:200]}"

    except requests.exceptions.Timeout:
        logger.error(f"LLM call timed out after {Config.LLM_TIMEOUT}s")
        return False, f"LLM call timed out after {Config.LLM_TIMEOUT} seconds"

    except KeyError as e:
        elapsed = round(time.time() - start, 2)
        logger.error(f"LLM response missing expected field '{e}' | {elapsed}s | Response: {r.text[:500]}")
        return False, f"Invalid API response: missing field {e}"

    except Exception as e:
        elapsed = round(time.time() - start, 2)
        try:
            logger.error(f"LLM call failed: {str(e)} | {elapsed}s | Response: {r.text[:500]}")
        except:
            logger.error(f"LLM call failed: {str(e)} | {elapsed}s")
        return False, f"LLM call failed: {str(e)}"


def parse_answer_response(raw: str) -> Tuple[str, Optional[dict]]:
    """
    Parse Call 2 JSON response into (answer, chart_spec).
    Handles markdown fences, leading/trailing text, and other LLM formatting quirks.
    """
    try:
        cleaned = raw.strip()

        # Strip any markdown fences (```json ... ``` or ``` ... ```)
        if "```" in cleaned:
            lines   = cleaned.split("\n")
            cleaned = "\n".join(
                l for l in lines if not l.strip().startswith("```")
            ).strip()

        # Find the first { and last } — extract just the JSON object
        start = cleaned.find("{")
        end   = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start:end + 1]

        data   = json.loads(cleaned)
        answer = data.get("answer", raw)
        chart  = data.get("chart", None)
        return answer, chart

    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("Could not parse LLM answer as JSON — using raw text")
        return raw, None


# ── Public API ────────────────────────────────────────────────────────────────

def generate_query_code(messages: list) -> Tuple[bool, str]:
    """Call 1 — generate pandas code. Temperature 0 for determinism."""
    return _call(messages, temperature=0)


def generate_human_answer(messages: list) -> Tuple[bool, str]:
    """Call 2 — convert result to plain English + optional chart spec."""
    ok, content = _call(messages, temperature=0.3, max_tokens=4096)
    #logger.info(f"Call 2 raw response: {repr(content[:500])}")
    return ok, content
