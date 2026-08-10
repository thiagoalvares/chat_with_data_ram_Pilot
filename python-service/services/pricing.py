"""
Model pricing (USD per 1M tokens) used for cost calculation in usage tracking.

NOTE: lives in services/ (the plan's config/pricing.py would have created a
`config` package shadowing config.py and broken every `from config import
Config` in the app).

Update these to match the gateway's billed rates; unknown models fall back to
DEFAULT_PRICING so tracking never fails.
"""

DEFAULT_PRICING = {"input": 2.00, "output": 10.00}

MODEL_PRICING = {
    "claude-sonnet-5":   {"input": 2.00,  "output": 10.00},
    "claude-sonnet-4-5": {"input": 3.00,  "output": 15.00},
    "claude-opus-4":     {"input": 15.00, "output": 75.00},
    "gpt-5.4":           {"input": 2.50,  "output": 15.00},
    "gpt-4o":            {"input": 2.50,  "output": 10.00},
}


def get_model_pricing(model_name: str) -> dict:
    return MODEL_PRICING.get(model_name, DEFAULT_PRICING)


def calculate_cost(prompt_tokens: int, completion_tokens: int, pricing: dict):
    prompt_cost = (prompt_tokens / 1_000_000) * pricing["input"]
    completion_cost = (completion_tokens / 1_000_000) * pricing["output"]
    return round(prompt_cost, 6), round(completion_cost, 6), round(prompt_cost + completion_cost, 6)
