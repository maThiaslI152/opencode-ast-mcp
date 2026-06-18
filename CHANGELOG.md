# Changelog

All notable changes to `opencode-ast-mcp` are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-06-18

### Added
- **4 new codebase-awareness MCP tools** (9 → 13 total):
  - `list_files(pattern)` — glob with skip-dir filtering (recursive)
  - `get_project_overview(depth)` — top-level project map with per-file
    skeletons
  - `search_symbol(name, language)` — find functions/classes/methods by
    name across the project; recurses into JS/TS `export_statement` and
    `lexical_declaration` wrappers
  - `find_references(name, filepath)` — AST-aware identifier reference
    search; skips string literals and comments (which `grep` would match)
- **New `codebase_index.py` module** (~280 lines): the `CodebaseIndex`
  class owns an mtime-keyed parsed-AST cache (1000 entries, FIFO
  eviction) shared across all 4 tools. No background threads, no
  file-watcher — every call revalidates the cache in O(1).
- **3 new static methods on `ASTExtractor`**:
  - `build_skeleton_from_tree(tree, source, ext)` — builds the skeleton
    string from a pre-parsed tree, enabling cache reuse
  - `find_named_nodes(tree, source, ext, name)` — top-level function /
    class / method name search
  - `find_identifier_references(tree, source, ext, name)` — walks
    identifier nodes, returns line + context, skips string literals
- **19 new tests** in `tests/test_codebase_index.py`:
  - `TestListFiles` (4): recursive default, non-recursive pattern,
    skip-dir filtering, no-match
  - `TestProjectOverview` (4): depth=1, depth=2, mixed languages,
    non-source-file exclusion
  - `TestSearchSymbol` (4): find function, find class, language filter,
    no-match
  - `TestFindReferences` (4): single-file scope, project-wide, ignores
    string literals, no-match
  - `TestMtimeCache` (3): cache hit, invalidation after mtime change,
    growth across distinct files
- **Skip-dir filter** (hardcoded set, used by all 4 new tools):
  `.venv`, `venv`, `__pycache__`, `.git`, `node_modules`,
  `.pytest_cache`, `.opencode`, `dist`, `build`, `.mypy_cache`,
  `.ruff_cache`, `htmlcov`.
- **Match cap**: 200 per call for `search_symbol` and `find_references`
  (response includes `truncated: true` if hit).
- **`test_mcp_tools_registered` updated** from 9 → 13 in
  `tests/test_integration.py`.
- **CI workflow** updated to expect 13 tools.

### Verified
- **59/59 sandbox tests pass** (Linux).
- **57/59 host tests pass** (2 pre-existing macOS APFS path-resolution
  failures are unrelated to this change).
- All 4 new tools smoke-tested via MCP stdio JSON-RPC:
  - `search_symbol("execute_step")` → 1 match in `autonomous_loop.py:101`
  - `find_references("_apply_patch", filepath="autonomous_loop.py")` →
    2 references (definition + call site)
- MCP `tools/list` now returns 13 tool definitions.

### Scope
- Recursive into the project root.
- Skips the 12 hardcoded noise directories listed above.
- Source parsing limited to `.py`, `.js`, `.ts`, `.tsx` (the languages
  `ast_extractor` already supports). Adding Go/Rust/Java is a tracked
  v0.3.0+ candidate — see `sdd/plan.md` "Known follow-ups".

## [0.1.1] — 2026-06-18

### Added
- **Patch application between iterations** — `execute_autonomous_loop_tool`
  now actually applies M3's generated patches to the project tree
  between iterations. Closes the v0.1.0 known gap. The primary applier
  is `git apply` (cleanest error messages, handles fuzz, rejects
  path-escapes); `patch -p1` is the fallback if `git` isn't on `PATH`.
- **`ApplyResult` dataclass** in `autonomous_loop.py` carries the
  outcome of a patch-application attempt (`success`, `stderr`, `method`).
- **`_apply_patch` method** on `AutonomousLoop` runs the unified-diff
  applier with a 30-second timeout and a 5 MiB input cap.
- **Patch-apply-failure handling** — when a patch can't be applied
  (context mismatch, fuzz rejection, etc.), the loop records the
  failure, skips the test run (code state is unknown), and feeds the
  apply error back to M3 as the next context. The test runs again on
  the next iteration with a fresh patch from M3.
- **5 new tests** in `TestPatchApplication`:
  - `test_real_patch_is_applied` — verifies a valid unified diff
    actually modifies the file
  - `test_invalid_patch_is_rejected` — garbage patches leave the file
    untouched
  - `test_empty_patch_is_rejected`
  - `test_loop_records_patch_apply_failure` — verifies the loop's
    behavior when M3's patches don't apply
  - `test_loop_applies_initial_patch_before_first_test` — verifies the
    `initial_patch` parameter works end-to-end
