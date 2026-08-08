"""Named DST cluster discovery, creation, selection, and maintenance."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.models import OverrideEntry
from app.services.overrides_parser import load_shard_overrides
from app.services.overrides_writer import render_overrides, write_file_atomic

CLUSTER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ACTIVE_CLUSTER_RE = re.compile(
    r"^\s*DST_CLUSTER\s*=\s*[\"']?([A-Za-z0-9][A-Za-z0-9_-]{0,63})[\"']?\s*(?:#.*)?$"
)
WORLD_DIR_NAMES = {"save", "backup"}


class ClusterError(RuntimeError):
    """A safe, user-facing cluster management error."""


@dataclass(frozen=True)
class ClusterEntry:
    name: str
    path: Path
    active: bool


class ClusterManager:
    def __init__(
        self,
        root: Path,
        active_file: Path | None,
        configured_path: Path,
        shards: list[str],
        lua_command: str = "",
    ) -> None:
        self.root = root.resolve()
        self.active_file = active_file
        self.configured_path = configured_path.resolve()
        self.shards = list(shards)
        self.lua_command = lua_command

    @staticmethod
    def validate_name(name: str) -> str:
        value = name.strip()
        if not CLUSTER_NAME_RE.fullmatch(value):
            raise ClusterError(
                "Cluster name must use 1–64 letters, numbers, '-' or '_' and start with a letter or number."
            )
        return value

    def path_for(self, name: str) -> Path:
        name = self.validate_name(name)
        return self.root / name

    def _read_active_name(self) -> str | None:
        if self.active_file is None or not self.active_file.is_file():
            return None
        try:
            for line in self.active_file.read_text(encoding="utf-8").splitlines():
                match = ACTIVE_CLUSTER_RE.match(line)
                if match:
                    return self.validate_name(match.group(1))
        except (OSError, ClusterError):
            return None
        return None

    def active_name(self) -> str:
        return self._read_active_name() or self.configured_path.name

    def active_path(self) -> Path:
        return self.path_for(self.active_name())

    def list_clusters(self) -> list[ClusterEntry]:
        active = self.active_name()
        if not self.root.is_dir():
            return []
        entries: list[ClusterEntry] = []
        for path in sorted(self.root.iterdir(), key=lambda p: p.name.lower()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            if not CLUSTER_NAME_RE.fullmatch(path.name):
                continue
            entries.append(ClusterEntry(path.name, path, path.name == active))
        return entries

    @property
    def switching_available(self) -> bool:
        return self.active_file is not None

    def set_active(self, name: str) -> Path:
        target = self.path_for(name)
        if not target.is_dir():
            raise ClusterError(f"cluster not found: {name}")
        if self.active_file is None:
            raise ClusterError("active_cluster_file is not configured")
        self.active_file.parent.mkdir(parents=True, exist_ok=True)
        write_file_atomic(self.active_file, f"DST_CLUSTER={name}\n")
        return target

    def _existing_cluster(self, name: str) -> Path:
        """Return a real cluster directory after validating its name/path."""
        target = self.path_for(name)
        if target.is_symlink():
            raise ClusterError(f"refusing to operate on symlinked cluster: {name}")
        if not target.is_dir():
            raise ClusterError(f"cluster not found: {name}")
        return target

    def reset(self, name: str) -> Path:
        """Remove generated world saves while keeping all cluster settings."""
        target = self._existing_cluster(name)
        for shard in self.shards:
            save_dir = target / shard / "save"
            if save_dir.is_symlink():
                raise ClusterError(f"refusing to remove symlinked save directory: {save_dir}")
            if save_dir.exists() and not save_dir.is_dir():
                raise ClusterError(f"save path is not a directory: {save_dir}")

        # Validate every shard before removing any save directory, so a bad
        # path cannot leave a partially reset cluster.
        for shard in self.shards:
            save_dir = target / shard / "save"
            if save_dir.is_dir():
                shutil.rmtree(save_dir)
        return target

    def delete(self, name: str) -> Path:
        """Delete a non-active cluster directory and everything in it."""
        name = self.validate_name(name)
        if name == self.active_name():
            raise ClusterError("cannot delete the active cluster; switch to another cluster first")
        target = self._existing_cluster(name)
        shutil.rmtree(target)
        return target

    @staticmethod
    def _copy_cluster_ini(source: Path, target: Path, name: str) -> None:
        content = source.read_text(encoding="utf-8", errors="replace")
        pattern = re.compile(r"^(\s*cluster_name\s*=\s*).*$", re.MULTILINE | re.IGNORECASE)
        content, count = pattern.subn(lambda match: f"{match.group(1)}{name}", content)
        if count:
            target.write_text(content, encoding="utf-8")
        else:
            shutil.copy2(source, target)

    def _copy_shard(self, source: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=False)
        if not source.is_dir():
            return
        for child in source.iterdir():
            if child.name in WORLD_DIR_NAMES or child.name.startswith("server_log.txt"):
                continue
            destination = target / child.name
            if child.name == "modoverrides.lua":
                result = load_shard_overrides(
                    source.name, child, lua_command=self.lua_command
                )
                if not result.ok:
                    raise ClusterError(
                        f"cannot create cluster: {child} could not be parsed ({result.error})"
                    )
                disabled = {
                    key: OverrideEntry(
                        enabled=False,
                        configuration_options=dict(entry.configuration_options),
                    )
                    for key, entry in result.entries.items()
                }
                write_file_atomic(destination, render_overrides(disabled))
            elif child.is_dir():
                shutil.copytree(child, destination)
            else:
                shutil.copy2(child, destination)

    def create_from_active(self, name: str) -> Path:
        name = self.validate_name(name)
        source = self.active_path()
        if not source.is_dir():
            raise ClusterError(f"active cluster directory not found: {source}")
        target = self.path_for(name)
        if target.exists():
            raise ClusterError(f"cluster already exists: {name}")
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            target.mkdir()
            for child in source.iterdir():
                if child.name in self.shards:
                    continue
                destination = target / child.name
                if child.name == "cluster.ini" and child.is_file():
                    self._copy_cluster_ini(child, destination, name)
                elif child.is_dir():
                    shutil.copytree(child, destination)
                else:
                    shutil.copy2(child, destination)
            for shard in self.shards:
                self._copy_shard(source / shard, target / shard)
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return target
