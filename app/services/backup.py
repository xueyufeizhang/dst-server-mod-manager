"""Operation-grouped backups.

Every write operation (save / add mod / remove mod / restore) opens one
*backup session*; each file it is about to overwrite is copied into that
session's directory first. One session == one record on the Backups page,
no matter how many shards the operation touched.

Layout:
    backups/
    └── 20260702-104512/
        ├── meta.json                            # created / reason / files
        ├── Master__modoverrides.lua
        ├── Caves__modoverrides.lua
        └── ModSetup__dedicated_server_mods_setup.lua

``keep_last`` counts sessions (operations), not individual files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from app.models import BackupFileInfo, BackupSessionInfo

SESSION_ID_RE = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")
META_NAME = "meta.json"


class BackupError(RuntimeError):
    pass


class BackupSession:
    """Lazily-created collector for one operation's pre-write copies.

    No directory is created until the first successful backup_file call, so
    operations that end up overwriting nothing leave no empty records.
    """

    def __init__(self, manager: "BackupManager", reason: str) -> None:
        self._manager = manager
        self.reason = reason
        self.session_id: str | None = None
        self._dir: Path | None = None
        self._files: dict[str, str] = {}
        self._created: datetime | None = None

    @property
    def has_backups(self) -> bool:
        return bool(self._files)

    def backup_file(self, key: str, source: Path) -> Path | None:
        """Copy `source` into this session under `key` (shard name or
        "ModSetup"). Returns None when the source doesn't exist yet."""
        if not source.is_file():
            return None
        if key in self._files and self._dir is not None:
            # Same file already captured by this operation.
            return self._dir / self._files[key]
        if self._dir is None:
            self.session_id, self._dir, self._created = self._manager._create_session_dir()
        dest_name = f"{key}__{source.name}"
        dest = self._dir / dest_name
        shutil.copy2(source, dest)
        self._files[key] = dest_name
        self._write_meta()
        self._manager._prune()
        return dest

    def _write_meta(self) -> None:
        assert self._dir is not None
        meta = {
            "created": (self._created or datetime.now()).isoformat(timespec="seconds"),
            "reason": self.reason,
            "files": self._files,
        }
        (self._dir / META_NAME).write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )


class BackupManager:
    def __init__(self, directory: Path, keep_last: int = 20) -> None:
        self.directory = directory
        self.keep_last = keep_last

    def new_session(self, reason: str) -> BackupSession:
        return BackupSession(self, reason)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _create_session_dir(self) -> tuple[str, Path, datetime]:
        self.directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        base = now.strftime("%Y%m%d-%H%M%S")
        session_id, counter = base, 1
        while (self.directory / session_id).exists():  # same-second operations
            counter += 1
            session_id = f"{base}-{counter}"
        session_dir = self.directory / session_id
        session_dir.mkdir()
        return session_id, session_dir, now

    def _session_dirs(self) -> list[Path]:
        """Session directories sorted oldest -> newest (ids sort naturally)."""
        if not self.directory.is_dir():
            return []
        return sorted(
            p
            for p in self.directory.iterdir()
            if p.is_dir() and SESSION_ID_RE.match(p.name)
        )

    def _load_session(self, path: Path) -> BackupSessionInfo:
        meta: dict = {}
        try:
            meta = json.loads((path / META_NAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

        files_map = meta.get("files")
        if not isinstance(files_map, dict):
            # meta.json missing/corrupt: reconstruct from the "key__name"
            # file naming convention.
            files_map = {
                f.name.split("__", 1)[0]: f.name
                for f in path.iterdir()
                if f.is_file() and "__" in f.name
            }

        files: list[BackupFileInfo] = []
        for key in sorted(files_map):
            file_path = path / str(files_map[key])
            if not file_path.is_file():
                continue
            files.append(
                BackupFileInfo(
                    key=str(key),
                    filename=file_path.name,
                    path=file_path,
                    size=file_path.stat().st_size,
                )
            )

        try:
            created = datetime.fromisoformat(str(meta.get("created")))
        except (TypeError, ValueError):
            created = datetime.fromtimestamp(path.stat().st_mtime)

        return BackupSessionInfo(
            session_id=path.name,
            created=created,
            reason=str(meta.get("reason") or "unknown"),
            files=files,
        )

    @staticmethod
    def _validate_id(session_id: str) -> None:
        # Strict format check doubles as path-traversal protection.
        if not SESSION_ID_RE.match(session_id):
            raise BackupError(f"invalid backup id: {session_id!r}")

    def _prune(self) -> None:
        if self.keep_last <= 0:  # 0 or negative disables pruning
            return
        dirs = self._session_dirs()
        for stale in dirs[: -self.keep_last]:
            shutil.rmtree(stale, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # Public queries / actions
    # ------------------------------------------------------------------ #

    def list_sessions(self) -> list[BackupSessionInfo]:
        """All backup records, newest first."""
        return [self._load_session(p) for p in reversed(self._session_dirs())]

    def get_session(self, session_id: str) -> BackupSessionInfo:
        self._validate_id(session_id)
        path = self.directory / session_id
        if not path.is_dir():
            raise BackupError(f"backup not found: {session_id}")
        return self._load_session(path)

    def find_next_backup(
        self, session_id: str, key: str
    ) -> tuple[str, BackupFileInfo] | None:
        """The same file's copy in the oldest session NEWER than session_id,
        i.e. the state the file had after this session's operation (as
        captured by the next backed-up operation)."""
        self._validate_id(session_id)
        for path in self._session_dirs():
            if path.name <= session_id:
                continue
            info = self._load_session(path)
            for f in info.files:
                if f.key == key:
                    return info.session_id, f
        return None

    def restore_file(self, session_id: str, key: str, target: Path) -> None:
        """Atomically copy one backed-up file over `target`. The caller is
        responsible for backing up the current target first."""
        info = self.get_session(session_id)
        source = next((f for f in info.files if f.key == key), None)
        if source is None:
            raise BackupError(f"backup {session_id} contains no file for {key}")

        fd, tmp_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            shutil.copyfile(source.path, tmp_path)
            os.replace(tmp_path, target)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def delete_session(self, session_id: str) -> None:
        self._validate_id(session_id)
        path = self.directory / session_id
        if not path.is_dir():
            raise BackupError(f"backup not found: {session_id}")
        shutil.rmtree(path)
