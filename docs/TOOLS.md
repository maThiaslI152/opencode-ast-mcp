# MCP Tools Reference

The server exposes 9 tools. They are grouped by backing service.

> **Note on returns:** the FastMCP transport wraps every return in
> `{"result": "<string>"}`. Tools that return JSON encode it as a string,
> so callers should `json.loads(...)` the `result` field.

---

## AST Tools (deterministic, no network)

### 1. `get_file_skeleton`

Return a compact outline of a file's top-level classes and functions.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `filepath` | string | yes | Absolute or relative path. Supported extensions: `.py`, `.js`, `.ts`, `.tsx` |

**Returns:** human-readable string.

```
class AuthService:
  def login(...)
  def logout(...)
def hash_password(...)
```

**When to use:** before reading a file, to see what's in it without paying
the token cost of the full source.

**Gotchas:**
- Only top-level nodes are shown — nested defs inside an `if __name__` block
  are excluded.
- Methods inside classes are listed without their signatures (just `name`).

---

### 2. `get_node`

Return the full source code of a single named function or class.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `filepath` | string | yes | Path to source file |
| `node_name` | string | yes | Function/method/class name |

**Returns:** source code as a string, or an error message if the name is
not found.

**When to use:** after `get_file_skeleton`, to read exactly the code you
need instead of the entire file.

**Gotchas:**
- The first match wins — if two functions share a name, the topmost wins.
- Returns an error string (not a Python exception) on miss; check whether
  the response starts with `"Error:"` or `"File not found"`.

---

### 3. `get_ast_json`

Return a structured JSON view of the file's nodes.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `filepath` | string | yes | Path to source file |

**Returns:** JSON string with shape:

```json
{
  "file": "...",
  "language": "python",
  "nodes": [
    {"type": "function", "name": "foo", "start_line": 1, "end_line": 5, "params": ["a", "b"]},
    {"type": "class", "name": "Bar", "start_line": 7, "end_line": 20, "methods": ["__init__", "run"]}
  ]
}
```

**When to use:** when you need machine-readable structural info (line
ranges, param lists) for further programmatic processing.

**Gotchas:**
- Same scope as `get_file_skeleton` — top-level only.
- `params` for Python includes typed/default params flattened to just the
  name (e.g. `x: int = 5` → `"x"`).

---

## Local LLM Tools (Qwen 18B via LM Studio)

### 4. `analyze_node`

Ask the local Qwen model to perform security / data-flow analysis on a
named function or class.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `filepath` | string | yes | Source file |
| `node_name` | string | yes | Function/class to analyze |
| `question` | string | yes | The analysis question |

**Returns:** JSON string with keys `node_name`, `state_mutations`,
`data_flow`, `security_context` (Qwen is forced into this schema via
`response_format: json_schema`).

**When to use:** for security review or data-flow tracing of a specific
code chunk without sending it to the cloud.

**Gotchas:**
- Requires LM Studio to be running on `LM_STUDIO_BASE` (default
  `http://localhost:1234/v1`) with the configured model loaded.
- Adds a 3s thermal cooldown after every successful call.
- If the model is not loaded, `lms load <model> --gpu max` then retry.

---

### 5. `compress_log`

Summarise a verbose error log to ≤2 sentences.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `error_log` | string | yes | The raw error output / stack trace |

**Returns:** a short string (typically one or two sentences) with the
root-cause file, line number, and error type.

**When to use:** any time an error output is longer than ~50 lines, before
sending it back to M3 in the autonomous loop.

**Gotchas:**
- For inputs under 200 chars, the input is returned unchanged (saves a
  round-trip).
- If LM Studio is unreachable, returns the literal string
  `"[LM Studio unavailable — raw log attached]"` — caller's responsibility
  to detect that.

---

## Sandbox Tools (Podman)

### 6. `execute_in_sandbox`

Run a single shell command in an ephemeral Podman container.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `command` | string | yes | Shell command, e.g. `"pytest tests/ -v"` |

**Returns:** JSON string:

```json
{
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "timed_out": false,
  "duration_seconds": 0.93
}
```

**When to use:** one-off inspection, running tests, checking dependencies.
The container is destroyed after the command exits.

**Gotchas:**
- Workspace is mounted read-write at `/workspace`. Edits inside the
  container persist on the host.
- Hard timeout: `SANDBOX_TIMEOUT` seconds (default 300). The container is
  killed and `timed_out=true` is set in the result.
- Stderr may include a harmless "Executing external compose provider"
  warning from podman-compose.

---

## Orchestration Tools (M3 + autonomous loop)

