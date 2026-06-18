"""LLM Brain client — architecture reasoning, SDD planning, patch generation.

Connects to any OpenAI-compatible endpoint (OpenRouter, OpenAI, local ollama,
etc.) for cloud-side reasoning. Configured via MINIMAX_API_KEY, MINIMAX_BASE_URL,
and MINIMAX_MODEL in `.env`. The historical name "MiniMax M3" is kept for env-var
backward compatibility — the variables now describe any OpenAI-compatible
provider.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from openai import OpenAI

import config


class M3Client:
    """High-level wrapper around the MiniMax M3 model.

    All methods communicate via the OpenAI-compatible chat completions
    endpoint and log token usage to *stderr* after every request.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        resolved_key = api_key or config.MINIMAX_API_KEY
        if not resolved_key:
            raise ValueError(
                "LLM brain API key is not set. "
                "Set the MINIMAX_API_KEY environment variable or pass api_key= to M3Client(). "
                "Works with OpenRouter (https://openrouter.ai/), OpenAI, or any OpenAI-compatible "
                "endpoint — see .env.example for configuration."
            )

        self.model = model or config.MINIMAX_MODEL
        self._client = OpenAI(
            api_key=resolved_key,
            base_url=base_url or config.MINIMAX_BASE_URL,
        )

    # -----------------------------------------------------------------
    # Token-usage logger
    # -----------------------------------------------------------------

    @staticmethod
    def _log_usage(usage: Any) -> None:
        """Print token counts to stderr for observability."""
        if usage is None:
            return
        prompt = getattr(usage, "prompt_tokens", "?")
        completion = getattr(usage, "completion_tokens", "?")
        total = getattr(usage, "total_tokens", "?")
        print(
            f"[M3] tokens  prompt={prompt}  completion={completion}  total={total}",
            file=sys.stderr,
        )

    # -----------------------------------------------------------------
    # Core chat method
    # -----------------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Send a chat completion and return the assistant's content string.

        Args:
            messages: Conversation history (list of ``{"role": …, "content": …}``).
            system_prompt: Optional system message prepended to *messages*.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the response.

        Returns:
            The text content of the assistant's reply.
        """
        full_messages = self._build_messages(messages, system_prompt)

        response = self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        self._log_usage(response.usage)
        return response.choices[0].message.content or ""

    # -----------------------------------------------------------------
    # Chat with tool definitions
    # -----------------------------------------------------------------

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str | None = None,
    ) -> dict:
        """Send a chat completion with tool definitions.

        Args:
            messages: Conversation history.
            tools: OpenAI-format tool definitions.
            system_prompt: Optional system message.

        Returns:
            The full response as a dict, including any ``tool_calls``.
        """
        full_messages = self._build_messages(messages, system_prompt)

        response = self._client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            tools=tools,
        )

        self._log_usage(response.usage)

        # Convert to a plain dict so callers don't depend on the SDK model
        msg = response.choices[0].message
        result: dict[str, Any] = {
            "role": msg.role,
            "content": msg.content,
        }
        if msg.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        return result

    # -----------------------------------------------------------------
    # Specialised: SDD planning
    # -----------------------------------------------------------------

    def plan_sdd(self, codebase_context: str, feature_request: str) -> dict:
        """Ask M3 to generate Software Design Documents for a feature.

        Args:
            codebase_context: Summarised codebase / architectural context.
            feature_request: Natural-language description of the feature.

        Returns:
            Dict with keys ``product``, ``tech``, and ``plan`` — each
            containing the respective SDD section as a string.
        """
        system = (
            "You are a principal software architect. "
            "Given the codebase context and a feature request, produce THREE sections:\n"
            "1. **product** — Product requirements document.\n"
            "2. **tech** — Technical design document.\n"
            "3. **plan** — Step-by-step implementation plan.\n\n"
            "Return your answer as a single JSON object with exactly these keys: "
            '"product", "tech", "plan". Each value is a markdown string. '
            "Do NOT wrap the JSON in a code fence. Do NOT include any prose before or "
            "after the JSON. Output ONLY the JSON object, starting with { and ending with }."
        )

        messages = [
            {
                "role": "user",
                "content": (
                    f"## Codebase Context\n{codebase_context}\n\n"
                    f"## Feature Request\n{feature_request}"
                ),
            }
        ]

        raw = self.chat(
            messages,
            system_prompt=system,
            temperature=0.3,
            max_tokens=4096,
        )

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            stripped = _strip_markdown_fence(raw)
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                # Best-effort: return the raw text under "plan"
                return {"product": "", "tech": "", "plan": raw}

    # -----------------------------------------------------------------
    # Specialised: patch generation
    # -----------------------------------------------------------------

    def generate_patch(
        self,
        context: str,
        error_summary: str,
        previous_patch: str | None = None,
    ) -> str:
        """Ask M3 to generate a code patch.

        Args:
            context: Relevant code / file context.
            error_summary: Compressed error description.
            previous_patch: Optional prior patch attempt that failed.

        Returns:
            A unified-diff-style patch string.
        """
        parts = [
            f"## Code Context\n{context}",
            f"## Error Summary\n{error_summary}",
        ]
        if previous_patch:
            parts.append(
                f"## Previous Patch (failed — do NOT repeat the same mistake)\n{previous_patch}"
            )

        system = (
            "You are a senior software engineer. "
            "Generate a minimal unified-diff patch that fixes the described error. "
            "Output ONLY the patch — no explanation, no markdown fences."
        )

        return self.chat(
            [{"role": "user", "content": "\n\n".join(parts)}],
            system_prompt=system,
            temperature=0.2,
            max_tokens=4096,
        )

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _build_messages(
        messages: list[dict], system_prompt: str | None
    ) -> list[dict]:
        """Prepend a system message if provided."""
        if system_prompt:
            return [{"role": "system", "content": system_prompt}, *messages]
        return list(messages)


def _strip_markdown_fence(text: str) -> str:
    """Remove a leading/trailing ```json (or ```) fence from *text*.

    Some chat models wrap JSON in a markdown code fence despite being told
    to return raw JSON. Stripping it lets the JSON parser succeed.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*\n?", "", stripped, count=1)
    if stripped.endswith("```"):
        stripped = stripped[: stripped.rfind("```")]
    return stripped.strip()
