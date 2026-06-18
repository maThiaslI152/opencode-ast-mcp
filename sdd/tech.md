# Technical Design — opencode-ast-mcp (v0.2.0)

## Architecture Decision

**Single FastMCP stdio server, lazy singletons, mtime-cached project index.**

The server runs as one Python process that:

1. Boots via `start.sh`, which optionally starts LM Studio + loads the
   configured Qwen model.
2. Enters a stdio JSON-RPC loop via FastMCP.
3. Lazily initialises five external-service clients on first tool call
   (extractor / codebase index / LM Studio / sandbox / brain) — saves
   ~4 s of startup time when the agent only uses AST tools.
4. Shuts itself down after 1 hour of zero tool calls (intentional idle
   timeout, `os._exit(0)`).

Four orthogonal sub-systems, each in its own module:

| Sub-system | Module | Backing service |
|------------|--------|-----------------|
| AST extraction | `ast_extractor.py` | tree-sitter (in-process, pure Python) |
| Codebase awareness | `codebase_index.py` | tree-sitter + mtime cache (in-process) |
| Local code analysis | `lm_client.py` | LM Studio (Qwen 18B) over HTTP |
| Cloud planning | `m3_client.py` | OpenAI-compatible endpoint (default: OpenRouter) |
| Sandboxed execution | `sandbox_runner.py` | Podman container |
| Orchestration | `autonomous_loop.py` | Combines sandbox + Qwen + brain |

## Component Interactions

```
OpenCode IDE
   │  MCP (stdio / JSON-RPC)
   ▼
server.py  (FastMCP)
   │
   ├── AST path           →  ast_extractor.py      →  tree-sitter
   │
   ├── Codebase-awareness →  codebase_index.py     →  tree-sitter + mtime cache
   │      ├── list_files      (pathlib.glob + skip-dir filter)
   │      ├── get_overview    (rglob + per-file skeleton, mtime-cached)
   │      ├── search_symbol   (cross-file name match, mtime-cached)
   │      └── find_references (identifier walk, AST-aware, mtime-cached)
   │
   ├── Local-LM path      →  lm_client.py          →  LM Studio (Qwen 18B)
   │                           └── 3 s thermal cooldown after every call
   │
   ├── Brain path         →  m3_client.py          →  OpenRouter (Claude Haiku)
   │                           └── token usage logged to stderr
   │
   └── Sandbox path       →  sandbox_runner.py     →  Podman container
                               ├── os.sync() before run (Gotcha A)
                               ├── _validate_workspace() before run (Gotcha D)
                               └── if test fails in autonomous_loop:
                                     Qwen.compress_error → brain.generate_patch
                                     → git apply → retry up to 5× (Gotcha B)
```

## Data Flow

1. **Tool call arrives** on stdin (JSON-RPC, FastMCP dispatches).
2. **`@activity_tracker`** bumps `_last_active_time` (resets 1-hour idle
   timer).
3. **Tool function** pulls lazy singleton(s) it needs:
   - AST tools → `ASTExtractor()` (constructor is fast).
   - Codebase-awareness tools → `CodebaseIndex()` (lazy, first call builds
     the mtime cache).
   - LM tools → `LMStudioClient()` (no network call in constructor).
   - Brain tool → `M3Client()` (raises `ValueError` if no key).
   - Sandbox tools → `SandboxRunner()` (validates workspace on construct).
4. **Work happens** (read file / call LLM / spawn container / walk project
   tree).
5. **Result returned** as a string (or JSON-encoded string for structured
   tools). FastMCP wraps it in a JSON-RPC response on stdout.

For **mtime-cached codebase-aware calls** the flow is:
1. Check cache key `(relative_path, mtime)`.
2. If hit → return cached `(tree, source)`.
3. If miss → parse with tree-sitter, store in cache, FIFO-evict if >1000
   entries.
4. Stat the file's `st_mtime` (no background watcher, no fsnotify).

