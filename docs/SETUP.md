# Setup Guide

Step-by-step instructions to get the Opencode AST MCP server running.

---

## 1. System prerequisites

| Requirement | macOS | Linux |
|-------------|-------|-------|
| Python | 3.11+ (`brew install python@3.13`) | 3.11+ (`apt install python3.13`) |
| Podman | `brew install podman` | `apt install podman` |
| LM Studio | Download from [lmstudio.ai](https://lmstudio.ai) | Download from [lmstudio.ai](https://lmstudio.ai) |
| git | preinstalled | preinstalled |

> **Podman on macOS requires a VM.** After `brew install podman`, run
> `podman machine init` then `podman machine start`. The first start
> downloads a small Linux VM (~150MB).

---

## 2. Clone & install

```bash
git clone <repo-url> opencode-ast-mcp
cd opencode-ast-mcp

python3.13 -m venv venv
./venv/bin/pip install -r requirements.txt
```

If you forget the `venv` and use the system Python, the `mcp` server will
still run but you will need to install the deps globally. The venv keeps
things isolated.

---

## 3. Configure secrets

```bash
cp .env.example .env
$EDITOR .env
```

Set the following:

| Variable | Required for | How to get |
|----------|-------------|------------|
| `MINIMAX_API_KEY` | `generate_sdd`, autonomous-loop patching | Any OpenAI-compatible provider. Default config targets DeepSeek — sign up at https://platform.deepseek.com and create an API key (format: `sk-...`) |
| `MINIMAX_BASE_URL` | `generate_sdd` | Default: `https://api.deepseek.com`. Override for OpenRouter / OpenAI / ollama |
| `MINIMAX_MODEL` | `generate_sdd` | Default: `deepseek-chat` (DeepSeek V3). See `§9` for options |
| `LM_STUDIO_BASE` | Qwen tools | Leave default (`http://localhost:1234/v1`) |
| `LM_STUDIO_MODEL` | Qwen tools | Whatever model identifier LM Studio assigns |
| `PODMAN_WORKSPACE` | Sandbox | Absolute path to this project |
| `PODMAN_IMAGE` | Sandbox | Leave default (`opencode-sandbox:latest`) |
| `SANDBOX_TIMEOUT` | Sandbox | Seconds before a sandbox command is killed (default 300) |
| `MAX_AUTONOMOUS_ITERATIONS` | Loop | Circuit-breaker cap (default 5) |
| `THERMAL_COOLDOWN_SECONDS` | Loop | Pause between Qwen calls (default 3.0) |

> **The `.env` file is gitignored.** Never commit it.

---

## 4. Build the Podman sandbox image

The sandbox is a `python:3.13-slim` image with `pytest` and `pytest-cov`
preinstalled. Build it once:

```bash
podman machine start                    # if not already running
podman compose -f sandbox/compose.yaml build
```

The build downloads ~100MB of base image + 30MB of pip packages. Expect
~1 minute on a fresh machine.

To verify the image exists:

```bash
podman images | grep opencode-sandbox
# expected: localhost/opencode-sandbox   latest   <image-id>   <date>
```

---

## 5. Load the LM Studio model

The server expects the model identified by `LM_STUDIO_MODEL` in `.env` to
be loaded and serving on `LM_STUDIO_BASE`. The `start.sh` script will try
to do this automatically if `lms` (the LM Studio CLI) is on your `PATH`:

```bash
# Either let start.sh do it:
./start.sh

# Or do it manually:
lms server start
lms load qwen3.5-18b-a3b-reap-coding-heretic-v0-i1 --gpu max -c 16384 --yes
```

Verify it's serving:

```bash
curl -s http://localhost:1234/v1/models | jq .
# expect an object with your model id in "data"
```

---

## 6. Register with OpenCode

Edit `~/.config/opencode/opencode.json` and add the `opencode-ast` server
under the `mcp` key (merge with any existing config):

```json
{
  "mcp": {
    "opencode-ast": {
      "type": "local",
      "command": ["/Users/tim/Works/opencode-ast-mcp/start.sh"]
    }
  }
}
```

Replace the absolute path with wherever you cloned the repo. The
`experimental.mcp_timeout` value in your config (default 120000 ms = 2
minutes) applies to all MCP calls — bump it if you plan to run long
autonomous-loop iterations.

Restart OpenCode (or reload its config) to pick up the new server. The
9 tools should appear in the tool list.

---

## 7. Smoke test

From the OpenCode UI, call each tool to confirm it works:

| Tool | What to call | Expected |
|------|--------------|----------|
| `get_file_skeleton` | `filepath: "server.py"` | Outline of 5 top-level defs |
| `get_node` | `filepath: "server.py"`, `node_name: "get_file_skeleton"` | Full source of the function |
| `get_ast_json` | `filepath: "config.py"` | JSON listing functions |
| `analyze_node` | `filepath: "dummy_auth.py"`, `node_name: "my_auth_logic"`, `question: "Any SQL injection?"` | Structured JSON analysis |
| `compress_log` | `error_log: "Traceback...long stack..."` | One or two sentences |
| `execute_in_sandbox` | `command: "echo hello"` | `exit_code: 0, stdout: "hello\n"` |
| `execute_autonomous_loop_tool` | `step_name: "smoke"`, `test_command: "echo ok"` | `success: true, iterations: 1` |
| `generate_sdd` | `feature_request: "Add /health endpoint"` | JSON with `product/tech/plan` |
| `get_loop_status` | (no args) | `{"status": "clear", ...}` |

If anything fails, see **[docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md)**.

---

## 8. Run the test suite

Verify the project itself works end-to-end via the sandbox:

```bash
# via MCP:
execute_in_sandbox(command="cd /workspace && pip install -q -r requirements.txt && python -m pytest tests/ -v")

# or directly with podman (skip the MCP layer):
podman compose -f sandbox/compose.yaml run --rm opencode-sandbox \
  bash -c "cd /workspace && pip install -q -r requirements.txt && python -m pytest tests/ -v"
```

Expected: `35 passed in ~0.5s`.

---

## 9. (Optional) Point at a different LLM provider

The brain client uses the OpenAI Python SDK, so it works with any
OpenAI-compatible endpoint. Change `MINIMAX_BASE_URL` and `MINIMAX_MODEL`
in `.env` (the env-var names keep the historical `MINIMAX_*` prefix for
backward compatibility):

```bash
# DeepSeek (default) — V3 is a strong generalist for SDD planning
MINIMAX_BASE_URL=https://api.deepseek.com
MINIMAX_MODEL=deepseek-chat   # or deepseek-reasoner (R1)
MINIMAX_API_KEY=sk-...

# OpenRouter — works with almost any model
MINIMAX_BASE_URL=https://openrouter.ai/api/v1
MINIMAX_MODEL=anthropic/claude-3.5-haiku
MINIMAX_API_KEY=sk-or-v1-...

# OpenAI
MINIMAX_BASE_URL=https://api.openai.com/v1
MINIMAX_MODEL=gpt-4o-mini
MINIMAX_API_KEY=sk-...

# Local ollama
MINIMAX_BASE_URL=http://localhost:11434/v1
MINIMAX_MODEL=qwen2.5-coder:32b
MINIMAX_API_KEY=ollama   # ollama ignores the value but the field must be non-empty
```

To discover the model IDs your provider supports:

- DeepSeek: see https://platform.deepseek.com/api-docs
- OpenRouter: `curl -H "Authorization: Bearer $MINIMAX_API_KEY" https://openrouter.ai/api/v1/models | jq '.data[].id'`
- OpenAI: https://platform.openai.com/docs/models
- ollama: `ollama list`

**Recommendation:** for SDD planning, use a model with at least 8B
parameters and good structured-output ability (`deepseek-chat`,
`claude-3.5-haiku`, `gpt-4o-mini`, `qwen2.5-coder-32b` all work well).
For patch generation, prefer code-specialised models
(`qwen/qwen3-coder`, `qwen-2.5-coder-32b-instruct`).
