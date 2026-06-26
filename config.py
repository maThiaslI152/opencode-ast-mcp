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

MINIMAX_MODEL: str = os.getenv("MINIMAX_MODEL", "deepseek-v4-pro")
"""Model identifier. Defaults to DeepSeek V4 Pro. Examples:
- DeepSeek:   "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat" (deprecated 2026/07/24)
- OpenRouter: "anthropic/claude-3.5-haiku", "openai/gpt-4o", "qwen/qwen3-coder"
- OpenAI:     "gpt-4o-mini"
- ollama:     "qwen2.5-coder:32b"  (set MINIMAX_API_KEY to "ollama")
"""

DEEPSEEK_REASONING_EFFORT: str = os.getenv("DEEPSEEK_REASONING_EFFORT", "high")
"""Reasoning effort for DeepSeek V4 models ("high" | "medium" | "low").
Only used when MINIMAX_MODEL starts with ``deepseek``. Higher effort
gives better planning at the cost of more tokens and latency."""

DEEPSEEK_THINKING_MODE: bool = (
    os.getenv("DEEPSEEK_THINKING_MODE", "true").lower() == "true"
)
"""Whether to enable DeepSeek V4's thinking mode (returns a reasoning
trace alongside the response). The trace is visible in stderr logs but
does not affect the JSON output consumed by generate_sdd."""

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

# ---------------------------------------------------------------------------
# MCP Transport (stdio vs network)
# ---------------------------------------------------------------------------

MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "stdio")
"""Transport protocol: ``"stdio"`` (default, for local IDE), ``"sse"``,
or ``"streamable-http"``. Use ``"sse"`` or ``"streamable-http"`` to
expose the server on the LAN."""

MCP_HOST: str = os.getenv("MCP_HOST", "127.0.0.1")
"""Bind address. Use ``"0.0.0.0"`` to accept connections from other
machines on the LAN."""

MCP_PORT: int = int(os.getenv("MCP_PORT", "8000"))
"""Port the server listens on for SSE/HTTP transports."""
