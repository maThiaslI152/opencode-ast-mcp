"""Tests for the autonomous execution loop."""

from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from autonomous_loop import AutonomousLoop, LoopResult, IterationLog
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

        result = loop.execute_step(
            step_name="fix_and_pass",
            test_command="pytest tests/ -v",
            context="def login(): ...",
        )

        assert result.success is True
        assert result.iterations == 2
        assert result.blocked is False


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
