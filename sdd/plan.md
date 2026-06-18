# Execution Plan — opencode-ast-mcp

## Status: COMPLETED (v0.1.1)
<!-- PENDING_APPROVAL | APPROVED | IN_PROGRESS | COMPLETED | BLOCKED -->

## Steps

### Step 1: Establish baseline — all 9 tools verified
- **Description**: Stand up the server, register tools, smoke-test each.
- **Files**: `server.py`, `start.sh`, `config.py`
- **Test command**: 40-test `pytest tests/ -v` (in sandbox)
- **Status**: COMPLETED (40/40 passing on Linux; 38/40 on macOS due to
  pre-existing APFS path-resolution issue unrelated to this work)

### Step 2: Repair missing `openai` module + create `.env`
- **Description**: `pip install -r requirements.txt` (adds openai);
  create `.env` from `.env.example`.
- **Files**: `requirements.txt`, `.env`, `.env.example`, `.gitignore`
- **Test command**: `./venv/bin/python -c "from openai import OpenAI; from m3_client import M3Client"`
- **Status**: COMPLETED (openai 2.43.0 installed, .env present, gitignored)

### Step 3: Switch brain to OpenRouter + Claude Haiku
- **Description**: User provided an OpenRouter key (`sk-or-v1-…`); the
  default config pointed at `api.minimax.io` and a non-existent model.
  Updated env defaults + `.env` + `.env.example` to OpenRouter +
  `anthropic/claude-3.5-haiku`. Strengthened SDD system prompt and
  added markdown-fence stripping in `m3_client.py`.
- **Files**: `.env`, `.env.example`, `m3_client.py`, `config.py`
- **Test command**: end-to-end `generate_sdd` via stdio JSON-RPC
- **Status**: COMPLETED (200 OK, 1082 tokens, real product/tech/plan JSON)

### Step 4: Build sandbox image + verify `execute_in_sandbox`
- **Description**: `podman compose -f sandbox/compose.yaml build`
  builds `opencode-sandbox:latest` (python:3.13-slim + pytest).
  Verified with `execute_in_sandbox("echo hello")` and full
  `pytest tests/ -v` via the sandbox.
- **Files**: `sandbox/Containerfile`, `sandbox/compose.yaml`
- **Test command**: full pytest suite inside container
- **Status**: COMPLETED (35/35 passing in sandbox, ~12 s end-to-end)

### Step 5: Author documentation suite
- **Description**: README, docs/{ARCHITECTURE,TOOLS,SETUP,TROUBLESHOOTING}.md,
  AGENTS.md. Both-audience tone, ASCII diagrams only.
- **Files**: `README.md`, `docs/*.md`, `AGENTS.md`
- **Test command**: visual review
- **Status**: COMPLETED

### Step 6: Add GitHub-readiness files
- **Description**: LICENSE (MIT), CONTRIBUTING.md, SECURITY.md,
  CHANGELOG.md, .github/workflows/test.yml, .github/dependabot.yml.
- **Files**: see above
- **Test command**: workflow runs the 35-test suite in CI
- **Status**: COMPLETED

### Step 7: `git init` + initial commit
- **Description**: `git init -b main`, `git add .`, verify staged set
  with `git status --short`, `git commit -m "Initial commit"`.
- **Files**: `.git/`
- **Test command**: `git log --oneline`
- **Status**: COMPLETED

## Completion Criteria
<!-- What must be true for the entire plan to be considered done -->
- [x] All 9 MCP tools registered and verified
- [x] 35/35 pytest tests pass in sandbox
- [x] `generate_sdd` returns real product/tech/plan via OpenRouter
- [x] `execute_in_sandbox` runs commands and respects mount allow-list
- [x] `execute_autonomous_loop_tool` runs the test + report cycle
- [x] Documentation suite (README + docs/ + AGENTS) covers all 9 tools
- [x] GitHub-readiness files (LICENSE / CONTRIBUTING / SECURITY /
      CHANGELOG / CI / Dependabot) in place
- [x] `.gitignore` covers secrets, caches, runtime artefacts
- [x] Git repo initialised on `main`, initial commit landed
- [x] No new security warnings, sandbox mount validation intact

## Known follow-ups (deferred to future versions)

- **Stream M3 responses** for `generate_sdd` so the user sees the
  product/tech/plan sections appear progressively.
- **Async I/O**: all I/O is currently synchronous. Worth converting if
  the agent ever wants to run multiple sandbox commands in parallel.
- **v0.2.0 "Codebase Awareness" tools** (`list_files`,
  `get_project_overview`, `search_symbol`, `find_references`) — the
  plan is approved and locked, awaiting a "go" to start implementation.
- **v0.3.0+ language expansion** — the AST extractor is designed for
  incremental language additions (~5 lines + one pip dep per language,
  see `AGENTS.md` → "Adding a new language to the AST extractor").
  Candidate languages in priority order:
  1. **Go** — `tree-sitter-go`, common in cloud/infra projects
  2. **Rust** — `tree-sitter-rust`, growing in agentic-coding workloads
  3. **Java** — `tree-sitter-java`, ubiquitous in enterprise
  4. **C / C++** — `tree-sitter-c` / `tree-sitter-cpp`, systems work
  5. **Ruby / PHP** — `tree-sitter-ruby` / `tree-sitter-php`, web backends

  Each addition is self-contained: add the pip dep, extend
  `_LANGUAGES`, add a `TestExtractor<Lang>` class, list the new
  extension in `docs/TOOLS.md`. No changes to the rest of the system.
