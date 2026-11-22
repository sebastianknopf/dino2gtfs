"""Temporary SQLite lifecycle."""
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

@contextmanager
def temporary_database() -> Iterator[sqlite3.Connection]:
    """Provide a temporary on-disk database removed on exit."""
    with TemporaryDirectory(prefix="dino2gtfs-") as directory:
        path = Path(directory) / "staging.db3"
        connection = sqlite3.connect(path)
        
        try:
            yield connection
        finally:
            connection.close()