- **`git` and `patch` installed in the sandbox image** —
  `sandbox/Containerfile` now installs both, so the autonomous loop
  can apply patches inside the container too (not just on the host).

### Fixed
- The v0.1.0 known gap where `execute_autonomous_loop_tool` generated
  M3 patches but never applied them, so the test always ran against
  unchanged code and the circuit breaker tripped after 5 broken
  iterations.

### Verified
- 38/40 host tests pass (2 pre-existing macOS-only failures unrelated
  to this change; see `tests/test_sandbox_runner.py`).
- **40/40 sandbox tests pass** (the 2 macOS-only failures don't apply
  on Linux).
- All other tools (`get_file_skeleton`, `get_node`, `get_ast_json`,
  `analyze_node`, `compress_log`, `execute_in_sandbox`,
  `generate_sdd`, `get_loop_status`) still work.

## [0.1.0] — 2026-06-18

### Added
- **9 MCP tools** registered in `server.py`:
  `get_file_skeleton`, `get_node`, `get_ast_json`, `analyze_node`,
  `compress_log`, `execute_in_sandbox`, `execute_autonomous_loop_tool`,
  `generate_sdd`, `get_loop_status`.
- **tree-sitter AST extractor** with graceful grammar fallback for
  Python, JavaScript, TypeScript, and TSX.
- **LM Studio client** (`lm_client.py`) — local Qwen 18B via HTTP, with
  `response_format: json_schema` enforcement for `analyze_node` and a
  3 s thermal cooldown (Gotcha C).
- **LLM brain client** (`m3_client.py`) — OpenAI-compatible, default
  config targets OpenRouter with `anthropic/claude-3.5-haiku`.
  Includes markdown-fence stripping fallback for SDD parsing.
- **Podman sandbox runner** (`sandbox_runner.py`) with workspace
  mount validation (Gotcha D) and `os.sync()` flush before runs
  (Gotcha A).
- **Autonomous loop** (`autonomous_loop.py`) — ReAct-style
  code → test → fix cycle with a 5-iteration circuit breaker (Gotcha B),
  `BLOCKED.md` writer, and `.opencode/patches/` audit trail.
- **35 pytest tests** across 4 modules — `test_ast_extractor`,
  `test_autonomous_loop`, `test_integration`, `test_sandbox_runner`.
- **1-hour idle monitor** in `server.py` — server self-exits after
  60 minutes of zero MCP traffic.
- **Documentation suite**:
  `README.md`, `docs/ARCHITECTURE.md`, `docs/TOOLS.md`, `docs/SETUP.md`,
  `docs/TROUBLESHOOTING.md`, `AGENTS.md`.
- **GitHub-readiness files**:
  `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`,
  `.github/workflows/test.yml` (CI), `.github/dependabot.yml`.
- **`sdd/{product,tech,plan}.md`** filled in with the project's own
  software design docs.
- **`CHANGELOG.md`** (this file).

### Fixed
- `generate_sdd` previously returned HTTP 500 because the `openai`
  Python package was missing from the venv. Fixed by running
  `pip install -r requirements.txt`.
- `generate_sdd` previously failed with 401 (`login fail`) when pointed
  at `api.minimax.io/v1` with an OpenRouter key. Fixed by switching
  the default `MINIMAX_BASE_URL` to `https://openrouter.ai/api/v1` and
  the default `MINIMAX_MODEL` to `anthropic/claude-3.5-haiku`.
- SDD response was being dumped into a single `plan` key because the
  model wrapped the JSON in a ```json fence. Fixed by strengthening
  the system prompt (explicit "no fence, no prose") and adding a
  `_strip_markdown_fence` fallback in `m3_client.py`.
- `.gitignore` was missing — risk of committing `.env` and `venv/`.
  Fixed by adding a standard Python gitignore plus project-specific
  entries (`BLOCKED.md`, `.opencode/`, `.pytest_cache/`).

### Verified
- All 9 MCP tools respond correctly via stdio JSON-RPC.
- 35/35 pytest tests pass both on the host and inside the Podman
  sandbox.
- `execute_in_sandbox("echo hello")` returns `exit_code=0` in ~1 s.
- `execute_autonomous_loop_tool("echo ok")` returns
  `success=true, iterations=1`.
- `generate_sdd("Add a /health endpoint")` returns a real
  product/tech/plan JSON via OpenRouter (HTTP 200, ~1k tokens).
- The MCP server starts cleanly and self-terminates after 1 h of idle.

### Known limitations
- All I/O is synchronous. No async/await. Fine for the current
  single-tool-call model.
- No HTTP transport — stdio only.
