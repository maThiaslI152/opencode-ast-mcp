# Product Specification — opencode-ast-mcp (v0.2.0)

## Feature Overview

A Model Context Protocol (MCP) server that exposes **13 specialised tools**
to AI coding agents (OpenCode, Claude Desktop, etc.) so they can plan,
analyse, edit, and verify code without burning context on boilerplate.

The server is a **local-first** assistant: AST extraction and project-wide
codebase awareness are pure Python (tree-sitter + mtime cache), code
analysis runs against a local Qwen 18B via LM Studio, and only the
optional cloud-side planning step (SDD generation, patch proposals)
talks to an external LLM. Code execution happens inside ephemeral
Podman containers so the host filesystem is never touched beyond the
project root.

## User Stories

- **As an OpenCode agent**, I want a quick project overview with
  per-file skeletons, so I can understand a new codebase without
  reading every file.
- **As an OpenCode agent**, I want to find all definitions of a
  function across the project, so I don't search files one by one.
- **As an OpenCode agent**, I want to find every reference to an
  identifier before refactoring, without false positives from strings
  and comments that `grep` would match.
- **As an OpenCode agent**, I want to list files in a directory with
  a glob, skipping noise dirs (.venv, node_modules, etc.),
  automatically.
- **As an OpenCode agent**, I want to read a specific function from a
  Python file without loading the whole file, so I save context tokens
  for actual reasoning.
- **As an OpenCode agent**, I want to ask the local LLM for a security
  review of a specific chunk, so I don't have to send proprietary code
  to the cloud.
- **As an OpenCode agent**, I want to run my test suite in an isolated
  sandbox, so a flaky test or runaway command can't corrupt the host.
- **As an OpenCode agent**, I want a code→test→fix loop that retries
  failures automatically up to a hard cap, so I don't get stuck
  babysitting 5-round patch cycles.
- **As a developer**, I want to point this server at OpenRouter / OpenAI
  / ollama with one config change, so I'm not locked to one LLM vendor.
- **As a developer**, I want my MCP server to self-shutdown after an
  hour of idle, so it doesn't leak resources.

## Acceptance Criteria

- [x] 13 MCP tools registered and reachable over stdio (FastMCP)
- [x] tree-sitter extraction works for `.py`, `.js`, `.ts`, `.tsx`
- [x] Project-wide codebase awareness tools return correct results
      from real project files, with mtime cache invalidation
- [x] Skip-dir filter excludes `.venv`, `.git`, `node_modules`, etc.
- [x] Match cap (200) enforced for `search_symbol` and `find_references`
- [x] LM Studio integration responds on `http://localhost:1234/v1`
- [x] LLM brain integration works with at least OpenRouter + Claude Haiku
- [x] Podman sandbox executes commands and respects mount allow-list
- [x] Autonomous loop enforces a 5-iteration circuit breaker and applies
      M3 patches to disk between iterations
- [x] 59-test pytest suite passes inside the sandbox
- [x] Server self-shuts down after 1 hour idle
- [x] `BLOCKED.md` written on circuit-breaker trip
- [x] `.gitignore` covers `.env`, `venv/`, `__pycache__/`, `.opencode/`,
      `BLOCKED.md`, `.pytest_cache/`
- [x] Documentation: README + docs/ + AGENTS.md + CHANGELOG + LICENSE +
      SDD (product/tech/plan.md)

## Non-Functional Requirements

- **Performance**: AST extraction is in-process (<10 ms per file).
  Project-wide search is ~50–200 ms on a 50-file project on first call,
  <10 ms per changed file on subsequent calls (mtime cache). LLM calls
  dominate latency (Qwen: ~1 s/req; OpenRouter: ~3 s/req).
- **Security**: Sandbox mount validation refuses `/`, `/Users`, `/home`,
  `/root`, `/var`, `/etc`, `/tmp`, and any user home directory root.
  Skip-dir filter prevents the codebase-awareness tools from walking
  into virtualenvs, `.git`, or `node_modules`. No secrets in logs.
- **Compatibility**: Python 3.11+ (tested on 3.13); Podman 5.x; Linux
  runners for CI; macOS + Linux for local dev.
- **Portability**: No host-specific paths in source; all paths resolve
  relative to `Path(__file__).parent` (the project root). The LLM
  brain works with any OpenAI-compatible endpoint.
- **Observability**: Token usage logged to stderr by every brain call.
  Iteration logs kept in `LoopResult`. Patches audited to
  `.opencode/patches/`. Mtime cache size checkable via internal counter.

## Out of Scope

- Multi-language AST extraction beyond Python/JavaScript/TypeScript
  (tracked as v0.3.0+ candidate)
- Persistent storage across server restarts (the server is stateless;
  mtime cache evaporates on 1-hour idle shutdown)
- HTTP transport (stdio only — fits OpenCode's local MCP model)
- Auth/auth (the server is local-only; relies on filesystem perms)
- Auto-discovery of LM Studio models (operator must `lms load` first)
- File-watcher or background index rebuild (mtime check on every call
  gives O(1) invalidation without a watcher thread)
