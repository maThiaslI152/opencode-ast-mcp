"""Centralized configuration for the Kiro-Opencode hybrid agentic coding system.

Reads settings from environment variables with sensible defaults.
Uses python-dotenv to load .env files automatically at module import.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from the project root at module import time
load_dotenv(dotenv_path=Path(__file__).parent / ".env")


def get_project_root() -> Path:
    """Return the directory containing config.py (the project root)."""
    return Path(__file__).parent


# ---------------------------------------------------------------------------
# LLM Brain (OpenAI-compatible — DeepSeek, OpenRouter, OpenAI, ollama, etc.)
# ---------------------------------------------------------------------------
# Historical name "MINIMAX_*" is kept for env-var backward compatibility,
# but the values now describe any OpenAI-compatible provider.

MINIMAX_API_KEY: str = os.getenv("MINIMAX_API_KEY", "")
"""API key for the LLM brain provider (DeepSeek, OpenRouter, OpenAI, etc.).
Required for `generate_sdd` and the autonomous loop's auto-patching, but can
be empty during local development."""

MINIMAX_BASE_URL: str = os.getenv("MINIMAX_BASE_URL", "https://api.deepseek.com")
"""Base URL of the OpenAI-compatible endpoint. Defaults to DeepSeek."""

MINIMAX_MODEL: str = os.getenv("MINIMAX_MODEL", "deepseek-chat")
"""Model identifier. Defaults to DeepSeek V3 (deepseek-chat). Examples:
- DeepSeek:   "deepseek-chat", "deepseek-reasoner"
- OpenRouter: "anthropic/claude-3.5-haiku", "openai/gpt-4o", "qwen/qwen3-coder"
- OpenAI:     "gpt-4o-mini"
- ollama:     "qwen2.5-coder:32b"  (set MINIMAX_API_KEY to "ollama")
"""

# ---------------------------------------------------------------------------
# LM Studio (Local Worker – Qwen 18B)
# ---------------------------------------------------------------------------

LM_STUDIO_BASE: str = os.getenv("LM_STUDIO_BASE", "http://localhost:1234/v1")
"""Base URL for the local LM Studio server."""

LM_STUDIO_MODEL: str = os.getenv(
    "LM_STUDIO_MODEL", "qwen3.5-18b-a3b-reap-coding-heretic-v0-i1"
)
"""Model identifier served by LM Studio."""

# ---------------------------------------------------------------------------
# Podman Sandbox
# ---------------------------------------------------------------------------

PODMAN_WORKSPACE: str = os.getenv("PODMAN_WORKSPACE", str(get_project_root()))
"""Host directory mounted into the Podman sandbox container."""

PODMAN_IMAGE: str = os.getenv("PODMAN_IMAGE", "opencode-sandbox:latest")
"""Container image used for the sandboxed execution environment."""

SANDBOX_TIMEOUT: int = int(os.getenv("SANDBOX_TIMEOUT", "300"))
"""Maximum seconds a sandbox command is allowed to run before being killed."""

# ---------------------------------------------------------------------------
# Autonomous Loop Safety
# ---------------------------------------------------------------------------

MAX_AUTONOMOUS_ITERATIONS: int = int(os.getenv("MAX_AUTONOMOUS_ITERATIONS", "5"))
"""Hard ceiling on consecutive autonomous iterations to prevent runaway loops."""

THERMAL_COOLDOWN_SECONDS: float = float(os.getenv("THERMAL_COOLDOWN_SECONDS", "3.0"))
"""Pause (in seconds) between autonomous iterations to manage thermal load."""
