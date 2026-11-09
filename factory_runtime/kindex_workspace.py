"""Keep Factory scratch trees out of Kindex's durable background indexing.

Kindex inherits this presence marker through ancestor directories. It does not
disable private queries, captures or knowledge exports. Only Factory-owned
workspace creation calls this helper; canonical repositories are never marked.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

NO_INDEX_MARKER = ".kindex-no-index"


def mark_no_index(root: Path) -> bool:
    """Create runtime metadata without following links or replacing existing bytes."""
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            descriptor = os.open(
                NO_INDEX_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=directory,
            )
        except FileExistsError:
            info = os.stat(NO_INDEX_MARKER, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Kindex exclusion marker is not a regular file: {root}") from None
            return False
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(directory)
        return True
    finally:
        os.close(directory)
