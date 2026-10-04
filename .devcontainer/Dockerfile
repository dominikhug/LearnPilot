FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm

# Pre-create the Claude Code config dir so the named volume mounted there
# is owned by the vscode user (login + settings survive rebuilds).
RUN mkdir -p /home/vscode/.claude && chown -R vscode:vscode /home/vscode/.claude

# Python package manager for the backend
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy
