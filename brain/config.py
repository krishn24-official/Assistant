"""
Central config for the brain service.

All keys are optional - the app only uses providers that have a key set.
Get free-tier keys from:
  Groq:      https://console.groq.com          (fast, generous free tier)
  Mistral:   https://console.mistral.ai         (free tier w/ rate limits)
  Anthropic: https://console.anthropic.com      (small free credit for new accounts)
"""
import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

from pathlib import Path
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass
class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    mistral_api_key: str = os.getenv("MISTRAL_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")

    # Order to try providers in. First one with a key + no error wins.
    # Reorder in .env via LLM_PROVIDER_ORDER="gemini,groq,mistral,anthropic" if you want.
    provider_order: List[str] = field(
        default_factory=lambda: os.getenv(
            "LLM_PROVIDER_ORDER", "gemini,groq,mistral,anthropic"
        ).split(",")
    )

    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    mistral_model: str = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    db_path: str = os.getenv("DB_PATH", "assistant_memory.db")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8008"))


settings = Settings()
