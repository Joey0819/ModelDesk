"""Configuration for the LLM Council."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY")

# Council members - list of OpenRouter model identifiers
COUNCIL_MODELS = [model.strip() for model in os.getenv("COUNCIL_MODELS", "").split(",") if model.strip()] or [
    "openai/gpt-5.1",
    "google/gemini-3-pro-preview",
    "anthropic/claude-sonnet-4.5",
    "x-ai/grok-4",
]

# Chairman model - synthesizes final response
CHAIRMAN_MODEL = os.getenv("CHAIRMAN_MODEL", "google/gemini-3-pro-preview")

# OpenRouter API endpoint
OPENROUTER_API_URL = os.getenv("LLM_API_BASE", "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
TITLE_MODEL = os.getenv("TITLE_MODEL", CHAIRMAN_MODEL)

# Data directory for conversation storage
DATA_DIR = "data/conversations"
