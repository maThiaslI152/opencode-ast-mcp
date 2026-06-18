"""Tests for the autonomous execution loop."""

from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from autonomous_loop import AutonomousLoop, ApplyResult, LoopResult, IterationLog
from sandbox_runner import SandboxResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_sandbox_result(
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    duration: float = 0.5,
) -> SandboxResult:
    return SandboxResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        duration_seconds=duration,
    )


@pytest.fixture
def mock_sandbox():
    sandbox = MagicMock()
    sandbox.run.return_value = _make_sandbox_result(exit_code=0, stdout="OK")
    return sandbox


@pytest.fixture
def mock_lm_client():
    client = MagicMock()
    client.compress_error_log.return_value = "Error in auth.py:42: TypeError"
    return client


@pytest.fixture
def mock_m3_client():
    client = MagicMock()
    client.generate_patch.return_value = "--- a/auth.py\n+++ b/auth.py\n-old\n+new"
    return client


# ---------------------------------------------------------------------------
# Success on first try
# ---------------------------------------------------------------------------

class TestLoopSuccess:
    def test_passes_on_first_iteration(self, mock_sandbox, mock_lm_client):
        loop = AutonomousLoop(
            sandbox=mock_sandbox,
            lm_client=mock_lm_client,
            max_iterations=5,
            cooldown=0,  # No delay in tests
        )

        result = loop.execute_step(
            step_name="test_step",
            test_command="pytest tests/ -v",
        )

        assert result.success is True
        assert result.iterations == 1
        assert result.blocked is False
        assert len(result.iteration_logs) == 1
        assert result.iteration_logs[0].test_passed is True

    def test_passes_on_second_iteration(self, mock_lm_client, mock_m3_client):
        sandbox = MagicMock()
        sandbox.run.side_effect = [
            _make_sandbox_result(exit_code=1, stderr="AssertionError"),
            _make_sandbox_result(exit_code=0, stdout="2 passed"),
        ]

        loop = AutonomousLoop(
            sandbox=sandbox,
            lm_client=mock_lm_client,
            m3_client=mock_m3_client,
            max_iterations=5,
            cooldown=0,
        )

        # The mock M3 patch is not a valid unified diff, so we mock
        # _apply_patch to succeed. Patch-application itself is tested
        # in TestPatchApplication below with real unified-diff patches.
        with patch.object(
            loop,
            "_apply_patch",
            return_value=ApplyResult(success=True, stderr="", method="git"),
        ):
            result = loop.execute_step(
                step_name="fix_and_pass",
                test_command="pytest tests/ -v",
                context="def login(): ...",
            )

        assert result.success is True
        assert result.iterations == 2
        assert result.blocked is False
        # The patch that was applied on iteration 2 is recorded.
        assert result.iteration_logs[1].patch_applied is not None


# ---------------------------------------------------------------------------
# Circuit breaker (Gotcha B)
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_trips_after_max_iterations(self, mock_lm_client, mock_m3_client, tmp_path):
        sandbox = MagicMock()
        sandbox.run.return_value = _make_sandbox_result(
            exit_code=1, stderr="persistent error"
        )

        with patch("autonomous_loop.get_project_root", return_value=tmp_path):
            loop = AutonomousLoop(
                sandbox=sandbox,
                lm_client=mock_lm_client,
                m3_client=mock_m3_client,
                max_iterations=3,
                cooldown=0,
            )

            result = loop.execute_step(
                step_name="stuck_step",
                test_command="pytest tests/ -v",
                context="broken code",
            )

        assert result.success is False
        assert result.iterations == 3
        assert result.blocked is True
        assert result.error_summary is not None

        # Verify BLOCKED.md was created
        blocked_file = tmp_path / "BLOCKED.md"
        assert blocked_file.exists()
        content = blocked_file.read_text()
        assert "stuck_step" in content
        assert "Circuit breaker" in content

    def test_default_max_iterations_is_5(self, mock_sandbox, mock_lm_client):
        with patch("autonomous_loop.MAX_AUTONOMOUS_ITERATIONS", 5):
            loop = AutonomousLoop(
                sandbox=mock_sandbox,
                lm_client=mock_lm_client,
                cooldown=0,
            )
            assert loop.max_iterations == 5


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------

class TestTimeoutHandling:
    def test_timeout_counts_as_failure(self, mock_lm_client, tmp_path):
        sandbox = MagicMock()
        sandbox.run.return_value = _make_sandbox_result(timed_out=True)

        with patch("autonomous_loop.get_project_root", return_value=tmp_path):
            loop = AutonomousLoop(
                sandbox=sandbox,
                lm_client=mock_lm_client,
                max_iterations=1,
                cooldown=0,
            )

            result = loop.execute_step(
                step_name="timeout_step",
                test_command="sleep 999",
            )

        assert result.success is False
        assert result.blocked is True


# ---------------------------------------------------------------------------
# M3 unavailable graceful degradation
# ---------------------------------------------------------------------------

class TestNoM3Client:
    def test_runs_without_m3(self, mock_lm_client, tmp_path):
        """Loop should still run and report failures even without M3."""
        sandbox = MagicMock()
        sandbox.run.return_value = _make_sandbox_result(
            exit_code=1, stderr="error"
        )

        with patch("autonomous_loop.get_project_root", return_value=tmp_path):
            loop = AutonomousLoop(
                sandbox=sandbox,
                lm_client=mock_lm_client,
                m3_client=None,  # No M3
                max_iterations=2,
                cooldown=0,
            )

            result = loop.execute_step(
                step_name="no_m3_step",
                test_command="pytest tests/ -v",
            )

        assert result.success is False
        assert result.blocked is True
        assert result.iterations == 2


