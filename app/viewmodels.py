"""View models for the templates + the matching form-decoding helpers.

Form field naming scheme (shared between rendering and the save handler):

  mod_ids                              hidden, one per rendered mod card
  enabled__<shard>__<workshop_id>      checkbox; absent == disabled
  opt__<shard>__<workshop_id>__<idx>   option value; <idx> is the option's
                                       index in modinfo's configuration_options
                                       (indices avoid escaping issues with
                                       arbitrary option names)

Select values are JSON-encoded with a "j:" prefix so types survive the HTML
round trip exactly. Free-text inputs (options without a predefined list) are
coerced heuristically by ``coerce_text_value``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.models import ConfigOption, Mod, OverrideEntry, ShardOverrides

JSON_VALUE_PREFIX = "j:"


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

def values_equal(a: Any, b: Any) -> bool:
    """Equality that never confuses True/False with 1/0."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


def display_value(value: Any) -> str:
    """Human-readable, Lua-flavored rendering of a scalar."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def encode_form_value(value: Any) -> str:
    return JSON_VALUE_PREFIX + json.dumps(value)


def coerce_text_value(raw: str, default: Any) -> Any:
    """Best-effort typing for free-text option inputs.

    "true"/"false" -> bool, "nil"/"null" -> None, numeric strings -> numbers,
    empty -> the modinfo default, "quoted" -> forced literal string.
    """
    s = raw.strip()
    if s == "":
        return default
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("nil", "null"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


# ---------------------------------------------------------------------------
# Field names
# ---------------------------------------------------------------------------

def enabled_field_name(shard: str, workshop_id: str) -> str:
    return f"enabled__{shard}__{workshop_id}"


def option_field_name(shard: str, workshop_id: str, index: int) -> str:
    return f"opt__{shard}__{workshop_id}__{index}"


# ---------------------------------------------------------------------------
# View models
# ---------------------------------------------------------------------------

@dataclass
class ChoiceVM:
    value: str
    label: str
    selected: bool = False
    hover: str = ""


@dataclass
class FieldVM:
    kind: str  # "select" | "text"
    input_name: str
    choices: list[ChoiceVM] = field(default_factory=list)
    text_value: str = ""
    # Form-encoded modinfo default, rendered as data-default so the
    # "Reset to defaults" button can restore it client-side.
    default_value: str = ""


@dataclass
class OptionRowVM:
    option: ConfigOption
    is_header: bool
    per_shard: dict[str, FieldVM] = field(default_factory=dict)


@dataclass
class ModCardVM:
    mod: Mod
    enabled: dict[str, bool] = field(default_factory=dict)
    option_rows: list[OptionRowVM] = field(default_factory=list)
    # shard -> {option_name: value} present in overrides but unknown to
    # modinfo; preserved verbatim on save and shown read-only.
    extra_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    search_blob: str = ""
    short_description: str = ""
    num_options: int = 0
    # False when the mod is missing from dedicated_server_mods_setup.lua
    # (True also when the setup file state is unknown — no badge then).
    in_setup: bool = True
    # False when the shards currently hold different settings for this mod
    # (only surfaced in unified mode, where saving re-syncs them).
    synced: bool = True


def _text_field_value(value: Any) -> str:
    if value is None:
        return ""
    return display_value(value)


def _build_field(option: ConfigOption, current: Any, input_name: str) -> FieldVM:
    # Predefined choice list -> select. A bare boolean option (no choices in
    # modinfo) also becomes a select for foolproof input; anything else is
    # free text.
    if option.choices:
        choices: list[ChoiceVM] = []
        matched = False
        for choice in option.choices:
            selected = not matched and values_equal(current, choice.data)
            matched = matched or selected
            label = choice.description or display_value(choice.data)
            choices.append(
                ChoiceVM(
                    value=encode_form_value(choice.data),
                    label=label,
                    selected=selected,
                    hover=choice.hover,
                )
            )
        if not matched:
            # Current value (from modoverrides.lua) is not one of the
            # modinfo choices; keep it selectable so saving preserves it.
            choices.append(
                ChoiceVM(
                    value=encode_form_value(current),
                    label=f"(current: {display_value(current)})",
                    selected=True,
                    hover="value from modoverrides.lua, not listed in modinfo",
                )
            )
        # data-default must match an existing <option> value exactly, so use
        # the encoded value of the choice whose data equals the default.
        default_value = next(
            (
                encode_form_value(c.data)
                for c in option.choices
                if values_equal(c.data, option.default)
            ),
            encode_form_value(option.default),
        )
        return FieldVM(
            kind="select",
            input_name=input_name,
            choices=choices,
            default_value=default_value,
        )

    if isinstance(option.default, bool) or isinstance(current, bool):
        current_bool = current if isinstance(current, bool) else bool(option.default)
        return FieldVM(
            kind="select",
            input_name=input_name,
            choices=[
                ChoiceVM(encode_form_value(True), "true", selected=current_bool is True),
                ChoiceVM(encode_form_value(False), "false", selected=current_bool is False),
            ],
            default_value=encode_form_value(bool(option.default)),
        )

    return FieldVM(
        kind="text",
        input_name=input_name,
        text_value=_text_field_value(current),
        default_value=_text_field_value(option.default),
    )


def _shorten(text: str, limit: int = 220) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def build_mod_card(
    mod: Mod,
    shards: list[str],
    overrides: Mapping[str, ShardOverrides],
    setup_ids: set[str] | None = None,
    field_shards: list[str] | None = None,
) -> ModCardVM:
    """Build the card VM. ``field_shards`` controls which shards get form
    fields — in unified mode that is just the primary shard, while sync
    detection and preserved-keys still consider every shard."""
    field_shards = field_shards or shards
    card = ModCardVM(mod=mod)
    if setup_ids is not None:
        card.in_setup = mod.workshop_id in setup_ids
    card.short_description = _shorten(mod.description)
    card.search_blob = " ".join(
        [mod.display_name, mod.folder_name, mod.workshop_id, mod.author]
    ).lower()

    entries: dict[str, OverrideEntry | None] = {}
    for shard in shards:
        entry = overrides[shard].entries.get(mod.override_key) if shard in overrides else None
        entries[shard] = entry
    for shard in field_shards:
        card.enabled[shard] = bool(entries[shard] and entries[shard].enabled)

    # Do the shards currently agree on this mod?
    primary = entries[shards[0]] or OverrideEntry()
    card.synced = all(
        (entries[s] or OverrideEntry()).enabled == primary.enabled
        and (entries[s] or OverrideEntry()).configuration_options == primary.configuration_options
        for s in shards
    )

    # Named options including pseudo-headers — their keys legitimately appear
    # in modoverrides.lua (the game writes headers too), so they must not be
    # reported as "unknown" extras.
    named_options = {o.name for o in mod.configuration_options if o.name}
    card.num_options = len(
        {o.name for o in mod.configuration_options if o.name and not o.is_header}
    )

    for index, option in enumerate(mod.configuration_options):
        row = OptionRowVM(option=option, is_header=option.is_header)
        if not option.is_header:
            for shard in field_shards:
                entry = entries[shard]
                if entry is not None and option.name in entry.configuration_options:
                    current = entry.configuration_options[option.name]
                else:
                    current = option.default
                row.per_shard[shard] = _build_field(
                    option, current, option_field_name(shard, mod.workshop_id, index)
                )
        card.option_rows.append(row)

    for shard in shards:
        entry = entries[shard]
        if entry is not None:
            extras = {
                k: v
                for k, v in entry.configuration_options.items()
                if k not in named_options
            }
            if extras:
                card.extra_options[shard] = extras

    return card


# ---------------------------------------------------------------------------
# Pending mods (listed in modoverrides.lua but not downloaded yet)
# ---------------------------------------------------------------------------

PENDING_KEY_RE = re.compile(r"^workshop-(\d+)$")


@dataclass
class PendingModVM:
    """A mod that is configured but not downloaded: referenced by a shard's
    modoverrides.lua and/or by dedicated_server_mods_setup.lua, with no
    matching mod folder on disk — typically a freshly added mod waiting for
    the server to download it."""

    workshop_id: str
    override_key: str
    enabled: dict[str, bool] = field(default_factory=dict)
    option_counts: dict[str, int] = field(default_factory=dict)
    in_setup: bool = True   # listed in dedicated_server_mods_setup.lua?
    in_overrides: bool = False  # has an entry in any shard's modoverrides.lua?
    synced: bool = True  # enabled flags agree across shards?


def build_pending_mods(
    shards: list[str],
    overrides: Mapping[str, ShardOverrides],
    known_ids: set[str],
    setup_ids: set[str] | None = None,
) -> list[PendingModVM]:
    """Union of both not-yet-downloaded sources, so the Mods page reflects
    restores/edits of either file (a mod only in modoverrides shows a
    "won't be downloaded" warning; a mod only in the setup file shows up
    with all shards unticked)."""
    pending: dict[str, PendingModVM] = {}

    def get_vm(workshop_id: str) -> PendingModVM:
        vm = pending.get(workshop_id)
        if vm is None:
            vm = PendingModVM(
                workshop_id=workshop_id,
                override_key=f"workshop-{workshop_id}",
                enabled={s: False for s in shards},
                option_counts={s: 0 for s in shards},
            )
            pending[workshop_id] = vm
        return vm

    for shard in shards:
        result = overrides.get(shard)
        if result is None or not result.ok:
            continue
        for key, entry in result.entries.items():
            match = PENDING_KEY_RE.match(key)
            if match is None or match.group(1) in known_ids:
                continue
            vm = get_vm(match.group(1))
            vm.in_overrides = True
            vm.enabled[shard] = entry.enabled
            vm.option_counts[shard] = len(entry.configuration_options)

    if setup_ids is not None:
        for workshop_id in setup_ids:
            if workshop_id not in known_ids:
                get_vm(workshop_id)
        for vm in pending.values():
            vm.in_setup = vm.workshop_id in setup_ids

    for vm in pending.values():
        vm.synced = len({vm.enabled[s] for s in shards}) <= 1

    return sorted(pending.values(), key=lambda v: v.workshop_id)


# ---------------------------------------------------------------------------
# Form decoding (save handler)
# ---------------------------------------------------------------------------

def decode_option_value(raw: str, default: Any) -> Any:
    if raw.startswith(JSON_VALUE_PREFIX):
        try:
            return json.loads(raw[len(JSON_VALUE_PREFIX):])
        except json.JSONDecodeError:
            return default
    return coerce_text_value(raw, default)


def build_config_from_form(
    form: Mapping[str, Any],
    shard: str,
    mod: Mod,
    old_entry: OverrideEntry,
) -> dict[str, Any]:
    """Rebuild a mod's configuration_options for one shard from form data.

    Options missing from the form fall back to their previous value (or the
    modinfo default); keys unknown to modinfo are preserved verbatim so a
    save never silently drops data written by hand or by an older mod version.
    """
    config: dict[str, Any] = {}
    for index, option in enumerate(mod.configuration_options):
        if option.is_header or not option.name:
            continue
        raw = form.get(option_field_name(shard, mod.workshop_id, index))
        if isinstance(raw, str):
            value = decode_option_value(raw, option.default)
        elif option.name in old_entry.configuration_options:
            value = old_entry.configuration_options[option.name]
        else:
            value = option.default
        config[option.name] = value

    for key, value in old_entry.configuration_options.items():
        if key not in config:
            config[key] = value
    return config
