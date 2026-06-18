# Security

## Supported versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | ✅ (current)       |

Older versions are not maintained. Please upgrade.

## Reporting a vulnerability

**Please do not file a public GitHub issue for security vulnerabilities.**

Email security concerns to the maintainer privately. Include:

- A clear description of the issue and its impact.
- Reproduction steps (preferably a minimal MCP tool call that demonstrates
  the issue, with secrets redacted).
- The version of `opencode-ast-mcp` affected.
- Your assessment of severity (low / medium / high / critical).

You should expect:

- **Acknowledgement** within 72 hours of your report.
- **A status update** every 7 days until resolution.
- **Credit** in the fix's release notes if you want it (and a CVE
  reference if applicable).

## Disclosure timeline

This project follows a **90-day responsible-disclosure window**:

- Day 0 — you report privately.
- Days 1–89 — maintainer investigates, develops, and tests a fix.
- Day 90 — coordinated public disclosure (security advisory +
  patched release + CVE if applicable).

If a fix takes longer than 90 days, the maintainer will coordinate an
extension with you before any public disclosure.

## What counts as a security issue here

Things to report:
- Sandbox escape (a sandbox command that reads/writes outside the
  mounted project directory — this should never happen; see Gotcha D
  in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)).
- Prompt injection via SDD output that causes `execute_in_sandbox`
  to do something destructive.
- LM Studio or OpenRouter key leakage in logs or error messages.
- `.env` accidentally committed to the repo (check git history).
- MCP protocol issues (e.g. a tool that crashes the server in a way
  that leaks sensitive data in the crash trace).
- Dependency CVEs in `requirements.txt`.

Not security issues (use Issues for these):
- "The autonomous loop didn't apply my patch" — known limitation,
  see [docs/TOOLS.md §7](docs/TOOLS.md#7-execute_autonomous_loop_tool).
- "The MCP server returns 500" — first try
  [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Hardening notes (for operators)

- The sandbox mount validator refuses to mount `/`, `/Users`, `/home`,
  `/root`, `/var`, `/etc`, `/tmp`, or any user home directory root.
  See `sandbox_runner.py:_FORBIDDEN_MOUNTS`. **Do not weaken this
  list.**
- The autonomous loop caps itself at 5 iterations
  (`MAX_AUTONOMOUS_ITERATIONS=5` in `.env`). **Do not raise this
  above 10** in defaults — it's load-bearing for cost and runaway-loop
  protection.
- M3 responses are logged to **stderr only**, never to a file in the
  project. M3 output can contain code that looks like it belongs in
  source files; logging to disk creates false positives in code
  search.
- `.env` is in `.gitignore`. Verify with `git status --ignored` before
  every commit.
- The server self-exits after 1 hour of idle (`server.py:_idle_monitor`).
  Don't remove this — it's intentional defence against zombie
  processes.
