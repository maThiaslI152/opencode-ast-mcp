# Contributing

Thanks for your interest in `opencode-ast-mcp`! PRs, issues, and
discussions are all welcome.

## Before you start

1. Read **[AGENTS.md](AGENTS.md)** — it documents the module
   boundaries, code style, safety constraints, and plan-mode
   etiquette that this project enforces. Both human and AI
   contributors are expected to follow it.
2. Skim **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** so you
   understand the four "Gotchas" (A: `os.sync()`, B: circuit breaker,
   C: thermal cooldown, D: mount validation). Don't weaken any of
   them.
3. Check existing issues to avoid duplicate work.

## Development setup

```bash
# Clone & install
git clone <repo-url> opencode-ast-mcp
cd opencode-ast-mcp
python3.13 -m venv venv
./venv/bin/pip install -r requirements.txt

# (Optional) Build the Podman sandbox image
podman machine start
podman compose -f sandbox/compose.yaml build

# (Optional) Configure secrets
cp .env.example .env
$EDITOR .env
```

## Running the tests

**Fast (host):**
```bash
./venv/bin/python -m pytest tests/ -v
```

**Matches CI (Podman):**
```bash
podman compose -f sandbox/compose.yaml run --rm opencode-sandbox \
  bash -c "cd /workspace && pip install -q -r requirements.txt && python -m pytest tests/ -v"
```

**Via the MCP tool itself** (the way OpenCode agents exercise it):
```
execute_in_sandbox(command="cd /workspace && pip install -q -r requirements.txt && python -m pytest tests/ -v")
```

All three paths must agree. The expected result is
`35 passed in ~0.5s` (host) or `~12s` (sandbox, including image boot).

## Adding a new MCP tool

1. Decide which module owns the logic — see the
   [module-boundaries table in AGENTS.md](AGENTS.md#module-boundaries-dont-cross-them-casually).
2. Add the function in that module with a Google-style docstring
   (Args / Returns / Raises).
3. Register it in `server.py` with `@mcp.tool()` and
   `@activity_tracker`. The tool will appear in `tools/list`
   automatically — no other wiring needed.
4. Add a smoke test to
   `tests/test_integration.py::test_mcp_tools_registered`.
5. Document it in **[docs/TOOLS.md](docs/TOOLS.md)** (params table,
   return shape, example call, gotchas).
6. Run the test suite — it must stay at 35+ passing.

## Adding a new language to the AST extractor

1. Add the tree-sitter grammar to `requirements.txt`.
2. Extend `_LANGUAGES` in `ast_extractor.py` with the new extension,
   `Language(...)`, and a config dict (mirror the `.js` entry).
3. Add a `TestExtractor<Lang>` class to
   `tests/test_ast_extractor.py`.
4. Update **[docs/TOOLS.md](docs/TOOLS.md)** to list the new
   supported extension.

## Pull request checklist

- [ ] Tests pass locally (`pytest tests/ -v`) AND in the sandbox.
- [ ] No new lint or type errors (no linter configured yet — see
      AGENTS.md "Lint / typecheck").
- [ ] Documentation updated if behaviour or APIs changed.
- [ ] No secrets committed (check `git status --ignored` for `.env`).
- [ ] No unrelated refactors mixed into the PR.
- [ ] Commit messages are imperative-mood ("Add foo", not "Added foo").

## Reporting bugs

Use GitHub Issues. Include:
- Output of `./venv/bin/python --version` and `podman --version`.
- The MCP tool call that failed (params redacted).
- The full stderr from the server (look in the OpenCode logs).
- What you expected vs. what happened.

## Reporting security issues

See **[SECURITY.md](SECURITY.md)**. **Do not** file public GitHub
issues for security vulnerabilities.

## Code of conduct

Be kind. Argue ideas, not people. Assume good faith. This project
follows the [Contributor Covenant](https://www.contributor-covenant.org/)
in spirit, if not in letter.
