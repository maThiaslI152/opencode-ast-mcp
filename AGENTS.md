# AGENTS.md — Guidance for AI Coding Agents

> **You are an AI agent working in this repo.** Read this file in full
> before making changes. It documents the conventions, the MCP tools
> available to you, and the failure modes specific to this project.

---

## Project at a glance

This is a Python MCP server that turns the OpenCode IDE into an agentic
coding system. It exposes 9 tools backed by:

- **tree-sitter** for AST extraction (Python / JS / TS)
- **LM Studio + Qwen 18B** for local code analysis and log compression
- **OpenAI-compatible LLM brain** (default: OpenRouter + Claude Haiku) for cloud SDD planning and patch generation
- **Podman** for sandboxed test execution

You are likely operating *through* the MCP tools (from OpenCode) or
*on* the MCP server code (as a contributor). These two modes have
different guidance — see below.

---

## If you are an OpenCode agent USING these tools

### How the tools map onto OpenCode's plan/build mode

OpenCode itself has a **plan mode** (read-only) and a **build mode** (full
write access). Of the 9 tools exposed by this server, only two are
side-effecting and must wait for build mode:

- ✅ Allowed in **plan mode**: `get_file_skeleton`, `get_node`, `get_ast_json`,
  `analyze_node`, `compress_log`, `get_loop_status`, `generate_sdd`
- ❌ Blocked in plan mode (use only in build mode): `execute_in_sandbox`,
  `execute_autonomous_loop_tool`

`generate_sdd` is the bridge between the two modes: it runs entirely in
plan mode (no file writes), produces the SDD artifacts, and the user
flips to build mode for the autonomous loop to walk through `plan.md`.
See `README.md` → "How it works with OpenCode plan/build mode" for the
full table.

### Tool-selection rules (in priority order)

1. **Before reading any file**, call `get_file_skeleton` first. It is
   cheaper than reading the full file and lets you decide whether to
   load the file at all.
2. **To read a specific function or class**, call `get_node` with the
   exact name. Do not guess at the structure.
3. **For security review or data-flow tracing** of a code chunk, use
   `analyze_node` (local Qwen). Do not paste the code into the main
   context window if you can avoid it.
4. **For verbose error output (>50 lines)**, call `compress_log` first,
   then read the summary. Saves tokens.
5. **For a single shell command**, use `execute_in_sandbox`. For a
   code→test→fix cycle, use `execute_autonomous_loop_tool`.
6. **For architectural planning** of a new feature, use `generate_sdd`
   and present the docs to the user for approval before writing code.

### Anti-patterns to avoid

- **Never** call `read` on a file before `get_file_skeleton` — you will
  waste tokens on a file that may not need to be read.
- **Never** use the local Qwen model to plan a whole feature. Use
  `generate_sdd` (M3) for that. Qwen is for narrow per-function analysis.
- **Never** call `execute_autonomous_loop_tool` without an approved plan.
  The loop writes `BLOCKED.md` on failure, which pollutes the project
  root and confuses the user.
- **Never** send a >50-line error log to M3 directly. Use
  `compress_log` first.
- **Never** modify files outside the project workspace. The sandbox
  enforces this, but be careful in your own edits too.

### Two-phase workflow (matches `prompts/system_prompt.md`)

**Phase 1 — Planning:**
1. Use `get_file_skeleton` to understand the codebase.
2. Use `get_node` to read the specific functions you need.
3. Use `analyze_node` for security review of the touch points.
4. Call `generate_sdd` to produce `product.md`, `tech.md`, `plan.md`.
5. **HALT** and present the plan to the user. Do not write code yet.

**Phase 2 — Execution (only after plan approval):**
1. For each step in `plan.md`, call `execute_autonomous_loop_tool`.
2. Wait for `success: true` before moving to the next step.
3. If `blocked: true`, stop and ask the user. Do not skip the step.

### Patch format

When the autonomous loop asks M3 for a patch, M3 returns a
unified-diff-style string. The expected format is:

```
--- a/filepath
+++ b/filepath
@@ -start,count +start,count @@
-old line
+new line
 context line
```

When *you* (the OpenCode agent) write a patch, use the same format.
Apply with `git apply` or paste into the file directly.

### Stop conditions

Stop and ask the user when:
- A test fails after the autonomous loop's circuit breaker trips.
- A tool returns an error you don't recognise.
- The plan in `plan.md` needs to change.
- You're about to install a new package or modify `requirements.txt`.
- You're about to commit changes (this is a hard rule — never commit).

---

## If you are a CONTRIBUTOR modifying this repo

### Repository conventions

- **Python 3.13**, type hints everywhere (use `X | None` not `Optional[X]`).
- **No comments** unless they explain *why*, not *what*. The code should
  be self-explanatory.
- **No `# noqa`** without a comment explaining why it's safe.
- **No bare `except:`** — catch specific exceptions.
- **No silent failures** — every `except` block must log or return.
- **Docstrings** on every public function, class, and module. Use the
  Google style (Args / Returns / Raises sections).

