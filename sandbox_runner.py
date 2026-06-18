"""Podman sandbox execution engine.

Runs commands inside ephemeral Podman containers with the project
workspace mounted as /workspace. Implements security guardrails:
- Gotcha A: os.sync() before execution to flush unsaved buffers
- Gotcha D: Only the project directory is mounted (not user home)
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from config import PODMAN_WORKSPACE, PODMAN_IMAGE, SANDBOX_TIMEOUT, get_project_root


@dataclass
class SandboxResult:
    """Result of a sandboxed command execution."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


# Directories that must never be mounted as the workspace volume.
# This prevents accidentally exposing the entire filesystem or user home.
_FORBIDDEN_MOUNTS: set[str] = {
    "/",
    "/Users",
    "/home",
    "/root",
    "/var",
    "/etc",
    "/tmp",
}


def _is_user_home_root(path: str) -> bool:
    """Return True if *path* is a user home directory root.

    Matches patterns like ``/Users/<name>`` or ``/home/<name>`` with no
    deeper components (i.e. exactly the home directory itself).
    """
    parts = Path(path).resolve().parts
    # macOS: /Users/<username>  -> ('/', 'Users', '<username>')
    # Linux: /home/<username>   -> ('/', 'home', '<username>')
    if len(parts) == 3 and parts[1] in ("Users", "home"):
        return True
    return False


def _validate_workspace(workspace: str) -> None:
    """Raise ValueError if *workspace* is a forbidden mount target.

    The volume mount MUST only mount the specific project workspace,
    never the user's home directory or a system root.
    """
    resolved = str(Path(workspace).resolve())

    if resolved in _FORBIDDEN_MOUNTS:
        raise ValueError(
            f"Refusing to mount '{resolved}' as the sandbox workspace. "
            "Only a specific project directory may be mounted."
        )

    if _is_user_home_root(resolved):
        raise ValueError(
            f"Refusing to mount user home directory '{resolved}' as the "
            "sandbox workspace. Mount a specific project directory instead."
        )


class SandboxRunner:
    """Execute commands inside ephemeral Podman containers.

    Parameters
    ----------
    workspace:
        Host directory to bind-mount at ``/workspace`` inside the
        container.  Defaults to ``PODMAN_WORKSPACE`` from the config
        module.
    image:
        Container image name/tag to use.  Defaults to ``PODMAN_IMAGE``
        from the config module.
    timeout:
        Maximum wall-clock seconds a container may run before being
        killed.  Defaults to ``SANDBOX_TIMEOUT`` from the config module.
    """

    def __init__(
        self,
        workspace: str | None = None,
        image: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.workspace: str = workspace or PODMAN_WORKSPACE
        self.image: str = image or PODMAN_IMAGE
        self.timeout: int = timeout or SANDBOX_TIMEOUT

        # Validate workspace before any execution can happen.
        _validate_workspace(self.workspace)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, command: str) -> SandboxResult:
        """Run *command* inside an ephemeral container.

        Steps:
        1. ``os.sync()`` — flush filesystem buffers (Gotcha A).
        2. Build the ``podman run`` command line.
        3. Execute via ``subprocess.run`` with timeout.
        4. Return a :class:`SandboxResult`.
        """
        # Step 1 — Gotcha A: flush unsaved buffers so the container
        # sees the most recent file state on the mounted volume.
        os.sync()

        # Step 2 — assemble the podman invocation.
        project_root = str(get_project_root())
        compose_file = os.path.join(project_root, "sandbox", "compose.yaml")

        env = os.environ.copy()
        env["PODMAN_WORKSPACE"] = self.workspace
        env["PODMAN_IMAGE"] = self.image

        podman_cmd: list[str] = [
            "podman",
            "compose",
            "-f",
            compose_file,
            "run",
            "--rm",
            "opencode-sandbox",
            "bash",
            "-c",
            command,
        ]

        start = time.monotonic()

        try:
            # Step 3 — execute with timeout.
            result = subprocess.run(
                podman_cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            duration = time.monotonic() - start

            return SandboxResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                timed_out=False,
                duration_seconds=round(duration, 3),
            )

        except subprocess.TimeoutExpired as exc:
            # Step 4 — timeout handling.
            duration = time.monotonic() - start
            return SandboxResult(
                exit_code=-1,
                stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
                stderr=exc.stderr or "" if isinstance(exc.stderr, str) else "",
                timed_out=True,
                duration_seconds=round(duration, 3),
            )

        except Exception as exc:
            # Step 5 — catch-all for unexpected errors.
            duration = time.monotonic() - start
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=f"Sandbox execution error: {exc}",
                timed_out=False,
                duration_seconds=round(duration, 3),
            )

    def build_image(self) -> bool:
        """Build the sandbox container image from ``sandbox/compose.yaml``.

        Runs ``podman compose build`` from the project root directory and returns
        ``True`` on success, ``False`` on failure.
        """
        project_root = str(get_project_root())
        compose_file = os.path.join(project_root, "sandbox", "compose.yaml")

        env = os.environ.copy()
        env["PODMAN_IMAGE"] = self.image
        env["PODMAN_WORKSPACE"] = self.workspace

        try:
            result = subprocess.run(
                [
                    "podman",
                    "compose",
                    "-f",
                    compose_file,
                    "build",
                ],
                cwd=project_root,
                env=env,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    def is_podman_available(self) -> bool:
        """Return ``True`` if Podman is installed and reachable."""
        try:
            result = subprocess.run(
                ["podman", "--version"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception:
            return False


# ------------------------------------------------------------------
# Module-level convenience function
# ------------------------------------------------------------------


def run_in_sandbox(command: str, **kwargs: object) -> SandboxResult:
    """Create a :class:`SandboxRunner` and execute *command*.

    Any keyword arguments are forwarded to the ``SandboxRunner``
    constructor (``workspace``, ``image``, ``timeout``).
    """
    runner = SandboxRunner(**kwargs)  # type: ignore[arg-type]
    return runner.run(command)
