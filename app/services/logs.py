"""Tail the DST server log files (<cluster>/<shard>/server_log.txt).

The dedicated server writes these as the running user, so the panel can read
them directly — no sudo or journal access needed.
"""

from __future__ import annotations

from pathlib import Path

# Never read more than this from the end of a log, however many lines were
# requested — server_log.txt can grow to many MB.
MAX_TAIL_BYTES = 1_000_000


def tail_file(path: Path, max_lines: int) -> str:
    """Return the last `max_lines` lines of a text file."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > MAX_TAIL_BYTES:
            handle.seek(size - MAX_TAIL_BYTES)
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    if size > MAX_TAIL_BYTES:
        # Drop the (probably partial) first line of the truncated read.
        text = text.split("\n", 1)[-1]
    return "\n".join(text.splitlines()[-max_lines:])
