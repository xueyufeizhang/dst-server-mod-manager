"""In-memory background download jobs with status polling.

At most one download runs at a time (concurrent steamcmd instances fight
over steamcmd's own lock files). The single-worker deployment assumption of
this app (see main.py) makes a module-level registry safe.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services.mod_downloader import DownloadResult

MAX_LOG_LINES = 4  # how many trailing steamcmd output lines to expose


@dataclass
class DownloadJob:
    workshop_id: str
    status: str = "running"  # running | done | error
    message: str = ""
    log_lines: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)


class DownloadManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: DownloadJob | None = None

    def start(
        self,
        workshop_id: str,
        runner: Callable[[Callable[[str], None]], DownloadResult],
    ) -> tuple[bool, str]:
        """Start a background download. Returns (started, error_message)."""
        with self._lock:
            if self._job is not None and self._job.status == "running":
                return (
                    False,
                    f"another download is already running (workshop-{self._job.workshop_id})",
                )
            job = DownloadJob(workshop_id=workshop_id)
            self._job = job
        threading.Thread(
            target=self._run, args=(job, runner), name=f"dl-{workshop_id}", daemon=True
        ).start()
        return True, ""

    def _run(
        self,
        job: DownloadJob,
        runner: Callable[[Callable[[str], None]], DownloadResult],
    ) -> None:
        def on_output(line: str) -> None:
            line = line.strip()
            if line:
                job.log_lines.append(line)
                del job.log_lines[:-MAX_LOG_LINES]

        try:
            result = runner(on_output)
            if result.ok:
                job.message = (
                    f"Downloaded workshop-{job.workshop_id} ({result.message}). "
                    "Configure its options below; a single server restart applies "
                    "the mod and its settings together."
                )
                job.status = "done"
            else:
                job.message = f"workshop-{job.workshop_id}: {result.message}"
                if result.output_tail:
                    job.message += f" — {result.output_tail[-200:]}"
                job.status = "error"
        except Exception as exc:  # never leave a job stuck in "running"
            job.message = f"workshop-{job.workshop_id}: download crashed: {exc}"
            job.status = "error"

    def snapshot(self) -> dict[str, Any] | None:
        """Status of the current/last job for the polling endpoint."""
        job = self._job
        if job is None:
            return None
        return {
            "active": job.status == "running",
            "workshop_id": job.workshop_id,
            "status": job.status,
            "message": job.message,
            "log": "\n".join(job.log_lines),
            "elapsed": int(time.monotonic() - job.started),
        }
