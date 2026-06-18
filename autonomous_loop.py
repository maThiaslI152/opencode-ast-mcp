"""Autonomous execution loop — The Opencode Chamber.

Implements the ReAct (Reason → Act → Observe) loop for executing
plan steps autonomously. Each step runs through a code→test→fix
cycle inside the Podman sandbox with these safety guardrails:

- Gotcha B: Circuit breaker — max 5 iterations per step
- Gotcha C: Thermal cooldown between iterations
- BLOCKED.md creation on circuit breaker trip
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import (
    MAX_AUTONOMOUS_ITERATIONS,
    THERMAL_COOLDOWN_SECONDS,
    get_project_root,
)
from lm_client import LMStudioClient
from m3_client import M3Client
from sandbox_runner import SandboxRunner, SandboxResult


@dataclass
class IterationLog:
    """Record of a single iteration within the autonomous loop."""

    iteration: int
    timestamp: str
    test_passed: bool
    exit_code: int
    error_summary: str | None = None
    patch_applied: str | None = None
    duration_seconds: float = 0.0


@dataclass
class LoopResult:
    """Final result of an autonomous execution loop."""

    step_name: str
    success: bool
    iterations: int
    final_output: str
    error_summary: str | None = None
    iteration_logs: list[IterationLog] = field(default_factory=list)
    blocked: bool = False


class AutonomousLoop:
    """Execute plan steps autonomously with a code→test→fix cycle.

    Parameters
    ----------
    sandbox:
        A ``SandboxRunner`` instance for executing commands in Podman.
    lm_client:
        An ``LMStudioClient`` for compressing error logs locally.
    m3_client:
        An ``M3Client`` for generating patches (optional — if ``None``,
        the loop will report failures without auto-patching).
    max_iterations:
        Hard ceiling on consecutive fix attempts per step.
        Defaults to ``MAX_AUTONOMOUS_ITERATIONS`` from config.
    cooldown:
        Seconds to pause between iterations for thermal protection.
        Defaults to ``THERMAL_COOLDOWN_SECONDS`` from config.
    """

    def __init__(
        self,
        sandbox: SandboxRunner | None = None,
        lm_client: LMStudioClient | None = None,
        m3_client: M3Client | None = None,
        max_iterations: int | None = None,
        cooldown: float | None = None,
    ) -> None:
        self.sandbox = sandbox or SandboxRunner()
        self.lm_client = lm_client or LMStudioClient()
        self.m3_client = m3_client  # May be None if API key not set
        self.max_iterations = max_iterations or MAX_AUTONOMOUS_ITERATIONS
        self.cooldown = cooldown if cooldown is not None else THERMAL_COOLDOWN_SECONDS

    def execute_step(
        self,
        step_name: str,
        test_command: str,
        context: str = "",
        initial_patch: str | None = None,
    ) -> LoopResult:
        """Run the autonomous loop for a single plan step.

        Parameters
        ----------
        step_name:
            Human-readable name for logging and BLOCKED.md.
        test_command:
            Shell command to run inside the sandbox (e.g. ``pytest tests/ -v``).
        context:
            Code context for M3 to understand what to patch.
        initial_patch:
            Optional initial patch to apply before the first test.

        Returns
        -------
        LoopResult:
            The outcome including whether the step succeeded, iteration
            count, and the final output or error summary.
        """
        iteration_logs: list[IterationLog] = []
        previous_patch = initial_patch
        last_error_summary: str | None = None

        for i in range(1, self.max_iterations + 1):
            timestamp = datetime.now(timezone.utc).isoformat()

            # --- Apply patch if available ---
            if previous_patch and i > 1:
                # Write patch info for audit trail
                self._write_patch_log(step_name, i, previous_patch)

            # --- Execute test in sandbox ---
            sandbox_result: SandboxResult = self.sandbox.run(test_command)

            # --- Check result ---
            if sandbox_result.timed_out:
                log = IterationLog(
                    iteration=i,
                    timestamp=timestamp,
                    test_passed=False,
                    exit_code=-1,
                    error_summary="Test timed out",
                    duration_seconds=sandbox_result.duration_seconds,
                )
                iteration_logs.append(log)
                last_error_summary = "Test timed out"

            elif sandbox_result.exit_code == 0:
                # SUCCESS — test passed
                log = IterationLog(
                    iteration=i,
                    timestamp=timestamp,
                    test_passed=True,
                    exit_code=0,
                    duration_seconds=sandbox_result.duration_seconds,
                )
                iteration_logs.append(log)

                return LoopResult(
                    step_name=step_name,
                    success=True,
                    iterations=i,
                    final_output=sandbox_result.stdout,
                    iteration_logs=iteration_logs,
                )

            else:
                # FAILURE — compress error log and try to patch
                raw_error = sandbox_result.stderr or sandbox_result.stdout
                error_summary = self._compress_error(raw_error)
                last_error_summary = error_summary

                log = IterationLog(
                    iteration=i,
                    timestamp=timestamp,
                    test_passed=False,
                    exit_code=sandbox_result.exit_code,
                    error_summary=error_summary,
                    patch_applied=previous_patch,
                    duration_seconds=sandbox_result.duration_seconds,
                )
                iteration_logs.append(log)

                # --- Generate new patch via M3 (if available) ---
                if self.m3_client and i < self.max_iterations:
                    try:
                        new_patch = self.m3_client.generate_patch(
                            context=context,
                            error_summary=error_summary,
                            previous_patch=previous_patch,
                        )
                        previous_patch = new_patch
                    except Exception as e:
                        previous_patch = None
                        last_error_summary = f"M3 patch generation failed: {e}"

            # --- Thermal cooldown (Gotcha C) ---
            if i < self.max_iterations:
                time.sleep(self.cooldown)

        # --- CIRCUIT BREAKER TRIPPED (Gotcha B) ---
        self._write_blocked_md(step_name, iteration_logs, last_error_summary)

        return LoopResult(
            step_name=step_name,
            success=False,
            iterations=self.max_iterations,
            final_output="",
            error_summary=last_error_summary,
            iteration_logs=iteration_logs,
            blocked=True,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compress_error(self, raw_error: str) -> str:
        """Compress an error log to ≤2 sentences via the local model.

        Falls back to truncating the raw error if the LM is unavailable.
        """
        if len(raw_error) < 200:
            return raw_error  # Short enough already

        try:
            return self.lm_client.compress_error_log(raw_error)
        except Exception:
            # Fallback: first 500 chars
            return raw_error[:500] + "..." if len(raw_error) > 500 else raw_error

    def _write_blocked_md(
        self,
        step_name: str,
        logs: list[IterationLog],
        last_error: str | None,
    ) -> None:
        """Write a BLOCKED.md file when the circuit breaker trips.

        This file lives in the project root and explains what failed,
        what was tried, and what the user should investigate.
        """
        project_root = get_project_root()
        blocked_path = project_root / "BLOCKED.md"

        lines = [
            f"# 🚫 BLOCKED: {step_name}",
            "",
            f"**Circuit breaker tripped at {self.max_iterations} iterations.**",
            f"**Time**: {datetime.now(timezone.utc).isoformat()}",
            "",
            "## Last Error",
            "",
            f"```\n{last_error or 'No error captured'}\n```",
            "",
            "## Iteration History",
            "",
            "| # | Passed | Exit Code | Duration | Error Summary |",
            "|---|--------|-----------|----------|---------------|",
        ]

        for log in logs:
            passed = "✅" if log.test_passed else "❌"
            summary = (log.error_summary or "—")[:80]
            lines.append(
                f"| {log.iteration} | {passed} | {log.exit_code} | "
                f"{log.duration_seconds:.1f}s | {summary} |"
            )

        lines.extend(
            [
                "",
                "## What to Do",
                "",
                "1. Review the error above",
                "2. Check the test command and fix the underlying issue manually",
                "3. Delete this file when the issue is resolved",
                "4. Re-run the autonomous loop for this step",
            ]
        )

        blocked_path.write_text("\n".join(lines), encoding="utf-8")

    def _write_patch_log(self, step_name: str, iteration: int, patch: str) -> None:
        """Write a patch attempt to the patches log directory for auditing."""
        patches_dir = get_project_root() / ".opencode" / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)

        safe_name = step_name.replace(" ", "_").lower()
        patch_file = patches_dir / f"{safe_name}_iter{iteration}.patch"
        patch_file.write_text(patch, encoding="utf-8")


# ------------------------------------------------------------------
# Module-level convenience function
# ------------------------------------------------------------------


def execute_autonomous_loop(
    step_name: str,
    test_command: str,
    context: str = "",
    initial_patch: str | None = None,
    max_iterations: int | None = None,
    cooldown: float | None = None,
    m3_client: M3Client | None = None,
) -> LoopResult:
    """Create an ``AutonomousLoop`` and execute a single step.

    This is the primary entry point called by the MCP server's
    ``execute_autonomous_loop`` tool.
    """
    loop = AutonomousLoop(
        m3_client=m3_client,
        max_iterations=max_iterations,
        cooldown=cooldown,
    )
    return loop.execute_step(
        step_name=step_name,
        test_command=test_command,
        context=context,
        initial_patch=initial_patch,
    )


# --- Local Testing Block ---
if __name__ == "__main__":
    print("Autonomous Loop module loaded successfully.")
    print(f"Max iterations: {MAX_AUTONOMOUS_ITERATIONS}")
    print(f"Thermal cooldown: {THERMAL_COOLDOWN_SECONDS}s")

    # Quick test: run a simple command (won't work without Podman)
    try:
        result = execute_autonomous_loop(
            step_name="test_echo",
            test_command="echo 'hello from sandbox'",
        )
        print(f"Result: success={result.success}, iterations={result.iterations}")
        print(f"Output: {result.final_output}")
    except Exception as e:
        print(f"Expected error (Podman not running): {e}")