### Module boundaries (don't cross them casually)

| Module | Owns | Do not put here |
|--------|------|-----------------|
| `server.py` | Tool registration, idle timer, activity tracker | Business logic |
| `ast_extractor.py` | tree-sitter parsing | I/O, network |
| `lm_client.py` | LM Studio HTTP calls | M3 calls |
| `m3_client.py` | M3 / OpenAI-compatible calls | LM Studio calls |
| `sandbox_runner.py` | Podman invocation | LLM calls |
| `autonomous_loop.py` | ReAct loop, circuit breaker | Direct LLM/Sandbox calls (use the modules) |
| `config.py` | Env-var loading | Anything else |

### Test commands

Run all tests via the sandbox (matches what OpenCode does):

```bash
execute_in_sandbox(command="cd /workspace && pip install -q -r requirements.txt && python -m pytest tests/ -v")
```

Or directly on the host (faster, no Podman overhead):

```bash
./venv/bin/pip install -r requirements.txt
./venv/bin/python -m pytest tests/ -v
```

Expected: `35 passed in ~0.5s`.

### Adding a new MCP tool

1. Decide which module owns the logic (see table above).
2. Add a function in that module with full docstring (Args / Returns).
3. Register it in `server.py` with `@mcp.tool()` + `@activity_tracker`.
4. Add a smoke test to `tests/test_integration.py::test_mcp_tools_registered`
   (it asserts the set of registered tool names).
5. Document the tool in `docs/TOOLS.md`.
6. Run the test suite — it must remain at 35+ passing.

### Adding a new language to the AST extractor

1. Add the tree-sitter grammar to `requirements.txt` (e.g.
   `tree-sitter-go>=0.25.0`).
2. Extend the `_LANGUAGES` dict in `ast_extractor.py` with the new
   extension, `Language(...)`, and a config dict (see the `.js` entry).
3. Add a test to `tests/test_ast_extractor.py::TestExtractorPython`-style
   classes (one per language).
4. Update `docs/TOOLS.md` to list the new supported extension.

### Safety constraints

- **Never** weaken the sandbox mount validation in `sandbox_runner.py`.
  Gotcha D (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)) prevents
  the agent from escaping into the host filesystem.
- **Never** increase `MAX_AUTONOMOUS_ITERATIONS` past 10 in defaults. The
  circuit breaker is load-bearing for cost and runaway-loop protection.
- **Never** log full API responses to a file — M3 responses can contain
  code that looks like it belongs in source files. Stderr only.
- **Never** commit `.env` or any file containing a real API key. The
  `.gitignore` is set up; verify with `git status --ignored` before
  committing.

### Lint / typecheck (none currently configured)

There is no `ruff`, `mypy`, or `black` config in this repo. If you add
one, wire it to a `Makefile` or `tox.ini` target and document it here.

---

## Key gotchas (read these before debugging)

- **`generate_sdd` 500** is almost always missing `openai` module
  (run `pip install -r requirements.txt`) or missing `MINIMAX_API_KEY`
  in `.env`. A 401 ("login fail") usually means the key prefix doesn't
  match the provider — `sk-or-v1-…` is OpenRouter, plain `sk-…` is
  OpenAI, `eyJ…` is MiniMax. See
  [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).
- **Sandbox timeouts** are silent — check the `timed_out` field in the
  JSON response, not the exit code.
- **The 1-hour idle timer** in `server.py:48` is intentional, not a bug.
- **The current `autonomous_loop` does apply M3's patches to disk** via
  `git apply` (fallback: `patch -p1`). Every patch attempt is also
  logged to `.opencode/patches/` for audit. See
  [docs/TOOLS.md](docs/TOOLS.md#7-execute_autonomous_loop_tool) for the
  full apply-failure flow.
- **`start.sh:18` `export $(grep ... | xargs)`** breaks on values with
  spaces. Keep `.env` values space-free.

---

## Pointers

- Architecture deep dive: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Per-tool reference: [docs/TOOLS.md](docs/TOOLS.md)
- Setup walkthrough: [docs/SETUP.md](docs/SETUP.md)
- Common failures: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- M3 orchestrator system prompt: [prompts/system_prompt.md](prompts/system_prompt.md)
- SDD templates: [sdd/](sdd/)

---

## Plan-mode etiquette

When you (the agent) are operating in plan mode:

- Do **not** write files, run commands, or install packages.
- Do **not** call `execute_in_sandbox` or `execute_autonomous_loop_tool`.
  They have side effects.
- Do use the read-only tools: `get_file_skeleton`, `get_node`,
  `get_ast_json`, `analyze_node`, `compress_log`, `get_loop_status`.
- Do present a concrete plan with file paths and line numbers.
- Do not exit plan mode without explicit user approval.

When you are out of plan mode but the user has not yet approved
executable actions, err on the side of asking before running
`execute_in_sandbox` or any `pip install` / `podman` command.
