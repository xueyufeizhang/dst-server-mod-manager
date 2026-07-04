"""FastAPI application: routes, auth, and page rendering.

Run with:  python -m app.main [config.yaml]
or:        uvicorn --factory app.main:create_app

The app is built by a factory so no config is loaded at import time; state
that must survive between requests (last restart output) lives on app.state.
This assumes a single uvicorn worker, which is the intended deployment.
"""

from __future__ import annotations

import difflib
import hashlib
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import CONFIG_ENV_VAR, AppConfig, ConfigError, load_config
from app.models import Mod, OverrideEntry, ShardOverrides
from app.services import mod_scanner
from app.services.auth import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    derive_key,
    make_token,
    verify_token,
)
from app.services.backup import BackupError, BackupManager, BackupSession
from app.services.cluster_info import read_cluster_info
from app.services.download_jobs import DownloadManager
from app.services.logs import tail_file
from app.services.lua_runner import (
    CHECK_LUA_SYNTAX_SCRIPT,
    PARSE_LUA_TABLE_SCRIPT,
    LuaError,
    LuaNotFoundError,
    find_lua_command,
    run_lua_json,
)
from app.services.mod_downloader import download_mod, find_steamcmd
from app.services.mod_scanner import ModsPathError, scan_mods
from app.services.mod_setup import (
    add_mod_to_setup,
    read_setup_ids,
    remove_mod_from_setup,
)
from app.services.overrides_parser import load_shard_overrides
from app.services.overrides_writer import render_overrides, write_file_atomic
from app.services.server_control import run_command
from app.services.system_info import gather as gather_system_info, human_bytes
from app.viewmodels import (
    ModCardVM,
    build_config_from_form,
    build_mod_card,
    build_pending_mods,
    enabled_field_name,
)

logger = logging.getLogger("dst_mod_manager")

APP_DIR = Path(__file__).resolve().parent

RESTART_HINT = "Restart the DST server for the changes to take effect."

# Pseudo-shard name used for dedicated_server_mods_setup.lua backups.
MODS_SETUP_BACKUP_KEY = "ModSetup"


def _write_overrides_if_changed(
    session: "BackupSession", key: str, path: Path, entries: dict[str, OverrideEntry]
) -> bool:
    """Back up and write a shard's modoverrides.lua only when the rendered
    content actually differs from what is on disk. Returns True when written.

    This keeps backup records honest: a record only ever contains files the
    operation really changed, and an operation that changes nothing creates
    no record at all (sessions are lazy)."""
    content = render_overrides(entries)
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass  # unreadable existing file: fall through and try the write
    session.backup_file(key, path)
    write_file_atomic(path, content)
    return True


def _extract_workshop_id(raw: str) -> str | None:
    """Accept a bare id, a workshop-<id> key, or a Steam Workshop URL."""
    s = raw.strip()
    if re.fullmatch(r"\d+", s):
        return s
    match = re.fullmatch(r"workshop-(\d+)", s)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=(\d+)", s)
    if match:
        return match.group(1)
    return None


def _redirect_flash(url: str, message: str, level: str = "success") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    query = urlencode({"msg": message, "level": level})
    return RedirectResponse(f"{url}{sep}{query}", status_code=303)


def _load_all_overrides(cfg: AppConfig) -> dict[str, ShardOverrides]:
    return {
        shard: load_shard_overrides(
            shard, cfg.dst.overrides_path(shard), lua_command=cfg.lua_command
        )
        for shard in cfg.dst.shards
    }


def _config_warnings(cfg: AppConfig, overrides: dict[str, ShardOverrides]) -> list[str]:
    warnings: list[str] = []
    if not cfg.dst.mods_path.is_dir():
        warnings.append(f"mods_path does not exist: {cfg.dst.mods_path}")
    if cfg.dst.local_mods_path is not None and not cfg.dst.local_mods_path.is_dir():
        warnings.append(f"local_mods_path does not exist: {cfg.dst.local_mods_path}")
    if not cfg.dst.cluster_path.is_dir():
        warnings.append(f"cluster_path does not exist: {cfg.dst.cluster_path}")
    for shard in cfg.dst.shards:
        if not cfg.dst.shard_dir(shard).is_dir():
            warnings.append(f"shard directory does not exist: {cfg.dst.shard_dir(shard)}")
    for shard, result in overrides.items():
        if not result.ok:
            warnings.append(f"{shard}/modoverrides.lua could not be parsed: {result.error}")
    try:
        find_lua_command(cfg.lua_command)
    except LuaNotFoundError as exc:
        warnings.append(str(exc))
    if cfg.security.enable_basic_auth and cfg.security.password in ("", "changeme"):
        warnings.append("security.password is empty or still the default 'changeme' — change it in config.yaml")
    if cfg.server.host not in ("127.0.0.1", "localhost", "::1"):
        warnings.append(
            f"server.host is {cfg.server.host!r} — the panel is reachable beyond localhost; "
            "prefer 127.0.0.1 plus an SSH tunnel"
        )
    return warnings


