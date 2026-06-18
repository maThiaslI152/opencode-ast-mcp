# Architecture

## High-level system

```
                    ┌──────────────────────────────────────────────┐
                    │     OpenCode IDE / MCP-aware client          │
                    │  (or any stdio-JSON-RPC MCP client)          │
                    └─────────────────────┬────────────────────────┘
                                          │ MCP protocol (stdio)
                                          ▼
                    ┌──────────────────────────────────────────────┐
                    │              server.py                       │
                    │   FastMCP("opencode-ast")                    │
                    │                                              │
                    │   • 9 @mcp.tool() registrations              │
                    │   • 1h idle monitor thread (auto-shutdown)   │
                    │   • @activity_tracker decorator on every tool│
                    │   • Lazy singletons for extractor/lm/sandbox │
                    └────┬──────────────┬───────────────┬─────────┘
                         │              │               │
                ┌────────▼────────┐ ┌───▼────────┐ ┌────▼──────────────┐
                │  ast_extractor  │ │ lm_client  │ │  sandbox_runner   │
                │  (tree-sitter)  │ │ (Qwen 18B) │ │  (Podman)         │
                │                 │ │ via LM     │ │  python:3.13-slim │
                │  • .py          │ │ Studio     │ │  + pytest         │
                │  • .js          │ │ local HTTP │ │  + pytest-cov     │
                │  • .ts / .tsx   │ └─────┬──────┘ └────────┬──────────┘
                │                 │       │                 │
                │  PURE PYTHON    │       │                 │ on test FAIL
                │  NO NETWORK     │       │                 │ in autonomous
                │  NO DISK WRITE  │       │                 │ loop only:
                └─────────────────┘       │                 ▼
                                          │       ┌──────────────────┐
                                          │       │  m3_client       │
                                          │       │  (LLM Brain)     │
                                          │       │  OpenAI-compat,  │
                                          │       │  e.g. DeepSeek   │
                                          │       │  + deepseek-v4-pro│
                                          │       └────────┬─────────┘
                                          │                │
                                          ▼                ▼
                                    ┌────────────────────────────┐
                                    │  Compressed log (≤2 sent.) │
                                    │  + M3-generated patch      │
                                    │  → applied to /workspace   │
                                    │  → retested in sandbox     │
                                    └────────────────────────────┘
```

## Request lifecycle

For a single tool call (e.g. `get_node`):

1. **OpenCode** sends a JSON-RPC `tools/call` message over stdio.
2. **start.sh** (if not already running) launches LM Studio if `lms` is on
   PATH, loads the configured model, then `exec`s `server.py`.
3. **`server.py`**'s FastMCP dispatches to the registered Python function.
4. The **activity tracker** bumps `_last_active_time` (resets the 1-hour
   idle-exit timer).
5. The tool function:
   - Pulls a lazily-initialised singleton (extractor / LM client / sandbox).
   - Does its work.
   - Returns a string (or a JSON-encoded string for structured tools).
6. FastMCP wraps the result in a JSON-RPC response.
7. **OpenCode** shows the result in the conversation.

For the **autonomous loop** the lifecycle is longer:

```
loop iteration 1..MAX_AUTONOMOUS_ITERATIONS:
  1. Apply any pending patch from M3
  2. podman compose run --rm opencode-sandbox bash -c <test_command>
  3. Capture exit_code, stdout, stderr, duration
  4. If exit_code == 0 → SUCCESS, return LoopResult(success=True)
  5. If timed_out → log "Test timed out", continue
  6. Else (test failed):
       a. Compress stderr via Qwen (≤2 sentences)
       b. Send code context + compressed error to M3
       c. M3 returns a unified-diff patch
       d. Loop continues with the new patch
  7. Sleep THERMAL_COOLDOWN_SECONDS between iterations

After MAX iterations:
  → Write BLOCKED.md with full iteration history
  → Return LoopResult(success=False, blocked=True)
```

## Module responsibilities

| File | Lines | Responsibility |
|------|-------|----------------|
| `server.py` | ~370 | FastMCP server, tool registration, idle timer, activity tracker |
| `ast_extractor.py` | ~520 | tree-sitter parsing for 11 languages: .py / .js / .ts / .tsx / .go / .rs / .java / .c / .cpp / .rb / .php |
| `lm_client.py` | ~240 | LM Studio HTTP client (Qwen 18B) — analysis, log compression, AST→JSON |
| `m3_client.py` | ~250 | OpenAI-compatible client (DeepSeek by default) — SDD planning, patch generation |
| `sandbox_runner.py` | ~250 | Podman container execution with workspace validation |
| `autonomous_loop.py` | ~340 | ReAct loop, circuit breaker, BLOCKED.md writer |
| `config.py` | ~70 | Env-var loader via python-dotenv, shared constants |
| `start.sh` | ~40 | Boot script — start LM Studio, load model, exec server |

