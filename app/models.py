"""Shared data models.

``LuaScalar`` is any value that survives the Lua -> JSON -> Python round trip
for mod option data: str, int, float, bool or None (Lua nil).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Union

LuaScalar = Union[str, int, float, bool, None]


@dataclass
class OptionChoice:
    """One entry of a configuration option's ``options`` list."""

    description: str = ""
    data: LuaScalar = None
    hover: str = ""


@dataclass
class ConfigOption:
    """One entry of modinfo.lua's ``configuration_options``."""

    name: str
    label: str = ""
    hover: str = ""
    default: LuaScalar = None
    choices: list[OptionChoice] = field(default_factory=list)

    @property
    def is_header(self) -> bool:
        # DST convention: options with an empty name are section separators.
        if self.name == "":
            return True
        # Common mod-author trick (e.g. Epic Healthbar): a named pseudo-option
        # whose only choice has a blank description is also just a header.
        # A single choice with a real description stays a normal option.
        return (
            len(self.choices) == 1
            and not (self.choices[0].description or "").strip()
        )

    @property
    def display_label(self) -> str:
        return self.label or self.name


@dataclass
class Mod:
    """A workshop mod found on disk, plus its parsed modinfo (if any)."""

    workshop_id: str
    folder_name: str
    path: Path
    parse_ok: bool = False
    parse_error: str = ""
    name: str = ""
    description: str = ""
    author: str = ""
    version: str = ""
    configuration_options: list[ConfigOption] = field(default_factory=list)

    @property
    def override_key(self) -> str:
        """Key used in modoverrides.lua (always workshop-<id>, even if the
        on-disk folder is a bare numeric id from an ugc_mods layout)."""
        return f"workshop-{self.workshop_id}"

    @property
    def display_name(self) -> str:
        return self.name or self.folder_name


@dataclass
class OverrideEntry:
    """One mod's entry inside a shard's modoverrides.lua."""

    enabled: bool = False
    configuration_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ShardOverrides:
    """Parse result for one shard's modoverrides.lua."""

    shard: str
    path: Path
    exists: bool = False
    ok: bool = True
    error: str = ""
    entries: dict[str, OverrideEntry] = field(default_factory=dict)

    @property
    def enabled_count(self) -> int:
        return sum(1 for e in self.entries.values() if e.enabled)


@dataclass
class BackupFileInfo:
    """One backed-up file inside a backup session."""

    key: str  # restore-target key: shard name or "ModSetup"
    filename: str
    path: Path
    size: int


@dataclass
class BackupSessionInfo:
    """One backup record == all files backed up by a single operation."""

    session_id: str
    created: datetime
    reason: str  # e.g. "save", "add workshop-123", "before restoring ..."
    files: list[BackupFileInfo]


@dataclass
class CommandResult:
    """Result of running a configured shell command (restart/status)."""

    command: str = ""
    ok: bool = False
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    ran_at: datetime | None = None
