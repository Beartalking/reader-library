#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Model calls through headless Claude Code (`claude -p`), billed to Bear's subscription.

For commands Bear runs by hand or through a skill. Unattended launchd jobs keep
using the API key: they must not depend on a logged-in session.

Canonical copy lives in content-publisher; other repos carry a verbatim copy.
Change it here first, then copy it over.

Checked 2026-10-08 against Claude Code 2.1.292: `--output-format json` returns
one object with `result`, `is_error`, `subtype` and `stop_reason`; with
ANTHROPIC_API_KEY removed from the environment, `claude auth status` reports
authMethod "claude.ai". `--bare` is not used because it forces API-key auth.
With `--setting-sources ""` and a temp working directory, no CLAUDE.md, memory,
hooks, tools or MCP servers reach the call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

DEFAULT_MODEL = "sonnet"
# Always sent: without --system-prompt, claude -p runs under Claude Code's own
# coding-agent prompt. Callers that keep their instructions in the user message
# get this instead (2026-10-08).
NEUTRAL_SYSTEM_PROMPT = "Follow the instructions in the user's message."
TIMEOUT_SECONDS = 300
# Absolute fallback: launchd's PATH has no ~/.local/bin.
_FALLBACK_BIN = Path.home() / ".local" / "bin" / "claude"


class ClaudeCLIError(RuntimeError):
    """The call failed; the message says why (usage limit, timeout, not logged in...)."""


def alias(model: Optional[str]) -> str:
    """Map an API model id (claude-opus-4-6) to a CLI alias (opus)."""
    text = (model or DEFAULT_MODEL).lower()
    for name in ("opus", "sonnet", "haiku"):
        if name in text:
            return name
    return text


def _binary() -> str:
    return shutil.which("claude") or str(_FALLBACK_BIN)


def complete(system: Optional[str], user: str, model: str = DEFAULT_MODEL,
             timeout: int = TIMEOUT_SECONDS) -> str:
    """Return the model's text, or raise ClaudeCLIError."""
    cmd = [
        _binary(), "-p",
        "--output-format", "json",
        "--system-prompt", system or NEUTRAL_SYSTEM_PROMPT,
        "--tools", "",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--no-session-persistence",
        "--model", alias(model),
    ]
    # With the key present, Claude Code would bill the API account instead.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        done = subprocess.run(cmd, input=user, capture_output=True, text=True,
                              timeout=timeout, env=env, cwd=tempfile.gettempdir())
    except FileNotFoundError:
        raise ClaudeCLIError(f"claude CLI not found at {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise ClaudeCLIError(f"claude -p timed out after {timeout}s")

    try:
        data = json.loads(done.stdout)
    except (json.JSONDecodeError, TypeError):
        raise ClaudeCLIError(f"claude -p exit {done.returncode}: {(done.stderr or done.stdout or '')[:200]}")
    if data.get("is_error") or data.get("subtype") != "success":
        raise ClaudeCLIError(f"claude -p error ({data.get('subtype')}): {str(data.get('result', ''))[:200]}")
    if data.get("stop_reason") == "max_tokens":
        raise ClaudeCLIError("claude -p reply hit max_tokens")
    return str(data.get("result") or "")


def call(system: Optional[str], user: str, model: str = DEFAULT_MODEL,
         timeout: int = TIMEOUT_SECONDS) -> str:
    """Return the model's text, or "" on any failure (printed with a ⚠)."""
    try:
        return complete(system, user, model=model, timeout=timeout)
    except ClaudeCLIError as e:
        print(f"  ⚠ {e}")
        return ""
