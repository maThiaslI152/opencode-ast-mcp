#!/usr/bin/env bash

# Kiro-Opencode MCP Server Startup Script
# Attached to OpenCode IDE

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure Podman machine is running
if ! podman info >/dev/null 2>&1; then
    # Start the default podman machine quietly
    echo "Starting Podman machine..." >&2
    podman machine start >/dev/null 2>&1 || true
fi

# Load environment variables (to find LM_STUDIO_MODEL)
if [ -f "${PROJECT_DIR}/.env" ]; then
    # shellcheck disable=SC2046
    export $(grep -v '^#' "${PROJECT_DIR}/.env" | xargs)
fi

LM_MODEL=${LM_STUDIO_MODEL:-"qwen3.5-18b-a3b-reap-coding-heretic-v0-i1"}

# Start LM Studio Server and load the model (Correct config for AST analysis)
if command -v lms >/dev/null 2>&1; then
    echo "Starting LM Studio server..." >&2
    lms server start >&2
    
    if lms ps | grep -q "${LM_MODEL}"; then
        echo "Model ${LM_MODEL} is already loaded in memory." >&2
    else
        echo "Loading model: ${LM_MODEL} for AST tasks (16k context, max GPU)..." >&2
        lms load "${LM_MODEL}" --gpu max -c 16384 --yes >&2
    fi
else
    echo "Warning: 'lms' CLI not found. Make sure LM Studio is running manually." >&2
fi

# Execute the MCP server
# (The server itself has an internal 1-hour idle timeout to gracefully shutdown)
exec "${PROJECT_DIR}/venv/bin/python" "${PROJECT_DIR}/server.py"
