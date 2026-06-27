"""Kiro-Opencode MCP Server — The Bridge.

FastMCP server exposing 13 tools to OpenCode IDE:

AST Tools (deterministic, instant):
  - get_file_skeleton: Compact structural outline of a file
  - get_node: Full source code of a named function/class
  - get_ast_json: Structured JSON AST representation

Codebase Awareness Tools (recursive, mtime-cached):
  - list_files: Glob with skip-dir filtering
  - get_project_overview: Top-level project map with skeletons
  - search_symbol: Find functions/classes/methods by name across project
  - find_references: AST-aware identifier reference search

Local LLM Tools (Qwen 3 VL 4B via LM Studio):
  - analyze_node: Security & data-flow analysis of a code node
  - compress_log: Error log compression to ≤2 sentences

Sandbox Tools (Podman):
  - execute_in_sandbox: Run a single command in the container

Orchestration Tools (M3 + autonomous loop):
  - generate_sdd: Trigger SDD document generation via M3
  - execute_autonomous_loop: Full plan→patch→test→fix cycle
  - get_loop_status: Check if the system is blocked
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ast_extractor import ASTExtractor
from codebase_index import CodebaseIndex
from lm_client import LMStudioClient
from sandbox_runner import SandboxRunner, run_in_sandbox
from config import get_project_root, MCP_HOST, MCP_PORT, MCP_TRANSPORT
import threading
import time
from functools import wraps

# ---------------------------------------------------------------------------
# Idle Timeout Monitor (1 hour)
# ---------------------------------------------------------------------------

_last_active_time = time.time()

def _idle_monitor():
    while True:
        time.sleep(60)
        if time.time() - _last_active_time > 3600:
            os._exit(0)

threading.Thread(target=_idle_monitor, daemon=True).start()

def activity_tracker(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        global _last_active_time
        _last_active_time = time.time()
        return func(*args, **kwargs)
    return wrapper

# ---------------------------------------------------------------------------
# Initialise components
# ---------------------------------------------------------------------------

mcp = FastMCP("opencode-ast", host=MCP_HOST, port=MCP_PORT)

# Lazily initialised singletons (created on first tool call)
_extractor: ASTExtractor | None = None
_lm_client: LMStudioClient | None = None
_sandbox: SandboxRunner | None = None
_codebase_index: CodebaseIndex | None = None


def _get_extractor() -> ASTExtractor:
    global _extractor
    if _extractor is None:
        _extractor = ASTExtractor()
    return _extractor


def _get_lm_client() -> LMStudioClient:
    global _lm_client
    if _lm_client is None:
        _lm_client = LMStudioClient()
    return _lm_client


def _get_sandbox() -> SandboxRunner:
    global _sandbox
    if _sandbox is None:
        _sandbox = SandboxRunner()
    return _sandbox


def _get_codebase_index() -> CodebaseIndex:
    global _codebase_index
    if _codebase_index is None:
        _codebase_index = CodebaseIndex()
    return _codebase_index


# ===================================================================
# Tool 1: get_file_skeleton (AST — deterministic)
# ===================================================================

@mcp.tool()
@activity_tracker
def get_file_skeleton(filepath: str) -> str:
    """Return a compact skeleton of every top-level class and function in the given file.

    Use this BEFORE reading full source code to understand file structure
    and decide which specific nodes to examine. Supports Python, JavaScript,
    and TypeScript files.

    Args:
        filepath: Absolute or relative path to the source file.

    Returns:
        A human-readable outline showing classes, methods, and functions.
    """
    return _get_extractor().get_skeleton(filepath)


# ===================================================================
# Tool 2: get_node (AST — deterministic)
# ===================================================================

@mcp.tool()
@activity_tracker
def get_node(filepath: str, node_name: str) -> str:
    """Return the full source code of a specific function or class by name.

    Use this after get_file_skeleton to read the exact code of a node
    you need to analyze or modify.

    Args:
        filepath: Path to the source file.
        node_name: Name of the function, method, or class to extract.

    Returns:
        The full source code of the matched node, or an error message
        if the node is not found.
    """
    result = _get_extractor().extract_function_code(filepath, node_name)
    if "error" in result:
        return result["error"]
    return result["code"]


# ===================================================================
# Tool 3: get_ast_json (AST — deterministic)
# ===================================================================

@mcp.tool()
@activity_tracker
def get_ast_json(filepath: str) -> str:
    """Return a structured JSON representation of a file's top-level nodes.

    Each node includes type (function/class), name, line range,
    parameters (for functions), and methods (for classes).

    Args:
        filepath: Path to the source file.

    Returns:
        JSON string with file, language, and nodes array.
    """
    result = _get_extractor().get_ast_json(filepath)
    return json.dumps(result, indent=2)


# ===================================================================
# Tool 4: list_files (Codebase Awareness — recursive, mtime-cached)
# ===================================================================

@mcp.tool()
@activity_tracker
def list_files(pattern: str = "**/*") -> str:
    """List files in the project matching a glob, with skip-dir filtering.

    Recursive by default (``pattern="**/*"``). Skips ``.venv``, ``.git``,
    ``node_modules``, ``__pycache__``, and other noise directories
    (see ``codebase_index._SKIP_DIRS``). Returns paths relative to the
    project root, sorted.

    Args:
        pattern: A glob pattern. Examples: ``"**/*"`` (recursive),
                 ``"*.py"`` (root only), ``"src/**/*.ts"``.

    Returns:
        JSON string ``{"files": ["relative/path.py", ...]}``.
    """
    return json.dumps(
        {"files": _get_codebase_index().list_files(pattern)}, indent=2
    )


# ===================================================================
# Tool 5: get_project_overview (Codebase Awareness — recursive, mtime-cached)
# ===================================================================

@mcp.tool()
@activity_tracker
def get_project_overview(depth: int = 1) -> str:
    """Return a top-level project map with per-file skeletons.

    Walks the project tree to *depth* directory levels (depth=1 = just
    the project root; depth=2 = one level of subdirectories too). For
    each supported-language file found, includes its path, language,
    size in bytes, and a compact skeleton (defs + classes).

    Parsed ASTs are mtime-cached, so repeated calls on unchanged files
    are O(1). Calling after editing a file re-parses only that file
    on the next invocation.

    Args:
        depth: Maximum number of path components relative to root.

    Returns:
        JSON string with keys ``root``, ``files`` (list of file dicts
        each with ``path``, ``language``, ``size``, ``skeleton``),
        ``truncated`` (bool), ``scanned_files`` (int).
    """
    return json.dumps(_get_codebase_index().get_overview(depth), indent=2)


# ===================================================================
# Tool 6: search_symbol (Codebase Awareness — recursive, mtime-cached)
# ===================================================================

@mcp.tool()
@activity_tracker
def search_symbol(name: str, language: str = "") -> str:
    """Find every top-level function/class/method named *name*.

    Walks every supported-language file under the project root and runs
    a tree-sitter-based name match. ``language`` filters to a single
    language (``"python"``, ``"javascript"``, ``"typescript"``,
    ``"tsx"``). Empty string = all supported languages.

    Args:
        name: Symbol name to search for (exact match).
        language: Optional language filter. Pass ``""`` to search all.

    Returns:
        JSON string with ``matches`` (list of ``{file, name, type,
        start_line, end_line}``), ``truncated`` (bool — true if capped
        at 200), ``scanned_files`` (int).
    """
    lang = language or None
    return json.dumps(_get_codebase_index().search_symbol(name, lang), indent=2)


# ===================================================================
# Tool 7: find_references (Codebase Awareness — recursive, mtime-cached)
# ===================================================================

@mcp.tool()
@activity_tracker
def find_references(name: str, filepath: str = "") -> str:
    """Find every identifier reference to *name* via AST walking.

    Unlike ``grep``, this skips string literals and comments because
    those aren't ``identifier`` nodes in the tree-sitter AST. Pass
    ``filepath`` to scope the search to one file; leave empty to scan
    the whole project.

    Args:
        name: Identifier name to find (exact match).
        filepath: Optional relative path to scope the search. ``""`` =
                  project-wide.

    Returns:
        JSON string with ``references`` (list of ``{file, line,
        context}``), ``truncated`` (bool), ``scanned_files`` (int).
    """
    path = filepath or None
    return json.dumps(
        _get_codebase_index().find_references(name, path), indent=2
    )


# ===================================================================
# Tool 8: analyze_node (Local LLM — Qwen 3 VL 4B)
# ===================================================================

@mcp.tool()
@activity_tracker
def analyze_node(filepath: str, node_name: str, question: str) -> str:
    """Use the local Qwen 3 VL 4B model to analyze a specific code node.

    Extracts the node via AST, then sends it to LM Studio for
    security analysis, data-flow extraction, or general questions.

    Args:
        filepath: Path to the source file.
        node_name: Name of the function/class to analyze.
        question: The analysis question (e.g. "Are there SQL injection risks?").

    Returns:
        JSON string with node_name, state_mutations, data_flow,
        and security_context.
    """
    extracted = _get_extractor().extract_function_code(filepath, node_name)
    result = _get_lm_client().analyze_node(extracted, question=question)
    return json.dumps(result, indent=2)


# ===================================================================
# Tool 9: compress_log (Local LLM — Qwen 3 VL 4B)
# ===================================================================

@mcp.tool()
@activity_tracker
def compress_log(error_log: str) -> str:
    """Compress a verbose error log into a ≤2 sentence summary.

    Sends the raw log to the local Qwen model which returns
    the root-cause file, line number, and error type in a
    concise summary. Use this for any error output longer
    than ~50 lines.

    Args:
        error_log: The raw error output / stack trace.

    Returns:
        A 1-2 sentence summary of the root cause.
    """
    return _get_lm_client().compress_error_log(error_log)


# ===================================================================
# Tool 10: execute_in_sandbox (Podman)
# ===================================================================

@mcp.tool()
@activity_tracker
def execute_in_sandbox(command: str) -> str:
    """Run a single shell command inside the Podman sandbox container.

    The project workspace is mounted at /workspace. Use this for
    one-off commands like running tests, checking dependencies,
    or verifying file state.

    Args:
        command: Shell command to execute (e.g. "pytest tests/ -v").

    Returns:
        JSON string with exit_code, stdout, stderr, timed_out,
        and duration_seconds.
    """
    result = run_in_sandbox(command)
    return json.dumps(
        {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
        },
        indent=2,
    )


# ===================================================================
# Tool 11: execute_autonomous_loop (M3 + Podman + Qwen)
# ===================================================================

@mcp.tool()
@activity_tracker
def execute_autonomous_loop_tool(
    step_name: str,
    test_command: str,
    context: str = "",
    initial_patch: str = "",
) -> str:
    """Execute the full autonomous code→test→fix loop for a plan step.

    Runs the test command in Podman. On failure, compresses the error
    via local Qwen, generates a patch via M3, and retries — up to
    5 iterations (circuit breaker). If all iterations fail, creates
    BLOCKED.md in the project root.

    Args:
        step_name: Human-readable name for this plan step.
        test_command: Shell command to validate the step (e.g. "pytest tests/test_auth.py -v").
        context: Code context for M3 to understand what to patch.
        initial_patch: Optional initial patch to apply before the first test.

    Returns:
        JSON string with success, iterations, final_output, error_summary,
        and blocked status.
    """
    from autonomous_loop import execute_autonomous_loop

    # Try to create M3 client (may fail if no API key)
    m3_client = None
    try:
        from m3_client import M3Client
        m3_client = M3Client()
    except (ValueError, ImportError):
        pass  # M3 unavailable — loop runs without auto-patching

    result = execute_autonomous_loop(
        step_name=step_name,
        test_command=test_command,
        context=context,
        initial_patch=initial_patch or None,
        m3_client=m3_client,
    )

    return json.dumps(
        {
            "step_name": result.step_name,
            "success": result.success,
            "iterations": result.iterations,
            "final_output": result.final_output[:2000],  # Truncate for token safety
            "error_summary": result.error_summary,
            "blocked": result.blocked,
        },
        indent=2,
    )


# ===================================================================
# Tool 12: generate_sdd (M3)
# ===================================================================

@mcp.tool()
@activity_tracker
def generate_sdd(feature_request: str, codebase_context: str = "") -> str:
    """Trigger M3 to generate Software Design Documents (SDD).

    Produces product.md, tech.md, and plan.md content based on the
    codebase context and feature request.

    Args:
        feature_request: Natural-language description of the feature.
        codebase_context: Optional summarised codebase / architecture context.

    Returns:
        JSON string with product, tech, and plan keys containing
        markdown content for each SDD document.
    """
    try:
        from m3_client import M3Client
        client = M3Client()
    except (ValueError, ImportError) as e:
        return json.dumps({"error": str(e)})

    # If no context provided, auto-generate from workspace skeleton
    if not codebase_context:
        extractor = _get_extractor()
        project_root = get_project_root()
        skeletons = []
        for py_file in sorted(project_root.glob("*.py")):
            if py_file.name.startswith("__"):
                continue
            skeleton = extractor.get_skeleton(str(py_file))
            skeletons.append(f"### {py_file.name}\n```\n{skeleton}\n```")
        codebase_context = "\n\n".join(skeletons) or "(empty project)"

    result = client.plan_sdd(codebase_context, feature_request)
    return json.dumps(result, indent=2)


# ===================================================================
# Tool 13: get_loop_status (Status check)
# ===================================================================

@mcp.tool()
@activity_tracker
def get_loop_status() -> str:
    """Check if the autonomous loop is currently blocked.

    Returns the content of BLOCKED.md if it exists, or a status
    message indicating the system is clear.

    Returns:
        The content of BLOCKED.md or a "clear" status message.
    """
    blocked_path = get_project_root() / "BLOCKED.md"
    if blocked_path.exists():
        return blocked_path.read_text(encoding="utf-8")
    return json.dumps({"status": "clear", "message": "No blocked steps."})


# ===================================================================
# Entry point
# ===================================================================

if __name__ == "__main__":
    mcp.run(transport=MCP_TRANSPORT)
