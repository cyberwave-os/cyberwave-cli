"""Low-level I/O helpers shared across the CLI package."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any, *, mode: int = 0o600) -> None:
    """Atomically write *data* as JSON to *path* with restrictive permissions.

    Uses a sibling temp file + ``os.replace`` so readers never see a
    half-written file.  On POSIX the file is ``chmod``-ed to *mode*
    (default ``0o600``, owner-only read/write) **before** the rename,
    closing the TOCTOU window where the file would briefly be
    world-readable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt":
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
