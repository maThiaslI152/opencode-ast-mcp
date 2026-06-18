# Execution Plan — opencode-ast-mcp

## Status: COMPLETED (v0.2.0)
<!-- PENDING_APPROVAL | APPROVED | IN_PROGRESS | COMPLETED | BLOCKED -->

## Steps

### Step 1: Establish baseline — all 13 tools verified
- **Description**: Stand up the server, register tools, smoke-test each.
- **Files**: `server.py`, `start.sh`, `config.py`
- **Test command**: 59-test `pytest tests/ -v` (in sandbox)
- **Status**: COMPLETED (59/59 passing on Linux; 57/59 on macOS due to
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
  builds `opencode-sandbox:latest` (python:3.13-slim + pytest + git + patch).
  Verified with `execute_in_sandbox("echo hello")` and full
  `pytest tests/ -v` via the sandbox.
- **Files**: `sandbox/Containerfile`, `sandbox/compose.yaml`
- **Test command**: full pytest suite inside container
- **Status**: COMPLETED (59/59 passing in sandbox, ~12 s end-to-end)

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
- **Test command**: workflow runs the 59-test suite in CI
- **Status**: COMPLETED

### Step 7: `git init` + initial commit
- **Description**: `git init -b main`, `git add .`, verify staged set
  with `git status --short`, `git commit -m "Initial commit"`.
- **Files**: `.git/`
- **Test command**: `git log --oneline`
- **Status**: COMPLETED

### Step 8: Close the patch-application gap (v0.1.1)
- **Description**: `execute_autonomous_loop_tool` was generating M3
  patches but never applying them. Added `ApplyResult` dataclass +
  `_apply_patch` method (git apply → patch -p1 fallback), modified the
  loop to apply patches between iterations, installed git+patch in the
  sandbox image.
- **Files**: `autonomous_loop.py`, `sandbox/Containerfile`,
  `tests/test_autonomous_loop.py`
- **Test command**: 40/40 sandbox; 38/40 host
- **Status**: COMPLETED

### Step 9: Add 4 codebase-awareness tools (v0.2.0)
- **Description**: `list_files`, `get_project_overview`, `search_symbol`,
  `find_references` — recursive, mtime-cached, tree-sitter-backed. New
  `codebase_index.py` module with shared cache. Three new static methods
  on `ASTExtractor` for skeleton-from-tree, named-node search, and
  identifier-reference search. 19 new tests.
- **Files**: `codebase_index.py`, `ast_extractor.py`, `server.py`,
  `tests/test_codebase_index.py`, `tests/test_integration.py`,
  `.github/workflows/test.yml`, `docs/TOOLS.md`, `README.md`,
  `AGENTS.md`, `CHANGELOG.md`
- **Test command**: 59/59 sandbox; 57/59 host; 4 tools smoke-tested via
  MCP stdio
- **Status**: COMPLETED

## Completion Criteria
<!-- What must be true for the entire plan to be considered done -->
- [x] All 13 MCP tools registered and verified
- [x] 59/59 pytest tests pass in sandbox
- [x] `generate_sdd` returns real product/tech/plan via OpenRouter
- [x] `execute_in_sandbox` runs commands and respects mount allow-list
- [x] `execute_autonomous_loop_tool` applies patches between iterations
- [x] 4 codebase-awareness tools return correct results from real project
      files, mtime cache validates correctly
- [x] Documentation suite (README + docs/ + AGENTS) covers all 13 tools
- [x] GitHub-readiness files (LICENSE / CONTRIBUTING / SECURITY /
      CHANGELOG / CI / Dependabot) in place
- [x] `.gitignore` covers secrets, caches, runtime artefacts
- [x] Git repo initialised on `main`, pushed to GitHub
- [x] No new security warnings, sandbox mount validation intact

## Known follow-ups (deferred to future versions)

- **Stream M3 responses** for `generate_sdd` so the user sees the
  product/tech/plan sections appear progressively.
- **Async I/O**: all I/O is currently synchronous. Worth converting if
  the agent ever wants to run multiple sandbox commands in parallel.
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