# ---------------------------------------------------------------------------
# Patch application (closes the v0.1.0 known-gap)
# ---------------------------------------------------------------------------

class TestPatchApplication:
    """Verify that M3-generated patches are actually applied to the project
    tree between iterations, with proper handling of apply failures.

    These tests use real files in a ``tmp_path`` and real ``git apply`` /
    ``patch -p1`` invocations — no mocks for the apply step.
    """

    def test_real_patch_is_applied(self, tmp_path):
        """A valid unified diff must modify the file on disk."""
        target = tmp_path / "victim.py"
        target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")

        patch_text = (
            "--- a/victim.py\n"
            "+++ b/victim.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def greet():\n"
            "-    return 'hello'\n"
            "+    return 'howdy'\n"
        )

        # Build a minimal git repo so git apply accepts the path.
        subprocess = __import__("subprocess")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "victim.py"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
            cwd=tmp_path,
            check=True,
        )

        with patch("autonomous_loop.get_project_root", return_value=tmp_path):
            loop = AutonomousLoop(
                sandbox=MagicMock(),
                lm_client=MagicMock(),
                m3_client=None,
                max_iterations=1,
                cooldown=0,
            )
            result = loop._apply_patch(patch_text)

        assert result.success is True, f"expected success, got: {result!r}"
        assert result.method in ("git", "patch")
        assert "howdy" in target.read_text(encoding="utf-8")

    def test_invalid_patch_is_rejected(self, tmp_path):
        """A patch with no hunk headers must be rejected, not silently applied."""
        target = tmp_path / "victim.py"
        target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
        original = target.read_text(encoding="utf-8")

        garbage_patch = "--- a/victim.py\n+++ b/victim.py\n-old\n+new"

        with patch("autonomous_loop.get_project_root", return_value=tmp_path):
            loop = AutonomousLoop(
                sandbox=MagicMock(),
                lm_client=MagicMock(),
                m3_client=None,
                max_iterations=1,
                cooldown=0,
            )
            result = loop._apply_patch(garbage_patch)

        assert result.success is False
        assert "patch" in result.stderr.lower() or "git" in result.stderr.lower()
        # File must be untouched.
        assert target.read_text(encoding="utf-8") == original

    def test_empty_patch_is_rejected(self, tmp_path):
        with patch("autonomous_loop.get_project_root", return_value=tmp_path):
            loop = AutonomousLoop(
                sandbox=MagicMock(),
                lm_client=MagicMock(),
                m3_client=None,
                max_iterations=1,
                cooldown=0,
            )
            result = loop._apply_patch("   \n\n  ")

        assert result.success is False
        assert "empty" in result.stderr.lower()

    def test_loop_records_patch_apply_failure(
        self, mock_lm_client, mock_m3_client, tmp_path
    ):
        """When the patch can't be applied, the loop records the failure
        and asks M3 for a new patch (verified via mock_m3_client call count)."""
        sandbox = MagicMock()
        sandbox.run.return_value = _make_sandbox_result(
            exit_code=1, stderr="AssertionError"
        )

        with patch("autonomous_loop.get_project_root", return_value=tmp_path):
            loop = AutonomousLoop(
                sandbox=sandbox,
                lm_client=mock_lm_client,
                m3_client=mock_m3_client,
                max_iterations=3,
                cooldown=0,
            )
            result = loop.execute_step(
                step_name="bad_patch",
                test_command="pytest tests/ -v",
                context="some context",
            )

        # Test ran only on iter 1; iters 2 and 3 are patch-apply failures
        # (we skip the test run after an apply failure since code state
        # is unknown).
        assert sandbox.run.call_count == 1
        assert result.success is False
        assert result.blocked is True
        # 3 iteration logs: 1 test failure + 2 patch-apply failures.
        assert len(result.iteration_logs) == 3
        assert "Patch application failed" in result.iteration_logs[1].error_summary
        assert "Patch application failed" in result.iteration_logs[2].error_summary
        # M3 was called after iter 1 (test fail) and iter 2 (apply fail);
        # iter 3 is the last so no third call.
        assert mock_m3_client.generate_patch.call_count == 2

    def test_loop_applies_initial_patch_before_first_test(
        self, mock_lm_client, tmp_path
    ):
        """An initial_patch passed to execute_step must be applied on iter 1
        BEFORE the first sandbox test run."""
        # Create a real file and a real patch
        target = tmp_path / "feature.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")

        subprocess = __import__("subprocess")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "add", "feature.py"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
            cwd=tmp_path,
            check=True,
        )

        real_patch = (
            "--- a/feature.py\n"
            "+++ b/feature.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 42\n"
        )

        sandbox = MagicMock()
        sandbox.run.return_value = _make_sandbox_result(exit_code=0, stdout="ok")

        with patch("autonomous_loop.get_project_root", return_value=tmp_path):
            loop = AutonomousLoop(
                sandbox=sandbox,
                lm_client=mock_lm_client,
                m3_client=None,
                max_iterations=1,
                cooldown=0,
            )
            result = loop.execute_step(
                step_name="with_initial",
                test_command="pytest tests/ -v",
                initial_patch=real_patch,
            )

        # File was actually changed by the initial patch.
        assert "VALUE = 42" in target.read_text(encoding="utf-8")
        # And the test ran successfully on the patched code.
        assert result.success is True
        assert result.iterations == 1
        assert result.iteration_logs[0].patch_applied == real_patch