## Key design decisions (the "Gotchas")

These are the load-bearing constraints baked into the code:

### Gotcha A — `os.sync()` before sandbox execution
`sandbox_runner.py:124` calls `os.sync()` before every `podman run`. Without
this, file edits made by M3 patches in the previous iteration may not be
visible to the new container (host's page cache hasn't been flushed to the
underlying volume).

### Gotcha B — Circuit breaker
`autonomous_loop.py:121` hard-caps the loop at `MAX_AUTONOMOUS_ITERATIONS` (5
by default). If all 5 fail, `BLOCKED.md` is written to the project root and
the MCP response sets `blocked=true`. This prevents runaway loops when M3 is
generating patches that just make things worse.

### Gotcha C — Thermal cooldown
`lm_client.py:110,164,211` sleeps `THERMAL_COOLDOWN_SECONDS` (3.0) after
every successful Qwen call. The 18B model pegs the GPU; back-to-back calls
throttle it to 0 tokens/sec within seconds. The cooldown lets the thermals
settle and keeps inference throughput consistent.

### Gotcha D — Sandboxed mount validation
`sandbox_runner.py:33-56` refuses to mount any of:
- `/`, `/Users`, `/home`, `/root`, `/var`, `/etc`, `/tmp`
- A user home directory root (`/Users/<name>`, `/home/<name>`)

Only a specific project subdirectory may be mounted. This prevents an agent
prompt-injection from getting a shell to `podman run -v /:/host ...` and
trashing the host filesystem.

## Concurrency model

- **Single-threaded stdio loop.** FastMCP reads one JSON-RPC message at a time
  from stdin. Tools are not concurrent — calling `execute_autonomous_loop`
  blocks all other tool calls for up to 5 × (test time + M3 time + cooldown).
- **One idle-monitor daemon thread.** Runs `time.sleep(60)` in a loop, exits
  the process via `os._exit(0)` if 1 hour of inactivity elapses.
- **No async.** All I/O is synchronous (`requests.post`, `subprocess.run`).
  This is fine because the LLM calls dominate latency by 100×.

## Failure isolation

| Failure | Blast radius | Recovery |
|---------|--------------|----------|
| M3 returns 401/5xx | Patch generation skipped; loop still runs tests | Fix `.env`, restart server |
| LM Studio down | `analyze_node` returns `{"error": "Could not connect to LM Studio..."}` | `lms server start` |
| Podman machine not running | `execute_in_sandbox` returns exit_code=-1 with stderr | `podman machine start` |
| Tree-sitter grammar missing | `ast_extractor` raises on import; tool returns error | `pip install tree-sitter-<lang>` |
| Test command hangs | `subprocess.TimeoutExpired` after `SANDBOX_TIMEOUT` (300s) | Investigate the test, lower timeout |
| M3 generates destructive patch | Sandbox mounts only project, so worst case is project corruption | `git checkout .` to revert |

## Observability

- All token usage from M3 is logged to **stderr** by `m3_client.py:_log_usage`.
- All Qwen calls log to **stderr** in `[M3]` / `[LM Studio]` style messages.
- The autonomous loop writes per-iteration `IterationLog` entries into the
  `LoopResult` and a `BLOCKED.md` on circuit-breaker trip.
- Patch attempts are written to `.opencode/patches/<step>_iter<N>.patch` for
  post-mortem inspection.

## Extension points

- **Add a tool** — register in `server.py` with `@mcp.tool()` + a docstring.
  The tool will appear in `tools/list` automatically.
- **Add a language** — extend `_LANGUAGES` in `ast_extractor.py` with the
  tree-sitter grammar + node-type config. See `.js` for the pattern.
- **Add a sandbox image** — edit `sandbox/Containerfile` and rebuild.
- **Swap the brain** — point `m3_client.py` at any OpenAI-compatible endpoint
  (DeepSeek, OpenRouter, OpenAI, local ollama, etc.) by changing
  `MINIMAX_BASE_URL` and `MINIMAX_MODEL` in `.env`. The env-var names
  keep the historical `MINIMAX_*` prefix for backward compatibility.
  Defaults to DeepSeek V4 (`deepseek-v4-pro`).
