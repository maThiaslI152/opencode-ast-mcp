# Opencode AST MCP Server

> Turn any MCP-aware IDE (OpenCode, Claude Desktop, etc.) into an agentic
> coding system backed by tree-sitter, a local Qwen 18B, OpenRouter
> (Claude Haiku by default), and Podman.

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.28-purple?logo=modelcontextprotocol)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 73 passing](https://img.shields.io/badge/tests-73%20passing-brightgreen)](tests/)
[![Sandbox: Podman](https://img.shields.io/badge/sandbox-podman-892CA0?logo=podman&logoColor=white)](sandbox/)

---

## Why this project

A drop-in MCP server that gives your coding agent **13 specialised tools** —
AST extraction without reading whole files, project-wide codebase awareness,
local code analysis via Qwen, cloud SDD planning via DeepSeek (or any
OpenAI-compatible endpoint), and isolated test execution in Podman — so the
agent spends its context window on *code*, not on boilerplate.

---

## Features

- **13 MCP tools** in a single Python server (FastMCP + stdio)
- **tree-sitter** AST extraction for Python, JavaScript, TypeScript, TSX, Go, Rust, Java, C/C++, Ruby, PHP, LaTeX
- **Project-wide codebase awareness** — recursive file listing, overview with skeletons, cross-file symbol search, AST-aware reference finder (mtime-cached)
- **Local LLM** analysis via LM Studio + Qwen 18B (no cloud for code review)
- **Cloud planning** via any OpenAI-compatible endpoint (default: DeepSeek, works with OpenRouter, OpenAI, ollama)
- **Podman sandbox** for test execution with hardened mount validation
- **Autonomous code→test→fix loop** with a 5-iteration circuit breaker
- **75 pytest tests**, all runnable in the sandbox

---

## Table of contents

- [Quick start](#quick-start)
- [Tools at a glance](#tools-at-a-glance)
- [How it works with OpenCode plan/build mode](#how-it-works-with-opencode-planbuild-mode)
- [Project status](#project-status)
- [Project layout](#project-layout)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgments](#acknowledgments)

---

## Quick start

```bash
# 1. Clone & enter
git clone <repo-url> opencode-ast-mcp
cd opencode-ast-mcp

# 2. Create a virtualenv & install
python3.13 -m venv venv
./venv/bin/pip install -r requirements.txt

# 3. Configure secrets
cp .env.example .env
# Edit .env — set MINIMAX_API_KEY to your OpenRouter/OpenAI key
$EDITOR .env

# 4. Build the Podman sandbox image (one time)
podman compose -f sandbox/compose.yaml build

# 5. (Optional) Start LM Studio and load the Qwen model
lms server start
lms load qwen3-vl-4b-instruct-c_abliterated-v2-mlx --gpu max -c 16384 --yes

# 6. Register the MCP server in OpenCode
#    Edit ~/.config/opencode/opencode.json and add:
#    "opencode-ast": {
#      "type": "local",
#      "command": ["/absolute/path/to/opencode-ast-mcp/start.sh"]
#    }
```

Full setup walkthrough (with all 9 env vars and Podman machine
init): **[docs/SETUP.md](docs/SETUP.md)**. Something broken?
**[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**.

---

## Tools at a glance

| # | Tool | Backing service | Purpose |
|---|------|-----------------|---------|
| 1 | `get_file_skeleton` | tree-sitter | Compact outline of a file's top-level structure |
| 2 | `get_node` | tree-sitter | Full source of a named function or class |
| 3 | `get_ast_json` | tree-sitter | Structured JSON of a file's nodes |
| 4 | `list_files` | tree-sitter | Glob with skip-dir filtering |
| 5 | `get_project_overview` | tree-sitter | Top-level project map with per-file skeletons |
| 6 | `search_symbol` | tree-sitter | Find functions/classes/methods by name across project |
| 7 | `find_references` | tree-sitter | AST-aware identifier reference search |
| 8 | `analyze_node` | LM Studio (Qwen) | Security / data-flow analysis of a code chunk |
| 9 | `compress_log` | LM Studio (Qwen) | Summarise a verbose error log to ≤2 sentences |
| 10 | `execute_in_sandbox` | Podman | Run a single shell command in a container |
| 11 | `execute_autonomous_loop_tool` | Podman + Qwen + OpenRouter | Code → test → fix loop with circuit breaker |
| 12 | `generate_sdd` | DeepSeek (default) | Generate product/tech/plan docs for a feature |
| 13 | `get_loop_status` | local FS | Read `BLOCKED.md` if the circuit breaker tripped |

Full per-tool reference (params, returns, gotchas, decision tree):
**[docs/TOOLS.md](docs/TOOLS.md)**

---

## How it works with OpenCode plan/build mode

OpenCode itself has a **plan mode** (read-only) and a **build mode** (full
write access). The MCP tools map onto those modes as follows:

| MCP tool | Side effects? | Plan mode | Build mode |
|----------|---------------|-----------|------------|
| `get_file_skeleton`, `get_node`, `get_ast_json` | None | ✅ | ✅ |
| `list_files`, `get_project_overview`, `search_symbol`, `find_references` | Read-only file scan + parse | ✅ | ✅ |
| `analyze_node` | LM Studio HTTP call | ✅ | ✅ |
| `compress_log` | LM Studio HTTP call | ✅ | ✅ |
| `get_loop_status` | Reads `BLOCKED.md` | ✅ | ✅ |
| **`generate_sdd`** | **One HTTPS call, no disk writes** | ✅ | ✅ |
| `execute_in_sandbox` | Podman container + workspace mount | ❌ | ✅ |
| `execute_autonomous_loop_tool` | Sandbox + writes `BLOCKED.md` / patch log | ❌ | ✅ |

`generate_sdd` is the bridge between the two modes: it runs entirely in
plan mode (no file writes), produces the SDD artifacts the user reviews,
and the user then flips to build mode for `execute_autonomous_loop_tool`
to walk through `plan.md` step by step. The 4 codebase-awareness tools
(`list_files`, `get_project_overview`, `search_symbol`, `find_references`)
are also read-only and can be used freely in plan mode to scope the
investigation. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full request
lifecycle and the four "Gotchas" (A: `os.sync()`, B: circuit breaker,
C: thermal cooldown, D: mount validation) that make the system safe.

---

## Project status

All 13 tools validated end-to-end as of `v0.2.0`:

| Tool | Status |
|------|--------|
| `get_file_skeleton` | ✅ working |
| `get_node` | ✅ working |
| `get_ast_json` | ✅ working |
| `list_files` | ✅ working (v0.2.0) |
| `get_project_overview` | ✅ working (v0.2.0) |
| `search_symbol` | ✅ working (v0.2.0) |
| `find_references` | ✅ working (v0.2.0) |
| `analyze_node` | ✅ working (requires LM Studio) |
| `compress_log` | ✅ working (requires LM Studio) |
| `execute_in_sandbox` | ✅ working (59/59 pytest tests verified) |
| `execute_autonomous_loop_tool` | ✅ working (test, patch, apply, retry — all wired up) |
| `generate_sdd` | ✅ working (DeepSeek — or any OpenAI-compatible provider) |
| `get_loop_status` | ✅ working |

As of `v0.1.1`, `execute_autonomous_loop_tool` actually applies M3's
generated patches between iterations via `git apply` (with `patch -p1`
as fallback). See [docs/TOOLS.md §7](docs/TOOLS.md#7-execute_autonomous_loop_tool)
for the full apply-failure flow.

---

## Project layout

```
opencode-ast-mcp/
├── server.py              # FastMCP entry point — registers 13 tools
├── start.sh               # Boot script: starts LM Studio, runs server.py
├── config.py              # Centralised env-var configuration
├── ast_extractor.py       # tree-sitter powered skeleton/JSON/extract
├── codebase_index.py      # Mtime-cached recursive project index (v0.2.0)
├── lm_client.py           # LM Studio HTTP client (Qwen 18B)
├── m3_client.py           # LLM brain client (OpenAI-compatible)
├── sandbox_runner.py      # Podman container execution + safety checks
├── autonomous_loop.py     # Code→test→fix loop with circuit breaker
├── dummy_auth.py          # Test fixture for the AST extractor
├── requirements.txt       # Python dependencies
├── LICENSE                # MIT
├── .env.example           # Template for .env
├── .github/
│   ├── workflows/test.yml # CI: 75 pytest in Podman on every push/PR
│   └── dependabot.yml     # Dependabot for pip
├── sandbox/
│   ├── Containerfile      # python:3.13-slim + pytest + git + patch
│   └── compose.yaml       # Podman compose for the sandbox
├── sdd/                   # Project's own SDD (product/tech/plan.md)
├── prompts/
│   └── system_prompt.md   # Brain orchestrator system prompt
├── tests/                 # pytest suite (75 tests)
├── docs/                  # ARCHITECTURE / TOOLS / SETUP / TROUBLESHOOTING
├── AGENTS.md              # Guidance for AI coding agents
├── CHANGELOG.md           # Release history
├── CONTRIBUTING.md        # How to contribute
├── SECURITY.md            # How to report security issues
└── venv/                  # Local virtualenv (gitignored)
```

---

## Documentation

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — component diagram, request lifecycle, Gotchas A–D
- **[docs/TOOLS.md](docs/TOOLS.md)** — full per-tool reference + decision tree
- **[docs/SETUP.md](docs/SETUP.md)** — 9-step setup with provider-swap matrix
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** — 6 grouped failure modes with fixes
- **[sdd/](sdd/)** — the project's own software design docs
- **[AGENTS.md](AGENTS.md)** — guidance for AI agents (OpenCode-using and contributor)
- **[prompts/system_prompt.md](prompts/system_prompt.md)** — the brain's own system prompt

---

## Contributing

PRs welcome. The dev loop is:

```bash
./venv/bin/pip install -r requirements.txt
./venv/bin/python -m pytest tests/ -v        # host, fast
# or
podman compose -f sandbox/compose.yaml run --rm opencode-sandbox \
  bash -c "cd /workspace && pip install -q -r requirements.txt && python -m pytest tests/ -v"
# sandbox, matches CI

# Add a tool → see AGENTS.md "Adding a new MCP tool"
# Add a language → see AGENTS.md "Adding a new language to the AST extractor"
```

Please read **[AGENTS.md](AGENTS.md)** before changing code — it
documents the module boundaries, safety constraints, and
plan-mode etiquette that all contributors (human or AI) are expected
to follow.

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for the full contributor
checklist and **[SECURITY.md](SECURITY.md)** for private disclosure.

---

## License

[MIT](LICENSE) — © 2026 Tim

---

## Acknowledgments

- [tree-sitter](https://tree-sitter.github.io/tree-sitter/) — the AST
  parser that makes deterministic code analysis possible
- [LM Studio](https://lmstudio.ai) + [Qwen 3 VL 4B](https://huggingface.co/Qwen)
  — local inference for per-function code review
- [Anthropic Claude](https://www.anthropic.com) via
  [OpenRouter](https://openrouter.ai) — cloud-side SDD planning
- [DeepSeek](https://platform.deepseek.com) — default brain provider (open-source, OpenAI-compatible, strong structured output)
- [Podman](https://podman.io) — rootless container isolation
- [FastMCP](https://github.com/jlowin/fastmcp) — the Python MCP server SDK
