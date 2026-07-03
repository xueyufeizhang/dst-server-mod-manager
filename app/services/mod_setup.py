"""Manage dedicated_server_mods_setup.lua.

This file lives in the server's mods/ directory and tells the dedicated
server which Workshop mods to download at boot, one line per mod:

    ServerModSetup("378160973")

Unlike modinfo.lua this file is strictly line-oriented, so a small regex is
appropriate here. Lines we don't understand (comments, collection setups,
manual edits) are always preserved verbatim.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.overrides_writer import write_file_atomic

# Anchored at line start, so commented-out lines ("--ServerModSetup(...)")
# never match.
SETUP_LINE_RE = re.compile(r"^\s*ServerModSetup\s*\(\s*[\"'](\d+)[\"']\s*\)")

DEFAULT_HEADER = (
    "-- dedicated_server_mods_setup.lua\n"
    "-- Each ServerModSetup line makes the dedicated server download that\n"
    "-- Workshop mod at boot. Managed by dst-mod-manager; manual lines are\n"
    "-- preserved.\n"
)


def read_setup_ids(path: Path) -> set[str]:
    """Workshop ids currently listed via ServerModSetup()."""
    if not path.is_file():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = SETUP_LINE_RE.match(line)
        if match:
            ids.add(match.group(1))
    return ids


def add_mod_to_setup(path: Path, workshop_id: str) -> bool:
    """Append a ServerModSetup line. Returns False if already present."""
    if workshop_id in read_setup_ids(path):
        return False
    if path.is_file():
        content = path.read_text(encoding="utf-8", errors="replace")
        if content and not content.endswith("\n"):
            content += "\n"
    else:
        content = DEFAULT_HEADER
    content += f'ServerModSetup("{workshop_id}")\n'
    write_file_atomic(path, content)
    return True


def remove_mod_from_setup(path: Path, workshop_id: str) -> bool:
    """Drop the ServerModSetup line(s) for one id. Returns True if removed."""
    if not path.is_file():
        return False
    kept: list[str] = []
    removed = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = SETUP_LINE_RE.match(line)
        if match and match.group(1) == workshop_id:
            removed = True
            continue
        kept.append(line)
    if removed:
        write_file_atomic(path, "\n".join(kept) + ("\n" if kept else ""))
    return removed
