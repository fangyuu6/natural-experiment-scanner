"""Model configurations for cross-model evaluation.

All models route through OpenRouter. The API key is shared (OPENAI_API_KEY).
"""
import os

OPENROUTER_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

MODELS = {
    "deepseek-v3.2": {
        "model": "deepseek/deepseek-v3.2",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "label": "DeepSeek V3.2",
    },
    "gpt-5.4": {
        "model": "openai/gpt-5.4",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "label": "GPT-5.4",
    },
    "minimax-2.5": {
        "model": "minimax/minimax-m2.5",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "label": "MiniMax 2.5",
    },
    "grok-4.1-fast": {
        "model": "x-ai/grok-4.1-fast",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "label": "Grok 4.1 Fast",
    },
    "xiaomi-mimo-v2-flash": {
        "model": "xiaomi/mimo-v2-flash",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "label": "Xiaomi MiMo V2 Flash",
    },
    "qwen-3.6-plus": {
        "model": "qwen/qwen3.6-plus:free",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "label": "Qwen 3.6 Plus",
        "is_primary": True,
    },
    "minimax-2.7": {
        "model": "minimax/minimax-m2.7",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "label": "MiniMax 2.7",
    },
}

# Model used as judge for accuracy review
JUDGE_MODEL = {
    "model": "anthropic/claude-opus-4",
    "api_key": OPENROUTER_API_KEY,
    "base_url": OPENROUTER_BASE_URL,
    "label": "Claude Opus 4",
}
