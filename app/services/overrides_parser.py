"""Read a shard's modoverrides.lua via the parse_lua_table.lua helper."""

from __future__ import annotations

from pathlib import Path

from app.models import OverrideEntry, ShardOverrides
from app.services.lua_runner import PARSE_LUA_TABLE_SCRIPT, LuaError, run_lua_json


def load_shard_overrides(
    shard: str, overrides_path: Path, lua_command: str = ""
) -> ShardOverrides:
    """Parse one shard's modoverrides.lua.

    A missing file is treated as an empty (valid) configuration. Parse
    failures are reported via ok=False so the UI can warn and the save
    handler can refuse to overwrite a file it could not read.
    """
    result = ShardOverrides(shard=shard, path=overrides_path)

    if not overrides_path.parent.is_dir():
        result.ok = False
        result.error = f"shard directory not found: {overrides_path.parent}"
        return result
    if not overrides_path.is_file():
        # No overrides yet: all mods disabled, nothing configured.
        return result

    result.exists = True
    try:
        parsed = run_lua_json(
            PARSE_LUA_TABLE_SCRIPT, [str(overrides_path)], lua_command=lua_command
        )
    except LuaError as exc:
        result.ok = False
        result.error = str(exc)
        return result

    if not parsed.get("ok"):
        result.ok = False
        result.error = str(parsed.get("error") or "unknown parse error")
        return result

    data = parsed.get("data")
    if not isinstance(data, dict):
        result.ok = False
        result.error = "modoverrides.lua did not return a table of mods"
        return result

    for key, value in data.items():
        if not isinstance(value, dict):
            # Malformed entry; keep it out of the editor but do not crash.
            continue
        config = value.get("configuration_options")
        result.entries[str(key)] = OverrideEntry(
            # DST treats a missing/false `enabled` as disabled.
            enabled=value.get("enabled") is True,
            configuration_options=dict(config) if isinstance(config, dict) else {},
        )
    return result