### 7. `execute_autonomous_loop_tool`

Run a code → test → fix cycle for a single plan step.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `step_name` | string | yes | Human-readable name; used in `BLOCKED.md` and patch log filenames |
| `test_command` | string | yes | Shell command run in the sandbox |
| `context` | string | no | Code context to send to M3 when patching (default `""`) |
| `initial_patch` | string | no | Unified-diff patch to apply before the first test (default `""`) |

**Returns:** JSON string:

```json
{
  "step_name": "...",
  "success": true,
  "iterations": 2,
  "final_output": "...",
  "error_summary": null,
  "blocked": false
}
```

**When to use:** for each step in a `plan.md`, after the user has approved
the plan. The loop runs the test, and on failure asks M3 for a patch
(using `context` as ground truth), **applies that patch to the project
tree** via `git apply` (with `patch -p1` as fallback), and retries — up
to `MAX_AUTONOMOUS_ITERATIONS` (5) times.

**Patch application flow:**

1. Iteration N runs the test → fails.
2. Loop compresses the error via Qwen and sends it to M3.
3. M3 returns a unified-diff patch.
4. The patch is written to `.opencode/patches/<step>_iter<N+1>.patch`
   (audit trail) **and** applied to the project tree via `_apply_patch`.
5. Iteration N+1 runs the test against the patched code.
6. If the patch fails to apply (context mismatch, fuzz rejection,
   path-escape attempt), the iteration is recorded as a patch-apply
   failure (no test run, since the code state is unknown), the apply
   error is fed back to M3, and the loop asks for a new patch.

**Gotchas:**
- M3 patches **are** applied to the filesystem between iterations. The
  primary applier is `git apply` (cleanest error messages, handles
  fuzz, rejects path-escapes); `patch -p1` is the fallback if `git`
  isn't on `PATH`.
- If M3 is unavailable (`MINIMAX_API_KEY` not set or 401), the loop still
  runs the test but never patches — it just reports the test result.
- On circuit-breaker trip, `BLOCKED.md` is written to the project root.
  Delete it after manually fixing the issue.
- `final_output` is truncated to 2000 chars to keep MCP responses small.
- Patches with path-escape attempts (e.g. `../../etc/passwd`) are
  rejected by `git apply` and never reach the filesystem.
- The sandbox image (`sandbox/Containerfile`) installs both `git` and
  `patch` so the loop works in the container as well as on the host.

---

### 8. `generate_sdd`

Ask M3 to generate product / tech / plan documents for a feature.

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `feature_request` | string | yes | Natural-language feature description |
| `codebase_context` | string | no | Optional pre-summarised context; if empty, the tool auto-builds a skeleton of all top-level `*.py` files in the project root |

**Returns:** JSON string with keys `product`, `tech`, `plan` — each
containing a markdown string.

**When to use:** at the start of a non-trivial feature. The returned docs
go into `sdd/product.md`, `sdd/tech.md`, `sdd/plan.md` for the user to
review and approve before any code is written.

**Gotchas:**
- Requires `MINIMAX_API_KEY` in `.env`. Without it, returns
  `{"error": "MiniMax API key is not set..."}`.
- The auto-built context includes every top-level `*.py` (not subdirs).
  Pass a `codebase_context` explicitly for larger projects.
- M3 occasionally returns non-JSON; in that case the tool falls back to
  putting the raw text under the `"plan"` key.

---

### 9. `get_loop_status`

Read the `BLOCKED.md` file (if it exists) to see if the autonomous loop
tripped the circuit breaker.

**Params:** none.

**Returns:** the contents of `BLOCKED.md` if present, otherwise
`{"status": "clear", "message": "No blocked steps."}`.

**When to use:** as a first step before launching a new plan step, to
make sure no prior step is still unresolved.

**Gotchas:**
- The status is just "does the file exist?" — it does not actually
  verify the issue is still present.

---

## Quick decision tree

```
Need to read code?
  ├─ Whole file structure first  →  get_file_skeleton
  └─ Specific function/class     →  get_node (after skeleton)
  └─ Machine-readable structure   →  get_ast_json

Need to understand code?
  ├─ Security / data-flow on a chunk  →  analyze_node (Qwen, local)
  └─ Big-picture design                →  generate_sdd (M3, cloud)

Need to run code?
  ├─ One-off command           →  execute_in_sandbox
  └─ Code→test→fix cycle       →  execute_autonomous_loop_tool

Need to summarise an error?    →  compress_log (Qwen, local)

Need to check loop state?      →  get_loop_status
```
