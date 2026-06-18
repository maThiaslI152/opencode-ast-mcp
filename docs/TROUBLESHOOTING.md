# Troubleshooting

Known issues and how to fix them. Grouped by tool family.

---

## Quick diagnosis

Run these in order. Stop at the first failure.

```bash
# 1. Server can be imported
./venv/bin/python -c "import server; print('OK')"

# 2. openai is installed (common cause of old generate_sdd 500)
./venv/bin/python -c "import openai; print('OK')"

# 3. .env exists and is readable (and key matches provider)
ls -la .env && grep '^MINIMAX_' .env | sed 's/=.*/=<set>/'

# 4. Podman machine is running
podman info | grep -E "host.os|machine"

# 5. Sandbox image exists
podman images | grep opencode-sandbox

# 6. LM Studio is serving
curl -fsS http://localhost:1234/v1/models | head -3

# 7. Server starts over stdio
( echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"x","version":"0"}}}'; sleep 0.3; echo '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'; sleep 0.5 ) | ./venv/bin/python server.py | tail -1
```

If step 7 returns a list of 9 tool names, the server is healthy. If it
returns nothing or an error, the problem is in steps 1–6.

---

## `generate_sdd` returns 500 / cryptic error

### Cause: missing `openai` module (legacy)
**Symptom:** `Error executing tool generate_sdd: ModuleNotFoundError: No module named 'openai'`
**Fix:** `./venv/bin/pip install -r requirements.txt` (this installs `openai>=1.30.0` per `requirements.txt:10`).
**Verify:** `./venv/bin/python -c "import openai; print(openai.__version__)"` should print a version.

### Cause: placeholder or missing API key
**Symptom:** `Error code: 401 - {'type': 'error', 'error': {'type': 'authorized_error', 'message': "login fail: ... (1004)"}}`
**Fix:** edit `.env` and set `MINIMAX_API_KEY=<your-real-key>`. The placeholder
`your-api-key-here` will be rejected by the API.
**Verify:** `./venv/bin/python -c "from m3_client import M3Client; M3Client()"` should not raise `ValueError`.

### Cause: server not restarted after editing `.env`
**Symptom:** API calls still fail with auth errors after fixing the key.
**Fix:** `start.sh:18` exports env vars only at server startup. Kill the
process: `pgrep -f "server.py" | xargs kill`, then trigger OpenCode to
re-spawn it (or run `./start.sh` manually).
**Verify:** the new server logs the model name to stderr on first M3 call:
`[M3] tokens  prompt=?  completion=?  total=?`.

### Cause: wrong provider for the key type
**Symptom:** `sk-or-...` keys are rejected by `https://api.deepseek.com`,
or DeepSeek keys are rejected by `https://openrouter.ai/api/v1`.
**Fix:** the key prefix tells you the provider:
- `sk-...` (new, short) → DeepSeek or OpenAI → set `MINIMAX_BASE_URL` to the matching endpoint
- `sk-or-v1-...` → OpenRouter → set `MINIMAX_BASE_URL=https://openrouter.ai/api/v1`
- `sk-...` (older/longer) → OpenAI → set `MINIMAX_BASE_URL=https://api.openai.com/v1`
**Verify:** `./venv/bin/python -c "import config; print(config.MINIMAX_API_KEY[:6], config.MINIMAX_BASE_URL)"`.

### Cause: model not available on the provider
**Symptom:** `Error code: 404 - The model 'deepseek-chat' does not exist` or similar.
**Fix:** list the provider's models and pick one that exists:
- DeepSeek: see https://platform.deepseek.com/api-docs (current: `deepseek-v4-pro`, `deepseek-v4-flash`; `deepseek-chat` / `deepseek-reasoner` deprecated 2026/07/24)
- OpenRouter: `curl -H "Authorization: Bearer $MINIMAX_API_KEY" https://openrouter.ai/api/v1/models | jq '.data[].id'`
- OpenAI: https://platform.openai.com/docs/models
- ollama: `ollama list`

