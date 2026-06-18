"""LM Studio client — The local Scout.

Connects to a local LM Studio instance (Qwen 18B) for:
- Code analysis with structured JSON output
- AST-to-JSON conversion
- Error log compression (≤2 sentences)

Implements thermal cooldown (Gotcha C) between inference calls.
"""

from __future__ import annotations

import json
import re
import time

import requests

from config import LM_STUDIO_BASE, LM_STUDIO_MODEL, THERMAL_COOLDOWN_SECONDS


class LMStudioClient:
    """Client for the local LM Studio inference server."""

    def __init__(
        self,
        base_url: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self.base_url: str = base_url or LM_STUDIO_BASE
        self.model_id: str = model_id or LM_STUDIO_MODEL

    # ------------------------------------------------------------------
    # Existing method — Security analysis of extracted code nodes
    # ------------------------------------------------------------------

    def analyze_node(self, extracted_data: dict) -> dict:
        """Analyze an AST-extracted code chunk for security and data flow.

        Expects *extracted_data* to have a ``code`` key (as produced by
        ``ASTExtractor.extract_function_code``).  Returns structured JSON
        with ``node_name``, ``state_mutations``, ``data_flow``, and
        ``security_context``.
        """
        if "error" in extracted_data:
            return extracted_data

        # Construct the targeted prompt using the extracted source code
        user_prompt = f"""Analyze this isolated code chunk:

{extracted_data['code']}
"""

        # Define the exact JSON schema to enforce Grammar-Based Decoding via API
        json_schema = {
            "type": "object",
            "properties": {
                "node_name": {"type": "string"},
                "state_mutations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "data_flow": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "security_context": {"type": "string"},
            },
            "required": [
                "node_name",
                "state_mutations",
                "data_flow",
                "security_context",
            ],
            "additionalProperties": False,
        }

        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an AST analysis worker. Extract data-flow "
                        "paths and security contexts. Output STRICT JSON. "
                        "Do not output any conversational text."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "repeat_penalty": 1.05,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ast_security_analysis",
                    "schema": json_schema,
                },
            },
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions", json=payload
            )
            response.raise_for_status()
            llm_output = response.json()["choices"][0]["message"]["content"]
            result = json.loads(llm_output)
            # Gotcha C: thermal cooldown after successful inference
            time.sleep(THERMAL_COOLDOWN_SECONDS)
            return result
        except requests.exceptions.ConnectionError:
            return {
                "error": (
                    "Could not connect to LM Studio. "
                    "Is the local server running on port 1234?"
                )
            }
        except json.JSONDecodeError:
            return {
                "error": (
                    f"Model failed to return valid JSON. Raw output: {llm_output}"
                )
            }
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # New method — Error log compression
    # ------------------------------------------------------------------

    def compress_error_log(self, log: str) -> str:
        """Summarise a stack trace in at most TWO sentences.

        Includes root-cause file, line number, and error type.
        Used by the autonomous loop to feed concise error context
        back to M3 without burning tokens on raw logs.
        """
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Summarise the following error log in at most TWO "
                        "sentences. Include the root-cause file, line number, "
                        "and error type."
                    ),
                },
                {"role": "user", "content": f"```\n{log}\n```"},
            ],
            "temperature": 0.0,
            "repeat_penalty": 1.05,
            "max_tokens": 200,
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions", json=payload
            )
            response.raise_for_status()
            summary = response.json()["choices"][0]["message"]["content"]
            # Gotcha C: thermal cooldown
            time.sleep(THERMAL_COOLDOWN_SECONDS)
            return summary
        except requests.exceptions.ConnectionError:
            return "[LM Studio unavailable — raw log attached]"
        except Exception as e:
            return f"[Compression failed: {e}]"

    # ------------------------------------------------------------------
    # New method — AST-to-JSON extraction
    # ------------------------------------------------------------------

    def extract_ast_json(self, source: str, schema_hint: str) -> dict:
        """Convert raw AST text into clean JSON using the local model.

        Uses ``response_format: json_object`` for models that support it,
        with a regex fallback to strip markdown fences.
        """
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Convert the following AST dump into clean JSON. "
                        "Respond ONLY with valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Desired schema: {schema_hint}\n"
                        f"AST:\n```\n{source}\n```"
                    ),
                },
            ],
            "temperature": 0.0,
            "repeat_penalty": 1.05,
            "response_format": {"type": "json_object"},
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions", json=payload
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            # Gotcha C: thermal cooldown
            time.sleep(THERMAL_COOLDOWN_SECONDS)
        except requests.exceptions.ConnectionError:
            return {"error": "Could not connect to LM Studio."}
        except Exception as e:
            return {"error": str(e)}

        # Strip markdown fences if present (fallback)
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            return {"error": f"Failed to parse JSON. Raw: {raw[:500]}"}


# --- Local Testing Block ---
if __name__ == "__main__":
    from ast_extractor import ASTExtractor

    print("Extracting code via AST...")
    extractor = ASTExtractor()
    ast_result = extractor.extract_function_code("dummy_auth.py", "my_auth_logic")

    if "error" not in ast_result:
        print(f"Extraction successful. Sending to LM Studio for security analysis...")
        client = LMStudioClient()
        analysis_result = client.analyze_node(ast_result)

        print("\n--- Final Security Report ---")
        print(json.dumps(analysis_result, indent=2))
    else:
        print(ast_result)