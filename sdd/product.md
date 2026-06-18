# Product Specification — opencode-ast-mcp

## Feature Overview

A Model Context Protocol (MCP) server that exposes 9 specialised tools
to AI coding agents (OpenCode, Claude Desktop, etc.) so they can plan,
analyse, edit, and verify code without burning context on boilerplate.

The server is a **local-first** assistant: AST extraction is pure
Python, code analysis runs against a local Qwen 18B via LM Studio, and
only the optional cloud-side planning step (SDD generation, patch
proposals) talks to an external LLM. Code execution happens inside
ephemeral Podman containers so the host filesystem is never touched
beyond the project root.

## User Stories

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

- [x] 9 MCP tools registered and reachable over stdio (FastMCP)
- [x] tree-sitter extraction works for `.py`, `.js`, `.ts`, `.tsx`
- [x] LM Studio integration responds on `http://localhost:1234/v1`
- [x] LLM brain integration works with at least OpenRouter + Claude Haiku
- [x] Podman sandbox executes commands and respects mount allow-list
- [x] Autonomous loop enforces a 5-iteration circuit breaker
- [x] 35-test pytest suite passes inside the sandbox
- [x] Server self-shuts down after 1 hour idle
- [x] `BLOCKED.md` written on circuit-breaker trip
- [x] `.gitignore` covers `.env`, `venv/`, `__pycache__/`, `.opencode/`,
      `BLOCKED.md`, `.pytest_cache/`
- [x] Documentation: README + docs/ + AGENTS.md + CHANGELOG + LICENSE

## Non-Functional Requirements

- **Performance**: AST extraction is in-process (<10 ms per file).
  LLM calls dominate latency (Qwen: ~1 s/req; OpenRouter: ~3 s/req).
- **Security**: Sandbox mount validation refuses `/`, `/Users`, `/home`,
  `/root`, `/var`, `/etc`, `/tmp`, and any user home directory root.
- **Compatibility**: Python 3.11+ (tested on 3.13); Podman 5.x; Linux
  runners for CI; macOS + Linux for local dev.
- **Portability**: No host-specific paths in source; all paths resolve
  relative to `Path(__file__).parent` (the project root).
- **Observability**: Token usage logged to stderr by every M3 call.
  Iteration logs kept in `LoopResult`. Patches audited to
  `.opencode/patches/`.

## Out of Scope

- Patch application to disk (currently logged only — known gap)
- Multi-language AST extraction beyond Python/JavaScript/TypeScript
- Persistent storage (BLOCKED.md aside, the server is stateless)
- HTTP transport (stdio only — fits OpenCode's local MCP model)
- Auth/auth (the server is local-only; relies on filesystem perms)
- Auto-discovery of LM Studio models (operator must `lms load` first)
