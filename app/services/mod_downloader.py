"""Download workshop mods directly with steamcmd.

DST workshop items can be fetched with an anonymous steamcmd login:

    steamcmd +force_install_dir <staging> +login anonymous \
             +workshop_download_item 322330 <id> +quit

The item lands in <staging>/steamapps/workshop/content/322330/<id> and is
then copied into mods_path. This lets an operator add a mod, configure it,
and restart the DST server once — instead of restart-to-download, configure,
restart again.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DST_APP_ID = "322330"


@dataclass
class DownloadResult:
    ok: bool
    message: str
    installed_path: Path | None = None
    output_tail: str = ""


def find_steamcmd(configured: str = "") -> str | None:
    """Resolve the steamcmd executable, or None when unavailable."""
    candidate = configured.strip() or "steamcmd"
    return shutil.which(candidate)


def install_dir(mods_path: Path, workshop_id: str) -> Path:
    """Folder the mod should be installed to, following the layout of
    mods_path: the classic mods/ folder uses workshop-<id>, while an
    ugc_mods content/322330 folder uses bare numeric ids."""
    if mods_path.name == DST_APP_ID:
        return mods_path / workshop_id
    return mods_path / f"workshop-{workshop_id}"


def _find_archive(content: Path) -> Path | None:
    """Newer DST workshop uploads arrive from steamcmd as a single zip
    archive, usually named ``<manifest>_legacy.bin`` — the game's own
    downloader unpacks it, so we must too."""
    for entry in sorted(content.iterdir()):
        if entry.is_file() and entry.suffix.lower() in (".bin", ".zip") and zipfile.is_zipfile(entry):
            return entry
    return None


def _flatten_single_dir(root: Path) -> None:
    """If an archive wrapped the mod in one top-level folder, hoist its
    contents so modinfo.lua sits directly inside the mod folder."""
    entries = list(root.iterdir())
    if (
        len(entries) == 1
        and entries[0].is_dir()
        and not (root / "modinfo.lua").exists()
    ):
        inner = entries[0]
        for item in list(inner.iterdir()):
            shutil.move(str(item), str(root / item.name))
        inner.rmdir()


def download_mod(
    workshop_id: str,
    mods_path: Path,
    configured_cmd: str = "",
    timeout: float = 900.0,
    on_output: Callable[[str], None] | None = None,
) -> DownloadResult:
    """Download one workshop item and install it into mods_path.

    An existing mod folder is replaced (this doubles as a manual update).
    ``on_output`` receives steamcmd's output line by line, for live progress
    display. Never raises for expected failures; inspect DownloadResult.
    """
    steamcmd = find_steamcmd(configured_cmd)
    if steamcmd is None:
        return DownloadResult(
            ok=False,
            message="steamcmd not found — install it (e.g. 'sudo apt install steamcmd') "
            "or set steamcmd.command in config.yaml",
        )
    if not mods_path.is_dir():
        return DownloadResult(ok=False, message=f"mods_path does not exist: {mods_path}")

    staging = Path(tempfile.mkdtemp(prefix="dst-mod-manager-steamcmd-"))
    try:
        cmd = [
            steamcmd,
            "+force_install_dir", str(staging),
            "+login", "anonymous",
            "+workshop_download_item", DST_APP_ID, workshop_id,
            "+quit",
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            return DownloadResult(ok=False, message=f"failed to run steamcmd: {exc}")

        # Stream output line by line for live progress; a timer enforces the
        # timeout even while we're blocked reading.
        timed_out = False

        def _kill_on_timeout() -> None:
            nonlocal timed_out
            timed_out = True
            proc.kill()

        timer = threading.Timer(timeout, _kill_on_timeout)
        timer.start()
        output_lines: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                output_lines.append(line)
                if on_output is not None:
                    on_output(line.rstrip())
            returncode = proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
        if timed_out:
            return DownloadResult(ok=False, message=f"steamcmd timed out after {timeout:.0f}s")

        # steamcmd's exit code is unreliable; the downloaded directory is the
        # source of truth.
        content = staging / "steamapps" / "workshop" / "content" / DST_APP_ID / workshop_id
        if not content.is_dir() or not any(content.iterdir()):
            tail = "".join(output_lines).strip()[-600:]
            return DownloadResult(
                ok=False,
                message=(
                    f"steamcmd did not produce mod {workshop_id} "
                    f"(exit code {returncode}) — is the workshop id correct?"
                ),
                output_tail=tail,
            )
        if on_output is not None:
            on_output("Download finished — installing into the mods folder…")

        dest = install_dir(mods_path, workshop_id)
        tmp_dest = mods_path / f".{dest.name}.downloading"
        note = ""
        try:
            if tmp_dest.exists():
                shutil.rmtree(tmp_dest)
            if (content / "modinfo.lua").is_file():
                # Classic layout: the item already is a plain mod folder.
                shutil.copytree(content, tmp_dest)
            else:
                archive = _find_archive(content)
                if archive is not None:
                    tmp_dest.mkdir()
                    with zipfile.ZipFile(archive) as zf:
                        zf.extractall(tmp_dest)  # extract() sanitizes member paths
                    _flatten_single_dir(tmp_dest)
                    note = f", extracted from {archive.name}"
                else:
                    # Unknown layout: install as-is so the user can inspect it.
                    shutil.copytree(content, tmp_dest)
            if not (tmp_dest / "modinfo.lua").is_file():
                note += " — warning: no modinfo.lua found in the downloaded content"
            if dest.exists():
                shutil.rmtree(dest)  # replace = manual update
            tmp_dest.rename(dest)
        except (OSError, zipfile.BadZipFile) as exc:
            shutil.rmtree(tmp_dest, ignore_errors=True)
            return DownloadResult(ok=False, message=f"failed to install into mods_path: {exc}")

        return DownloadResult(
            ok=True, message=f"installed as {dest.name}{note}", installed_path=dest
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
