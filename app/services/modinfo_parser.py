"""Parse a mod's modinfo.lua via the parse_modinfo.lua helper script."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models import ConfigOption, OptionChoice
from app.services.lua_runner import PARSE_MODINFO_SCRIPT, LuaError, run_lua_json


@dataclass
class ModinfoResult:
    ok: bool
    error: str = ""
    name: str = ""
    description: str = ""
    author: str = ""
    version: str = ""
    configuration_options: list[ConfigOption] = field(default_factory=list)


def _convert_choice(raw: Any) -> OptionChoice:
    if not isinstance(raw, dict):
        return OptionChoice()
    return OptionChoice(
        description=str(raw.get("description") or ""),
        data=raw.get("data"),  # missing key == Lua nil == None
        hover=str(raw.get("hover") or ""),
    )


def _convert_option(raw: Any) -> ConfigOption | None:
    if not isinstance(raw, dict):
        return None
    choices_raw = raw.get("options")
    choices = (
        [_convert_choice(c) for c in choices_raw]
        if isinstance(choices_raw, list)
        else []
    )
    return ConfigOption(
        name=str(raw.get("name") or ""),
        label=str(raw.get("label") or ""),
        hover=str(raw.get("hover") or ""),
        default=raw.get("default"),
        choices=choices,
    )


def parse_modinfo_file(
    modinfo_path: Path, folder_name: str, lua_command: str = ""
) -> ModinfoResult:
    """Parse one modinfo.lua. Never raises for a bad modinfo file; only for
    infrastructure problems (LuaError propagates so the caller can decide)."""
    try:
        result = run_lua_json(
            PARSE_MODINFO_SCRIPT,
            [str(modinfo_path), folder_name],
            lua_command=lua_command,
        )
    except LuaError as exc:
        return ModinfoResult(ok=False, error=str(exc))

    if not result.get("ok"):
        return ModinfoResult(ok=False, error=str(result.get("error") or "unknown parse error"))

    modinfo = result.get("modinfo") or {}
    options_raw = modinfo.get("configuration_options")
    options: list[ConfigOption] = []
    if isinstance(options_raw, list):
        for raw in options_raw:
            option = _convert_option(raw)
            if option is not None:
                options.append(option)

    return ModinfoResult(
        ok=True,
        name=str(modinfo.get("name") or ""),
        description=str(modinfo.get("description") or ""),
        author=str(modinfo.get("author") or ""),
        version=str(modinfo.get("version") or ""),
        configuration_options=options,
    )
