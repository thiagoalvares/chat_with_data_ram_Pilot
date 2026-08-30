"""
Model pricing (USD per 1M tokens) used for cost calculation in usage tracking.

NOTE: lives in services/ (the plan's config/pricing.py would have created a
`config` package shadowing config.py and broken every `from config import
Config` in the app).

Update these to match the gateway's billed rates; unknown models fall back to
DEFAULT_PRICING so tracking never fails.

AVAILABLE_MODELS is the SINGLE source of truth for which models users may
select, their display labels, and their prices. The user-facing model picker
(/user/set_model, /user/get_model) and cost tracking all derive from it —
edit models or prices HERE and nowhere else.
"""

DEFAULT_PRICING = {"input": 2.00, "output": 10.00}

# One row per selectable model: gateway name, UI label, USD per 1M tokens.
AVAILABLE_MODELS = [
    {"value": "claude-sonnet-4-5", "label": "Claude Sonnet 4.5", "input": 3.00, "output": 15.00},
    {"value": "gpt-5.4",           "label": "GPT-5.4",           "input": 2.50, "output": 15.00},
    {"value": "gpt-4o",            "label": "GPT-4o",            "input": 2.50, "output": 10.00},
    {"value": "gpt-5.1",           "label": "GPT-5.1",           "input": 2.00, "output": 10.00},
]

MODEL_PRICING = {m["value"]: {"input": m["input"], "output": m["output"]} for m in AVAILABLE_MODELS}

ALLOWED_MODEL_NAMES = [m["value"] for m in AVAILABLE_MODELS]


def model_options() -> list:
    """Picker entries for the UI, with price labels derived from the table."""
    return [{"value": m["value"], "label": m["label"],
             "price": f"${m['input']:g} / ${m['output']:g}"} for m in AVAILABLE_MODELS]


def get_model_pricing(model_name: str) -> dict:
    return MODEL_PRICING.get(model_name, DEFAULT_PRICING)


def calculate_cost(prompt_tokens: int, completion_tokens: int, pricing: dict):
    prompt_cost = (prompt_tokens / 1_000_000) * pricing["input"]
    completion_cost = (completion_tokens / 1_000_000) * pricing["output"]
    return round(prompt_cost, 6), round(completion_cost, 6), round(prompt_cost + completion_cost, 6)
