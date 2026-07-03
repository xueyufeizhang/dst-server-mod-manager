"""Thin wrapper around the helper Lua scripts in scripts/.

Both helper scripts print exactly one JSON document to stdout with an "ok"
flag, and always exit 0 for *protocol-level* errors (bad file, parse error),
so a non-zero exit code or non-JSON output means the interpreter itself
misbehaved.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Preference order for auto-detection. Version-suffixed names first (Debian/
# Ubuntu style), then generic names.
LUA_CANDIDATES = ("lua5.4", "lua5.3", "lua5.2", "lua5.1", "lua", "luajit")

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
PARSE_MODINFO_SCRIPT = SCRIPTS_DIR / "parse_modinfo.lua"
PARSE_LUA_TABLE_SCRIPT = SCRIPTS_DIR / "parse_lua_table.lua"
CHECK_LUA_SYNTAX_SCRIPT = SCRIPTS_DIR / "check_lua_syntax.lua"

SUBPROCESS_TIMEOUT = 10.0  # seconds; modinfo.lua should execute instantly


class LuaError(RuntimeError):
    """A Lua helper invocation failed."""


class LuaNotFoundError(LuaError):
    """No usable Lua interpreter was found on this system."""


def find_lua_command(configured: str = "") -> str:
    """Return the Lua executable to use, honoring the configured override."""
    if configured.strip():
        resolved = shutil.which(configured.strip())
        if resolved is None:
            raise LuaNotFoundError(
                f"configured lua.command not found in PATH: {configured!r}"
            )
        return resolved
    for candidate in LUA_CANDIDATES:
        resolved = shutil.which(candidate)
        if resolved is not None:
            return resolved
    raise LuaNotFoundError(
        "no Lua interpreter found; install one, e.g. 'sudo apt install lua5.4' "
        "on Debian/Ubuntu, or set lua.command in config.yaml"
    )


def run_lua_json(
    script: Path,
    args: list[str],
    lua_command: str = "",
    timeout: float = SUBPROCESS_TIMEOUT,
) -> dict[str, Any]:
    """Run a helper script and return its parsed JSON output.

    Raises LuaError/LuaNotFoundError on infrastructure failures; a normal
    "the target file is bad" outcome is returned as ``{"ok": False, ...}``.
    """
    lua = find_lua_command(lua_command)
    cmd = [lua, str(script), *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LuaError(f"lua helper timed out after {timeout}s: {' '.join(cmd)}") from exc
    except OSError as exc:
        raise LuaError(f"failed to run lua helper: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise LuaError(
            f"lua helper exited with code {proc.returncode}: {detail[:500]}"
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise LuaError(
            f"lua helper produced invalid JSON: {proc.stdout[:200]!r}"
        ) from exc
    if not isinstance(result, dict):
        raise LuaError("lua helper produced a non-object JSON document")
    return result