### Cause: wrong base URL
**Symptom:** connection refused, DNS error, or 404 from a non-OpenRouter host.
**Fix:** check `MINIMAX_BASE_URL` in `.env`. Default is
`https://openrouter.ai/api/v1`. If using a different provider, see
[SETUP.md §9](SETUP.md#9-optional-point-at-a-different-llm-provider).

### Cause: response is dumped into the `plan` key only
**Symptom:** `{"product": "", "tech": "", "plan": "<full response>"}`.
**Fix:** the model wrapped its JSON in a markdown fence. The parser
strips single-level fences automatically (since the `m3_client` change
for OpenRouter), but if the model uses nested fences or wraps JSON
inside a string, the parser falls back to dumping it into `plan`. Try a
stronger model (`claude-3.5-haiku`, `gpt-4o-mini`) or a different
provider.

---

## `execute_in_sandbox` / `execute_autonomous_loop_tool` failures

### Cause: Podman machine not running
**Symptom:** `exit_code: -1`, `stderr: "connection refused"` or
`"Cannot connect to the Podman socket"`.
**Fix:** `podman machine start`.

### Cause: sandbox image not built
**Symptom:** `stderr: "image not known"` or `Error: image not found`.
**Fix:** `podman compose -f sandbox/compose.yaml build`.

### Cause: workspace mount refused
**Symptom:** `ValueError: Refusing to mount '/Users' as the sandbox workspace.`
**Fix:** the `PODMAN_WORKSPACE` in `.env` must point to a specific project
directory, never a user home or system root. See Gotcha D in
[ARCHITECTURE.md](ARCHITECTURE.md#gotcha-d--sandboxed-mount-validation).

### Cause: command hangs / times out
**Symptom:** `timed_out: true`, `duration_seconds` close to `SANDBOX_TIMEOUT`.
**Fix:** either increase `SANDBOX_TIMEOUT` in `.env` or fix the hanging
command. The container is killed by `subprocess.run`'s timeout.

### Cause: test command exits non-zero on first try
**Symptom:** `success: false`, `iterations: 5`, `blocked: true`,
`BLOCKED.md` appears in project root.
**This is by design** — the circuit breaker tripped because M3 couldn't
produce a passing patch in 5 tries. Read `BLOCKED.md` for the iteration
log, fix the underlying issue manually, delete `BLOCKED.md`, then retry.

---

## Qwen tools (`analyze_node`, `compress_log`)

### Cause: LM Studio not running
**Symptom:** `analyze_node` returns `{"error": "Could not connect to LM Studio. Is the local server running on port 1234?"}`.
`compress_log` returns `[LM Studio unavailable — raw log attached]`.
**Fix:** `lms server start` (or start LM Studio app and click the server
toggle). The `lms` CLI comes with the LM Studio app.

### Cause: model not loaded
**Symptom:** connection succeeds but `error: model "qwen3..." not found`.
**Fix:** `lms load qwen3.5-18b-a3b-reap-coding-heretic-v0-i1 --gpu max -c 16384 --yes`
(or whatever model id matches `LM_STUDIO_MODEL` in `.env`).

### Cause: model not in `tools` view of LM Studio
**Symptom:** `curl http://localhost:1234/v1/models` returns `{"data": []}`.
**Fix:** in LM Studio, click the "Developer" tab, load the model, and
verify it's listed at the top. The `lms load` command does this from the
CLI.

### Cause: GPU thermal throttling
**Symptom:** first call works, second call takes 10× longer.
**Fix:** this is expected. `THERMAL_COOLDOWN_SECONDS=3.0` (the default)
is already enforced after every Qwen call. If you see sustained slowdown,
bump the cooldown to `5.0` in `.env` and reload the server.

### Cause: `analyze_node` returns invalid JSON
**Symptom:** `{"error": "Model failed to return valid JSON. Raw output: ..."}`.
**Fix:** the Qwen model occasionally breaks the schema. The `response_format:
json_schema` constraint is enforced by the API, but some quantised builds
ignore it. Try a higher-precision quant or a different model.

---

## AST tools (`get_file_skeleton`, `get_node`, `get_ast_json`)

### Cause: unsupported file extension
**Symptom:** `Error: Unsupported file extension '.rs'. Supported: ['.py', '.js', '.ts', '.tsx', '.go', '.rs', '.java', '.c', '.cpp', '.rb', '.php']`.
**Fix:** the 11 supported languages are listed above. Adding a new one
is a ~5-line change — see `AGENTS.md` "Adding a new language to the
AST extractor".

### Cause: file not found
**Symptom:** `Error: File not found: ...` (from `get_file_skeleton`) or
`{"error": "File not found: ..."}` (from `get_node`).
**Fix:** use an absolute path, or a path relative to where `server.py`
is running (the project root, since start.sh `cd`s there).

### Cause: function not found
**Symptom:** `{"error": "Function 'foo' not found in ..."}`.
**Fix:** the function might be nested inside a class (try the class name
first to see the methods), or it might be a closure / lambda (not
extractable).

---

## General / server-level

### Cause: server keeps exiting
**Symptom:** every MCP call re-spawns the server, or it dies after an hour
of idle.
**This is by design.** `server.py:48` has a 1-hour idle monitor that calls
`os._exit(0)`. OpenCode re-spawns on the next call.

### Cause: `start.sh: export` line breaks on values with spaces
**Symptom:** env var appears empty in the spawned Python process.
**Fix:** `start.sh:18` does `export $(grep -v '^#' .env | xargs)`. If any
value contains spaces, it gets split. Don't put spaces in `.env` values,
or refactor to use `set -a; source .env; set +a` instead.

### Cause: Port 1234 already in use
**Symptom:** `lms server start` fails with `Address already in use`.
**Fix:** another process is on port 1234. Either kill it
(`lsof -i :1234`) or change `LM_STUDIO_BASE` in `.env` to a different
port and update LM Studio's port setting to match.

### Cause: `pip install` fails on Apple Silicon
**Symptom:** architecture errors when installing `tree-sitter-*` or `openai`.
**Fix:** ensure your Python is arm64 native (`python3.13 -c "import
platform; print(platform.machine())"` should print `arm64`). The `brew
install python@3.13` formula is arm64 on Apple Silicon.

---

## Still stuck?

1. Read the relevant section of [docs/ARCHITECTURE.md](ARCHITECTURE.md).
2. Run the test suite — it covers all the tools and surfaces most regressions.
3. Capture stderr from the server (OpenCode usually shows it; otherwise
   `start.sh` prints to its own stderr).
4. If you find a new failure mode, please add it to this document.
