"""Scan the mods directory for downloaded workshop mods.

Recognized folder names:
  * workshop-378160973   (classic mods/ layout)
  * 378160973            (ugc_mods content/322330/ layout)

A per-modinfo cache keyed by (path, mtime, size) avoids re-spawning a Lua
subprocess for every page load.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.models import Mod
from app.services.lua_runner import LuaNotFoundError, find_lua_command
from app.services.modinfo_parser import parse_modinfo_file

FOLDER_RE = re.compile(r"^(?:workshop-)?(\d+)$")

# modinfo path -> ((mtime_ns, size), Mod)
_cache: dict[Path, tuple[tuple[int, int], Mod]] = {}


class ModsPathError(RuntimeError):
    """The configured mods_path is missing or not a directory."""


def clear_cache() -> None:
    _cache.clear()


def _build_mod(
    mod_dir: Path, workshop_id: str, source: str, lua_command: str, lua_missing: str
) -> Mod:
    mod = Mod(
        workshop_id=workshop_id, folder_name=mod_dir.name, path=mod_dir, source=source
    )
    modinfo_path = mod_dir / "modinfo.lua"

    if not modinfo_path.is_file():
        mod.parse_error = "modinfo.lua not found in mod folder"
        return mod
    if lua_missing:
        mod.parse_error = lua_missing
        return mod

    try:
        stat = modinfo_path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError as exc:
        mod.parse_error = f"cannot stat modinfo.lua: {exc}"
        return mod

    cached = _cache.get(modinfo_path)
    if cached is not None and cached[0] == stamp:
        return cached[1]

    result = parse_modinfo_file(modinfo_path, mod_dir.name, lua_command=lua_command)
    mod.parse_ok = result.ok
    mod.parse_error = result.error
    mod.name = result.name
    mod.description = result.description
    mod.author = result.author
    mod.version = result.version
    mod.configuration_options = result.configuration_options

    _cache[modinfo_path] = (stamp, mod)
    return mod


def _scan_dir(
    root: Path, source: str, lua_command: str, lua_missing: str
) -> list[Mod]:
    mods: list[Mod] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        match = FOLDER_RE.match(entry.name)
        if match is None:
            continue
        mods.append(_build_mod(entry, match.group(1), source, lua_command, lua_missing))
    return mods


def scan_mods(
    mods_path: Path,
    local_mods_path: Path | None = None,
    lua_command: str = "",
    use_cache: bool = True,
) -> list[Mod]:
    """Return all recognized mods, sorted by display name.

    ``local_mods_path`` (the game's classic mods/ folder) is scanned on top of
    mods_path; when the same workshop id exists in both, the local copy is the
    one shown — it is what the game actually loads without a Workshop download.

    Raises ModsPathError if mods_path itself is unusable; individual mod
    failures are reported via Mod.parse_error instead of raising.
    """
    if not mods_path.is_dir():
        raise ModsPathError(f"mods_path does not exist or is not a directory: {mods_path}")

    if not use_cache:
        clear_cache()

    # If no Lua interpreter is available we still list the folders, each
    # carrying the same actionable error message.
    lua_missing = ""
    try:
        find_lua_command(lua_command)
    except LuaNotFoundError as exc:
        lua_missing = str(exc)

    by_id: dict[str, Mod] = {}
    for mod in _scan_dir(mods_path, "workshop", lua_command, lua_missing):
        by_id[mod.workshop_id] = mod
    if local_mods_path is not None and local_mods_path.is_dir():
        for mod in _scan_dir(local_mods_path, "local", lua_command, lua_missing):
            by_id[mod.workshop_id] = mod

    mods = list(by_id.values())
    mods.sort(key=lambda m: (m.display_name.lower(), m.workshop_id))
    return mods
