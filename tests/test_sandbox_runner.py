"""Tests for the Podman sandbox runner."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from sandbox_runner import SandboxRunner, SandboxResult, _validate_workspace


# ---------------------------------------------------------------------------
# Workspace validation (Gotcha D)
# ---------------------------------------------------------------------------

class TestWorkspaceValidation:
    def test_rejects_root(self):
        with pytest.raises(ValueError, match="Refusing"):
            _validate_workspace("/")

    def test_rejects_users(self):
        with pytest.raises(ValueError, match="Refusing"):
            _validate_workspace("/Users")

    def test_rejects_home(self):
        with pytest.raises(ValueError, match="Refusing"):
            _validate_workspace("/home")

    def test_rejects_user_home_root(self):
        with pytest.raises(ValueError, match="user home directory"):
            _validate_workspace("/Users/someuser")

    def test_rejects_linux_home_root(self):
        with pytest.raises(ValueError, match="user home directory"):
            _validate_workspace("/home/someuser")

    def test_accepts_project_directory(self):
        # Should not raise
        _validate_workspace("/Users/someuser/projects/myapp")

    def test_accepts_deep_path(self):
        _validate_workspace("/Users/someuser/Work/opencode-ast-mcp")


# ---------------------------------------------------------------------------
# SandboxRunner — mocked execution
# ---------------------------------------------------------------------------

class TestSandboxRunner:
    @patch("sandbox_runner.PODMAN_WORKSPACE", "/Users/test/projects/myapp")
    @patch("sandbox_runner.PODMAN_IMAGE", "test-image:latest")
    @patch("sandbox_runner.SANDBOX_TIMEOUT", 60)
    @patch("sandbox_runner.subprocess.run")
    @patch("sandbox_runner.os.sync")
    def test_successful_run(self, mock_sync, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="all tests passed",
            stderr="",
        )

        runner = SandboxRunner(workspace="/Users/test/projects/myapp")
        result = runner.run("pytest tests/ -v")

        # Verify os.sync was called (Gotcha A)
        mock_sync.assert_called_once()

        # Verify podman command structure
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "podman"
        assert cmd[1] == "compose"
        assert cmd[4] == "run"
        assert "--rm" in cmd
        assert "opencode-sandbox" in cmd

        # Verify result
        assert result.exit_code == 0
        assert result.stdout == "all tests passed"
        assert not result.timed_out

    @patch("sandbox_runner.PODMAN_WORKSPACE", "/Users/test/projects/myapp")
    @patch("sandbox_runner.subprocess.run")
    @patch("sandbox_runner.os.sync")
    def test_timeout_handling(self, mock_sync, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="podman run ...", timeout=60
        )

        runner = SandboxRunner(workspace="/Users/test/projects/myapp")
        result = runner.run("sleep 999")

        assert result.timed_out is True
        assert result.exit_code == -1

    @patch("sandbox_runner.PODMAN_WORKSPACE", "/Users/test/projects/myapp")
    @patch("sandbox_runner.subprocess.run")
    @patch("sandbox_runner.os.sync")
    def test_failed_test(self, mock_sync, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="FAILED test_auth.py::test_login - AssertionError",
        )

        runner = SandboxRunner(workspace="/Users/test/projects/myapp")
        result = runner.run("pytest tests/test_auth.py")

        assert result.exit_code == 1
        assert "FAILED" in result.stderr
        assert not result.timed_out


# ---------------------------------------------------------------------------
# SandboxRunner — Podman availability check
# ---------------------------------------------------------------------------

class TestPodmanAvailability:
    @patch("sandbox_runner.PODMAN_WORKSPACE", "/Users/test/projects/myapp")
    @patch("sandbox_runner.subprocess.run")
    def test_podman_available(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        runner = SandboxRunner(workspace="/Users/test/projects/myapp")
        assert runner.is_podman_available() is True

    @patch("sandbox_runner.PODMAN_WORKSPACE", "/Users/test/projects/myapp")
    @patch("sandbox_runner.subprocess.run")
    def test_podman_not_available(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        runner = SandboxRunner(workspace="/Users/test/projects/myapp")
        assert runner.is_podman_available() is False
