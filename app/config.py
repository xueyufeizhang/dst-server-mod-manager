"""Application configuration loaded from a YAML file.

The config file location is resolved in this order:
1. explicit ``path`` argument,
2. the ``DST_MOD_MANAGER_CONFIG`` environment variable,
3. ``config.yaml`` in the current working directory.

Relative paths inside the file (cluster_path, mods_path, backup.directory)
are resolved relative to the directory containing the config file, so the
service behaves the same regardless of the working directory it is started
from.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_ENV_VAR = "DST_MOD_MANAGER_CONFIG"
DEFAULT_CONFIG_NAME = "config.yaml"


class ConfigError(RuntimeError):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class DSTConfig:
    cluster_path: Path
    mods_path: Path
    # dedicated_server_mods_setup.lua — the file that tells the dedicated
    # server which Workshop mods to download at boot ("Add mod" writes here).
    mods_setup_path: Path
    shards: list[str] = field(default_factory=lambda: ["Master", "Caves"])
    # True (default): the Mods page shows ONE set of controls per mod and
    # saving writes identical settings to every shard — the usual setup.
    # False: per-shard columns, shards can be configured independently.
    unified_mod_config: bool = True

    def shard_dir(self, shard: str) -> Path:
        return self.cluster_path / shard

    def overrides_path(self, shard: str) -> Path:
        return self.shard_dir(shard) / "modoverrides.lua"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    start_command: str = ""
    stop_command: str = ""
    restart_command: str = ""
    status_command: str = ""


@dataclass
class SecurityConfig:
    enable_basic_auth: bool = True
    username: str = "admin"
    password: str = "changeme"


@dataclass
class BackupConfig:
    directory: Path = Path("backups")
    keep_last: int = 20


@dataclass
class SteamCmdConfig:
    # Path to steamcmd; empty = auto-detect "steamcmd" in PATH. Used by the
    # optional "download now" feature; everything else works without it.
    command: str = ""
    # Seconds before a download attempt is aborted. steamcmd's first run
    # self-updates and can take a while.
    timeout: int = 900


@dataclass
class AppConfig:
    dst: DSTConfig
    server: ServerConfig
    security: SecurityConfig
    backup: BackupConfig
    steamcmd: SteamCmdConfig = field(default_factory=SteamCmdConfig)
    lua_command: str = ""
    config_path: Path = Path(DEFAULT_CONFIG_NAME)


def _resolve_path(value: str, base: Path) -> Path:
    """Expand ~ and make relative paths relative to the config file dir."""
    p = Path(os.path.expanduser(str(value)))
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"config section '{name}' must be a mapping")
    return value


def find_config_path(path: str | Path | None = None) -> Path:
    candidate = path or os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_NAME
    config_path = Path(candidate).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(
            f"config file not found: {config_path}\n"
            f"Copy config.example.yaml to config.yaml and edit it, or point "
            f"{CONFIG_ENV_VAR} at your config (e.g. config.sample.yaml for the demo data)."
        )
    return config_path


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = find_config_path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} does not contain a YAML mapping")

    base = config_path.parent

    dst_raw = _section(raw, "dst")
    for required in ("cluster_path", "mods_path"):
        if not dst_raw.get(required):
            raise ConfigError(f"config is missing required key: dst.{required}")
    shards = dst_raw.get("shards") or ["Master", "Caves"]
    if not isinstance(shards, list) or not all(isinstance(s, str) for s in shards):
        raise ConfigError("dst.shards must be a list of shard folder names")

    server_raw = _section(raw, "server")
    security_raw = _section(raw, "security")
    backup_raw = _section(raw, "backup")
    lua_raw = _section(raw, "lua")
    steamcmd_raw = _section(raw, "steamcmd")

    mods_path = _resolve_path(dst_raw["mods_path"], base)
    mods_setup_raw = str(dst_raw.get("mods_setup_path") or "").strip()
    mods_setup_path = (
        _resolve_path(mods_setup_raw, base)
        if mods_setup_raw
        else mods_path / "dedicated_server_mods_setup.lua"
    )

    return AppConfig(
        dst=DSTConfig(
            cluster_path=_resolve_path(dst_raw["cluster_path"], base),
            mods_path=mods_path,
            mods_setup_path=mods_setup_path,
            shards=[s.strip() for s in shards if s.strip()],
            unified_mod_config=bool(dst_raw.get("unified_mod_config", True)),
        ),
        server=ServerConfig(
            host=str(server_raw.get("host", "127.0.0.1")),
            port=int(server_raw.get("port", 8080)),
            start_command=str(server_raw.get("start_command") or ""),
            stop_command=str(server_raw.get("stop_command") or ""),
            restart_command=str(server_raw.get("restart_command") or ""),
            status_command=str(server_raw.get("status_command") or ""),
        ),
        security=SecurityConfig(
            enable_basic_auth=bool(security_raw.get("enable_basic_auth", True)),
            username=str(security_raw.get("username", "admin")),
            password=str(security_raw.get("password", "")),
        ),
        backup=BackupConfig(
            directory=_resolve_path(backup_raw.get("directory", "backups"), base),
            keep_last=int(backup_raw.get("keep_last", 20)),
        ),
        steamcmd=SteamCmdConfig(
            command=str(steamcmd_raw.get("command") or ""),
            timeout=int(steamcmd_raw.get("timeout", 900)),
        ),
        lua_command=str(lua_raw.get("command") or ""),
        config_path=config_path,
    )