def _safe_scan(cfg: AppConfig, use_cache: bool = True) -> tuple[list[Mod], str]:
    """Scan mods, returning (mods, error_message)."""
    try:
        return (
            scan_mods(
                cfg.dst.mods_path,
                local_mods_path=cfg.dst.local_mods_path,
                lua_command=cfg.lua_command,
                use_cache=use_cache,
            ),
            "",
        )
    except ModsPathError as exc:
        return [], str(exc)


def create_app() -> FastAPI:
    cfg = load_config()

    app = FastAPI(
        title="DST Mod Manager",
        # This is a page-only app; don't expose interactive API docs.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Only the most recent control command is kept for display.
    app.state.last_command: dict[str, Any] | None = None

    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=APP_DIR / "templates")

    backups = BackupManager(cfg.backup.directory, cfg.backup.keep_last)
    downloads = DownloadManager()

    def _start_download(workshop_id: str) -> tuple[bool, str]:
        """Kick off a background steamcmd download for status polling."""
        def runner(on_output):  # type: ignore[no-untyped-def]
            return download_mod(
                workshop_id,
                cfg.dst.mods_path,
                cfg.steamcmd.command,
                cfg.steamcmd.timeout,
                on_output=on_output,
            )

        return downloads.start(workshop_id, runner)

    # Cache-buster for CSS/JS links so style changes reach browsers without
    # a hard refresh; based on the newest static file's mtime.
    try:
        static_version = str(
            max(int(p.stat().st_mtime) for p in (APP_DIR / "static").iterdir() if p.is_file())
        )
    except (ValueError, OSError):
        static_version = "1"

    def render(request: Request, name: str, context: dict[str, Any]) -> HTMLResponse:
        context.setdefault("cfg", cfg)
        context.setdefault("shards", cfg.dst.shards)
        context.setdefault("static_version", static_version)
        context.setdefault("auth_enabled", cfg.security.enable_basic_auth)
        return templates.TemplateResponse(request, name, context)

    # ------------------------------------------------------------------ #
    # Login page + session cookie auth
    # ------------------------------------------------------------------ #
    auth_enabled = cfg.security.enable_basic_auth
    session_key = derive_key(cfg.security.username, cfg.security.password)

    def _session_ok(request: Request) -> bool:
        return verify_token(session_key, request.cookies.get(COOKIE_NAME))

    @app.middleware("http")
    async def require_login(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not auth_enabled:
            return await call_next(request)
        path = request.url.path
        if path == "/login" or path.startswith("/static/"):
            return await call_next(request)
        if _session_ok(request):
            return await call_next(request)
        # Background pollers (fetch with Accept: application/json) get a
        # clean 401 instead of a redirect-to-HTML they can't parse.
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"detail": "not authenticated"}, status_code=401)
        target = "/login"
        if request.method == "GET" and path != "/":
            target += "?next=" + quote(path, safe="/")
        return RedirectResponse(target, status_code=303)

    def _safe_next(raw: str) -> str:
        # Internal paths only — no open redirects.
        return raw if raw.startswith("/") and not raw.startswith("//") else "/"

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/") -> Any:
        if not auth_enabled or _session_ok(request):
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html", {"error": "", "next": _safe_next(next)})

    @app.post("/login")
    async def login_submit(request: Request) -> Any:
        form = await request.form()
        username = str(form.get("username") or "")
        password = str(form.get("password") or "")
        next_url = _safe_next(str(form.get("next") or "/"))
        user_ok = secrets.compare_digest(
            username.encode("utf-8"), cfg.security.username.encode("utf-8")
        )
        pass_ok = secrets.compare_digest(
            password.encode("utf-8"), cfg.security.password.encode("utf-8")
        )
        if not (user_ok and pass_ok):
            logger.warning("failed login attempt (user %r)", username[:40])
            return render(request, "login.html", {
                "error": "Invalid username or password.",
                "next": next_url,
            })
        response = RedirectResponse(next_url, status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            make_token(session_key),
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            path="/",
        )
        logger.info("login ok")
        return response

    @app.post("/logout")
    def logout() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    def _unified_field_shards() -> tuple[bool, list[str]]:
        """(unified?, shards that get form fields/columns). In unified mode
        all controls bind to the primary shard and apply everywhere."""
        unified = cfg.dst.unified_mod_config and len(cfg.dst.shards) > 1
        return unified, ([cfg.dst.shards[0]] if unified else cfg.dst.shards)

    # ------------------------------------------------------------------ #
    # Dashboard
    # ------------------------------------------------------------------ #
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        overrides = _load_all_overrides(cfg)
        mods, scan_error = _safe_scan(cfg)

        try:
            lua_command = find_lua_command(cfg.lua_command)
        except LuaNotFoundError:
            lua_command = ""

        # Compact server summary for the control panel.
        cluster = read_cluster_info(cfg.dst.cluster_path, cfg.dst.shards)
        cluster_summary = dict(cluster.cluster.summary)
        system = gather_system_info([cfg.dst.cluster_path, cfg.dst.mods_path])
        host_bits: list[str] = []
        if system.memory:
            host_bits.append(f"RAM {system.memory.percent}%")
        for disk in system.disks[:1]:
            host_bits.append(f"disk {disk.percent}%")
        if system.load:
            host_bits.append(f"load {system.load}")
        if system.uptime:
            host_bits.append(f"up {system.uptime}")

        return render(request, "index.html", {
            "active_page": "dashboard",
            "warnings": _config_warnings(cfg, overrides),
            "scan_error": scan_error,
            "mods_total": len(mods),
            "mods_failed": sum(1 for m in mods if not m.parse_ok),
            "overrides": overrides,
            "backups_count": len(backups.list_sessions()),
            "lua_command": lua_command,
            "steamcmd_command": find_steamcmd(cfg.steamcmd.command),
            "last_command": app.state.last_command,
            "unified": _unified_field_shards()[0],
            "cluster_name": cluster.cluster_name,
            "cluster_summary": cluster_summary,
            "host_line": " · ".join(host_bits),
        })

    # ------------------------------------------------------------------ #
    # Mods list + save
    # ------------------------------------------------------------------ #
    @app.get("/mods", response_class=HTMLResponse)
    def mods_page(request: Request, refresh: int = 0) -> HTMLResponse:
        mods, scan_error = _safe_scan(cfg, use_cache=not refresh)
        overrides = _load_all_overrides(cfg)

        # The download list (dedicated_server_mods_setup.lua) is part of the
        # mod state too — read it so restores/edits of that file show up
        # here. None = file absent/unreadable: skip the sync badges rather
        # than mislabeling every mod.
        setup_ids: set[str] | None = None
        try:
            if cfg.dst.mods_setup_path.is_file():
                setup_ids = read_setup_ids(cfg.dst.mods_setup_path)
        except OSError:
            pass

        unified, field_shards = _unified_field_shards()
        cards = [
            build_mod_card(mod, cfg.dst.shards, overrides, setup_ids, field_shards)
            for mod in mods
        ]
        shard_errors = {s: o.error for s, o in overrides.items() if not o.ok}
        not_downloaded = build_pending_mods(
            cfg.dst.shards, overrides, {m.workshop_id for m in mods}, setup_ids
        )
        # "Pending download" strictly means the server WILL download it —
        # i.e. it is in dedicated_server_mods_setup.lua. Entries referenced
        # only by modoverrides go to a separate "orphaned" section, so
        # restoring/editing the download list is immediately visible here.
        pending = [p for p in not_downloaded if p.in_setup]
        orphaned = [p for p in not_downloaded if not p.in_setup]
        return render(request, "mods.html", {
            "active_page": "mods",
            "cards": cards,
            "pending": pending,
            "orphaned": orphaned,
            "scan_error": scan_error,
            "shard_errors": shard_errors,
            "steamcmd_found": find_steamcmd(cfg.steamcmd.command) is not None,
            "unified": unified,
            "field_shards": field_shards,
        })

    @app.get("/mods/{workshop_id}", response_class=HTMLResponse)
    def mod_detail(request: Request, workshop_id: str) -> HTMLResponse:
        mods, scan_error = _safe_scan(cfg)
        mod = next((m for m in mods if m.workshop_id == workshop_id), None)
        if mod is None:
            raise HTTPException(status_code=404, detail=f"mod not found: {workshop_id} ({scan_error})")
        overrides = _load_all_overrides(cfg)
        unified, field_shards = _unified_field_shards()
        card = build_mod_card(mod, cfg.dst.shards, overrides, None, field_shards)
        return render(request, "mod_detail.html", {
            "active_page": "mods",
            "card": card,
            "mod": mod,
            "overrides": overrides,
            "unified": unified,
            "field_shards": field_shards,
        })

    @app.post("/mods/save")
    async def save_mods(request: Request) -> RedirectResponse:
        form = await request.form()
        mod_ids = [str(v) for v in form.getlist("mod_ids")]
        if not mod_ids:
            return _redirect_flash("/mods", "nothing to save (no mods submitted)", "error")

        mods, scan_error = _safe_scan(cfg)
        if scan_error:
            return _redirect_flash("/mods", f"cannot save: {scan_error}", "error")
        mods_by_id = {m.workshop_id: m for m in mods}

        saved: list[str] = []
        unchanged: list[str] = []
        errors: list[str] = []
        session = backups.new_session("save")
        for shard in cfg.dst.shards:
            # Unified mode: the form only carries fields for the primary
            # shard; every shard is written from those same values.
            field_shard = cfg.dst.shards[0] if cfg.dst.unified_mod_config else shard
            overrides_path = cfg.dst.overrides_path(shard)
            current = load_shard_overrides(shard, overrides_path, lua_command=cfg.lua_command)
            if not current.ok:
                # Never overwrite a file we could not read back.
                errors.append(f"{shard}: refused to save — current modoverrides.lua is unreadable ({current.error})")
                continue

            # Start from the existing entries so mods that are configured but
            # no longer on disk (or not shown in the form) are preserved.
            entries: dict[str, OverrideEntry] = dict(current.entries)
            for workshop_id in mod_ids:
                if not workshop_id.isdigit():
                    continue
                mod = mods_by_id.get(workshop_id)
                if mod is None:
                    # Pending mod (not downloaded yet): only the enabled flag
                    # is editable, config is kept as-is. A mod that has no
                    # overrides entry (listed only in the download list) and
                    # was left unticked gets none invented for it.
                    key = f"workshop-{workshop_id}"
                    enabled = enabled_field_name(field_shard, workshop_id) in form
                    old_pending = entries.get(key)
                    if old_pending is None and not enabled:
                        continue
                    entries[key] = OverrideEntry(
                        enabled=enabled,
                        configuration_options=(
                            dict(old_pending.configuration_options) if old_pending else {}
                        ),
                    )
                    continue
                old = entries.get(mod.override_key, OverrideEntry())
                enabled = enabled_field_name(field_shard, workshop_id) in form
                if mod.parse_ok:
                    config = build_config_from_form(form, field_shard, mod, old)
                else:
                    # Unparsable modinfo: only the enabled flag is editable;
                    # keep whatever configuration already exists.
                    config = dict(old.configuration_options)
                entries[mod.override_key] = OverrideEntry(enabled=enabled, configuration_options=config)

            try:
                if _write_overrides_if_changed(session, shard, overrides_path, entries):
                    saved.append(shard)
                    logger.info("saved %s", overrides_path)
                else:
                    unchanged.append(shard)
            except (OSError, BackupError, ValueError, TypeError) as exc:
                errors.append(f"{shard}: save failed — {exc}")
                continue

        backup_note = f" Backup: {session.session_id}." if session.has_backups else ""
        unchanged_note = f" ({', '.join(unchanged)} unchanged)" if unchanged and saved else ""
        if saved and not errors:
            message = f"Saved {', '.join(saved)}{unchanged_note}.{backup_note} {RESTART_HINT}"
            level = "success"
        elif saved:
            message = (
                f"Saved {', '.join(saved)}{unchanged_note}.{backup_note} {RESTART_HINT} "
                f"Errors: {'; '.join(errors)}"
            )
            level = "error"
        elif errors:
            message = f"Nothing saved. Errors: {'; '.join(errors)}"
            level = "error"
        else:
            message = "No changes detected — nothing was saved and no backup was created."
            level = "success"
        return _redirect_flash("/mods", message, level)

    # ------------------------------------------------------------------ #
    # Raw file viewer / editor
    # ------------------------------------------------------------------ #
    def _file_targets() -> dict[str, Path]:
        """The managed files, keyed like backups (shard names + ModSetup)."""
        targets = {shard: cfg.dst.overrides_path(shard) for shard in cfg.dst.shards}
        targets[MODS_SETUP_BACKUP_KEY] = cfg.dst.mods_setup_path
        return targets

    def _validate_lua_content(key: str, content: str) -> str:
        """Validate edited content before it may touch the real file.
        Returns an error message, or '' when the content is acceptable."""
        fd, tmp_name = tempfile.mkstemp(suffix=".lua", prefix="dst-mm-edit-")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            tmp.write_text(content, encoding="utf-8")
            if key == MODS_SETUP_BACKUP_KEY:
                # The setup file calls engine functions, so only compile-check it.
                result = run_lua_json(
                    CHECK_LUA_SYNTAX_SCRIPT, [str(tmp)], lua_command=cfg.lua_command
                )
            else:
                # modoverrides.lua must execute and return a table.
                result = run_lua_json(
                    PARSE_LUA_TABLE_SCRIPT, [str(tmp)], lua_command=cfg.lua_command
                )
                if result.get("ok") and not isinstance(result.get("data"), dict):
                    return "the file must return a table, e.g. `return {}`"
            if not result.get("ok"):
                # The Lua error mentions our temp file; that path is noise.
                error = str(result.get("error") or "invalid Lua")
                return error.replace(str(tmp), "edited content")
            return ""
        except (LuaError, OSError) as exc:
            return str(exc).replace(str(tmp), "edited content")
        finally:
            tmp.unlink(missing_ok=True)

    @app.get("/files", response_class=HTMLResponse)
    def files_page(request: Request) -> HTMLResponse:
        files = []
        for key, path in _file_targets().items():
            exists = path.is_file()
            content = ""
            error = ""
            mtime = None
            if exists:
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    mtime = datetime.fromtimestamp(path.stat().st_mtime)
                except OSError as exc:
                    error = str(exc)
            files.append({
                "key": key,
                "path": path,
                "exists": exists,
                "content": content,
                # Fingerprint of what the user sees, for conflict detection.
                "hash": hashlib.md5(content.encode("utf-8")).hexdigest(),
                "mtime": mtime,
                "error": error,
            })
        return render(request, "files.html", {"active_page": "files", "files": files})

    @app.post("/files/save")
    async def files_save(request: Request) -> RedirectResponse:
        form = await request.form()
        key = str(form.get("key") or "")
        targets = _file_targets()
        if key not in targets:
            return _redirect_flash("/files", f"unknown file: {key}", "error")
        path = targets[key]

        # Textareas submit CRLF line endings; the files on disk use LF.
        content = str(form.get("content") or "").replace("\r\n", "\n").replace("\r", "\n")
        if content and not content.endswith("\n"):
            content += "\n"

        current = ""
        if path.is_file():
            try:
                current = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return _redirect_flash("/files", f"{key}: cannot read current file: {exc}", "error")
        current_hash = hashlib.md5(current.encode("utf-8")).hexdigest()
        if str(form.get("base_hash") or "") != current_hash:
            return _redirect_flash(
                "/files",
                f"{key}: not saved — the file changed on disk after you opened this page "
                "(another save or manual edit). Reload and re-apply your changes.",
                "error",
            )
        if content == current:
            return _redirect_flash("/files", f"{key}: no changes to save.")

        validation_error = _validate_lua_content(key, content)
        if validation_error:
            return _redirect_flash(
                "/files", f"{key}: not saved — Lua validation failed: {validation_error}", "error"
            )

        session = backups.new_session(f"edit {key}")
        try:
            session.backup_file(key, path)
            write_file_atomic(path, content)
        except (OSError, BackupError) as exc:
            return _redirect_flash("/files", f"{key}: save failed — {exc}", "error")
        backup_note = f" Backup: {session.session_id}." if session.has_backups else ""
        logger.info("edited %s via files page", path)
        return _redirect_flash("/files", f"Saved {key} ({path.name}).{backup_note} {RESTART_HINT}")

    # ------------------------------------------------------------------ #
    # Add / remove a mod by workshop id
    # ------------------------------------------------------------------ #
    @app.post("/mods/add")
    async def add_mod(request: Request) -> RedirectResponse:
        form = await request.form()
        raw = str(form.get("workshop_input") or "")
        workshop_id = _extract_workshop_id(raw)
        if workshop_id is None:
            return _redirect_flash(
                "/mods", f"could not extract a workshop id from {raw!r}", "error"
            )
        selected = [str(s) for s in form.getlist("add_shards")]
        if "__all__" in selected:  # unified mode: one checkbox = every shard
            selected = list(cfg.dst.shards)
        else:
            selected = [s for s in selected if s in cfg.dst.shards]
        key = f"workshop-{workshop_id}"

        notes: list[str] = []
        errors: list[str] = []
        session = backups.new_session(f"add workshop-{workshop_id}")

        # 1. Make the server download the mod at next boot.
        setup_path = cfg.dst.mods_setup_path
        try:
            if workshop_id in read_setup_ids(setup_path):
                notes.append(f"already listed in {setup_path.name}")
            else:
                session.backup_file(MODS_SETUP_BACKUP_KEY, setup_path)
                add_mod_to_setup(setup_path, workshop_id)
                notes.append(f"added ServerModSetup to {setup_path.name}")
        except OSError as exc:
            errors.append(f"{setup_path.name}: {exc}")

        # 2. Enable it in the selected shards' modoverrides.lua.
        for shard in selected:
            overrides_path = cfg.dst.overrides_path(shard)
            current = load_shard_overrides(shard, overrides_path, lua_command=cfg.lua_command)
            if not current.ok:
                errors.append(f"{shard}: modoverrides.lua unreadable ({current.error})")
                continue
            entries = dict(current.entries)
            old = entries.get(key)
            entries[key] = OverrideEntry(
                enabled=True,
                configuration_options=dict(old.configuration_options) if old else {},
            )
            try:
                if _write_overrides_if_changed(session, shard, overrides_path, entries):
                    notes.append(f"enabled in {shard}")
                else:
                    notes.append(f"already enabled in {shard}")
            except (OSError, BackupError) as exc:
                errors.append(f"{shard}: {exc}")
                continue

        # Optional immediate download (runs in the background; the mods page
        # polls its progress), so options can be configured right away and
        # one restart applies mod + settings together.
        download_started = False
        if str(form.get("action") or "add") == "add_download" and not errors:
            download_started, start_error = _start_download(workshop_id)
            if download_started:
                notes.append("download started")
            else:
                errors.append(f"download not started: {start_error}")

        mods, _ = _safe_scan(cfg)
        downloaded = any(m.workshop_id == workshop_id for m in mods)
        if download_started:
            tail = "Download progress is shown below; the mod appears here when it finishes."
        elif downloaded:
            tail = (
                "It is downloaded — configure its options below; a single server "
                "restart applies the mod and its settings together."
            )
        else:
            tail = "Restart the DST server to download it; it will show its name and options here once downloaded."
        logger.info("add mod %s: %s / errors: %s", key, notes, errors)
        if errors:
            return _redirect_flash(
                "/mods", f"{key}: {'; '.join(notes)}. Errors: {'; '.join(errors)}", "error"
            )
        return _redirect_flash("/mods", f"Added {key} ({'; '.join(notes)}). {tail}")

    @app.post("/mods/download")
    async def download_mod_route(request: Request) -> RedirectResponse:
        form = await request.form()
        workshop_id = _extract_workshop_id(str(form.get("workshop_id") or ""))
        if workshop_id is None:
            return _redirect_flash("/mods", "no workshop id given", "error")
        started, start_error = _start_download(workshop_id)
        logger.info("download workshop-%s: started=%s %s", workshop_id, started, start_error)
        if not started:
            return _redirect_flash("/mods", f"download not started: {start_error}", "error")
        return _redirect_flash(
            "/mods",
            f"Download of workshop-{workshop_id} started — progress is shown on this page.",
        )

    @app.get("/mods/download/status")
    def download_status() -> JSONResponse:
        snapshot = downloads.snapshot()
        return JSONResponse(snapshot if snapshot is not None else {"active": False})

    def _remove_mod_config(
        workshop_id: str, session: BackupSession
    ) -> tuple[list[str], list[str]]:
        """Strip one mod from every shard's modoverrides.lua and from the
        download list. Shared by remove (pending/orphaned) and delete
        (downloaded). Returns (notes, errors)."""
        key = f"workshop-{workshop_id}"
        notes: list[str] = []
        errors: list[str] = []
        for shard in cfg.dst.shards:
            overrides_path = cfg.dst.overrides_path(shard)
            current = load_shard_overrides(shard, overrides_path, lua_command=cfg.lua_command)
            if not current.ok:
                errors.append(f"{shard}: modoverrides.lua unreadable ({current.error})")
                continue
            if key not in current.entries:
                continue
            entries = dict(current.entries)
            del entries[key]
            try:
                if _write_overrides_if_changed(session, shard, overrides_path, entries):
                    notes.append(f"removed from {shard}")
            except (OSError, BackupError) as exc:
                errors.append(f"{shard}: {exc}")
                continue

        try:
            if workshop_id in read_setup_ids(cfg.dst.mods_setup_path):
                session.backup_file(MODS_SETUP_BACKUP_KEY, cfg.dst.mods_setup_path)
                remove_mod_from_setup(cfg.dst.mods_setup_path, workshop_id)
                notes.append(f"removed from {cfg.dst.mods_setup_path.name}")
        except OSError as exc:
            errors.append(f"{cfg.dst.mods_setup_path.name}: {exc}")
        return notes, errors

    @app.post("/mods/delete")
    async def delete_mod(request: Request) -> RedirectResponse:
        """Full cleanup of a downloaded mod: config in every shard, the
        ServerModSetup line, and the local mod folder. Config files are
        backed up first; the folder is not (it can be re-downloaded)."""
        form = await request.form()
        workshop_id = _extract_workshop_id(str(form.get("workshop_id") or ""))
        if workshop_id is None:
            return _redirect_flash("/mods", "no workshop id given", "error")

        session = backups.new_session(f"delete workshop-{workshop_id}")
        notes, errors = _remove_mod_config(workshop_id, session)

        # Sweep every configured mod root so a mod copied into the local
        # mods folder doesn't survive (and reappear) after a delete.
        roots = [cfg.dst.mods_path]
        if cfg.dst.local_mods_path is not None:
            roots.append(cfg.dst.local_mods_path)
        deleted_any = False
        for root in roots:
            if not root.is_dir():
                continue
            for name in (f"workshop-{workshop_id}", workshop_id):
                folder = root / name
                if not folder.is_dir():
                    continue
                try:
                    # Only ever delete a direct child of a configured root.
                    if folder.resolve().parent != root.resolve():
                        errors.append(f"refusing to delete {folder}: not directly inside {root}")
                    else:
                        shutil.rmtree(folder)
                        deleted_any = True
                        notes.append(f"deleted local folder {folder}")
                except OSError as exc:
                    errors.append(f"failed to delete {folder.name}: {exc}")
        if deleted_any:
            mod_scanner.clear_cache()
        else:
            notes.append("no local mod folder found")

        logger.info("delete mod workshop-%s: %s / errors: %s", workshop_id, notes, errors)
        summary = "; ".join(notes) or "nothing to delete"
        if errors:
            return _redirect_flash(
                "/mods", f"workshop-{workshop_id}: {summary}. Errors: {'; '.join(errors)}", "error"
            )
        return _redirect_flash(
            "/mods",
            f"Deleted workshop-{workshop_id} ({summary}). Settings were backed up and can be "
            f"restored from the Backups page; the mod files can be re-downloaded from the Workshop. "
            f"{RESTART_HINT}",
        )

    @app.post("/mods/remove")
    async def remove_mod(request: Request) -> RedirectResponse:
        form = await request.form()
        workshop_id = _extract_workshop_id(str(form.get("workshop_id") or ""))
        if workshop_id is None:
            return _redirect_flash("/mods", "no workshop id given", "error")
        mods, _ = _safe_scan(cfg)
        if any(m.workshop_id == workshop_id for m in mods):
            # Downloaded mods keep their config; disabling is the safe action.
            return _redirect_flash(
                "/mods",
                f"workshop-{workshop_id} is downloaded on disk — untick its shard "
                "checkboxes and save instead of removing the entry",
                "error",
            )
        session = backups.new_session(f"remove workshop-{workshop_id}")
        notes, errors = _remove_mod_config(workshop_id, session)
        key = f"workshop-{workshop_id}"
        logger.info("remove mod %s: %s / errors: %s", key, notes, errors)
        if errors:
            return _redirect_flash(
                "/mods", f"{key}: {'; '.join(notes) or 'nothing removed'}. Errors: {'; '.join(errors)}", "error"
            )
        return _redirect_flash("/mods", f"Removed {key} ({'; '.join(notes) or 'no entries found'}).")

    # ------------------------------------------------------------------ #
    # Backups
    # ------------------------------------------------------------------ #
    def _restore_target(key: str) -> Path | None:
        """Map a backup file key to the live file it restores over."""
        if key in cfg.dst.shards:
            return cfg.dst.overrides_path(key)
        if key == MODS_SETUP_BACKUP_KEY:
            return cfg.dst.mods_setup_path
        return None

    @app.get("/backups", response_class=HTMLResponse)
    def backups_page(request: Request) -> HTMLResponse:
        return render(request, "backups.html", {
            "active_page": "backups",
            "sessions": backups.list_sessions(),
        })

    def _diff_class(line: str) -> str:
        if line.startswith("+++") or line.startswith("---"):
            return "meta"
        if line.startswith("@@"):
            return "hunk"
        if line.startswith("+"):
            return "add"
        if line.startswith("-"):
            return "del"
        return ""

    @app.get("/backups/{session_id}", response_class=HTMLResponse)
    def backup_detail(request: Request, session_id: str) -> HTMLResponse:
        try:
            session = backups.get_session(session_id)
        except BackupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

        # For each backed-up file, show what the operation changed: the
        # backup holds the file BEFORE the operation; the state AFTER it is
        # the same file's copy in the next-newer backup, or the current live
        # file when this is the most recent record.
        diffs: list[dict[str, Any]] = []
        for f in session.files:
            before_text = f.path.read_text(encoding="utf-8", errors="replace")
            next_backup = backups.find_next_backup(session.session_id, f.key)
            if next_backup is not None:
                after_path: Path | None = next_backup[1].path
                after_label = f"next backup ({next_backup[0]})"
            else:
                after_path = _restore_target(f.key)
                after_label = "current file"
            if after_path is not None and after_path.is_file():
                after_text = after_path.read_text(encoding="utf-8", errors="replace")
            else:
                after_text = ""
                after_label += " — missing"
            lines = [
                (_diff_class(line), line)
                for line in difflib.unified_diff(
                    before_text.splitlines(),
                    after_text.splitlines(),
                    fromfile=f"{f.key} (this backup)",
                    tofile=f"{f.key} ({after_label})",
                    lineterm="",
                )
            ]
            diffs.append({
                "key": f.key,
                "filename": f.filename,
                "after_label": after_label,
                "lines": lines,
                "content": before_text,
            })

        return render(request, "backup_detail.html", {
            "active_page": "backups",
            "session": session,
            "diffs": diffs,
        })

    @app.post("/backups/restore")
    async def restore_backup(request: Request) -> RedirectResponse:
        form = await request.form()
        session_id = str(form.get("session_id") or "")
        keys = [str(k) for k in form.getlist("keys")]
        try:
            session = backups.get_session(session_id)
        except BackupError as exc:
            return _redirect_flash("/backups", str(exc), "error")
        detail_url = f"/backups/{session.session_id}"

        available = {f.key for f in session.files}
        keys = [k for k in keys if k in available]
        if not keys:
            return _redirect_flash(detail_url, "no files selected to restore", "error")

        backup_files = {f.key: f for f in session.files}
        pre_restore = backups.new_session(f"before restoring backup {session.session_id}")
        restored: list[str] = []
        unchanged: list[str] = []
        errors: list[str] = []
        for key in keys:
            target = _restore_target(key)
            if target is None:
                errors.append(f"{key}: no restore target for this key (shard removed from config?)")
                continue
            try:
                # Skip files that already match the backup — no pre-restore
                # copy, no write, no pointless record.
                if target.is_file() and target.read_bytes() == backup_files[key].path.read_bytes():
                    unchanged.append(key)
                    continue
                pre_restore.backup_file(key, target)
                backups.restore_file(session.session_id, key, target)
            except (BackupError, OSError) as exc:
                errors.append(f"{key}: {exc}")
                continue
            restored.append(key)
            logger.info("restored %s/%s -> %s", session.session_id, key, target)

        note = (
            f" Previous files backed up as {pre_restore.session_id}."
            if pre_restore.has_backups
            else ""
        )
        unchanged_note = (
            f" ({', '.join(unchanged)} already matched this backup)" if unchanged else ""
        )
        if restored and not errors:
            return _redirect_flash(
                detail_url, f"Restored {', '.join(restored)}{unchanged_note}.{note} {RESTART_HINT}"
            )
        if restored:
            return _redirect_flash(
                detail_url,
                f"Restored {', '.join(restored)}{unchanged_note}.{note} Errors: {'; '.join(errors)}",
                "error",
            )
        if errors:
            return _redirect_flash(
                detail_url, f"Nothing restored. Errors: {'; '.join(errors)}", "error"
            )
        return _redirect_flash(
            detail_url, f"Nothing to restore — current files already match this backup{unchanged_note}."
        )

    @app.post("/backups/delete")
    async def delete_backup(request: Request) -> RedirectResponse:
        # Accepts one or many ids: the detail page submits a single value,
        # the list page submits every checked row.
        form = await request.form()
        session_ids = [str(s) for s in form.getlist("session_ids")]
        if not session_ids:
            return _redirect_flash("/backups", "no backups selected", "error")

        deleted: list[str] = []
        errors: list[str] = []
        for session_id in session_ids:
            try:
                backups.delete_session(session_id)
            except (BackupError, OSError) as exc:
                errors.append(str(exc))
                continue
            deleted.append(session_id)
            logger.info("deleted backup %s", session_id)

        if deleted and not errors:
            noun = "backup" if len(deleted) == 1 else f"{len(deleted)} backups"
            return _redirect_flash("/backups", f"Deleted {noun}: {', '.join(deleted)}.")
        if deleted:
            return _redirect_flash(
                "/backups",
                f"Deleted {', '.join(deleted)}. Errors: {'; '.join(errors)}",
                "error",
            )
        return _redirect_flash("/backups", f"Nothing deleted. Errors: {'; '.join(errors)}", "error")

    # ------------------------------------------------------------------ #
    # Server page + control + logs
    # ------------------------------------------------------------------ #
    def _control_command(action: str) -> str:
        return {
            "start": cfg.server.start_command,
            "stop": cfg.server.stop_command,
            "restart": cfg.server.restart_command,
            "status": cfg.server.status_command,
        }.get(action, "")

    def _shard_log_path(shard: str) -> Path:
        return cfg.dst.shard_dir(shard) / "server_log.txt"

    @app.get("/server", response_class=HTMLResponse)
    def server_page(request: Request) -> HTMLResponse:
        logs = []
        for shard in cfg.dst.shards:
            path = _shard_log_path(shard)
            entry: dict[str, Any] = {"shard": shard, "path": path, "exists": path.is_file()}
            if entry["exists"]:
                try:
                    stat = path.stat()
                    entry["size"] = human_bytes(stat.st_size)
                    entry["mtime"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                except OSError:
                    entry["exists"] = False
            logs.append(entry)
        return render(request, "server.html", {
            "active_page": "server",
            "cluster": read_cluster_info(cfg.dst.cluster_path, cfg.dst.shards),
            "system": gather_system_info(
                [cfg.dst.cluster_path, cfg.dst.mods_path, cfg.backup.directory]
            ),
            "logs": logs,
            "last_command": app.state.last_command,
        })

    @app.post("/server/run")
    async def server_run(request: Request) -> RedirectResponse:
        form = await request.form()
        # The control buttons live on both the dashboard and /server.
        next_url = "/server" if str(form.get("next") or "") == "/server" else "/"
        action = str(form.get("action") or "")
        if action not in ("start", "stop", "restart", "status"):
            return _redirect_flash(next_url, f"unknown action: {action}", "error")
        command = _control_command(action)
        if not command.strip():
            return _redirect_flash(next_url, f"no {action}_command configured", "error")
        result = run_command(command)
        app.state.last_command = {"action": action, "result": result}
        logger.info("%s command finished: ok=%s exit=%s", action, result.ok, result.exit_code)
        if result.ok:
            return _redirect_flash(next_url, f"{action.capitalize()} command executed — output below.")
        return _redirect_flash(next_url, f"{action.capitalize()} command failed — output below.", "error")

    @app.get("/server/logs/{shard}", response_class=HTMLResponse)
    def server_log_page(request: Request, shard: str, lines: int = 200) -> HTMLResponse:
        if shard not in cfg.dst.shards:
            raise HTTPException(status_code=404, detail=f"unknown shard: {shard}")
        lines = max(50, min(lines, 5000))
        path = _shard_log_path(shard)
        content = None
        error = ""
        size = ""
        mtime = ""
        if path.is_file():
            try:
                stat = path.stat()
                size = human_bytes(stat.st_size)
                mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                content = tail_file(path, lines)
            except OSError as exc:
                error = str(exc)
        return render(request, "logs.html", {
            "active_page": "server",
            "shard": shard,
            "log_path": path,
            "content": content,
            "error": error,
            "size": size,
            "mtime": mtime,
            "lines": lines,
            "line_options": [200, 500, 1000, 2000],
        })

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Optional positional argument: path to the config file.
    if len(sys.argv) > 1:
        os.environ[CONFIG_ENV_VAR] = sys.argv[1]
    try:
        cfg = load_config()
    except ConfigError as exc:
        raise SystemExit(f"error: {exc}") from exc

    import uvicorn

    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=cfg.server.host,
        port=cfg.server.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
