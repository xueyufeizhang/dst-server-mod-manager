"""Run the operator-configured restart/status shell commands.

The commands come from config.yaml, which is trusted operator input (whoever
can edit that file already controls the machine). They intentionally run
through the shell so operators can write "sudo systemctl restart dst-server"
without extra quoting rules.
"""

from __future__ import annotations

import subprocess
from datetime import datetime

from app.models import CommandResult

COMMAND_TIMEOUT = 120.0  # seconds


def run_command(command: str, timeout: float = COMMAND_TIMEOUT) -> CommandResult:
    result = CommandResult(command=command, ran_at=datetime.now())
    if not command.strip():
        result.error = "no command configured"
        return result
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result.error = f"command timed out after {timeout:.0f}s"
        return result
    except OSError as exc:
        result.error = f"failed to run command: {exc}"
        return result

    result.exit_code = proc.returncode
    result.stdout = proc.stdout.strip()
    result.stderr = proc.stderr.strip()
    result.ok = proc.returncode == 0
    if not result.ok and not result.stderr and not result.stdout:
        result.error = f"command exited with code {proc.returncode}"
    return result