For the **autonomous loop** the flow is longer (see
[docs/ARCHITECTURE.md §Request lifecycle](../docs/ARCHITECTURE.md#request-lifecycle)).

## Mtime cache design

```
┌─────────────────────────────────────────────────────────┐
│ _cache: dict[str, _CacheEntry]                          │
│                                                         │
│ key: "ast_extractor.py"                                 │
│   _CacheEntry(mtime=1718700000.0, tree=..., source=...) │
│ key: "codebase_index.py"                                │
│   _CacheEntry(mtime=1718700001.0, tree=..., source=...) │
│ ...                                                     │
│                                                         │
│ Max 1000 entries, FIFO eviction.                        │
│ (capped because unbounded growth on a 10k-file project  │
│  could hurt memory over a long MCP session.)            │
└─────────────────────────────────────────────────────────┘
```

## File Changes

| Action | File | Description |
|--------|------|-------------|
| MODIFY | server.py | `@mcp.tool()` + `@activity_tracker` on each of 13 functions |
| NEW | ast_extractor.py | tree-sitter wrapper, lazy grammar loading, static methods for skeleton/refs/named-nodes |
| NEW | codebase_index.py | Mtime-cached recursive project index (4 public methods, shared cache) |
| NEW | lm_client.py | LM Studio HTTP client, json_schema constraint for `analyze_node` |
| NEW | m3_client.py | OpenAI-compatible client, `_strip_markdown_fence` fallback for SDD parsing |
| NEW | sandbox_runner.py | Podman wrapper, `_validate_workspace` allow-list |
| NEW | autonomous_loop.py | ReAct loop, `_apply_patch` (git apply → patch -p1), `BLOCKED.md` writer, patch log writer |
| NEW | dummy_auth.py | Test fixture exercising `extract_function_code` |
| NEW | tests/test_*.py | 59 pytest tests across 5 modules |
| NEW | start.sh | Boot script, `lms server start` + `lms load` |
| NEW | sandbox/Containerfile | `python:3.13-slim` + pytest + git + patch |
| NEW | sandbox/compose.yaml | Podman compose, mounts `${PODMAN_WORKSPACE}` at `/workspace` |
| NEW | docs/ | ARCHITECTURE / TOOLS / SETUP / TROUBLESHOOTING |
| NEW | AGENTS.md | Guidance for AI coding agents |
| NEW | CHANGELOG.md | Release history |
| NEW | CONTRIBUTING.md | Contributor checklist |
| NEW | SECURITY.md | Private-disclosure policy |
| NEW | LICENSE | MIT (2026) |
| NEW | .github/workflows/test.yml | CI — 59 pytest in Podman on every push/PR |
| NEW | .github/dependabot.yml | Weekly pip + GitHub Actions updates |

## Error Handling Strategy

| Layer | Strategy |
|-------|----------|
| Tool input | Validate via FastMCP's Pydantic-generated schema. Invalid args |
|          | never reach the function body. |
| AST | Return `{"error": "..."}` dict from `extract_function_code`. Tool |
|     | checks for the key and surfaces the message. |
| Codebase index | `FileNotFoundError` from `_get_parsed` → skip the file. |
|                 | Unsupported extension → `ValueError`, skip. |
|                 | Cache eviction is silent (FIFO pop). |
| LM Studio | Catch `requests.ConnectionError` → return friendly hint. |
|            | Catch `json.JSONDecodeError` → return raw output snippet. |
| OpenRouter | Catch all exceptions, log to stderr, return JSON `{"error": ...}` |
|             | with the SDK's error message. |
| Sandbox | `subprocess.TimeoutExpired` → `timed_out=true` in result. |
|          | Podman unavailable → exit_code=-1, stderr="Sandbox execution error: ...". |
| Loop | Circuit breaker at 5 iterations → writes `BLOCKED.md`, returns |
|       | `LoopResult(success=False, blocked=True)`. |
| Loop patch apply | `_apply_patch` failures skip the test run (code state unknown) |
|                   | and feed the apply error back to M3 as next context. |

Every `except` block either logs or returns — no silent failures.

## Security Considerations

- **Workspace mount validation** (Gotcha D): refuses `/`, `/Users`,
  `/home`, `/root`, `/var`, `/etc`, `/tmp`, and any user home root.
  Prevents prompt-injection escape via `-v /:/host`.
- **`os.sync()` before sandbox run** (Gotcha A): ensures M3's patched
  file state is visible inside the container.
- **Skip-dir filter**: prevents the codebase-awareness tools from
  walking into `.venv`, `.git`, `node_modules`, etc. — no accidental
  exposure of virtualenvs or node_modules content.
- **No secrets in logs**: token-usage messages go to stderr, never to
  `.opencode/patches/`. Patch content is logged but is generated by
  the brain, not by the user.
- **Path-escape rejection**: `git apply` inherently rejects patches
  that reference files outside the working tree (`../../etc/passwd`
  is a no-op).
- **Idle timeout** prevents zombie processes from accumulating API
  quota / GPU memory.
- **`.gitignore` covers `.env`**: secrets never get committed.
- **No `--privileged`** in Podman compose, no host network mode.

## Rollback Plan

The server is stateless except for `BLOCKED.md` and `.opencode/patches/`.
To roll back any change:

1. `git checkout -- .` (revert working tree)
2. `rm BLOCKED.md .opencode/patches/*` (clean audit artefacts)
3. Restart the server (`pgrep -f server.py | xargs kill` then re-spawn).

The 1-hour idle timer means a misbehaving process self-exits within
60 minutes of the last MCP call, so an unrecoverable crash has a
bounded blast radius.
