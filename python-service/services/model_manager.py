"""
Model management — list available models from the gateway, get/change the
globally active model (admin only). The active model is stored in SQLite and
applied to every request via services/request_context.py.
"""

import requests
from typing import List, Dict, Any

from config import Config
from logger import logger
from services.database import get_current_model, set_current_model, get_model_change_history
from services.pricing import get_model_pricing


def get_available_models(api_key: str) -> List[Dict[str, Any]]:
    """OpenAI-compatible /v1/models listing from the gateway."""
    try:
        url = f"{Config.LITELLM_API_BASE}/v1/models"
        r = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10, verify=False)
        r.raise_for_status()
        models = r.json().get("data", [])
        logger.info(f"Fetched {len(models)} available models from gateway")
        return models
    except Exception as e:
        logger.error(f"Failed to fetch available models: {e}")
        return []


def get_current_model_config() -> Dict[str, Any]:
    mc = get_current_model()
    if not mc:
        pricing = get_model_pricing(Config._ENV_LLM_MODEL)
        return {"model_name": Config._ENV_LLM_MODEL, "input_price": pricing["input"],
                "output_price": pricing["output"], "set_by": "system", "set_at": None}
    return {"model_name": mc["ModelName"], "input_price": mc["InputPrice"],
            "output_price": mc["OutputPrice"], "set_by": mc["SetBy"], "set_at": mc["SetAt"]}


def change_model(model_name: str, admin_username: str, reason: str = "") -> bool:
    try:
        pricing = get_model_pricing(model_name)
        set_current_model(model_name, admin_username, pricing["input"], pricing["output"], reason)
        return True
    except Exception as e:
        logger.error(f"Failed to change model: {e}")
        return False


def get_model_history(limit: int = 20) -> List[Dict[str, Any]]:
    return get_model_change_history(limit)
