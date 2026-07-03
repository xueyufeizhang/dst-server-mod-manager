"""Read cluster.ini / server.ini for display on the Server page.

Read-only: this panel never writes these files. Values whose key looks
sensitive (password / token / key) are masked before anything reaches the
browser, including the raw-file view.
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path

SENSITIVE_RE = re.compile(r"password|token|key", re.IGNORECASE)
MASK = "••••••"

# (label, section, key, kind); kind "secret" renders as set/not set.
CLUSTER_SUMMARY_FIELDS = [
    ("Cluster name", "NETWORK", "cluster_name", ""),
    ("Description", "NETWORK", "cluster_description", ""),
    ("Game mode", "GAMEPLAY", "game_mode", ""),
    ("Max players", "GAMEPLAY", "max_players", ""),
    ("PvP", "GAMEPLAY", "pvp", ""),
    ("Pause when empty", "GAMEPLAY", "pause_when_empty", ""),
    ("Language", "NETWORK", "cluster_language", ""),
    ("Password", "NETWORK", "cluster_password", "secret"),
    ("Multi-shard enabled", "SHARD", "shard_enabled", ""),
    ("Console enabled", "MISC", "console_enabled", ""),
]

SHARD_SUMMARY_FIELDS = [
    ("Is master", "SHARD", "is_master", ""),
    ("Shard name", "SHARD", "name", ""),
    ("Server port (UDP)", "NETWORK", "server_port", ""),
    ("Steam master port", "STEAM", "master_server_port", ""),
    ("Steam auth port", "STEAM", "authentication_port", ""),
]


@dataclass
class IniFileInfo:
    """One parsed INI file (cluster.ini or a shard's server.ini)."""

    title: str
    path: Path
    exists: bool = False
    error: str = ""
    summary: list[tuple[str, str]] = field(default_factory=list)
    raw_masked: str = ""


@dataclass
class ClusterConfigInfo:
    cluster: IniFileInfo
    token_present: bool = False
    cluster_name: str = ""
    shards: list[IniFileInfo] = field(default_factory=list)


def mask_sensitive(text: str) -> str:
    """Mask values of password/token/key-ish lines in an INI dump."""
    masked: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(\s*)([^=;#\[\]]+?)(\s*=\s*)(.+)$", line)
        if match and SENSITIVE_RE.search(match.group(2)) and match.group(4).strip():
            line = f"{match.group(1)}{match.group(2)}{match.group(3)}{MASK}"
        masked.append(line)
    return "\n".join(masked)


def _get(parser: configparser.ConfigParser, section: str, key: str) -> str | None:
    try:
        value = parser.get(section, key, fallback=None)
    except configparser.Error:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read_ini_file(
    title: str, path: Path, fields: list[tuple[str, str, str, str]]
) -> IniFileInfo:
    info = IniFileInfo(title=title, path=path)
    if not path.is_file():
        return info
    info.exists = True
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        info.error = str(exc)
        return info
    info.raw_masked = mask_sensitive(raw)

    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(raw)
    except configparser.Error as exc:
        info.error = f"could not parse: {exc}"
        return info

    for label, section, key, kind in fields:
        value = _get(parser, section, key)
        if kind == "secret":
            info.summary.append((label, "set (hidden)" if value else "not set"))
        elif value is not None:
            info.summary.append((label, value))
    return info


def read_cluster_info(cluster_path: Path, shards: list[str]) -> ClusterConfigInfo:
    cluster = _read_ini_file(
        "cluster.ini", cluster_path / "cluster.ini", CLUSTER_SUMMARY_FIELDS
    )
    info = ClusterConfigInfo(
        cluster=cluster,
        token_present=(cluster_path / "cluster_token.txt").is_file(),
    )
    for label, value in cluster.summary:
        if label == "Cluster name":
            info.cluster_name = value
            break
    for shard in shards:
        info.shards.append(
            _read_ini_file(
                f"{shard}/server.ini",
                cluster_path / shard / "server.ini",
                SHARD_SUMMARY_FIELDS,
            )
        )
    return info
